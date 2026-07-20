import json
from hashlib import sha256
import unittest

from open_storyline.mvp.creative_intent import (
    build_creative_intent,
    creative_intent_conformance_evidence,
    validate_intent_capabilities,
)


class CreativeIntentTests(unittest.TestCase):
    def test_conformance_evidence_is_bounded_and_attributable(self):
        evidence = creative_intent_conformance_evidence(ValueError(
            "intent prompt-generated-image visible duration is outside its contract"
        ))

        self.assertEqual(evidence, {
            "constraint_code": "asset_visible_duration_outside_contract",
            "intent_id": "prompt-generated-image",
        })
        self.assertEqual(
            creative_intent_conformance_evidence(ValueError("private provider body")),
            {"constraint_code": "intent_conformance_failed"},
        )

    def test_extracts_private_prompt_requirements_without_persisting_prompt_text(self):
        prompt = (
            "Use exactly one vertical Pexels video for approximately 3-5 seconds. "
            "Use exactly one generated editorial image for approximately 2-4 seconds."
        )

        intent = build_creative_intent(
            prompt,
            {
                "settings_version": 1,
                "asset_policy": "auto",
                "max_generated_assets_per_clip": 2,
                "stock_policy": "auto",
                "max_stock_assets_per_clip": 2,
            },
            selected_clip_count=1,
        )

        self.assertEqual(
            [(item.provider, item.kind, item.count) for item in intent.asset_intents],
            [("9router", "generated_image", 1), ("pexels", "stock_video", 1)],
        )
        self.assertEqual(
            [
                (item.duration_min_ms, item.duration_max_ms)
                for item in intent.asset_intents
            ],
            [(2000, 4000), (3000, 5000)],
        )
        serialized = json.dumps(intent.to_dict())
        self.assertNotIn("Use exactly", serialized)
        self.assertEqual(intent.prompt_sha256, sha256(prompt.encode("utf-8")).hexdigest())

    def test_prompt_hash_preserves_internal_whitespace_from_the_stored_value(self):
        prompt = "Use exactly one\n  generated editorial image."

        intent = build_creative_intent(
            f"  {prompt}  ",
            {"asset_policy": "auto", "stock_policy": "off"},
            selected_clip_count=1,
        )

        self.assertEqual(intent.prompt_sha256, sha256(prompt.encode("utf-8")).hexdigest())

    def test_required_settings_expand_per_clip_and_keep_optional_auto_as_budget(self):
        intent = build_creative_intent(
            "Keep the source speaker primary.",
            {
                "settings_version": 2,
                "asset_policy": "required",
                "max_generated_assets_per_clip": 1,
                "stock_policy": "required",
                "max_stock_assets_per_clip": 1,
                "stock_asset_kind": "video",
            },
            selected_clip_count=2,
        )

        self.assertEqual(len(intent.asset_intents), 4)
        self.assertEqual(
            [item.clip_index for item in intent.asset_intents],
            [1, 2, 1, 2],
        )

        optional = build_creative_intent(
            "Keep the source speaker primary.",
            {
                "asset_policy": "auto",
                "max_generated_assets_per_clip": 8,
                "stock_policy": "auto",
                "max_stock_assets_per_clip": 8,
            },
            selected_clip_count=1,
        )
        self.assertEqual(optional.asset_intents, ())

    def test_prompt_requirement_fails_when_provider_capability_is_disabled(self):
        intent = build_creative_intent(
            "Use exactly one generated editorial image.",
            {"asset_policy": "off", "stock_policy": "off"},
            selected_clip_count=1,
        )

        with self.assertRaises(ValueError):
            validate_intent_capabilities(
                intent,
                generated_available=False,
                stock_available=False,
            )

    def test_negative_prompt_does_not_create_required_asset_intent(self):
        intent = build_creative_intent(
            "Do not use exactly one generated editorial image.",
            {"asset_policy": "auto", "stock_policy": "off"},
            selected_clip_count=1,
        )

        self.assertEqual(intent.asset_intents, ())


if __name__ == "__main__":
    unittest.main()
