import ast
from pathlib import Path
import re
import unittest

from open_storyline.mvp.defects import (
    DEFECT_ALIASES,
    DEFECT_REGISTRY,
    DEFECT_REGISTRY_VERSION,
    REGISTRY_V1_EXCLUSIONS,
    DefectSeverity,
    PromotionClass,
    RepairPhase,
    RepairStrategy,
    defect_definition,
    defect_public_metadata,
    normalize_defect_code,
    retryable_for_code,
)


class DefectRegistryTests(unittest.TestCase):
    def test_registry_is_versioned_unique_and_presentable(self):
        self.assertEqual(DEFECT_REGISTRY_VERSION, "defect_registry.v1")
        self.assertGreater(len(DEFECT_REGISTRY), 300)
        self.assertEqual(len(DEFECT_REGISTRY), len(set(DEFECT_REGISTRY)))
        for code, definition in DEFECT_REGISTRY.items():
            self.assertEqual(definition.code, code)
            self.assertTrue(definition.title_en)
            self.assertTrue(definition.description_en)
            self.assertTrue(definition.title_es)
            self.assertTrue(definition.description_es)
            self.assertNotIn("Bearer ", definition.description_en)
            self.assertNotIn("api_key", definition.description_en.lower())

    def test_current_outcome_families_are_registered(self):
        expected = {
            "ACTIVE_PICTURE_TOO_SMALL",
            "AUDIO_MISSING",
            "CAPTION_WIDTH_EXCEEDED",
            "EDIT_PLAN_INVALID",
            "EDIT_PLAN_VISUAL_COVERAGE_INSUFFICIENT",
            "EFFECT_OMITTED",
            "NINEROUTER_REQUEST_FAILED",
            "RENDER_PROMOTION_BLOCKED",
            "REQUESTED_ASSETS_MISSING",
            "TRANSITION_FALLBACK",
            "VISUAL_FRAME_UNKNOWN",
            "VISUAL_REFRAME_FALLBACK",
        }
        self.assertEqual(expected - DEFECT_REGISTRY.keys(), set())

    def test_lowercase_qa_codes_normalize_without_mutating_raw_metadata(self):
        self.assertEqual(
            normalize_defect_code("active_picture_too_small"),
            "ACTIVE_PICTURE_TOO_SMALL",
        )
        metadata = defect_public_metadata("active_picture_too_small")
        self.assertEqual(metadata["raw_code"], "ACTIVE_PICTURE_TOO_SMALL")
        self.assertEqual(metadata["canonical_code"], "ACTIVE_PICTURE_TOO_SMALL")
        self.assertTrue(metadata["registered"])
        self.assertEqual(
            DEFECT_ALIASES["ACTIVE_PICTURE_TOO_SMALL"],
            "ACTIVE_PICTURE_TOO_SMALL",
        )

    def test_unknown_codes_fail_closed_and_are_not_repairable(self):
        definition = defect_definition("FUTURE_UNREGISTERED_CODE")
        self.assertEqual(definition.default_severity, DefectSeverity.TERMINAL)
        self.assertEqual(definition.repair_strategy, RepairStrategy.TERMINAL)
        self.assertEqual(definition.promotion_class, PromotionClass.TECHNICAL_BLOCKER)
        self.assertFalse(definition.retryable)
        metadata = defect_public_metadata("FUTURE_UNREGISTERED_CODE")
        self.assertFalse(metadata["registered"])
        self.assertEqual(metadata["raw_code"], "FUTURE_UNREGISTERED_CODE")
        self.assertEqual(metadata["canonical_code"], "UNKNOWN_DEFECT")
        self.assertFalse(defect_public_metadata("unsafe code")["registered"])

    def test_repair_policy_matches_required_code_families(self):
        self.assertEqual(
            defect_definition("VISUAL_FRAME_UNKNOWN").repair_strategy,
            RepairStrategy.LLM_VISUAL_REPAIR,
        )
        self.assertEqual(
            defect_definition("EDIT_PLAN_INVALID").repair_strategy,
            RepairStrategy.LLM_PLAN_REPAIR,
        )
        self.assertEqual(
            defect_definition("FFMPEGA_PLAN_INVALID").repair_strategy,
            RepairStrategy.DETERMINISTIC_FALLBACK,
        )
        self.assertEqual(
            defect_definition("FFPROBE_UNAVAILABLE").promotion_class,
            PromotionClass.TECHNICAL_BLOCKER,
        )
        self.assertEqual(
            defect_definition("SEMANTIC_QA_UNAVAILABLE").repair_strategy,
            RepairStrategy.ADVISORY,
        )
        self.assertTrue(defect_definition("ATTENTION_GAP").retryable)
        caption = defect_definition("CAPTION_WIDTH_EXCEEDED")
        self.assertEqual(caption.repair_strategy, RepairStrategy.DETERMINISTIC_FALLBACK)
        self.assertEqual(caption.repair_phase, RepairPhase.POST_RENDER)
        self.assertEqual(caption.promotion_class, PromotionClass.CREATIVE_LIMITATION)

    def test_retryability_uses_registry_with_legacy_unknown_suffix_compatibility(self):
        self.assertTrue(retryable_for_code("NINEROUTER_REQUEST_FAILED"))
        self.assertFalse(retryable_for_code("SOURCE_VIDEO_INVALID"))
        self.assertTrue(retryable_for_code("HISTORICAL_TOOL_UNAVAILABLE"))
        self.assertFalse(
            retryable_for_code(
                "HISTORICAL_TOOL_UNAVAILABLE",
                legacy_suffix_compatibility=False,
            )
        )

    def test_out_of_scope_codes_have_stable_exclusion_reasons(self):
        self.assertIn("ADMIN_COMMAND_INVALID", REGISTRY_V1_EXCLUSIONS)
        self.assertIn("PAGE_LIMIT_INVALID", REGISTRY_V1_EXCLUSIONS)
        self.assertIn("ACTIVITY_FIELD_INVALID", REGISTRY_V1_EXCLUSIONS)
        for code, reason in REGISTRY_V1_EXCLUSIONS.items():
            self.assertNotIn(code, DEFECT_REGISTRY)
            self.assertTrue(reason)

    def test_mvp_code_literals_are_registered_or_explicitly_excluded(self):
        code_pattern = re.compile(r"^[A-Z][A-Z0-9_]{2,119}$")
        suffixes = (
            "_BLOCKED", "_BUSY", "_CANCELLED", "_CHANGED", "_COLLAPSED",
            "_CONFLICT", "_DETECTED", "_EMPTY", "_EXCEEDED", "_EXPIRED",
            "_FAILED", "_FAILURE", "_IMMUTABLE", "_INCOMPLETE", "_INVALID",
            "_MISMATCH", "_MISSING", "_NOT_FOUND", "_OMITTED", "_RATE_LIMITED",
            "_REQUIRED", "_REVIEW", "_TIMEOUT", "_TOO_LARGE", "_TOO_LONG",
            "_TOO_SMALL", "_UNKNOWN", "_UNAVAILABLE", "_UNMET", "_UNRESOLVED",
            "_UNSUPPORTED", "_USED",
        )
        discovered: set[str] = set()
        root = Path(__file__).parents[1] / "src" / "open_storyline" / "mvp"
        for path in root.rglob("*.py"):
            if path.name == "defects.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                value = node.value.strip()
                if value.startswith("OPENSTORYLINE_"):
                    continue
                if code_pattern.fullmatch(value) and (
                    value.endswith(suffixes) or value == "UNAUTHENTICATED"
                ):
                    discovered.add(value)
        uncovered = discovered - DEFECT_REGISTRY.keys() - REGISTRY_V1_EXCLUSIONS.keys()
        self.assertEqual(sorted(uncovered), [])


if __name__ == "__main__":
    unittest.main()
