from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import os
import unittest
from unittest.mock import patch

import httpx

from open_storyline.mvp.edit_plan import AssetRequest, TimeWindow
from open_storyline.mvp.stock import (
    PEXELS_LICENSE_URL,
    PexelsClient,
    PexelsError,
    pexels_server_cap,
)


JPEG = b"\xff\xd8\xff\xe0mock-jpeg"
MP4 = b"\x00\x00\x00\x18ftypmp42mock-mp4"


def stock_request(kind: str = "stock_image") -> AssetRequest:
    return AssetRequest(
        id="stock-1",
        kind=kind,
        provider="pexels",
        timeline_window=TimeWindow(start_ms=1000, end_ms=4000),
        visual_gap="the source has no neutral supporting visual",
        purpose="support the spoken example",
        rationale="a brief cutaway avoids an unrelated source hold",
        prompt="remote teamwork planning",
        orientation="portrait",
    )


def pexels_config(**overrides):
    values = {
        "pexels_enabled": True,
        "max_stock_assets_per_clip": 2,
        "pexels_license_reviewed_at": datetime.now(timezone.utc).date().isoformat(),
        "pexels_search_limit": 8,
        "pexels_timeout": 30.0,
        "pexels_max_retries": 2,
        "pexels_max_bytes": 80 * 1024 * 1024,
        "pexels_max_video_duration_seconds": 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class PexelsAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_search_download_and_provenance_are_bounded(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.host == "api.pexels.com":
                self.assertEqual(request.headers["Authorization"], "test-key")
                self.assertEqual(request.url.path, "/v1/search")
                self.assertEqual(request.url.params["per_page"], "8")
                return httpx.Response(200, json={
                    "photos": [{
                        "id": 42,
                        "width": 1200,
                        "height": 1800,
                        "url": "https://www.pexels.com/photo/example-42/",
                        "photographer": "Example Creator",
                        "photographer_url": "https://www.pexels.com/@example",
                        "src": {
                            "large2x": "https://images.pexels.com/photos/42/example.jpeg",
                        },
                    }],
                })
            return httpx.Response(
                200,
                content=JPEG,
                headers={"content-type": "image/jpeg", "content-length": str(len(JPEG))},
            )

        asset = await PexelsClient(
            api_key="test-key",
            transport=httpx.MockTransport(handler),
        ).acquire(stock_request())

        self.assertEqual(len(calls), 2)
        self.assertEqual(asset.content, JPEG)
        self.assertEqual(asset.extension, "jpg")
        provenance = asset.provenance()
        self.assertEqual(provenance["pexels_asset_id"], 42)
        self.assertEqual(provenance["creator"], "Example Creator")
        self.assertEqual(provenance["license_url"], PEXELS_LICENSE_URL)
        self.assertEqual(provenance["selected_file"]["bytes"], len(JPEG))

    async def test_video_uses_official_endpoint_and_rejects_excess_duration(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.pexels.com":
                self.assertEqual(request.url.path, "/videos/search")
                return httpx.Response(200, json={
                    "videos": [
                        {
                            "id": 10,
                            "width": 1080,
                            "height": 1920,
                            "duration": 90,
                            "url": "https://www.pexels.com/video/too-long-10/",
                            "user": {"name": "Long", "url": "https://www.pexels.com/@long"},
                            "video_files": [],
                        },
                        {
                            "id": 11,
                            "width": 1080,
                            "height": 1920,
                            "duration": 12,
                            "url": "https://www.pexels.com/video/example-11/",
                            "user": {"name": "Example", "url": "https://www.pexels.com/@example"},
                            "video_files": [{
                                "file_type": "video/mp4",
                                "width": 1080,
                                "height": 1920,
                                "link": "https://videos.pexels.com/video-files/11/example.mp4",
                            }],
                        },
                    ],
                })
            return httpx.Response(
                200,
                content=MP4,
                headers={"content-type": "video/mp4", "content-length": str(len(MP4))},
            )

        asset = await PexelsClient(
            api_key="test-key",
            max_video_duration_seconds=60,
            transport=httpx.MockTransport(handler),
        ).acquire(stock_request("stock_video"))

        self.assertEqual(asset.provider_id, 11)
        self.assertEqual(asset.duration_seconds, 12)
        self.assertEqual(asset.extension, "mp4")

    async def test_search_retries_transient_failures_without_exposing_key(self):
        search_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal search_calls
            if request.url.host == "api.pexels.com":
                search_calls += 1
                if search_calls == 1:
                    return httpx.Response(500, text="temporary provider failure")
                return httpx.Response(401, text="Bearer test-key rejected")
            raise AssertionError("download must not run")

        client = PexelsClient(
            api_key="test-key",
            max_retries=2,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(PexelsError) as caught:
            await client.acquire(stock_request())

        self.assertEqual(caught.exception.code, "PEXELS_SEARCH_FAILED")
        self.assertEqual(search_calls, 2)
        serialized = json.dumps(caught.exception.to_dict())
        self.assertNotIn("test-key", serialized)
        self.assertNotIn("temporary provider failure", serialized)
        self.assertNotIn("remote teamwork planning", serialized)

    async def test_redirects_mime_and_media_bytes_fail_closed(self):
        def redirect_handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.pexels.com":
                return httpx.Response(200, json={
                    "photos": [{
                        "id": 42,
                        "width": 1200,
                        "height": 1800,
                        "url": "https://www.pexels.com/photo/example-42/",
                        "photographer": "Example Creator",
                        "photographer_url": "https://www.pexels.com/@example",
                        "src": {"large": "https://images.pexels.com/photos/42/example.jpeg"},
                    }],
                })
            return httpx.Response(302, headers={"location": "https://evil.invalid/file.jpg"})

        with self.assertRaises(PexelsError) as caught:
            await PexelsClient(
                api_key="test-key",
                transport=httpx.MockTransport(redirect_handler),
            ).acquire(stock_request())
        self.assertEqual(caught.exception.code, "PEXELS_DOWNLOAD_REDIRECT_INVALID")

        def mime_handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.pexels.com":
                return httpx.Response(200, json={
                    "photos": [{
                        "id": 42,
                        "width": 1200,
                        "height": 1800,
                        "url": "https://www.pexels.com/photo/example-42/",
                        "photographer": "Example Creator",
                        "photographer_url": "https://www.pexels.com/@example",
                        "src": {"large": "https://images.pexels.com/photos/42/example.jpeg"},
                    }],
                })
            return httpx.Response(200, content=b"not-an-image", headers={"content-type": "text/html"})

        with self.assertRaises(PexelsError) as caught:
            await PexelsClient(
                api_key="test-key",
                transport=httpx.MockTransport(mime_handler),
            ).acquire(stock_request())
        self.assertEqual(caught.exception.code, "PEXELS_MEDIA_TYPE_INVALID")

    def test_enablement_requires_secret_current_review_and_bounded_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PexelsError) as caught:
                PexelsClient.from_config(pexels_config(pexels_enabled=False))
            self.assertEqual(caught.exception.code, "PEXELS_DISABLED")

        stale = (datetime.now(timezone.utc).date() - timedelta(days=181)).isoformat()
        with patch.dict(os.environ, {"PEXELS_API_KEY": "test-key"}, clear=True):
            with self.assertRaises(PexelsError) as caught:
                PexelsClient.from_config(pexels_config(pexels_license_reviewed_at=stale))
            self.assertEqual(caught.exception.code, "PEXELS_LICENSE_REVIEW_REQUIRED")

        with patch.dict(os.environ, {
            "PEXELS_API_KEY": "test-key",
            "OPENSTORYLINE_PEXELS_ENABLED": "true",
            "OPENSTORYLINE_MAX_STOCK_ASSETS_PER_CLIP": "3",
            "OPENSTORYLINE_PEXELS_SEARCH_LIMIT": "7",
            "OPENSTORYLINE_PEXELS_MAX_BYTES": "2000000",
        }, clear=True):
            client = PexelsClient.from_config(pexels_config())
            self.assertEqual(pexels_server_cap(pexels_config()), 3)
            self.assertEqual(client.search_limit, 7)
            self.assertEqual(client.max_bytes, 2_000_000)


if __name__ == "__main__":
    unittest.main()
