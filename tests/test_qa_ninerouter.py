from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "qa_ninerouter", ROOT / "scripts" / "qa_ninerouter.py"
)
assert SPEC is not None and SPEC.loader is not None
qa_ninerouter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qa_ninerouter
SPEC.loader.exec_module(qa_ninerouter)


class NineRouterPreflightTests(unittest.TestCase):
    def test_normalizes_root_and_v1_urls(self):
        self.assertEqual(
            qa_ninerouter.normalize_base_url("https://router.test/"),
            "https://router.test",
        )
        self.assertEqual(
            qa_ninerouter.normalize_base_url("https://router.test/v1/"),
            "https://router.test",
        )

    def test_rejects_non_http_and_query_urls(self):
        with self.assertRaises(ValueError):
            qa_ninerouter.normalize_base_url("router.test")
        with self.assertRaises(ValueError):
            qa_ninerouter.normalize_base_url("https://router.test?key=secret")

    def test_catalog_ids_accepts_openai_and_array_shapes(self):
        self.assertEqual(
            qa_ninerouter.catalog_ids({"data": [{"id": "b"}, {"id": "a"}]}),
            ["a", "b"],
        )
        self.assertEqual(qa_ninerouter.catalog_ids([{"id": "model"}]), ["model"])

    def test_configured_models_uses_csv_without_exposing_other_env(self):
        with patch.dict(os.environ, {"OPENSTORYLINE_STT_MODELS": "one, two,,"}):
            self.assertEqual(
                qa_ninerouter.configured_models("OPENSTORYLINE_STT_MODELS"),
                ["one", "two"],
            )

    def test_redacts_explicit_and_bearer_secrets(self):
        value = qa_ninerouter.redact(
            "Bearer token-value and endpoint-secret",
            ["endpoint-secret"],
        )
        self.assertNotIn("token-value", value)
        self.assertNotIn("endpoint-secret", value)

    def test_validates_structured_chat_content(self):
        self.assertTrue(qa_ninerouter._json_object_contract({
            "choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}],
        }))
        self.assertFalse(qa_ninerouter._json_object_contract({
            "choices": [{"message": {"content": "not json"}}],
        }))

    def test_validates_binary_and_base64_images(self):
        valid, size = qa_ninerouter._image_bytes(
            None,
            qa_ninerouter.VISION_FIXTURE,
            1_000_000,
        )
        self.assertTrue(valid)
        self.assertEqual(size, len(qa_ninerouter.VISION_FIXTURE))

        encoded = qa_ninerouter.base64.b64encode(
            qa_ninerouter.VISION_FIXTURE
        ).decode("ascii")
        valid, size = qa_ninerouter._image_bytes(
            {"data": [{"b64_json": encoded}]},
            b'{"data":[{"b64_json":"redacted"}]}',
            1_000_000,
        )
        self.assertTrue(valid)
        self.assertEqual(size, len(qa_ninerouter.VISION_FIXTURE))

    def test_strict_catalog_requires_a_configured_model(self):
        with patch.object(
            qa_ninerouter,
            "http_json",
            return_value=(200, {"data": [{"id": "model"}]}, "ok"),
        ):
            check = qa_ninerouter.catalog_check(
                "https://router.test",
                "/v1/models",
                "endpoint-key",
                [],
                timeout=1,
                strict_models=True,
            )

        self.assertFalse(check.ok)
        self.assertEqual(check.category, "invalid_config")

    def test_requires_non_empty_finite_stt_segments(self):
        self.assertEqual(
            qa_ninerouter._timestamped_segments({
                "text": "Hola",
                "segments": [{"text": "Hola", "start": 0.1, "end": 0.9}],
            }),
            (True, 1),
        )
        self.assertEqual(
            qa_ninerouter._timestamped_segments({
                "text": "Hola",
                "segments": [{"text": "Hola", "start": 1, "end": 1}],
            }),
            (False, 0),
        )

    def test_multipart_contains_only_selected_stt_fields(self):
        with TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "sample.wav"
            audio.write_bytes(b"synthetic-audio")
            body, content_type = qa_ninerouter._multipart({
                "model": "mistral/voxtral-mini-2602",
                "response_format": "verbose_json",
            }, audio)

        self.assertIn("multipart/form-data; boundary=", content_type)
        self.assertIn(b"mistral/voxtral-mini-2602", body)
        self.assertNotIn(b"language", body)

    def test_live_contracts_skip_models_missing_from_catalog(self):
        mismatch = qa_ninerouter.Check(
            "catalog",
            False,
            200,
            "catalog_mismatch",
            {"missing": ["model"]},
        )
        with patch.object(qa_ninerouter, "post_json") as post_json, patch.object(
            qa_ninerouter, "http_request"
        ) as http_request, patch.dict(os.environ, {
            "OPENSTORYLINE_LLM_MODEL": "cx/gpt-5.6-sol",
            "OPENSTORYLINE_IMAGE_MODELS": "cx/gpt-5.5-image",
            "OPENSTORYLINE_STT_MODELS": "mistral/voxtral-mini-2602",
        }, clear=False):
            checks = qa_ninerouter.live_contract_checks(
                "https://router.test",
                "endpoint-key",
                timeout=1,
                max_image_bytes=1_000_000,
                text_catalog=mismatch,
                image_catalog=mismatch,
                stt_catalog=mismatch,
                stt_audio="",
            )

        self.assertEqual(
            [check.name for check in checks],
            ["text_contract", "vision_contract", "image_contract", "stt_contract"],
        )
        self.assertTrue(all(check.category == "skipped" for check in checks))
        post_json.assert_not_called()
        http_request.assert_not_called()

    def test_container_probe_rejects_unsafe_image_name(self):
        check = qa_ninerouter.container_host_checks(
            "203.0.113.10",
            "root",
            22,
            1,
            "image; reboot",
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.category, "invalid_config")

    def test_http_status_categories_are_actionable(self):
        self.assertEqual(qa_ninerouter._status_category(401), "auth")
        self.assertEqual(qa_ninerouter._status_category(403), "auth")
        self.assertEqual(qa_ninerouter._status_category(429), "rate_limited")
        self.assertEqual(qa_ninerouter._status_category(503), "upstream")


if __name__ == "__main__":
    unittest.main()
