from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url

from open_storyline.mvp.api import create_mvp_router
from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.jobs import JobManager, JobStore, JobStoreError
from open_storyline.mvp.retention import RetentionService, RetentionSettings


ROOT = Path(__file__).resolve().parents[1]


def _integration_url() -> str:
    raw = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw:
        return ""
    url = make_url(normalize_database_url(raw))
    if not str(url.database or "").startswith("openstoryline_test"):
        raise RuntimeError("TEST_DATABASE_URL must use an openstoryline_test database")
    return raw


class PostgresTestCase(unittest.IsolatedAsyncioTestCase):
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
                    "video_jobs, editing_sessions, auth_sessions, login_attempt_buckets"
                )
            )
        self.temporary_directory = TemporaryDirectory()
        self.store = JobStore(self.temporary_directory.name, self.database)

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary_directory.cleanup()

    async def create_queued_job(self, *, prompt: str = "make shorts"):
        editing_session = await self.store.create_session("Test session")
        state = await self.store.create(
            editing_session_id=editing_session["id"],
            prompt=prompt,
            filename="../../talk.mp4",
            max_clips=4,
        )
        source = self.store.input_path(state["id"], "talk.mp4")
        source.write_bytes(b"video")
        return editing_session, await self.store.mark_uploaded(state["id"], source, 5)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class JobStoreTests(PostgresTestCase):
    async def test_postgres_is_authoritative_and_artifacts_remain_job_scoped(self):
        _editing_session, state = await self.create_queued_job()
        artifact = self.store.output_dir(state["id"]) / "short-01.mp4"
        artifact.write_bytes(b"result")
        await self.store.register_artifact(state["id"], artifact, kind="video")

        self.store._state_path(state["id"]).write_text("{broken", encoding="utf-8")
        restored = await JobStore(
            self.temporary_directory.name,
            self.database,
        ).load(state["id"])

        self.assertEqual(restored["input"]["original_filename"], "talk.mp4")
        self.assertEqual(restored["artifacts"][0]["name"], "short-01.mp4")
        self.assertEqual(
            await self.store.resolve_artifact(state["id"], "short-01.mp4"),
            artifact,
        )
        with self.assertRaises(JobStoreError):
            await self.store.resolve_artifact(state["id"], "../short-01.mp4")
        events = await self.store.events(state["id"])
        self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
        self.assertIn("artifact_registered", [event["event_type"] for event in events])

    async def test_concurrent_updates_are_serialized_without_lost_versions(self):
        _editing_session, state = await self.create_queued_job()
        starting_version = state["version"]

        await asyncio.gather(
            self.store.update(state["id"], stage="one"),
            self.store.update(state["id"], stage="two"),
        )

        restored = await self.store.load(state["id"])
        events = await self.store.events(state["id"])
        self.assertEqual(restored["version"], starting_version + 2)
        self.assertEqual(len({event["sequence"] for event in events}), len(events))
        self.assertIn(restored["stage"], {"one", "two"})

    async def test_database_reconnect_reconstructs_job_without_snapshot(self):
        _editing_session, state = await self.create_queued_job()
        self.store._state_path(state["id"]).unlink()
        second_database = Database(self.database_url)
        try:
            restored = await JobStore(
                self.temporary_directory.name,
                second_database,
            ).load(state["id"])
        finally:
            await second_database.dispose()
        self.assertEqual(restored["state"], "queued")
        self.assertEqual(restored["editing_session_id"], state["editing_session_id"])


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class WorkerCoordinationTests(PostgresTestCase):
    async def test_missing_job_directory_records_failure_without_crashing_worker(self):
        _editing_session, state = await self.create_queued_job(prompt="missing directory")
        shutil.rmtree(self.store._job_dir(state["id"]))
        manager = JobManager(
            self.store,
            lambda job_id, store: store.source_path(job_id),
        )

        await manager._process(state["id"])

        failed = await self.store.load(state["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["error"]["code"], "JOB_INPUT_MISSING")
        self.assertTrue(
            (self.store.output_dir(state["id"]) / "failure.json").is_file()
        )

    async def test_two_managers_process_a_job_only_once(self):
        _editing_session, state = await self.create_queued_job()
        processed: list[str] = []

        async def processor(job_id: str, current_store: JobStore):
            processed.append(job_id)
            artifact = current_store.output_dir(job_id) / "done.txt"
            artifact.write_text("done", encoding="utf-8")
            await current_store.register_artifact(job_id, artifact, kind="manifest")
            return {"processor": "test"}

        first = JobManager(self.store, processor, poll_interval=0.05)
        second = JobManager(self.store, processor, poll_interval=0.05)
        await first.start()
        await second.start()
        try:
            await first.enqueue(state["id"])
            restored = await first.wait_for_terminal(state["id"], timeout=5)
            self.assertEqual(sum([first.is_leader, second.is_leader]), 1)
        finally:
            await first.stop()
            await second.stop()

        self.assertEqual(processed, [state["id"]])
        self.assertEqual(restored["state"], "completed")
        self.assertEqual(restored["processor"], "test")

    async def test_standby_reacquires_leadership_after_release(self):
        processed: list[str] = []

        async def processor(job_id: str, _store: JobStore):
            processed.append(job_id)
            return None

        first = JobManager(self.store, processor, poll_interval=0.05)
        second = JobManager(self.store, processor, poll_interval=0.05)
        await first.start()
        await second.start()
        self.assertTrue(
            await first.wait_until_leader(timeout=2)
            or await second.wait_until_leader(timeout=2)
        )
        leader, standby = (first, second) if first.is_leader else (second, first)
        await leader.stop()
        try:
            self.assertTrue(await standby.wait_until_leader(timeout=3))
            _editing_session, state = await self.create_queued_job(prompt="after handoff")
            await standby.enqueue(state["id"])
            await standby.wait_for_terminal(state["id"], timeout=5)
        finally:
            await standby.stop()
        self.assertEqual(processed, [state["id"]])

    async def test_connection_loss_releases_leadership_and_job_is_recovered_once(self):
        processed: list[str] = []

        async def processor(job_id: str, _store: JobStore):
            processed.append(job_id)
            return None

        first = JobManager(self.store, processor, poll_interval=0.05)
        second = JobManager(self.store, processor, poll_interval=0.05)
        await first.start()
        await second.start()
        self.assertTrue(
            await first.wait_until_leader(timeout=2)
            or await second.wait_until_leader(timeout=2)
        )
        leader = first if first.is_leader else second
        self.assertIsNotNone(leader._lock_connection)
        await leader._lock_connection.close()
        deadline = asyncio.get_running_loop().time() + 3
        while leader.is_leader and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        while asyncio.get_running_loop().time() < deadline:
            if sum([first.is_leader, second.is_leader]) == 1:
                break
            await asyncio.sleep(0.05)
        try:
            _editing_session, state = await self.create_queued_job(prompt="after loss")
            await first.enqueue(state["id"])
            restored = await first.wait_for_terminal(state["id"], timeout=5)
        finally:
            await first.stop()
            await second.stop()
        self.assertEqual(restored["state"], "completed")
        self.assertEqual(processed, [state["id"]])

    async def test_connection_loss_does_not_overlap_an_active_processor(self):
        started = asyncio.Event()
        release = asyncio.Event()
        processed: list[str] = []

        async def processor(job_id: str, _store: JobStore):
            processed.append(job_id)
            started.set()
            await release.wait()
            return None

        _editing_session, state = await self.create_queued_job(prompt="active handoff")
        first = JobManager(self.store, processor, poll_interval=0.05)
        second = JobManager(self.store, processor, poll_interval=0.05)
        await first.start()
        await second.start()
        try:
            await first.enqueue(state["id"])
            await asyncio.wait_for(started.wait(), timeout=3)
            leader = first if first.is_leader else second
            standby = second if leader is first else first
            self.assertIsNotNone(leader._lock_connection)
            self.assertIsNotNone(leader._execution_connection)
            await leader._lock_connection.close()
            await asyncio.sleep(leader.poll_interval * 4)
            self.assertFalse(standby.is_leader)
            self.assertEqual(processed, [state["id"]])
            release.set()
            restored = await first.wait_for_terminal(state["id"], timeout=5)
            self.assertTrue(
                await first.wait_until_leader(timeout=3)
                or await second.wait_until_leader(timeout=3)
            )
        finally:
            release.set()
            await first.stop()
            await second.stop()
        self.assertEqual(restored["state"], "completed")
        self.assertEqual(processed, [state["id"]])

    async def test_failure_events_and_manifest_are_sanitized(self):
        class ProviderFailure(RuntimeError):
            code = "STT_ALL_PROVIDERS_FAILED"

            def to_dict(self):
                return {
                    "attempts": [
                        {"model": "one", "reason": "Bearer super-secret failed"},
                        {"model": "two", "reason": "api_key=super-secret quota"},
                    ],
                    "api_key": "super-secret",
                }

        _editing_session, state = await self.create_queued_job(prompt="fail closed")

        async def processor(job_id: str, current_store: JobStore):
            await current_store.update(job_id, stage="remote_transcription")
            raise ProviderFailure("Bearer super-secret unavailable")

        with patch.dict(os.environ, {"NINEROUTER_KEY": "super-secret"}, clear=False):
            manager = JobManager(self.store, processor, poll_interval=0.05)
            await manager.start()
            try:
                await manager.enqueue(state["id"])
                failed = await manager.wait_for_terminal(state["id"], timeout=5)
            finally:
                await manager.stop()

        failure_path = await self.store.resolve_artifact(state["id"], "failure.json")
        events = await self.store.events(state["id"])
        serialized = json.dumps(
            {
                "state": failed,
                "events": events,
                "manifest": json.loads(failure_path.read_text(encoding="utf-8")),
            }
        )
        self.assertEqual(failed["error"]["code"], "STT_ALL_PROVIDERS_FAILED")
        self.assertNotIn("super-secret", serialized)
        self.assertIn("remote_transcription", serialized)

    async def test_active_capacity_is_not_a_time_window_quota(self):
        store = JobStore(
            self.temporary_directory.name,
            self.database,
            max_active_jobs=1,
        )
        editing_session = await store.create_session("Capacity")
        await store.create(
            editing_session_id=editing_session["id"],
            prompt="first",
            filename="one.mp4",
        )
        with self.assertRaises(JobStoreError) as raised:
            await store.create(
                editing_session_id=editing_session["id"],
                prompt="second",
                filename="two.mp4",
            )
        self.assertEqual(raised.exception.code, "JOB_QUEUE_FULL")


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class LegacyImportTests(PostgresTestCase):
    async def test_dry_run_apply_and_repeat_are_safe_and_idempotent(self):
        root = Path(self.temporary_directory.name)
        first_id = "1" * 32
        second_id = "2" * 32
        first = root / first_id
        second = root / second_id
        corrupt = root / ("3" * 32)
        invalid = root / "not-a-job"
        for directory in (first, second, corrupt, invalid):
            (directory / "output").mkdir(parents=True)
        (first / "output" / "manifest.json").write_text('{"ok": true}', encoding="utf-8")
        (first / "job.json").write_text(
            json.dumps(
                {
                    "id": first_id,
                    "state": "completed",
                    "progress": 1,
                    "prompt": "legacy prompt",
                    "request": {"max_clips": 2},
                    "input": {"original_filename": "talk.mp4"},
                    "artifacts": [
                        {"name": "manifest.json", "kind": "manifest"},
                        {"name": "missing.srt", "kind": "subtitles"},
                        {"name": "../unsafe.json", "kind": "manifest"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (second / "job.json").write_text(
            json.dumps(
                {
                    "id": second_id,
                    "state": "failed",
                    "progress": 0.3,
                    "prompt": "failed legacy",
                    "request": {},
                    "input": {},
                    "error": {"code": "LEGACY_FAILURE"},
                    "artifacts": [],
                }
            ),
            encoding="utf-8",
        )
        (corrupt / "job.json").write_text("{broken", encoding="utf-8")
        original_manifest = (first / "output" / "manifest.json").read_bytes()

        dry_run = await self.store.import_legacy_jobs(root, dry_run=True)
        self.assertEqual(dry_run["would_import"], 2)
        self.assertEqual((await self.store.list_sessions())["items"], [])

        applied = await self.store.import_legacy_jobs(root, dry_run=False)
        repeated = await self.store.import_legacy_jobs(root, dry_run=False)

        self.assertEqual(applied["imported"], 2)
        self.assertEqual(applied["missing_artifacts"], 1)
        self.assertEqual(applied["unsafe_artifacts"], 1)
        self.assertEqual(repeated["already_present"], 2)
        self.assertEqual((await self.store.load(first_id))["state"], "completed")
        self.assertEqual(
            (first / "output" / "manifest.json").read_bytes(),
            original_manifest,
        )

    async def test_admin_dry_run_reports_counts_without_private_content(self):
        root = Path(self.temporary_directory.name)
        job_id = "4" * 32
        job_dir = root / job_id
        (job_dir / "output").mkdir(parents=True)
        (job_dir / "job.json").write_text(
            json.dumps(
                {
                    "id": job_id,
                    "state": "failed",
                    "prompt": "private prompt must not be reported",
                    "request": {},
                    "input": {},
                    "artifacts": [],
                }
            ),
            encoding="utf-8",
        )
        completed = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "open_storyline.mvp.admin",
                "import-legacy-jobs",
                "--root",
                str(root),
                "--dry-run",
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "DATABASE_URL": self.database_url,
            },
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(completed.stdout)
        self.assertEqual(report["would_import"], 1)
        self.assertNotIn("private prompt", completed.stdout)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class JobAPITests(PostgresTestCase):
    async def test_session_upload_status_and_download_contracts(self):
        manager = JobManager(self.store)
        retention = RetentionService(
            self.store,
            RetentionSettings(False, 7, 30, 3600, 100),
        )
        app = FastAPI()
        app.include_router(
            create_mvp_router(
                lambda: self.store,
                lambda: manager,
                lambda: retention,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created_session = await client.post(
                "/api/mvp/sessions",
                json={"title": "API session"},
            )
            self.assertEqual(created_session.status_code, 201)
            session_id = created_session.json()["id"]
            retired = await client.post("/api/mvp/jobs")
            self.assertEqual(retired.status_code, 409)
            self.assertEqual(retired.json()["detail"]["code"], "SESSION_REQUIRED")

            created = await client.post(
                f"/api/mvp/sessions/{session_id}/jobs",
                data={
                    "prompt": "make four vertical clips",
                    "max_clips": "4",
                    "edit_mode": "agentic",
                    "asset_policy": "off",
                },
                files={"file": ("talk.mp4", b"fake-video", "video/mp4")},
            )
            self.assertEqual(created.status_code, 202)
            job = created.json()
            self.assertEqual(job["state"], "queued")
            self.assertEqual(job["editing_session_id"], session_id)
            self.assertEqual(job["request"]["edit_mode"], "agentic")
            self.assertEqual(job["request"]["asset_policy"], "off")

            resumed = await client.get(f"/api/mvp/sessions/{session_id}")
            self.assertEqual(resumed.status_code, 200)
            self.assertEqual(resumed.json()["jobs"][0]["id"], job["id"])

            artifact = self.store.output_dir(job["id"]) / "manifest.json"
            artifact.write_text("{}", encoding="utf-8")
            await self.store.register_artifact(job["id"], artifact, kind="manifest")
            download = await client.get(
                f"/api/mvp/jobs/{job['id']}/artifacts/manifest.json"
            )
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.content, b"{}")

            bundle = await client.get(f"/api/mvp/jobs/{job['id']}/bundle")
            self.assertEqual(bundle.status_code, 200)
            bundle_path = Path(self.temporary_directory.name) / "download.zip"
            bundle_path.write_bytes(bundle.content)
            with zipfile.ZipFile(bundle_path) as archive:
                self.assertIn("manifest.json", archive.namelist())

            traversal = await client.get(
                f"/api/mvp/jobs/{job['id']}/artifacts/%2E%2E%2Fmanifest.json"
            )
            self.assertIn(traversal.status_code, {404, 400})

    async def test_session_delete_is_idempotent_and_rejects_active_jobs(self):
        manager = JobManager(self.store)
        retention = RetentionService(
            self.store,
            RetentionSettings(False, 7, 30, 3600, 100),
        )
        app = FastAPI()
        app.include_router(
            create_mvp_router(
                lambda: self.store,
                lambda: manager,
                lambda: retention,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            empty = await client.post("/api/mvp/sessions", json={"title": "Delete me"})
            session_id = empty.json()["id"]
            deleted = await client.delete(f"/api/mvp/sessions/{session_id}")
            repeated = await client.delete(f"/api/mvp/sessions/{session_id}")
            resumed = await client.get(f"/api/mvp/sessions/{session_id}")

            active_session = await client.post(
                "/api/mvp/sessions",
                json={"title": "Active"},
            )
            active_id = active_session.json()["id"]
            await self.store.create(
                editing_session_id=active_id,
                prompt="active job",
                filename="active.mp4",
            )
            conflict = await client.delete(f"/api/mvp/sessions/{active_id}")

        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(deleted.json()["already_deleted"])
        self.assertTrue(repeated.json()["already_deleted"])
        self.assertEqual(resumed.status_code, 404)
        self.assertEqual(resumed.json()["detail"]["code"], "SESSION_NOT_FOUND")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["code"], "SESSION_ACTIVE_JOBS")


if __name__ == "__main__":
    unittest.main()
