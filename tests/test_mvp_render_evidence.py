from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil
import subprocess
import unittest

from pydantic import ValidationError

from open_storyline.mvp.render_evidence import (
    EvidenceClip,
    EvidenceEvent,
    EvidenceFrame,
    EvidenceLimits,
    RenderEvidenceError,
    RenderEvidenceManifest,
    RenderedCandidate,
    build_render_evidence,
    derive_evidence_events,
    evidence_fingerprint,
)


def _candidate_plan() -> dict:
    return {
        "clips": [{
            "clip_index": 1,
            "source_window": {"start_ms": 0, "end_ms": 4000},
            "segments": [{
                "timeline_window": {"start_ms": 0, "end_ms": 2000},
                "transition_in": {"kind": "cut"},
                "layout": {"mode": "crop"},
                "overlays": [{
                    "kind": "text",
                    "timeline_window": {"start_ms": 400, "end_ms": 1200},
                }],
            }, {
                "timeline_window": {"start_ms": 2000, "end_ms": 4000},
                "transition_in": {"kind": "xfade"},
                "layout": {"mode": "fit"},
                "overlays": [],
            }],
        }],
    }


def _execution() -> dict:
    return {
        "version": "render_execution.v1",
        "clips": [{
            "clip_index": 1,
            "segments": [{
                "timeline_window": {"start_ms": 0, "end_ms": 2000},
                "transition_kind": "cut",
                "strategy": "crop",
                "overlays": [{
                    "kind": "text",
                    "timeline_window": {"start_ms": 400, "end_ms": 1200},
                }],
            }, {
                "timeline_window": {"start_ms": 2000, "end_ms": 4000},
                "transition_kind": "xfade",
                "transition_duration_ms": 300,
                "strategy": "fit",
                "overlays": [],
            }],
        }],
    }


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is unavailable")
class RenderEvidenceSamplingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.video = Path(self.temporary.name) / "short-01.mp4"
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x180:rate=12",
                "-t", "4", "-pix_fmt", "yuv420p", str(self.video),
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            self.skipTest("ffmpeg cannot create the synthetic fixture")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _candidate(self, events=()) -> RenderedCandidate:
        return RenderedCandidate(
            clip_index=1,
            video_path=self.video,
            duration_ms=4000,
            source_artifact=self.video.name,
            source_width=320,
            source_height=180,
            events=tuple(events),
        )

    def test_simple_clip_uses_small_stable_anchor_set(self):
        limits = EvidenceLimits(
            max_frames_per_clip=8,
            max_frames_total=8,
            max_total_bytes=8 * 1024 * 1024,
        )
        first = build_render_evidence(
            [self._candidate()],
            source_sha256="a" * 64,
            render_execution=_execution(),
            plan=_candidate_plan(),
            effects={"effects": []},
            limits=limits,
        )
        second = build_render_evidence(
            [self._candidate()],
            source_sha256="a" * 64,
            render_execution=_execution(),
            plan=_candidate_plan(),
            effects={"effects": []},
            limits=limits,
        )
        self.assertEqual(first.manifest.frame_count, 3)
        self.assertEqual(
            [frame.evidence_id for frame in first.manifest.clips[0].frames],
            [frame.evidence_id for frame in second.manifest.clips[0].frames],
        )
        self.assertEqual(first.manifest.candidate_fingerprint, second.manifest.candidate_fingerprint)
        self.assertEqual(len(first.image_data_urls), first.manifest.frame_count)

    def test_high_risk_events_receive_bounded_bursts(self):
        events = (
            EvidenceEvent(1000, "caption_event", 90, 300),
            EvidenceEvent(2000, "transition_boundary", 95, 300),
            EvidenceEvent(3000, "defect_window", 100, 400),
        )
        bundle = build_render_evidence(
            [self._candidate(events)],
            source_sha256="b" * 64,
            render_execution=_execution(),
            plan=_candidate_plan(),
            effects={"effects": [{"skill": "sharpen"}]},
            limits=EvidenceLimits(max_frames_per_clip=10, max_frames_total=10),
        )
        clip = bundle.manifest.clips[0]
        reasons = {reason for frame in clip.frames for reason in frame.purpose}
        self.assertTrue({"caption_event", "transition_boundary", "defect_window"} <= reasons)
        self.assertGreaterEqual(len(clip.bursts), 2)
        self.assertLessEqual(bundle.manifest.frame_count, 10)
        self.assertLess(bundle.manifest.frame_count, 100)

    def test_repeated_identical_events_do_not_duplicate_bursts(self):
        events = (
            EvidenceEvent(1000, "defect_window", 100, 400),
            EvidenceEvent(1000, "defect_window", 100, 400),
        )
        bundle = build_render_evidence(
            [self._candidate(events)],
            source_sha256="b" * 64,
            render_execution=_execution(),
            plan=_candidate_plan(),
            effects={"effects": []},
            limits=EvidenceLimits(max_frames_per_clip=8, max_frames_total=8),
        )
        bursts = bundle.manifest.clips[0].bursts
        self.assertEqual(len(bursts), len({burst.burst_id for burst in bursts}))

    def test_changed_candidate_invalidates_fingerprint_without_persisting_frame_bytes(self):
        limits = EvidenceLimits(max_frames_per_clip=6, max_frames_total=6)
        candidate = self._candidate()
        first = evidence_fingerprint(
            [candidate],
            source_sha256="c" * 64,
            render_execution=_execution(),
            plan=_candidate_plan(),
            effects={"effects": []},
            limits=limits,
        )
        self.video.write_bytes(self.video.read_bytes() + b"changed")
        second = evidence_fingerprint(
            [candidate],
            source_sha256="c" * 64,
            render_execution=_execution(),
            plan=_candidate_plan(),
            effects={"effects": []},
            limits=limits,
        )
        self.assertNotEqual(first, second)

    def test_changed_event_selection_invalidates_fingerprint(self):
        candidate = self._candidate((EvidenceEvent(1000, "caption_event", 90, 300),))
        first = evidence_fingerprint(
            [candidate],
            source_sha256="c" * 64,
            render_execution=_execution(),
            plan=_candidate_plan(),
            effects={"effects": []},
            limits=EvidenceLimits(max_frames_per_clip=6, max_frames_total=6),
        )
        changed = self._candidate((EvidenceEvent(1200, "caption_event", 90, 300),))
        second = evidence_fingerprint(
            [changed],
            source_sha256="c" * 64,
            render_execution=_execution(),
            plan=_candidate_plan(),
            effects={"effects": []},
            limits=EvidenceLimits(max_frames_per_clip=6, max_frames_total=6),
        )
        self.assertNotEqual(first, second)



class RenderEvidenceContractTests(unittest.TestCase):
    def _frame(self, *, timestamp_ms: int = 1000, evidence_id: str = "ev-" + "a" * 24):
        return EvidenceFrame(
            evidence_id=evidence_id,
            clip_index=1,
            timestamp_ms=timestamp_ms,
            purpose=("opening_anchor",),
            source_artifact="short-01.mp4",
            width=320,
            height=180,
            encoded_bytes=100,
            sha256="b" * 64,
        )

    def test_schema_rejects_unknown_fields_duplicate_ids_and_out_of_range_times(self):
        with self.assertRaises(ValidationError):
            EvidenceFrame.model_validate({
                **self._frame().model_dump(),
                "private_provider_body": "secret",
            })
        with self.assertRaises(ValidationError):
            EvidenceClip(
                clip_index=1,
                source_artifact="short-01.mp4",
                output_sha256="c" * 64,
                duration_ms=1000,
                frames=(self._frame(timestamp_ms=1000),),
                selected_reasons=("opening_anchor",),
            )
        with self.assertRaises(ValidationError):
            EvidenceClip(
                clip_index=1,
                source_artifact="short-01.mp4",
                output_sha256="c" * 64,
                duration_ms=2000,
                frames=(self._frame(), self._frame()),
                selected_reasons=("opening_anchor",),
            )

    def test_manifest_serialization_contains_metadata_only(self):
        frame = self._frame()
        clip = EvidenceClip(
            clip_index=1,
            source_artifact="short-01.mp4",
            output_sha256="c" * 64,
            duration_ms=2000,
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
        serialized = json.dumps(manifest.to_dict())
        self.assertNotIn("data:image", serialized)
        self.assertNotIn("provider", serialized.lower())
        self.assertNotIn("private", serialized.lower())

    def test_event_derivation_covers_caption_transition_crop_defect_and_uncertainty(self):
        events = derive_evidence_events(
            clip_plan=_candidate_plan()["clips"][0],
            render_clip=_execution()["clips"][0],
            quality_clip={
                "status": "warning",
                "findings": [{"code": "ACTIVE_PICTURE_TOO_SMALL"}],
                "active_picture": {"samples": [{"timestamp_ms": 1700, "active_area_ratio": 0.4}]},
            },
            duration_ms=4000,
            has_subtitles=True,
            effect_count=1,
        )
        reasons = {event.reason for event in events}
        self.assertTrue({"caption_event", "transition_boundary", "crop_focus_change", "defect_window", "effect_boundary"} <= reasons)

    def test_event_rejects_unbounded_or_unknown_reasons(self):
        with self.assertRaises(RenderEvidenceError):
            EvidenceEvent(100, "raw_provider_body", 1, 0)
        with self.assertRaises(RenderEvidenceError):
            EvidenceEvent(100, "caption_event", 101, 0)


if __name__ == "__main__":
    unittest.main()
