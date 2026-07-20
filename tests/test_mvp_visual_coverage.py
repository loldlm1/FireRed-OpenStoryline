from types import SimpleNamespace
import unittest

from open_storyline.mvp.edit_plan import (
    ClipEditPlan,
    EditPlan,
    EditSegment,
    FocalTarget,
    LayoutSpec,
    TimeWindow,
)
from open_storyline.mvp.frame_sampling import FrameManifest, SampledFrame
from open_storyline.mvp.visual_coverage import build_clip_visual_coverage
from open_storyline.mvp.visual_understanding import (
    NormalizedBox,
    RegionObservation,
    TrackObservation,
)


def sampled_frame(frame_id: str, timestamp_ms: int) -> SampledFrame:
    return SampledFrame(
        id=frame_id,
        timestamp_ms=timestamp_ms,
        scene_id="scene-001",
        width=512,
        height=288,
        extraction_reason="clip_uniform_coverage",
        encoded_bytes=4,
        data_url="data:image/jpeg;base64,ZmFrZQ==",
    )


def visual_for(timestamps: tuple[int, ...]):
    frames = [
        {"id": f"clip-01-frame-{index:03d}", "timestamp_ms": timestamp}
        for index, timestamp in enumerate(timestamps, start=1)
    ]
    regions = tuple(
        RegionObservation(
            id=f"clip-01-region-{index:03d}",
            frame_id=frame["id"],
            role="speaker",
            bbox=NormalizedBox(x=0.35, y=0.1, width=0.3, height=0.8),
            confidence=0.9,
            salience=0.9,
        )
        for index, frame in enumerate(frames, start=1)
    )
    track = TrackObservation(
        id="clip-01-track-001",
        role="speaker",
        region_ids=tuple(region.id for region in regions),
        start_ms=min(timestamps),
        end_ms=max(timestamps) + 1,
        confidence=0.9,
        motion="low",
    )
    return SimpleNamespace(
        frame_manifest={"frames": frames},
        regions=regions,
        tracks=(track,),
    )


def crop_plan(*, allow_full_frame_fallback: bool = False) -> EditPlan:
    return EditPlan(
        planner_version="test.v1",
        source_duration_ms=30_000,
        requested_capabilities=("crop", "hard_cut", "subtitles"),
        clips=(ClipEditPlan(
            clip_index=1,
            source_window=TimeWindow(start_ms=20_000, end_ms=29_000),
            output_name="short-01.mp4",
            segments=(EditSegment(
                id="segment-01",
                source_window=TimeWindow(start_ms=20_000, end_ms=29_000),
                timeline_window=TimeWindow(start_ms=0, end_ms=9_000),
                layout=LayoutSpec(
                    mode="crop",
                    focal_target=FocalTarget(track_id="clip-01-track-001"),
                    fallback="fit" if allow_full_frame_fallback else "crop",
                    allow_full_frame_fallback=allow_full_frame_fallback,
                ),
                reason="keep the same-window speaker visible",
            ),),
        ),),
    )


class ClipVisualCoverageTests(unittest.TestCase):
    def test_same_window_track_coverage_passes(self):
        timestamps = (20_250, 22_500, 24_500, 26_500, 28_750)
        manifest = FrameManifest(
            source_duration_ms=30_000,
            source_width=1920,
            source_height=1080,
            frames=tuple(
                sampled_frame(f"clip-01-frame-{index:03d}", timestamp)
                for index, timestamp in enumerate(timestamps, start=1)
            ),
        )

        report = build_clip_visual_coverage(
            crop_plan(),
            visual=visual_for(timestamps),
            clip_frame_manifests={1: manifest},
        )

        self.assertEqual(report.blocking, 0)
        self.assertEqual(report.segments[0].observation_count, 5)
        self.assertGreater(report.segments[0].temporal_coverage_ratio, 0.9)
        self.assertEqual(report.to_dict()["version"], "clip_visual_coverage.v1")

    def test_cross_window_track_observations_block(self):
        timestamps = (1_000, 5_000, 10_000)
        manifest = FrameManifest(
            source_duration_ms=30_000,
            source_width=1920,
            source_height=1080,
            frames=tuple(
                sampled_frame(f"clip-01-frame-{index:03d}", timestamp)
                for index, timestamp in enumerate(timestamps, start=1)
            ),
        )

        report = build_clip_visual_coverage(
            crop_plan(),
            visual=visual_for(timestamps),
            clip_frame_manifests={1: manifest},
        )

        self.assertIn("CROP_VISUAL_OBSERVATION_MISSING", report.blocker_codes)
        self.assertEqual(report.affected_clip_indexes, (1,))

    def test_explicit_full_frame_fallback_can_degrade_without_false_crop_success(self):
        timestamps = (1_000,)
        manifest = FrameManifest(
            source_duration_ms=30_000,
            source_width=1920,
            source_height=1080,
            frames=(sampled_frame("clip-01-frame-001", 1_000),),
        )

        report = build_clip_visual_coverage(
            crop_plan(allow_full_frame_fallback=True),
            visual=visual_for(timestamps),
            clip_frame_manifests={1: manifest},
        )

        self.assertEqual(report.blocking, 0)
        self.assertTrue(report.segments[0].full_frame_fallback_allowed)


if __name__ == "__main__":
    unittest.main()
