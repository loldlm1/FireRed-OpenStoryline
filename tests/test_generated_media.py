from pathlib import Path
from tempfile import TemporaryDirectory
import base64
import json
import unittest

from open_storyline.utils.generated_media import (
    ORIGINALITY_SUFFIX,
    build_original_image_prompt,
    generate_remote_media,
)
from open_storyline.utils.remote_image import RemoteImageError, RemoteImageResult


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeCascade:
    def __init__(self, fail_at: int | None = None) -> None:
        self.prompts = []
        self.fail_at = fail_at

    async def generate(self, prompt: str, *, size: str) -> RemoteImageResult:
        self.prompts.append((prompt, size))
        if self.fail_at == len(self.prompts):
            raise RemoteImageError("IMAGE_ALL_PROVIDERS_FAILED", "quota exhausted")
        return RemoteImageResult(
            model=f"image/model-{len(self.prompts)}",
            content=PNG,
            extension="png",
            content_type="image/png",
            attempts=[],
        )


class GeneratedMediaTests(unittest.IsolatedAsyncioTestCase):
    def test_builds_plan_aware_originality_prompt(self):
        prompt = build_original_image_prompt(
            "A calm editorial scene with blue light",
            orientation="portrait",
            index=0,
            count=2,
        )

        self.assertIn("vertical portrait", prompt)
        self.assertIn("Variation 1 of 2", prompt)
        self.assertIn(ORIGINALITY_SUFFIX, prompt)

        with self.assertRaises(ValueError):
            build_original_image_prompt(
                "x" * 8000,
                orientation="portrait",
                index=0,
                count=1,
            )

    async def test_saves_assets_and_provenance_manifest(self):
        cascade = FakeCascade()
        with TemporaryDirectory() as tmpdir:
            batch = await generate_remote_media(
                cascade,
                media_dir=tmpdir,
                prompt="An original rainforest macro scene",
                count=2,
                orientation="landscape",
                size="1024x1024",
            )
            manifest = json.loads(batch.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(len(batch.paths), 2)
            self.assertTrue(all(path.read_bytes() == PNG for path in batch.paths))
            self.assertEqual(batch.models, ["image/model-1", "image/model-2"])
            self.assertEqual(manifest["source"], "9router-generated")
            self.assertEqual(len(manifest["assets"]), 2)
            self.assertIn("not automatically copyright-free", manifest["rights_notice"])
            self.assertNotIn("rainforest", json.dumps(manifest))

    async def test_cleans_partial_batch_when_provider_cascade_fails(self):
        cascade = FakeCascade(fail_at=2)
        with TemporaryDirectory() as tmpdir:
            with self.assertRaises(RemoteImageError) as caught:
                await generate_remote_media(
                    cascade,
                    media_dir=tmpdir,
                    prompt="Original city scene",
                    count=3,
                    orientation="portrait",
                    size="1024x1024",
                )

            self.assertEqual(caught.exception.code, "IMAGE_ALL_PROVIDERS_FAILED")
            self.assertEqual(list(Path(tmpdir).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
