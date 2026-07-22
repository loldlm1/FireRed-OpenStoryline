from __future__ import annotations

from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

from open_storyline.mvp.compositor import resolve_clip_composition
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
from open_storyline.mvp.creative_intent import (
    CreativeIntentDecision,
    build_creative_intent,
    validate_creative_intent_conformance,
)
from open_storyline.mvp.fallbacks import (
    FallbackDirective,
    FallbackConfigurationError,
    baseline_fallbacks_enabled,
    compile_baseline_plan,
)
from open_storyline.mvp.outcomes import build_completed_outcome_report
from open_storyline.mvp.render import MediaInfo


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
    def test_registered_reference_fallback_preserves_unaffected_operations(self):
        segment = EditSegment(
            id="speaker",
            source_window=TimeWindow(start_ms=0, end_ms=4000),
            timeline_window=TimeWindow(start_ms=0, end_ms=4000),
            layout=LayoutSpec(
                mode="crop",
                focal_target=FocalTarget(region_id="missing-region"),
            ),
            overlays=(OverlaySpec(
                id="title",
                kind="text",
                timeline_window=TimeWindow(start_ms=0, end_ms=1000),
                text="Keep me",
                opacity=0.8,
                position="top",
            ),),
            evidence_ids=("missing-region",),
            reason="Follow the visible speaker.",
        )

        result = compile_baseline_plan(
            plan_with_segment(segment),
            available_capabilities={
                "fit", "hard_cut", "text_emphasis", "subtitles"
            },
            remaining_defects=(FallbackDirective(
                code="REGION_REFERENCE_UNKNOWN",
                clip_index=1,
                segment_id="speaker",
                attempt_evidenced=True,
            ),),
        )

        repaired = result.plan.clips[0].segments[0]
        self.assertEqual(repaired.layout.mode, "fit")
        self.assertIsNone(repaired.layout.focal_target)
        self.assertEqual(repaired.evidence_ids, ())
        self.assertEqual(repaired.overlays, segment.overlays)
        self.assertEqual(result.entries[-1].requested, "REGION_REFERENCE_UNKNOWN")
        self.assertEqual(result.entries[-1].code, "VISUAL_REFRAME_FALLBACK")

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
            remaining_defects=(FallbackDirective(
                code="CROP_VISUAL_OBSERVATION_MISSING",
                clip_index=1,
                segment_id="speaker",
                attempt_evidenced=True,
            ),),
        )

        compiled = result.plan.clips[0].segments[0]
        self.assertEqual(compiled.layout.mode, "fit")
        self.assertIsNone(compiled.layout.focal_target)
        self.assertEqual(result.entries[0].code, "VISUAL_REFRAME_FALLBACK")
        self.assertEqual(result.ledger()["status"], "with_limitations")

    def test_visual_coverage_fallback_preserves_required_reframe_sequence(self):
        segment_ids = ("reframe-1", "reframe-2", "reframe-3")
        windows = (
            TimeWindow(start_ms=0, end_ms=1333),
            TimeWindow(start_ms=1333, end_ms=2666),
            TimeWindow(start_ms=2666, end_ms=4000),
        )
        segments = tuple(
            EditSegment(
                id=segment_id,
                source_window=window,
                timeline_window=window,
                layout=LayoutSpec(
                    mode="crop",
                    focal_target=FocalTarget(track_id="speaker-track"),
                    max_zoom=zoom,
                ),
                evidence_ids=("speaker-track",),
                reason="follow the speaker",
            )
            for segment_id, window, zoom in zip(
                segment_ids,
                windows,
                (1.04, 1.08, 1.06),
            )
        )
        plan = EditPlan(
            planner_version="test.v1",
            source_duration_ms=4000,
            requested_capabilities=(
                "crop",
                "fit",
                "focus_zoom",
                "hard_cut",
                "subtitles",
            ),
            clips=(ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=0, end_ms=4000),
                output_name="short-01.mp4",
                segments=segments,
                intent_decisions=(CreativeIntentDecision(
                    intent_id="prompt-reframe-sequence",
                    decision="execute",
                    operation_ids=segment_ids,
                ),),
            ),),
        )
        coverage = SimpleNamespace(segments=(SimpleNamespace(
            clip_index=1,
            segment_id="reframe-2",
            blocker_codes=("CROP_VISUAL_TEMPORAL_COVERAGE_LOW",),
        ),))

        result = compile_baseline_plan(
            plan,
            visual_coverage=coverage,
            available_capabilities={
                "crop", "fit", "focus_zoom", "hard_cut", "subtitles"
            },
            remaining_defects=(FallbackDirective(
                code="CROP_VISUAL_TEMPORAL_COVERAGE_LOW",
                clip_index=1,
                segment_id="reframe-2",
                attempt_evidenced=True,
            ),),
            enforce_attempt_gate=True,
        )

        compiled = result.plan.clips[0].segments[1]
        self.assertEqual(
            [item.layout.mode for item in result.plan.clips[0].segments],
            ["crop", "crop", "crop"],
        )
        self.assertEqual(compiled.layout.mode, "crop")
        self.assertIsNone(compiled.layout.focal_target)
        self.assertEqual(compiled.layout.fallback, "crop")
        self.assertFalse(compiled.layout.allow_full_frame_fallback)
        self.assertEqual(compiled.layout.max_zoom, 1.08)
        self.assertEqual(compiled.evidence_ids, ())
        self.assertEqual(result.entries[0].executed, "bounded_center_reframe")
        intent = build_creative_intent(
            "Apply exactly 3 gentle reframes or zooms.",
            {"asset_policy": "off", "stock_policy": "off"},
            selected_clip_count=1,
        )
        validate_creative_intent_conformance(result.plan, intent)
        executable_segment = compiled.model_copy(update={
            "source_window": TimeWindow(
                start_ms=0,
                end_ms=compiled.source_window.duration_ms,
            ),
            "timeline_window": TimeWindow(
                start_ms=0,
                end_ms=compiled.timeline_window.duration_ms,
            ),
        })
        composition = resolve_clip_composition(
            ClipEditPlan(
                clip_index=1,
                source_window=executable_segment.source_window,
                output_name="short-01.mp4",
                segments=(executable_segment,),
            ),
            visual=SimpleNamespace(
                frame_manifest={"frames": []},
                regions=(),
                tracks=(),
            ),
            source_media=MediaInfo(4000, 1920, 1080, True),
            output_width=1080,
            output_height=1920,
        )
        resolved = composition.segments[0]
        self.assertEqual(resolved.operation, "focus_zoom")
        self.assertEqual(resolved.strategy, "crop")
        self.assertIsNotNone(resolved.crop)

    def test_active_picture_risk_replaces_letterbox_with_blurred_fit(self):
        segment = EditSegment(
            id="wide-source",
            source_window=TimeWindow(start_ms=0, end_ms=4000),
            timeline_window=TimeWindow(start_ms=0, end_ms=4000),
            layout=LayoutSpec(
                mode="letterbox",
                fallback="letterbox",
                allow_full_frame_fallback=True,
            ),
            reason="preserve the full landscape source",
        )

        result = compile_baseline_plan(
            plan_with_segment(segment),
            available_capabilities={"fit", "letterbox", "hard_cut", "subtitles"},
            remaining_defects=(FallbackDirective(
                code="PREDICTIVE_ACTIVE_PICTURE_RISK",
                clip_index=1,
                segment_id="wide-source",
                attempt_evidenced=True,
            ),),
            enforce_attempt_gate=True,
        )

        compiled = result.plan.clips[0].segments[0]
        self.assertEqual(compiled.layout.mode, "fit")
        self.assertEqual(compiled.layout.fallback, "fit")
        self.assertIn(
            "VISUAL_REFRAME_FALLBACK",
            {entry.code for entry in result.entries},
        )

    def test_repairable_fallback_requires_attempt_evidence_and_is_segment_local(self):
        affected = EditSegment(
            id="affected",
            source_window=TimeWindow(start_ms=0, end_ms=2000),
            timeline_window=TimeWindow(start_ms=0, end_ms=2000),
            layout=LayoutSpec(mode="crop", fallback="crop"),
            reason="affected crop",
        )
        untouched = EditSegment(
            id="untouched",
            source_window=TimeWindow(start_ms=2000, end_ms=4000),
            timeline_window=TimeWindow(start_ms=2000, end_ms=4000),
            layout=LayoutSpec(mode="crop", fallback="crop"),
            reason="unaffected crop",
        )
        plan = EditPlan(
            planner_version="test.v1",
            source_duration_ms=4000,
            requested_capabilities=("crop", "fit", "hard_cut", "subtitles"),
            clips=(ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=0, end_ms=4000),
                output_name="short-01.mp4",
                segments=(affected, untouched),
            ),),
        )
        directive = FallbackDirective(
            code="COMPOSITION_CROP_TARGET_TOO_WIDE",
            clip_index=1,
            segment_id="affected",
        )
        with self.assertRaises(FallbackConfigurationError) as caught:
            compile_baseline_plan(
                plan,
                remaining_defects=(directive,),
                enforce_attempt_gate=True,
            )
        self.assertEqual(caught.exception.code, "REPAIR_ATTEMPT_REQUIRED")

        result = compile_baseline_plan(
            plan,
            remaining_defects=(FallbackDirective(
                **{**directive.__dict__, "attempt_evidenced": True},
            ),),
            enforce_attempt_gate=True,
        )
        segments = result.plan.clips[0].segments
        self.assertEqual(segments[0].layout.mode, "fit")
        self.assertEqual(segments[1], untouched)
        self.assertIn(
            "VISUAL_REFRAME_FALLBACK",
            {entry.code for entry in result.entries},
        )

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
