from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import base64
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
    OverlaySpec,
    TimeWindow,
    build_shadow_edit_plan,
)
from open_storyline.mvp.frame_sampling import FrameManifest, SampledFrame
from open_storyline.mvp.pipeline import MVPJobProcessor
from open_storyline.mvp.render import AgenticRenderResult, MediaInfo, RenderedShort
from open_storyline.mvp.scene_boundaries import build_scene_boundaries
from open_storyline.mvp.shorts import ShortCandidate, ShortsPlan
from open_storyline.utils.remote_image import ImageAttempt, RemoteImageResult


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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


class FakeAgenticRenderer:
    def __init__(self, _settings):
        pass

    def render_plan(self, *, selected_clips, destination_dir, **_kwargs):
        path = Path(destination_dir) / "short-01.mp4"
        path.write_bytes(b"agentic-render")
        return AgenticRenderResult(
            rendered=(RenderedShort(path, None, selected_clips[0]),),
            execution={
                "version": "render_execution.v1",
                "summary": {"clips": 1, "encodes": 1, "fallbacks": 0},
                "clips": [{"video": "short-01.mp4", "encode_count": 1}],
            },
        )


class FakeAssetAwareRenderer(FakeAgenticRenderer):
    resolved_assets = {}

    def render_plan(self, *, resolved_assets, **kwargs):
        type(self).resolved_assets = dict(resolved_assets)
        return super().render_plan(**kwargs)


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
            requested_capabilities=("fit", "hard_cut", "image_overlay", "subtitles"),
            clips=(ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                output_name="short-01.mp4",
                segments=(EditSegment(
                    id="segment-1",
                    source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                    timeline_window=TimeWindow(start_ms=0, end_ms=clip.duration_ms),
                    layout=LayoutSpec(mode="fit"),
                    overlays=(OverlaySpec(
                        id="asset-overlay",
                        kind="image",
                        timeline_window=TimeWindow(start_ms=0, end_ms=2000),
                        asset_id="asset-1",
                        position="top_right",
                    ),),
                    reason="keep source visible",
                ),),
                asset_requests=(AssetRequest(
                    id="asset-1",
                    kind="generated_image",
                    provider="9router",
                    timeline_window=TimeWindow(start_ms=0, end_ms=2000),
                    visual_gap="the source lacks the requested diagram",
                    purpose="explain a visual gap",
                    rationale="the source lacks the requested diagram",
                    prompt="a simple editorial diagram",
                ),),
            ),),
        )


class FakeGeneratedEditPlanner:
    def __init__(self, _client):
        pass

    async def plan(self, *, shorts_plan, source_duration_ms, **_kwargs):
        clip = shorts_plan.clips[0]
        asset_window = TimeWindow(start_ms=1000, end_ms=3000)
        return EditPlan(
            planner_version="agentic-editor.v1",
            source_duration_ms=source_duration_ms,
            requested_capabilities=("fit", "hard_cut", "image_overlay", "subtitles"),
            clips=(ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                output_name="short-01.mp4",
                segments=(EditSegment(
                    id="segment-1",
                    source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                    timeline_window=TimeWindow(start_ms=0, end_ms=clip.duration_ms),
                    layout=LayoutSpec(mode="fit"),
                    overlays=(OverlaySpec(
                        id="asset-overlay",
                        kind="image",
                        timeline_window=asset_window,
                        asset_id="asset-1",
                        position="top_right",
                    ),),
                    reason="insert one justified supporting still",
                ),),
                asset_requests=(AssetRequest(
                    id="asset-1",
                    kind="generated_image",
                    provider="9router",
                    timeline_window=asset_window,
                    visual_gap="the source contains no supporting diagram",
                    purpose="clarify the explanation",
                    rationale="a brief original diagram closes the visual gap",
                    prompt="an original editorial diagram with simple shapes",
                ),),
            ),),
        )


class FakeAssetCascade:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, *, size):
        self.calls.append((prompt, size))
        return RemoteImageResult(
            model="cx/gpt-5.5-image",
            content=PNG,
            extension="png",
            content_type="image/png",
            attempts=[ImageAttempt("cx/gpt-5.5-image", True, 200, "ok")],
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


def config(mode: str, *, generated_assets: bool = False):
    return SimpleNamespace(
        remote_asr=SimpleNamespace(language=""),
        ninerouter=SimpleNamespace(),
        agentic_editing=SimpleNamespace(
            mode=mode,
            shadow_allow_blocked_plans=True,
            max_segments_per_clip=24,
            max_overlays_per_clip=12,
            max_assets_per_clip=4,
            generated_assets_enabled=generated_assets,
            max_generated_assets_per_clip=2,
            scene_threshold=0.35,
            min_scene_duration_ms=1000,
            max_scenes=64,
            vision_frame_count=6,
            vision_frame_max_width=512,
            vision_frame_max_height=512,
            vision_frame_max_bytes=1_500_000,
            crop_hysteresis_ratio=0.03,
            crop_smoothing_alpha=0.65,
            max_crop_velocity_ratio_per_second=0.45,
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
        remote_image=SimpleNamespace(size="1024x1024"),
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
                "mvp-agentic-edit-plan.v2",
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

    async def test_render_mode_uses_agentic_renderer_and_registers_execution(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={"max_clips": 1, "edit_mode": "agentic", "asset_policy": "off"},
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render")
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
                patch("open_storyline.mvp.pipeline.AgenticEditPlanner", FakeEditPlanner),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=FakeRemoteClient()),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch("open_storyline.mvp.pipeline.AgenticShortRenderer", FakeAgenticRenderer),
                patch("open_storyline.mvp.pipeline.CPUShortRenderer", side_effect=AssertionError("legacy renderer called")),
            ):
                await processor("d" * 32, store)

            names = {name for name, _kind in store.registered}
            manifest = json.loads((root / "output" / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("render_execution.json", names)
            self.assertEqual(manifest["agentic"]["render_execution"], "render_execution.json")
            self.assertEqual((root / "output" / "short-01.mp4").read_bytes(), b"agentic-render")

    async def test_render_mode_generates_only_requested_assets_and_inserts_them(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={
                    "max_clips": 1,
                    "edit_mode": "agentic",
                    "asset_policy": "auto",
                    "max_generated_assets_per_clip": 1,
                },
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render", generated_assets=True)
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
            cascade = FakeAssetCascade()
            FakeAssetAwareRenderer.resolved_assets = {}

            with (
                patch("open_storyline.mvp.pipeline.probe_media", return_value=MediaInfo(30_000, 1920, 1080, True)),
                patch("open_storyline.mvp.pipeline.extract_audio_for_stt", side_effect=lambda _source, target: target),
                patch("open_storyline.mvp.pipeline.detect_scene_boundaries", return_value=scene_report),
                patch("open_storyline.mvp.pipeline.sample_frames", return_value=frame_manifest),
                patch("open_storyline.mvp.pipeline.VisualUnderstandingPlanner", FakeVisualPlanner),
                patch("open_storyline.mvp.pipeline.AgenticEditPlanner", FakeGeneratedEditPlanner),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=FakeRemoteClient()),
                patch("open_storyline.mvp.pipeline.RemoteImageCascade.from_config", return_value=cascade),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch("open_storyline.mvp.pipeline.AgenticShortRenderer", FakeAssetAwareRenderer),
                patch("open_storyline.mvp.pipeline.CPUShortRenderer", side_effect=AssertionError("legacy renderer called")),
            ):
                await processor("e" * 32, store)

            registered = dict(store.registered)
            manifest = json.loads((root / "output" / "manifest.json").read_text(encoding="utf-8"))
            asset_manifest = json.loads(
                (root / "output" / "asset_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(cascade.calls), 1)
            self.assertIn("Do not deliberately reproduce", cascade.calls[0][0])
            self.assertEqual(registered["asset_manifest.json"], "asset_manifest")
            self.assertEqual(registered["asset-asset-1.png"], "generated_image")
            self.assertTrue(FakeAssetAwareRenderer.resolved_assets["asset-1"].is_file())
            self.assertEqual(asset_manifest["resolved_count"], 1)
            self.assertEqual(manifest["agentic"]["assets"]["provider_calls"], 1)
            self.assertEqual(manifest["agentic"]["asset_manifest"], "asset_manifest.json")


if __name__ == "__main__":
    unittest.main()
