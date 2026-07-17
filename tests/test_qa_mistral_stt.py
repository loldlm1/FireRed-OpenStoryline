from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "qa_mistral_stt", ROOT / "scripts" / "qa_mistral_stt.py"
)
assert SPEC is not None and SPEC.loader is not None
qa_mistral_stt = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qa_mistral_stt
SPEC.loader.exec_module(qa_mistral_stt)


class MistralSTTPreflightTests(unittest.IsolatedAsyncioTestCase):
    def audio_file(self, root: str) -> Path:
        path = Path(root) / "synthetic.wav"
        path.write_bytes(b"synthetic-audio")
        return path

    @staticmethod
    def args(audio: Path, *, each_key: bool = False):
        return SimpleNamespace(audio=str(audio), timeout=10, each_key=each_key)

    async def test_success_reports_contract_metadata_without_transcript_or_key(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "text": "private transcript marker",
                "segments": [{"text": "marker", "start": 0.1, "end": 0.8}],
            })

        with TemporaryDirectory() as tmpdir, patch.dict(
            os.environ, {"MISTRAL_API_KEYS": "private-key-value"}, clear=False
        ):
            code, payload = await qa_mistral_stt.run(
                self.args(self.audio_file(tmpdir)),
                transport=httpx.MockTransport(handler),
            )

        serialized = json.dumps(payload)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["checks"][0]["segments"], 1)
        self.assertNotIn("private transcript marker", serialized)
        self.assertNotIn("private-key-value", serialized)

    async def test_each_key_classifies_rate_limit_and_accepts_one_success(self):
        calls: list[str] = []
        sleeps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            authorization = request.headers["authorization"]
            calls.append(authorization)
            if authorization == "Bearer limited-key":
                return httpx.Response(429, headers={"Retry-After": "60"})
            return httpx.Response(200, json={
                "text": "ok",
                "segments": [{"text": "ok", "start": 0, "end": 1}],
            })

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        with TemporaryDirectory() as tmpdir, patch.dict(os.environ, {
            "MISTRAL_API_KEYS": "limited-key,working-key",
        }, clear=False):
            code, payload = await qa_mistral_stt.run(
                self.args(self.audio_file(tmpdir), each_key=True),
                transport=httpx.MockTransport(handler),
                sleep=fake_sleep,
            )

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["Bearer limited-key", "Bearer working-key"])
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(payload["checks"][0]["category"], "rate_limited")
        self.assertTrue(payload["checks"][1]["ok"])

    async def test_invalid_timestamp_contract_is_red(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"text": "no segments"})

        with TemporaryDirectory() as tmpdir, patch.dict(
            os.environ, {"MISTRAL_API_KEYS": "secret"}, clear=False
        ):
            code, payload = await qa_mistral_stt.run(
                self.args(self.audio_file(tmpdir)),
                transport=httpx.MockTransport(handler),
            )

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["checks"][0]["category"], "contract_invalid")

    async def test_missing_secret_and_fixture_are_configuration_errors(self):
        with TemporaryDirectory() as tmpdir, patch.dict(
            os.environ, {"MISTRAL_API_KEYS": ""}, clear=False
        ):
            code, payload = await qa_mistral_stt.run(
                self.args(self.audio_file(tmpdir))
            )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "MISTRAL_API_KEYS is required")

        code, payload = await qa_mistral_stt.run(
            self.args(Path("/missing/synthetic.wav"))
        )
        self.assertEqual(code, 2)
        self.assertIn("MISTRAL_QA_STT_AUDIO", payload["error"])


if __name__ == "__main__":
    unittest.main()
