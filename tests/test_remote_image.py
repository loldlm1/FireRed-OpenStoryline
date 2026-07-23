from types import SimpleNamespace
import base64
import json
import os
import unittest
from unittest.mock import patch

import httpx

from open_storyline.mvp.remote_image import RemoteImageCascade, RemoteImageError


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
MODEL = "cx/gpt-5.5-image"


class RemoteImageCascadeTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def catalog(*models: str) -> dict:
        return {"data": [{"id": model, "object": "model"} for model in models]}

    async def test_discovers_catalog_and_returns_first_binary_image(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=self.catalog(MODEL))
            return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})

        cascade = RemoteImageCascade(
            base_url="https://router.test/v1",
            api_key="secret",
            models=[MODEL],
            transport=httpx.MockTransport(handler),
        )
        result = await cascade.generate("An original mountain scene", size="1024x1024")

        self.assertEqual(result.model, MODEL)
        self.assertEqual(result.extension, "png")
        self.assertEqual(result.content, PNG)
        self.assertEqual(requests[0].url.path, "/v1/models/image")
        self.assertEqual(requests[1].url.params["response_format"], "binary")

    def test_rejects_unapproved_or_fallback_models(self):
        with self.assertRaises(RemoteImageError) as caught:
            RemoteImageCascade(
                base_url="https://router.test",
                api_key="secret",
                models=["cx/gpt-5.5-image", "image/fallback"],
            )
        self.assertEqual(caught.exception.code, "IMAGE_CONFIG_INVALID")

    async def test_accepts_b64_json_when_binary_mode_is_ignored(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=self.catalog(MODEL))
            return httpx.Response(200, json={
                "data": [{"b64_json": base64.b64encode(PNG).decode("ascii")}],
            })

        cascade = RemoteImageCascade(
            base_url="https://router.test",
            api_key="secret",
            models=[MODEL],
            transport=httpx.MockTransport(handler),
        )
        result = await cascade.generate("Original abstract background")

        self.assertEqual(result.content, PNG)
        self.assertEqual(result.content_type, "image/png")

    async def test_does_not_follow_provider_urls(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=self.catalog(MODEL))
            return httpx.Response(200, json={"data": [{"url": "https://untrusted.test/image.png"}]})

        cascade = RemoteImageCascade(
            base_url="https://router.test",
            api_key="secret",
            models=[MODEL],
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(RemoteImageError) as caught:
            await cascade.generate("Original scene")

        self.assertEqual(caught.exception.code, "IMAGE_ALL_PROVIDERS_FAILED")
        self.assertIn("did not contain", caught.exception.attempts[0].reason)

    async def test_rejects_configured_models_missing_from_catalog(self):
        posts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal posts
            if request.method == "POST":
                posts += 1
            return httpx.Response(200, json=self.catalog("another/model"))

        cascade = RemoteImageCascade(
            base_url="https://router.test",
            api_key="secret",
            models=[MODEL],
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(RemoteImageError) as caught:
            await cascade.generate("Original scene")

        self.assertEqual(caught.exception.code, "IMAGE_MODELS_UNAVAILABLE")
        self.assertEqual(posts, 0)

    async def test_total_failure_sanitizes_endpoint_key(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=self.catalog(MODEL))
            return httpx.Response(503, text="Bearer top-secret unavailable")

        cascade = RemoteImageCascade(
            base_url="https://router.test",
            api_key="top-secret",
            models=[MODEL],
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(RemoteImageError) as caught:
            await cascade.generate("Original scene")

        serialized = json.dumps(caught.exception.to_dict())
        self.assertEqual(caught.exception.code, "IMAGE_ALL_PROVIDERS_FAILED")
        self.assertNotIn("top-secret", serialized)
        self.assertIn("Bearer ***", serialized)

    async def test_rate_limit_fails_closed_without_another_model(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=self.catalog(MODEL))
            return httpx.Response(429, json={"error": "quota"})

        cascade = RemoteImageCascade(
            base_url="https://router.test",
            api_key="secret",
            models=[MODEL],
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(RemoteImageError) as caught:
            await cascade.generate("Original scene")

        self.assertEqual(len(caught.exception.attempts), 1)
        self.assertEqual(caught.exception.attempts[0].status_code, 429)

    def test_from_config_prefers_environment(self):
        config = SimpleNamespace(
            base_url="",
            api_key="",
            models=["config/model"],
            timeout=30,
            max_bytes=1000,
        )
        with patch.dict(os.environ, {
            "NINEROUTER_URL": "https://router.test/v1",
            "NINEROUTER_KEY": "endpoint-key",
            "OPENSTORYLINE_IMAGE_MODELS": MODEL,
            "OPENSTORYLINE_IMAGE_TIMEOUT": "45",
            "OPENSTORYLINE_IMAGE_MAX_BYTES": "12345",
        }, clear=False):
            cascade = RemoteImageCascade.from_config(config)

        self.assertEqual(cascade.models, [MODEL])
        self.assertEqual(cascade.timeout, 45)
        self.assertEqual(cascade.max_bytes, 12345)
        self.assertEqual(cascade.generations_endpoint, "https://router.test/v1/images/generations")

    def test_missing_configuration_fails_closed(self):
        with self.assertRaises(RemoteImageError) as caught:
            RemoteImageCascade(base_url="", api_key="", models=[])
        self.assertEqual(caught.exception.code, "IMAGE_CONFIG_INVALID")


if __name__ == "__main__":
    unittest.main()
