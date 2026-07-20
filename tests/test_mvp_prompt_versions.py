from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url

from open_storyline.mvp.api import create_mvp_router
from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.jobs import JobManager, JobStore, JobStoreError
from open_storyline.mvp.models import (
    Artifact,
    AuditDocument,
    PromptVersion,
    SessionInputVideo,
    VideoJob,
)
from open_storyline.mvp.prompt_versions import PromptVersionService, validate_run_settings
from open_storyline.mvp.session_media import SessionMediaStore


ROOT = Path(__file__).resolve().parents[1]


def _integration_url() -> str:
    raw = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw:
        return ""
    url = make_url(normalize_database_url(raw))
    if not str(url.database or "").startswith("openstoryline_test"):
        raise RuntimeError("TEST_DATABASE_URL must use an openstoryline_test database")
    return raw


async def _body(value: bytes):
    yield value


class RunSettingsTests(unittest.TestCase):
    def test_required_asset_mix_is_explicit_and_versioned(self):
        settings = validate_run_settings(
            max_clips=1,
            edit_mode="agentic",
            asset_policy="required",
            max_generated_assets_per_clip=1,
            stock_policy="required",
            max_stock_assets_per_clip=1,
            stock_asset_kind="video",
        )

        self.assertEqual(settings["settings_version"], 2)
        self.assertEqual(settings["asset_policy"], "required")
        self.assertEqual(settings["stock_policy"], "required")
        self.assertEqual(settings["stock_asset_kind"], "video")

    def test_required_asset_mix_rejects_zero_counts(self):
        with self.assertRaises(JobStoreError) as generated:
            validate_run_settings(
                asset_policy="required",
                max_generated_assets_per_clip=0,
            )
        self.assertEqual(
            generated.exception.code,
            "REQUIRED_GENERATED_ASSET_COUNT_INVALID",
        )

        with self.assertRaises(JobStoreError) as stock:
            validate_run_settings(
                stock_policy="required",
                max_stock_assets_per_clip=0,
            )
        self.assertEqual(stock.exception.code, "REQUIRED_STOCK_ASSET_COUNT_INVALID")


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PromptVersionPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.database_url = _integration_url()
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env={**os.environ, "DATABASE_URL": cls.database_url},
            check=True,
            capture_output=True,
            text=True,
        )

    async def asyncSetUp(self):
        self.database = Database(self.database_url)
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE audit_reviews, audit_documents, artifacts, job_events, "
                    "video_jobs, prompt_versions, session_input_videos, editing_sessions, "
                    "auth_sessions, login_attempt_buckets"
                )
            )
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.media_root = self.root / "mvp_sessions"
        self.store = JobStore(
            self.root / "mvp_jobs",
            self.database,
            session_media_root=self.media_root,
        )
        self.media = SessionMediaStore(
            self.media_root,
            self.database,
            max_upload_bytes=32 * 1024 * 1024,
            max_chunk_bytes=1024 * 1024,
        )
        self.service = PromptVersionService(self.store, self.media)
        self.settings = validate_run_settings(
            max_clips=3,
            edit_mode="agentic",
            asset_policy="off",
            max_generated_assets_per_clip=1,
            stock_policy="off",
            max_stock_assets_per_clip=0,
        )

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary_directory.cleanup()

    def synthetic_video(self) -> bytes:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg and FFprobe are required")
        path = self.root / f"synthetic-{uuid.uuid4().hex}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x28465f:s=96x96:r=12:d=0.5",
                "-an",
                "-c:v",
                "mpeg4",
                "-q:v",
                "5",
                "-y",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        return path.read_bytes()

    async def ready_session(self, title: str = "Reusable") -> tuple[dict, dict, Path]:
        video = self.synthetic_video()
        editing_session = await self.store.create_session(title, workflow_version=2)
        upload = await self.media.initialize(
            editing_session["id"],
            original_filename="source.mp4",
            expected_size=len(video),
            media_type="video/mp4",
        )
        await self.media.append_chunk(
            editing_session["id"],
            upload["upload_id"],
            offset=0,
            chunks=_body(video),
            content_length=len(video),
        )
        ready = await self.media.complete(editing_session["id"], upload["upload_id"])
        path, _state = await self.media.resolve_ready(editing_session["id"])
        return editing_session, ready, path

    async def test_versions_and_reruns_reuse_one_source_without_job_input_copies(self):
        editing_session, source, source_path = await self.ready_session()
        first = await self.service.create_version(
            editing_session["id"],
            prompt="first immutable prompt",
            settings=self.settings,
        )
        second = await self.service.create_version(
            editing_session["id"],
            prompt="second immutable prompt",
            settings={**self.settings, "max_clips": 2},
        )
        rerun = await self.service.rerun(first["prompt_version"]["id"])

        self.assertEqual(first["prompt_version"]["version_number"], 1)
        self.assertEqual(second["prompt_version"]["version_number"], 2)
        self.assertEqual(first["run"]["attempt_number"], 1)
        self.assertEqual(rerun["attempt_number"], 2)
        self.assertEqual(rerun["prompt"], first["run"]["prompt"])
        self.assertEqual(rerun["request"], first["run"]["request"])
        self.assertEqual(rerun["request"]["settings_version"], 2)

        runs = (first["run"], second["run"], rerun)
        self.assertEqual({run["input"]["input_video_id"] for run in runs}, {source["id"]})
        self.assertEqual({run["input"]["sha256"] for run in runs}, {source["sha256"]})
        resolved = [await self.store.source_path(run["id"]) for run in runs]
        self.assertTrue(all(path == source_path for path in resolved))
        for run in runs:
            job_dir = self.root / "mvp_jobs" / run["id"]
            self.assertFalse((job_dir / "input").exists())
            self.assertTrue((job_dir / "output").is_dir())
            self.assertTrue((job_dir / "work").is_dir())
            self.assertTrue((job_dir / "job.json").is_file())
        original_bytes = source_path.read_bytes()
        tampered = bytearray(original_bytes)
        tampered[-1] ^= 1
        source_path.write_bytes(tampered)
        with self.assertRaises(JobStoreError) as changed:
            await self.store.source_path(first["run"]["id"])
        self.assertEqual(changed.exception.code, "SESSION_SOURCE_CHANGED")
        source_path.write_bytes(original_bytes)

    async def test_concurrent_version_and_attempt_numbers_are_unique(self):
        editing_session, _source, _path = await self.ready_session()

        async def create(prompt: str):
            return await self.service.create_version(
                editing_session["id"],
                prompt=prompt,
                settings=self.settings,
            )

        created = await asyncio.gather(create("concurrent one"), create("concurrent two"))
        self.assertEqual(
            {item["prompt_version"]["version_number"] for item in created},
            {1, 2},
        )
        first_version = created[0]["prompt_version"]["id"]
        reruns = await asyncio.gather(
            self.service.rerun(first_version),
            self.service.rerun(first_version),
        )
        self.assertEqual({run["attempt_number"] for run in reruns}, {2, 3})

    async def test_worker_recovery_processes_queued_versioned_run_once(self):
        editing_session, _source, source_path = await self.ready_session("Recovery")
        created = await self.service.create_version(
            editing_session["id"],
            prompt="recover this queued run",
            settings=self.settings,
        )
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(SessionInputVideo)
                .where(
                    SessionInputVideo.editing_session_id == editing_session["id"]
                )
                .values(expires_at=datetime.now(UTC) + timedelta(seconds=1))
            )
        calls = 0

        async def processor(job_id: str, store: JobStore):
            nonlocal calls
            calls += 1
            self.assertEqual(await store.source_path(job_id), source_path)
            return {"clip_count": 1}

        manager = JobManager(self.store, processor, poll_interval=0.05)
        await manager.start()
        try:
            terminal = await manager.wait_for_terminal(
                created["run"]["id"],
                timeout=5,
            )
        finally:
            await manager.stop()
        self.assertEqual(terminal["state"], "completed")
        self.assertEqual(calls, 1)
        async with self.database.sessions() as session:
            renewed = await session.scalar(
                select(SessionInputVideo.expires_at).where(
                    SessionInputVideo.editing_session_id == editing_session["id"]
                )
            )
        self.assertGreater(renewed, datetime.now(UTC) + timedelta(days=6))

    async def test_queue_source_and_atomicity_failures_leave_no_orphans(self):
        editing_session, source, source_path = await self.ready_session()
        limited_store = JobStore(
            self.root / "limited_jobs",
            self.database,
            max_active_jobs=1,
            session_media_root=self.media_root,
        )
        limited = PromptVersionService(limited_store, self.media)
        first = await limited.create_version(
            editing_session["id"],
            prompt="fills queue",
            settings=self.settings,
        )
        rejected_job_id = uuid.uuid4().hex
        with self.assertRaises(JobStoreError) as queue_full:
            await limited.create_version(
                editing_session["id"],
                prompt="must roll back",
                settings=self.settings,
                job_id=rejected_job_id,
            )
        self.assertEqual(queue_full.exception.code, "JOB_QUEUE_FULL")
        async with self.database.sessions() as session:
            version_count = await session.scalar(
                select(func.count()).select_from(PromptVersion)
            )
        self.assertEqual(version_count, 1)
        self.assertFalse((self.root / "limited_jobs" / rejected_job_id).exists())

        await limited_store.fail(first["run"]["id"], code="TEST_DONE", message="done")
        original_bytes = source_path.read_bytes()
        source_path.write_bytes(b"tampered")
        with self.assertRaises(JobStoreError) as changed:
            await self.service.create_version(
                editing_session["id"],
                prompt="changed source must fail",
                settings=self.settings,
            )
        self.assertEqual(changed.exception.code, "SESSION_SOURCE_CHANGED")

        source_path.write_bytes(original_bytes)
        original_relative = first["run"]["input"]["relative_path"]
        unsafe_input = dict(first["run"]["input"])
        unsafe_input["relative_path"] = "../outside.mp4"
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(SessionInputVideo)
                .where(SessionInputVideo.id == source["id"])
                .values(relative_path="../outside.mp4")
            )
            await connection.execute(
                update(VideoJob)
                .where(VideoJob.id == first["run"]["id"])
                .values(input_data=unsafe_input)
            )
        with self.assertRaises(JobStoreError) as unsafe:
            await limited_store.source_path(first["run"]["id"])
        self.assertEqual(unsafe.exception.code, "SESSION_SOURCE_PATH_INVALID")
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(SessionInputVideo)
                .where(SessionInputVideo.id == source["id"])
                .values(relative_path=original_relative)
            )
            safe_input = dict(first["run"]["input"])
            await connection.execute(
                update(VideoJob)
                .where(VideoJob.id == first["run"]["id"])
                .values(input_data=safe_input)
            )
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(SessionInputVideo)
                .where(SessionInputVideo.id == source["id"])
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        with self.assertRaises(JobStoreError) as expired:
            await self.service.rerun(first["prompt_version"]["id"])
        self.assertEqual(expired.exception.code, "SESSION_SOURCE_EXPIRED")

    async def test_required_asset_settings_survive_rerun_unchanged(self):
        editing_session, _source, _source_path = await self.ready_session()
        settings = validate_run_settings(
            max_clips=1,
            edit_mode="agentic",
            asset_policy="required",
            max_generated_assets_per_clip=1,
            stock_policy="required",
            max_stock_assets_per_clip=1,
            stock_asset_kind="video",
        )
        created = await self.service.create_version(
            editing_session["id"],
            prompt="immutable required asset contract",
            settings=settings,
        )

        rerun = await self.service.rerun(created["prompt_version"]["id"])

        self.assertEqual(rerun["request"], created["run"]["request"])
        self.assertEqual(rerun["request"]["asset_policy"], "required")
        self.assertEqual(rerun["request"]["stock_policy"], "required")

    async def test_explicit_rerun_can_use_sanitized_prior_quality_evidence(self):
        editing_session, _source, _source_path = await self.ready_session()
        created = await self.service.create_version(
            editing_session["id"],
            prompt="repair the prior objective quality blockers",
            settings=self.settings,
        )
        prior = created["run"]
        await self.store.update(prior["id"], state="completed", progress=1)
        document = {
            "version": "frame_quality_qa.v1",
            "clips": [{
                "clip_index": 1,
                "active_picture": {"summary": {
                    "median_active_area_ratio": 0.31,
                    "median_active_height_ratio": 0.3125,
                }},
                "reference_metrics": {"samples": [{
                    "timestamp_ms": 1000,
                    "segment_id": "segment-1",
                    "operation": "crop",
                    "strategy": "crop",
                    "ssim": 0.6,
                    "psnr": 17.0,
                }]},
                "findings": [{
                    "code": "ACTIVE_PICTURE_TOO_SMALL",
                    "severity": "blocker",
                }],
            }],
        }
        raw = json.dumps(document)
        async with self.database.sessions() as session:
            async with session.begin():
                session.add(AuditDocument(
                    job_id=prior["id"],
                    kind="frame_quality_qa",
                    source_name="frame_quality_qa.json",
                    raw_text=raw,
                    parsed_data=document,
                    parse_status="parsed",
                    parser_version="audit.v1",
                    sha256=hashlib.sha256(raw.encode()).hexdigest(),
                    byte_size=len(raw.encode()),
                ))

        rerun = await self.service.rerun(
            created["prompt_version"]["id"],
            prior_attempt_id=prior["id"],
            use_quality_feedback=True,
        )
        feedback = rerun["request"]["prior_attempt_quality_feedback"]

        self.assertEqual(rerun["prompt"], created["run"]["prompt"])
        self.assertEqual(rerun["request"]["settings_version"], 2)
        self.assertEqual(feedback["prior_attempt_id"], prior["id"])
        self.assertEqual(feedback["prior_attempt_number"], 1)
        self.assertIn("ACTIVE_PICTURE_TOO_SMALL", feedback["blocker_codes"])
        self.assertEqual(feedback["worst_metric_samples"][0]["timestamp_ms"], 1000)

        with self.assertRaises(JobStoreError) as missing_flag:
            await self.service.rerun(
                created["prompt_version"]["id"],
                prior_attempt_id=prior["id"],
            )
        self.assertEqual(
            missing_flag.exception.code,
            "PRIOR_QUALITY_FEEDBACK_FLAG_REQUIRED",
        )

    async def test_history_favorite_and_cross_session_rules(self):
        editing_session, _source, _path = await self.ready_session("History")
        versions = []
        for index in range(3):
            versions.append(
                await self.service.create_version(
                    editing_session["id"],
                    prompt=f"version {index} " + "long " * 100,
                    settings={**self.settings, "max_clips": index + 1},
                )
            )
        first_page = await self.service.list_versions(editing_session["id"], limit=2)
        second_page = await self.service.list_versions(
            editing_session["id"],
            limit=2,
            cursor=first_page["next_cursor"],
        )
        self.assertEqual(len(first_page["items"]), 2)
        self.assertEqual(len(second_page["items"]), 1)
        detail = await self.service.get_version(versions[0]["prompt_version"]["id"])
        self.assertIn("long long", detail["prompt"])
        self.assertEqual(detail["attempts"][0]["attempt_number"], 1)

        first_run = versions[0]["run"]
        second_run = versions[1]["run"]
        with self.assertRaises(JobStoreError) as incomplete:
            await self.service.select_favorite(editing_session["id"], first_run["id"])
        self.assertEqual(incomplete.exception.code, "FAVORITE_RUN_INVALID")
        await self.store.update(first_run["id"], state="completed", progress=1)
        await self.store.update(second_run["id"], state="completed", progress=1)
        selected = await self.service.select_favorite(editing_session["id"], first_run["id"])
        switched = await self.service.select_favorite(editing_session["id"], second_run["id"])
        self.assertEqual(selected["selection_source"], "human")
        self.assertEqual(switched["favorite_run_id"], second_run["id"])
        async with self.database.sessions() as session:
            favorites = list(
                (
                    await session.execute(
                        select(VideoJob).where(VideoJob.is_favorite.is_(True))
                    )
                ).scalars()
            )
        self.assertEqual([row.id for row in favorites], [second_run["id"]])

        other_session, _source, _path = await self.ready_session("Other")
        with self.assertRaises(JobStoreError) as cross_session:
            await self.service.select_favorite(other_session["id"], second_run["id"])
        self.assertEqual(cross_session.exception.code, "FAVORITE_RUN_INVALID")
        cleared = await self.service.clear_favorite(editing_session["id"])
        self.assertIsNone(cleared["favorite_run_id"])

    async def test_concurrent_favorite_switches_leave_exactly_one_human_choice(self):
        editing_session, _source, _path = await self.ready_session("Concurrent favorite")
        first = await self.service.create_version(
            editing_session["id"],
            prompt="first completed choice",
            settings=self.settings,
        )
        second = await self.service.create_version(
            editing_session["id"],
            prompt="second completed choice",
            settings=self.settings,
        )
        run_ids = {first["run"]["id"], second["run"]["id"]}
        for run_id in run_ids:
            await self.store.update(run_id, state="completed", progress=1)

        selected = await asyncio.gather(
            self.service.select_favorite(editing_session["id"], first["run"]["id"]),
            self.service.select_favorite(editing_session["id"], second["run"]["id"]),
        )

        self.assertEqual(
            {item["favorite_run_id"] for item in selected},
            run_ids,
        )
        async with self.database.sessions() as session:
            favorites = list(
                (
                    await session.execute(
                        select(VideoJob).where(
                            VideoJob.editing_session_id == editing_session["id"],
                            VideoJob.is_favorite.is_(True),
                        )
                    )
                ).scalars()
            )
        self.assertEqual(len(favorites), 1)
        self.assertIn(favorites[0].id, run_ids)

    async def test_api_history_runs_favorite_and_registered_preview(self):
        editing_session, _source, _path = await self.ready_session("API")
        manager = JobManager(self.store)
        app = FastAPI()
        app.include_router(
            create_mvp_router(
                lambda: self.store,
                lambda: manager,
                None,
                lambda: self.media,
                lambda: "enabled",
                lambda: self.service,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/mvp/sessions/{editing_session['id']}/prompt-versions",
                json={
                    "prompt": "API immutable prompt",
                    "max_clips": 2,
                    "edit_mode": "legacy",
                    "asset_policy": "off",
                },
            )
            self.assertEqual(created.status_code, 202)
            version_id = created.json()["prompt_version"]["id"]
            run_id = created.json()["run"]["id"]
            rerun = await client.post(f"/api/mvp/prompt-versions/{version_id}/runs")
            history = await client.get(
                f"/api/mvp/sessions/{editing_session['id']}/prompt-versions"
            )
            detail = await client.get(f"/api/mvp/prompt-versions/{version_id}")
            self.assertEqual(rerun.status_code, 202)
            self.assertEqual(history.json()["items"][0]["id"], version_id)
            self.assertEqual(len(detail.json()["attempts"]), 2)

            await self.store.update(run_id, state="completed", progress=1)
            favorite = await client.put(
                f"/api/mvp/sessions/{editing_session['id']}/favorite-run",
                json={"run_id": run_id},
            )
            self.assertEqual(favorite.status_code, 200)
            self.assertEqual(favorite.json()["selection_source"], "human")

            video = self.store.output_dir(run_id) / "preview.mp4"
            video.write_bytes(b"0123456789video")
            await self.store.register_artifact(run_id, video, kind="video")
            preview = await client.get(
                f"/api/mvp/jobs/{run_id}/artifacts/preview.mp4/preview",
                headers={"Range": "bytes=2-5"},
            )
            self.assertEqual(preview.status_code, 206)
            self.assertEqual(preview.content, b"2345")
            self.assertTrue(preview.headers["content-disposition"].startswith("inline;"))

            async with self.database.engine.begin() as connection:
                await connection.execute(
                    update(Artifact)
                    .where(Artifact.job_id == run_id, Artifact.name == "preview.mp4")
                    .values(
                        availability="missing",
                        purged_at=datetime.now(UTC),
                        purge_reason="test_expired",
                    )
                )
            unavailable = await client.get(
                f"/api/mvp/jobs/{run_id}/artifacts/preview.mp4/preview"
            )
            self.assertEqual(unavailable.status_code, 404)

            manifest = self.store.output_dir(run_id) / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            await self.store.register_artifact(run_id, manifest, kind="manifest")
            unsupported = await client.get(
                f"/api/mvp/jobs/{run_id}/artifacts/manifest.json/preview"
            )
            self.assertEqual(unsupported.status_code, 415)


if __name__ == "__main__":
    unittest.main()
