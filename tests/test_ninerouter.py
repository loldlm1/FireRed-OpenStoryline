from types import SimpleNamespace
import json
import os
import unittest
from unittest.mock import patch

import httpx

from open_storyline.mvp.ninerouter import NineRouterClient, NineRouterError


class NineRouterClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_sol_medium_and_returns_structured_output(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "{\"clips\": [{\"start\": 1}]}"}}],
            })

        client = NineRouterClient(
            base_url="https://router.test/v1",
            api_key="secret",
            model="cx/gpt-5.6-sol",
            reasoning_effort="medium",
            transport=httpx.MockTransport(handler),
        )
        result = await client.complete_json(
            system_prompt="plan",
            user_prompt="make shorts",
            image_data_urls=["data:image/jpeg;base64,ZmFrZQ=="],
        )

        self.assertEqual(result["clips"][0]["start"], 1)
        self.assertEqual(captured["model"], "cx/gpt-5.6-sol")
        self.assertEqual(captured["reasoning_effort"], "medium")
        self.assertEqual(captured["response_format"], {"type": "json_object"})
        self.assertEqual(captured["messages"][1]["content"][1]["type"], "image_url")

    async def test_accepts_fenced_json_and_content_parts(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": [
                    {"type": "text", "text": "```json\n{\"ok\": true}\n```"},
                ]}}],
            })

        client = NineRouterClient(
            base_url="https://router.test",
            api_key="secret",
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(
            await client.complete_json(system_prompt="system", user_prompt="user"),
            {"ok": True},
        )

    async def test_invalid_json_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "not json"}}],
            })

        client = NineRouterClient(
            base_url="https://router.test",
            api_key="secret",
            max_retries=1,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(NineRouterError) as caught:
            await client.complete_json(system_prompt="system", user_prompt="user")

        self.assertEqual(caught.exception.code, "NINEROUTER_RESPONSE_INVALID")
        self.assertEqual(len(caught.exception.attempts), 2)

    async def test_error_reasons_do_not_expose_key(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Bearer top-secret rejected")

        client = NineRouterClient(
            base_url="https://router.test",
            api_key="top-secret",
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(NineRouterError) as caught:
            await client.complete_json(system_prompt="system", user_prompt="user")

        serialized = json.dumps(caught.exception.to_dict())
        self.assertNotIn("top-secret", serialized)
        self.assertIn("Bearer ***", serialized)
        self.assertEqual(len(caught.exception.attempts), 1)

    def test_environment_overrides_config(self):
        config = SimpleNamespace(
            base_url="https://config.test",
            api_key="config-key",
            model="config-model",
            reasoning_effort="low",
            timeout=30,
            max_retries=0,
        )
        with patch.dict(os.environ, {
            "NINEROUTER_URL": "https://env.test",
            "NINEROUTER_KEY": "env-key",
            "OPENSTORYLINE_LLM_MODEL": "cx/gpt-5.6-sol",
            "OPENSTORYLINE_REASONING_EFFORT": "medium",
        }, clear=False):
            client = NineRouterClient.from_config(config)

        self.assertEqual(client.base_url, "https://env.test")
        self.assertEqual(client.api_key, "env-key")
        self.assertEqual(client.model, "cx/gpt-5.6-sol")
        self.assertEqual(client.reasoning_effort, "medium")

    def test_missing_configuration_fails_closed(self):
        with self.assertRaises(NineRouterError) as caught:
            NineRouterClient(base_url="", api_key="", model="")
        self.assertEqual(caught.exception.code, "NINEROUTER_CONFIG_INVALID")

    def test_rejects_unapproved_text_or_vision_model(self):
        with self.assertRaises(NineRouterError) as caught:
            NineRouterClient(
                base_url="https://router.test",
                api_key="secret",
                model="another/model",
            )
        self.assertEqual(caught.exception.code, "NINEROUTER_CONFIG_INVALID")


if __name__ == "__main__":
    unittest.main()
