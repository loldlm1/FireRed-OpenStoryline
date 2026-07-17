from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import asyncio
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

    @staticmethod
    def success_response() -> httpx.Response:
        return httpx.Response(200, json={
            "text": "Hola mundo",
            "segments": [
                {"id": 1, "text": " Hola mundo ", "start": 1.25, "end": 2.5},
            ],
        })

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
            return self.success_response()

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
        self.assertEqual(result.attempts[0].key_ordinal, "key_1")
        self.assertEqual(result.attempts[0].category, "success")

    async def test_rate_limit_cools_first_key_and_fails_over_to_second(self):
        calls: list[str] = []
        clock = [100.0]

        def handler(request: httpx.Request) -> httpx.Response:
            authorization = request.headers["authorization"]
            calls.append(authorization)
            if authorization == "Bearer first":
                return httpx.Response(429, headers={"Retry-After": "120"})
            return self.success_response()

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["first", "second"],
                transport=httpx.MockTransport(handler),
                monotonic=lambda: clock[0],
            )
            audio = self.audio_file(tmpdir)
            result = await client.transcribe(audio)
            second_result = await client.transcribe(audio)

        self.assertEqual(calls, ["Bearer first", "Bearer second", "Bearer second"])
        self.assertEqual(result.attempts[0].category, "rate_limited")
        self.assertEqual(result.attempts[0].retry_after_seconds, 120)
        self.assertEqual(result.attempts[1].key_ordinal, "key_2")
        self.assertFalse(second_result.attempts[0].request_sent)
        self.assertEqual(second_result.attempts[0].category, "cooldown")

    async def test_invalid_key_is_disabled_and_next_key_succeeds(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            authorization = request.headers["authorization"]
            calls.append(authorization)
            if authorization == "Bearer invalid":
                return httpx.Response(401, text="invalid key")
            return self.success_response()

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["invalid", "valid"],
                transport=httpx.MockTransport(handler),
            )
            audio = self.audio_file(tmpdir)
            first = await client.transcribe(audio)
            second = await client.transcribe(audio)

        self.assertEqual(calls, ["Bearer invalid", "Bearer valid", "Bearer valid"])
        self.assertEqual(first.attempts[0].category, "auth")
        self.assertEqual(second.attempts[0].category, "disabled")
        self.assertFalse(second.attempts[0].request_sent)

    async def test_all_rate_limited_keys_stop_after_one_pass(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(429, headers={"Retry-After": "invalid"})

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["first", "second"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(calls, 2)
        self.assertEqual(caught.exception.category, "rate_limited")
        self.assertEqual([item.retry_after_seconds for item in caught.exception.attempts], [60, 60])

    async def test_bad_media_request_does_not_fan_out_across_keys(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(400, text="unsupported media")

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["first", "second"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(calls, 1)
        self.assertEqual(caught.exception.category, "input_invalid")

    async def test_transient_upstream_failure_retries_once_then_uses_next_key(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            authorization = request.headers["authorization"]
            calls.append(authorization)
            if authorization == "Bearer first":
                return httpx.Response(503, text="temporarily unavailable")
            return self.success_response()

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["first", "second"],
                transport=httpx.MockTransport(handler),
            )
            result = await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(calls, ["Bearer first", "Bearer first", "Bearer second"])
        self.assertEqual([item.category for item in result.attempts], ["upstream", "upstream", "success"])

    async def test_request_pacing_respects_the_observed_free_tier_rps(self):
        calls = 0
        clock = [10.0]
        sleeps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503)
            return self.success_response()

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)
            clock[0] += delay

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["secret"],
                transport=httpx.MockTransport(handler),
                monotonic=lambda: clock[0],
                minimum_request_interval=1.0,
                sleep=fake_sleep,
            )
            await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(sleeps, [1.0])

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
        self.assertEqual(caught.exception.category, "contract_invalid")

    async def test_invalid_contract_does_not_fan_out_across_keys(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"text": "no timestamps"})

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["first", "second"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError):
                await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(calls, 1)

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
        self.assertEqual(client.minimum_request_interval, 1.0)

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
            return httpx.Response(503, text="Bearer top-secret leaked-body-marker")

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["top-secret"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir))

        error = caught.exception
        self.assertEqual(error.code, "STT_ALL_PROVIDERS_FAILED")
        self.assertEqual(len(error.attempts), 2)
        serialized = json.dumps(error.to_dict())
        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("leaked-body-marker", serialized)
        self.assertIn("provider temporarily unavailable", serialized)

    async def test_transport_timeout_fails_closed(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectTimeout("timed out", request=request)

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["secret"],
                transport=httpx.MockTransport(handler),
            )
            with self.assertRaises(RemoteSTTError) as caught:
                await client.transcribe(self.audio_file(tmpdir))

        self.assertEqual(calls, 2)
        self.assertEqual(len(caught.exception.attempts), 2)
        self.assertIsNone(caught.exception.attempts[0].status_code)

    async def test_concurrent_calls_are_serialized_and_share_cooldowns(self):
        active = 0
        max_active = 0
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            authorization = request.headers["authorization"]
            calls.append(authorization)
            await asyncio.sleep(0.01)
            active -= 1
            if authorization == "Bearer first":
                return httpx.Response(429, headers={"Retry-After": "60"})
            return self.success_response()

        with TemporaryDirectory() as tmpdir:
            client = MistralSTTClient(
                api_keys=["first", "second"],
                transport=httpx.MockTransport(handler),
            )
            audio = self.audio_file(tmpdir)
            await asyncio.gather(client.transcribe(audio), client.transcribe(audio))

        self.assertEqual(max_active, 1)
        self.assertEqual(calls.count("Bearer first"), 1)
        self.assertEqual(calls.count("Bearer second"), 2)

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
