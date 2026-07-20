from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from open_storyline.mvp.frame_sampling import (
    FrameSamplingError,
    build_clip_frame_requests,
    build_frame_requests,
    sample_frames,
)
from open_storyline.mvp.ninerouter import NineRouterAttempt
from open_storyline.mvp.scene_boundaries import build_scene_boundaries
from open_storyline.mvp.visual_understanding import (
    VisualUnderstandingError,
    VisualUnderstandingPlanner,
    merge_visual_understandings,
    scope_visual_understanding,
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


class RepairingVisionClient(FakeVisionClient):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.user_prompts = []

    async def complete_json(self, *, system_prompt, user_prompt, image_data_urls=()):
        self.calls += 1
        self.last_attempts = (NineRouterAttempt(1, 200, "ok"),)
        self.user_prompts.append(json.loads(user_prompt))
        response = await super().complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_data_urls=image_data_urls,
        )
        if self.calls == 1:
            frame = self.user_prompts[-1]["attached_images_in_exact_order"][0]
            response["regions"].append({
                "id": "region-2",
                "frame_id": frame["frame_id"],
                "role": "screen",
                "bbox": {"x": 0.55, "y": 0.1, "width": 0.35, "height": 0.8},
                "confidence": 0.9,
                "salience": 0.8,
                "description": "visible screen",
            })
            response["tracks"][0]["region_ids"].append("region-2")
        return response


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

    def test_clip_sampling_covers_a_late_selected_window_deterministically(self):
        first = build_clip_frame_requests(
            self.scenes.scenes,
            source_duration_ms=30_000,
            clip_start_ms=20_000,
            clip_end_ms=29_000,
            max_frames=6,
        )
        second = build_clip_frame_requests(
            self.scenes.scenes,
            source_duration_ms=30_000,
            clip_start_ms=20_000,
            clip_end_ms=29_000,
            max_frames=6,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 6)
        self.assertTrue(all(20_000 <= item.timestamp_ms < 29_000 for item in first))
        self.assertLessEqual(first[0].timestamp_ms, 20_500)
        self.assertGreaterEqual(first[-1].timestamp_ms, 28_500)

    async def test_planner_repairs_one_invalid_mixed_role_track_response(self):
        client = RepairingVisionClient()
        manifest = self._manifest()

        understanding = await VisualUnderstandingPlanner(client).plan(
            frame_manifest=manifest,
            scene_report=self.scenes,
            editing_prompt="Create a portrait edit.",
            transcript_text="A short transcript.",
        )

        self.assertEqual(client.calls, 2)
        self.assertEqual([item.number for item in client.last_attempts], [1, 2])
        self.assertEqual(
            client.user_prompts[1]["repair_feedback"]["error_code"],
            "VISUAL_TRACK_ROLE_INVALID",
        )
        self.assertEqual(understanding.tracks[0].role, "speaker")
        self.assertEqual(understanding.tracks[0].region_ids, ("region-1",))

    def test_clip_repair_sampling_covers_each_blocked_window(self):
        windows = ((20_000, 23_000), (25_000, 29_000))
        requests = build_clip_frame_requests(
            self.scenes.scenes,
            source_duration_ms=30_000,
            clip_start_ms=20_000,
            clip_end_ms=29_000,
            max_frames=8,
            focus_windows=windows,
        )

        self.assertEqual(len(requests), 8)
        for start_ms, end_ms in windows:
            focused = [
                item for item in requests
                if start_ms <= item.timestamp_ms < end_ms
                and "repair_window" in item.reason
            ]
            self.assertEqual(len(focused), 2)

        with self.assertRaises(FrameSamplingError) as caught:
            build_clip_frame_requests(
                self.scenes.scenes,
                source_duration_ms=30_000,
                clip_start_ms=10_000,
                clip_end_ms=29_000,
                max_frames=5,
                focus_windows=(
                    (10_000, 14_000),
                    (15_000, 19_000),
                    (20_000, 24_000),
                ),
            )

        self.assertEqual(caught.exception.code, "FRAME_FOCUS_LIMIT_INVALID")

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
        self.assertEqual(
            payload["allowed_motion_values"],
            ["high", "low", "medium", "static", "unknown"],
        )
        self.assertEqual(len(payload["track_timing_constraints"]), 3)
        self.assertEqual(len(payload["scene_region_constraints"]), 2)
        serialized = json.dumps(understanding.to_dict())
        self.assertNotIn("data:image", serialized)
        self.assertNotIn("jpeg-bytes", serialized)

    async def test_clip_visual_ids_are_scoped_before_merging(self):
        manifest = self._manifest()
        first = await VisualUnderstandingPlanner(FakeVisionClient()).plan(
            frame_manifest=manifest,
            scene_report=self.scenes,
            editing_prompt="Keep the speaker visible.",
            transcript_text="A short explanation.",
        )
        second = await VisualUnderstandingPlanner(FakeVisionClient()).plan(
            frame_manifest=manifest,
            scene_report=self.scenes,
            editing_prompt="Keep the speaker visible.",
            transcript_text="A short explanation.",
        )

        scoped_first = scope_visual_understanding(first, clip_index=1)
        scoped_second = scope_visual_understanding(second, clip_index=2)
        merged = merge_visual_understandings(first, (scoped_first, scoped_second))

        self.assertTrue(scoped_first.regions[0].id.startswith("clip-01-region-"))
        self.assertTrue(scoped_second.tracks[0].id.startswith("clip-02-track-"))
        self.assertEqual(len(merged.regions), 3)
        self.assertEqual(len({region.id for region in merged.regions}), 3)

    def test_normalizes_prose_motion_to_unknown(self):
        manifest = self._manifest()
        frame = manifest.frames[0]
        raw = {
            "regions": [{
                "id": "region-1",
                "frame_id": frame.id,
                "role": "screen",
                "bbox": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.7},
                "confidence": 0.9,
            }],
            "tracks": [{
                "id": "track-1",
                "role": "screen",
                "region_ids": ["region-1"],
                "start_ms": 0,
                "end_ms": frame.timestamp_ms + 1000,
                "confidence": 0.8,
                "motion": "The camera remains fixed while the chart changes.",
                "description": "The chart remains visible.",
            }],
            "warnings": [],
        }

        understanding = validate_visual_understanding(
            raw,
            frame_manifest=manifest,
            scene_report=self.scenes,
            model="cx/gpt-5.6-sol",
        )

        self.assertEqual(understanding.tracks[0].motion, "unknown")
        self.assertEqual(
            understanding.warnings,
            ("Normalized unsupported motion values for 1 track(s).",),
        )

    def test_aligns_track_role_with_unanimous_referenced_observations(self):
        manifest = self._manifest()
        frame = manifest.frames[0]
        raw = {
            "regions": [{
                "id": "region-1",
                "frame_id": frame.id,
                "role": "speaker",
                "bbox": {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.7},
                "confidence": 0.9,
            }],
            "tracks": [{
                "id": "track-1",
                "role": "screen",
                "region_ids": ["region-1"],
                "start_ms": 0,
                "end_ms": frame.timestamp_ms + 1000,
                "confidence": 0.8,
            }],
        }

        understanding = validate_visual_understanding(
            raw,
            frame_manifest=manifest,
            scene_report=self.scenes,
            model="cx/gpt-5.6-sol",
        )

        self.assertEqual(understanding.tracks[0].role, "speaker")
        self.assertEqual(
            understanding.warnings,
            (
                "Aligned semantic roles for 1 track(s) with unanimous "
                "referenced observations.",
            ),
        )

    def test_rejects_tracks_with_genuinely_mixed_region_roles(self):
        manifest = self._manifest()
        first_frame = manifest.frames[0]
        second_frame = manifest.frames[1]
        raw = {
            "regions": [
                {
                    "id": "region-1",
                    "frame_id": first_frame.id,
                    "role": "speaker",
                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.7},
                    "confidence": 0.9,
                },
                {
                    "id": "region-2",
                    "frame_id": second_frame.id,
                    "role": "screen",
                    "bbox": {"x": 0.5, "y": 0.1, "width": 0.4, "height": 0.7},
                    "confidence": 0.9,
                },
            ],
            "tracks": [{
                "id": "track-1",
                "role": "speaker",
                "region_ids": ["region-1", "region-2"],
                "start_ms": 0,
                "end_ms": max(first_frame.timestamp_ms, second_frame.timestamp_ms) + 1000,
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

        self.assertEqual(caught.exception.code, "VISUAL_TRACK_ROLE_INVALID")

    def test_removes_cross_scene_salient_regions(self):
        manifest = self._manifest()
        first_frame = manifest.frames[0]
        other_frame = next(frame for frame in manifest.frames if frame.scene_id != first_frame.scene_id)
        raw = {
            "regions": [
                {
                    "id": "region-1",
                    "frame_id": first_frame.id,
                    "role": "speaker",
                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.7},
                    "confidence": 0.9,
                },
                {
                    "id": "region-2",
                    "frame_id": other_frame.id,
                    "role": "screen",
                    "bbox": {"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.6},
                    "confidence": 0.9,
                },
            ],
            "scenes": [{
                "scene_id": first_frame.scene_id,
                "summary": "The presenter introduces the visible subject.",
                "salient_region_ids": ["region-1", "region-2", "missing-region"],
            }],
        }

        understanding = validate_visual_understanding(
            raw,
            frame_manifest=manifest,
            scene_report=self.scenes,
            model="cx/gpt-5.6-sol",
        )

        self.assertEqual(understanding.scenes[0].salient_region_ids, ("region-1",))
        self.assertEqual(
            understanding.warnings,
            (
                "Removed 2 invalid salient region reference(s) from scene summaries.",
            ),
        )

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

    async def test_normalizes_track_windows_that_do_not_contain_observations(self):
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
        understanding = validate_visual_understanding(
            raw,
            frame_manifest=manifest,
            scene_report=self.scenes,
            model="cx/gpt-5.6-sol",
        )
        scene = next(scene for scene in self.scenes.scenes if scene.id == frame.scene_id)
        self.assertEqual(understanding.tracks[0].start_ms, scene.start_ms)
        self.assertEqual(understanding.tracks[0].end_ms, scene.end_ms)
        self.assertEqual(
            understanding.warnings,
            ("Normalized invalid timing windows for 1 track(s).",),
        )


if __name__ == "__main__":
    unittest.main()
