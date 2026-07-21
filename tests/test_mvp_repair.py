from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import unittest

from open_storyline.mvp.defects import (
    DEFECT_REGISTRY,
    RepairStrategy,
    defect_definition,
)
from open_storyline.mvp.edit_plan import EditPlan, build_shadow_edit_plan
from open_storyline.mvp.observability import compact_repair_observability
from open_storyline.mvp.prompts import (
    REPAIR_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT_VERSION,
)
from open_storyline.mvp.repair import (
    RepairBudget,
    RepairContractError,
    RepairEvidence,
    RepairFinding,
    RepairMode,
    RepairStage,
    TranscriptExcerpt,
    build_repair_batch,
    evaluate_repair_quality_floor,
    predict_plan_findings,
    repair_disposition,
    resolve_repair_mode,
)
from open_storyline.mvp.shorts import ShortCandidate


REPAIRABLE_STRATEGIES = {
    RepairStrategy.LLM_VISUAL_REPAIR,
    RepairStrategy.LLM_PLAN_REPAIR,
    RepairStrategy.CONDITIONAL_LLM_OR_FALLBACK,
}


def finding_for(code: str, *, objective: bool = True) -> RepairFinding:
    definition = defect_definition(code)
    evidence = tuple(
        RepairEvidence(
            evidence_type=evidence_type,
            clip_index=1,
            values={
                "code": code,
                "clip_index": 1,
                "observed": "invalid",
            },
        )
        for evidence_type in definition.evidence_requirements
    )
    return RepairFinding(
        code=code,
        objective=objective,
        evidence=evidence,
        clip_index=1,
    )


def build_batch(finding: RepairFinding, *, mode: RepairMode = RepairMode.REPORT):
    definition = defect_definition(finding.code)
    stage = (
        RepairStage.VISUAL_UNDERSTANDING
        if definition.repair_strategy is RepairStrategy.LLM_VISUAL_REPAIR
        else RepairStage.PLAN_REPAIR
    )
    return build_repair_batch(
        stage=stage,
        mode=mode,
        findings=(finding,),
        budget=RepairBudget(),
        candidate_clips={1: {
            "source_window": {"start_ms": 0, "end_ms": 8_000},
            "segments": [],
        }},
        available_capabilities=("crop", "hard_cut", "subtitles"),
        catalog_context={
            "catalog_version": "2026.07.1",
            "manifest_sha256": "a" * 64,
            "entries": [],
        },
        immutable_constraints={"preserve_source_windows": True, "max_clips": 8},
        editing_prompt="Keep the visible subject clear.",
    )


def shadow_plan() -> EditPlan:
    return build_shadow_edit_plan(
        (
            ShortCandidate(0, 8_000, "One", "Hook", "Reason", 0.9),
            ShortCandidate(10_000, 18_000, "Two", "Hook", "Reason", 0.8),
        ),
        source_duration_ms=30_000,
    )


class RepairEligibilityTests(unittest.TestCase):
    def test_mode_values_are_strict(self):
        self.assertIs(resolve_repair_mode("off"), RepairMode.OFF)
        self.assertIs(resolve_repair_mode("report"), RepairMode.REPORT)
        self.assertIs(resolve_repair_mode("enforce"), RepairMode.ENFORCE)
        with self.assertRaises(RepairContractError) as caught:
            resolve_repair_mode("automatic")
        self.assertEqual(caught.exception.code, "REPAIR_MODE_INVALID")

    def test_every_registered_llm_strategy_builds_a_bounded_request(self):
        tested = set()
        for code, definition in DEFECT_REGISTRY.items():
            if definition.repair_strategy not in REPAIRABLE_STRATEGIES:
                continue
            with self.subTest(code=code):
                request, dispositions = build_batch(finding_for(code))
                provider = request.to_provider_dict()
                report = request.to_report_dict()
                self.assertEqual(provider["defects"][0]["code"], code)
                self.assertEqual(report["objective_codes"], [code])
                self.assertTrue(dispositions[0].eligible)
                self.assertTrue(dispositions[0].would_call)
                self.assertFalse(dispositions[0].call_allowed)
                tested.add(code)
        self.assertTrue(tested)

    def test_nonrepairable_unknown_technical_provider_and_ffmpega_codes_are_rejected(self):
        for code in (
            "UNKNOWN_PRIVATE_DEFECT",
            "AUDIO_MISSING",
            "NINEROUTER_REQUEST_FAILED",
            "FFMPEGA_PLAN_INVALID",
            "EFFECT_PLANNING_FAILED",
        ):
            with self.subTest(code=code):
                disposition = repair_disposition(
                    finding_for(code),
                    stage=RepairStage.PLAN_REPAIR,
                    mode=RepairMode.ENFORCE,
                    budget=RepairBudget(),
                )
                self.assertFalse(disposition.eligible)
                self.assertFalse(disposition.call_allowed)

    def test_off_report_and_enforce_have_distinct_call_semantics(self):
        finding = finding_for("EDIT_PLAN_INVALID")
        dispositions = {
            mode: repair_disposition(
                finding,
                stage=RepairStage.PLAN_REPAIR,
                mode=mode,
                budget=RepairBudget(),
            )
            for mode in RepairMode
        }
        self.assertFalse(dispositions[RepairMode.OFF].eligible)
        self.assertTrue(dispositions[RepairMode.REPORT].would_call)
        self.assertFalse(dispositions[RepairMode.REPORT].call_allowed)
        self.assertTrue(dispositions[RepairMode.ENFORCE].call_allowed)

    def test_stage_budgets_are_independent_and_single_use(self):
        visual = finding_for("VISUAL_RESPONSE_INVALID")
        plan = finding_for("EDIT_PLAN_INVALID")
        self.assertTrue(repair_disposition(
            visual,
            stage=RepairStage.VISUAL_UNDERSTANDING,
            mode=RepairMode.ENFORCE,
            budget=RepairBudget(plan_attempts_used=1),
        ).eligible)
        self.assertTrue(repair_disposition(
            plan,
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            budget=RepairBudget(visual_attempts_used=1),
        ).eligible)
        self.assertFalse(repair_disposition(
            visual,
            stage=RepairStage.VISUAL_UNDERSTANDING,
            mode=RepairMode.ENFORCE,
            budget=RepairBudget(visual_attempts_used=1),
        ).eligible)
        self.assertFalse(repair_disposition(
            plan,
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            budget=RepairBudget(plan_attempts_used=1),
        ).eligible)

    def test_missing_evidence_and_advisory_only_findings_make_no_call(self):
        missing = RepairFinding(
            code="EDIT_PLAN_INVALID",
            objective=True,
            evidence=(),
            clip_index=1,
        )
        disposition = repair_disposition(
            missing,
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            budget=RepairBudget(),
        )
        self.assertEqual(disposition.reason, "required_evidence_missing")
        advisory = finding_for("PREDICTIVE_INACTIVE_HOOK_RISK", objective=False)
        with self.assertRaises(RepairContractError) as caught:
            build_batch(advisory, mode=RepairMode.ENFORCE)
        self.assertEqual(caught.exception.code, "REPAIR_NOT_ELIGIBLE")


class RepairContractPrivacyTests(unittest.TestCase):
    def test_transient_prompt_transcript_and_candidate_text_never_enter_reports(self):
        private_prompt = "private editing instruction marker"
        private_transcript = "private transcript marker"
        private_candidate = "private candidate marker"
        request, _ = build_repair_batch(
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.REPORT,
            findings=(finding_for("EDIT_PLAN_INVALID"),),
            budget=RepairBudget(),
            candidate_clips={1: {
                "source_window": {"start_ms": 0, "end_ms": 8_000},
                "segments": [{"id": "segment-1", "reason": private_candidate}],
            }},
            available_capabilities=("crop", "subtitles"),
            catalog_context={},
            immutable_constraints={"preserve_source_windows": True},
            editing_prompt=private_prompt,
            transcript_excerpts=(TranscriptExcerpt(1, 0, 1_000, private_transcript),),
        )
        provider_text = json.dumps(request.to_provider_dict())
        report = request.to_report_dict()
        report_text = json.dumps(report)
        self.assertIn(private_prompt, provider_text)
        self.assertIn(private_transcript, provider_text)
        self.assertIn(private_candidate, provider_text)
        self.assertNotIn(private_prompt, report_text)
        self.assertNotIn(private_transcript, report_text)
        self.assertNotIn(private_candidate, report_text)
        compacted = compact_repair_observability({
            **report,
            "editing_prompt": private_prompt,
            "transcript_excerpts": [private_transcript],
            "provider_body": private_candidate,
        })
        self.assertNotIn("private", json.dumps(compacted))

    def test_blocked_context_fields_fail_closed(self):
        with self.assertRaises(RepairContractError) as caught:
            build_repair_batch(
                stage=RepairStage.PLAN_REPAIR,
                mode=RepairMode.REPORT,
                findings=(finding_for("EDIT_PLAN_INVALID"),),
                budget=RepairBudget(),
                candidate_clips={1: {"provider_response": "private"}},
                available_capabilities=("crop",),
                catalog_context={},
                immutable_constraints={"preserve_source_windows": True},
                editing_prompt="Repair the objective plan defect.",
            )
        self.assertEqual(caught.exception.code, "REPAIR_CONTEXT_PRIVATE")

    def test_repair_prompt_version_and_hash_are_stable(self):
        self.assertEqual(REPAIR_SYSTEM_PROMPT_VERSION, "mvp-defect-repair.v1")
        self.assertEqual(
            sha256(REPAIR_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
            "f4b2f664902cd53bfad30314e99bad7910437322594cdc292e9a6d0b81ba6f95",
        )


class RepairQualityFloorTests(unittest.TestCase):
    def test_unchanged_repair_resolves_original_code(self):
        original = shadow_plan()
        result = evaluate_repair_quality_floor(
            original,
            original,
            original_codes=("EDIT_PLAN_INVALID",),
            repaired_codes=(),
            available_capabilities=original.requested_capabilities,
            affected_clip_indexes=(1,),
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.resolution.resolved_codes, ("EDIT_PLAN_INVALID",))

    def test_quality_floor_rejects_collapsed_or_semantically_worse_candidates(self):
        original = shadow_plan()
        base = original.to_dict()
        cases = {}

        removed_clip = deepcopy(base)
        removed_clip["clips"] = removed_clip["clips"][:1]
        cases["REPAIR_OUTPUT_COUNT_CHANGED"] = EditPlan.model_validate(removed_clip)

        moved_source = deepcopy(base)
        moved_source["clips"][0]["source_window"] = {"start_ms": 1_000, "end_ms": 9_000}
        moved_source["clips"][0]["segments"][0]["source_window"] = {
            "start_ms": 1_000,
            "end_ms": 9_000,
        }
        cases["REPAIR_SOURCE_WINDOW_CHANGED"] = EditPlan.model_validate(moved_source)

        removed_subtitles = deepcopy(base)
        removed_subtitles["requested_capabilities"].remove("subtitles")
        cases["REPAIR_SUBTITLE_REQUIREMENT_LOST"] = EditPlan.model_validate(
            removed_subtitles
        )

        changed_operation = deepcopy(base)
        changed_operation["clips"][0]["segments"][0]["id"] = "replacement-segment"
        cases["REPAIR_UNAFFECTED_OPERATION_REMOVED"] = EditPlan.model_validate(
            changed_operation
        )

        catalog_original = deepcopy(base)
        catalog_original["clips"][0]["catalog_selection"] = {
            "style_profile_id": "style.clean",
            "caption_treatment_id": "caption.clean",
            "color_treatment_id": "color.clean",
            "recipe_ids": ["recipe.focus"],
        }
        original_with_catalog = EditPlan.model_validate(catalog_original)
        catalog_lost = deepcopy(catalog_original)
        catalog_lost["clips"][0]["catalog_selection"]["caption_treatment_id"] = ""
        catalog_result = evaluate_repair_quality_floor(
            original_with_catalog,
            EditPlan.model_validate(catalog_lost),
            original_codes=("EDIT_PLAN_CATALOG_STYLE_MISMATCH",),
            repaired_codes=(),
            available_capabilities=original.requested_capabilities,
            affected_clip_indexes=(1,),
        )
        self.assertIn("REPAIR_CATALOG_STYLE_LOST", catalog_result.violation_codes)

        unsupported = deepcopy(base)
        unsupported["requested_capabilities"].append("focus_zoom")
        cases["REPAIR_CAPABILITY_UNSUPPORTED"] = EditPlan.model_validate(unsupported)

        for expected, repaired in cases.items():
            with self.subTest(expected=expected):
                result = evaluate_repair_quality_floor(
                    original,
                    repaired,
                    original_codes=("EDIT_PLAN_INVALID",),
                    repaired_codes=(),
                    available_capabilities=original.requested_capabilities,
                    affected_clip_indexes=(1,),
                )
                self.assertFalse(result.accepted)
                self.assertIn(expected, result.violation_codes)

    def test_new_validator_codes_reject_schema_valid_repair(self):
        original = shadow_plan()
        result = evaluate_repair_quality_floor(
            original,
            original,
            original_codes=("EDIT_PLAN_INVALID",),
            repaired_codes=("EDIT_PLAN_EVIDENCE_UNKNOWN",),
            available_capabilities=original.requested_capabilities,
            affected_clip_indexes=(1,),
        )
        self.assertFalse(result.accepted)
        self.assertIn("REPAIR_NEW_DEFECT_INTRODUCED", result.violation_codes)
        self.assertEqual(
            result.resolution.introduced_codes,
            ("EDIT_PLAN_EVIDENCE_UNKNOWN",),
        )


class PredictiveRepairPolicyTests(unittest.TestCase):
    def test_every_predictive_code_declares_complete_policy_metadata(self):
        predictive = {
            code: definition
            for code, definition in DEFECT_REGISTRY.items()
            if code.startswith("PREDICTIVE_")
        }
        self.assertTrue(predictive)
        for code, definition in predictive.items():
            with self.subTest(code=code):
                self.assertTrue(definition.detector)
                self.assertTrue(definition.threshold)
                self.assertTrue(definition.evidence_requirements)
                self.assertIn(
                    definition.repair_strategy,
                    {RepairStrategy.LLM_PLAN_REPAIR, RepairStrategy.ADVISORY},
                )
                if definition.trigger_eligible:
                    self.assertIsNotNone(definition.safe_fallback_code)
                else:
                    self.assertIsNone(definition.safe_fallback_code)

    def test_objective_and_advisory_predictions_have_distinct_trigger_policy(self):
        plan = {
            "clips": [{
                "clip_index": 1,
                "segments": [{
                    "id": "segment-1",
                    "timeline_window": {"start_ms": 0, "end_ms": 12_000},
                    "layout": {"mode": "fit"},
                    "overlays": [
                        {
                            "id": "overlay-1",
                            "opacity": 0.1,
                            "width_ratio": 0.9,
                            "margin_ratio": 0.1,
                            "timeline_window": {"start_ms": 0, "end_ms": 13_000},
                            "protect_subtitles": True,
                            "position": "bottom",
                        },
                        {
                            "id": "overlay-1",
                            "timeline_window": {"start_ms": 0, "end_ms": 1_000},
                        },
                    ],
                }],
            }],
        }
        findings = predict_plan_findings(plan, source_aspect_ratios={1: 16 / 9})
        by_code = {item.code: item for item in findings}
        objective_codes = {
            "PREDICTIVE_ACTIVE_PICTURE_RISK",
            "PREDICTIVE_OVERLAY_DUPLICATE",
            "PREDICTIVE_OVERLAY_GEOMETRY_INVALID",
            "PREDICTIVE_OVERLAY_OPACITY_LOW",
            "PREDICTIVE_OVERLAY_TIMING_INVALID",
            "PREDICTIVE_SUBTITLE_SAFE_ZONE_CONFLICT",
        }
        self.assertTrue(objective_codes <= by_code.keys())
        self.assertIn("PREDICTIVE_RHYTHM_RISK", by_code)
        for code in objective_codes:
            disposition = repair_disposition(
                by_code[code].to_repair_finding(),
                stage=RepairStage.PLAN_REPAIR,
                mode=RepairMode.ENFORCE,
                budget=RepairBudget(),
            )
            self.assertTrue(disposition.call_allowed, code)
        advisory = repair_disposition(
            by_code["PREDICTIVE_RHYTHM_RISK"].to_repair_finding(),
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            budget=RepairBudget(),
        )
        self.assertFalse(advisory.call_allowed)


if __name__ == "__main__":
    unittest.main()
