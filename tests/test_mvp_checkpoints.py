from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import os
import json
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from open_storyline.mvp.checkpoints import (
    CheckpointError,
    CheckpointStore,
    checkpoint_fingerprint,
)
from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.models import (
    EditingSession,
    JobStageCheckpoint,
    PromptVersion,
    SessionAnalysisCache,
    SessionInputVideo,
    VideoJob,
)
from open_storyline.mvp.render_evidence import (
    EvidenceClip,
    EvidenceFrame,
    EvidenceLimits,
    RenderEvidenceManifest,
    manifest_from_checkpoint,
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


class CheckpointContractTests(unittest.TestCase):
    def test_fingerprint_is_canonical_and_sensitive_to_contract_inputs(self):
        left = checkpoint_fingerprint({"source": "a", "settings": {"count": 4}})
        reordered = checkpoint_fingerprint({"settings": {"count": 4}, "source": "a"})
        changed = checkpoint_fingerprint({"source": "a", "settings": {"count": 5}})
        self.assertEqual(left, reordered)
        self.assertNotEqual(left, changed)
        self.assertEqual(len(left), 64)

    def test_invalid_checkpoint_flag_fails_closed(self):
        store = SimpleNamespace()
        with patch.dict(
            os.environ,
            {"OPENSTORYLINE_CHECKPOINTS_ENABLED": "sometimes"},
        ):
            with self.assertRaises(CheckpointError) as caught:
                CheckpointStore(store)
        self.assertEqual(caught.exception.code, "CHECKPOINT_CONFIG_INVALID")

    def test_store_without_database_is_disabled(self):
        with patch.dict(
            os.environ,
            {"OPENSTORYLINE_CHECKPOINTS_ENABLED": "true"},
        ):
            checkpoints = CheckpointStore(SimpleNamespace())
        self.assertFalse(checkpoints.enabled)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class CheckpointDatabaseTests(unittest.IsolatedAsyncioTestCase):
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
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.database = Database(self.database_url)
        self.session_id = uuid.uuid4().hex
        self.source_id = uuid.uuid4().hex
        self.version_id = uuid.uuid4().hex
        self.job_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        expires = now + timedelta(days=2)
        async with self.database.sessions() as session:
            async with session.begin():
                session.add(EditingSession(
                    id=self.session_id,
                    title="Checkpoint test",
                    workflow_version=2,
                    updated_at=now,
                    audit_expires_at=expires,
                ))
                session.add(SessionInputVideo(
                    id=self.source_id,
                    editing_session_id=self.session_id,
                    state="ready",
                    original_filename="source.mp4",
                    expected_size=10,
                    received_bytes=10,
                    media_type="video/mp4",
                    relative_path=f"{self.session_id}/input/source.mp4",
                    sha256="a" * 64,
                    completed_at=now,
                    expires_at=expires,
                ))
                session.add(PromptVersion(
                    id=self.version_id,
                    editing_session_id=self.session_id,
                    version_number=1,
                    prompt="Create a marketing video",
                    settings_data={},
                    created_at=now,
                ))
                session.add(VideoJob(
                    id=self.job_id,
                    editing_session_id=self.session_id,
                    prompt_version_id=self.version_id,
                    attempt_number=1,
                    state="running",
                    stage="remote_planning",
                    progress=Decimal("0.5"),
                    prompt="Create a marketing video",
                    request_data={},
                    input_data={"input_video_id": self.source_id, "sha256": "a" * 64},
                    result_data={},
                    updated_at=now,
                    media_expires_at=expires,
                    audit_expires_at=expires,
                ))
        self.store = SimpleNamespace(
            database=self.database,
            session_media_root=root / "sessions",
            root=root / "jobs",
        )
        self.checkpoints = CheckpointStore(self.store, enabled=True)

    async def asyncTearDown(self):
        async with self.database.sessions() as session:
            async with session.begin():
                await session.execute(
                    delete(VideoJob).where(VideoJob.id == self.job_id)
                )
                await session.execute(
                    delete(PromptVersion).where(PromptVersion.id == self.version_id)
                )
                await session.execute(
                    delete(SessionInputVideo).where(SessionInputVideo.id == self.source_id)
                )
                await session.execute(
                    delete(EditingSession).where(EditingSession.id == self.session_id)
                )
        await self.database.dispose()
        self.temporary.cleanup()

    async def test_session_and_job_checkpoints_round_trip(self):
        session_fingerprint = checkpoint_fingerprint({"stage": "transcript", "v": 1})
        job_fingerprint = checkpoint_fingerprint({"stage": "clip", "v": 1})

        await self.checkpoints.save_session(
            editing_session_id=self.session_id,
            input_video_id=self.source_id,
            stage="transcript",
            contract_version="transcript.v1",
            fingerprint=session_fingerprint,
            payload={"text": "hello", "segments": [{"start": 0, "end": 1000}]},
        )
        await self.checkpoints.save_job(
            job_id=self.job_id,
            stage="clip_visual_analysis",
            contract_version="clip.v1",
            fingerprint=job_fingerprint,
            payload={"clips": [1]},
        )

        session_hit = await self.checkpoints.load_session(
            editing_session_id=self.session_id,
            input_video_id=self.source_id,
            stage="transcript",
            fingerprint=session_fingerprint,
        )
        job_hit = await self.checkpoints.load_job(
            job_id=self.job_id,
            stage="clip_visual_analysis",
            fingerprint=job_fingerprint,
        )

        self.assertEqual(session_hit.payload["text"], "hello")
        self.assertEqual(job_hit.payload, {"clips": [1]})

    async def test_render_evidence_checkpoint_round_trip_is_metadata_only(self):
        frame = EvidenceFrame(
            evidence_id="ev-" + "a" * 24,
            clip_index=1,
            timestamp_ms=100,
            purpose=("opening_anchor",),
            source_artifact="short-01.mp4",
            width=320,
            height=180,
            encoded_bytes=100,
            sha256="b" * 64,
        )
        clip = EvidenceClip(
            clip_index=1,
            source_artifact="short-01.mp4",
            output_sha256="c" * 64,
            duration_ms=1000,
            frames=(frame,),
            selected_reasons=("opening_anchor",),
        )
        manifest = RenderEvidenceManifest(
            source_sha256="d" * 64,
            render_execution_sha256="e" * 64,
            plan_sha256="f" * 64,
            effects_sha256="0" * 64,
            candidate_fingerprint="1" * 64,
            call_fingerprint="1" * 64,
            limits=EvidenceLimits(),
            clips=(clip,),
            frame_count=1,
            burst_count=0,
            encoded_bytes=100,
        )
        fingerprint = checkpoint_fingerprint({
            "stage": "render_evidence",
            "candidate_fingerprint": manifest.candidate_fingerprint,
        })
        await self.checkpoints.save_job(
            job_id=self.job_id,
            stage="render_evidence",
            contract_version="render_evidence.v1",
            fingerprint=fingerprint,
            payload=manifest.to_dict(),
            metadata={"frame_count": 1, "encoded_bytes": 100},
        )
        hit = await self.checkpoints.load_job(
            job_id=self.job_id,
            stage="render_evidence",
            fingerprint=fingerprint,
        )
        self.assertIsNotNone(hit)
        restored = manifest_from_checkpoint(hit.payload)
        self.assertEqual(restored.candidate_fingerprint, manifest.candidate_fingerprint)
        self.assertNotIn("data_url", json.dumps(hit.payload))

    async def test_tampered_checkpoint_is_quarantined(self):
        fingerprint = checkpoint_fingerprint({"stage": "transcript", "v": 2})
        await self.checkpoints.save_session(
            editing_session_id=self.session_id,
            input_video_id=self.source_id,
            stage="transcript",
            contract_version="transcript.v1",
            fingerprint=fingerprint,
            payload={"text": "safe"},
        )
        async with self.database.sessions() as session:
            row = await session.scalar(
                select(SessionAnalysisCache).where(
                    SessionAnalysisCache.input_video_id == self.source_id,
                    SessionAnalysisCache.fingerprint == fingerprint,
                )
            )
        path = Path(self.store.session_media_root) / row.relative_path
        path.write_text("tampered", encoding="utf-8")

        hit = await self.checkpoints.load_session(
            editing_session_id=self.session_id,
            input_video_id=self.source_id,
            stage="transcript",
            fingerprint=fingerprint,
        )
        async with self.database.sessions() as session:
            status = await session.scalar(
                select(SessionAnalysisCache.status).where(
                    SessionAnalysisCache.id == row.id
                )
            )

        self.assertIsNone(hit)
        self.assertEqual(status, "quarantined")

    async def test_checkpoint_rows_are_scoped_to_the_owner(self):
        fingerprint = checkpoint_fingerprint({"stage": "clip", "v": 3})
        await self.checkpoints.save_job(
            job_id=self.job_id,
            stage="clip_visual_analysis",
            contract_version="clip.v1",
            fingerprint=fingerprint,
            payload={"clips": [1]},
        )
        async with self.database.sessions() as session:
            row = await session.scalar(
                select(JobStageCheckpoint).where(
                    JobStageCheckpoint.job_id == self.job_id,
                    JobStageCheckpoint.fingerprint == fingerprint,
                )
            )
        self.assertEqual(row.job_id, self.job_id)
        self.assertTrue(row.relative_path.startswith(f"{self.job_id}/output/"))


if __name__ == "__main__":
    unittest.main()
