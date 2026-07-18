from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from open_storyline.mvp.edit_plan import (
    AssetRequest,
    ClipEditPlan,
    EditPlan,
    EditPlanError,
    EditSegment,
    LayoutSpec,
    TimeWindow,
    build_shadow_edit_plan,
)
from open_storyline.mvp.frame_sampling import FrameManifest, SampledFrame
from open_storyline.mvp.pipeline import MVPJobProcessor
from open_storyline.mvp.render import MediaInfo, RenderedShort
from open_storyline.mvp.scene_boundaries import build_scene_boundaries
from open_storyline.mvp.shorts import ShortCandidate, ShortsPlan


class FakeTranscript:
    model = "voxtral-test"
    text = "A useful explanation"
    segments = [{"start": 0, "end": 20_000, "text": text}]
    attempts = []


class FakeSTT:
    async def transcribe(self, _audio, *, language=""):
        return FakeTranscript()


class FakePlanner:
    def __init__(self, _client):
        pass

    async def plan(self, **_kwargs):
        return ShortsPlan(
            clips=[ShortCandidate(0, 20_000, "Title", "Hook", "Reason", 0.9)],
            rejected=[],
        )


class FakeRenderer:
    def __init__(self, _settings):
        pass

    def render_plan(self, *, clips, destination_dir, **_kwargs):
        path = Path(destination_dir) / "short-01.mp4"
        path.write_bytes(b"legacy-render")
        return [RenderedShort(path, None, clips[0])]


class FakeVisualUnderstanding:
    frame_manifest = {"frames": [{"id": "frame-001", "timestamp_ms": 250}]}
    regions = ()
    tracks = ()

    def to_dict(self):
        return {
            "version": "visual_understanding.v1",
            "frame_manifest": self.frame_manifest,
            "regions": [],
            "tracks": [],
            "scenes": [],
            "warnings": [],
        }


class FakeVisualPlanner:
    def __init__(self, _client):
        pass

    async def plan(self, **_kwargs):
        return FakeVisualUnderstanding()


class FakeEditPlanner:
    def __init__(self, _client):
        pass

    async def plan(self, *, shorts_plan, source_duration_ms, **_kwargs):
        return build_shadow_edit_plan(
            shorts_plan.clips,
            source_duration_ms=source_duration_ms,
        )


class FakeBlockedEditPlanner:
    def __init__(self, _client):
        pass

    async def plan(self, *, shorts_plan, source_duration_ms, **_kwargs):
        clip = shorts_plan.clips[0]
        return EditPlan(
            planner_version="agentic-editor.v1",
            source_duration_ms=source_duration_ms,
            requested_capabilities=("fit", "hard_cut", "subtitles"),
            clips=(ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                output_name="short-01.mp4",
                segments=(EditSegment(
                    id="segment-1",
                    source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                    timeline_window=TimeWindow(start_ms=0, end_ms=clip.duration_ms),
                    layout=LayoutSpec(mode="fit"),
                    reason="keep source visible",
                ),),
                asset_requests=(AssetRequest(
                    id="asset-1",
                    kind="generated_image",
                    provider="9router",
                    timeline_window=TimeWindow(start_ms=0, end_ms=2000),
                    purpose="explain a visual gap",
                    rationale="the source lacks the requested diagram",
                    prompt="a simple editorial diagram",
                ),),
            ),),
        )


class FakeRemoteClient:
    model = "cx/gpt-5.6-sol"
    last_attempts = ()


class FakeStore:
    def __init__(self, root: Path, *, server_request: dict):
        self.root = root
        self.request = server_request
        self.registered: list[tuple[str, str]] = []

    async def load(self, _job_id):
        return {
            "prompt": "make a strong short",
            "request": self.request,
            "input": {"original_filename": "source.mp4"},
        }

    async def source_path(self, _job_id):
        path = self.root / "input.mp4"
        path.write_bytes(b"source")
        return path

    def work_dir(self, _job_id):
        path = self.root / "work"
        path.mkdir(exist_ok=True)
        return path

    def output_dir(self, _job_id):
        path = self.root / "output"
        path.mkdir(exist_ok=True)
        return path

    async def update(self, _job_id, **changes):
        return changes

    async def register_artifact(self, _job_id, path, *, kind):
        self.registered.append((Path(path).name, kind))


def config(mode: str):
    return SimpleNamespace(
        remote_asr=SimpleNamespace(language=""),
        ninerouter=SimpleNamespace(),
        agentic_editing=SimpleNamespace(
            mode=mode,
            shadow_allow_blocked_plans=True,
            max_segments_per_clip=24,
            max_overlays_per_clip=12,
            max_assets_per_clip=4,
            scene_threshold=0.35,
            min_scene_duration_ms=1000,
            max_scenes=64,
            vision_frame_count=6,
            vision_frame_max_width=512,
            vision_frame_max_height=512,
            vision_frame_max_bytes=1_500_000,
        ),
        mvp=SimpleNamespace(
            frame_count=0,
            render_width=1080,
            render_height=1920,
            render_fps=30,
            render_preset="veryfast",
            render_crf=23,
        ),
        ffmpega=SimpleNamespace(enabled=False),
    )


class MVPAgenticPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_shadow_mode_registers_plans_and_keeps_legacy_render(self):
        with TemporaryDirectory() as directory:
            store = FakeStore(
                Path(directory),
                server_request={"max_clips": 1, "edit_mode": "agentic", "asset_policy": "auto"},
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("shadow")
            processor.stt = FakeSTT()
            scene_report = build_scene_boundaries(
                [],
                source_duration_ms=30_000,
                threshold=0.35,
            )
            frame_manifest = FrameManifest(
                source_duration_ms=30_000,
                source_width=1920,
                source_height=1080,
                frames=(SampledFrame(
                    id="frame-001",
                    timestamp_ms=250,
                    scene_id="scene-001",
                    width=512,
                    height=288,
                    extraction_reason="scene_opening",
                    encoded_bytes=4,
                    data_url="data:image/jpeg;base64,ZmFrZQ==",
                ),),
            )

            with (
                patch("open_storyline.mvp.pipeline.probe_media", return_value=MediaInfo(30_000, 1920, 1080, True)),
                patch("open_storyline.mvp.pipeline.extract_audio_for_stt", side_effect=lambda _source, target: target),
                patch("open_storyline.mvp.pipeline.detect_scene_boundaries", return_value=scene_report),
                patch("open_storyline.mvp.pipeline.sample_frames", return_value=frame_manifest),
                patch("open_storyline.mvp.pipeline.VisualUnderstandingPlanner", FakeVisualPlanner),
                patch("open_storyline.mvp.pipeline.AgenticEditPlanner", FakeEditPlanner),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=FakeRemoteClient()),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch("open_storyline.mvp.pipeline.CPUShortRenderer", FakeRenderer),
            ):
                result = await processor("a" * 32, store)

            names = {name for name, _kind in store.registered}
            self.assertEqual(result["clip_count"], 1)
            self.assertIn("shorts_plan.json", names)
            self.assertIn("scene_boundaries.json", names)
            self.assertIn("visual_understanding.json", names)
            self.assertIn("edit_plan.json", names)
            self.assertIn("edit_preflight.json", names)
            self.assertIn("short-01.mp4", names)
            self.assertEqual((Path(directory) / "output" / "short-01.mp4").read_bytes(), b"legacy-render")
            shorts_artifact = json.loads(
                (Path(directory) / "output" / "shorts_plan.json").read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (Path(directory) / "output" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(shorts_artifact["version"], "shorts_plan.v1")
            self.assertEqual(manifest["agentic"]["edit_planner"]["schema_version"], "edit_plan.v1")
            self.assertEqual(
                manifest["agentic"]["edit_planner"]["prompt_version"],
                "mvp-agentic-edit-plan.v1",
            )
            registered_names = [name for name, _kind in store.registered]
            self.assertLess(
                registered_names.index("shorts_plan.json"),
                registered_names.index("edit_plan.json"),
            )

    async def test_agentic_request_fails_explicitly_when_server_is_off(self):
        with TemporaryDirectory() as directory:
            store = FakeStore(
                Path(directory),
                server_request={"max_clips": 1, "edit_mode": "agentic", "asset_policy": "auto"},
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("off")
            processor.stt = FakeSTT()

            with (
                patch("open_storyline.mvp.pipeline.probe_media", return_value=MediaInfo(30_000, 1920, 1080, True)),
                patch("open_storyline.mvp.pipeline.extract_audio_for_stt", side_effect=lambda _source, target: target),
                patch("open_storyline.mvp.pipeline.extract_frame_data_urls", return_value=[]),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=object()),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
            ):
                with self.assertRaises(EditPlanError) as caught:
                    await processor("b" * 32, store)

            self.assertEqual(caught.exception.code, "AGENTIC_EDITING_DISABLED")

    async def test_shadow_policy_preserves_blocked_plan_and_legacy_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={"max_clips": 1, "edit_mode": "agentic", "asset_policy": "auto"},
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("shadow")
            processor.stt = FakeSTT()
            scene_report = build_scene_boundaries([], source_duration_ms=30_000, threshold=0.35)
            frame_manifest = FrameManifest(
                source_duration_ms=30_000,
                source_width=1920,
                source_height=1080,
                frames=(SampledFrame(
                    id="frame-001",
                    timestamp_ms=250,
                    scene_id="scene-001",
                    width=512,
                    height=288,
                    extraction_reason="scene_opening",
                    encoded_bytes=4,
                    data_url="data:image/jpeg;base64,ZmFrZQ==",
                ),),
            )

            with (
                patch("open_storyline.mvp.pipeline.probe_media", return_value=MediaInfo(30_000, 1920, 1080, True)),
                patch("open_storyline.mvp.pipeline.extract_audio_for_stt", side_effect=lambda _source, target: target),
                patch("open_storyline.mvp.pipeline.detect_scene_boundaries", return_value=scene_report),
                patch("open_storyline.mvp.pipeline.sample_frames", return_value=frame_manifest),
                patch("open_storyline.mvp.pipeline.VisualUnderstandingPlanner", FakeVisualPlanner),
                patch("open_storyline.mvp.pipeline.AgenticEditPlanner", FakeBlockedEditPlanner),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=FakeRemoteClient()),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch("open_storyline.mvp.pipeline.CPUShortRenderer", FakeRenderer),
            ):
                await processor("c" * 32, store)

            manifest = json.loads((root / "output" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["agentic"]["preflight_status"], "blocked")
            self.assertTrue(manifest["agentic"]["shadow_blocked"])
            self.assertEqual((root / "output" / "short-01.mp4").read_bytes(), b"legacy-render")


if __name__ == "__main__":
    unittest.main()
