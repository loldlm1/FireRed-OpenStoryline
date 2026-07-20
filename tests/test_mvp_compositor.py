import json
from types import SimpleNamespace
import unittest

from open_storyline.mvp.compositor import CompositionError, resolve_clip_composition
from open_storyline.mvp.edit_plan import (
    ClipEditPlan,
    EditSegment,
    FocalTarget,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
)
from open_storyline.mvp.ffmpeg_filters import FilterGraphError, build_reframe_filtergraph
from open_storyline.mvp.render import MediaInfo
from open_storyline.mvp.visual_understanding import NormalizedBox, RegionObservation


def region(region_id: str, frame_id: str, *, x: float, width: float, role: str = "speaker"):
    return RegionObservation(
        id=region_id,
        frame_id=frame_id,
        role=role,
        bbox=NormalizedBox(x=x, y=0.1, width=width, height=0.8),
        confidence=0.9,
        salience=0.9,
        description="visible target",
    )


def visual(frames, regions):
    return SimpleNamespace(
        frame_manifest={"frames": frames},
        regions=tuple(regions),
        tracks=(),
    )


def clip_plan(segments):
    return ClipEditPlan(
        clip_index=1,
        source_window=TimeWindow(start_ms=0, end_ms=4000),
        output_name="short-01.mp4",
        segments=tuple(segments),
    )


class CompositorTests(unittest.TestCase):
    def test_execution_dict_serializes_nested_overlay_windows(self):
        composition = resolve_clip_composition(
            clip_plan([EditSegment(
                id="overlay-segment",
                source_window=TimeWindow(start_ms=0, end_ms=4000),
                timeline_window=TimeWindow(start_ms=0, end_ms=4000),
                layout=LayoutSpec(mode="fit"),
                overlays=(OverlaySpec(
                    id="presenter-pip",
                    kind="pip",
                    source_window=TimeWindow(start_ms=500, end_ms=2500),
                    timeline_window=TimeWindow(start_ms=1000, end_ms=3000),
                    position="top_right",
                ),),
                reason="keep the supporting source visible",
            )]),
            visual=visual([], []),
            source_media=MediaInfo(4000, 640, 360, True),
            output_width=180,
            output_height=320,
        )

        payload = composition.to_dict()

        json.dumps(payload)
        overlay = payload["segments"][0]["overlays"][0]
        self.assertEqual(overlay["timeline_window"], {"start_ms": 1000, "end_ms": 3000})
        self.assertEqual(overlay["source_window"], {"start_ms": 500, "end_ms": 2500})

    def test_tracks_a_semantic_target_and_falls_back_for_wide_content(self):
        source = MediaInfo(4000, 640, 360, True)
        crop_segment = EditSegment(
            id="crop-right",
            source_window=TimeWindow(start_ms=0, end_ms=4000),
            timeline_window=TimeWindow(start_ms=0, end_ms=4000),
            layout=LayoutSpec(
                mode="crop",
                focal_target=FocalTarget(region_id="right"),
                fallback="letterbox",
            ),
            reason="keep the right subject visible",
        )
        crop_visual = visual(
            [{"id": "frame-001", "timestamp_ms": 1000}],
            [region("right", "frame-001", x=0.72, width=0.18)],
        )
        crop = resolve_clip_composition(
            clip_plan([crop_segment]),
            visual=crop_visual,
            source_media=source,
            output_width=180,
            output_height=320,
        )
        self.assertEqual(crop.segments[0].strategy, "crop")
        self.assertGreater(crop.segments[0].crop.x, 350)
        self.assertEqual(crop.fallback_count, 0)

        wide_segment = crop_segment.model_copy(update={
            "id": "wide-screen",
            "layout": LayoutSpec(
                mode="crop",
                focal_target=FocalTarget(region_id="screen"),
                fallback="letterbox",
                allow_full_frame_fallback=True,
            ),
        })
        wide_visual = visual(
            [{"id": "frame-001", "timestamp_ms": 1000}],
            [region("screen", "frame-001", x=0.05, width=0.9, role="screen")],
        )
        wide = resolve_clip_composition(
            clip_plan([wide_segment]),
            visual=wide_visual,
            source_media=source,
            output_width=180,
            output_height=320,
        )
        self.assertEqual(wide.segments[0].strategy, "letterbox")
        self.assertTrue(wide.segments[0].fallback_used)
        self.assertTrue(wide.segments[0].fallback_allowed)
        self.assertLess(wide.segments[0].expected_active_area_ratio, 0.4)

        unapproved = wide_segment.model_copy(update={
            "layout": wide_segment.layout.model_copy(update={
                "allow_full_frame_fallback": False,
            }),
        })
        with self.assertRaises(CompositionError) as caught:
            resolve_clip_composition(
                clip_plan([unapproved]),
                visual=wide_visual,
                source_media=source,
                output_width=180,
                output_height=320,
            )
        self.assertEqual(caught.exception.code, "COMPOSITION_CROP_TARGET_TOO_WIDE")

    def test_bounds_adjacent_crop_velocity_and_records_center_fallback(self):
        source = MediaInfo(4000, 640, 360, True)
        segments = [
            EditSegment(
                id="left",
                source_window=TimeWindow(start_ms=0, end_ms=2000),
                timeline_window=TimeWindow(start_ms=0, end_ms=2000),
                layout=LayoutSpec(mode="crop", focal_target=FocalTarget(region_id="left")),
                reason="left target",
            ),
            EditSegment(
                id="right",
                source_window=TimeWindow(start_ms=2000, end_ms=4000),
                timeline_window=TimeWindow(start_ms=2000, end_ms=4000),
                layout=LayoutSpec(mode="crop", focal_target=FocalTarget(region_id="right")),
                reason="right target",
            ),
        ]
        evidence = visual(
            [
                {"id": "frame-left", "timestamp_ms": 1000},
                {"id": "frame-right", "timestamp_ms": 3000},
            ],
            [
                region("left", "frame-left", x=0.02, width=0.18),
                region("right", "frame-right", x=0.8, width=0.18),
            ],
        )
        composition = resolve_clip_composition(
            clip_plan(segments),
            visual=evidence,
            source_media=source,
            output_width=180,
            output_height=320,
            hysteresis_ratio=0,
            smoothing_alpha=1,
            max_crop_velocity_ratio_per_second=0.1,
        )
        delta = composition.segments[1].crop.x - composition.segments[0].crop.x
        self.assertLessEqual(delta, 128)
        self.assertTrue(composition.segments[1].smoothed)

        fallback_segment = EditSegment(
            id="fallback",
            source_window=TimeWindow(start_ms=0, end_ms=4000),
            timeline_window=TimeWindow(start_ms=0, end_ms=4000),
            layout=LayoutSpec(
                mode="crop",
                focal_target=FocalTarget(region_id="missing"),
                fallback="crop",
            ),
            reason="explicit center fallback",
        )
        fallback = resolve_clip_composition(
            clip_plan([fallback_segment]),
            visual=visual([], []),
            source_media=source,
            output_width=180,
            output_height=320,
        )
        self.assertEqual(fallback.fallback_count, 1)
        self.assertEqual(fallback.segments[0].strategy, "crop")
        self.assertEqual(fallback.segments[0].expected_active_area_ratio, 1.0)
        self.assertIn("fallback", fallback.segments[0].reason.lower())

    def test_resolved_crop_contains_all_same_window_target_observations(self):
        source = MediaInfo(4000, 640, 360, True)
        segment = EditSegment(
            id="tracked-speaker",
            source_window=TimeWindow(start_ms=0, end_ms=4000),
            timeline_window=TimeWindow(start_ms=0, end_ms=4000),
            layout=LayoutSpec(
                mode="crop",
                focal_target=FocalTarget(semantic_role="speaker"),
                fallback="crop",
            ),
            reason="keep the moving speaker visible",
        )
        observations = [
            region("speaker-left", "frame-left", x=0.4, width=0.1),
            region("speaker-right", "frame-right", x=0.5, width=0.1),
        ]
        composition = resolve_clip_composition(
            clip_plan([segment]),
            visual=visual(
                [
                    {"id": "frame-left", "timestamp_ms": 500},
                    {"id": "frame-right", "timestamp_ms": 3500},
                ],
                observations,
            ),
            source_media=source,
            output_width=180,
            output_height=320,
        )

        crop = composition.segments[0].crop
        self.assertIsNotNone(crop)
        for observation in observations:
            self.assertLessEqual(crop.x, observation.bbox.x * source.width)
            self.assertGreaterEqual(
                crop.x + crop.width,
                (observation.bbox.x + observation.bbox.width) * source.width,
            )

    def test_filtergraph_is_server_generated_bounded_and_requires_audio(self):
        segment = SimpleNamespace(
            source_window=TimeWindow(start_ms=0, end_ms=4000),
            strategy="fit",
            crop=None,
        )
        graph, video, audio = build_reframe_filtergraph(
            [segment],
            output_width=180,
            output_height=320,
            subtitle_filename="short-01.srt",
            has_audio=True,
        )
        self.assertEqual((video, audio), ("vout", "a0"))
        self.assertIn("trim=start=0.000:end=4.000", graph)
        self.assertNotIn("../", graph)

        second = SimpleNamespace(
            source_window=TimeWindow(start_ms=4000, end_ms=8000),
            strategy="fit",
            crop=None,
        )
        multi, video, audio = build_reframe_filtergraph(
            [segment, second],
            output_width=180,
            output_height=320,
            subtitle_filename=None,
            has_audio=True,
        )
        self.assertEqual((video, audio), ("vchain1", "achain1"))
        self.assertIn("split=2", multi)
        self.assertIn("concat=n=2:v=1:a=0", multi)
        self.assertIn("concat=n=2:v=0:a=1", multi)

        with self.assertRaises(FilterGraphError):
            build_reframe_filtergraph(
                [segment],
                output_width=181,
                output_height=320,
                subtitle_filename="../hostile.srt",
                has_audio=True,
            )
        with self.assertRaises(FilterGraphError) as caught:
            build_reframe_filtergraph(
                [segment],
                output_width=180,
                output_height=320,
                subtitle_filename=None,
                has_audio=False,
            )
        self.assertEqual(caught.exception.code, "FILTER_AUDIO_REQUIRED")


if __name__ == "__main__":
    unittest.main()
