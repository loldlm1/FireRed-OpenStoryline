from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import os
import unittest
from unittest.mock import patch

import httpx

from open_storyline.utils.remote_stt import (
    MAX_MISTRAL_API_KEYS,
    MISTRAL_STT_ENDPOINT,
    MISTRAL_STT_MODEL,
    MistralSTTClient,
    RemoteSTTError,
    parse_mistral_api_keys,
)


class MistralSTTClientTests(unittest.IsolatedAsyncioTestCase):
    def audio_file(self, root: str) -> Path:
        path = Path(root) / "sample.mp3"
        path.write_bytes(b"fake-audio")
        return path

    def test_parses_and_deduplicates_secret_keys(self):
        self.assertEqual(
            parse_mistral_api_keys(" first, second, first, ,"),
            ["first", "second"],
        )

    def test_rejects_placeholder_and_oversized_key_lists(self):
        with self.assertRaises(RemoteSTTError):
            parse_mistral_api_keys("replace-with-your-mistral-api-key")
        with self.assertRaises(RemoteSTTError):
            parse_mistral_api_keys(",".join(
                f"key-{index}" for index in range(MAX_MISTRAL_API_KEYS + 1)
            ))

    async def test_returns_timestamped_transcript_from_direct_mistral(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), MISTRAL_STT_ENDPOINT)
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            self.assertIn(b'voxtral-mini-2602', request.content)
            self.assertIn(b'timestamp_granularities', request.content)
            self.assertIn(b'segment', request.content)
            self.assertNotIn(b'language', request.content)
            return httpx.Response(200, json={
                "text": "Hola mundo",
                "segments": [
                    {"id": 1, "text": " Hola mundo ", "start": 1.25, "end": 2.5},
                ],
            })

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["secret"],
                transport=httpx.MockTransport(handler),
            )
            result = await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(result.model, MISTRAL_STT_MODEL)
        self.assertEqual(result.text, "Hola mundo")
        self.assertEqual(result.segments[0]["start"], 1250)
        self.assertEqual(result.timestamps, [[1250, 2500]])

    def test_sprint_six_rejects_multiple_keys_until_failover_is_enabled(self):
        with self.assertRaises(RemoteSTTError) as caught:
            MistralSTTClient(api_keys=["first", "second"])
        self.assertEqual(caught.exception.code, "STT_CONFIG_INVALID")

    async def test_missing_timestamps_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"text": "no timestamps"})

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["secret"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(caught.exception.code, "STT_ALL_PROVIDERS_FAILED")
        self.assertEqual(
            caught.exception.attempts[0].reason,
            "transcript has no timestamped segments",
        )

    async def test_non_finite_timestamps_fail_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "text": "invalid timing",
                "segments": [{"text": "invalid timing", "start": "nan", "end": 1}],
            })

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["secret"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(
            caught.exception.attempts[0].reason,
            "transcript has no timestamped segments",
        )

    async def test_invalid_json_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not-json")

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["secret"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(caught.exception.attempts[0].reason, "invalid JSON response")

    def test_from_config_uses_only_mistral_secret_and_timeout(self):
        config = SimpleNamespace(timeout=30)
        with patch.dict(os.environ, {
            "MISTRAL_API_KEYS": "direct-secret",
            "MISTRAL_STT_TIMEOUT": "45",
            "NINEROUTER_KEY": "unrelated-endpoint-key",
        }, clear=False):
            client = MistralSTTClient.from_config(config)

        self.assertEqual(client.api_keys, ["direct-secret"])
        self.assertEqual(client.timeout, 45)
        self.assertEqual(client.endpoint, MISTRAL_STT_ENDPOINT)

    def test_invalid_timeout_fails_with_safe_configuration_error(self):
        config = SimpleNamespace(timeout=30)
        with patch.dict(os.environ, {
            "MISTRAL_API_KEYS": "direct-secret",
            "MISTRAL_STT_TIMEOUT": "not-a-number",
        }, clear=False):
            with self.assertRaises(RemoteSTTError) as caught:
                MistralSTTClient.from_config(config)

        self.assertEqual(caught.exception.code, "STT_CONFIG_INVALID")
        self.assertNotIn("direct-secret", str(caught.exception))

    async def test_total_failure_keeps_reasons_without_secret(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Bearer top-secret unavailable")

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["top-secret"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir))

        error = caught.exception
        self.assertEqual(error.code, "STT_ALL_PROVIDERS_FAILED")
        self.assertEqual(len(error.attempts), 1)
        serialized = json.dumps(error.to_dict())
        self.assertNotIn("top-secret", serialized)
        self.assertIn("Bearer ***", serialized)

    async def test_transport_timeout_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["secret"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(len(caught.exception.attempts), 1)
        self.assertIsNone(caught.exception.attempts[0].status_code)

    async def test_explicit_language_fails_before_provider_call(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["secret"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir), language="es")

        self.assertEqual(caught.exception.code, "STT_LANGUAGE_UNSUPPORTED")
        self.assertEqual(calls, 0)

    def test_missing_configuration_fails_closed(self):
        with self.assertRaises(RemoteSTTError) as caught:
            MistralSTTClient(api_keys=[])
        self.assertEqual(caught.exception.code, "STT_CONFIG_INVALID")


if __name__ == "__main__":
    unittest.main()
