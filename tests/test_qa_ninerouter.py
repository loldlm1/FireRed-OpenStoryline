from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
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


if __name__ == "__main__":
    unittest.main()
