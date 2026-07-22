from __future__ import annotations

from collections import Counter
import unittest

from pydantic import ValidationError

from open_storyline.mvp.defects import DEFECT_REGISTRY, RepairStrategy
from open_storyline.mvp.fallbacks import FallbackEntry
from open_storyline.mvp.promotion import build_render_promotion_report
from open_storyline.mvp.repair import (
    RepairBudget,
    RepairMode,
    RepairStage,
    build_repair_batch,
    build_repair_report,
    compute_repair_resolution,
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


class StrictRepairProvider:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def complete(self, schema_name: str, payload: dict) -> dict:
        self.calls[schema_name] += 1
        return structured_output(schema_name).validate(payload)


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
