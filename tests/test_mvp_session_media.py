from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import httpx
from sqlalchemy import text, update
from sqlalchemy.engine import make_url

from mvp_fastapi import create_app
from open_storyline.mvp.auth import CSRF_COOKIE, SESSION_COOKIE
from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.jobs import JobManager, JobStore
from open_storyline.mvp.models import SessionInputVideo
from open_storyline.mvp.retention import RetentionService, RetentionSettings
from open_storyline.mvp.prompt_versions import PromptVersionService, validate_run_settings
from open_storyline.mvp.session_media import SessionMediaError, SessionMediaStore


ROOT = Path(__file__).resolve().parents[1]


def _integration_url() -> str:
    raw = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw:
        return ""
    url = make_url(normalize_database_url(raw))
    if not str(url.database or "").startswith("openstoryline_test"):
        raise RuntimeError("TEST_DATABASE_URL must use an openstoryline_test database")
    return raw


async def _body(*chunks: bytes):
    for chunk in chunks:
        yield chunk


class _AuthStub:
    async def resolve_session(self, raw_token):
        return object() if raw_token == "test-session" else None

    @staticmethod
    def same_origin(request):
        return request.headers.get("origin") == "https://test"

    @staticmethod
    def valid_csrf(_context, raw_token):
        return raw_token == "test-csrf"


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class SessionMediaPostgresTests(unittest.IsolatedAsyncioTestCase):
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
        self.now = datetime.now(UTC)
        self.store = JobStore(self.root / "mvp_jobs", self.database)
        self.media = SessionMediaStore(
            self.root / "mvp_sessions",
            self.database,
            media_retention_days=7,
            incomplete_upload_hours=24,
            max_upload_bytes=32 * 1024 * 1024,
            max_chunk_bytes=1024 * 1024,
            now=lambda: self.now,
        )

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary_directory.cleanup()

    def synthetic_video(self) -> bytes:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg and FFprobe are required")
        path = self.root / "synthetic.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x17324d:s=96x96:r=12:d=0.5",
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

    async def test_resume_reconciles_filesystem_offset_and_ready_source_is_immutable(self):
        video = self.synthetic_video()
        editing_session = await self.store.create_session("Reusable", workflow_version=2)
        initialized = await self.media.initialize(
            editing_session["id"],
            original_filename="camera.mp4",
            expected_size=len(video),
            media_type="video/mp4",
        )
        first_size = min(256, len(video) // 3)
        first = await self.media.append_chunk(
            editing_session["id"],
            initialized["upload_id"],
            offset=0,
            chunks=_body(video[:first_size]),
            content_length=first_size,
        )
        self.assertEqual(first["upload_offset"], first_size)

        crash_bytes = video[first_size : first_size + 37]
        with (self.root / "mvp_sessions" / editing_session["id"] / "input" / "source.part").open(
            "ab"
        ) as stream:
            stream.write(crash_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        authoritative = first_size + len(crash_bytes)
        resumed = await self.media.status(editing_session["id"])
        self.assertEqual(resumed["upload_offset"], authoritative)

        with self.assertRaises(SessionMediaError) as mismatch:
            await self.media.append_chunk(
                editing_session["id"],
                initialized["upload_id"],
                offset=first_size,
                chunks=_body(b"stale"),
                content_length=5,
            )
        self.assertEqual(mismatch.exception.code, "UPLOAD_OFFSET_MISMATCH")
        self.assertEqual(mismatch.exception.details["upload_offset"], authoritative)

        uploaded = await self.media.append_chunk(
            editing_session["id"],
            initialized["upload_id"],
            offset=authoritative,
            chunks=_body(video[authoritative:]),
            content_length=len(video) - authoritative,
        )
        self.assertEqual(uploaded["upload_offset"], len(video))
        ready = await self.media.complete(editing_session["id"], initialized["upload_id"])
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["sha256"], hashlib.sha256(video).hexdigest())

        path, preview = await self.media.resolve_ready(editing_session["id"])
        self.assertEqual(path.read_bytes(), video)
        self.assertEqual(preview["media_type"], "video/mp4")
        with self.assertRaises(SessionMediaError) as immutable:
            await self.media.initialize(
                editing_session["id"],
                original_filename="replacement.mp4",
                expected_size=len(video),
                media_type="video/mp4",
            )
        self.assertEqual(immutable.exception.code, "SESSION_SOURCE_IMMUTABLE")
        with self.assertRaises(SessionMediaError) as cancellation:
            await self.media.cancel(editing_session["id"], initialized["upload_id"])
        self.assertEqual(cancellation.exception.code, "SESSION_SOURCE_IMMUTABLE")

    async def test_metadata_cross_session_invalid_media_and_cancel_fail_closed(self):
        first = await self.store.create_session("First", workflow_version=2)
        second = await self.store.create_session("Second", workflow_version=2)
        initialized = await self.media.initialize(
            first["id"],
            original_filename="clip.mp4",
            expected_size=9,
            media_type="video/mp4",
        )
        with self.assertRaises(SessionMediaError) as metadata:
            await self.media.initialize(
                first["id"],
                original_filename="clip.mp4",
                expected_size=10,
                media_type="video/mp4",
            )
        self.assertEqual(metadata.exception.code, "UPLOAD_METADATA_CONFLICT")
        with self.assertRaises(SessionMediaError) as isolated:
            await self.media.append_chunk(
                second["id"],
                initialized["upload_id"],
                offset=0,
                chunks=_body(b"private"),
                content_length=7,
            )
        self.assertEqual(isolated.exception.code, "SOURCE_UPLOAD_NOT_FOUND")

        await self.media.append_chunk(
            first["id"],
            initialized["upload_id"],
            offset=0,
            chunks=_body(b"not-video"),
            content_length=9,
        )
        with self.assertRaises(SessionMediaError) as invalid:
            await self.media.complete(first["id"], initialized["upload_id"])
        self.assertEqual(invalid.exception.code, "SOURCE_VIDEO_INVALID")
        failed = await self.media.status(first["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["received_bytes"], 0)

        restarted = await self.media.initialize(
            first["id"],
            original_filename="retry.webm",
            expected_size=12,
            media_type="video/webm",
        )
        cancelled = await self.media.cancel(first["id"], restarted["upload_id"])
        self.assertEqual(cancelled["state"], "failed")
        self.assertEqual(cancelled["failure_code"], "UPLOAD_CANCELLED")

    async def test_legacy_sessions_reject_the_reusable_source_contract(self):
        legacy = await self.store.create_session("Legacy")
        with self.assertRaises(SessionMediaError) as raised:
            await self.media.status(legacy["id"])
        self.assertEqual(raised.exception.code, "SESSION_WORKFLOW_LEGACY")

    async def test_concurrent_equal_offsets_accept_only_one_chunk(self):
        editing_session = await self.store.create_session("Concurrent", workflow_version=2)
        initialized = await self.media.initialize(
            editing_session["id"],
            original_filename="clip.mp4",
            expected_size=8,
            media_type="video/mp4",
        )

        async def append(value: bytes):
            try:
                return await self.media.append_chunk(
                    editing_session["id"],
                    initialized["upload_id"],
                    offset=0,
                    chunks=_body(value),
                    content_length=len(value),
                )
            except SessionMediaError as exc:
                return exc.code

        results = await asyncio.gather(append(b"first"), append(b"other"))
        accepted = [result for result in results if isinstance(result, dict)]
        rejected = [result for result in results if isinstance(result, str)]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)
        self.assertIn(rejected[0], {"SOURCE_UPLOAD_BUSY", "UPLOAD_OFFSET_MISMATCH"})
        status = await self.media.status(editing_session["id"])
        self.assertEqual(status["received_bytes"], 5)

    async def test_retention_previews_then_idempotently_purges_incomplete_source(self):
        editing_session = await self.store.create_session("Expiring", workflow_version=2)
        initialized = await self.media.initialize(
            editing_session["id"],
            original_filename="clip.mp4",
            expected_size=12,
            media_type="video/mp4",
        )
        await self.media.append_chunk(
            editing_session["id"],
            initialized["upload_id"],
            offset=0,
            chunks=_body(b"partial"),
            content_length=7,
        )
        self.now += timedelta(hours=25)
        retention = RetentionService(
            self.store,
            RetentionSettings(False, 7, 30, 3600, 20, 24),
            session_media=self.media,
            now=lambda: self.now,
        )

        preview = await retention.preview(limit=10)
        self.assertEqual(preview["session_sources"]["selected"], 1)
        self.assertEqual(
            preview["session_sources"]["items"][0]["reason"],
            "incomplete_upload_expired",
        )
        part = self.root / "mvp_sessions" / editing_session["id"] / "input" / "source.part"
        self.assertTrue(part.is_file())

        applied = await retention.run(limit=10)
        repeated = await retention.run(limit=10)
        self.assertEqual(applied["session_sources"]["purged"], 1)
        self.assertEqual(repeated["session_sources"]["selected"], 0)
        self.assertFalse(part.exists())
        state = await self.media.status(editing_session["id"])
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["failure_code"], "UPLOAD_EXPIRED")

    async def test_ready_source_retention_respects_active_jobs_and_session_deletion(self):
        video = self.synthetic_video()
        editing_session = await self.store.create_session("Ready retention", workflow_version=2)
        initialized = await self.media.initialize(
            editing_session["id"],
            original_filename="clip.mp4",
            expected_size=len(video),
            media_type="video/mp4",
        )
        await self.media.append_chunk(
            editing_session["id"],
            initialized["upload_id"],
            offset=0,
            chunks=_body(video),
            content_length=len(video),
        )
        ready = await self.media.complete(editing_session["id"], initialized["upload_id"])
        source_path, _state = await self.media.resolve_ready(editing_session["id"])
        active = (
            await PromptVersionService(self.store, self.media).create_version(
                editing_session["id"],
                prompt="active retention guard",
                settings=validate_run_settings(max_clips=1),
            )
        )["run"]
        self.now += timedelta(days=8)
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(SessionInputVideo)
                .where(SessionInputVideo.id == ready["id"])
                .values(expires_at=self.now - timedelta(seconds=1))
            )
        retention = RetentionService(
            self.store,
            RetentionSettings(False, 7, 30, 3600, 20, 24),
            session_media=self.media,
            now=lambda: self.now,
        )
        guarded = await retention.preview(limit=10)
        self.assertEqual(guarded["session_sources"]["selected"], 0)

        await self.store.fail(active["id"], code="TEST_DONE", message="terminal")
        due = await retention.preview(limit=10)
        self.assertEqual(due["session_sources"]["selected"], 1)
        applied = await retention.run(limit=10)
        self.assertEqual(applied["session_sources"]["purged"], 1)
        self.assertFalse(source_path.exists())
        expired = await self.media.status(editing_session["id"])
        self.assertEqual(expired["state"], "expired")

        deleted_session = await self.store.create_session("Delete source", workflow_version=2)
        deleted_upload = await self.media.initialize(
            deleted_session["id"],
            original_filename="delete.mp4",
            expected_size=len(video),
            media_type="video/mp4",
        )
        await self.media.append_chunk(
            deleted_session["id"],
            deleted_upload["upload_id"],
            offset=0,
            chunks=_body(video),
            content_length=len(video),
        )
        await self.media.complete(deleted_session["id"], deleted_upload["upload_id"])
        delete_path, _state = await self.media.resolve_ready(deleted_session["id"])
        deleted = await retention.delete_session(deleted_session["id"])
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["source_purge"]["purged"], 1)
        self.assertFalse(delete_path.exists())
        async with self.database.sessions() as session:
            source = await session.get(SessionInputVideo, deleted_upload["upload_id"])
        self.assertEqual(source.state, "deleted")

    async def test_authenticated_routes_enforce_csrf_range_and_path_safety(self):
        video = self.synthetic_video()
        with patch.dict(
            os.environ,
            {"OPENSTORYLINE_SESSION_WORKSPACE_MODE": "enabled"},
            clear=False,
        ):
            app = create_app()
        app.state.database = self.database
        app.state.auth_service = _AuthStub()
        app.state.mvp_jobs = self.store
        app.state.mvp_manager = JobManager(self.store)
        app.state.session_media = self.media
        app.state.retention_service = RetentionService(
            self.store,
            RetentionSettings(False, 7, 30, 3600, 20, 24),
            session_media=self.media,
        )
        transport = httpx.ASGITransport(app=app, client=("192.0.2.10", 443))

        async with httpx.AsyncClient(transport=transport, base_url="https://test") as guest:
            hidden = await guest.get(f"/api/mvp/sessions/{'0' * 32}/input-video")
        self.assertEqual(hidden.status_code, 401)

        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            client.cookies.set(SESSION_COOKIE, "test-session")
            client.cookies.set(CSRF_COOKIE, "test-csrf")
            csrf = {"Origin": "https://test", "X-CSRF-Token": "test-csrf"}
            created = await client.post(
                "/api/mvp/sessions",
                headers=csrf,
                json={"title": "Browser workspace"},
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["workflow_version"], 2)
            session_id = created.json()["id"]

            missing_csrf = await client.post(
                f"/api/mvp/sessions/{session_id}/input-video/uploads",
                json={
                    "original_filename": "source.mp4",
                    "expected_size": len(video),
                    "media_type": "video/mp4",
                },
            )
            self.assertEqual(missing_csrf.status_code, 403)
            rejected_mime = await client.post(
                f"/api/mvp/sessions/{session_id}/input-video/uploads",
                headers=csrf,
                json={
                    "original_filename": "source.mp4",
                    "expected_size": len(video),
                    "media_type": "video/mpeg",
                },
            )
            self.assertEqual(rejected_mime.status_code, 415)
            rejected_suffix = await client.post(
                f"/api/mvp/sessions/{session_id}/input-video/uploads",
                headers=csrf,
                json={
                    "original_filename": "source.txt",
                    "expected_size": len(video),
                    "media_type": "video/mp4",
                },
            )
            self.assertEqual(rejected_suffix.status_code, 415)
            too_large = await client.post(
                f"/api/mvp/sessions/{session_id}/input-video/uploads",
                headers=csrf,
                json={
                    "original_filename": "source.mp4",
                    "expected_size": 32 * 1024 * 1024 + 1,
                    "media_type": "video/mp4",
                },
            )
            self.assertEqual(too_large.status_code, 413)
            initialized = await client.post(
                f"/api/mvp/sessions/{session_id}/input-video/uploads",
                headers=csrf,
                json={
                    "original_filename": "source.mp4",
                    "expected_size": len(video),
                    "media_type": "video/mp4",
                },
            )
            upload_id = initialized.json()["upload_id"]
            wrong_offset = await client.patch(
                f"/api/mvp/sessions/{session_id}/input-video/uploads/{upload_id}",
                headers={
                    **csrf,
                    "Upload-Offset": "1",
                    "Content-Type": "application/offset+octet-stream",
                },
                content=b"stale",
            )
            self.assertEqual(wrong_offset.status_code, 409)
            self.assertEqual(
                wrong_offset.json()["detail"]["details"]["upload_offset"], 0
            )
            uploaded = await client.patch(
                f"/api/mvp/sessions/{session_id}/input-video/uploads/{upload_id}",
                headers={
                    **csrf,
                    "Upload-Offset": "0",
                    "Content-Type": "application/offset+octet-stream",
                },
                content=video,
            )
            self.assertEqual(uploaded.status_code, 200)
            completed = await client.post(
                f"/api/mvp/sessions/{session_id}/input-video/uploads/{upload_id}/complete",
                headers=csrf,
            )
            self.assertEqual(completed.status_code, 200)
            ranged = await client.get(
                f"/api/mvp/sessions/{session_id}/input-video/content",
                headers={"Range": "bytes=0-15"},
            )
            self.assertEqual(ranged.status_code, 206)
            self.assertEqual(ranged.content, video[:16])
            self.assertTrue(ranged.headers["content-type"].startswith("video/mp4"))
            self.assertTrue(
                ranged.headers["content-disposition"].startswith("inline;")
            )
            immutable = await client.delete(
                f"/api/mvp/sessions/{session_id}/input-video/uploads/{upload_id}",
                headers=csrf,
            )
            self.assertEqual(immutable.status_code, 409)

            async with self.database.engine.begin() as connection:
                await connection.execute(
                    update(SessionInputVideo)
                    .where(SessionInputVideo.editing_session_id == session_id)
                    .values(relative_path="../outside.mp4")
                )
            traversal = await client.get(
                f"/api/mvp/sessions/{session_id}/input-video/content"
            )
            self.assertEqual(traversal.status_code, 410)
            self.assertNotIn("outside", traversal.text)

            cancelled_session = await client.post(
                "/api/mvp/sessions",
                headers=csrf,
                json={"title": "Cancelled workspace"},
            )
            cancelled_id = cancelled_session.json()["id"]
            pending = await client.post(
                f"/api/mvp/sessions/{cancelled_id}/input-video/uploads",
                headers=csrf,
                json={
                    "original_filename": "pending.mp4",
                    "expected_size": 10,
                    "media_type": "video/mp4",
                },
            )
            cancelled = await client.delete(
                f"/api/mvp/sessions/{cancelled_id}/input-video/uploads/"
                f"{pending.json()['upload_id']}",
                headers=csrf,
            )
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json()["failure_code"], "UPLOAD_CANCELLED")


if __name__ == "__main__":
    unittest.main()
