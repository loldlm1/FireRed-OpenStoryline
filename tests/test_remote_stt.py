from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import os
import unittest
from unittest.mock import patch

import httpx

from open_storyline.utils.remote_stt import RemoteSTTError, RemoteSttCascade


class RemoteSttCascadeTests(unittest.IsolatedAsyncioTestCase):
    def audio_file(self, root: str) -> Path:
        path = Path(root) / "sample.mp3"
        path.write_bytes(b"fake-audio")
        return path

    async def test_returns_first_success_and_normalizes_segments(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "text": "Hola mundo",
                "segments": [{"id": 1, "text": " Hola mundo ", "start": 1.25, "end": 2.5}],
            })

        with TemporaryDirectory() as tmpdir:
            cascade = RemoteSttCascade(
                base_url="https://router.test",
                api_key="secret",
                models=["groq/whisper-large-v3-turbo"],
                transport=httpx.MockTransport(handler),
            )
            result = await cascade.transcribe(self.audio_file(tmpdir), language="es")

        self.assertEqual(result.model, "groq/whisper-large-v3-turbo")
        self.assertEqual(result.text, "Hola mundo")
        self.assertEqual(result.segments[0]["start"], 1250)
        self.assertEqual(result.timestamps, [[1250, 2500]])

    async def test_uses_next_model_after_provider_failure(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode("utf-8", "ignore")
            calls.append(body)
            if "primary/model" in body:
                return httpx.Response(429, json={"error": "quota"})
            return httpx.Response(200, json={
                "text": "fallback",
                "segments": [{"text": "fallback", "start": 0, "end": 1}],
            })

        with TemporaryDirectory() as tmpdir:
            cascade = RemoteSttCascade(
                base_url="https://router.test",
                api_key="secret",
                models=["primary/model", "fallback/model"],
                transport=httpx.MockTransport(handler),
            )
            result = await cascade.transcribe(self.audio_file(tmpdir))

        self.assertEqual(result.model, "fallback/model")
        self.assertEqual(len(result.attempts), 2)
        self.assertFalse(result.attempts[0].success)
        self.assertTrue(result.attempts[1].success)

    async def test_missing_timestamps_uses_next_model(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(200, json={"text": "no timestamps"})
            return httpx.Response(200, json={
                "text": "word timestamps",
                "words": [{"word": "hello", "timestamp": [1.0, 1.5]}],
            })

        with TemporaryDirectory() as tmpdir:
            cascade = RemoteSttCascade(
                base_url="https://router.test/v1",
                api_key="secret",
                models=["missing-segments", "word-fallback"],
                transport=httpx.MockTransport(handler),
            )
            result = await cascade.transcribe(self.audio_file(tmpdir))

        self.assertEqual(result.model, "word-fallback")
        self.assertEqual(result.timestamps, [[1000, 1500]])
        self.assertEqual(result.attempts[0].reason, "transcript has no timestamped segments")

    def test_from_config_builds_remote_cascade(self):
        config = SimpleNamespace(
            base_url="",
            api_key="",
            models=["config-model"],
            timeout=30,
            response_format="verbose_json",
        )
        with patch.dict(os.environ, {
            "NINEROUTER_URL": "https://router.test/v1",
            "NINEROUTER_KEY": "endpoint-key",
            "OPENSTORYLINE_STT_MODELS": "primary,fallback",
            "OPENSTORYLINE_STT_TIMEOUT": "45",
        }, clear=False):
            cascade = RemoteSttCascade.from_config(config)

        self.assertEqual(cascade.models, ["primary", "fallback"])
        self.assertEqual(cascade.timeout, 45)
        self.assertEqual(cascade.endpoint, "https://router.test/v1/audio/transcriptions")

    async def test_total_failure_keeps_reasons_without_secret(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Bearer top-secret unavailable")

        with TemporaryDirectory() as tmpdir:
            cascade = RemoteSttCascade(
                base_url="https://router.test",
                api_key="top-secret",
                models=["one", "two"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await cascade.transcribe(self.audio_file(tmpdir))

        error = caught.exception
        self.assertEqual(error.code, "STT_ALL_PROVIDERS_FAILED")
        self.assertEqual(len(error.attempts), 2)
        serialized = json.dumps(error.to_dict())
        self.assertNotIn("top-secret", serialized)
        self.assertIn("Bearer ***", serialized)

    async def test_missing_configuration_fails_closed(self):
        with self.assertRaises(RemoteSTTError) as caught:
            RemoteSttCascade(base_url="", api_key="", models=[])
        self.assertEqual(caught.exception.code, "STT_CONFIG_INVALID")


if __name__ == "__main__":
    unittest.main()
