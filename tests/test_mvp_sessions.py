from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
import unittest

from sqlalchemy import text, update
from sqlalchemy.engine import make_url

from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.jobs import JobStore, JobStoreError
from open_storyline.mvp.models import EditingSession


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
                    "video_jobs, editing_sessions, auth_sessions, login_attempt_buckets"
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
        with self.assertRaises(JobStoreError):
            await self.store.list_sessions(limit=51)

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
