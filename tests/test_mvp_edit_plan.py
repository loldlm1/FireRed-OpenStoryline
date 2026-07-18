import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from open_storyline.mvp.edit_plan import (
    AssetRequest,
    AgenticEditPlanner,
    ClipEditPlan,
    EditPlan,
    EditPlanError,
    EditSegment,
    FocalTarget,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
    build_shadow_edit_plan,
    resolve_agentic_server_mode,
    validate_edit_plan,
    validate_job_controls,
)
from open_storyline.mvp.shorts import ShortCandidate, ShortsPlan, build_shorts_plan_artifact
from open_storyline.mvp.scene_boundaries import build_scene_boundaries


class FakeRegion:
    def __init__(self, role: str):
        self.id = "region-1"
        self.frame_id = "frame-001"
        self.role = role

    def model_dump(self, *, mode="json"):
        return {
            "id": self.id,
            "frame_id": self.frame_id,
            "role": self.role,
            "bbox": {"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.7},
            "confidence": 0.9,
            "salience": 0.9,
            "description": "relevant visible subject",
        }


class FakeEditClient:
    model = "cx/gpt-5.6-sol"

    def __init__(self, response):
        self.response = response
        self.call = None

    async def complete_json(self, **kwargs):
        self.call = kwargs
        return self.response


def planner_fixture(role: str, editing_prompt: str):
    clip = ShortCandidate(0, 20_000, "Title", "Hook", "Reason", 0.9)
    shorts_plan = ShortsPlan(clips=[clip], rejected=[])
    scenes = build_scene_boundaries([], source_duration_ms=30_000, threshold=0.35)
    visual = SimpleNamespace(
        frame_manifest={"frames": [{
            "id": "frame-001",
            "timestamp_ms": 5000,
            "scene_id": "scene-001",
            "width": 512,
            "height": 288,
        }]},
        regions=(FakeRegion(role),),
        tracks=(),
    )
    artifact = build_shorts_plan_artifact(
        shorts_plan,
        transcript_segments=[{"start": 0, "end": 20_000, "text": "Visible explanation"}],
        scene_report=scenes,
        visual_understanding=visual,
    )
    response = {
        "requested_capabilities": ["crop", "hard_cut", "subtitles"],
        "clips": [{
            "clip_index": 1,
            "title": "Title",
            "source_window": {"start_ms": 0, "end_ms": 20_000},
            "output_name": "short-01.mp4",
            "segments": [{
                "id": "segment-1",
                "source_window": {"start_ms": 0, "end_ms": 20_000},
                "timeline_window": {"start_ms": 0, "end_ms": 20_000},
                "layout": {
                    "mode": "crop",
                    "focal_target": {"region_id": "region-1"},
                    "fallback": "fit",
                },
                "transition_in": {"kind": "cut", "duration_ms": 0},
                "reason": "Keep the prompt-relevant visible subject readable.",
                "evidence_ids": ["region-1"],
            }],
            "asset_requests": [],
        }],
    }
    client = FakeEditClient(response)
    planner = AgenticEditPlanner(client)
    kwargs = {
        "editing_prompt": editing_prompt,
        "shorts_plan": shorts_plan,
        "shorts_plan_artifact": artifact,
        "transcript_segments": [{"start": 0, "end": 20_000, "text": "Visible explanation"}],
        "scene_report": scenes,
        "visual_understanding": visual,
        "source_duration_ms": 30_000,
        "asset_policy": "auto",
        "max_segments_per_clip": 24,
        "max_overlays_per_clip": 12,
        "max_assets_per_clip": 4,
    }
    return planner, client, kwargs


class EditPlanContractTests(unittest.TestCase):
    def test_builds_versioned_shadow_plan(self):
        plan = build_shadow_edit_plan(
            [ShortCandidate(1_000, 21_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=30_000,
        )

        self.assertEqual(plan.version, "edit_plan.v1")
        self.assertEqual(plan.clips[0].segments[0].timeline_window.start_ms, 0)
        self.assertEqual(plan.clips[0].segments[0].timeline_window.end_ms, 20_000)
        self.assertEqual(plan.requested_capabilities, ("crop", "hard_cut", "subtitles"))
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

    def test_rejects_unsafe_output_paths_and_timeline_gaps(self):
        with self.assertRaises(ValueError):
            ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=0, end_ms=20_000),
                output_name="../short.mp4",
                segments=(EditSegment(
                    id="one",
                    source_window=TimeWindow(start_ms=0, end_ms=20_000),
                    timeline_window=TimeWindow(start_ms=0, end_ms=20_000),
                    layout=LayoutSpec(mode="fit"),
                    reason="safe",
                ),),
            )

        with self.assertRaises(ValueError):
            ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=0, end_ms=20_000),
                output_name="short.mp4",
                segments=(
                    EditSegment(
                        id="one",
                        source_window=TimeWindow(start_ms=0, end_ms=10_000),
                        timeline_window=TimeWindow(start_ms=0, end_ms=10_000),
                        layout=LayoutSpec(mode="fit"),
                        reason="first",
                    ),
                    EditSegment(
                        id="two",
                        source_window=TimeWindow(start_ms=11_000, end_ms=20_000),
                        timeline_window=TimeWindow(start_ms=11_000, end_ms=20_000),
                        layout=LayoutSpec(mode="fit"),
                        reason="gap",
                    ),
                ),
            )


class AgenticEditPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_niche_context_uses_one_general_schema_without_forced_assets(self):
        cases = [
            ("screen", "Keep the moving chart readable."),
            ("speaker", "Prioritize the interview guest's face."),
            ("demonstration_target", "Show the tutorial action clearly."),
            ("object", "Keep the demonstrated product visible."),
        ]
        for role, editing_prompt in cases:
            with self.subTest(role=role):
                planner, client, kwargs = planner_fixture(role, editing_prompt)
                plan = await planner.plan(**kwargs)
                payload = json.loads(client.call["user_prompt"])

                self.assertEqual(plan.clips[0].source_window.end_ms, 20_000)
                self.assertEqual(plan.clips[0].asset_requests, ())
                self.assertEqual(plan.clips[0].segments[0].layout.focal_target.region_id, "region-1")
                self.assertEqual(payload["editing_prompt"], editing_prompt)
                self.assertEqual(payload["clips"][0]["regions"][0]["role"], role)

    async def test_rejects_clip_expansion_and_undeclared_capabilities(self):
        planner, client, kwargs = planner_fixture("speaker", "Keep the speaker visible.")
        client.response["clips"][0]["source_window"]["end_ms"] = 21_000
        client.response["clips"][0]["segments"][0]["source_window"]["end_ms"] = 21_000
        client.response["clips"][0]["segments"][0]["timeline_window"]["end_ms"] = 21_000
        with self.assertRaises(EditPlanError) as caught:
            await planner.plan(**kwargs)
        self.assertEqual(caught.exception.code, "EDIT_PLAN_CLIP_BOUNDS_INVALID")

        planner, client, kwargs = planner_fixture("speaker", "Keep the speaker visible.")
        client.response["requested_capabilities"].remove("hard_cut")
        with self.assertRaises(EditPlanError) as caught:
            await planner.plan(**kwargs)
        self.assertEqual(caught.exception.code, "EDIT_PLAN_CAPABILITY_UNDECLARED")


if __name__ == "__main__":
    unittest.main()
