from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from open_storyline.mvp.frame_sampling import build_frame_requests, sample_frames
from open_storyline.mvp.scene_boundaries import build_scene_boundaries
from open_storyline.mvp.visual_understanding import (
    VisualUnderstandingError,
    VisualUnderstandingPlanner,
    validate_visual_understanding,
)


class FakeVisionClient:
    model = "cx/gpt-5.6-sol"

    def __init__(self):
        self.user_prompt = ""
        self.images = ()

    async def complete_json(self, *, system_prompt, user_prompt, image_data_urls=()):
        self.user_prompt = user_prompt
        self.images = tuple(image_data_urls)
        frame = json.loads(user_prompt)["attached_images_in_exact_order"][0]
        return {
            "regions": [{
                "id": "region-1",
                "frame_id": frame["frame_id"],
                "role": "speaker",
                "bbox": {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.8},
                "confidence": 0.9,
                "salience": 0.8,
                "description": "visible presenter",
            }],
            "tracks": [{
                "id": "track-1",
                "role": "speaker",
                "region_ids": ["region-1"],
                "start_ms": 0,
                "end_ms": frame["timestamp_ms"] + 1000,
                "confidence": 0.8,
                "motion": "low",
                "description": "presenter remains visible",
            }],
            "scenes": [{
                "scene_id": frame["scene_id"],
                "summary": "Presenter explains the topic.",
                "salient_region_ids": ["region-1"],
            }],
            "warnings": [],
        }


class VisualUnderstandingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scenes = build_scene_boundaries(
            [10_000],
            source_duration_ms=30_000,
            threshold=0.35,
        )

    def _manifest(self):
        result = SimpleNamespace(returncode=0, stdout=b"jpeg-bytes", stderr=b"")
        with patch("open_storyline.mvp.frame_sampling.subprocess.run", return_value=result):
            return sample_frames(
                "/tmp/source.mp4",
                scene_report=self.scenes,
                source_width=1920,
                source_height=1080,
                max_frames=6,
                max_width=512,
                max_height=512,
            )

    def test_frame_sampling_is_bounded_timestamped_and_private(self):
        requests = build_frame_requests(
            self.scenes.scenes,
            source_duration_ms=30_000,
            max_frames=5,
        )
        self.assertLessEqual(len(requests), 5)
        self.assertEqual(list(requests), sorted(requests, key=lambda item: item.timestamp_ms))

        manifest = self._manifest()
        serialized = json.dumps(manifest.to_dict())
        self.assertEqual([frame.id for frame in manifest.frames], [f"frame-{i:03d}" for i in range(1, 7)])
        self.assertNotIn("data:image", serialized)
        self.assertNotIn("/tmp/source.mp4", serialized)
        self.assertTrue(all(frame.width == 512 and frame.height == 288 for frame in manifest.frames))

    async def test_prompt_maps_attached_images_to_exact_frame_order(self):
        manifest = self._manifest()
        client = FakeVisionClient()
        understanding = await VisualUnderstandingPlanner(client).plan(
            frame_manifest=manifest,
            scene_report=self.scenes,
            editing_prompt="Prioritize the relevant visible action.",
            transcript_text="A short explanation.",
        )

        payload = json.loads(client.user_prompt)
        self.assertEqual(
            [item["frame_id"] for item in payload["attached_images_in_exact_order"]],
            [frame.id for frame in manifest.frames],
        )
        self.assertEqual(client.images, manifest.image_data_urls)
        self.assertEqual(understanding.regions[0].role, "speaker")
        serialized = json.dumps(understanding.to_dict())
        self.assertNotIn("data:image", serialized)
        self.assertNotIn("jpeg-bytes", serialized)

    def test_rejects_unknown_frames_and_invalid_boxes(self):
        manifest = self._manifest()
        raw = {
            "regions": [{
                "id": "bad",
                "frame_id": "frame-unknown",
                "role": "screen",
                "bbox": {"x": 0.8, "y": 0.2, "width": 0.4, "height": 0.5},
                "confidence": 0.8,
            }],
        }
        with self.assertRaises(VisualUnderstandingError) as caught:
            validate_visual_understanding(
                raw,
                frame_manifest=manifest,
                scene_report=self.scenes,
                model="cx/gpt-5.6-sol",
            )
        self.assertEqual(caught.exception.code, "VISUAL_RESPONSE_INVALID")

        raw["regions"][0]["bbox"] = {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.5}
        with self.assertRaises(VisualUnderstandingError) as caught:
            validate_visual_understanding(
                raw,
                frame_manifest=manifest,
                scene_report=self.scenes,
                model="cx/gpt-5.6-sol",
            )
        self.assertEqual(caught.exception.code, "VISUAL_FRAME_UNKNOWN")

    async def test_rejects_track_windows_that_do_not_contain_observations(self):
        manifest = self._manifest()
        frame = manifest.frames[0]
        raw = {
            "regions": [{
                "id": "region-1",
                "frame_id": frame.id,
                "role": "speaker",
                "bbox": {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.5},
                "confidence": 0.8,
            }],
            "tracks": [{
                "id": "track-1",
                "role": "speaker",
                "region_ids": ["region-1"],
                "start_ms": frame.timestamp_ms + 1,
                "end_ms": frame.timestamp_ms + 1000,
                "confidence": 0.8,
            }],
        }
        with self.assertRaises(VisualUnderstandingError) as caught:
            validate_visual_understanding(
                raw,
                frame_manifest=manifest,
                scene_report=self.scenes,
                model="cx/gpt-5.6-sol",
            )
        self.assertEqual(caught.exception.code, "VISUAL_TRACK_TIMING_INVALID")


if __name__ == "__main__":
    unittest.main()
