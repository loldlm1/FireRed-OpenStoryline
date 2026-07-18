from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import base64
import json
import os
import unittest
from unittest.mock import patch

from open_storyline.mvp.assets import (
    AssetResolutionError,
    generated_asset_server_cap,
    generated_asset_size,
    generated_assets_enabled,
    resolve_generated_assets,
)
from open_storyline.mvp.edit_plan import (
    AssetRequest,
    ClipEditPlan,
    EditPlan,
    EditSegment,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
)
from open_storyline.utils.remote_image import (
    ImageAttempt,
    RemoteImageError,
    RemoteImageResult,
)


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def edit_plan(asset_count: int) -> EditPlan:
    overlays = []
    requests = []
    for index in range(asset_count):
        asset_id = f"asset-{index + 1}"
        start = 1000 + index * 2500
        window = TimeWindow(start_ms=start, end_ms=start + 1500)
        overlays.append(OverlaySpec(
            id=f"overlay-{index + 1}",
            kind="image",
            timeline_window=window,
            asset_id=asset_id,
            position="top_right",
        ))
        requests.append(AssetRequest(
            id=asset_id,
            kind="generated_image",
            provider="9router",
            timeline_window=window,
            visual_gap="the source has no supporting visual for this idea",
            purpose="clarify the spoken concept",
            rationale="a short original still prevents a long visual hold",
            prompt=f"an original editorial illustration number {index + 1}",
        ))
    return EditPlan(
        planner_version="test.v1",
        source_duration_ms=10_000,
        requested_capabilities=("fit", "hard_cut", "image_overlay", "subtitles"),
        clips=(ClipEditPlan(
            clip_index=1,
            source_window=TimeWindow(start_ms=0, end_ms=10_000),
            output_name="short-01.mp4",
            segments=(EditSegment(
                id="segment-1",
                source_window=TimeWindow(start_ms=0, end_ms=10_000),
                timeline_window=TimeWindow(start_ms=0, end_ms=10_000),
                layout=LayoutSpec(mode="fit"),
                overlays=tuple(overlays),
                reason="use source video and only the requested supporting layers",
            ),),
            asset_requests=tuple(requests),
        ),),
    )


class FakeCascade:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, str]] = []

    async def generate(self, prompt: str, *, size: str) -> RemoteImageResult:
        self.calls.append((prompt, size))
        if self.fail_at == len(self.calls):
            raise RemoteImageError(
                "IMAGE_ALL_PROVIDERS_FAILED",
                "quota exhausted",
                [ImageAttempt("cx/gpt-5.5-image", False, 429, "quota exhausted")],
            )
        return RemoteImageResult(
            model="cx/gpt-5.5-image",
            content=PNG,
            extension="png",
            content_type="image/png",
            attempts=[ImageAttempt("cx/gpt-5.5-image", True, 200, "ok")],
        )


class GeneratedAssetTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_without_requests_makes_zero_provider_calls(self):
        cascade = FakeCascade()
        with TemporaryDirectory() as tmpdir:
            result = await resolve_generated_assets(
                edit_plan(0),
                output_dir=tmpdir,
                asset_policy="auto",
                max_generated_assets_per_clip=2,
                cascade=cascade,
                size="1024x1024",
            )

            self.assertEqual(cascade.calls, [])
            self.assertEqual(result.paths, {})
            self.assertEqual(result.manifest["status"], "no_requests")
            self.assertEqual(result.provider_call_count, 0)

    async def test_resolves_job_scoped_asset_with_provenance_and_rights_notice(self):
        cascade = FakeCascade()
        with TemporaryDirectory() as tmpdir:
            result = await resolve_generated_assets(
                edit_plan(1),
                output_dir=tmpdir,
                asset_policy="auto",
                max_generated_assets_per_clip=1,
                cascade=cascade,
                size="1024x1024",
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(len(cascade.calls), 1)
            self.assertIn("Do not deliberately reproduce", cascade.calls[0][0])
            self.assertEqual(result.paths["asset-1"].read_bytes(), PNG)
            self.assertEqual(manifest["version"], "asset_manifest.v1")
            self.assertEqual(manifest["requested_count"], 1)
            self.assertEqual(manifest["resolved_count"], 1)
            self.assertEqual(manifest["provider_call_count"], 1)
            self.assertEqual(manifest["assets"][0]["model"], "cx/gpt-5.5-image")
            self.assertEqual(manifest["assets"][0]["content_type"], "image/png")
            self.assertIn("not automatically copyright-free", manifest["rights_notice"])
            serialized = json.dumps(manifest)
            self.assertNotIn("original editorial illustration number", serialized)

    async def test_partial_failure_cleans_all_generated_media_and_fails_closed(self):
        cascade = FakeCascade(fail_at=2)
        with TemporaryDirectory() as tmpdir:
            with self.assertRaises(AssetResolutionError) as caught:
                await resolve_generated_assets(
                    edit_plan(2),
                    output_dir=tmpdir,
                    asset_policy="auto",
                    max_generated_assets_per_clip=2,
                    cascade=cascade,
                    size="1024x1024",
                )

            self.assertEqual(caught.exception.code, "IMAGE_ALL_PROVIDERS_FAILED")
            self.assertEqual(len(cascade.calls), 2)
            self.assertEqual(list(Path(tmpdir).iterdir()), [])
            self.assertNotIn("prompt", json.dumps(caught.exception.to_dict()).lower())

    async def test_server_cap_wins_before_provider_execution(self):
        cascade = FakeCascade()
        with TemporaryDirectory() as tmpdir:
            with self.assertRaises(AssetResolutionError) as caught:
                await resolve_generated_assets(
                    edit_plan(2),
                    output_dir=tmpdir,
                    asset_policy="auto",
                    max_generated_assets_per_clip=1,
                    cascade=cascade,
                    size="1024x1024",
                )

            self.assertEqual(caught.exception.code, "ASSET_LIMIT_EXCEEDED")
            self.assertEqual(cascade.calls, [])

    def test_environment_overrides_are_strict_and_bounded(self):
        config = SimpleNamespace(
            generated_assets_enabled=False,
            max_generated_assets_per_clip=2,
            size="1024x1024",
        )
        with patch.dict(os.environ, {
            "OPENSTORYLINE_GENERATED_ASSETS_ENABLED": "true",
            "OPENSTORYLINE_MAX_GENERATED_ASSETS_PER_CLIP": "3",
            "OPENSTORYLINE_IMAGE_SIZE": "1024x1536",
        }):
            self.assertTrue(generated_assets_enabled(config))
            self.assertEqual(generated_asset_server_cap(config), 3)
            self.assertEqual(generated_asset_size(config), "1024x1536")

        with patch.dict(os.environ, {"OPENSTORYLINE_GENERATED_ASSETS_ENABLED": "maybe"}):
            with self.assertRaises(AssetResolutionError):
                generated_assets_enabled(config)


if __name__ == "__main__":
    unittest.main()
