from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url

from open_storyline.mvp.audit import AuditService
from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.jobs import JobManager, JobStore
from open_storyline.mvp.models import (
    AuditDocument,
    AuditReview,
    PromptVersion,
    SessionInputVideo,
    VideoJob,
)
from open_storyline.mvp.outcomes import build_completed_outcome_report


ROOT = Path(__file__).resolve().parents[1]


def _integration_url() -> str:
    raw = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw:
        return ""
    url = make_url(normalize_database_url(raw))
    if not str(url.database or "").startswith("openstoryline_test"):
        raise RuntimeError("TEST_DATABASE_URL must use an openstoryline_test database")
    return raw


class AuditPostgresTestCase(unittest.IsolatedAsyncioTestCase):
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
                    "TRUNCATE job_stage_checkpoints, session_analysis_cache, "
                    "audit_reviews, audit_documents, artifacts, job_events, "
                    "video_jobs, prompt_versions, session_input_videos, editing_sessions, "
                    "auth_sessions, login_attempt_buckets"
                )
            )
        self.temporary_directory = TemporaryDirectory()
        self.store = JobStore(self.temporary_directory.name, self.database)
        self.audit = AuditService(self.store, max_document_bytes=4096)
        self.store.attach_audit(self.audit)

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary_directory.cleanup()

    async def create_job(self, *, prompt: str = "make one clip") -> dict:
        editing_session = await self.store.create_session("Audit session")
        state = await self.store.create(
            editing_session_id=editing_session["id"],
            prompt=prompt,
            filename="talk.mp4",
            max_clips=1,
        )
        source = self.store.input_path(state["id"], "talk.mp4")
        source.write_bytes(b"source")
        return await self.store.mark_uploaded(state["id"], source, 6)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class AuditDocumentTests(AuditPostgresTestCase):
    async def test_outcome_slo_summary_is_bounded_and_tracks_retry_success(self):
        editing_session = await self.store.create_session(
            "Outcome audit",
            workflow_version=2,
        )
        version = PromptVersion(
            id=uuid.uuid4().hex,
            editing_session_id=editing_session["id"],
            version_number=1,
            prompt="auditable outcome prompt",
            settings_data={"settings_version": 1, "max_clips": 1},
        )
        now = datetime.now(UTC)
        first_id = uuid.uuid4().hex
        retry_id = uuid.uuid4().hex
        enhanced = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
        )
        limited = build_completed_outcome_report(
            outputs=[{"video": "short-02.mp4", "subtitles": None}],
            promotion_report={
                "technical_blocker_codes": [],
                "creative_limitation_codes": ["CAPTION_WIDTH_EXCEEDED"],
            },
            reused_stages=("transcript",),
        )
        async with self.database.sessions() as session:
            async with session.begin():
                session.add(version)
        async with self.database.sessions() as session:
            async with session.begin():
                session.add_all((
                    VideoJob(
                        id=first_id,
                        editing_session_id=editing_session["id"],
                        prompt_version_id=version.id,
                        attempt_number=1,
                        state="completed",
                        progress=Decimal("1"),
                        prompt=version.prompt,
                        request_data={"settings_version": 1},
                        result_data={"outcome": enhanced},
                        started_at=now - timedelta(minutes=3),
                        completed_at=now - timedelta(minutes=2),
                        audit_expires_at=now + timedelta(days=30),
                    ),
                    VideoJob(
                        id=retry_id,
                        editing_session_id=editing_session["id"],
                        prompt_version_id=version.id,
                        attempt_number=2,
                        state="completed",
                        progress=Decimal("1"),
                        prompt=version.prompt,
                        request_data={
                            "settings_version": 1,
                            "retry_of_attempt_id": first_id,
                            "prior_attempt_quality_feedback": {
                                "retry_reason_codes": ["CAPTION_WIDTH_EXCEEDED"],
                            },
                        },
                        result_data={"outcome": limited},
                        started_at=now - timedelta(minutes=1),
                        completed_at=now,
                        audit_expires_at=now + timedelta(days=30),
                    ),
                ))

        summary = await self.audit.outcome_slo_summary(limit=10)

        self.assertEqual(summary["sample_size"], 2)
        self.assertEqual(summary["playable_outputs"], 2)
        self.assertEqual(summary["retry"]["attempts"], 1)
        self.assertEqual(summary["retry"]["playable_successes"], 1)
        self.assertEqual(summary["checkpoints"]["reused_stage_count"], 1)
        self.assertFalse(summary["truncated"])

    async def test_version_attempt_source_and_human_favorite_are_attributable(self):
        editing_session = await self.store.create_session(
            "Versioned audit",
            workflow_version=2,
        )
        now = datetime.now(UTC)
        source = SessionInputVideo(
            id=uuid.uuid4().hex,
            editing_session_id=editing_session["id"],
            state="ready",
            original_filename="source.mp4",
            expected_size=12,
            received_bytes=12,
            media_type="video/mp4",
            relative_path=f"{editing_session['id']}/input/source.mp4",
            sha256="a" * 64,
            completed_at=now,
            expires_at=now + timedelta(days=7),
        )
        version = PromptVersion(
            id=uuid.uuid4().hex,
            editing_session_id=editing_session["id"],
            version_number=1,
            prompt="auditable immutable prompt",
            settings_data={"settings_version": 1, "max_clips": 1},
        )
        async with self.database.sessions() as session:
            async with session.begin():
                session.add_all((source, version))
        job_id = uuid.uuid4().hex
        self.store._prepare_job_directories(job_id, include_input=False)
        async with self.database.sessions() as session:
            async with session.begin():
                session.add(
                    VideoJob(
                        id=job_id,
                        editing_session_id=editing_session["id"],
                        prompt_version_id=version.id,
                        attempt_number=1,
                        is_favorite=True,
                        state="completed",
                        progress=Decimal("1"),
                        prompt=version.prompt,
                        request_data={"settings_version": 1, "max_clips": 1},
                        input_data={
                            "source_kind": "session_input_video",
                            "input_video_id": source.id,
                            "sha256": source.sha256,
                            "size": source.expected_size,
                        },
                        result_data={},
                        completed_at=now,
                        media_expires_at=now + timedelta(days=7),
                        audit_expires_at=now + timedelta(days=30),
                    )
                )
        job = await self.store.load(job_id)

        listed = await self.audit.list_jobs(limit=10)
        item = listed["items"][0]
        self.assertEqual(item["prompt_version_id"], version.id)
        self.assertEqual(item["attempt_number"], 1)
        self.assertTrue(item["is_favorite"])
        self.assertEqual(item["favorite_selection_source"], "human")
        self.assertEqual(item["source_sha256"], source.sha256)
        self.assertEqual(item["settings_version"], 1)

        shown = await self.audit.show_job(job["id"])
        self.assertEqual(shown["editing_session"]["workflow_version"], 2)
        self.assertEqual(shown["editing_session"]["input_video"]["id"], source.id)
        self.assertEqual(shown["job"]["prompt_version_id"], version.id)

    async def test_json_is_full_sanitized_versioned_and_idempotent(self):
        job = await self.create_job(prompt="retain the complete plan")
        document = self.store.output_dir(job["id"]) / "manifest.json"
        secret = "audit-secret-marker"
        with patch.dict(os.environ, {"NINEROUTER_KEY": secret}, clear=False):
            document.write_text(
                json.dumps(
                    {
                        "plan": {"clips": [{"title": "Useful moment", "score": 0.9}]},
                        "provider": {"api_key": secret},
                        "credentials": {
                            "keys": ["unknown-key-one", "unknown-key-two"],
                            "password": {"nested": "unknown-password"},
                        },
                        "long_text": "x" * 1000,
                    }
                ),
                encoding="utf-8",
            )
            await self.store.register_artifact(job["id"], document, kind="manifest")
            await self.store.register_artifact(job["id"], document, kind="manifest")

        rows = await self.audit.documents(job["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "parsed")
        self.assertEqual(len(rows[0]["parsed_data"]["long_text"]), 1000)
        self.assertNotIn(secret, rows[0]["raw_text"])
        self.assertNotIn("unknown-key-one", rows[0]["raw_text"])
        self.assertNotIn("unknown-password", rows[0]["raw_text"])
        self.assertEqual(rows[0]["parsed_data"]["provider"]["api_key"], "***")
        self.assertEqual(rows[0]["parsed_data"]["credentials"]["keys"], "***")

        document.write_text('{"plan": {"clips": []}, "revision": 2}', encoding="utf-8")
        await self.store.register_artifact(job["id"], document, kind="manifest")
        changed = await self.audit.documents(job["id"])
        self.assertEqual(len(changed), 2)
        self.assertEqual(len({item["sha256"] for item in changed}), 2)

    async def test_srt_invalid_and_oversized_documents_remain_auditable(self):
        job = await self.create_job()
        subtitle = self.store.output_dir(job["id"]) / "short-01.srt"
        subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
            "2\n00:00:00,500 --> 00:00:02,000\nOverlap\n",
            encoding="utf-8",
        )
        await self.store.register_artifact(job["id"], subtitle, kind="subtitles")
        invalid = self.store.output_dir(job["id"]) / "broken.json"
        invalid.write_text("{broken", encoding="utf-8")
        await self.store.register_artifact(job["id"], invalid, kind="audit_json")

        small_audit = AuditService(self.store, max_document_bytes=1024)
        oversized = self.store.output_dir(job["id"]) / "oversized.json"
        oversized.write_text(json.dumps({"text": "x" * 5000}), encoding="utf-8")
        await self.store.register_artifact(job["id"], oversized, kind="audit_json")
        result = await small_audit.ingest_artifact(job["id"], oversized.name)

        documents = {item["source_name"]: item for item in await self.audit.documents(job["id"])}
        self.assertEqual(documents[subtitle.name]["parsed_data"]["ordering_errors"], 1)
        self.assertEqual(documents[invalid.name]["parse_error_code"], "JSON_INVALID")
        self.assertEqual(result["parse_error_code"], "DOCUMENT_TOO_LARGE")
        self.assertEqual(result["raw_text"] if "raw_text" in result else "", "")

    async def test_binary_artifacts_are_never_ingested(self):
        job = await self.create_job()
        video = self.store.output_dir(job["id"]) / "short-01.mp4"
        video.write_bytes(b"private-video-bytes")
        await self.store.register_artifact(job["id"], video, kind="video")
        self.assertEqual(await self.audit.documents(job["id"]), [])


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class DeterministicQualityTests(AuditPostgresTestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    async def test_valid_video_and_subtitles_receive_structural_approval(self):
        job = await self.create_job()
        output = self.store.output_dir(job["id"])
        video = output / "short-01.mp4"
        completed = await asyncio.to_thread(
            subprocess.run,
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x568:r=25",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-t",
                "1",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                str(video),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        subtitle = output / "short-01.srt"
        subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:00,900\nHello\n\n"
            "2\n00:00:00,500 --> 00:00:00,950\nOverlap\n",
            encoding="utf-8",
        )
        await self.store.register_artifact(job["id"], subtitle, kind="subtitles")
        subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:00,900\nHello\n",
            encoding="utf-8",
        )
        frame_quality = output / "frame_quality_qa.json"
        frame_quality.write_text(
            json.dumps({"version": "frame_quality_qa.v1", "status": "pass"}),
            encoding="utf-8",
        )
        promotion = output / "render_promotion.json"
        promotion.write_text(
            json.dumps({
                "version": "render_promotion.v1",
                "mode": "enforce",
                "decision": "promote",
                "status": "pass",
                "blocker_codes": [],
            }),
            encoding="utf-8",
        )
        manifest = output / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "plan": {
                        "clips": [
                            {"start_ms": 0, "end_ms": 1000, "duration_ms": 1000}
                        ]
                    },
                    "agentic": {
                        "render_promotion": {
                            "artifact": promotion.name,
                            "mode": "enforce",
                            "decision": "promote",
                            "blocker_codes": [],
                        }
                    },
                    "outputs": [
                        {
                            "video": video.name,
                            "subtitles": subtitle.name,
                            "clip": {
                                "start_ms": 0,
                                "end_ms": 1000,
                                "duration_ms": 1000,
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        await self.store.register_artifact(job["id"], video, kind="video")
        await self.store.register_artifact(job["id"], subtitle, kind="subtitles")
        await self.store.register_artifact(job["id"], frame_quality, kind="frame_quality_qa")
        await self.store.register_artifact(job["id"], promotion, kind="render_promotion")
        await self.store.register_artifact(job["id"], manifest, kind="manifest")

        review = await self.audit.verify_job(job["id"])

        self.assertEqual(review["verdict"], "approved")
        self.assertEqual(review["findings"]["scope"], "deterministic_structural")
        self.assertFalse(review["findings"]["creative_quality_evaluated"])
        video_check = next(
            item for item in review["findings"]["checks"] if item["code"] == "VIDEO_STRUCTURE"
        )
        self.assertGreater(video_check["size"], 0)
        self.assertEqual(len(video_check["sha256"]), 64)
        checks = {item["code"]: item for item in review["findings"]["checks"]}
        self.assertEqual(checks["RENDER_PROMOTION"]["status"], "pass")
        self.assertEqual(checks["FRAME_QUALITY_EVIDENCE"]["status"], "pass")

    async def test_missing_outputs_cannot_receive_approval(self):
        job = await self.create_job()
        manifest = self.store.output_dir(job["id"]) / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "plan": {"clips": [{"start_ms": 0, "end_ms": 1000}]},
                    "outputs": [
                        {
                            "video": "missing.mp4",
                            "subtitles": None,
                            "clip": {"start_ms": 0, "end_ms": 1000},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        await self.store.register_artifact(job["id"], manifest, kind="manifest")
        review = await self.audit.verify_job(job["id"])
        self.assertEqual(review["verdict"], "rejected")
        self.assertIn(
            "VIDEO_ARTIFACT_MISSING",
            {item["code"] for item in review["findings"]["checks"]},
        )


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class AuditBackfillTests(AuditPostgresTestCase):
    async def test_backfill_is_bounded_and_idempotent(self):
        job = await self.create_job()
        self.store.audit = None
        transcript = self.store.output_dir(job["id"]) / "transcript.json"
        transcript.write_text('{"segments": []}', encoding="utf-8")
        await self.store.register_artifact(job["id"], transcript, kind="transcript")

        preview = await self.audit.backfill(dry_run=True, limit=10)
        applied = await self.audit.backfill(dry_run=False, limit=10)
        repeated = await self.audit.backfill(dry_run=False, limit=10)

        self.assertEqual(preview["would_ingest"], 2)
        self.assertEqual(applied["ingested"], 2)
        self.assertEqual(repeated["scanned"], 0)
        async with self.database.sessions() as session:
            count = await session.scalar(select(func.count()).select_from(AuditDocument))
        self.assertEqual(count, 2)
        self.assertEqual(
            {item["source_name"] for item in await self.audit.documents(job["id"])},
            {"job.json", "transcript.json"},
        )


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class AuditCLITests(AuditPostgresTestCase):
    async def test_cli_outputs_are_bounded_machine_readable_and_review_uses_stdin(self):
        job = await self.create_job(prompt="private prompt remains in authorized show only")
        manifest = self.store.output_dir(job["id"]) / "manifest.json"
        manifest.write_text('{"plan": {"clips": []}}', encoding="utf-8")
        await self.store.register_artifact(job["id"], manifest, kind="manifest")
        environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "DATABASE_URL": self.database_url,
        }

        listed = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "open_storyline.mvp.admin",
                "audit",
                "list",
                "--since",
                "24h",
                "--limit",
                "1",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(listed.stdout)["items"][0]["id"], job["id"])

        documents = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "open_storyline.mvp.admin",
                "audit",
                "documents",
                job["id"],
                "--format",
                "ndjson",
            ],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(documents.stdout)["source_name"], "manifest.json")

        for command in (
            ["show", job["id"], "--limit", "1", "--format", "json"],
            ["events", job["id"], "--limit", "10", "--format", "json"],
            ["verify", job["id"], "--format", "json"],
            ["backfill", "--dry-run", "--limit", "10", "--format", "json"],
        ):
            completed = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "open_storyline.mvp.admin",
                    "audit",
                    *command,
                ],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIsNotNone(json.loads(completed.stdout))

        review_input = json.dumps(
            {
                "verdict": "needs_review",
                "source": "agent",
                "reviewer_label": "qa-agent",
                "notes": "private review note",
                "findings": {"code": "CREATIVE_REVIEW_REQUIRED"},
            }
        )
        reviewed = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "open_storyline.mvp.admin",
                "audit",
                "review",
                job["id"],
                "--input",
                "-",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=environment,
            input=review_input,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(reviewed.stdout)
        self.assertEqual(payload["source"], "agent")
        self.assertEqual(payload["verdict"], "needs_review")

    async def test_latest_verdict_filters_and_show_output_are_bounded(self):
        job = await self.create_job()
        await self.audit.add_review(
            job["id"],
            verdict="rejected",
            source="human",
            reviewer_label="first-pass",
            notes=None,
            findings={},
        )
        await self.audit.add_review(
            job["id"],
            verdict="approved",
            source="human",
            reviewer_label="second-pass",
            notes=None,
            findings={},
        )

        approved = await self.audit.list_jobs(verdict="approved")
        rejected = await self.audit.list_jobs(verdict="rejected")
        shown = await self.audit.show_job(job["id"], limit=1)

        self.assertEqual(approved["items"][0]["latest_verdict"], "approved")
        self.assertEqual(rejected["items"], [])
        self.assertEqual(len(shown["reviews"]), 1)
        self.assertTrue(shown["truncated"]["reviews"])

    async def test_audit_failure_never_rewrites_a_completed_job_as_failed(self):
        class FailingAudit:
            async def ingest_artifact(self, _job_id, _artifact_name):
                raise RuntimeError("private audit backend detail")

            async def ingest_job_snapshot(self, _job_id):
                raise RuntimeError("private audit backend detail")

            async def verify_job(self, _job_id):
                raise RuntimeError("private audit backend detail")

        job = await self.create_job()
        self.store.attach_audit(FailingAudit())
        manifest = self.store.output_dir(job["id"]) / "manifest.json"
        manifest.write_text('{"plan": {"clips": []}}', encoding="utf-8")
        await self.store.register_artifact(job["id"], manifest, kind="manifest")
        manager = JobManager(self.store, lambda _job_id, _store: asyncio.sleep(0, result={}))

        await manager._process(job["id"])

        state = await self.store.load(job["id"])
        events = await self.store.events(job["id"])
        self.assertEqual(state["state"], "completed")
        self.assertGreaterEqual(
            sum(item["event_type"] == "audit_operation_failed" for item in events),
            3,
        )
        self.assertNotIn(
            "private audit backend detail",
            json.dumps(events, sort_keys=True),
        )

    async def test_structured_logs_correlate_without_event_payloads(self):
        job = await self.create_job(prompt="log-secret-prompt")
        logger = logging.getLogger("openstoryline.mvp")
        with self.assertLogs(logger, level="INFO") as captured:
            await self.store.update(
                job["id"],
                stage="remote_planning",
                provider_detail="log-secret-provider-body",
            )
        serialized = "\n".join(captured.output)
        self.assertIn(job["id"], serialized)
        self.assertIn("job_stage_changed", serialized)
        self.assertNotIn("log-secret-prompt", serialized)
        self.assertNotIn("log-secret-provider-body", serialized)
        payload = json.loads(captured.records[0].getMessage())
        self.assertRegex(payload["timestamp"], r"Z$")


class AuditPureTests(unittest.TestCase):
    def test_database_url_credentials_are_sanitized(self):
        from open_storyline.mvp.security import sanitize_text

        value = sanitize_text(
            "postgresql+psycopg://openstoryline:private-password@db:5432/openstoryline"
        )
        self.assertEqual(
            value,
            "postgresql+psycopg://openstoryline:***@db:5432/openstoryline",
        )

    def test_admin_configuration_errors_are_machine_readable(self):
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        environment.pop("DATABASE_URL", None)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "open_storyline.mvp.admin",
                "audit",
                "list",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            json.loads(completed.stderr),
            {"code": "DATABASE_CONFIG_INVALID", "ok": False},
        )
        self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
