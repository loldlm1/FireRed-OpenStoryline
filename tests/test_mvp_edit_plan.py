import json
import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from open_storyline.mvp.edit_plan import (
    AssetRequest,
    AgenticEditPlanner,
    CatalogSelection,
    ClipEditPlan,
    EditPlan,
    EditPlanError,
    EditSegment,
    FocalTarget,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
    TransitionSpec,
    build_shadow_edit_plan,
    resolve_agentic_server_mode,
    validate_edit_plan,
    validate_generated_asset_limit,
    validate_catalog_plan_context,
    validate_job_controls,
    validate_stock_asset_limit,
    validate_stock_policy,
    _normalize_edit_plan_response,
)
from open_storyline.mvp.creative_intent import build_creative_intent
from open_storyline.mvp.shorts import ShortCandidate, ShortsPlan, build_shorts_plan_artifact
from open_storyline.mvp.scene_boundaries import build_scene_boundaries
from open_storyline.mvp.structured_outputs import EDIT_PLAN_SCHEMA, structured_output


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
    reasoning_effort = "medium"

    def __init__(self, response):
        self.response = response
        self.call = None
        self.calls = []
        self.last_attempts = ()

    async def complete_structured(self, **kwargs):
        self.call = kwargs
        self.calls.append(kwargs)
        if isinstance(self.response, list):
            return self.response.pop(0)
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
    def test_normalizes_observed_source_fallback_aliases(self):
        for alias in (
            "keep_source_video",
            "source_only",
            "use_source_video",
            "Use source video",
        ):
            with self.subTest(alias=alias):
                normalized = _normalize_edit_plan_response({
                    "clips": [{"asset_requests": [{"fallback": alias}]}],
                })

                self.assertEqual(
                    normalized["clips"][0]["asset_requests"][0]["fallback"],
                    "source",
                )

    def test_normalizes_asset_failure_fallback_aliases(self):
        for alias in (
            "error",
            "Error if unavailable",
            "fail",
            "Fail if unavailable",
            "fail_job",
            "hard-fail",
            "raise error",
        ):
            with self.subTest(alias=alias):
                normalized = _normalize_edit_plan_response({
                    "clips": [{"asset_requests": [{"fallback": alias}]}],
                })

                self.assertEqual(
                    normalized["clips"][0]["asset_requests"][0]["fallback"],
                    "omit",
                )

    def test_derives_only_unambiguous_missing_asset_overlays(self):
        normalized = _normalize_edit_plan_response({
            "clips": [{
                "asset_requests": [
                    {
                        "id": "generated-1",
                        "kind": "generated_image",
                        "timeline_window": {"start_ms": 1000, "end_ms": 4000},
                    },
                    {
                        "id": "stock-1",
                        "kind": "stock_video",
                        "timeline_window": {"start_ms": 5000, "end_ms": 9000},
                    },
                ],
                "intent_decisions": [{
                    "intent_id": "prompt-generated-image",
                    "decision": "execute",
                    "asset_ids": ["generated-1"],
                }],
                "segments": [{
                    "timeline_window": {"start_ms": 0, "end_ms": 20_000},
                }],
            }],
        })

        overlays = normalized["clips"][0]["segments"][0]["overlays"]
        self.assertEqual(
            [(item["asset_id"], item["position"]) for item in overlays],
            [("generated-1", "top_left"), ("stock-1", "top_right")],
        )
        self.assertEqual(
            normalized["clips"][0]["intent_decisions"][0]["operation_ids"],
            ["asset-overlay-01"],
        )

    def test_derives_missing_required_operation_decisions(self):
        prompt = (
            "Start with an opening title, apply exactly 3 reframes or zooms, "
            "and use restrained transitions."
        )
        intent = build_creative_intent(
            prompt,
            {"asset_policy": "off", "stock_policy": "off"},
            selected_clip_count=1,
        )
        segments = []
        for index, (start, end) in enumerate(
            ((0, 7000), (7000, 14_000), (14_000, 20_000)),
            start=1,
        ):
            segments.append({
                "id": f"segment-{index}",
                "source_window": {"start_ms": start, "end_ms": end},
                "timeline_window": {"start_ms": start, "end_ms": end},
                "layout": {"mode": "crop"},
                "transition_in": {
                    "kind": "cut" if index == 1 else "fade",
                    "duration_ms": 0 if index == 1 else 220,
                },
                "overlays": ([{
                    "id": "opening-title",
                    "kind": "text",
                    "timeline_window": {"start_ms": 0, "end_ms": 2200},
                    "text": "Opening",
                }] if index == 1 else []),
            })

        normalized = _normalize_edit_plan_response(
            {"clips": [{"segments": segments, "intent_decisions": []}]},
            creative_intent=intent,
        )
        decisions = {
            item["intent_id"]: item["operation_ids"]
            for item in normalized["clips"][0]["intent_decisions"]
        }

        self.assertEqual(decisions["prompt-opening-title"], ["opening-title"])
        self.assertEqual(
            decisions["prompt-reframe-sequence"],
            ["segment-1", "segment-2", "segment-3"],
        )
        self.assertEqual(
            decisions["prompt-restrained-transitions"],
            ["segment-2", "segment-3"],
        )

        executable = build_shadow_edit_plan(
            [ShortCandidate(0, 20_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=20_000,
        ).to_dict()
        executable["clips"][0]["segments"][0].pop("overlays")
        executable["clips"][0]["asset_requests"] = [{
            "id": "generated-1",
            "kind": "generated_image",
            "provider": "9router",
            "timeline_window": {"start_ms": 1000, "end_ms": 4000},
            "visual_gap": "the source cannot show the abstract process",
            "purpose": "show the abstract process",
            "rationale": "an editorial still closes the visual gap",
            "prompt": "a restrained editorial process diagram",
        }]
        plan = validate_edit_plan(_normalize_edit_plan_response(executable))
        self.assertEqual(
            plan.clips[0].segments[0].overlays[0].asset_id,
            "generated-1",
        )

        ambiguous = _normalize_edit_plan_response({
            "clips": [{
                "asset_requests": [{
                    "id": "spanning-asset",
                    "kind": "stock_video",
                    "timeline_window": {"start_ms": 4000, "end_ms": 6000},
                }],
                "segments": [
                    {
                        "timeline_window": {"start_ms": 0, "end_ms": 5000},
                        "overlays": [],
                    },
                    {
                        "timeline_window": {"start_ms": 5000, "end_ms": 10_000},
                        "overlays": [],
                    },
                ],
            }],
        })
        self.assertFalse(any(
            segment["overlays"]
            for segment in ambiguous["clips"][0]["segments"]
        ))

    def test_normalizes_asset_backed_pip_without_deriving_a_duplicate(self):
        normalized = _normalize_edit_plan_response({
            "clips": [{
                "asset_requests": [{
                    "id": "stock-1",
                    "kind": "stock_video",
                    "timeline_window": {"start_ms": 1000, "end_ms": 5000},
                }],
                "segments": [{
                    "timeline_window": {"start_ms": 0, "end_ms": 8000},
                    "overlays": [{
                        "id": "stock-pip",
                        "kind": "pip",
                        "asset_id": "stock-1",
                        "source_window": {"start_ms": 1000, "end_ms": 5000},
                        "timeline_window": {"start_ms": 1000, "end_ms": 5000},
                    }],
                }],
            }],
        })

        overlays = normalized["clips"][0]["segments"][0]["overlays"]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0]["kind"], "image")
        self.assertEqual(overlays[0]["asset_id"], "stock-1")
        self.assertNotIn("source_window", overlays[0])

    def test_accepts_explicit_empty_optional_identifiers(self):
        target = FocalTarget(region_id="", track_id="", semantic_role="speaker")
        overlay = OverlaySpec(
            id="text-1",
            kind="text",
            timeline_window=TimeWindow(start_ms=0, end_ms=1000),
            text="Hook",
            asset_id="",
        )

        self.assertEqual(target.region_id, "")
        self.assertEqual(overlay.asset_id, "")

    def test_builds_versioned_shadow_plan(self):
        plan = build_shadow_edit_plan(
            [ShortCandidate(1_000, 21_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=30_000,
        )

        self.assertEqual(plan.version, "edit_plan.v2")
        self.assertEqual(plan.clips[0].segments[0].timeline_window.start_ms, 0)
        self.assertEqual(plan.clips[0].segments[0].timeline_window.end_ms, 20_000)
        self.assertEqual(plan.requested_capabilities, ("crop", "hard_cut", "subtitles"))
        self.assertEqual(validate_edit_plan(plan.to_dict()), plan)

    def test_catalog_contract_rejects_unknown_and_style_mismatched_ids(self):
        snapshot = {
            "version": "catalog_candidates.v1",
            "catalog_version": "2026.07.1",
            "manifest_sha256": "a" * 64,
            "entries": [
                {
                    "id": "style.clean-product",
                    "kind": "style_profile",
                    "config": {"catalog_ids": [
                        "caption.clean",
                        "color.clean-contrast",
                        "transition.crossfade",
                        "recipe.slow-zoom",
                    ]},
                },
                {"id": "caption.clean", "kind": "caption_treatment", "config": {}},
                {"id": "caption.bold-hook", "kind": "caption_treatment", "config": {}},
                {"id": "color.clean-contrast", "kind": "color_treatment", "config": {}},
                {"id": "transition.hard-cut", "kind": "transition", "config": {"operation": "hard_cut"}},
                {"id": "transition.crossfade", "kind": "transition", "config": {"operation": "fade"}},
                {"id": "recipe.slow-zoom", "kind": "recipe", "config": {}},
            ],
        }
        base = build_shadow_edit_plan(
            [ShortCandidate(0, 20_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=20_000,
        )
        clip = base.clips[0].model_copy(update={
            "catalog_selection": CatalogSelection(
                style_profile_id="style.clean-product",
                caption_treatment_id="caption.clean",
                color_treatment_id="color.clean-contrast",
                recipe_ids=("recipe.slow-zoom",),
            ),
            "segments": (
                base.clips[0].segments[0].model_copy(update={
                    "transition_in": TransitionSpec(
                        kind="cut",
                        duration_ms=0,
                        catalog_id="transition.hard-cut",
                    ),
                }),
            ),
        })
        plan = base.model_copy(update={
            "catalog_version": snapshot["catalog_version"],
            "catalog_manifest_sha256": snapshot["manifest_sha256"],
            "clips": (clip,),
        })

        self.assertEqual(validate_catalog_plan_context(plan, snapshot), plan)

        unknown = plan.model_copy(update={
            "clips": (clip.model_copy(update={
                "catalog_selection": clip.catalog_selection.model_copy(update={
                    "recipe_ids": ("recipe.invented",),
                }),
            }),),
        })
        with self.assertRaises(EditPlanError) as caught:
            validate_catalog_plan_context(unknown, snapshot)
        self.assertEqual(caught.exception.code, "EDIT_PLAN_CATALOG_ID_UNKNOWN")

        mismatched = plan.model_copy(update={
            "clips": (clip.model_copy(update={
                "catalog_selection": clip.catalog_selection.model_copy(update={
                    "caption_treatment_id": "caption.bold-hook",
                }),
            }),),
        })
        with self.assertRaises(EditPlanError) as caught:
            validate_catalog_plan_context(mismatched, snapshot)
        self.assertEqual(caught.exception.code, "EDIT_PLAN_CATALOG_STYLE_MISMATCH")

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
                visual_gap="the source has no usable diagram",
                purpose="illustrate",
                rationale="source has no suitable visual",
                prompt="a clean diagram",
            )

        with self.assertRaises(ValueError):
            AssetRequest(
                id="asset-2",
                kind="generated_image",
                provider="9router",
                timeline_window=TimeWindow(start_ms=0, end_ms=1_000),
                visual_gap="the source lacks a visual",
                purpose="illustrate",
                rationale="a still is justified",
                prompt="x" * 7001,
            )

        self.assertEqual(validate_generated_asset_limit(2), 2)
        with self.assertRaises(EditPlanError):
            validate_generated_asset_limit(9)
        self.assertEqual(validate_stock_policy("auto"), "auto")
        self.assertEqual(validate_stock_asset_limit(2), 2)
        with self.assertRaises(EditPlanError):
            validate_stock_policy("fallback")
        with self.assertRaises(EditPlanError):
            validate_stock_asset_limit(9)

        with self.assertRaises(ValueError):
            AssetRequest(
                id="stock-empty",
                kind="stock_image",
                provider="pexels",
                timeline_window=TimeWindow(start_ms=0, end_ms=1_000),
                visual_gap="the source lacks a visual",
                purpose="illustrate",
                rationale="a stock image is justified",
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

    def test_schema_evidence_uses_allowlisted_constraint_codes(self):
        payload = build_shadow_edit_plan(
            [ShortCandidate(0, 20_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=20_000,
        ).to_dict()
        payload["clips"][0]["segments"].append(
            deepcopy(payload["clips"][0]["segments"][0])
        )

        with self.assertRaises(EditPlanError) as caught:
            validate_edit_plan(payload)

        issues = caught.exception.to_dict()["evidence"]["validation"]["issues"]
        self.assertIn(
            "segment_ids_not_unique",
            {item.get("constraint_code") for item in issues},
        )

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

    def test_accepts_declared_crossfade_and_bounded_source_pip(self):
        first = EditSegment(
            id="first",
            source_window=TimeWindow(start_ms=0, end_ms=2000),
            timeline_window=TimeWindow(start_ms=0, end_ms=2000),
            layout=LayoutSpec(mode="source"),
            overlays=(OverlaySpec(
                id="pip-1",
                kind="pip",
                source_window=TimeWindow(start_ms=500, end_ms=1000),
                timeline_window=TimeWindow(start_ms=500, end_ms=1000),
                position="top_right",
                width_ratio=0.25,
                transition_ms=100,
            ),),
            reason="source cutaway with presenter PiP",
        )
        second = EditSegment(
            id="second",
            source_window=TimeWindow(start_ms=1500, end_ms=3500),
            timeline_window=TimeWindow(start_ms=1500, end_ms=3500),
            layout=LayoutSpec(mode="fit"),
            transition_in=TransitionSpec(kind="xfade", duration_ms=500),
            reason="bounded crossfade",
        )
        clip = ClipEditPlan(
            clip_index=1,
            source_window=TimeWindow(start_ms=0, end_ms=3500),
            output_name="short.mp4",
            segments=(first, second),
        )
        self.assertEqual(clip.segments[1].timeline_window.start_ms, 1500)
        self.assertEqual(clip.segments[0].overlays[0].z_index, 10)

        with self.assertRaises(ValueError):
            OverlaySpec(
                id="bad-pip",
                kind="pip",
                source_window=TimeWindow(start_ms=0, end_ms=1000),
                timeline_window=TimeWindow(start_ms=0, end_ms=1500),
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
                self.assertNotIn("output_contract", payload)
                self.assertEqual(
                    payload["exact_field_contract"]["EditSegment"],
                    [
                        "id",
                        "source_window",
                        "timeline_window",
                        "layout",
                        "transition_in",
                        "overlays",
                        "reason",
                        "evidence_ids",
                    ],
                )
                self.assertIn(
                    "intent_decisions",
                    payload["exact_field_contract"]["ClipEditPlan"],
                )
                self.assertNotIn("response_schema", client.call)
                self.assertEqual(client.call["schema_name"], "edit_plan.v1")
                self.assertEqual(client.call["reasoning_effort"], "medium")
                template = payload["valid_output_template"]
                self.assertEqual(
                    structured_output(EDIT_PLAN_SCHEMA).validate(template),
                    template,
                )
                self.assertEqual(template["clips"][0]["source_window"]["end_ms"], 20_000)
                self.assertEqual(
                    template["clips"][0]["segments"][0]["layout"]["focal_target"]["region_id"],
                    "region-1",
                )

    async def test_maps_footer_caption_intent_to_executable_segments(self):
        prompt = "Use readable footer-safe subtitles."
        planner, client, kwargs = planner_fixture("speaker", prompt)
        client.response["clips"][0]["intent_decisions"] = [{
            "intent_id": "prompt-footer-captions",
            "decision": "execute",
            "operation_ids": [{"id": "subtitle-overlay"}],
        }]
        kwargs["creative_intent"] = build_creative_intent(
            prompt,
            {
                "asset_policy": "off",
                "stock_policy": "off",
            },
            selected_clip_count=1,
        )

        plan = await planner.plan(**kwargs)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            plan.clips[0].intent_decisions[0].operation_ids,
            ("segment-1",),
        )

    async def test_deferred_repair_template_preserves_required_operations(self):
        prompt = (
            "Start with an opening title that says 'A Clear Opening', apply "
            "exactly 3 gentle reframes or zooms, and use restrained transitions."
        )
        planner, client, kwargs = planner_fixture("speaker", prompt)
        kwargs.update({
            "creative_intent": build_creative_intent(
                prompt,
                {"asset_policy": "off", "stock_policy": "off"},
                selected_clip_count=1,
            ),
            "defer_registry_repair": True,
        })

        plan = await planner.plan(**kwargs)
        clip = plan.clips[0]
        decisions = {
            item.intent_id: item.operation_ids for item in clip.intent_decisions
        }

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(planner.deferred_defects), 1)
        self.assertEqual(len(clip.segments), 3)
        self.assertEqual(
            [segment.transition_in.kind for segment in clip.segments],
            ["cut", "fade", "fade"],
        )
        self.assertEqual(clip.segments[0].overlays[0].text, "A Clear Opening")
        self.assertEqual(
            decisions["prompt-reframe-sequence"],
            tuple(segment.id for segment in clip.segments),
        )
        self.assertEqual(
            decisions["prompt-restrained-transitions"],
            tuple(segment.id for segment in clip.segments[1:]),
        )

    async def test_deferred_repair_template_uses_catalog_backed_transitions(self):
        prompt = (
            "Start with an opening title, apply exactly 3 gentle reframes or zooms, "
            "and use restrained transitions."
        )
        planner, _client, kwargs = planner_fixture("speaker", prompt)
        kwargs.update({
            "creative_intent": build_creative_intent(
                prompt,
                {"asset_policy": "off", "stock_policy": "off"},
                selected_clip_count=1,
            ),
            "defer_registry_repair": True,
            "catalog_snapshot": {
                "catalog_version": "2026.07.1",
                "manifest_sha256": "b" * 64,
                "entries": [
                    {
                        "id": "style.clean-product",
                        "kind": "style_profile",
                        "config": {"catalog_ids": [
                            "caption.clean",
                            "color.clean-contrast",
                            "transition.crossfade",
                        ]},
                    },
                    {
                        "id": "style.restrained-cinematic",
                        "kind": "style_profile",
                        "config": {"catalog_ids": [
                            "caption.editorial",
                            "color.cinematic-soft",
                            "transition.fade-black",
                        ]},
                    },
                    {
                        "id": "caption.clean",
                        "kind": "caption_treatment",
                        "config": {},
                    },
                    {
                        "id": "color.clean-contrast",
                        "kind": "color_treatment",
                        "config": {},
                    },
                    {
                        "id": "caption.editorial",
                        "kind": "caption_treatment",
                        "config": {},
                    },
                    {
                        "id": "color.cinematic-soft",
                        "kind": "color_treatment",
                        "config": {},
                    },
                    {
                        "id": "transition.hard-cut",
                        "kind": "transition",
                        "config": {"operation": "hard_cut", "duration_ms": 0},
                    },
                    {
                        "id": "transition.crossfade",
                        "kind": "transition",
                        "config": {"operation": "fade", "duration_ms": 220},
                    },
                    {
                        "id": "transition.fade-black",
                        "kind": "transition",
                        "config": {"operation": "fade", "duration_ms": 220},
                    },
                ],
            },
        })

        plan = await planner.plan(**kwargs)
        clip = plan.clips[0]

        self.assertEqual(
            [segment.transition_in.kind for segment in clip.segments],
            ["cut", "fade", "fade"],
        )
        self.assertEqual(
            [segment.transition_in.catalog_id for segment in clip.segments],
            ["transition.hard-cut", "transition.fade-black", "transition.fade-black"],
        )
        self.assertEqual(
            clip.catalog_selection.style_profile_id,
            "style.restrained-cinematic",
        )

    async def test_maps_title_reframes_and_transitions_to_executable_operations(self):
        prompt = (
            "Agrega un t\u00edtulo de apertura, aplica entre 2 y 4 reencuadres "
            "o zooms y usa transiciones suaves y discretas."
        )
        planner, client, kwargs = planner_fixture("speaker", prompt)
        response = client.response
        response["requested_capabilities"] = [
            "crop",
            "focus_zoom",
            "hard_cut",
            "fade",
            "text_emphasis",
            "subtitles",
        ]
        response["clips"][0]["segments"] = [
            {
                "id": "segment-1",
                "source_window": {"start_ms": 0, "end_ms": 7000},
                "timeline_window": {"start_ms": 0, "end_ms": 7000},
                "layout": {
                    "mode": "crop",
                    "focal_target": {"region_id": "region-1"},
                    "fallback": "fit",
                    "max_zoom": 1.1,
                },
                "transition_in": {"kind": "cut", "duration_ms": 0},
                "overlays": [{
                    "id": "opening-title",
                    "kind": "text",
                    "timeline_window": {"start_ms": 0, "end_ms": 2200},
                    "text": "Opening hook",
                    "position": "top",
                }],
                "reason": "Open with a concise title and speaker focus.",
                "evidence_ids": ["region-1"],
            },
            {
                "id": "segment-2",
                "source_window": {"start_ms": 7000, "end_ms": 14_000},
                "timeline_window": {"start_ms": 7000, "end_ms": 14_000},
                "layout": {
                    "mode": "crop",
                    "focal_target": {"region_id": "region-1"},
                    "fallback": "fit",
                    "max_zoom": 1.2,
                },
                "transition_in": {"kind": "fade", "duration_ms": 220},
                "overlays": [],
                "reason": "Shift the framing at the next editorial beat.",
                "evidence_ids": ["region-1"],
            },
            {
                "id": "segment-3",
                "source_window": {"start_ms": 14_000, "end_ms": 20_000},
                "timeline_window": {"start_ms": 14_000, "end_ms": 20_000},
                "layout": {
                    "mode": "crop",
                    "focal_target": {"region_id": "region-1"},
                    "fallback": "fit",
                    "max_zoom": 1.15,
                },
                "transition_in": {"kind": "fade", "duration_ms": 220},
                "overlays": [],
                "reason": "Use a final restrained focus change.",
                "evidence_ids": ["region-1"],
            },
        ]
        response["clips"][0]["intent_decisions"] = [
            {
                "intent_id": "prompt-opening-title",
                "decision": "execute",
                "operation_ids": [{"id": "opening-title"}],
            },
            {
                "intent_id": "prompt-reframe-sequence",
                "decision": "execute",
                "operation_ids": [{"id": "segment-1"}],
            },
            {
                "intent_id": "prompt-restrained-transitions",
                "decision": "execute",
                "operation_ids": [{"id": "segment-2"}],
            },
        ]
        kwargs["creative_intent"] = build_creative_intent(
            prompt,
            {"asset_policy": "off", "stock_policy": "off"},
            selected_clip_count=1,
        )

        plan = await planner.plan(**kwargs)
        decisions = {
            item.intent_id: item.operation_ids
            for item in plan.clips[0].intent_decisions
        }

        self.assertEqual(decisions["prompt-opening-title"], ("opening-title",))
        self.assertEqual(
            decisions["prompt-reframe-sequence"],
            ("segment-1", "segment-2", "segment-3"),
        )
        self.assertEqual(
            decisions["prompt-restrained-transitions"],
            ("segment-2", "segment-3"),
        )
        payload = json.loads(client.call["user_prompt"])
        operation_intents = {
            item["kind"]: item for item in payload["creative_intent"]["operation_intents"]
        }
        self.assertEqual(operation_intents["reframe_sequence"]["count_min"], 2)
        self.assertEqual(operation_intents["opening_title"]["start_max_ms"], 3500)

    async def test_does_not_invent_portrait_operation_without_a_crop(self):
        prompt = "Use a portrait reframe."
        planner, client, kwargs = planner_fixture("speaker", prompt)
        response = client.response
        response["requested_capabilities"] = ["fit", "hard_cut", "subtitles"]
        response["clips"][0]["segments"][0]["layout"] = {
            "mode": "fit",
            "fallback": "fit",
            "allow_full_frame_fallback": True,
        }
        response["clips"][0]["intent_decisions"] = [{
            "intent_id": "prompt-portrait-reframe",
            "decision": "execute",
            "operation_ids": ["fit-operation"],
        }]
        client.response = [deepcopy(response), deepcopy(response)]
        kwargs["creative_intent"] = build_creative_intent(
            prompt,
            {
                "asset_policy": "off",
                "stock_policy": "off",
            },
            selected_clip_count=1,
        )

        with self.assertRaises(EditPlanError) as caught:
            await planner.plan(**kwargs)

        self.assertEqual(caught.exception.code, "EDIT_PLAN_REPAIR_EXHAUSTED")
        self.assertEqual(
            caught.exception.evidence["attempts"][0]["intent_conformance"],
            {
                "constraint_code": "required_operation_mapping_invalid",
                "intent_id": "prompt-portrait-reframe",
            },
        )

    async def test_prior_attempt_quality_feedback_is_explicit_planner_input(self):
        planner, client, kwargs = planner_fixture(
            "speaker", "Keep the speaker primary and repair objective blockers."
        )
        kwargs["prior_attempt_quality_feedback"] = {
            "version": "quality_feedback.v1",
            "prior_attempt_id": "a" * 32,
            "prior_attempt_number": 1,
            "blocker_codes": ["ACTIVE_PICTURE_TOO_SMALL"],
            "active_picture": [{
                "clip_index": 1,
                "median_active_height_ratio": 0.31,
            }],
        }

        await planner.plan(**kwargs)
        payload = json.loads(client.call["user_prompt"])

        self.assertEqual(
            payload["prior_attempt_quality_feedback"]["blocker_codes"],
            ["ACTIVE_PICTURE_TOO_SMALL"],
        )
        self.assertEqual(payload["visual_coverage_feedback"], {})

    async def test_planner_receives_only_compact_catalog_ids_and_records_selection(self):
        planner, client, kwargs = planner_fixture(
            "speaker", "Create a clean product marketing short."
        )
        snapshot = {
            "version": "catalog_candidates.v1",
            "catalog_version": "2026.07.1",
            "manifest_sha256": "b" * 64,
            "aspect_ratio": "9:16",
            "requested_tags": ["clean", "marketing", "portrait", "product"],
            "entries": [
                {
                    "id": "style.clean-product",
                    "kind": "style_profile",
                    "label": "Clean Product",
                    "config": {"catalog_ids": [
                        "caption.clean",
                        "color.clean-contrast",
                        "transition.crossfade",
                        "recipe.slow-zoom",
                    ]},
                },
                {
                    "id": "caption.clean",
                    "kind": "caption_treatment",
                    "label": "Clean Captions",
                    "config": {"font_id": "font.caption.core"},
                },
                {
                    "id": "color.clean-contrast",
                    "kind": "color_treatment",
                    "label": "Clean Contrast",
                    "config": {"filter": "eq", "contrast": 1.06, "saturation": 1.02},
                },
                {
                    "id": "transition.hard-cut",
                    "kind": "transition",
                    "label": "Hard Cut",
                    "config": {"operation": "hard_cut", "duration_ms": 0},
                },
                {
                    "id": "transition.crossfade",
                    "kind": "transition",
                    "label": "Crossfade",
                    "config": {"operation": "fade", "duration_ms": 220},
                },
                {
                    "id": "recipe.slow-zoom",
                    "kind": "recipe",
                    "label": "Slow Zoom",
                    "config": {"operation": "focus_zoom"},
                },
            ],
        }
        client.response["clips"][0]["catalog_selection"] = {
            "style_profile_id": "style.clean-product",
            "caption_treatment_id": "caption.clean",
            "color_treatment_id": "color.clean-contrast",
            "recipe_ids": ["recipe.slow-zoom"],
        }
        client.response["clips"][0]["segments"][0]["transition_in"]["catalog_id"] = (
            "transition.hard-cut"
        )
        kwargs["catalog_snapshot"] = snapshot

        plan = await planner.plan(**kwargs)
        payload = json.loads(client.call["user_prompt"])

        self.assertEqual(
            payload["creative_catalog"]["manifest_sha256"],
            "b" * 64,
        )
        self.assertNotIn("https://", json.dumps(payload["creative_catalog"]))
        self.assertEqual(plan.catalog_version, "2026.07.1")
        self.assertEqual(
            plan.clips[0].catalog_selection.style_profile_id,
            "style.clean-product",
        )

    async def test_repairs_one_structurally_invalid_response(self):
        planner, client, kwargs = planner_fixture(
            "screen", "Keep the visible screen readable."
        )
        valid_response = client.response
        client.response = [
            {
                "format": {"aspect_ratio": "9:16"},
                "clips": [{
                    "clip_index": 1,
                    "segments": [{
                        "timeline": {"start_ms": 0, "end_ms": 20_000},
                        "source": {"start_ms": 0, "end_ms": 20_000},
                        "capability": "crop",
                    }],
                }],
            },
            valid_response,
        ]

        plan = await planner.plan(**kwargs)

        self.assertEqual(len(client.calls), 2)
        repair_payload = json.loads(client.calls[1]["user_prompt"])
        self.assertIn("repair_task", repair_payload)
        self.assertIn("do not collapse", repair_payload["repair_task"])
        self.assertNotIn("output_contract", repair_payload)
        self.assertNotIn("response_schema", client.calls[1])
        self.assertEqual(client.calls[1]["schema_name"], "edit_plan_repair.v1")
        self.assertEqual(client.calls[1]["reasoning_effort"], "medium")
        self.assertEqual(plan.clips[0].segments[0].layout.mode, "crop")

    async def test_render_mode_fails_after_invalid_repair(self):
        planner, client, kwargs = planner_fixture(
            "screen", "Keep the validated screen evidence visible."
        )
        invalid_response = {
            "format": {"aspect_ratio": "9:16"},
            "clips": [{"segments": [{"operation": "smart crop"}]}],
        }
        client.response = [deepcopy(invalid_response), deepcopy(invalid_response)]

        with self.assertRaises(EditPlanError) as caught:
            await planner.plan(**kwargs)

        self.assertEqual(caught.exception.code, "EDIT_PLAN_REPAIR_EXHAUSTED")
        self.assertEqual(len(client.calls), 2)

    async def test_repair_failure_persists_only_bounded_schema_evidence(self):
        planner, client, kwargs = planner_fixture(
            "speaker", "Use a relevant stock image."
        )
        response = client.response
        response["requested_capabilities"].append("image_overlay")
        response["clips"][0]["segments"][0]["overlays"] = [{
            "id": "stock-overlay",
            "kind": "image",
            "timeline_window": {"start_ms": 1000, "end_ms": 3000},
            "asset_id": "stock-1",
            "position": "top_right",
        }]
        response["clips"][0]["asset_requests"] = [{
            "id": "stock-1",
            "kind": "stock_image",
            "provider": "pexels",
            "timeline_window": {"start_ms": 1000, "end_ms": 3000},
            "visual_gap": "the source lacks a supporting visual",
            "purpose": "support the explanation",
            "rationale": "a stock image closes the visual gap",
            "prompt": "a neutral supporting visual",
            "orientation": "vertical-private-provider-response",
            "required": False,
            "fallback": "fail",
        }]
        client.response = [deepcopy(response), deepcopy(response)]
        kwargs.update({
            "max_stock_assets_per_clip": 1,
            "stock_policy": "auto",
        })

        with self.assertRaises(EditPlanError) as caught:
            await planner.plan(**kwargs)

        evidence = caught.exception.to_dict()["evidence"]
        serialized = json.dumps(evidence)
        self.assertEqual(
            [item["cause_code"] for item in evidence["attempts"]],
            ["EDIT_PLAN_INVALID", "EDIT_PLAN_INVALID"],
        )
        self.assertIn("orientation", serialized)
        self.assertIn("fallback", serialized)
        self.assertIn('"observed_value": "fail"', serialized)
        self.assertNotIn("private-provider-response", serialized)
        self.assertNotIn("neutral supporting visual", serialized)

    async def test_shadow_mode_marks_schema_fallback_as_degraded(self):
        planner, client, kwargs = planner_fixture(
            "screen", "Keep the validated screen evidence visible."
        )
        invalid_response = {
            "format": {"aspect_ratio": "9:16"},
            "clips": [{"segments": [{"operation": "smart crop"}]}],
        }
        client.response = [deepcopy(invalid_response), deepcopy(invalid_response)]

        plan = await planner.plan(**kwargs, allow_degraded_fallback=True)

        self.assertTrue(plan.degraded)
        self.assertEqual(
            plan.degradation_reason,
            "schema_repair_exhausted_shadow_fallback",
        )
        self.assertEqual(
            plan.clips[0].segments[0].layout.focal_target.region_id,
            "region-1",
        )

    async def test_removes_source_windows_from_text_and_image_overlays(self):
        planner, client, kwargs = planner_fixture(
            "speaker", "Emphasize the visible explanation."
        )
        client.response["requested_capabilities"].append("text_emphasis")
        client.response["clips"][0]["segments"][0]["overlays"] = [{
            "id": "text-overlay",
            "kind": "text",
            "timeline_window": {"start_ms": 1000, "end_ms": 3000},
            "source_window": {"start_ms": 1000, "end_ms": 3000},
            "text": "Key point",
            "asset_id": "",
            "position": "top",
        }]

        plan = await planner.plan(**kwargs)

        overlay = plan.clips[0].segments[0].overlays[0]
        self.assertIsNone(overlay.source_window)
        self.assertEqual(len(client.calls), 1)

    async def test_moves_protected_bottom_overlays_out_of_subtitle_zone(self):
        planner, client, kwargs = planner_fixture(
            "speaker", "Keep the emphasis clear of subtitles."
        )
        client.response["requested_capabilities"].append("text_emphasis")
        client.response["clips"][0]["segments"][0]["overlays"] = [{
            "id": "text-overlay",
            "kind": "text",
            "timeline_window": {"start_ms": 1000, "end_ms": 3000},
            "text": "Key point",
            "asset_id": "",
            "protect_subtitles": True,
            "position": "bottom_right",
        }]

        plan = await planner.plan(**kwargs)

        self.assertEqual(
            plan.clips[0].segments[0].overlays[0].position,
            "top_right",
        )

    async def test_plans_multiple_selected_clips_in_bounded_calls(self):
        planner, client, kwargs = planner_fixture(
            "speaker", "Keep each selected moment visually focused."
        )
        first_response = deepcopy(client.response)
        second_response = deepcopy(client.response)
        second_response["clips"][0].update({
            "clip_index": 2,
            "source_window": {"start_ms": 10_000, "end_ms": 30_000},
            "output_name": "short-02.mp4",
        })
        second_segment = second_response["clips"][0]["segments"][0]
        second_segment.update({
            "id": "segment-2",
            "source_window": {"start_ms": 10_000, "end_ms": 30_000},
            "evidence_ids": [],
        })
        second_segment["layout"]["focal_target"] = {"semantic_role": "speaker"}
        second_clip = ShortCandidate(
            10_000, 30_000, "Second", "Hook", "Reason", 0.8
        )
        shorts_plan = ShortsPlan(
            clips=[kwargs["shorts_plan"].clips[0], second_clip],
            rejected=[],
        )
        kwargs["shorts_plan"] = shorts_plan
        kwargs["shorts_plan_artifact"] = build_shorts_plan_artifact(
            shorts_plan,
            transcript_segments=kwargs["transcript_segments"],
            scene_report=kwargs["scene_report"],
            visual_understanding=kwargs["visual_understanding"],
        )
        client.response = [first_response, second_response]

        plan = await planner.plan(**kwargs)

        self.assertEqual([clip.clip_index for clip in plan.clips], [1, 2])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(
            [json.loads(call["user_prompt"])["clip_task"]["clip_index"] for call in client.calls],
            [1, 2],
        )
        self.assertTrue(all(
            len(json.loads(call["user_prompt"])["clips"]) == 1
            for call in client.calls
        ))

    async def test_stock_planning_requires_an_explicit_pexels_capability(self):
        planner, client, kwargs = planner_fixture("speaker", "Use a neutral teamwork cutaway.")
        client.response["requested_capabilities"].append("image_overlay")
        client.response["clips"][0]["segments"][0]["overlays"] = [{
            "id": "stock-overlay",
            "kind": "image",
            "timeline_window": {"start_ms": 1000, "end_ms": 3000},
            "asset_id": "stock-1",
            "position": "top_right",
        }]
        client.response["clips"][0]["asset_requests"] = [{
            "id": "stock-1",
            "kind": "stock_image",
            "provider": "pexels",
            "timeline_window": {"start_ms": 1000, "end_ms": 3000},
            "visual_gap": "the source has no neutral teamwork visual",
            "purpose": "support the spoken example",
            "rationale": "a short cutaway closes the visible gap",
            "prompt": "remote teamwork planning",
        }]
        kwargs.update({
            "max_generated_assets_per_clip": 0,
            "max_stock_assets_per_clip": 1,
            "stock_policy": "auto",
        })

        plan = await planner.plan(**kwargs)
        payload = json.loads(client.call["user_prompt"])
        self.assertEqual(plan.clips[0].asset_requests[0].provider, "pexels")
        self.assertEqual(payload["asset_providers"]["generated_image"], [])
        self.assertEqual(payload["asset_providers"]["stock_video"], ["pexels"])

        planner, client, blocked_kwargs = planner_fixture(
            "speaker", "Use a neutral teamwork cutaway."
        )
        client.response["requested_capabilities"].append("image_overlay")
        client.response["clips"][0]["segments"][0]["overlays"] = [{
            "id": "stock-overlay",
            "kind": "image",
            "timeline_window": {"start_ms": 1000, "end_ms": 3000},
            "asset_id": "stock-1",
            "position": "top_right",
        }]
        client.response["clips"][0]["asset_requests"] = [{
            "id": "stock-1",
            "kind": "stock_image",
            "provider": "pexels",
            "timeline_window": {"start_ms": 1000, "end_ms": 3000},
            "visual_gap": "the source has no neutral teamwork visual",
            "purpose": "support the spoken example",
            "rationale": "a short cutaway closes the visible gap",
            "prompt": "remote teamwork planning",
        }]
        blocked_kwargs.update({
            "max_generated_assets_per_clip": 0,
            "max_stock_assets_per_clip": 0,
            "stock_policy": "off",
        })
        with self.assertRaises(EditPlanError) as caught:
            await planner.plan(**blocked_kwargs)
        self.assertEqual(caught.exception.code, "EDIT_PLAN_REPAIR_EXHAUSTED")
        self.assertEqual(len(client.calls), 2)

    async def test_rejects_clip_expansion_after_one_repair_and_derives_capabilities(self):
        planner, client, kwargs = planner_fixture("speaker", "Keep the speaker visible.")
        client.response["clips"][0]["source_window"]["end_ms"] = 21_000
        client.response["clips"][0]["segments"][0]["source_window"]["end_ms"] = 21_000
        client.response["clips"][0]["segments"][0]["timeline_window"]["end_ms"] = 21_000
        with self.assertRaises(EditPlanError) as caught:
            await planner.plan(**kwargs)
        self.assertEqual(caught.exception.code, "EDIT_PLAN_REPAIR_EXHAUSTED")
        self.assertEqual(len(client.calls), 2)

        planner, client, kwargs = planner_fixture("speaker", "Keep the speaker visible.")
        client.response["requested_capabilities"].remove("hard_cut")
        plan = await planner.plan(**kwargs)
        self.assertIn("hard_cut", plan.requested_capabilities)

    async def test_required_prompt_assets_must_be_requested_and_executed(self):
        prompt = (
            "Use exactly one generated editorial image for approximately 2-4 seconds. "
            "Use exactly one vertical Pexels video for approximately 3-5 seconds."
        )
        planner, client, kwargs = planner_fixture("speaker", prompt)
        response = client.response
        response["requested_capabilities"].extend(["image_overlay", "source_cutaway"])
        response["degradation_reason"] = None
        response["clips"][0]["segments"][0]["layout"]["focal_target"] = {
            "region_id": None,
            "track_id": None,
            "semantic_role": "speaker",
        }
        response["clips"][0]["segments"][0]["transition_in"] = {
            "kind": "hard_cut",
            "duration_ms": 0,
        }
        response["clips"][0]["segments"][0]["overlays"] = [
            {
                "id": "generated-overlay",
                "kind": "image",
                "timeline_window": {"start_ms": 1000, "end_ms": 4000},
                "asset_id": "generated-1",
                "position": "upper_right",
            },
            {
                "id": "pexels-overlay",
                "kind": "image_overlay",
                "timeline_window": {"start_ms": 5000, "end_ms": 9000},
                "asset_id": "pexels-1",
                "position": "top_left",
            },
            {
                "id": "source-overlay",
                "kind": "source",
                "timeline_window": {"start_ms": 10_000, "end_ms": 12_000},
                "text": None,
                "asset_id": None,
                "position": "top",
            },
            {
                "id": "duplicate-subtitle-overlay",
                "kind": "subtitles",
                "timeline_window": {"start_ms": 0, "end_ms": 20_000},
                "position": "bottom",
            },
        ]
        response["clips"][0]["asset_requests"] = [
            {
                "id": "generated-1",
                "kind": "generated_image",
                "provider": "9router",
                "timeline_window": {"start_ms": 1000, "end_ms": 4000},
                "visual_gap": "the source cannot show the requested concept",
                "purpose": "show the abstract process",
                "rationale": "an editorial still closes the conceptual gap",
                "prompt": "a restrained editorial process diagram",
                "orientation": "horizontal",
                "fallback": "use_source",
            },
            {
                "id": "pexels-1",
                "kind": "stock_video",
                "provider": "pexels",
                "timeline_window": {"start_ms": 5000, "end_ms": 9000},
                "visual_gap": "the source cannot show the mentioned real-world action",
                "purpose": "show the real-world action",
                "rationale": "a short vertical cutaway closes the visible gap",
                "prompt": "vertical real-world action",
                "orientation": "vertical",
                "fallback": "fail",
            },
        ]
        response["clips"][0]["intent_decisions"] = [
            {
                "intent_id": "prompt-generated-image",
                "decision": "execute",
                "asset_ids": ["generated-1"],
                "operation_ids": ["generated-overlay"],
                "omission_reason": None,
            },
            {
                "intent_id": "prompt-pexels-video",
                "decision": "execute",
                "asset_ids": ["pexels-1"],
                "operation_ids": ["pexels-overlay"],
                "omission_reason": None,
            },
        ]
        intent = build_creative_intent(
            prompt,
            {
                "settings_version": 1,
                "asset_policy": "auto",
                "max_generated_assets_per_clip": 1,
                "stock_policy": "auto",
                "max_stock_assets_per_clip": 1,
            },
            selected_clip_count=1,
        )
        kwargs.update({
            "creative_intent": intent,
            "max_generated_assets_per_clip": 1,
            "max_stock_assets_per_clip": 1,
            "stock_policy": "auto",
        })

        plan = await planner.plan(**kwargs)

        self.assertEqual(
            [item.kind for item in plan.clips[0].asset_requests],
            ["generated_image", "stock_video"],
        )
        self.assertEqual(
            [item.orientation for item in plan.clips[0].asset_requests],
            ["landscape", "portrait"],
        )
        self.assertEqual(
            [item.fallback for item in plan.clips[0].asset_requests],
            ["source", "omit"],
        )
        self.assertEqual(plan.clips[0].segments[0].transition_in.kind, "cut")
        self.assertEqual(plan.clips[0].segments[0].overlays[0].position, "top_right")
        self.assertEqual(
            plan.clips[0].segments[0].overlays[2].source_window,
            TimeWindow(start_ms=10_000, end_ms=12_000),
        )
        self.assertEqual(
            [item.kind for item in plan.clips[0].segments[0].overlays],
            ["image", "image", "source"],
        )
        self.assertEqual(plan.degradation_reason, "")
        self.assertTrue(
            all(not item.omission_reason for item in plan.clips[0].intent_decisions)
        )
        self.assertEqual(len(plan.clips[0].intent_decisions), 2)

    async def test_required_prompt_assets_cannot_complete_source_only(self):
        prompt = (
            "Use exactly one generated editorial image and exactly one Pexels video."
        )
        planner, client, kwargs = planner_fixture("speaker", prompt)
        client.response = [deepcopy(client.response), deepcopy(client.response)]
        kwargs.update({
            "creative_intent": build_creative_intent(
                prompt,
                {
                    "asset_policy": "auto",
                    "max_generated_assets_per_clip": 1,
                    "stock_policy": "auto",
                    "max_stock_assets_per_clip": 1,
                },
                selected_clip_count=1,
            ),
            "max_generated_assets_per_clip": 1,
            "max_stock_assets_per_clip": 1,
            "stock_policy": "auto",
        })

        with self.assertRaises(EditPlanError) as caught:
            await planner.plan(**kwargs)

        self.assertEqual(caught.exception.code, "EDIT_PLAN_REPAIR_EXHAUSTED")

    async def test_required_asset_visible_duration_must_match_prompt_contract(self):
        prompt = "Use exactly one generated editorial image for approximately 2-4 seconds."
        planner, client, kwargs = planner_fixture("speaker", prompt)
        response = client.response
        response["requested_capabilities"].append("image_overlay")
        response["clips"][0]["segments"][0]["overlays"] = [{
            "id": "generated-overlay",
            "kind": "image",
            "timeline_window": {"start_ms": 1000, "end_ms": 1500},
            "asset_id": "generated-1",
            "position": "top_right",
        }]
        response["clips"][0]["asset_requests"] = [{
            "id": "generated-1",
            "kind": "generated_image",
            "provider": "9router",
            "timeline_window": {"start_ms": 1000, "end_ms": 4000},
            "visual_gap": "the source cannot show the requested concept",
            "purpose": "show the abstract process",
            "rationale": "an editorial still closes the conceptual gap",
            "prompt": "a restrained editorial process diagram",
        }]
        response["clips"][0]["intent_decisions"] = [{
            "intent_id": "prompt-generated-image",
            "decision": "execute",
            "asset_ids": ["generated-1"],
            "operation_ids": ["generated-overlay"],
        }]
        client.response = [deepcopy(response), deepcopy(response)]
        kwargs.update({
            "creative_intent": build_creative_intent(
                prompt,
                {
                    "asset_policy": "auto",
                    "max_generated_assets_per_clip": 1,
                    "stock_policy": "off",
                },
                selected_clip_count=1,
            ),
            "max_generated_assets_per_clip": 1,
        })

        with self.assertRaises(EditPlanError) as caught:
            await planner.plan(**kwargs)

        self.assertEqual(caught.exception.code, "EDIT_PLAN_REPAIR_EXHAUSTED")
        attempts = caught.exception.evidence["attempts"]
        self.assertEqual(
            attempts[0]["intent_conformance"]["constraint_code"],
            "asset_visible_duration_outside_contract",
        )


if __name__ == "__main__":
    unittest.main()
