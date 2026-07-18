from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from open_storyline.mvp.edit_plan import EditPlanError
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
    def to_dict(self):
        return {
            "version": "visual_understanding.v1",
            "frame_manifest": {"frames": [{"id": "frame-001", "timestamp_ms": 250}]},
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
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=object()),
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


if __name__ == "__main__":
    unittest.main()
