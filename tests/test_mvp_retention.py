from __future__ import annotations

from datetime import UTC, datetime, timedelta
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

from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url

from open_storyline.mvp.audit import AuditService
from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.jobs import JobStore, JobStoreError
from open_storyline.mvp.models import Artifact, EditingSession, VideoJob
from open_storyline.mvp.retention import (
    RetentionScheduler,
    RetentionService,
    RetentionSettings,
)


ROOT = Path(__file__).resolve().parents[1]


def _integration_url() -> str:
    raw = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw:
        return ""
    url = make_url(normalize_database_url(raw))
    if not str(url.database or "").startswith("openstoryline_test"):
        raise RuntimeError("TEST_DATABASE_URL must use an openstoryline_test database")
    return raw


class RetentionSettingsTests(unittest.TestCase):
    def test_defaults_are_disabled_and_preserve_the_product_policy(self):
        environment = {
            "OPENSTORYLINE_RETENTION_ENABLED": "false",
            "OPENSTORYLINE_MEDIA_RETENTION_DAYS": "7",
            "OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS": "24",
            "OPENSTORYLINE_AUDIT_RETENTION_DAYS": "30",
            "OPENSTORYLINE_RETENTION_INTERVAL_SECONDS": "86400",
            "OPENSTORYLINE_RETENTION_BATCH_SIZE": "100",
        }
        with patch.dict(os.environ, environment, clear=False):
            settings = RetentionSettings.from_env()

        self.assertFalse(settings.enabled)
        self.assertEqual(settings.media_days, 7)
        self.assertEqual(settings.incomplete_upload_hours, 24)
        self.assertEqual(settings.audit_days, 30)
        self.assertEqual(settings.interval_seconds, 86400)
        self.assertEqual(settings.batch_size, 100)

    def test_invalid_or_unbounded_values_fail_closed(self):
        for name, value in (
            ("OPENSTORYLINE_RETENTION_ENABLED", "yes"),
            ("OPENSTORYLINE_MEDIA_RETENTION_DAYS", "0"),
            ("OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS", "169"),
            ("OPENSTORYLINE_AUDIT_RETENTION_DAYS", "forever"),
            ("OPENSTORYLINE_RETENTION_INTERVAL_SECONDS", "60"),
            ("OPENSTORYLINE_RETENTION_BATCH_SIZE", "1001"),
        ):
            with self.subTest(name=name):
                with patch.dict(os.environ, {name: value}, clear=False):
                    with self.assertRaises(JobStoreError) as raised:
                        RetentionSettings.from_env()
                self.assertEqual(raised.exception.code, "RETENTION_CONFIG_INVALID")


class RetentionPostgresTestCase(unittest.IsolatedAsyncioTestCase):
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
        self.now = datetime.now(UTC)
        self.settings = RetentionSettings(
            enabled=False,
            media_days=7,
            audit_days=30,
            interval_seconds=3600,
            batch_size=20,
        )
        self.store = JobStore(
            Path(self.temporary_directory.name) / "mvp_jobs",
            self.database,
            media_retention_days=7,
            audit_retention_days=30,
        )
        self.audit = AuditService(self.store)
        self.store.attach_audit(self.audit)
        self.retention = RetentionService(
            self.store,
            self.settings,
            now=lambda: self.now,
        )
        self.store.attach_retention(self.retention)

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary_directory.cleanup()

    async def create_terminal_job(self, title: str = "Retention session"):
        editing_session = await self.store.create_session(title)
        job = await self.store.create(
            editing_session_id=editing_session["id"],
            prompt="private prompt retained for audit",
            filename="source.mp4",
        )
        source = self.store.input_path(job["id"], "source.mp4")
        source.write_bytes(b"source-video")
        await self.store.mark_uploaded(job["id"], source, source.stat().st_size)

        output = self.store.output_dir(job["id"])
        files = {
            "video": output / "short-01.mp4",
            "bundle": output / "all-clips.zip",
            "manifest": output / "manifest.json",
            "subtitles": output / "short-01.srt",
        }
        files["video"].write_bytes(b"rendered-video")
        files["bundle"].write_bytes(b"bundle")
        files["manifest"].write_text(
            '{"outputs": [], "plan": {"clips": []}}',
            encoding="utf-8",
        )
        files["subtitles"].write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nRetained subtitle\n",
            encoding="utf-8",
        )
        await self.store.register_artifact(job["id"], files["video"], kind="video")
        await self.store.register_artifact(job["id"], files["bundle"], kind="bundle")
        await self.store.register_artifact(job["id"], files["manifest"], kind="manifest")
        await self.store.register_artifact(job["id"], files["subtitles"], kind="subtitles")
        completed = await self.store.update(
            job["id"],
            state="completed",
            progress=1.0,
            clip_count=1,
            event_type="job_completed",
        )
        await self.audit.ingest_job_snapshot(job["id"])
        await self.audit.add_review(
            job["id"],
            verdict="needs_review",
            source="agent",
            reviewer_label="retention-test",
            notes="retained review",
            findings={"code": "MANUAL_REVIEW"},
        )
        return editing_session, completed, files

    async def expire_job(
        self,
        job_id: str,
        *,
        media: bool = False,
        audit: bool = False,
    ) -> None:
        values = {}
        if media:
            values["media_expires_at"] = self.now - timedelta(seconds=1)
        if audit:
            values["audit_expires_at"] = self.now - timedelta(seconds=1)
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(VideoJob).where(VideoJob.id == job_id).values(**values)
            )
            if audit:
                session_id = await connection.scalar(
                    select(VideoJob.editing_session_id).where(VideoJob.id == job_id)
                )
                await connection.execute(
                    update(EditingSession)
                    .where(EditingSession.id == session_id)
                    .values(audit_expires_at=self.now - timedelta(seconds=1))
                )


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class RetentionDecisionTests(RetentionPostgresTestCase):
    async def test_binary_and_text_artifacts_receive_their_distinct_expiry(self):
        _editing_session, job, _files = await self.create_terminal_job()
        state = await self.store.load(job["id"])
        artifacts = {item["name"]: item for item in state["artifacts"]}

        self.assertEqual(
            artifacts["short-01.mp4"]["retention_expires_at"],
            state["media_expires_at"],
        )
        self.assertEqual(
            artifacts["all-clips.zip"]["retention_expires_at"],
            state["media_expires_at"],
        )
        self.assertEqual(
            artifacts["manifest.json"]["retention_expires_at"],
            state["audit_expires_at"],
        )
        self.assertEqual(
            artifacts["short-01.srt"]["retention_expires_at"],
            state["audit_expires_at"],
        )

    async def test_preview_is_repeatable_read_only_and_excludes_active_jobs(self):
        _editing_session, job, files = await self.create_terminal_job()
        await self.expire_job(job["id"], media=True)
        active_session = await self.store.create_session("Active")
        active = await self.store.create(
            editing_session_id=active_session["id"],
            prompt="active job",
            filename="active.mp4",
        )
        active_source = self.store.input_path(active["id"], "active.mp4")
        active_source.write_bytes(b"active")
        await self.store.mark_uploaded(active["id"], active_source, 6)
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(VideoJob)
                .where(VideoJob.id == active["id"])
                .values(media_expires_at=self.now - timedelta(seconds=1))
            )
        events_before = await self.store.events(job["id"])

        first = await self.retention.preview(limit=10)
        second = await self.retention.preview(limit=10)

        self.assertEqual(first, second)
        self.assertEqual(first["media"]["selected"], 1)
        self.assertEqual(first["media"]["items"][0]["job_id"], job["id"])
        self.assertNotEqual(first["media"]["items"][0]["job_id"], active["id"])
        self.assertTrue(files["video"].is_file())
        self.assertEqual(events_before, await self.store.events(job["id"]))

    async def test_audit_hold_blocks_hard_delete_but_not_media_purge(self):
        editing_session, job, files = await self.create_terminal_job()
        await self.expire_job(job["id"], media=True, audit=True)
        await self.retention.set_audit_hold(editing_session["id"], "quality investigation")

        held = await self.retention.run(limit=10)

        self.assertEqual(held["media"]["purged"], 1)
        self.assertEqual(held["audit"]["deleted"], 0)
        self.assertFalse(files["video"].exists())
        self.assertEqual((await self.audit.show_job(job["id"]))["job"]["id"], job["id"])

        await self.retention.clear_audit_hold(editing_session["id"])
        released = await self.retention.run(limit=10)
        repeated = await self.retention.run(limit=10)

        self.assertEqual(released["audit"]["deleted"], 1)
        self.assertEqual(repeated["audit"]["deleted"], 0)
        with self.assertRaises(JobStoreError):
            await self.store.load_for_audit(job["id"])

    async def test_cleanup_advisory_lock_prevents_overlapping_runs(self):
        competing = RetentionService(self.store, self.settings, now=lambda: self.now)

        async with self.retention.cleanup_lock() as acquired:
            result = await competing.run(limit=1)

        self.assertTrue(acquired)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "RETENTION_BUSY")


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class MediaPurgeTests(RetentionPostgresTestCase):
    async def test_media_purge_keeps_small_evidence_and_audit_tombstones(self):
        _editing_session, job, files = await self.create_terminal_job()
        work_file = self.store.work_dir(job["id"]) / "temporary.wav"
        work_file.write_bytes(b"work")
        orphan = self.store.output_dir(job["id"]) / "orphan.mp4"
        orphan.write_bytes(b"orphan")
        await self.expire_job(job["id"], media=True)

        report = await self.retention.run(limit=10)
        state = await self.store.load(job["id"])
        shown = await self.audit.show_job(job["id"])
        artifacts = {item["name"]: item for item in state["artifacts"]}

        self.assertEqual(report["media"]["purged"], 1)
        self.assertFalse(files["video"].exists())
        self.assertFalse(files["bundle"].exists())
        self.assertFalse(work_file.exists())
        self.assertFalse(orphan.exists())
        self.assertTrue(files["manifest"].is_file())
        self.assertTrue(files["subtitles"].is_file())
        self.assertEqual(artifacts["short-01.mp4"]["availability"], "deleted")
        self.assertEqual(artifacts["short-01.mp4"]["purge_reason"], "media_expired")
        self.assertEqual(artifacts["manifest.json"]["availability"], "available")
        self.assertIsNone(state["media_expires_at"])
        self.assertEqual(shown["job"]["prompt"], "private prompt retained for audit")
        self.assertGreaterEqual(len(shown["documents"]), 3)
        self.assertEqual(shown["reviews"][0]["findings"]["code"], "MANUAL_REVIEW")

    async def test_session_delete_is_soft_idempotent_and_rejects_active_jobs(self):
        editing_session = await self.store.create_session("Delete session")
        job = await self.store.create(
            editing_session_id=editing_session["id"],
            prompt="delete after completion",
            filename="source.mp4",
        )
        source = self.store.input_path(job["id"], "source.mp4")
        source.write_bytes(b"source")
        await self.store.mark_uploaded(job["id"], source, 6)
        with self.assertRaises(JobStoreError) as raised:
            await self.retention.delete_session(editing_session["id"])
        self.assertEqual(raised.exception.code, "SESSION_ACTIVE_JOBS")

        await self.store.update(job["id"], state="cancelled", event_type="job_cancelled")
        first = await self.retention.delete_session(editing_session["id"])
        second = await self.retention.delete_session(editing_session["id"])

        self.assertTrue(first["ok"])
        self.assertFalse(first["already_deleted"])
        self.assertTrue(second["already_deleted"])
        self.assertFalse(source.exists())
        with self.assertRaises(JobStoreError):
            await self.store.get_session(editing_session["id"])
        with self.assertRaises(JobStoreError):
            await self.store.load(job["id"])
        self.assertEqual((await self.audit.show_job(job["id"]))["job"]["deleted_at"], first["deleted_at"])

    async def test_unsafe_job_does_not_abort_other_jobs_or_delete_outside_root(self):
        _bad_session, bad, _bad_files = await self.create_terminal_job("Bad path")
        _good_session, good, good_files = await self.create_terminal_job("Good path")
        bad_source = self.store._job_dir(bad["id"]) / "input" / "source.mp4"
        await self.expire_job(bad["id"], media=True)
        await self.expire_job(good["id"], media=True)
        outside = self.store.root / f"{bad['id']}-outside.mp4"
        outside.write_bytes(b"outside")
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    session.add(
                        Artifact(
                            job_id=bad["id"],
                            name="outside.mp4",
                            kind="video",
                            relative_path=f"../{outside.name}",
                            size=outside.stat().st_size,
                            availability="available",
                            retention_expires_at=self.now,
                        )
                    )

            report = await self.retention.run(limit=10)

            self.assertEqual(report["media"]["failed"], 1)
            self.assertEqual(report["media"]["purged"], 1)
            self.assertTrue(outside.is_file())
            self.assertTrue(bad_source.is_file())
            self.assertFalse(good_files["video"].exists())
            self.assertIsNotNone((await self.store.load(bad["id"]))["media_expires_at"])
        finally:
            outside.unlink(missing_ok=True)

    async def test_job_directory_symlink_is_unlinked_without_following_it(self):
        _editing_session, job, _files = await self.create_terminal_job()
        await self.expire_job(job["id"], media=True)
        external = TemporaryDirectory()
        external_file = Path(external.name) / "keep.mp4"
        external_file.write_bytes(b"keep")
        job_dir = self.store._job_dir(job["id"])
        shutil.rmtree(job_dir)
        job_dir.symlink_to(external.name, target_is_directory=True)
        try:
            report = await self.retention.run(limit=10)
            self.assertEqual(report["media"]["purged"], 1)
            self.assertFalse(os.path.lexists(job_dir))
            self.assertTrue(external_file.is_file())
        finally:
            external.cleanup()

    async def test_missing_media_is_reconciled_as_missing_not_deleted(self):
        _editing_session, job, files = await self.create_terminal_job()
        files["video"].unlink()
        await self.expire_job(job["id"], media=True)

        report = await self.retention.run(limit=10)
        artifacts = {
            item["name"]: item
            for item in (await self.store.load(job["id"]))["artifacts"]
        }

        self.assertGreaterEqual(report["media"]["missing_files"], 1)
        self.assertEqual(artifacts["short-01.mp4"]["availability"], "missing")
        self.assertEqual(artifacts["short-01.mp4"]["purge_reason"], "media_missing")


class RetentionSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_scheduler_does_not_start(self):
        class Service:
            settings = RetentionSettings(False, 7, 30, 3600, 10)

            async def run(self, *, limit):
                raise AssertionError(f"unexpected retention run with {limit}")

        scheduler = RetentionScheduler(Service())
        await scheduler.start()
        self.assertIsNone(scheduler._task)

    async def test_enabled_scheduler_runs_once_at_startup_and_stops_cleanly(self):
        called = asyncio.Event()

        class Service:
            settings = RetentionSettings(True, 7, 30, 3600, 10)

            async def run(self, *, limit):
                self.limit = limit
                called.set()
                return {"ok": True, "media": {}, "audit": {}}

        service = Service()
        scheduler = RetentionScheduler(service)
        await scheduler.start()
        await asyncio.wait_for(called.wait(), timeout=1)
        await scheduler.stop()

        self.assertEqual(service.limit, 10)
        self.assertIsNone(scheduler._task)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class RetentionCLITests(RetentionPostgresTestCase):
    async def test_cli_preview_defaults_to_no_mutation_and_hold_uses_stdin(self):
        editing_session, job, files = await self.create_terminal_job()
        await self.expire_job(job["id"], media=True)
        config_path = Path(self.temporary_directory.name) / "config.toml"
        config_path.write_text(
            (ROOT / "config.toml")
            .read_text(encoding="utf-8")
            .replace(
                'outputs_dir = "./outputs"',
                f'outputs_dir = "{Path(self.temporary_directory.name).as_posix()}"',
            ),
            encoding="utf-8",
        )
        environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "DATABASE_URL": self.database_url,
            "OPENSTORYLINE_CONFIG": str(config_path),
            "OPENSTORYLINE_RETENTION_ENABLED": "false",
        }

        preview = await self._admin(environment, "retention", "preview", "--format", "json")
        default_run = await self._admin(
            environment,
            "retention",
            "run",
            "--format",
            "json",
        )
        held = await self._admin(
            environment,
            "audit",
            "hold",
            editing_session["id"],
            "--set",
            "--input",
            "-",
            "--format",
            "json",
            input_value='{"reason":"private retention review"}',
        )

        self.assertEqual(preview["mode"], "preview")
        self.assertEqual(default_run["mode"], "preview")
        self.assertTrue(files["video"].is_file())
        self.assertTrue(held["held"])
        self.assertNotIn("private retention review", json.dumps(await self.store.events(job["id"])))

        cleared = await self._admin(
            environment,
            "audit",
            "hold",
            editing_session["id"],
            "--clear",
            "--format",
            "json",
        )
        applied = await self._admin(
            environment,
            "retention",
            "run",
            "--apply",
            "--format",
            "json",
        )
        repeated = await self._admin(
            environment,
            "retention",
            "run",
            "--apply",
            "--format",
            "json",
        )

        self.assertFalse(cleared["held"])
        self.assertEqual(applied["media"]["purged"], 1)
        self.assertEqual(repeated["media"]["purged"], 0)
        self.assertFalse(files["video"].exists())

    async def _admin(
        self,
        environment: dict[str, str],
        *arguments: str,
        input_value: str | None = None,
    ) -> dict:
        completed = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "open_storyline.mvp.admin",
                *arguments,
            ],
            cwd=ROOT,
            env=environment,
            input=input_value,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)


if __name__ == "__main__":
    unittest.main()
