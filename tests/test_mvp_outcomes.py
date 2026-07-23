from __future__ import annotations

import os
import json
import unittest
from unittest.mock import patch

from open_storyline.mvp.outcomes import (
    build_completed_outcome_report,
    build_failed_outcome_report,
    build_outcome_slo_summary,
    outcome_summary,
    retry_ux_enabled,
)
from open_storyline.mvp.repair import RepairMode, build_repair_report


class OutcomeTests(unittest.TestCase):
    def test_post_render_repair_outcome_is_compact_and_private(self):
        private_marker = "private prompt transcript and /private/source.mp4"
        report = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
            post_render_repair={
                "version": "post_render_repair.v1",
                "mode": "enforce",
                "status": "accepted",
                "selected_candidate": "repaired",
                "provider_calls": 1,
                "rounds": 1,
                "affected_clip_indexes": [1],
                "improvement": {
                    "demonstrated": True,
                    "original_finding_count": 1,
                    "repaired_finding_count": 0,
                    "private_reason": private_marker,
                },
                "round_records": [{"private_reason": private_marker}],
            },
        )

        repair = report["post_render_repair"]
        self.assertEqual(repair["status"], "accepted")
        self.assertEqual(repair["selected_candidate"], "repaired")
        self.assertTrue(repair["improvement"]["demonstrated"])
        self.assertNotIn(private_marker, json.dumps(repair))

    def test_render_critic_lifecycle_is_compact_non_mutating_and_private(self):
        private_marker = "private prompt transcript and /private/source.mp4"
        report = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
            render_critic={
                "version": "render_critic.v1",
                "mode": "report",
                "status": "review",
                "non_mutating": True,
                "call_fingerprint": "a" * 64,
                "candidate_fingerprint": "b" * 64,
                "provider_calls": 1,
                "summary": private_marker,
                "findings": [{
                    "finding_id": "finding-" + "c" * 24,
                    "finding_fingerprint": "d" * 64,
                    "defect_code": "RENDER_CRITIC_FINDING",
                    "category": "captions",
                    "severity": "warning",
                    "classification": "creative",
                    "clip_index": 1,
                    "start_ms": 100,
                    "end_ms": 500,
                    "repairable": True,
                    "lifecycle": "observed",
                    "explanation": private_marker,
                }],
            },
        )
        review = report["creative_review"]
        self.assertEqual(review["finding_count"], 1)
        self.assertTrue(review["non_mutating"])
        self.assertEqual(review["findings"][0]["lifecycle"], "observed")
        self.assertNotIn(private_marker, json.dumps(review))

    def test_completed_outcome_separates_creative_and_technical_findings(self):
        limited = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
            promotion_report={
                "promotion_decision": "promote_with_limitations",
                "effective_policy": "baseline_guaranteed",
                "technical_blocker_codes": [],
                "creative_limitation_codes": ["ACTIVE_PICTURE_TOO_SMALL"],
                "policy_decisions": {
                    "strict": "block",
                    "baseline_guaranteed": "promote",
                },
            },
        )
        technical = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
            promotion_report={
                "promotion_decision": "block_technical",
                "technical_blocker_codes": ["AUDIO_MISSING"],
                "creative_limitation_codes": [],
                "policy_decisions": {
                    "strict": "block",
                    "baseline_guaranteed": "block",
                },
            },
        )

        self.assertEqual(limited["grade"], "with_limitations")
        self.assertEqual(limited["technical_status"], "pass")
        self.assertEqual(limited["limitations"][0]["code"], "ACTIVE_PICTURE_TOO_SMALL")
        self.assertEqual(
            limited["limitations"][0]["presentation"]["es"]["title"],
            "La imagen activa es demasiado pequena",
        )
        self.assertEqual(technical["grade"], "retryable_failure")
        self.assertEqual(technical["technical_status"], "blocked")
        self.assertEqual(technical["fatal_errors"][0]["code"], "AUDIO_MISSING")
        self.assertTrue(limited["retry"]["supported"])
        self.assertTrue(technical["retry"]["supported"])
        self.assertEqual(
            technical["fatal_errors"][0]["presentation"]["en"]["title"],
            "Audio is missing",
        )

    def test_v2_preserves_strict_delivery_and_per_code_repair_lifecycle(self):
        report = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
            promotion_report={
                "promotion_decision": "promote_with_limitations",
                "technical_blocker_codes": [],
                "creative_limitation_codes": ["ACTIVE_PICTURE_TOO_SMALL"],
                "strict_decision": "block",
                "delivery_policy": "technical_pass_guaranteed",
                "delivery_decision": "publish_with_limitations",
                "download_available": True,
            },
            repair_report={
                "version": "repair_report.v1",
                "registry_version": "defect_registry.v1",
                "mode": "enforce",
                "stages": [{
                    "stage": "plan_repair",
                    "status": "rejected",
                    "dispositions": [{
                        "code": "ACTIVE_PICTURE_TOO_SMALL",
                        "eligible": True,
                    }],
                    "attempts": [{"category": "plan_repair"}],
                    "checkpoint_reused": True,
                }],
                "fallbacks": [{
                    "code": "ACTIVE_PICTURE_TOO_SMALL",
                    "requested": "crop",
                    "executed": "fit",
                }],
                "summary": {
                    "resolved_codes": [],
                    "remaining_codes": ["ACTIVE_PICTURE_TOO_SMALL"],
                    "introduced_codes": [],
                    "fallback_applied_codes": ["ACTIVE_PICTURE_TOO_SMALL"],
                    "not_repairable_codes": [],
                },
            },
        )

        self.assertEqual(report["version"], "outcome_report.v2")
        self.assertEqual(report["strict_qa"]["decision"], "block")
        self.assertEqual(
            report["delivery"]["decision"],
            "publish_with_limitations",
        )
        self.assertTrue(report["delivery"]["download_available"])
        defect = report["repair"]["defects"][0]
        self.assertTrue(defect["repair_attempted"])
        self.assertEqual(
            defect["dispositions"],
            ["fallback_applied", "remaining"],
        )
        self.assertTrue(defect["stage_statuses"][0]["checkpoint_reused"])

    def test_v1_unknown_codes_remain_safe_and_non_repairable(self):
        summary = outcome_summary({
            "version": "outcome_report.v1",
            "grade": "with_limitations",
            "outputs": [{"video": "short-01.mp4"}],
            "limitations": [{
                "code": "HISTORICAL_UNKNOWN_CODE",
                "stage": "qa",
                "description": "untrusted provider text",
            }],
            "fatal_errors": [],
            "retry": {},
        })

        self.assertEqual(summary["version"], "outcome_report.v1")
        presentation = summary["limitations"][0]["presentation"]
        self.assertEqual(presentation["raw_code"], "HISTORICAL_UNKNOWN_CODE")
        self.assertFalse(presentation["registered"])
        self.assertEqual(presentation["repair_strategy"], "terminal")

    def test_failed_outcome_keeps_strict_creative_block_retryable(self):
        report = build_failed_outcome_report(
            code="RENDER_PROMOTION_BLOCKED",
            stage="post_render_qa",
            retryable=True,
            blocker_codes=["ACTIVE_PICTURE_TOO_SMALL"],
            creative_limitation_codes=["ACTIVE_PICTURE_TOO_SMALL"],
        )

        self.assertEqual(report["grade"], "retryable_failure")
        self.assertEqual(report["technical_status"], "pass")
        self.assertEqual(report["promotion"]["decision"], "block_strict")
        self.assertEqual(report["limitations"][0]["code"], "ACTIVE_PICTURE_TOO_SMALL")
        summary = outcome_summary(report)
        self.assertEqual(
            summary["limitations"][0]["presentation"]["canonical_code"],
            "ACTIVE_PICTURE_TOO_SMALL",
        )
        self.assertEqual(report["fatal_errors"], [])

    def test_non_retryable_failure_uses_terminal_grade(self):
        report = build_failed_outcome_report(
            code="SOURCE_VIDEO_INVALID",
            stage="validating",
            retryable=False,
        )

        self.assertEqual(report["grade"], "terminal_failure")
        self.assertEqual(report["technical_status"], "blocked")
        self.assertTrue(report["retry"]["supported"])
        self.assertFalse(report["retry"]["quality_feedback_supported"])
        self.assertEqual(report["retry"]["recommended_action"], "rerun")

    def test_clean_completed_outcome_still_supports_plain_rerun(self):
        report = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
        )

        self.assertTrue(report["retry"]["supported"])
        self.assertFalse(report["retry"]["quality_feedback_supported"])
        self.assertEqual(report["retry"]["recommended_action"], "rerun")
        self.assertEqual(report["retry"]["unavailable_reason"], "")

    def test_summary_preserves_safe_rerun_unavailable_reason(self):
        report = build_failed_outcome_report(
            code="SOURCE_VIDEO_INVALID",
            stage="validating",
            retryable=False,
        )
        report["retry"].update({
            "supported": False,
            "recommended_action": "none",
            "unavailable_reason": "SESSION_SOURCE_EXPIRED",
        })

        summary = outcome_summary(report)

        self.assertFalse(summary["retry"]["supported"])
        self.assertEqual(
            summary["retry"]["unavailable_reason"],
            "SESSION_SOURCE_EXPIRED",
        )

    def test_failed_outcome_preserves_repair_checkpoint_and_fallback_evidence(self):
        repair = build_repair_report(
            mode=RepairMode.ENFORCE,
            stage_records=({
                "stage": "plan_repair",
                "status": "failed",
                "repair_round": "primary",
                "authoritative_plan_fingerprint": "a" * 64,
                "provider_outcome": "provider_unavailable",
                "schema_valid": False,
                "semantic_valid": False,
                "candidate_disposition": "unavailable",
                "checkpoint_fingerprint": "b" * 64,
                "request": {
                    "request_version": "repair_batch_request.v1",
                    "response_schema": "edit_plan_repair.v1",
                    "response_schema_sha256": "d" * 64,
                    "repair_prompt_sha256": "e" * 64,
                    "repair_round": "primary",
                    "semantic_attempt": 1,
                    "authoritative_plan_fingerprint": "a" * 64,
                    "defect_instance_ids": ["c" * 64],
                    "affected_clip_ids": [1],
                    "objective_codes": ["EDIT_PLAN_INVALID"],
                    "advisory_codes": [],
                    "evidence_types": ["edit_plan"],
                    "evidence_ids": ["evidence-1"],
                    "evidence_count": 1,
                    "would_call": True,
                    "call_allowed": True,
                },
                "dispositions": [{
                    "code": "EDIT_PLAN_INVALID",
                    "eligible": True,
                    "would_call": True,
                    "call_allowed": True,
                    "reason": "eligible",
                }],
                "resolution": {
                    "original_codes": ["EDIT_PLAN_INVALID"],
                    "resolved_codes": [],
                    "remaining_codes": ["EDIT_PLAN_INVALID"],
                    "introduced_codes": [],
                },
                "quality_floor": {
                    "accepted": False,
                    "violation_codes": [],
                },
                "attempts": [{
                    "category": "plan_repair",
                    "number": 1,
                    "status_code": 503,
                    "reason": "provider_unavailable",
                    "duration_ms": 250,
                }],
                "checkpoint_reused": True,
            },),
            fallback_entries=({
                "code": "VISUAL_REFRAME_FALLBACK",
                "clip_index": 1,
                "segment_id": "segment-1",
                "requested": "semantic_crop",
                "executed": "content_preserving_fit",
            },),
            reused_stages=("plan_repair",),
            recomputed_stages=("render_preflight",),
            rollout_attribution={
                "model": "cx/gpt-5.6-sol",
                "reasoning_effort": "medium",
                "structured_output_mode": "json_schema",
                "structured_output_boundaries": ["edit_plan_repair.v1"],
                "repair_mode": "enforce",
            },
        )

        report = build_failed_outcome_report(
            code="SOURCE_VIDEO_INVALID",
            stage="rendering",
            retryable=False,
            repair_report=repair,
            rollout_attribution=repair["attribution"],
            checkpoint_summary=repair["checkpoints"],
            fallback_ledger={
                "entries": [{
                    "code": "VISUAL_REFRAME_FALLBACK",
                    "clip_index": 1,
                    "segment_id": "segment-1",
                    "requested": "semantic_crop",
                    "executed": "content_preserving_fit",
                }],
            },
        )

        self.assertEqual(report["repair"]["report_version"], "repair_report.v2")
        self.assertEqual(report["repair"]["mode"], "enforce")
        self.assertEqual(report["attribution"]["model"], "cx/gpt-5.6-sol")
        self.assertEqual(report["attribution"]["schema_hashes"], ["d" * 64])
        self.assertEqual(report["attribution"]["prompt_hashes"], ["e" * 64])
        self.assertEqual(report["repair"]["stages"][0]["repair_round"], "primary")
        self.assertEqual(
            report["repair"]["stages"][0]["provider_outcome"],
            "provider_unavailable",
        )
        self.assertEqual(report["repair"]["stages"][0]["transport_attempts"], 1)
        self.assertIn(
            "VISUAL_REFRAME_FALLBACK",
            report["repair"]["fallback_applied_codes"],
        )
        self.assertIn("plan_repair", report["retry"]["reused_stage_names"])
        self.assertIn("EDIT_PLAN_INVALID", report["repair"]["remaining_codes"])
        self.assertIn("SOURCE_VIDEO_INVALID", report["repair"]["remaining_codes"])
        self.assertNotIn("SOURCE_VIDEO_INVALID", report["repair"]["introduced_codes"])

    def test_completed_outcome_preserves_bounded_semantic_qa_evidence(self):
        report = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
            semantic_review={
                "status": "pass",
                "provider_calls": 1,
                "frame_count": 4,
                "observations": [{"frame_id": f"frame-{index}"} for index in range(4)],
                "attempts": [{
                    "duration_ms": 1500,
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "reasoning_tokens": 10,
                    "total_tokens": 130,
                    "cost_usd": 0.01,
                }],
            },
        )

        semantic = report["semantic_qa"]
        compact = outcome_summary(report)["semantic_qa"]
        self.assertEqual(semantic["status"], "pass")
        self.assertTrue(semantic["schema_valid"])
        self.assertEqual(semantic["frame_count"], 4)
        self.assertEqual(semantic["observation_count"], 4)
        self.assertEqual(semantic["metrics"]["provider_latency_ms"], 1500)
        self.assertEqual(semantic["metrics"]["total_tokens"], 130)
        self.assertEqual(semantic["metrics"]["cost_usd"], 0.01)
        self.assertEqual(compact, semantic)

    def test_summary_ignores_untrusted_stored_presentation(self):
        report = build_completed_outcome_report(
            outputs=[{"video": "clip-01.mp4", "subtitles": None}],
            qa_blocker_codes=["ACTIVE_PICTURE_TOO_SMALL"],
        )
        report["limitations"][0]["presentation"] = {
            "es": {"title": "provider response", "description": "private payload"}
        }

        summary = outcome_summary(report)

        self.assertEqual(
            summary["limitations"][0]["presentation"]["es"]["title"],
            "La imagen activa es demasiado pequena",
        )

    def test_visual_coverage_failure_supports_evidence_backed_retry(self):
        report = build_failed_outcome_report(
            code="EDIT_PLAN_VISUAL_COVERAGE_INSUFFICIENT",
            stage="planning_agentic_edit",
            retryable=True,
        )

        self.assertEqual(report["grade"], "retryable_failure")
        self.assertTrue(report["retry"]["supported"])
        self.assertTrue(report["retry"]["quality_feedback_supported"])

    def test_summary_is_bounded_and_slo_reports_confidence(self):
        enhanced = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
        )
        limited = build_completed_outcome_report(
            outputs=[{"video": "short-02.mp4", "subtitles": None}],
            promotion_report={
                "technical_blocker_codes": [],
                "creative_limitation_codes": ["CAPTION_WIDTH_EXCEEDED"],
            },
            reused_stages=("transcript", "global_analysis"),
        )
        rows = [
            {
                "outcome": enhanced,
                "started_at": "2026-07-21T10:00:00Z",
                "completed_at": "2026-07-21T10:01:00Z",
            },
            {
                "outcome": limited,
                "retry_of_attempt_id": "a" * 32,
                "prior_limitation_codes": ["CAPTION_WIDTH_EXCEEDED"],
                "started_at": "2026-07-21T10:02:00Z",
                "completed_at": "2026-07-21T10:03:30Z",
            },
            {"outcome": None},
        ]

        compact = outcome_summary(limited)
        slo = build_outcome_slo_summary(rows)

        self.assertEqual(compact["output_count"], 1)
        self.assertTrue(compact["limitations"][0]["retryable"])
        self.assertEqual(compact["retry"]["reused_stage_names"], [
            "global_analysis",
            "transcript",
        ])
        self.assertEqual(slo["sample_size"], 2)
        self.assertEqual(slo["unclassified_attempts"], 1)
        self.assertEqual(slo["playable_output_rate"], 1.0)
        self.assertFalse(slo["claim_ready"])
        self.assertEqual(slo["retry"]["success_rate"], 1.0)
        self.assertEqual(
            slo["retry"]["by_prior_limitation_code"][0]["code"],
            "CAPTION_WIDTH_EXCEEDED",
        )
        self.assertEqual(slo["top_limitation_codes"][0]["code"], "CAPTION_WIDTH_EXCEEDED")

    def test_retry_ux_flag_defaults_off_and_rejects_invalid_values(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(retry_ux_enabled())
        with patch.dict(os.environ, {"OPENSTORYLINE_RETRY_UX_ENABLED": "true"}):
            self.assertTrue(retry_ux_enabled())
        with patch.dict(os.environ, {"OPENSTORYLINE_RETRY_UX_ENABLED": "sometimes"}):
            with self.assertRaises(ValueError):
                retry_ux_enabled()

    def test_rollout_metrics_are_attributable_bounded_and_reviewed(self):
        schema_hash = "a" * 64
        prompt_hash = "b" * 64

        def stage(name, status, code, reason, duration, *, introduced=()):
            return {
                "stage": name,
                "status": status,
                "request": {
                    "request_version": "repair_batch_request.v1",
                    "response_schema": (
                        "visual_understanding.v1"
                        if name == "visual_understanding"
                        else "edit_plan_repair.v1"
                    ),
                    "response_schema_sha256": schema_hash,
                    "repair_prompt_version": "mvp-defect-repair.v1",
                    "repair_prompt_sha256": prompt_hash,
                    "request_fingerprint": "c" * 64,
                    "editing_prompt_sha256": "d" * 64,
                    "transcript_sha256": "e" * 64,
                    "candidate_sha256": "f" * 64,
                    "affected_clip_ids": [1],
                    "objective_codes": [code],
                    "advisory_codes": [],
                    "evidence_types": ["edit_plan"],
                    "evidence_ids": ["evidence-1"],
                    "evidence_count": 1,
                    "would_call": True,
                    "call_allowed": True,
                },
                "dispositions": [{
                    "code": code,
                    "eligible": True,
                    "would_call": True,
                    "call_allowed": True,
                    "reason": "eligible",
                }],
                "resolution": {
                    "original_codes": [code],
                    "resolved_codes": [code] if status == "repaired" else [],
                    "remaining_codes": [] if status == "repaired" else [code],
                    "introduced_codes": list(introduced),
                },
                "quality_floor": {
                    "accepted": status == "repaired",
                    "violation_codes": [],
                },
                "attempts": [{
                    "category": name,
                    "number": 1,
                    "status_code": 200,
                    "reason": reason,
                    "duration_ms": duration,
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "reasoning_tokens": 10,
                    "total_tokens": 130,
                    "cost_usd": 0.01,
                }],
                "checkpoint_reused": False,
            }

        repair = build_repair_report(
            mode=RepairMode.ENFORCE,
            stage_records=(
                stage(
                    "visual_understanding",
                    "failed",
                    "VISUAL_RESPONSE_INVALID",
                    "schema_mismatch",
                    1_500,
                    introduced=("VISUAL_FRAME_UNKNOWN",),
                ),
                stage(
                    "plan_repair",
                    "repaired",
                    "EDIT_PLAN_INVALID",
                    "ok",
                    2_500,
                ),
            ),
            predictive_findings=(
                {
                    "code": "PREDICTIVE_ACTIVE_PICTURE_RISK",
                    "clip_index": 1,
                    "objective": True,
                    "confidence": 0.9,
                    "detector": "synthetic",
                    "threshold": "ratio<0.5",
                },
                {
                    "code": "PREDICTIVE_INACTIVE_HOOK_RISK",
                    "clip_index": 1,
                    "objective": False,
                    "confidence": 0.7,
                    "detector": "synthetic",
                    "threshold": "motion<0.1",
                },
            ),
            fallback_entries=({
                "code": "EFFECT_OMITTED",
                "clip_index": 1,
                "segment_id": "segment-1",
                "requested": "FFMPEGA_PLAN_INVALID",
                "executed": "native_render",
            },),
        )
        report = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
            promotion_report={
                "technical_blocker_codes": [],
                "creative_limitation_codes": ["ACTIVE_PICTURE_TOO_SMALL"],
                "strict_decision": "block",
                "delivery_policy": "technical_pass_guaranteed",
                "delivery_decision": "publish_with_limitations",
                "download_available": True,
            },
            repair_report=repair,
            rollout_attribution={
                "model": "cx/gpt-5.6-sol",
                "reasoning_effort": "medium",
                "structured_output_mode": "json_schema",
                "structured_output_boundaries": [
                    "visual_understanding.v1",
                    "edit_plan_repair.v1",
                ],
                "repair_mode": "enforce",
                "delivery_policy": "technical_pass_guaranteed",
                "catalog_version": "2026.07.1",
                "renderer_profile": "high",
                "schema_hashes": [schema_hash],
                "prompt_hashes": [prompt_hash],
            },
        )
        summary = build_outcome_slo_summary([{
            "outcome": report,
            "started_at": "2026-07-21T10:00:00Z",
            "completed_at": "2026-07-21T10:00:10Z",
        }])

        metrics = report["repair"]["metrics"]
        self.assertEqual(metrics["semantic_calls"], 2)
        self.assertEqual(metrics["strict_schema_attempts"], 2)
        self.assertEqual(metrics["strict_schema_valid"], 1)
        self.assertEqual(metrics["semantic_valid"], 1)
        self.assertEqual(metrics["provider_latency_ms"], 4_000)
        self.assertEqual(metrics["total_tokens"], 260)
        self.assertEqual(metrics["ffmpega_omission_count"], 1)
        self.assertEqual(metrics["primary_calls"], 1)
        self.assertEqual(metrics["contingency_calls"], 0)
        self.assertEqual(metrics["defects_presented"], 2)
        self.assertEqual(metrics["provider_failures"], 1)
        self.assertEqual(metrics["repair_invariant_violation_count"], 0)
        self.assertEqual(summary["repair"]["strict_schema"]["validity_rate"], 0.5)
        self.assertEqual(summary["repair"]["semantic_validity"]["rate"], 0.5)
        self.assertEqual(summary["repair"]["success"]["plan"]["rate"], 1.0)
        self.assertEqual(summary["repair"]["success"]["visual"]["rate"], 0.0)
        self.assertEqual(summary["repair"]["rounds"]["primary_calls"], 1)
        self.assertEqual(summary["repair"]["rounds"]["contingency_calls"], 0)
        self.assertEqual(
            summary["repair"]["predictive"]["advisory_attachment_rate"],
            1.0,
        )
        self.assertEqual(summary["delivery"]["technical_pass_publication_rate"], 1.0)
        self.assertFalse(
            summary["rollout_review"]["checks"]["no_new_defect_regression"]
        )
        self.assertTrue(summary["claim_gate"]["evidence_only"])
        self.assertFalse(summary["claim_gate"]["enables_rollout"])
        self.assertEqual(summary["attribution"][0]["model"], "cx/gpt-5.6-sol")

    def test_zero_failure_claim_is_statistical_evidence_not_rollout_authority(self):
        outcome = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
        )
        summary = build_outcome_slo_summary([
            {"outcome": outcome}
            for _ in range(500)
        ])

        self.assertTrue(summary["claim_ready"])
        self.assertGreaterEqual(summary["confidence_95"]["low"], 0.99)
        self.assertTrue(summary["claim_gate"]["evidence_only"])
        self.assertFalse(summary["claim_gate"]["enables_rollout"])
        self.assertTrue(summary["rollout_review"]["operator_approval_required"])
        self.assertFalse(summary["rollout_review"]["automatic_enablement"])

    def test_report_only_trigger_does_not_create_provider_latency_sample(self):
        repair = build_repair_report(
            mode=RepairMode.REPORT,
            stage_records=({
                "stage": "plan_repair",
                "status": "report_only",
                "request": {
                    "request_version": "repair_batch_request.v1",
                    "response_schema": "edit_plan_repair.v1",
                    "affected_clip_ids": [1],
                    "objective_codes": ["EDIT_PLAN_INVALID"],
                    "advisory_codes": [],
                    "evidence_types": ["edit_plan"],
                    "evidence_ids": ["evidence-1"],
                    "evidence_count": 1,
                    "would_call": True,
                    "call_allowed": False,
                },
                "dispositions": [{
                    "code": "EDIT_PLAN_INVALID",
                    "eligible": True,
                    "would_call": True,
                    "call_allowed": False,
                    "reason": "report_only",
                }],
                "resolution": {
                    "original_codes": ["EDIT_PLAN_INVALID"],
                    "resolved_codes": [],
                    "remaining_codes": ["EDIT_PLAN_INVALID"],
                    "introduced_codes": [],
                },
                "quality_floor": {
                    "accepted": False,
                    "violation_codes": [],
                },
                "attempts": [],
                "checkpoint_reused": False,
            },),
        )
        report = build_completed_outcome_report(
            outputs=[{"video": "short-01.mp4", "subtitles": None}],
            repair_report=repair,
        )

        summary = build_outcome_slo_summary([{"outcome": report}])

        self.assertEqual(summary["repair"]["triggered_attempts"], 1)
        self.assertEqual(summary["repair"]["provider_calls"], 0)
        self.assertIsNone(summary["repair"]["provider_latency_ms"]["p95"])
        self.assertEqual(summary["repair"]["provider_latency_ms"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
