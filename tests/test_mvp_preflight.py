import unittest

from open_storyline.mvp.edit_plan import (
    AssetRequest,
    ClipEditPlan,
    EditPlan,
    EditSegment,
    FocalTarget,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
    build_shadow_edit_plan,
)
from open_storyline.mvp.preflight import build_preflight
from open_storyline.mvp.shorts import ShortCandidate


class EditPreflightTests(unittest.TestCase):
    def test_shadow_plan_warns_without_blocking(self):
        plan = build_shadow_edit_plan(
            [ShortCandidate(0, 20_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=20_000,
        )
        report = build_preflight(
            plan,
            available_capabilities={"crop", "hard_cut", "subtitles"},
            asset_policy="auto",
        )

        self.assertEqual(report.status, "warn")
        self.assertEqual(report.blocking, 0)
        self.assertEqual(report.warnings, 1)
        self.assertEqual(report.to_dict()["version"], "edit_preflight.v1")

    def test_blocks_unavailable_capability(self):
        plan = build_shadow_edit_plan(
            [ShortCandidate(0, 20_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=20_000,
        )
        report = build_preflight(
            plan,
            available_capabilities={"subtitles"},
            asset_policy="auto",
        )
        self.assertEqual(report.status, "blocked")
        self.assertIn("CAPABILITY_UNAVAILABLE", {item.code for item in report.findings})

    def test_blocks_asset_when_policy_is_off_or_unresolved(self):
        asset = AssetRequest(
            id="generated-1",
            kind="generated_image",
            provider="9router",
            timeline_window=TimeWindow(start_ms=0, end_ms=2_000),
            purpose="explain an abstract idea",
            rationale="source contains only the speaker",
            prompt="a simple editorial illustration",
        )
        clip = ClipEditPlan(
            clip_index=1,
            source_window=TimeWindow(start_ms=0, end_ms=20_000),
            output_name="short.mp4",
            segments=(EditSegment(
                id="segment",
                source_window=TimeWindow(start_ms=0, end_ms=20_000),
                timeline_window=TimeWindow(start_ms=0, end_ms=20_000),
                layout=LayoutSpec(mode="fit"),
                reason="keep source visible",
            ),),
            asset_requests=(asset,),
        )
        plan = EditPlan(
            planner_version="test.v1",
            source_duration_ms=20_000,
            requested_capabilities=("fit", "hard_cut", "subtitles"),
            clips=(clip,),
        )

        capabilities = {"fit", "hard_cut", "subtitles"}
        off = build_preflight(plan, available_capabilities=capabilities, asset_policy="off")
        unresolved = build_preflight(plan, available_capabilities=capabilities, asset_policy="auto")
        resolved = build_preflight(
            plan,
            available_capabilities=capabilities,
            asset_policy="auto",
            resolved_asset_ids={"generated-1"},
        )

        self.assertEqual(off.status, "blocked")
        self.assertEqual(unresolved.status, "blocked")
        self.assertEqual(resolved.status, "ready")

    def test_blocks_unknown_visual_and_evidence_references(self):
        clip = ClipEditPlan(
            clip_index=1,
            source_window=TimeWindow(start_ms=0, end_ms=20_000),
            output_name="short.mp4",
            segments=(EditSegment(
                id="segment",
                source_window=TimeWindow(start_ms=0, end_ms=20_000),
                timeline_window=TimeWindow(start_ms=0, end_ms=20_000),
                layout=LayoutSpec(
                    mode="crop",
                    focal_target=FocalTarget(region_id="region-missing"),
                ),
                reason="track visible evidence",
                evidence_ids=("evidence-missing",),
            ),),
        )
        plan = EditPlan(
            planner_version="test.v1",
            source_duration_ms=20_000,
            requested_capabilities=("crop", "hard_cut", "subtitles"),
            clips=(clip,),
        )
        report = build_preflight(
            plan,
            available_capabilities={"crop", "hard_cut", "subtitles"},
            asset_policy="auto",
        )
        codes = {finding.code for finding in report.findings}
        self.assertIn("REGION_REFERENCE_UNKNOWN", codes)
        self.assertIn("EVIDENCE_REFERENCE_UNKNOWN", codes)

    def test_blocks_subtitle_zone_conflicts_and_unresolved_image_layers(self):
        clip = ClipEditPlan(
            clip_index=1,
            source_window=TimeWindow(start_ms=0, end_ms=20_000),
            output_name="short.mp4",
            segments=(EditSegment(
                id="segment",
                source_window=TimeWindow(start_ms=0, end_ms=20_000),
                timeline_window=TimeWindow(start_ms=0, end_ms=20_000),
                layout=LayoutSpec(mode="fit"),
                overlays=(
                    OverlaySpec(
                        id="text-hook",
                        kind="text",
                        timeline_window=TimeWindow(start_ms=0, end_ms=2000),
                        text="Hook",
                        position="bottom",
                    ),
                    OverlaySpec(
                        id="image-1",
                        kind="image",
                        timeline_window=TimeWindow(start_ms=3000, end_ms=5000),
                        asset_id="asset-1",
                        position="top_right",
                    ),
                ),
                reason="requested timeline layers",
            ),),
        )
        plan = EditPlan(
            planner_version="test.v1",
            source_duration_ms=20_000,
            requested_capabilities=(
                "fit", "hard_cut", "image_overlay", "text_emphasis", "subtitles"
            ),
            clips=(clip,),
        )
        report = build_preflight(
            plan,
            available_capabilities=plan.requested_capabilities,
            asset_policy="auto",
        )
        codes = {finding.code for finding in report.findings}
        self.assertIn("SUBTITLE_SAFE_ZONE_CONFLICT", codes)
        self.assertIn("ASSET_UNRESOLVED", codes)


if __name__ == "__main__":
    unittest.main()
