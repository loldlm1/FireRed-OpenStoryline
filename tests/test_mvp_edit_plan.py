import os
import unittest
from unittest.mock import patch

from open_storyline.mvp.edit_plan import (
    AssetRequest,
    ClipEditPlan,
    EditPlan,
    EditPlanError,
    EditSegment,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
    build_shadow_edit_plan,
    resolve_agentic_server_mode,
    validate_edit_plan,
    validate_job_controls,
)
from open_storyline.mvp.shorts import ShortCandidate


class EditPlanContractTests(unittest.TestCase):
    def test_builds_versioned_shadow_plan(self):
        plan = build_shadow_edit_plan(
            [ShortCandidate(1_000, 21_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=30_000,
        )

        self.assertEqual(plan.version, "edit_plan.v1")
        self.assertEqual(plan.clips[0].segments[0].timeline_window.start_ms, 0)
        self.assertEqual(plan.clips[0].segments[0].timeline_window.end_ms, 20_000)
        self.assertEqual(plan.requested_capabilities, ("crop", "subtitles"))
        self.assertEqual(validate_edit_plan(plan.to_dict()), plan)

    def test_rejects_overlapping_primary_timeline(self):
        with self.assertRaises(ValueError):
            ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=0, end_ms=20_000),
                output_name="short.mp4",
                segments=(
                    EditSegment(
                        id="one",
                        source_window=TimeWindow(start_ms=0, end_ms=12_000),
                        timeline_window=TimeWindow(start_ms=0, end_ms=12_000),
                        layout=LayoutSpec(mode="fit"),
                        reason="first",
                    ),
                    EditSegment(
                        id="two",
                        source_window=TimeWindow(start_ms=10_000, end_ms=20_000),
                        timeline_window=TimeWindow(start_ms=10_000, end_ms=20_000),
                        layout=LayoutSpec(mode="fit"),
                        reason="second",
                    ),
                ),
            )

    def test_rejects_generated_asset_with_wrong_provider(self):
        with self.assertRaises(ValueError):
            AssetRequest(
                id="asset-1",
                kind="generated_image",
                provider="pexels",
                timeline_window=TimeWindow(start_ms=0, end_ms=1_000),
                purpose="illustrate",
                rationale="source has no suitable visual",
                prompt="a clean diagram",
            )

    def test_rejects_unknown_capability_and_non_finite_values(self):
        base = build_shadow_edit_plan(
            [ShortCandidate(0, 20_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=20_000,
        ).to_dict()
        base["requested_capabilities"] = ["raw_ffmpeg"]
        with self.assertRaises(EditPlanError):
            validate_edit_plan(base)
        with self.assertRaises(ValueError):
            LayoutSpec(mode="crop", safe_margin_ratio=float("nan"))

    def test_overlay_contract_requires_kind_payload(self):
        with self.assertRaises(ValueError):
            OverlaySpec(
                id="overlay",
                kind="text",
                timeline_window=TimeWindow(start_ms=0, end_ms=1_000),
            )

    def test_validates_job_controls_and_server_mode(self):
        self.assertEqual(validate_job_controls("agentic", "auto"), ("agentic", "auto"))
        with self.assertRaises(EditPlanError):
            validate_job_controls("cinematic", "auto")

        config = type("Config", (), {"mode": "off"})()
        with patch.dict(os.environ, {"OPENSTORYLINE_AGENTIC_EDITING_MODE": "shadow"}):
            self.assertEqual(resolve_agentic_server_mode(config), "shadow")


if __name__ == "__main__":
    unittest.main()
