from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import json
import os
import unittest
from unittest.mock import patch
import zipfile

import httpx
from fastapi import FastAPI

from open_storyline.mvp.api import create_mvp_router
from open_storyline.mvp.jobs import JobManager, JobStore, JobStoreError


class JobStoreTests(unittest.TestCase):
    def test_state_is_durable_and_artifacts_are_job_scoped(self):
        with TemporaryDirectory() as tmpdir:
            store = JobStore(tmpdir)
            state = store.create(prompt="make shorts", filename="../../talk.mp4", max_clips=4)
            source = store.input_path(state["id"], "talk.mp4")
            source.write_bytes(b"video")
            store.mark_uploaded(state["id"], source, 5)
            artifact = store.output_dir(state["id"]) / "short-01.mp4"
            artifact.write_bytes(b"result")
            store.register_artifact(state["id"], artifact, kind="video")

            restored = JobStore(tmpdir).load(state["id"])
            self.assertEqual(restored["input"]["original_filename"], "talk.mp4")
            self.assertEqual(restored["artifacts"][0]["name"], "short-01.mp4")
            self.assertEqual(store.resolve_artifact(state["id"], "short-01.mp4"), artifact)
            with self.assertRaises(JobStoreError):
                store.resolve_artifact(state["id"], "../short-01.mp4")


class JobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_job_is_recovered_after_restart(self):
        with TemporaryDirectory() as tmpdir:
            store = JobStore(tmpdir)
            state = store.create(prompt="recover", filename="talk.mp4")
            source = store.input_path(state["id"], "talk.mp4")
            source.write_bytes(b"video")
            store.mark_uploaded(state["id"], source, 5)
            store.update(state["id"], state="running")

            async def processor(job_id: str, current_store: JobStore):
                artifact = current_store.output_dir(job_id) / "done.txt"
                artifact.write_text("done", encoding="utf-8")
                current_store.register_artifact(job_id, artifact, kind="manifest")
                return {"processor": "test"}

            manager = JobManager(JobStore(tmpdir), processor)
            await manager.start()
            await asyncio.wait_for(manager.queue.join(), timeout=2)
            await manager.stop()

            restored = store.load(state["id"])
            self.assertEqual(restored["state"], "completed")
            self.assertEqual(restored["recovery_count"], 1)
            self.assertEqual(restored["processor"], "test")

    async def test_failure_persists_all_attempts_without_secrets(self):
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

        with TemporaryDirectory() as tmpdir, patch.dict(
            os.environ, {"NINEROUTER_KEY": "super-secret"}, clear=False
        ):
            store = JobStore(tmpdir)
            state = store.create(prompt="fail closed", filename="talk.mp4")
            source = store.input_path(state["id"], "talk.mp4")
            source.write_bytes(b"video")
            store.mark_uploaded(state["id"], source, 5)

            async def processor(job_id: str, current_store: JobStore):
                current_store.update(job_id, stage="remote_transcription")
                raise ProviderFailure("Bearer super-secret unavailable")

            manager = JobManager(store, processor)
            await manager.start()
            await asyncio.wait_for(manager.queue.join(), timeout=2)
            await manager.stop()

            failed = store.load(state["id"])
            failure_path = store.resolve_artifact(state["id"], "failure.json")
            serialized = json.dumps({
                "state": failed,
                "manifest": json.loads(failure_path.read_text(encoding="utf-8")),
            })
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["error"]["code"], "STT_ALL_PROVIDERS_FAILED")
            self.assertEqual(len(failed["error"]["details"]["attempts"]), 2)
            self.assertEqual(failed["error"]["details"]["api_key"], "***")
            self.assertNotIn("super-secret", serialized)
            self.assertIn("remote_transcription", serialized)


class JobAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_status_and_download(self):
        with TemporaryDirectory() as tmpdir:
            store = JobStore(tmpdir)
            manager = JobManager(store)
            app = FastAPI()
            app.include_router(create_mvp_router(lambda: store, lambda: manager))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                created = await client.post(
                    "/api/mvp/jobs",
                    data={"prompt": "make four vertical clips", "max_clips": "4"},
                    files={"file": ("talk.mp4", b"fake-video", "video/mp4")},
                )
                self.assertEqual(created.status_code, 202)
                job = created.json()
                self.assertEqual(job["state"], "queued")

                status = await client.get(f"/api/mvp/jobs/{job['id']}")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()["input"]["size"], 10)

                artifact = store.output_dir(job["id"]) / "manifest.json"
                artifact.write_text("{}", encoding="utf-8")
                store.register_artifact(job["id"], artifact, kind="manifest")
                download = await client.get(
                    f"/api/mvp/jobs/{job['id']}/artifacts/manifest.json"
                )
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.content, b"{}")

                bundle = await client.get(f"/api/mvp/jobs/{job['id']}/bundle")
                self.assertEqual(bundle.status_code, 200)
                bundle_path = Path(tmpdir) / "download.zip"
                bundle_path.write_bytes(bundle.content)
                with zipfile.ZipFile(bundle_path) as archive:
                    self.assertIn("manifest.json", archive.namelist())

                traversal = await client.get(
                    f"/api/mvp/jobs/{job['id']}/artifacts/%2E%2E%2Fmanifest.json"
                )
                self.assertIn(traversal.status_code, {404, 400})


if __name__ == "__main__":
    unittest.main()
