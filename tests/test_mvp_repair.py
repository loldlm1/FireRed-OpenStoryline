from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import unittest

from open_storyline.mvp.defects import (
    DEFECT_REGISTRY,
    DEFECT_REGISTRY_SHA256,
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
    PlanRepairRound,
    PlanRepairState,
    RepairBudget,
    RepairContractError,
    RepairEvidence,
    RepairFinding,
    RepairMode,
    RepairStage,
    TranscriptExcerpt,
    authoritative_plan_fingerprint,
    bounded_repair_findings,
    build_repair_batch,
    build_repair_report,
    evaluate_repair_quality_floor,
    make_repair_finding,
    predict_plan_findings,
    repair_disposition,
    resolve_repair_mode,
    validate_repair_report,
)
from open_storyline.mvp.structured_outputs import structured_output
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

    def test_creative_intent_mismatch_reaches_the_llm_with_bounded_contract_evidence(self):
        finding = make_repair_finding(
            "EDIT_PLAN_INTENT_MISMATCH",
            clip_index=1,
            objective=True,
            values={
                "observed": "intent_mismatch",
                "constraint_code": "opening_title_invalid",
                "intent_id": "prompt-opening-title",
            },
            source="creative_intent",
        )
        request, dispositions = build_repair_batch(
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            findings=(finding,),
            budget=RepairBudget(),
            candidate_clips={1: {"segments": []}},
            available_capabilities=("crop", "text_emphasis", "subtitles"),
            catalog_context={},
            immutable_constraints={
                "creative_intent": {
                    "version": "creative_intent.v2",
                    "operation_intents": [{
                        "id": "prompt-opening-title",
                        "kind": "opening_title",
                        "count_min": 1,
                        "count_max": 1,
                        "start_max_ms": 3500,
                    }],
                },
            },
            editing_prompt="Add an opening title.",
            authoritative_plan_sha256="a" * 64,
        )

        provider = request.to_provider_dict()
        self.assertTrue(dispositions[0].call_allowed)
        self.assertEqual(
            provider["evidence"][0]["values"]["constraint_code"],
            "opening_title_invalid",
        )
        self.assertEqual(
            provider["immutable_constraints"]["creative_intent"][
                "operation_intents"
            ][0]["start_max_ms"],
            3500,
        )

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

    def test_visual_budget_is_single_use_and_plan_budget_allows_two_rounds(self):
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
        self.assertTrue(repair_disposition(
            plan,
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            budget=RepairBudget(plan_attempts_used=1),
        ).eligible)
        self.assertFalse(repair_disposition(
            plan,
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            budget=RepairBudget(plan_attempts_used=2),
        ).eligible)

    def test_visual_budget_is_bounded_independently_per_clip(self):
        clip_one = make_repair_finding(
            "VISUAL_RESPONSE_INVALID",
            clip_index=1,
            objective=True,
            values={"observed": "invalid", "count": 1},
        )
        clip_two = make_repair_finding(
            "VISUAL_RESPONSE_INVALID",
            clip_index=2,
            objective=True,
            values={"observed": "invalid", "count": 1},
        )
        budget = RepairBudget(
            visual_attempts_used_by_clip={1: 1, 2: 0},
        )

        self.assertFalse(repair_disposition(
            clip_one,
            stage=RepairStage.VISUAL_UNDERSTANDING,
            mode=RepairMode.ENFORCE,
            budget=budget,
        ).call_allowed)
        self.assertTrue(repair_disposition(
            clip_two,
            stage=RepairStage.VISUAL_UNDERSTANDING,
            mode=RepairMode.ENFORCE,
            budget=budget,
        ).call_allowed)

    def test_plan_repair_state_requires_matching_outbound_attempt_before_fallback(self):
        finding = make_repair_finding(
            "COMPOSITION_CROP_TARGET_TOO_WIDE",
            clip_index=1,
            objective=True,
            values={"segment_id": "segment-1", "observed": "overflow"},
        )
        fingerprint = authoritative_plan_fingerprint(shadow_plan())
        state = PlanRepairState()
        state.record_round(
            round=PlanRepairRound.PRIMARY,
            findings=(finding,),
            authoritative_plan_fingerprint=fingerprint,
            provider_attempts=({"number": 1, "reason": "timeout"},),
            provider_outcome="NINEROUTER_REQUEST_FAILED",
            schema_valid=False,
            semantic_valid=False,
        )

        evidence = state.require_fallback_evidence(
            (finding,),
            authoritative_plan_fingerprint=fingerprint,
        )
        self.assertEqual(evidence[0].round, PlanRepairRound.PRIMARY)
        different_segment = make_repair_finding(
            "COMPOSITION_CROP_TARGET_TOO_WIDE",
            clip_index=1,
            objective=True,
            values={"segment_id": "segment-2", "observed": "overflow"},
        )
        with self.assertRaises(RepairContractError) as caught:
            state.require_fallback_evidence(
                (different_segment,),
                authoritative_plan_fingerprint=fingerprint,
            )
        self.assertEqual(caught.exception.code, "REPAIR_ATTEMPT_REQUIRED")

    def test_same_code_on_two_segments_remains_two_defect_instances(self):
        findings = tuple(
            make_repair_finding(
                "COMPOSITION_CROP_TARGET_TOO_WIDE",
                clip_index=1,
                objective=True,
                values={"segment_id": segment_id, "observed": "overflow"},
            )
            for segment_id in ("segment-1", "segment-2")
        )
        selected, overflow = bounded_repair_findings(findings)
        self.assertEqual(len(selected), 2)
        self.assertEqual(overflow, ())
        request, _ = build_repair_batch(
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            findings=selected,
            budget=RepairBudget(),
            candidate_clips={1: {"segments": []}},
            available_capabilities=("crop", "fit", "subtitles"),
            catalog_context={},
            immutable_constraints={"preserve_source_windows": True},
            editing_prompt="Repair both synthetic segment defects.",
            authoritative_plan_sha256="a" * 64,
        )
        self.assertEqual(len(request.to_report_dict()["defect_instance_ids"]), 2)
        self.assertEqual(
            len(set(request.to_report_dict()["defect_instance_ids"])),
            2,
        )

    def test_report_only_does_not_satisfy_fallback_gate_and_third_round_is_rejected(self):
        primary = finding_for("EDIT_PLAN_INVALID")
        contingency = finding_for("EDIT_PLAN_EVIDENCE_UNKNOWN")
        fingerprint = authoritative_plan_fingerprint(shadow_plan())
        state = PlanRepairState()
        state.record_round(
            round=PlanRepairRound.PRIMARY,
            findings=(primary,),
            authoritative_plan_fingerprint=fingerprint,
            provider_attempts=(),
            provider_outcome="report_only",
            schema_valid=False,
            semantic_valid=False,
        )
        with self.assertRaises(RepairContractError) as caught:
            state.require_fallback_evidence(
                (primary,),
                authoritative_plan_fingerprint=fingerprint,
            )
        self.assertEqual(caught.exception.code, "REPAIR_ATTEMPT_REQUIRED")
        state.record_round(
            round=PlanRepairRound.CONTINGENCY,
            findings=(contingency,),
            authoritative_plan_fingerprint=fingerprint,
            provider_attempts=({"number": 1},),
            provider_outcome="ok",
            schema_valid=True,
            semantic_valid=True,
        )
        with self.assertRaises(RepairContractError) as caught:
            state.next_round()
        self.assertEqual(caught.exception.code, "REPAIR_PLAN_CALL_LIMIT_EXCEEDED")

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
    def test_schema_shaped_invalid_candidate_fits_bounded_repair_context(self):
        request, _dispositions = build_repair_batch(
            stage=RepairStage.PLAN_REPAIR,
            mode=RepairMode.ENFORCE,
            findings=(finding_for("EDIT_PLAN_INVALID"),),
            budget=RepairBudget(),
            candidate_clips={1: {
                "segments": [],
                "invalid_candidate": {
                    "clips": [{
                        "segments": [{
                            "overlays": [{
                                "timeline_window": {
                                    "start_ms": 0,
                                    "end_ms": 1_000,
                                },
                            }],
                        }],
                    }],
                },
            }},
            available_capabilities=("crop",),
            catalog_context={},
            immutable_constraints={"preserve_source_windows": True},
            editing_prompt="Repair the objective plan defect.",
        )
        self.assertEqual(request.candidate_clips[0]["clip_index"], 1)

    def test_excessively_nested_candidate_still_fails_closed(self):
        nested = {"value": True}
        for _ in range(13):
            nested = {"nested": nested}
        with self.assertRaises(RepairContractError) as caught:
            build_repair_batch(
                stage=RepairStage.PLAN_REPAIR,
                mode=RepairMode.ENFORCE,
                findings=(finding_for("EDIT_PLAN_INVALID"),),
                budget=RepairBudget(),
                candidate_clips={1: nested},
                available_capabilities=("crop",),
                catalog_context={},
                immutable_constraints={"preserve_source_windows": True},
                editing_prompt="Repair the objective plan defect.",
            )
        self.assertEqual(caught.exception.code, "REPAIR_CONTEXT_INVALID")

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

    def test_repair_report_is_bounded_versioned_and_private(self):
        private_marker = "private prompt transcript provider body /private/video.mp4"
        request, dispositions = build_batch(
            finding_for("EDIT_PLAN_INVALID"),
            mode=RepairMode.ENFORCE,
        )
        request_report = compact_repair_observability({
            **request.to_report_dict(),
            "provider_body": private_marker,
        })
        report = build_repair_report(
            mode=RepairMode.ENFORCE,
            stage_records=({
                "stage": "plan_repair",
                "status": "failed",
                "request": request_report,
                "dispositions": [item.to_dict() for item in dispositions],
                "resolution": {
                    "original_codes": ["EDIT_PLAN_INVALID"],
                    "resolved_codes": [],
                    "remaining_codes": ["EDIT_PLAN_INVALID"],
                    "introduced_codes": [],
                },
                "quality_floor": {"accepted": False, "violation_codes": []},
                "attempts": [{
                    "category": "plan_repair",
                    "number": 1,
                    "status_code": 503,
                    "reason": "provider_unavailable",
                    "duration_ms": 120,
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "reasoning_tokens": 10,
                    "total_tokens": 130,
                    "cost_usd": 0.001,
                }],
                "checkpoint_reused": False,
                "provider_body": private_marker,
            },),
        )

        self.assertEqual(report["registry_sha256"], DEFECT_REGISTRY_SHA256)
        self.assertEqual(
            report["stages"][0]["request"]["response_schema_sha256"],
            structured_output("edit_plan_repair.v1").fingerprint,
        )
        self.assertEqual(len(report["stages"][0]["request"]["evidence_ids"]), 2)
        self.assertEqual(
            report["summary"]["remaining_codes"],
            ["EDIT_PLAN_INVALID"],
        )
        self.assertNotIn(private_marker, json.dumps(report))
        self.assertEqual(validate_repair_report(report), report)

    def test_repair_report_v2_preserves_round_attempt_and_fallback_identity(self):
        primary = make_repair_finding(
            "COMPOSITION_CROP_TARGET_TOO_WIDE",
            clip_index=1,
            objective=True,
            values={"segment_id": "segment-primary", "observed": "overflow"},
        )
        contingency = make_repair_finding(
            "PREDICTIVE_ACTIVE_PICTURE_RISK",
            clip_index=1,
            objective=True,
            values={"segment_id": "segment-contingency", "observed": "late"},
        )
        primary_fingerprint = "a" * 64
        contingency_fingerprint = "b" * 64
        state = PlanRepairState()
        state.record_round(
            round=PlanRepairRound.PRIMARY,
            findings=(primary,),
            authoritative_plan_fingerprint=primary_fingerprint,
            provider_attempts=({"number": 1},),
            provider_outcome="provider_unavailable",
            schema_valid=False,
            semantic_valid=False,
        )
        state.record_round(
            round=PlanRepairRound.CONTINGENCY,
            findings=(contingency,),
            authoritative_plan_fingerprint=contingency_fingerprint,
            provider_attempts=({"number": 1},),
            provider_outcome="ok",
            schema_valid=True,
            semantic_valid=False,
        )
        attempt_by_round = {item.round: item for item in state.attempts}

        def stage_record(round_name, attempt, status, candidate_disposition):
            return {
                "stage": "plan_repair",
                "status": status,
                "repair_round": round_name.value,
                "authoritative_plan_fingerprint": (
                    attempt.defect.authoritative_plan_fingerprint
                ),
                "provider_outcome": attempt.provider_outcome,
                "schema_valid": attempt.schema_valid,
                "semantic_valid": attempt.semantic_valid,
                "candidate_disposition": candidate_disposition,
                "checkpoint_fingerprint": "c" * 64,
                "request": {
                    "request_version": "repair_batch_request.v1",
                    "response_schema": "edit_plan_repair.v1",
                    "repair_round": round_name.value,
                    "semantic_attempt": (
                        1 if round_name is PlanRepairRound.PRIMARY else 2
                    ),
                    "authoritative_plan_fingerprint": (
                        attempt.defect.authoritative_plan_fingerprint
                    ),
                    "defect_instance_ids": [attempt.defect.id],
                    "affected_clip_ids": [1],
                    "objective_codes": [attempt.defect.code],
                    "advisory_codes": [],
                    "evidence_types": ["composition_geometry"],
                    "evidence_ids": ["evidence-1"],
                    "evidence_count": 1,
                    "would_call": True,
                    "call_allowed": True,
                },
                "dispositions": [{
                    "code": attempt.defect.code,
                    "eligible": True,
                    "would_call": True,
                    "call_allowed": True,
                    "reason": "eligible",
                }],
                "resolution": {
                    "original_codes": [attempt.defect.code],
                    "resolved_codes": [],
                    "remaining_codes": [attempt.defect.code],
                    "introduced_codes": [],
                },
                "quality_floor": {
                    "accepted": False,
                    "violation_codes": [],
                },
                "attempts": [{
                    "category": "plan_repair",
                    "number": 1,
                    "status_code": 503 if status == "failed" else 200,
                    "reason": attempt.provider_outcome,
                    "duration_ms": 100,
                }],
                "checkpoint_reused": False,
            }

        report = build_repair_report(
            mode=RepairMode.ENFORCE,
            stage_records=(
                stage_record(
                    PlanRepairRound.PRIMARY,
                    attempt_by_round[PlanRepairRound.PRIMARY],
                    "failed",
                    "unavailable",
                ),
                stage_record(
                    PlanRepairRound.CONTINGENCY,
                    attempt_by_round[PlanRepairRound.CONTINGENCY],
                    "rejected",
                    "rejected",
                ),
            ),
            fallback_entries=({
                "code": "VISUAL_REFRAME_FALLBACK",
                "clip_index": 1,
                "segment_id": "segment-contingency",
                "requested": "semantic_crop",
                "executed": "content_preserving_fit",
            },),
            attempt_evidence=state.attempts,
            rollout_attribution={
                "model": "cx/gpt-5.6-sol",
                "reasoning_effort": "medium",
                "structured_output_mode": "json_schema",
                "structured_output_boundaries": ["edit_plan_repair.v1"],
                "repair_mode": "enforce",
            },
        )

        self.assertEqual(report["version"], "repair_report.v2")
        self.assertEqual(
            [item["repair_round"] for item in report["stages"]],
            ["primary", "contingency"],
        )
        self.assertEqual(len(report["attempt_ledger"]), 2)
        self.assertEqual(report["attribution"]["model"], "cx/gpt-5.6-sol")
        self.assertTrue(report["fallbacks"][0]["fallback_authorized"])
        self.assertEqual(report["fallbacks"][0]["attempt_round"], "contingency")
        self.assertEqual(report["summary"]["fallback_after_attempt_count"], 1)
        self.assertEqual(report["summary"]["jobs_at_two_call_cap"], 1)
        self.assertEqual(validate_repair_report(report), report)

    def test_historical_repair_report_v1_remains_readable(self):
        legacy = build_repair_report(mode=RepairMode.REPORT)
        legacy["version"] = "repair_report.v1"
        legacy.pop("attempt_ledger")
        legacy.pop("attribution")
        legacy["summary"].pop("fallback_after_attempt_count")
        legacy["summary"].pop("repair_invariant_violation_count")
        legacy["summary"].pop("jobs_at_two_call_cap")

        normalized = validate_repair_report(legacy)

        self.assertEqual(normalized["version"], "repair_report.v1")
        self.assertNotIn("attempt_ledger", normalized)

    def test_malformed_repair_reports_fail_closed(self):
        report = build_repair_report(mode=RepairMode.REPORT)
        malformed = deepcopy(report)
        malformed["registry_sha256"] = "0" * 64
        with self.assertRaises(RepairContractError) as caught:
            validate_repair_report(malformed)
        self.assertEqual(caught.exception.code, "REPAIR_REPORT_INVALID")

        malformed = deepcopy(report)
        malformed["stages"] = ["private provider response"]
        with self.assertRaises(RepairContractError) as caught:
            validate_repair_report(malformed)
        self.assertEqual(caught.exception.code, "REPAIR_REPORT_INVALID")


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

    def test_geometry_repair_cannot_mutate_fields_outside_segment_layout_allowlist(self):
        original = shadow_plan()
        payload = original.to_dict()
        segment = payload["clips"][0]["segments"][0]
        segment["layout"].update({
            "mode": "fit",
            "fallback": "fit",
            "allow_full_frame_fallback": True,
            "max_zoom": 1.0,
        })
        accepted = evaluate_repair_quality_floor(
            original,
            EditPlan.model_validate(payload),
            original_codes=("COMPOSITION_CROP_TARGET_TOO_WIDE",),
            repaired_codes=(),
            available_capabilities=original.requested_capabilities,
            affected_clip_indexes=(1,),
            affected_operation_ids=(segment["id"],),
            allowed_mutations_by_operation={
                segment["id"]: (
                    "layout.mode",
                    "layout.fallback",
                    "layout.allow_full_frame_fallback",
                    "layout.max_zoom",
                ),
            },
        )
        self.assertTrue(accepted.accepted)

        payload["clips"][0]["segments"][0]["reason"] = "unapproved rewrite"
        rejected = evaluate_repair_quality_floor(
            original,
            EditPlan.model_validate(payload),
            original_codes=("COMPOSITION_CROP_TARGET_TOO_WIDE",),
            repaired_codes=(),
            available_capabilities=original.requested_capabilities,
            affected_clip_indexes=(1,),
            affected_operation_ids=(segment["id"],),
            allowed_mutations_by_operation={segment["id"]: ("layout.mode",)},
        )
        self.assertIn(
            "REPAIR_MUTATION_OUTSIDE_ALLOWLIST",
            rejected.violation_codes,
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
                    "layout": {"mode": "letterbox"},
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

        blurred_fit = {
            "clips": [{
                "clip_index": 1,
                "segments": [{
                    "id": "fit-segment",
                    "timeline_window": {"start_ms": 0, "end_ms": 4_000},
                    "layout": {"mode": "fit"},
                    "overlays": [],
                }],
            }],
        }
        fit_codes = {
            item.code
            for item in predict_plan_findings(
                blurred_fit,
                source_aspect_ratios={1: 16 / 9},
            )
        }
        self.assertNotIn("PREDICTIVE_ACTIVE_PICTURE_RISK", fit_codes)


if __name__ == "__main__":
    unittest.main()
