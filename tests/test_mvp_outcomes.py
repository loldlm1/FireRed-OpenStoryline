from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from open_storyline.mvp.outcomes import (
    build_completed_outcome_report,
    build_failed_outcome_report,
    build_outcome_slo_summary,
    outcome_summary,
    retry_ux_enabled,
)


class OutcomeTests(unittest.TestCase):
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
        self.assertEqual(
            technical["fatal_errors"][0]["presentation"]["en"]["title"],
            "Audio is missing",
        )

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


if __name__ == "__main__":
    unittest.main()
