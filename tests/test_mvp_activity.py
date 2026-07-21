from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import json
import os
import subprocess
import sys
import unittest

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url

from open_storyline.mvp.activity import ActivityService, encode_sse, normalize_activity
from open_storyline.mvp.api import create_mvp_router
from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.jobs import JobManager, JobStore, JobStoreError


ROOT = Path(__file__).resolve().parents[1]


def _integration_url() -> str:
    raw = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw:
        return ""
    url = make_url(normalize_database_url(raw))
    if not str(url.database or "").startswith("openstoryline_test"):
        raise RuntimeError("TEST_DATABASE_URL must use an openstoryline_test database")
    return raw


class ActivityValidationTests(unittest.TestCase):
    def test_schema_accepts_only_bounded_public_metadata(self):
        event = normalize_activity(
            {
                "category": "render",
                "status": "progress",
                "message_key": "activity.render.rendering_clip",
                "progress": 0.75,
                "current": 2,
                "total": 3,
                "elapsed_ms": 4200,
                "provider": "9Router",
                "tool": "FFmpeg",
                "retryable": False,
            }
        )
        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["current"], 2)
        self.assertNotIn("prompt", event)

        for private_field in ("prompt", "transcript", "provider_body", "reasoning"):
            with self.subTest(private_field=private_field):
                with self.assertRaises(JobStoreError) as caught:
                    normalize_activity(
                        {
                            "category": "system",
                            "status": "warning",
                            "message_key": "activity.system.warning",
                            private_field: "private marker",
                        }
                    )
                self.assertEqual(caught.exception.code, "ACTIVITY_FIELD_INVALID")

    def test_failed_activity_requires_stable_code_and_retryability(self):
        with self.assertRaises(JobStoreError) as caught:
            normalize_activity(
                {
                    "category": "system",
                    "status": "failed",
                    "message_key": "activity.system.failed",
                }
            )
        self.assertEqual(caught.exception.code, "ACTIVITY_FAILURE_INVALID")
        event = normalize_activity(
            {
                "category": "system",
                "status": "failed",
                "message_key": "activity.system.failed",
                "error_code": "DATABASE_UNAVAILABLE",
                "retryable": True,
            }
        )
        self.assertEqual(event["error_code"], "DATABASE_UNAVAILABLE")

    def test_sse_encoding_is_compact_and_does_not_add_private_fields(self):
        encoded = encode_sse(
            sequence=7,
            event={
                "schema_version": 1,
                "sequence": 7,
                "category": "queue",
                "status": "queued",
                "message_key": "activity.queue.waiting",
            },
        ).decode("utf-8")
        self.assertIn("id: 7\n", encoded)
        self.assertIn("event: activity\n", encoded)
        payload = json.loads(encoded.split("data: ", 1)[1])
        self.assertEqual(payload["sequence"], 7)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class ActivityPostgresTests(unittest.IsolatedAsyncioTestCase):
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
        self.activity = ActivityService(
            self.store,
            poll_interval=0.01,
            heartbeat_interval=0.02,
        )
        editing_session = await self.store.create_session("Activity")
        job = await self.store.create(
            editing_session_id=editing_session["id"],
            prompt="private editing prompt marker",
            filename="source.mp4",
        )
        source = self.store.input_path(job["id"], "source.mp4")
        source.write_bytes(b"video")
        self.job = await self.store.mark_uploaded(job["id"], source, 5)

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary_directory.cleanup()

    async def emit(self, *, progress: float, status: str = "progress") -> dict:
        return await self.activity.emit(
            self.job["id"],
            stage="rendering",
            category="render",
            status=status,
            message_key="activity.render.rendering_clip",
            progress=progress,
            current=1,
            total=2,
            tool="FFmpeg",
        )

    async def test_public_projection_filters_internal_events_and_is_monotonic(self):
        first = await self.emit(progress=0.68)
        second = await self.emit(progress=0.75)
        page = await self.activity.list(self.job["id"])
        self.assertEqual([item["sequence"] for item in page["items"]], [first["sequence"], second["sequence"]])
        serialized = json.dumps(page)
        self.assertNotIn("private editing prompt marker", serialized)
        self.assertNotIn("job_uploaded", serialized)
        with self.assertRaises(JobStoreError) as caught:
            await self.emit(progress=0.70)
        self.assertEqual(caught.exception.code, "ACTIVITY_PROGRESS_INVALID")

    async def test_replay_live_append_reconnect_heartbeat_and_disconnect(self):
        first = await self.emit(progress=0.68)
        stream = self.activity.stream(
            self.job["id"],
            after_sequence=first["sequence"],
        )
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)
        second = await self.emit(progress=0.75)
        chunk = (await asyncio.wait_for(waiting, timeout=1)).decode("utf-8")
        self.assertIn(f"id: {second['sequence']}", chunk)
        heartbeat = (await asyncio.wait_for(anext(stream), timeout=1)).decode("utf-8")
        self.assertEqual(heartbeat, ": heartbeat\n\n")
        await stream.aclose()

        disconnected = self.activity.stream(
            self.job["id"],
            after_sequence=second["sequence"],
            disconnected=lambda: _true(),
        )
        with self.assertRaises(StopAsyncIteration):
            await anext(disconnected)

    async def test_terminal_stream_and_api_replay_use_last_event_id(self):
        first = await self.emit(progress=0.68)
        second = await self.emit(progress=0.86, status="completed")
        await self.store.update(
            self.job["id"],
            state="completed",
            progress=1,
            stage="completed",
        )
        terminal = await self.activity.emit(
            self.job["id"],
            stage="completed",
            category="system",
            status="completed",
            message_key="activity.system.completed",
            progress=1,
            clip_count=2,
        )
        chunks = [
            chunk
            async for chunk in self.activity.stream(
                self.job["id"],
                after_sequence=first["sequence"],
            )
        ]
        content = b"".join(chunks).decode("utf-8")
        self.assertNotIn(f"id: {first['sequence']}\n", content)
        self.assertIn(f"id: {second['sequence']}\n", content)
        self.assertIn(f"id: {terminal['sequence']}\n", content)

        manager = JobManager(self.store)
        app = FastAPI()
        app.include_router(
            create_mvp_router(
                lambda: self.store,
                lambda: manager,
                None,
                None,
                None,
                None,
                lambda: self.activity,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            replay = await client.get(
                f"/api/mvp/jobs/{self.job['id']}/events",
                params={"after": first["sequence"]},
            )
            streamed = await client.get(
                f"/api/mvp/jobs/{self.job['id']}/events/stream",
                headers={"Last-Event-ID": str(first["sequence"])},
            )
            invalid = await client.get(
                f"/api/mvp/jobs/{self.job['id']}/events/stream",
                headers={"Last-Event-ID": "not-an-id"},
            )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["items"][0]["sequence"], second["sequence"])
        self.assertEqual(streamed.status_code, 200)
        self.assertTrue(streamed.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(streamed.headers["x-accel-buffering"], "no")
        self.assertNotIn(f"id: {first['sequence']}\n", streamed.text)
        self.assertIn(f"id: {terminal['sequence']}\n", streamed.text)
        self.assertEqual(invalid.status_code, 400)

    async def test_recovery_keeps_public_progress_monotonic_while_replaying_stages(self):
        await self.activity.emit(
            self.job["id"],
            stage="rendering",
            category="render",
            status="progress",
            message_key="activity.render.rendering_clip",
            progress=0.86,
            current=2,
            total=2,
        )
        await self.store.update(
            self.job["id"],
            state="running",
            stage="rendering",
            progress=0.86,
        )
        self.assertEqual(await self.store.recover_pending(), [self.job["id"]])
        self.assertEqual(await self.store.claim_next_job(), self.job["id"])
        restarted = await self.activity.stage(self.job["id"], "extracting_audio")
        self.assertEqual(restarted["progress"], 0.86)
        page = await self.activity.list(self.job["id"])
        progresses = [
            item["progress"] for item in page["items"] if "progress" in item
        ]
        self.assertEqual(progresses, sorted(progresses))

    async def test_worker_failure_exposes_only_safe_terminal_metadata(self):
        class ProviderUnavailable(RuntimeError):
            code = "PROVIDER_UNAVAILABLE"

        async def processor(_job_id, _store):
            raise ProviderUnavailable(
                "private transcript marker and provider response must stay internal"
            )

        manager = JobManager(self.store, processor, poll_interval=0.01)
        await manager.enqueue(self.job["id"])
        await manager.start()
        try:
            terminal = await manager.wait_for_terminal(self.job["id"], timeout=5)
        finally:
            await manager.stop()
        self.assertEqual(terminal["state"], "failed")
        page = await self.activity.list(self.job["id"])
        failure = page["items"][-1]
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(failure["error_code"], "PROVIDER_UNAVAILABLE")
        self.assertTrue(failure["retryable"])
        serialized = json.dumps(page)
        self.assertNotIn("private transcript marker", serialized)
        self.assertNotIn("provider response", serialized)


async def _true() -> bool:
    return True


if __name__ == "__main__":
    unittest.main()
