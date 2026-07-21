import json
import unittest

from open_storyline.mvp.observability import (
    QUALITY_FEEDBACK_VERSION,
    compact_prior_attempt_quality_feedback,
)


class PriorAttemptQualityFeedbackTests(unittest.TestCase):
    def test_compacts_only_allowlisted_objective_evidence(self):
        private_marker = "private transcript and /private/source.mp4"
        feedback = compact_prior_attempt_quality_feedback(
            prior_attempt_id="a" * 32,
            prior_attempt_number=2,
            documents={
                "render_promotion.json": {
                    "version": "render_promotion.v1",
                    "blocker_codes": ["ACTIVE_PICTURE_TOO_SMALL"],
                    "private": private_marker,
                },
                "clip_visual_coverage.json": {
                    "version": "clip_visual_coverage.v1",
                    "segments": [{
                        "clip_index": 1,
                        "segment_id": "segment-1",
                        "source_start_ms": 5000,
                        "source_end_ms": 9000,
                        "observation_count": 0,
                        "maximum_gap_ms": 4000,
                        "blocker_codes": ["CROP_VISUAL_OBSERVATION_MISSING"],
                        "target_id": private_marker,
                    }],
                },
                "frame_quality_qa.json": {
                    "version": "frame_quality_qa.v1",
                    "clips": [{
                        "clip_index": 1,
                        "active_picture": {"summary": {
                            "median_active_area_ratio": 0.31,
                            "minimum_active_area_ratio": 0.30,
                            "median_active_height_ratio": 0.3125,
                        }},
                        "reference_metrics": {"samples": [{
                            "timestamp_ms": 7000,
                            "segment_id": "segment-1",
                            "operation": "crop",
                            "strategy": "crop",
                            "ssim": 0.62,
                            "psnr": 16.5,
                            "private": private_marker,
                        }]},
                        "findings": [{
                            "code": "REFERENCE_QUALITY_CATASTROPHIC",
                            "severity": "blocker",
                            "details": {"private": private_marker},
                        }],
                    }],
                },
                "creative_conformance.json": {
                    "version": "creative_conformance.v1",
                    "findings": [{
                        "code": "requested_assets_missing",
                        "severity": "blocker",
                        "message": private_marker,
                    }],
                },
                "outcome_report.json": {
                    "version": "outcome_report.v1",
                    "grade": "with_limitations",
                    "limitations": [{
                        "code": "VISUAL_REFRAME_FALLBACK",
                        "stage": "compile",
                        "clip_index": 1,
                        "segment_id": "segment-1",
                        "requested": "crop",
                        "executed": "fit",
                        "description": private_marker,
                        "recommended_retry_action": "retry_defects",
                    }],
                },
                "fallback_ledger.json": {
                    "version": "fallback_ledger.v1",
                    "entries": [{
                        "code": "TRANSITION_FALLBACK",
                        "clip_index": 1,
                        "segment_id": "segment-2",
                        "requested": "custom transition",
                        "executed": "hard_cut",
                        "reason": private_marker,
                        "retry_action": "retry_defects",
                    }],
                },
                "short-01.caption-footprint.json": {
                    "version": "caption_footprint.v1",
                    "summary": {
                        "blocker_codes": ["CAPTION_WIDTH_EXCEEDED"],
                        "maximum_width_ratio": 0.9,
                        "maximum_height_ratio": 0.12,
                        "worst_cue_index": 3,
                    },
                    "bounds": [{"text": private_marker}],
                },
            },
        )

        self.assertEqual(feedback["version"], QUALITY_FEEDBACK_VERSION)
        self.assertEqual(feedback["prior_attempt_number"], 2)
        self.assertEqual(feedback["crop_windows"][0]["source_start_ms"], 5000)
        self.assertEqual(feedback["worst_metric_samples"][0]["timestamp_ms"], 7000)
        self.assertEqual(feedback["active_picture"][0]["median_active_height_ratio"], 0.3125)
        self.assertIn("CAPTION_WIDTH_EXCEEDED", feedback["blocker_codes"])
        self.assertIn("REQUESTED_ASSETS_MISSING", feedback["blocker_codes"])
        self.assertEqual(feedback["prior_outcome_grade"], "with_limitations")
        self.assertEqual(
            feedback["retry_reason_codes"],
            ["TRANSITION_FALLBACK", "VISUAL_REFRAME_FALLBACK"],
        )
        self.assertEqual(feedback["limitations"][0]["executed"], "fit")
        self.assertIn("frame_quality_qa.v1", feedback["evidence_versions"])
        self.assertIn("outcome_report.v1", feedback["evidence_versions"])
        self.assertNotIn(private_marker, json.dumps(feedback))

    def test_malformed_documents_fail_closed_without_private_text(self):
        private_marker = "private transcript and /private/source.mp4"
        feedback = compact_prior_attempt_quality_feedback(
            prior_attempt_id="b" * 32,
            prior_attempt_number=1,
            documents={
                "render_promotion.json": private_marker,
                "clip_visual_coverage.json": {
                    "segments": [private_marker, {
                        "clip_index": private_marker,
                        "segment_id": private_marker,
                        "source_start_ms": float("inf"),
                        "source_end_ms": {},
                        "observation_count": [],
                        "maximum_gap_ms": None,
                        "blocker_codes": ["CROP_VISUAL_OBSERVATION_MISSING"],
                    }],
                },
                "frame_quality_qa.json": {
                    "clips": [{
                        "clip_index": {},
                        "active_picture": private_marker,
                        "reference_metrics": {"samples": private_marker},
                        "findings": [private_marker],
                    }],
                },
                "creative_conformance.json": {"findings": private_marker},
                "short.caption-footprint.json": {"summary": private_marker},
            },
        )

        self.assertEqual(feedback["blocker_codes"], ["CROP_VISUAL_OBSERVATION_MISSING"])
        self.assertEqual(feedback["crop_windows"][0]["segment_id"], "")
        self.assertEqual(feedback["worst_metric_samples"], [])
        self.assertEqual(feedback["evidence_versions"], [])
        self.assertNotIn(private_marker, json.dumps(feedback))


if __name__ == "__main__":
    unittest.main()
