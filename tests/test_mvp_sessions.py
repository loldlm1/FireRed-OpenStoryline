from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import sys
import unittest
import uuid

from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url

from open_storyline.mvp.admin import backfill_legacy_prompt_versions
from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.jobs import JobStore, JobStoreError
from open_storyline.mvp.models import EditingSession, PromptVersion, VideoJob


ROOT = Path(__file__).resolve().parents[1]


def _integration_url() -> str:
    raw = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw:
        return ""
    url = make_url(normalize_database_url(raw))
    if not str(url.database or "").startswith("openstoryline_test"):
        raise RuntimeError("TEST_DATABASE_URL must use an openstoryline_test database")
    return raw


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class EditingSessionTests(unittest.IsolatedAsyncioTestCase):
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
        self.store = JobStore(self.temporary_directory.name, self.database)

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary_directory.cleanup()

    async def test_create_list_resume_and_bounded_cursor_pagination(self):
        created = [
            await self.store.create_session(title)
            for title in ("First", "Second", "Third")
        ]

        first_page = await self.store.list_sessions(limit=2)
        second_page = await self.store.list_sessions(
            limit=2,
            cursor=first_page["next_cursor"],
        )

        self.assertEqual(len(first_page["items"]), 2)
        self.assertEqual(len(second_page["items"]), 1)
        self.assertFalse(
            {item["id"] for item in first_page["items"]}
            & {item["id"] for item in second_page["items"]}
        )
        resumed = await self.store.get_session(created[1]["id"])
        self.assertEqual(resumed["title"], "Second")
        self.assertEqual(resumed["workflow_version"], 1)
        self.assertIsNone(resumed["input_video"])
        with self.assertRaises(JobStoreError):
            await self.store.list_sessions(limit=51)

    async def test_internal_session_contract_accepts_only_known_workflow_versions(self):
        workspace = await self.store.create_session("Workspace", workflow_version=2)
        self.assertEqual(workspace["workflow_version"], 2)
        with self.assertRaises(JobStoreError) as raised:
            await self.store.create_session("Unknown", workflow_version=3)
        self.assertEqual(raised.exception.code, "SESSION_WORKFLOW_VERSION_INVALID")

    async def test_one_session_contains_multiple_jobs_in_stable_order(self):
        editing_session = await self.store.create_session("Series")
        jobs = []
        for prompt in ("first prompt", "second prompt"):
            jobs.append(
                await self.store.create(
                    editing_session_id=editing_session["id"],
                    prompt=prompt,
                    filename="talk.mp4",
                )
            )

        listed = await self.store.list_jobs(editing_session["id"])
        self.assertEqual(
            {item["id"] for item in listed["items"]},
            {item["id"] for item in jobs},
        )
        self.assertTrue(
            all(item["editing_session_id"] == editing_session["id"] for item in listed["items"])
        )
        self.assertTrue(
            all(item["prompt_version_id"] is None for item in listed["items"])
        )
        self.assertTrue(all(item["attempt_number"] is None for item in listed["items"]))
        self.assertTrue(all(item["is_favorite"] is False for item in listed["items"]))

    async def test_legacy_prompt_backfill_is_bounded_resumable_and_idempotent(self):
        first_session = await self.store.create_session("First legacy session")
        second_session = await self.store.create_session("Second legacy session")
        jobs = []
        for prompt in ("same private prompt", "same private prompt", "third prompt"):
            jobs.append(
                await self.store.create(
                    editing_session_id=first_session["id"],
                    prompt=prompt,
                    filename="talk.mp4",
                )
            )
        jobs.append(
            await self.store.create(
                editing_session_id=second_session["id"],
                prompt="other private prompt",
                filename="talk.mp4",
            )
        )
        workspace_session = await self.store.create_session(
            "Future workspace", workflow_version=2
        )
        await self.store.create(
            editing_session_id=workspace_session["id"],
            prompt="must not be backfilled",
            filename="talk.mp4",
        )

        async with self.database.sessions() as session:
            async with session.begin():
                first_job = await session.scalar(
                    select(VideoJob)
                    .where(VideoJob.editing_session_id == first_session["id"])
                    .order_by(VideoJob.created_at, VideoJob.id)
                    .limit(1)
                )
                self.assertIsNotNone(first_job)
                prompt_version = PromptVersion(
                    id=uuid.uuid4().hex,
                    editing_session_id=first_session["id"],
                    version_number=1,
                    prompt=first_job.prompt,
                    settings_data=dict(first_job.request_data or {}),
                    created_at=first_job.created_at,
                )
                session.add(prompt_version)
                await session.flush()
                first_job.prompt_version_id = prompt_version.id
                first_job.attempt_number = 1

        preview = await backfill_legacy_prompt_versions(
            self.database,
            dry_run=True,
            limit=10,
            batch_size=1,
        )
        self.assertEqual(preview["eligible"], 3)
        self.assertEqual(preview["processed"], 0)
        self.assertNotIn("private prompt", json.dumps(preview))

        first_apply = await backfill_legacy_prompt_versions(
            self.database,
            dry_run=False,
            limit=2,
            batch_size=1,
        )
        self.assertEqual(first_apply["processed"], 2)
        self.assertEqual(first_apply["remaining"], 1)
        final_apply = await backfill_legacy_prompt_versions(
            self.database,
            dry_run=False,
            limit=10,
            batch_size=2,
        )
        self.assertEqual(final_apply["processed"], 1)
        self.assertTrue(final_apply["complete"])
        repeated = await backfill_legacy_prompt_versions(
            self.database,
            dry_run=False,
            limit=10,
            batch_size=2,
        )
        self.assertEqual(repeated["processed"], 0)
        self.assertTrue(repeated["complete"])

        async with self.database.sessions() as session:
            for editing_session_id in (first_session["id"], second_session["id"]):
                linked_jobs = list(
                    (
                        await session.execute(
                            select(VideoJob)
                            .where(VideoJob.editing_session_id == editing_session_id)
                            .order_by(VideoJob.created_at, VideoJob.id)
                        )
                    ).scalars()
                )
                versions = {
                    item.id: item
                    for item in (
                        await session.execute(
                            select(PromptVersion).where(
                                PromptVersion.editing_session_id == editing_session_id
                            )
                        )
                    ).scalars()
                }
                self.assertEqual(len(versions), len(linked_jobs))
                self.assertEqual(
                    [versions[item.prompt_version_id].version_number for item in linked_jobs],
                    list(range(1, len(linked_jobs) + 1)),
                )
                self.assertTrue(all(item.attempt_number == 1 for item in linked_jobs))
                self.assertTrue(
                    all(
                        versions[item.prompt_version_id].settings_data
                        == item.request_data
                        for item in linked_jobs
                    )
                )

    async def test_expired_and_soft_deleted_sessions_fail_closed(self):
        expired = await self.store.create_session("Expired")
        deleted = await self.store.create_session("Deleted")
        now = datetime.now(UTC)
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(EditingSession)
                .where(EditingSession.id == expired["id"])
                .values(audit_expires_at=now - timedelta(seconds=1))
            )
            await connection.execute(
                update(EditingSession)
                .where(EditingSession.id == deleted["id"])
                .values(deleted_at=now)
            )

        for session_id in (expired["id"], deleted["id"], "f" * 32):
            with self.assertRaises(JobStoreError) as raised:
                await self.store.get_session(session_id)
            self.assertEqual(raised.exception.code, "SESSION_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
