from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from open_storyline.mvp.defects import DEFECT_REGISTRY, RepairStrategy
from open_storyline.mvp.edit_plan import (
    AssetRequest,
    ClipEditPlan,
    EditPlan,
    EditSegment,
    FocalTarget,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
)
from open_storyline.mvp.fallbacks import (
    FallbackConfigurationError,
    FallbackDirective,
    FallbackEntry,
    compile_baseline_plan,
)
from open_storyline.mvp.promotion import build_render_promotion_report
from open_storyline.mvp.repair import (
    RepairBudget,
    PlanRepairRound,
    PlanRepairState,
    RepairMode,
    RepairStage,
    build_repair_batch,
    build_repair_report,
    compute_repair_resolution,
    authoritative_plan_fingerprint,
    evaluate_repair_quality_floor,
    make_repair_finding,
    repair_disposition,
)
from open_storyline.mvp.structured_outputs import (
    EDIT_PLAN_REPAIR_SCHEMA,
    VISUAL_UNDERSTANDING_SCHEMA,
    structured_output,
)


LLM_STRATEGIES = frozenset({
    RepairStrategy.LLM_VISUAL_REPAIR,
    RepairStrategy.LLM_PLAN_REPAIR,
    RepairStrategy.CONDITIONAL_LLM_OR_FALLBACK,
})

FIXTURE = Path(__file__).parent / "fixtures" / "mvp_agentic" / "crop-geometry-overflow.json"
RENDER_REVIEW_FIXTURE = Path(__file__).parent / "fixtures" / "mvp_agentic" / "render-review-eval.json"


def stage_for(strategy: RepairStrategy) -> RepairStage:
    return (
        RepairStage.VISUAL_UNDERSTANDING
        if strategy is RepairStrategy.LLM_VISUAL_REPAIR
        else RepairStage.PLAN_REPAIR
    )


def finding_for(code: str, *, objective: bool = True):
    return make_repair_finding(
        code,
        clip_index=1,
        objective=objective,
        values={"observed": "synthetic_invalid", "count": 1},
        source="repair_eval",
    )


def valid_plan_response() -> dict:
    return {
        "requested_capabilities": ["crop", "hard_cut", "subtitles"],
        "clips": [{
            "clip_index": 1,
            "title": "Synthetic repair",
            "source_window": {"start_ms": 0, "end_ms": 8_000},
            "output_name": "short-01.mp4",
            "segments": [{
                "id": "segment-1",
                "source_window": {"start_ms": 0, "end_ms": 8_000},
                "timeline_window": {"start_ms": 0, "end_ms": 8_000},
                "layout": {
                    "mode": "fit",
                    "focal_target": None,
                    "fallback": "fit",
                    "allow_full_frame_fallback": True,
                    "safe_margin_ratio": 0.08,
                    "max_zoom": 1.0,
                },
                "transition_in": {
                    "kind": "cut",
                    "duration_ms": 0,
                    "catalog_id": None,
                },
                "overlays": [],
                "reason": "Preserve the bounded synthetic source window.",
                "evidence_ids": [],
            }],
            "asset_requests": [],
            "intent_decisions": [],
            "catalog_selection": {
                "style_profile_id": None,
                "caption_treatment_id": None,
                "color_treatment_id": None,
                "recipe_ids": [],
            },
        }],
    }


def incident_plan() -> EditPlan:
    window = TimeWindow(start_ms=0, end_ms=8_000)
    segment = EditSegment(
        id="speaker-union",
        source_window=window,
        timeline_window=window,
        layout=LayoutSpec(
            mode="crop",
            focal_target=FocalTarget(semantic_role="speaker"),
            fallback="crop",
            allow_full_frame_fallback=False,
        ),
        overlays=(OverlaySpec(
            id="synthetic-diagram-1",
            kind="image",
            timeline_window=TimeWindow(start_ms=2_000, end_ms=4_000),
            asset_id="synthetic-diagram-1",
            position="top_right",
        ),),
        reason="Keep both synthetic speakers visible.",
    )
    return EditPlan(
        planner_version="synthetic-recovery.v1",
        source_duration_ms=8_000,
        requested_capabilities=("crop", "fit", "image_overlay", "hard_cut", "subtitles"),
        clips=(ClipEditPlan(
            clip_index=1,
            source_window=window,
            output_name="synthetic-short-01.mp4",
            segments=(segment,),
            asset_requests=(AssetRequest(
                id="synthetic-diagram-1",
                kind="generated_image",
                provider="9router",
                timeline_window=TimeWindow(start_ms=2_000, end_ms=4_000),
                visual_gap="synthetic visual gap",
                purpose="synthetic support",
                rationale="synthetic support",
                prompt="synthetic diagram",
            ),),
        ),),
    )


def composition_finding(code: str = "COMPOSITION_CROP_TARGET_TOO_WIDE"):
    return make_repair_finding(
        code,
        clip_index=1,
        objective=True,
        values={
            "segment_id": "speaker-union",
            "operation_id": "speaker-union",
            "width_overflow_ratio": 1.16,
            "threshold": 1.12,
            "source_width": 1920,
            "source_height": 1080,
            "target_width": 1080,
            "target_height": 1920,
        },
        source="synthetic_geometry",
    )


def provider_attempt(status_code: int = 200) -> dict:
    return {"category": "plan_repair", "number": 1, "status_code": status_code}


class StrictRepairProvider:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def complete(self, schema_name: str, payload: dict) -> dict:
        self.calls[schema_name] += 1
        return structured_output(schema_name).validate(payload)


class IncidentRecoveryEvalTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.original = incident_plan()
        self.finding = composition_finding()
        self.fingerprint = authoritative_plan_fingerprint(self.original)

    def test_render_review_fixture_is_private_and_covers_subjective_dimensions(self):
        fixture = json.loads(RENDER_REVIEW_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["version"], "render_review_eval.v1")
        self.assertFalse(fixture["private_data"])
        self.assertEqual(len(fixture["dimensions"]), 7)
        self.assertIn("pacing_rhythm", fixture["dimensions"])
        self.assertIn("tie", fixture["allowed_preferences"])
        self.assertFalse(fixture["raw_media_included"])
        self.assertFalse(fixture["raw_provider_bodies_included"])

    def assert_preserved_contract(self, candidate: EditPlan) -> None:
        expected = self.fixture["recovery_contract"]
        original_clip = self.original.clips[0]
        candidate_clip = candidate.clips[0]
        self.assertEqual(len(candidate.clips), 1)
        self.assertEqual(candidate_clip.clip_index, expected["clip_index"])
        self.assertEqual(candidate_clip.source_window, original_clip.source_window)
        self.assertEqual(candidate_clip.source_window.model_dump(), expected["source_window"])
        self.assertEqual(
            candidate_clip.segments[0].source_window,
            original_clip.segments[0].source_window,
        )
        self.assertEqual(
            candidate_clip.segments[0].timeline_window,
            original_clip.segments[0].timeline_window,
        )
        self.assertEqual(
            candidate_clip.segments[0].timeline_window.model_dump(),
            expected["timeline_window"],
        )
        self.assertEqual(candidate_clip.output_name, expected["output_name"])
        self.assertEqual(candidate_clip.asset_requests, original_clip.asset_requests)
        self.assertEqual(
            [asset.id for asset in candidate_clip.asset_requests],
            expected["asset_ids"],
        )
        self.assertEqual(
            candidate_clip.segments[0].overlays,
            original_clip.segments[0].overlays,
        )
        self.assertIn("subtitles", candidate.requested_capabilities)

    def record(
        self,
        state: PlanRepairState,
        *,
        round: PlanRepairRound,
        finding=None,
        status_code: int = 200,
        provider_outcome: str = "ok",
        semantic_valid: bool = True,
        fingerprint: str | None = None,
    ):
        return state.record_round(
            round=round,
            findings=(finding or self.finding,),
            authoritative_plan_fingerprint=fingerprint or self.fingerprint,
            provider_attempts=(provider_attempt(status_code),),
            provider_outcome=provider_outcome,
            schema_valid=status_code == 200,
            semantic_valid=semantic_valid,
        )

    def test_fixture_is_private_free_and_declares_the_complete_recovery_matrix(self):
        fixture_text = FIXTURE.read_text(encoding="utf-8").lower()
        scenarios = self.fixture["recovery_scenarios"]
        self.assertFalse(self.fixture["recovery_contract"]["private_data"])
        self.assertEqual(
            set(scenarios),
            {
                "primary_success",
                "primary_provider_failure",
                "candidate_only_rejection",
                "new_authoritative_contingency_success",
                "contingency_provider_failure",
                "no_safe_baseline",
            },
        )
        for forbidden in ("62d79", "session=", "transcript", "provider_response"):
            self.assertNotIn(forbidden, fixture_text)

    def test_primary_success_uses_one_call_and_preserves_non_layout_contracts(self):
        state = PlanRepairState()
        self.record(state, round=PlanRepairRound.PRIMARY)
        payload = deepcopy(self.original.to_dict())
        payload["clips"][0]["segments"][0]["layout"].update({
            "mode": "fit",
            "focal_target": None,
            "fallback": "fit",
            "allow_full_frame_fallback": True,
            "max_zoom": 1.0,
        })
        candidate = EditPlan.model_validate(payload)
        quality = evaluate_repair_quality_floor(
            self.original,
            candidate,
            original_codes=(self.finding.code,),
            repaired_codes=(),
            available_capabilities=self.original.requested_capabilities,
            affected_clip_indexes=(1,),
            affected_operation_ids=("speaker-union",),
            allowed_mutations_by_operation={
                "speaker-union": (
                    "layout.mode",
                    "layout.focal_target",
                    "layout.fallback",
                    "layout.allow_full_frame_fallback",
                    "layout.max_zoom",
                ),
            },
        )

        self.assertTrue(quality.accepted, quality.violation_codes)
        self.assertEqual(
            [item.value for item in state.rounds],
            self.fixture["recovery_scenarios"]["primary_success"]["plan_calls"],
        )
        self.assert_preserved_contract(candidate)

    def test_primary_provider_failure_authorizes_only_the_matching_local_fallback(self):
        state = PlanRepairState()
        self.record(
            state,
            round=PlanRepairRound.PRIMARY,
            status_code=503,
            provider_outcome="provider_unavailable",
            semantic_valid=False,
        )
        evidence = state.require_fallback_evidence(
            (self.finding,),
            authoritative_plan_fingerprint=self.fingerprint,
        )
        compilation = compile_baseline_plan(
            self.original,
            available_capabilities=self.original.requested_capabilities,
            remaining_defects=(FallbackDirective(
                code=self.finding.code,
                clip_index=1,
                segment_id="speaker-union",
                attempt_evidenced=True,
            ),),
            enforce_attempt_gate=True,
        )
        report = build_repair_report(
            mode=RepairMode.ENFORCE,
            fallback_entries=compilation.entries,
            attempt_evidence=evidence,
        )

        self.assertEqual(len(state.rounds), 1)
        self.assertEqual(report["summary"]["repair_invariant_violation_count"], 0)
        self.assertGreaterEqual(report["summary"]["fallback_after_attempt_count"], 1)
        self.assertTrue(all(item["fallback_authorized"] for item in report["fallbacks"]))
        self.assert_preserved_contract(compilation.plan)

    def test_candidate_only_defect_rejects_the_candidate_without_spending_contingency(self):
        state = PlanRepairState()
        self.record(state, round=PlanRepairRound.PRIMARY, semantic_valid=False)
        quality = evaluate_repair_quality_floor(
            self.original,
            self.original,
            original_codes=(self.finding.code,),
            repaired_codes=("EDIT_PLAN_EVIDENCE_UNKNOWN",),
            available_capabilities=self.original.requested_capabilities,
            affected_clip_indexes=(1,),
            affected_operation_ids=("speaker-union",),
        )

        self.assertFalse(quality.accepted)
        self.assertIn("REPAIR_NEW_DEFECT_INTRODUCED", quality.violation_codes)
        self.assertEqual(len(state.rounds), 1)
        self.assertEqual(
            self.fixture["recovery_scenarios"]["candidate_only_rejection"][
                "candidate_disposition"
            ],
            "rejected",
        )

    def test_new_authoritative_defect_gets_the_only_contingency_attempt(self):
        state = PlanRepairState()
        self.record(state, round=PlanRepairRound.PRIMARY)
        late = composition_finding("PREDICTIVE_OVERLAY_OPACITY_LOW")
        self.record(
            state,
            round=PlanRepairRound.CONTINGENCY,
            finding=late,
            fingerprint=self.fingerprint,
        )

        self.assertEqual(
            [item.value for item in state.rounds],
            self.fixture["recovery_scenarios"][
                "new_authoritative_contingency_success"
            ]["plan_calls"],
        )
        with self.assertRaisesRegex(Exception, "no third plan-repair batch"):
            state.next_round()

    def test_contingency_failure_can_fallback_with_round_attribution(self):
        state = PlanRepairState()
        self.record(state, round=PlanRepairRound.PRIMARY)
        late = composition_finding("PREDICTIVE_OVERLAY_OPACITY_LOW")
        payload = deepcopy(self.original.to_dict())
        payload["clips"][0]["segments"][0]["overlays"][0]["opacity"] = 0.05
        contingency_plan = EditPlan.model_validate(payload)
        contingency_fingerprint = authoritative_plan_fingerprint(contingency_plan)
        self.record(
            state,
            round=PlanRepairRound.CONTINGENCY,
            finding=late,
            status_code=503,
            provider_outcome="provider_unavailable",
            semantic_valid=False,
            fingerprint=contingency_fingerprint,
        )
        evidence = state.require_fallback_evidence(
            (late,),
            authoritative_plan_fingerprint=contingency_fingerprint,
        )
        compilation = compile_baseline_plan(
            contingency_plan,
            available_capabilities=contingency_plan.requested_capabilities,
            remaining_defects=(FallbackDirective(
                code=late.code,
                clip_index=1,
                segment_id="speaker-union",
                attempt_evidenced=True,
            ),),
            enforce_attempt_gate=True,
        )
        report = build_repair_report(
            mode=RepairMode.ENFORCE,
            fallback_entries=compilation.entries,
            attempt_evidence=state.attempts,
        )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].round, PlanRepairRound.CONTINGENCY)
        fallback = next(
            item for item in report["fallbacks"] if item["requested"] == late.code
        )
        self.assertTrue(fallback["fallback_authorized"])
        self.assertEqual(fallback["attempt_round"], "contingency")
        self.assertEqual(compilation.plan.clips[0].segments[0].overlays[0].opacity, 0.15)
        self.assertEqual(
            compilation.plan.clips[0].asset_requests,
            contingency_plan.clips[0].asset_requests,
        )

    def test_fallback_attempt_gate_fails_closed_without_matching_evidence(self):
        with self.assertRaises(FallbackConfigurationError) as caught:
            compile_baseline_plan(
                self.original,
                remaining_defects=(FallbackDirective(
                    code=self.finding.code,
                    clip_index=1,
                    segment_id="speaker-union",
                ),),
                enforce_attempt_gate=True,
            )
        self.assertEqual(caught.exception.code, "REPAIR_ATTEMPT_REQUIRED")


class RepairEvalMatrixTests(unittest.TestCase):
    def build_batch(self, code: str, mode: RepairMode):
        definition = DEFECT_REGISTRY[code]
        return build_repair_batch(
            stage=stage_for(definition.repair_strategy),
            mode=mode,
            findings=(finding_for(code),),
            budget=RepairBudget(),
            candidate_clips={1: {
                "source_window": {"start_ms": 0, "end_ms": 8_000},
                "segments": [],
            }},
            available_capabilities=("crop", "fit", "hard_cut", "subtitles"),
            catalog_context={},
            immutable_constraints={"preserve_source_windows": True},
            editing_prompt="Repair the synthetic objective defect.",
        )

    def test_every_llm_repairable_code_has_strict_success_and_failure_evidence(self):
        provider = StrictRepairProvider()
        tested = set()
        for code, definition in DEFECT_REGISTRY.items():
            if definition.repair_strategy not in LLM_STRATEGIES:
                continue
            with self.subTest(code=code):
                request, dispositions = self.build_batch(code, RepairMode.ENFORCE)
                schema_name = (
                    VISUAL_UNDERSTANDING_SCHEMA
                    if definition.repair_strategy is RepairStrategy.LLM_VISUAL_REPAIR
                    else EDIT_PLAN_REPAIR_SCHEMA
                )
                valid_payload = (
                    {"regions": [], "tracks": [], "scenes": [], "warnings": []}
                    if schema_name == VISUAL_UNDERSTANDING_SCHEMA
                    else valid_plan_response()
                )
                self.assertEqual(provider.complete(schema_name, valid_payload), valid_payload)

                resolved = compute_repair_resolution((code,), ()).to_dict()
                success = build_repair_report(
                    mode=RepairMode.ENFORCE,
                    stage_records=({
                        "stage": request.stage.value,
                        "status": "repaired",
                        "request": request.to_report_dict(),
                        "dispositions": [item.to_dict() for item in dispositions],
                        "resolution": resolved,
                        "quality_floor": {"accepted": True, "violation_codes": []},
                        "attempts": [{
                            "category": request.stage.value,
                            "number": 1,
                            "status_code": 200,
                            "reason": "ok",
                        }],
                        "checkpoint_reused": False,
                    },),
                )
                self.assertIn(code, success["summary"]["resolved_codes"])

                with self.assertRaises(ValidationError):
                    provider.complete(schema_name, {"unexpected": True})
                fallback_entries = ()
                if definition.safe_fallback_code:
                    fallback_entries = (FallbackEntry(
                        code=definition.safe_fallback_code,
                        clip_index=1,
                        segment_id="segment-1",
                        requested=code,
                        executed="safe_baseline",
                        reason="Synthetic failed-repair fallback.",
                    ),)
                failed = build_repair_report(
                    mode=RepairMode.ENFORCE,
                    stage_records=({
                        "stage": request.stage.value,
                        "status": "failed",
                        "request": request.to_report_dict(),
                        "dispositions": [item.to_dict() for item in dispositions],
                        "resolution": compute_repair_resolution((code,), (code,)).to_dict(),
                        "quality_floor": {
                            "accepted": False,
                            "violation_codes": ["REPAIR_NEW_DEFECT_INTRODUCED"],
                        },
                        "attempts": [{
                            "category": request.stage.value,
                            "number": 1,
                            "status_code": 200,
                            "reason": "schema_mismatch",
                        }],
                        "checkpoint_reused": False,
                    },),
                    fallback_entries=fallback_entries,
                )
                self.assertIn(code, failed["summary"]["remaining_codes"])
                if definition.safe_fallback_code:
                    self.assertIn(code, failed["summary"]["fallback_applied_codes"])
                tested.add(code)

        expected = {
            code
            for code, definition in DEFECT_REGISTRY.items()
            if definition.repair_strategy in LLM_STRATEGIES
        }
        self.assertEqual(tested, expected)
        self.assertEqual(sum(provider.calls.values()), len(expected) * 2)

    def test_every_non_llm_code_and_advisory_only_case_makes_zero_semantic_calls(self):
        provider = StrictRepairProvider()
        tested = set()
        for code, definition in DEFECT_REGISTRY.items():
            if definition.repair_strategy in LLM_STRATEGIES:
                continue
            with self.subTest(code=code):
                disposition = repair_disposition(
                    finding_for(
                        code,
                        objective=definition.repair_strategy is not RepairStrategy.ADVISORY,
                    ),
                    stage=stage_for(definition.repair_strategy),
                    mode=RepairMode.ENFORCE,
                    budget=RepairBudget(),
                )
                self.assertFalse(disposition.call_allowed)
                tested.add(code)
        expected = {
            code
            for code, definition in DEFECT_REGISTRY.items()
            if definition.repair_strategy not in LLM_STRATEGIES
        }
        self.assertEqual(tested, expected)
        self.assertEqual(sum(provider.calls.values()), 0)

    def test_advisory_attaches_to_one_objective_request_without_an_extra_call(self):
        objective = finding_for("EDIT_PLAN_INVALID")
        advisory = finding_for("PREDICTIVE_INACTIVE_HOOK_RISK", objective=False)
        request, dispositions = build_repair_batch(
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            findings=(objective, advisory),
            budget=RepairBudget(),
            candidate_clips={1: {"segments": []}},
            available_capabilities=("crop", "fit", "hard_cut", "subtitles"),
            catalog_context={},
            immutable_constraints={"preserve_source_windows": True},
            editing_prompt="Repair the objective defect and retain advisory context.",
        )
        provider = StrictRepairProvider()
        provider.complete(EDIT_PLAN_REPAIR_SCHEMA, valid_plan_response())

        self.assertEqual(request.to_report_dict()["objective_codes"], ["EDIT_PLAN_INVALID"])
        self.assertEqual(
            request.to_report_dict()["advisory_codes"],
            ["PREDICTIVE_INACTIVE_HOOK_RISK"],
        )
        self.assertEqual(sum(item.call_allowed for item in dispositions), 1)
        self.assertEqual(sum(provider.calls.values()), 1)

    def test_modes_and_stage_budgets_bound_semantic_calls(self):
        provider = StrictRepairProvider()
        code = "EDIT_PLAN_INVALID"
        report_request, report_dispositions = self.build_batch(code, RepairMode.REPORT)
        enforce_request, enforce_dispositions = self.build_batch(code, RepairMode.ENFORCE)
        off = repair_disposition(
            finding_for(code),
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.OFF,
            budget=RepairBudget(),
        )

        self.assertFalse(off.eligible)
        self.assertEqual(
            [(item.code, item.eligible, item.would_call, item.fallback_code)
             for item in report_dispositions],
            [(item.code, item.eligible, item.would_call, item.fallback_code)
             for item in enforce_dispositions],
        )
        self.assertFalse(report_request.to_report_dict()["call_allowed"])
        self.assertTrue(enforce_request.to_report_dict()["call_allowed"])
        self.assertEqual(sum(provider.calls.values()), 0)

        for stage, repair_code in (
            (RepairStage.VISUAL_UNDERSTANDING, "VISUAL_RESPONSE_INVALID"),
            (RepairStage.PLAN_REPAIR, "EDIT_PLAN_INVALID"),
        ):
            exhausted = RepairBudget(
                visual_attempts_used=int(stage is RepairStage.VISUAL_UNDERSTANDING),
                plan_attempts_used=(
                    2 if stage is RepairStage.PLAN_REPAIR else 0
                ),
            )
            disposition = repair_disposition(
                finding_for(repair_code),
                stage=stage,
                mode=RepairMode.ENFORCE,
                budget=exhausted,
            )
            self.assertFalse(disposition.call_allowed)
            self.assertEqual(disposition.reason, "semantic_budget_exhausted")

        ffmpega = [code for code in DEFECT_REGISTRY if code.startswith("FFMPEGA_")]
        self.assertTrue(ffmpega)
        for repair_code in ffmpega:
            disposition = repair_disposition(
                finding_for(repair_code),
                stage=RepairStage.PLAN_REPAIR,
                mode=RepairMode.ENFORCE,
                budget=RepairBudget(),
            )
            self.assertFalse(disposition.call_allowed)

    def test_technical_pass_delivery_publishes_only_technically_valid_candidates(self):
        base = {
            "mode": "enforce",
            "policy": "strict",
            "limited_output_enabled": False,
            "delivery": "technical_pass_guaranteed",
            "creative_conformance": {"status": "pass", "findings": []},
            "caption_footprints": [],
        }
        creative = build_render_promotion_report(
            **base,
            frame_quality={
                "status": "blocked",
                "findings": [{
                    "severity": "blocker",
                    "code": "ACTIVE_PICTURE_TOO_SMALL",
                }],
            },
            render_qa={"status": "pass", "findings": []},
        )
        technical = build_render_promotion_report(
            **base,
            frame_quality={"status": "pass", "findings": []},
            render_qa={
                "status": "blocked",
                "findings": [{"severity": "blocker", "code": "AUDIO_MISSING"}],
            },
        )
        mixed = build_render_promotion_report(
            **base,
            frame_quality={
                "status": "blocked",
                "findings": [{
                    "severity": "blocker",
                    "code": "ACTIVE_PICTURE_TOO_SMALL",
                }],
            },
            render_qa={
                "status": "blocked",
                "findings": [{"severity": "blocker", "code": "AUDIO_MISSING"}],
            },
        )

        self.assertEqual(creative["strict_decision"], "block")
        self.assertEqual(creative["delivery_decision"], "publish_with_limitations")
        self.assertTrue(creative["download_available"])
        for report in (technical, mixed):
            self.assertEqual(report["delivery_decision"], "withhold_technical")
            self.assertFalse(report["download_available"])


if __name__ == "__main__":
    unittest.main()
