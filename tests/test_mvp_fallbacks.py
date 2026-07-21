from __future__ import annotations

from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

from open_storyline.mvp.edit_plan import (
    AssetRequest,
    ClipEditPlan,
    EditPlan,
    EditSegment,
    FocalTarget,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
    TransitionSpec,
)
from open_storyline.mvp.fallbacks import (
    FallbackConfigurationError,
    baseline_fallbacks_enabled,
    compile_baseline_plan,
)
from open_storyline.mvp.outcomes import build_completed_outcome_report


def plan_with_segment(segment: EditSegment, *, assets=()) -> EditPlan:
    return EditPlan(
        planner_version="test.v1",
        source_duration_ms=4000,
        requested_capabilities=(
            "crop",
            "fit",
            "hard_cut",
            "image_overlay",
            "subtitles",
        ),
        clips=(ClipEditPlan(
            clip_index=1,
            source_window=TimeWindow(start_ms=0, end_ms=4000),
            output_name="short-01.mp4",
            segments=(segment,),
            asset_requests=tuple(assets),
        ),),
    )


class BaselineFallbackTests(unittest.TestCase):
    def test_flag_defaults_off_and_invalid_values_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(
                baseline_fallbacks_enabled(
                    SimpleNamespace(baseline_fallbacks_enabled=False)
                )
            )
        with patch.dict(
            os.environ,
            {"OPENSTORYLINE_BASELINE_FALLBACKS_ENABLED": "sometimes"},
        ):
            with self.assertRaises(FallbackConfigurationError):
                baseline_fallbacks_enabled(SimpleNamespace())

    def test_visual_coverage_blocker_compiles_to_content_preserving_fit(self):
        segment = EditSegment(
            id="speaker",
            source_window=TimeWindow(start_ms=0, end_ms=4000),
            timeline_window=TimeWindow(start_ms=0, end_ms=4000),
            layout=LayoutSpec(
                mode="crop",
                focal_target=FocalTarget(track_id="speaker-track"),
            ),
            reason="follow the speaker",
        )
        coverage = SimpleNamespace(segments=(SimpleNamespace(
            clip_index=1,
            segment_id="speaker",
            blocker_codes=("CROP_VISUAL_OBSERVATION_MISSING",),
        ),))

        result = compile_baseline_plan(
            plan_with_segment(segment),
            visual_coverage=coverage,
            available_capabilities={"fit", "hard_cut", "subtitles"},
        )

        compiled = result.plan.clips[0].segments[0]
        self.assertEqual(compiled.layout.mode, "fit")
        self.assertIsNone(compiled.layout.focal_target)
        self.assertEqual(result.entries[0].code, "VISUAL_REFRAME_FALLBACK")
        self.assertEqual(result.ledger()["status"], "with_limitations")

    def test_missing_asset_is_removed_and_caption_zone_is_repaired(self):
        window = TimeWindow(start_ms=0, end_ms=2000)
        segment = EditSegment(
            id="overlay",
            source_window=TimeWindow(start_ms=0, end_ms=4000),
            timeline_window=TimeWindow(start_ms=0, end_ms=4000),
            layout=LayoutSpec(mode="fit"),
            overlays=(
                OverlaySpec(
                    id="asset-overlay",
                    kind="image",
                    timeline_window=window,
                    asset_id="asset-1",
                    position="top_right",
                ),
                OverlaySpec(
                    id="text-overlay",
                    kind="text",
                    timeline_window=window,
                    text="Launch now",
                    position="bottom",
                ),
            ),
            reason="support the source",
        )
        asset = AssetRequest(
            id="asset-1",
            kind="generated_image",
            provider="9router",
            timeline_window=window,
            visual_gap="missing diagram",
            purpose="explain",
            rationale="support the explanation",
            prompt="simple diagram",
            required=False,
        )

        result = compile_baseline_plan(
            plan_with_segment(segment, assets=(asset,)),
            available_capabilities={
                "fit", "hard_cut", "image_overlay", "text_emphasis", "subtitles"
            },
            omitted_asset_ids={"asset-1"},
            cause_code="ASSET_PROVIDER_UNAVAILABLE",
        )

        compiled = result.plan.clips[0]
        self.assertEqual(compiled.asset_requests, ())
        self.assertEqual([item.id for item in compiled.segments[0].overlays], ["text-overlay"])
        self.assertEqual(compiled.segments[0].overlays[0].position, "top")
        self.assertEqual(
            {entry.code for entry in result.entries},
            {"EXTERNAL_ASSET_OMITTED", "CAPTION_SAFE_ZONE_FALLBACK"},
        )

    def test_unsupported_transition_uses_single_segment_baseline(self):
        first = EditSegment(
            id="first",
            source_window=TimeWindow(start_ms=0, end_ms=2500),
            timeline_window=TimeWindow(start_ms=0, end_ms=2500),
            layout=LayoutSpec(mode="fit"),
            reason="first",
        )
        second = EditSegment(
            id="second",
            source_window=TimeWindow(start_ms=2000, end_ms=4000),
            timeline_window=TimeWindow(start_ms=2000, end_ms=4000),
            layout=LayoutSpec(mode="fit"),
            transition_in=TransitionSpec(kind="xfade", duration_ms=500),
            reason="second",
        )
        plan = EditPlan(
            planner_version="test.v1",
            source_duration_ms=4000,
            requested_capabilities=("fit", "hard_cut", "xfade", "subtitles"),
            clips=(ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=0, end_ms=4000),
                output_name="short-01.mp4",
                segments=(first, second),
            ),),
        )

        result = compile_baseline_plan(
            plan,
            available_capabilities={"fit", "hard_cut", "subtitles"},
        )

        self.assertEqual(len(result.plan.clips[0].segments), 1)
        self.assertEqual(result.plan.clips[0].segments[0].transition_in.kind, "cut")
        self.assertEqual(result.entries[-1].code, "TRANSITION_FALLBACK")

    def test_outcome_report_keeps_playability_separate_from_limitations(self):
        result = compile_baseline_plan(
            plan_with_segment(EditSegment(
                id="segment",
                source_window=TimeWindow(start_ms=0, end_ms=4000),
                timeline_window=TimeWindow(start_ms=0, end_ms=4000),
                layout=LayoutSpec(mode="fit"),
                reason="source",
            )),
            force_minimal=True,
            cause_code="AGENTIC_PREFLIGHT_FAILED",
        )
        report = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": "short-01.srt"}],
            fallback_entries=result.entries,
            reused_stages=("transcript",),
            prior_limitation_codes=("VISUAL_REFRAME_FALLBACK", "RENDER_PREFLIGHT_FALLBACK"),
        )

        self.assertEqual(report["grade"], "with_limitations")
        self.assertEqual(report["technical_status"], "pass")
        self.assertEqual(report["limitations"][0]["code"], "RENDER_PREFLIGHT_FALLBACK")
        self.assertEqual(report["retry"]["reused_stage_names"], ["transcript"])
        self.assertEqual(
            report["retry"]["resolved_limitation_codes"],
            ["VISUAL_REFRAME_FALLBACK"],
        )
        self.assertEqual(
            report["retry"]["remaining_limitation_codes"],
            ["RENDER_PREFLIGHT_FALLBACK"],
        )


if __name__ == "__main__":
    unittest.main()
