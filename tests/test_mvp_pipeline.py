from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import base64
import json
import os
import unittest
from unittest.mock import patch

from open_storyline.mvp.checkpoints import CheckpointHit
from open_storyline.mvp.compositor import CompositionError, dry_run_edit_plan_composition
from open_storyline.mvp.edit_plan import (
    AssetRequest,
    ClipEditPlan,
    EditPlan,
    EditPlanError,
    EditSegment,
    FocalTarget,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
    build_shadow_edit_plan,
)
from open_storyline.mvp.frame_sampling import FrameManifest, SampledFrame
from open_storyline.mvp.creative_qa import CreativeQAArtifacts
from open_storyline.mvp.fallbacks import compile_baseline_plan
from open_storyline.mvp.ffmpega import FFMPEGAError
from open_storyline.mvp.ninerouter import NineRouterAttempt, NineRouterError
from open_storyline.mvp.pipeline import MVPJobProcessor
from open_storyline.mvp.preflight import PreflightFinding, PreflightReport, build_preflight
from open_storyline.mvp.promotion import RenderPromotionError
from open_storyline.mvp.render import AgenticRenderResult, MediaInfo, RenderError, RenderedShort
from open_storyline.mvp.scene_boundaries import build_scene_boundaries
from open_storyline.mvp.shorts import ShortCandidate, ShortsPlan
from open_storyline.mvp.stock import PexelsAsset, PexelsAttempt
from open_storyline.mvp.visual_understanding import (
    NormalizedBox,
    RegionObservation,
    VisualUnderstanding,
)
from open_storyline.utils.remote_image import ImageAttempt, RemoteImageError, RemoteImageResult


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeTranscript:
    model = "voxtral-test"
    text = "A useful explanation"
    segments = [{"start": 0, "end": 20_000, "text": text}]
    attempts = []


class FakeSTT:
    def __init__(self):
        self.calls = 0

    async def transcribe(self, _audio, *, language=""):
        self.calls += 1
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
    def __init__(self, _settings, **_kwargs):
        pass

    def preflight_plan(self, **_kwargs):
        return {"version": "ffmpeg_preflight.v1", "status": "pass", "clips": []}

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


class FakePreflightFallbackRenderer(FakeAgenticRenderer):
    preflight_calls = 0

    def preflight_plan(self, **_kwargs):
        type(self).preflight_calls += 1
        if type(self).preflight_calls == 1:
            raise RenderError("AGENTIC_PREFLIGHT_FAILED", "synthetic filter failure")
        return super().preflight_plan(**_kwargs)


class FakeVisualPlanner:
    def __init__(self, _client):
        pass

    async def plan(self, *, frame_manifest, **_kwargs):
        return VisualUnderstanding(
            model="fake-vision",
            source_duration_ms=frame_manifest.source_duration_ms,
            frame_manifest=frame_manifest.to_dict(),
            regions=(),
            tracks=(),
            scenes=(),
            warnings=(),
        )


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
            planner_version="agentic-editor.v2",
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


class FakeMissingCropEditPlanner:
    calls = 0

    def __init__(self, _client):
        pass

    async def plan(self, *, shorts_plan, source_duration_ms, **_kwargs):
        type(self).calls += 1
        clip = shorts_plan.clips[0]
        return EditPlan(
            planner_version="agentic-editor.v2",
            source_duration_ms=source_duration_ms,
            requested_capabilities=("crop", "hard_cut", "subtitles"),
            clips=(ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                output_name="short-01.mp4",
                segments=(EditSegment(
                    id="segment-1",
                    source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                    timeline_window=TimeWindow(start_ms=0, end_ms=clip.duration_ms),
                    layout=LayoutSpec(
                        mode="crop",
                        focal_target=FocalTarget(track_id="clip-01-track-missing"),
                        fallback="crop",
                    ),
                    reason="keep the tracked speaker visible",
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
            planner_version="agentic-editor.v2",
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


class FakeStockEditPlanner:
    def __init__(self, _client):
        pass

    async def plan(self, *, shorts_plan, source_duration_ms, **_kwargs):
        clip = shorts_plan.clips[0]
        asset_window = TimeWindow(start_ms=1000, end_ms=3000)
        return EditPlan(
            planner_version="agentic-editor.v2",
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
                        id="stock-overlay",
                        kind="image",
                        timeline_window=asset_window,
                        asset_id="stock-1",
                        position="top_right",
                    ),),
                    reason="insert one justified stock cutaway",
                ),),
                asset_requests=(AssetRequest(
                    id="stock-1",
                    kind="stock_image",
                    provider="pexels",
                    timeline_window=asset_window,
                    visual_gap="the source lacks a neutral supporting cutaway",
                    purpose="clarify the spoken example",
                    rationale="a brief stock image closes the visual gap",
                    prompt="remote planning meeting",
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


class FakeFailingAssetCascade:
    async def generate(self, _prompt, *, size):
        raise RemoteImageError(
            "REMOTE_IMAGE_UNAVAILABLE",
            f"synthetic provider failure for {size}",
        )


class FakePexelsClient:
    def __init__(self):
        self.calls = []

    async def acquire(self, request):
        self.calls.append(request.id)
        attempt = PexelsAttempt(1, "search", 200, "ok")
        return PexelsAsset(
            provider_id=42,
            kind=request.kind,
            content=PNG,
            extension="png",
            content_type="image/png",
            creator="Example Creator",
            creator_url="https://www.pexels.com/@example",
            source_url="https://www.pexels.com/photo/example-42/",
            media_url="https://images.pexels.com/photos/42/example.png",
            width=1080,
            height=1920,
            duration_seconds=None,
            file_size=len(PNG),
            retrieved_at="2026-07-18T00:00:00+00:00",
            attempts=(attempt,),
        )


class FakeRemoteClient:
    model = "cx/gpt-5.6-sol"
    last_attempts = ()


class FakeFailingEffectsPlanner:
    def __init__(self, _client):
        pass

    async def plan(self, *_args, **_kwargs):
        raise FFMPEGAError("FFMPEGA_PLAN_INVALID", "synthetic optional failure")


class FakePredictiveRepairEditPlanner:
    def __init__(self, client):
        self.client = client
        self.deferred_defects = ()

    async def plan(self, *, shorts_plan, source_duration_ms, defer_registry_repair, **_kwargs):
        self.client.last_attempts = ()
        if not defer_registry_repair:
            raise AssertionError("registry repair was not enabled")
        clip = shorts_plan.clips[0]
        return EditPlan(
            planner_version="agentic-editor.v2",
            source_duration_ms=source_duration_ms,
            requested_capabilities=(
                "source_cutaway",
                "text_emphasis",
                "hard_cut",
                "subtitles",
            ),
            clips=(ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                output_name="short-01.mp4",
                segments=(EditSegment(
                    id="segment-1",
                    source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                    timeline_window=TimeWindow(start_ms=0, end_ms=clip.duration_ms),
                    layout=LayoutSpec(mode="source"),
                    overlays=(OverlaySpec(
                        id="emphasis-1",
                        kind="text",
                        timeline_window=TimeWindow(start_ms=0, end_ms=2_000),
                        text="Key point",
                        opacity=0.1,
                        width_ratio=0.9,
                        margin_ratio=0.1,
                        position="top",
                    ),),
                    reason="Keep the source and emphasize the key point.",
                ),),
            ),),
        )


class FakePlanRepairClient:
    model = "cx/gpt-5.6-sol"
    reasoning_effort = "medium"

    def __init__(self):
        self.calls = []
        self.last_attempts = ()
        self.invalid = False

    async def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        self.last_attempts = (NineRouterAttempt(
            1,
            200,
            "ok",
            duration_ms=321,
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        ),)
        if self.invalid:
            return {"requested_capabilities": [], "clips": []}
        payload = json.loads(kwargs["user_prompt"])
        candidate = payload["candidate_clips"][0]
        candidate["segments"][0]["overlays"][0]["opacity"] = 0.8
        candidate["segments"][0]["overlays"][0]["width_ratio"] = 0.5
        return {
            "requested_capabilities": [
                "source_cutaway",
                "text_emphasis",
                "hard_cut",
                "subtitles",
            ],
            "clips": [candidate],
        }


class FakeWideVisualPlanner:
    def __init__(self, _client):
        pass

    async def plan(self, *, frame_manifest, **_kwargs):
        regions = []
        for frame in frame_manifest.frames:
            regions.extend((
                RegionObservation(
                    id=f"{frame.id}-left",
                    frame_id=frame.id,
                    role="speaker",
                    bbox=NormalizedBox(x=0.04, y=0.12, width=0.38, height=0.76),
                    confidence=0.95,
                    salience=0.95,
                    description="synthetic left speaker",
                ),
                RegionObservation(
                    id=f"{frame.id}-right",
                    frame_id=frame.id,
                    role="speaker",
                    bbox=NormalizedBox(x=0.58, y=0.12, width=0.38, height=0.76),
                    confidence=0.95,
                    salience=0.95,
                    description="synthetic right speaker",
                ),
            ))
        return VisualUnderstanding(
            model="fake-wide-vision",
            source_duration_ms=frame_manifest.source_duration_ms,
            frame_manifest=frame_manifest.to_dict(),
            regions=tuple(regions),
            tracks=(),
            scenes=(),
            warnings=(),
        )


class FakeGeometryEditPlanner:
    def __init__(self, _client):
        self.deferred_defects = ()

    async def plan(self, *, shorts_plan, source_duration_ms, **_kwargs):
        clip = shorts_plan.clips[0]
        return EditPlan(
            planner_version="agentic-editor.v2",
            source_duration_ms=source_duration_ms,
            requested_capabilities=("crop", "hard_cut", "subtitles"),
            clips=(ClipEditPlan(
                clip_index=1,
                source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                output_name="short-01.mp4",
                segments=(EditSegment(
                    id="wide-speakers",
                    source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                    timeline_window=TimeWindow(start_ms=0, end_ms=clip.duration_ms),
                    layout=LayoutSpec(
                        mode="crop",
                        focal_target=FocalTarget(semantic_role="speaker"),
                        fallback="crop",
                        allow_full_frame_fallback=False,
                    ),
                    reason="keep both synthetic speakers visible",
                ),),
            ),),
        )


class FakeGeometryRepairClient:
    model = "cx/gpt-5.6-sol"
    reasoning_effort = "medium"

    def __init__(self, *, fail_calls=()):
        self.calls = []
        self.fail_calls = set(fail_calls)
        self.last_attempts = ()

    async def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        call_number = len(self.calls)
        attempt = NineRouterAttempt(
            1,
            503 if call_number in self.fail_calls else 200,
            "provider_unavailable" if call_number in self.fail_calls else "ok",
            duration_ms=50,
        )
        self.last_attempts = (attempt,)
        if call_number in self.fail_calls:
            raise NineRouterError(
                "NINEROUTER_REQUEST_FAILED",
                "synthetic repair provider failure",
                attempts=[attempt],
            )
        payload = json.loads(kwargs["user_prompt"])
        candidate = payload["candidate_clips"][0]
        candidate["segments"][0]["layout"].update({
            "mode": "fit",
            "focal_target": None,
            "fallback": "fit",
            "allow_full_frame_fallback": True,
            "max_zoom": 1.0,
        })
        return {
            "requested_capabilities": ["fit", "hard_cut", "subtitles"],
            "clips": [candidate],
        }


async def fake_creative_qa_artifacts(*, output_dir, **_kwargs):
    root = Path(output_dir)
    render_path = root / "render_qa.json"
    rhythm_path = root / "retention_rhythm_qa.json"
    conformance_path = root / "creative_conformance.json"
    render = {"version": "render_qa.v1", "status": "pass"}
    rhythm = {"version": "retention_rhythm_qa.v1", "status": "pass"}
    conformance = {"version": "creative_conformance.v1", "status": "pass"}
    render_path.write_text(json.dumps(render), encoding="utf-8")
    rhythm_path.write_text(json.dumps(rhythm), encoding="utf-8")
    conformance_path.write_text(json.dumps(conformance), encoding="utf-8")
    return CreativeQAArtifacts(
        render_path,
        rhythm_path,
        conformance_path,
        render,
        rhythm,
        conformance,
    )


class FakeStore:
    def __init__(self, root: Path, *, server_request: dict, prompt: str = "make a strong short"):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.request = server_request
        self.prompt = prompt
        self.registered: list[tuple[str, str]] = []

    async def load(self, _job_id):
        return {
            "editing_session_id": "e" * 32,
            "prompt": self.prompt,
            "prompt_version_id": "b" * 32,
            "attempt_number": 2,
            "is_favorite": True,
            "request": {"settings_version": 1, **self.request},
            "input": {
                "input_video_id": "c" * 32,
                "original_filename": "source.mp4",
                "sha256": "d" * 64,
            },
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


class FakeCheckpointStore:
    enabled = True

    def __init__(self):
        self.session = {}
        self.jobs = {}

    async def load_session(self, *, stage, fingerprint, **_kwargs):
        payload = self.session.get((stage, fingerprint))
        if payload is None:
            return None
        return CheckpointHit(stage, fingerprint, payload, "a" * 64)

    async def save_session(self, *, stage, fingerprint, payload, **_kwargs):
        self.session[(stage, fingerprint)] = payload

    async def load_job(self, *, job_id, stage, fingerprint):
        payload = self.jobs.get((job_id, stage, fingerprint))
        if payload is None:
            return None
        return CheckpointHit(
            stage,
            fingerprint,
            payload,
            "b" * 64,
            source_job_id=job_id,
        )

    async def save_job(self, *, job_id, stage, fingerprint, payload, **_kwargs):
        self.jobs[(job_id, stage, fingerprint)] = payload


def config(mode: str, *, generated_assets: bool = False, pexels_assets: bool = False):
    return SimpleNamespace(
        remote_asr=SimpleNamespace(language=""),
        ninerouter=SimpleNamespace(),
        agentic_editing=SimpleNamespace(
            mode=mode,
            shadow_allow_blocked_plans=True,
            baseline_fallbacks_enabled=False,
            max_segments_per_clip=24,
            max_overlays_per_clip=12,
            max_assets_per_clip=4,
            generated_assets_enabled=generated_assets,
            max_generated_assets_per_clip=2,
            pexels_enabled=pexels_assets,
            max_stock_assets_per_clip=2,
            pexels_license_reviewed_at="2026-07-18",
            pexels_search_limit=8,
            pexels_timeout=30.0,
            pexels_max_retries=2,
            pexels_max_bytes=80 * 1024 * 1024,
            pexels_max_video_duration_seconds=60,
            creative_qa_enabled=False,
            creative_qa_strict=True,
            render_promotion_mode="report",
            completion_policy="strict",
            delivery_policy="qa_enforced",
            semantic_qa_enabled=False,
            semantic_qa_max_frames=4,
            scene_threshold=0.35,
            min_scene_duration_ms=1000,
            max_scenes=64,
            vision_frame_count=6,
            vision_clip_frame_count=6,
            vision_clip_repair_frame_count=12,
            vision_frame_max_width=512,
            vision_frame_max_height=512,
            vision_frame_max_bytes=1_500_000,
            crop_coverage_min_observations=2,
            crop_coverage_min_ratio=0.5,
            crop_coverage_max_gap_ms=8_000,
            crop_hysteresis_ratio=0.03,
            crop_smoothing_alpha=0.65,
            max_crop_velocity_ratio_per_second=0.45,
        ),
        mvp=SimpleNamespace(
            frame_count=0,
            render_width=1080,
            render_height=1920,
            render_quality_profile="high",
            render_fps_cap=60,
            render_fps=30,
            render_preset="veryfast",
            render_crf=23,
        ),
        ffmpega=SimpleNamespace(enabled=False),
        remote_image=SimpleNamespace(size="1024x1024"),
    )


def wide_frame_manifest() -> FrameManifest:
    return FrameManifest(
        source_duration_ms=30_000,
        source_width=1920,
        source_height=1080,
        frames=tuple(
            SampledFrame(
                id=f"frame-{index:03d}",
                timestamp_ms=timestamp_ms,
                scene_id="scene-001",
                width=512,
                height=288,
                extraction_reason="synthetic_geometry",
                encoded_bytes=4,
                data_url="data:image/jpeg;base64,ZmFrZQ==",
            )
            for index, timestamp_ms in enumerate(
                (1_000, 5_000, 10_000, 15_000),
                start=1,
            )
        ),
    )


class MVPAgenticPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_geometry_recovery_uses_two_attempted_batches_then_segment_fallback(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={
                    "max_clips": 1,
                    "edit_mode": "agentic",
                    "asset_policy": "off",
                },
            )
            remote = FakeGeometryRepairClient(fail_calls=(1, 2))
            checkpoints = FakeCheckpointStore()
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render")
            processor.config.agentic_editing.baseline_fallbacks_enabled = True
            processor.config.agentic_editing.render_promotion_mode = "off"
            processor.stt = FakeSTT()
            scene_report = build_scene_boundaries(
                [],
                source_duration_ms=30_000,
                threshold=0.35,
            )
            frame_manifest = wide_frame_manifest()
            execution_order = []

            def tracked_dry_run(*args, **kwargs):
                execution_order.append("dry_run")
                return dry_run_edit_plan_composition(*args, **kwargs)

            compilation_calls = 0

            def compile_with_new_authoritative_defect(*args, **kwargs):
                nonlocal compilation_calls
                compilation_calls += 1
                result = compile_baseline_plan(*args, **kwargs)
                if compilation_calls != 1:
                    return result
                payload = result.plan.to_dict()
                layout = payload["clips"][0]["segments"][0]["layout"]
                layout.update({"mode": "letterbox", "fallback": "letterbox"})
                payload["requested_capabilities"] = [
                    "letterbox",
                    "hard_cut",
                    "subtitles",
                ]
                return type(result)(
                    plan=EditPlan.model_validate(payload),
                    entries=result.entries,
                )

            class OrderedRenderer(FakeAgenticRenderer):
                def preflight_plan(self, **kwargs):
                    execution_order.append("ffmpeg_preflight")
                    return super().preflight_plan(**kwargs)

                def render_plan(self, **kwargs):
                    execution_order.append("ffmpeg_render")
                    return super().render_plan(**kwargs)

            with (
                patch.dict(
                    os.environ,
                    {"OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE": "enforce"},
                ),
                patch(
                    "open_storyline.mvp.pipeline.probe_media",
                    return_value=MediaInfo(30_000, 1920, 1080, True),
                ),
                patch(
                    "open_storyline.mvp.pipeline.extract_audio_for_stt",
                    side_effect=lambda _source, target: target,
                ),
                patch(
                    "open_storyline.mvp.pipeline.detect_scene_boundaries",
                    return_value=scene_report,
                ),
                patch(
                    "open_storyline.mvp.pipeline.sample_frames",
                    return_value=frame_manifest,
                ),
                patch(
                    "open_storyline.mvp.pipeline.VisualUnderstandingPlanner",
                    FakeWideVisualPlanner,
                ),
                patch(
                    "open_storyline.mvp.pipeline.AgenticEditPlanner",
                    FakeGeometryEditPlanner,
                ),
                patch(
                    "open_storyline.mvp.pipeline.NineRouterClient.from_config",
                    return_value=remote,
                ),
                patch(
                    "open_storyline.mvp.pipeline.CheckpointStore",
                    return_value=checkpoints,
                ),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch(
                    "open_storyline.mvp.pipeline.AgenticShortRenderer",
                    OrderedRenderer,
                ),
                patch(
                    "open_storyline.mvp.pipeline.dry_run_edit_plan_composition",
                    side_effect=tracked_dry_run,
                ),
                patch(
                    "open_storyline.mvp.pipeline.compile_baseline_plan",
                    side_effect=compile_with_new_authoritative_defect,
                ),
                patch(
                    "open_storyline.mvp.pipeline.build_frame_quality_report",
                    return_value={
                        "version": "frame_quality_qa.v1",
                        "status": "pass",
                        "findings": [],
                    },
                ),
            ):
                result = await processor("7" * 32, store)

            self.assertEqual(len(remote.calls), 2)
            requests = [json.loads(call["user_prompt"]) for call in remote.calls]
            self.assertEqual(
                [request["repair_round"] for request in requests],
                ["primary", "contingency"],
            )
            self.assertIn(
                "COMPOSITION_CROP_TARGET_TOO_WIDE",
                {item["code"] for item in requests[0]["defects"]},
            )
            self.assertIn(
                "PREDICTIVE_ACTIVE_PICTURE_RISK",
                {item["code"] for item in requests[1]["defects"]},
            )
            compiled = json.loads(
                (root / "output" / "edit_plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                compiled["clips"][0]["segments"][0]["layout"]["mode"],
                "fit",
            )
            fallback_ledger = json.loads(
                (root / "output" / "fallback_ledger.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn(
                "VISUAL_REFRAME_FALLBACK",
                fallback_ledger["summary"]["codes"],
            )
            repair_report = json.loads(
                (root / "output" / "repair_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(repair_report["version"], "repair_report.v2")
            self.assertEqual(
                [
                    item["repair_round"]
                    for item in repair_report["stages"]
                    if item["stage"] == "plan_repair"
                ],
                ["primary", "contingency"],
            )
            self.assertEqual(len(repair_report["attempt_ledger"]), 2)
            self.assertGreaterEqual(
                repair_report["summary"]["fallback_after_attempt_count"],
                1,
            )
            self.assertEqual(
                repair_report["summary"]["repair_invariant_violation_count"],
                0,
            )
            checkpoint_stages = {
                key[1]
                for key in checkpoints.jobs
                if key[0] == "7" * 32
            }
            self.assertIn("plan_repair", checkpoint_stages)
            self.assertIn("plan_repair_contingency", checkpoint_stages)
            self.assertEqual(result["outcome"]["technical_status"], "pass")
            self.assertEqual(result["outcome"]["repair"]["metrics"]["primary_calls"], 1)
            self.assertEqual(
                result["outcome"]["repair"]["metrics"]["contingency_calls"],
                1,
            )
            self.assertLess(
                execution_order.index("dry_run"),
                execution_order.index("ffmpeg_preflight"),
            )
            self.assertLess(
                execution_order.index("ffmpeg_preflight"),
                execution_order.index("ffmpeg_render"),
            )

    async def test_geometry_recovery_fails_closed_when_the_final_baseline_is_not_executable(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={
                    "max_clips": 1,
                    "edit_mode": "agentic",
                    "asset_policy": "off",
                },
            )
            remote = FakeGeometryRepairClient(fail_calls=(1,))
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render")
            processor.config.agentic_editing.baseline_fallbacks_enabled = True
            processor.config.agentic_editing.render_promotion_mode = "off"
            processor.stt = FakeSTT()
            scene_report = build_scene_boundaries(
                [],
                source_duration_ms=30_000,
                threshold=0.35,
            )

            class UnexpectedRenderer(FakeAgenticRenderer):
                def preflight_plan(self, **_kwargs):
                    raise AssertionError("FFmpeg preflight ran after a failed dry-run gate")

                def render_plan(self, **_kwargs):
                    raise AssertionError("FFmpeg render ran after a failed dry-run gate")

            with (
                patch.dict(
                    os.environ,
                    {"OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE": "enforce"},
                ),
                patch(
                    "open_storyline.mvp.pipeline.probe_media",
                    return_value=MediaInfo(30_000, 1920, 1080, True),
                ),
                patch(
                    "open_storyline.mvp.pipeline.extract_audio_for_stt",
                    side_effect=lambda _source, target: target,
                ),
                patch(
                    "open_storyline.mvp.pipeline.detect_scene_boundaries",
                    return_value=scene_report,
                ),
                patch(
                    "open_storyline.mvp.pipeline.sample_frames",
                    return_value=wide_frame_manifest(),
                ),
                patch(
                    "open_storyline.mvp.pipeline.VisualUnderstandingPlanner",
                    FakeWideVisualPlanner,
                ),
                patch(
                    "open_storyline.mvp.pipeline.AgenticEditPlanner",
                    FakeGeometryEditPlanner,
                ),
                patch(
                    "open_storyline.mvp.pipeline.NineRouterClient.from_config",
                    return_value=remote,
                ),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch(
                    "open_storyline.mvp.pipeline.AgenticShortRenderer",
                    UnexpectedRenderer,
                ),
                patch(
                    "open_storyline.mvp.pipeline.dry_run_edit_plan_composition",
                    side_effect=CompositionError(
                        "COMPOSITION_LAYOUT_UNSUPPORTED",
                        "synthetic final baseline cannot be executed",
                    ),
                ),
            ):
                with self.assertRaises(EditPlanError) as caught:
                    await processor("6" * 32, store)

            self.assertEqual(caught.exception.code, "REPAIR_EXECUTION_DRY_RUN_FAILED")
            self.assertEqual(len(remote.calls), 1)
            self.assertFalse((root / "output" / "short-01.mp4").exists())
            repair_report = json.loads(
                (root / "output" / "repair_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(repair_report["attempt_ledger"]), 1)
            self.assertEqual(
                repair_report["summary"]["repair_invariant_violation_count"],
                0,
            )
            self.assertEqual(repair_report["summary"]["jobs_at_two_call_cap"], 0)

    async def test_enforced_plan_repair_is_batched_and_checkpointed_once(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={
                    "max_clips": 1,
                    "edit_mode": "agentic",
                    "asset_policy": "off",
                },
            )
            checkpoints = FakeCheckpointStore()
            remote = FakePlanRepairClient()
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render")
            processor.config.agentic_editing.render_promotion_mode = "off"
            processor.stt = FakeSTT()
            scene_report = build_scene_boundaries(
                [], source_duration_ms=30_000, threshold=0.35
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
                patch.dict(
                    os.environ,
                    {"OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE": "enforce"},
                ),
                patch("open_storyline.mvp.pipeline.CheckpointStore", return_value=checkpoints),
                patch("open_storyline.mvp.pipeline.probe_media", return_value=MediaInfo(30_000, 1920, 1080, True)),
                patch("open_storyline.mvp.pipeline.extract_audio_for_stt", side_effect=lambda _source, target: target),
                patch("open_storyline.mvp.pipeline.detect_scene_boundaries", return_value=scene_report),
                patch("open_storyline.mvp.pipeline.sample_frames", return_value=frame_manifest),
                patch("open_storyline.mvp.pipeline.VisualUnderstandingPlanner", FakeVisualPlanner),
                patch("open_storyline.mvp.pipeline.AgenticEditPlanner", FakePredictiveRepairEditPlanner),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=remote),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch("open_storyline.mvp.pipeline.AgenticShortRenderer", FakeAgenticRenderer),
                patch(
                    "open_storyline.mvp.pipeline.build_frame_quality_report",
                    return_value={
                        "version": "frame_quality_qa.v1",
                        "status": "pass",
                        "findings": [],
                    },
                ),
            ):
                first = await processor("4" * 32, store)
                first_manifest = json.loads(
                    (root / "output" / "manifest.json").read_text(encoding="utf-8")
                )
                first_edit_plan = json.loads(
                    (root / "output" / "edit_plan.json").read_text(encoding="utf-8")
                )
                second = await processor("4" * 32, store)
                calls_after_reuse = len(remote.calls)
                for key, payload in checkpoints.jobs.items():
                    if key[1] == "plan_repair":
                        payload["edit_plan"] = {"requested_capabilities": [], "clips": []}
                remote.invalid = True
                third = await processor("4" * 32, store)
                third_manifest = json.loads(
                    (root / "output" / "manifest.json").read_text(encoding="utf-8")
                )

            self.assertEqual(calls_after_reuse, 1)
            self.assertEqual(len(remote.calls), 2)
            self.assertEqual(remote.calls[0]["schema_name"], "edit_plan_repair.v1")
            request = json.loads(remote.calls[0]["user_prompt"])
            self.assertEqual(request["affected_clip_ids"], [1])
            self.assertEqual(
                {
                    "PREDICTIVE_OVERLAY_GEOMETRY_INVALID",
                    "PREDICTIVE_OVERLAY_OPACITY_LOW",
                },
                {item["code"] for item in request["defects"]},
            )
            self.assertEqual(
                first_manifest["agentic"]["edit_planner"]["attempts"][-1]["category"],
                "plan_repair",
            )
            self.assertEqual(
                first_edit_plan["clips"][0]["segments"][0]["overlays"][0]["opacity"],
                0.8,
            )
            self.assertIn("plan_repair", second["checkpoints"]["reused_stages"])
            self.assertIn("plan_repair", third["checkpoints"]["recomputed_stages"])
            self.assertEqual(first["clip_count"], second["clip_count"])
            third_repair_attempts = [
                item
                for item in third_manifest["agentic"]["edit_planner"]["attempts"]
                if item.get("category") == "plan_repair"
            ]
            self.assertEqual(len(third_repair_attempts), 1)
            repair_metrics = third["outcome"]["repair"]["metrics"]
            self.assertEqual(repair_metrics["transport_attempts"], 1)
            self.assertEqual(repair_metrics["provider_latency_ms"], 321)
            self.assertEqual(repair_metrics["input_tokens"], 100)

    async def test_retry_reuses_expensive_analysis_checkpoints(self):
        class CountingPlanner(FakePlanner):
            calls = 0

            async def plan(self, **kwargs):
                type(self).calls += 1
                return await super().plan(**kwargs)

        class CountingVisualPlanner(FakeVisualPlanner):
            calls = 0

            async def plan(self, **kwargs):
                type(self).calls += 1
                return await super().plan(**kwargs)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            first_store = FakeStore(
                root / "first",
                server_request={
                    "max_clips": 1,
                    "edit_mode": "agentic",
                    "asset_policy": "off",
                },
            )
            second_store = FakeStore(
                root / "second",
                server_request={
                    "max_clips": 1,
                    "edit_mode": "agentic",
                    "asset_policy": "off",
                    "prior_attempt_quality_feedback": {
                        "prior_attempt_id": "1" * 32,
                        "prior_attempt_number": 1,
                    },
                },
            )
            checkpoints = FakeCheckpointStore()
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
            CountingPlanner.calls = 0
            CountingVisualPlanner.calls = 0

            with (
                patch("open_storyline.mvp.pipeline.CheckpointStore", return_value=checkpoints),
                patch("open_storyline.mvp.pipeline.probe_media", return_value=MediaInfo(30_000, 1920, 1080, True)),
                patch("open_storyline.mvp.pipeline.extract_audio_for_stt", side_effect=lambda _source, target: target),
                patch("open_storyline.mvp.pipeline.detect_scene_boundaries", return_value=scene_report) as detector,
                patch("open_storyline.mvp.pipeline.sample_frames", return_value=frame_manifest) as sampler,
                patch("open_storyline.mvp.pipeline.VisualUnderstandingPlanner", CountingVisualPlanner),
                patch("open_storyline.mvp.pipeline.AgenticEditPlanner", FakeEditPlanner),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=FakeRemoteClient()),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", CountingPlanner),
                patch("open_storyline.mvp.pipeline.CPUShortRenderer", FakeRenderer),
            ):
                first = await processor("1" * 32, first_store)
                second = await processor("2" * 32, second_store)

            self.assertEqual(first["checkpoints"]["reused_stages"], [])
            self.assertEqual(
                set(second["checkpoints"]["reused_stages"]),
                {
                    "transcript",
                    "scene_boundaries",
                    "agentic_global_analysis",
                    "clip_visual_analysis",
                },
            )
            self.assertEqual(processor.stt.calls, 1)
            self.assertEqual(detector.call_count, 1)
            self.assertEqual(sampler.call_count, 2)
            self.assertEqual(CountingVisualPlanner.calls, 2)
            self.assertEqual(CountingPlanner.calls, 1)

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
            self.assertIn("clip_visual_coverage.json", names)
            self.assertIn("edit_plan.json", names)
            self.assertIn("creative_intent.json", names)
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
            self.assertEqual(manifest["run"]["prompt_version_id"], "b" * 32)
            self.assertEqual(manifest["run"]["attempt_number"], 2)
            self.assertEqual(manifest["run"]["settings_version"], 1)
            self.assertTrue(manifest["run"]["is_favorite"])
            self.assertEqual(manifest["source"]["input_video_id"], "c" * 32)
            self.assertEqual(manifest["source"]["sha256"], "d" * 64)
            self.assertEqual(manifest["agentic"]["edit_planner"]["schema_version"], "edit_plan.v2")
            self.assertEqual(
                manifest["agentic"]["edit_planner"]["prompt_version"],
                "mvp-agentic-edit-plan.v8",
            )
            registered_names = [name for name, _kind in store.registered]
            self.assertLess(
                registered_names.index("shorts_plan.json"),
                registered_names.index("creative_intent.json"),
            )
            self.assertLess(
                registered_names.index("creative_intent.json"),
                registered_names.index("edit_plan.json"),
            )

    async def test_prompt_required_asset_fails_before_provider_calls_when_disabled(self):
        with TemporaryDirectory() as directory:
            store = FakeStore(
                Path(directory),
                server_request={
                    "max_clips": 1,
                    "edit_mode": "agentic",
                    "asset_policy": "off",
                    "stock_policy": "off",
                },
                prompt="Use exactly one generated editorial image.",
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render")
            processor.stt = FakeSTT()

            with self.assertRaises(EditPlanError) as caught:
                await processor("9" * 32, store)

            self.assertEqual(
                caught.exception.code,
                "CREATIVE_INTENT_CAPABILITY_UNAVAILABLE",
            )
            self.assertEqual(store.registered, [])

    async def test_crop_coverage_repairs_once_then_compiles_safe_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={
                    "max_clips": 1,
                    "edit_mode": "agentic",
                    "asset_policy": "off",
                },
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render")
            processor.config.agentic_editing.baseline_fallbacks_enabled = True
            processor.config.agentic_editing.render_promotion_mode = "off"
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
            FakeMissingCropEditPlanner.calls = 0
            FakePreflightFallbackRenderer.preflight_calls = 0

            with (
                patch("open_storyline.mvp.pipeline.probe_media", return_value=MediaInfo(30_000, 1920, 1080, True)),
                patch("open_storyline.mvp.pipeline.extract_audio_for_stt", side_effect=lambda _source, target: target),
                patch("open_storyline.mvp.pipeline.detect_scene_boundaries", return_value=scene_report),
                patch("open_storyline.mvp.pipeline.sample_frames", return_value=frame_manifest) as sampler,
                patch("open_storyline.mvp.pipeline.VisualUnderstandingPlanner", FakeVisualPlanner),
                patch("open_storyline.mvp.pipeline.AgenticEditPlanner", FakeMissingCropEditPlanner),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=FakeRemoteClient()),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch(
                    "open_storyline.mvp.pipeline.AgenticShortRenderer",
                    FakePreflightFallbackRenderer,
                ),
                patch(
                    "open_storyline.mvp.pipeline.build_frame_quality_report",
                    return_value={
                        "version": "frame_quality_qa.v1",
                        "status": "pass",
                        "findings": [],
                    },
                ),
            ):
                result = await processor("8" * 32, store)

            self.assertEqual(result["outcome"]["grade"], "with_limitations")
            self.assertEqual(FakePreflightFallbackRenderer.preflight_calls, 2)
            self.assertEqual(FakeMissingCropEditPlanner.calls, 2)
            self.assertEqual(sampler.call_count, 3)
            self.assertEqual(
                sampler.call_args_list[-1].kwargs["focus_windows"],
                ((0, 20_000),),
            )
            coverage = json.loads(
                (root / "output" / "clip_visual_coverage.json").read_text(encoding="utf-8")
            )
            self.assertTrue(coverage["repair"]["attempted"])
            self.assertEqual(coverage["status"], "ready")
            fallback_ledger = json.loads(
                (root / "output" / "fallback_ledger.json").read_text(encoding="utf-8")
            )
            self.assertIn("VISUAL_REFRAME_FALLBACK", fallback_ledger["summary"]["codes"])
            self.assertIn("RENDER_PREFLIGHT_FALLBACK", fallback_ledger["summary"]["codes"])
            compiled = json.loads(
                (root / "output" / "edit_plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(compiled["clips"][0]["segments"][0]["layout"]["mode"], "fit")
            registered_names = [name for name, _kind in store.registered]
            self.assertEqual(registered_names.count("visual_understanding.json"), 2)
            self.assertEqual(registered_names.count("shorts_plan.json"), 2)

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
            processor.config.agentic_editing.creative_qa_enabled = True
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
                patch(
                    "open_storyline.mvp.pipeline.generate_creative_qa_artifacts",
                    side_effect=fake_creative_qa_artifacts,
                ),
                patch(
                    "open_storyline.mvp.pipeline.build_frame_quality_report",
                    return_value={
                        "version": "frame_quality_qa.v1",
                        "status": "pass",
                        "findings": [],
                    },
                ),
            ):
                await processor("d" * 32, store)

            names = {name for name, _kind in store.registered}
            manifest = json.loads((root / "output" / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("render_execution.json", names)
            self.assertIn("render_quality_profile.json", names)
            self.assertIn("render_qa.json", names)
            self.assertIn("retention_rhythm_qa.json", names)
            self.assertIn("creative_conformance.json", names)
            self.assertIn("frame_quality_qa.json", names)
            self.assertIn("render_promotion.json", names)
            self.assertEqual(manifest["agentic"]["render_execution"], "render_execution.json")
            self.assertEqual(
                manifest["agentic"]["render_quality_profile"],
                "render_quality_profile.json",
            )
            self.assertEqual(manifest["agentic"]["qa"]["status"], "pass")
            self.assertEqual(manifest["agentic"]["render_promotion"]["decision"], "promote")
            self.assertEqual((root / "output" / "short-01.mp4").read_bytes(), b"agentic-render")
            registered_names = [name for name, _kind in store.registered]
            self.assertLess(
                registered_names.index("render_promotion.json"),
                registered_names.index("short-01.mp4"),
            )

    async def test_enforce_mode_blocks_and_removes_candidate_before_video_registration(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={"max_clips": 1, "edit_mode": "agentic", "asset_policy": "off"},
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render")
            processor.config.agentic_editing.creative_qa_enabled = True
            processor.config.agentic_editing.render_promotion_mode = "enforce"
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
                patch(
                    "open_storyline.mvp.pipeline.generate_creative_qa_artifacts",
                    side_effect=fake_creative_qa_artifacts,
                ),
                patch(
                    "open_storyline.mvp.pipeline.build_frame_quality_report",
                    return_value={
                        "version": "frame_quality_qa.v1",
                        "status": "blocker",
                        "findings": [{
                            "code": "ACTIVE_PICTURE_TOO_SMALL",
                            "severity": "blocker",
                        }],
                    },
                ),
            ):
                with self.assertRaises(RenderPromotionError) as caught:
                    await processor("f" * 32, store)

            self.assertEqual(caught.exception.code, "RENDER_PROMOTION_BLOCKED")
            registered = {name for name, _kind in store.registered}
            self.assertIn("frame_quality_qa.json", registered)
            self.assertIn("render_promotion.json", registered)
            self.assertNotIn("short-01.mp4", registered)
            self.assertFalse((root / "output" / "short-01.mp4").exists())
            promotion = json.loads(
                (root / "output" / "render_promotion.json").read_text(encoding="utf-8")
            )
            self.assertEqual(promotion["decision"], "block")
            self.assertEqual(promotion["candidate_cleanup"]["video_candidates_removed"], 1)

    async def test_technical_pass_delivery_publishes_creative_only_strict_block(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={"max_clips": 1, "edit_mode": "agentic", "asset_policy": "off"},
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render")
            processor.config.agentic_editing.creative_qa_enabled = True
            processor.config.agentic_editing.render_promotion_mode = "enforce"
            processor.config.agentic_editing.delivery_policy = "technical_pass_guaranteed"
            processor.stt = FakeSTT()
            scene_report = build_scene_boundaries(
                [], source_duration_ms=30_000, threshold=0.35
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
                patch("open_storyline.mvp.pipeline.AgenticShortRenderer", FakeAgenticRenderer),
                patch(
                    "open_storyline.mvp.pipeline.generate_creative_qa_artifacts",
                    side_effect=fake_creative_qa_artifacts,
                ),
                patch(
                    "open_storyline.mvp.pipeline.build_frame_quality_report",
                    return_value={
                        "version": "frame_quality_qa.v1",
                        "status": "blocker",
                        "findings": [{
                            "code": "ACTIVE_PICTURE_TOO_SMALL",
                            "severity": "blocker",
                        }],
                    },
                ),
            ):
                result = await processor("1" * 32, store)

            registered = {name for name, _kind in store.registered}
            self.assertIn("short-01.mp4", registered)
            self.assertEqual(result["outcome"]["strict_qa"]["decision"], "block")
            self.assertEqual(
                result["outcome"]["delivery"]["decision"],
                "publish_with_limitations",
            )
            self.assertTrue(result["outcome"]["delivery"]["download_available"])

    async def test_optional_ffmpega_failure_keeps_native_candidate_without_baseline_flag(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={"max_clips": 1, "edit_mode": "agentic", "asset_policy": "off"},
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render")
            processor.config.ffmpega.enabled = True
            processor.config.agentic_editing.render_promotion_mode = "off"
            processor.stt = FakeSTT()
            scene_report = build_scene_boundaries(
                [], source_duration_ms=30_000, threshold=0.35
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
                patch("open_storyline.mvp.pipeline.AgenticShortRenderer", FakeAgenticRenderer),
                patch("open_storyline.mvp.pipeline.EffectsPlanner", FakeFailingEffectsPlanner),
            ):
                result = await processor("2" * 32, store)

            registered = {name for name, _kind in store.registered}
            self.assertIn("short-01.mp4", registered)
            self.assertIn("repair_report.json", registered)
            self.assertFalse(processor.config.agentic_editing.baseline_fallbacks_enabled)
            limitations = result["outcome"]["limitations"]
            effect = next(item for item in limitations if item["code"] == "EFFECT_OMITTED")
            self.assertEqual(effect["requested"], "ffmpega_effect_plan")
            self.assertEqual(effect["executed"], "native_ffmpeg_render")

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

    async def test_asset_provider_failure_omits_optional_asset_and_completes(self):
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
            processor.config.agentic_editing.baseline_fallbacks_enabled = True
            processor.config.agentic_editing.render_promotion_mode = "off"
            processor.stt = FakeSTT()
            scene_report = build_scene_boundaries(
                [], source_duration_ms=30_000, threshold=0.35
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
            FakeAssetAwareRenderer.resolved_assets = {"unexpected": Path("missing")}

            with (
                patch("open_storyline.mvp.pipeline.probe_media", return_value=MediaInfo(30_000, 1920, 1080, True)),
                patch("open_storyline.mvp.pipeline.extract_audio_for_stt", side_effect=lambda _source, target: target),
                patch("open_storyline.mvp.pipeline.detect_scene_boundaries", return_value=scene_report),
                patch("open_storyline.mvp.pipeline.sample_frames", return_value=frame_manifest),
                patch("open_storyline.mvp.pipeline.VisualUnderstandingPlanner", FakeVisualPlanner),
                patch("open_storyline.mvp.pipeline.AgenticEditPlanner", FakeGeneratedEditPlanner),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=FakeRemoteClient()),
                patch(
                    "open_storyline.mvp.pipeline.RemoteImageCascade.from_config",
                    return_value=FakeFailingAssetCascade(),
                ),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch("open_storyline.mvp.pipeline.AgenticShortRenderer", FakeAssetAwareRenderer),
                patch(
                    "open_storyline.mvp.pipeline.build_frame_quality_report",
                    return_value={
                        "version": "frame_quality_qa.v1",
                        "status": "pass",
                        "findings": [],
                    },
                ),
            ):
                result = await processor("a" * 32, store)

            self.assertEqual(result["outcome"]["grade"], "with_limitations")
            self.assertEqual(FakeAssetAwareRenderer.resolved_assets, {})
            fallback_ledger = json.loads(
                (root / "output" / "fallback_ledger.json").read_text(encoding="utf-8")
            )
            self.assertIn("EXTERNAL_ASSET_OMITTED", fallback_ledger["summary"]["codes"])
            asset_manifest = json.loads(
                (root / "output" / "asset_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(asset_manifest["requested_count"], 0)
            self.assertTrue(asset_manifest["status"].startswith("fallback_omitted:"))

    async def test_render_mode_resolves_opt_in_pexels_without_generated_fallback(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = FakeStore(
                root,
                server_request={
                    "max_clips": 1,
                    "edit_mode": "agentic",
                    "asset_policy": "off",
                    "max_generated_assets_per_clip": 0,
                    "stock_policy": "auto",
                    "max_stock_assets_per_clip": 1,
                },
            )
            processor = object.__new__(MVPJobProcessor)
            processor.config = config("render", pexels_assets=True)
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
            pexels = FakePexelsClient()
            FakeAssetAwareRenderer.resolved_assets = {}

            with (
                patch("open_storyline.mvp.pipeline.probe_media", return_value=MediaInfo(30_000, 1920, 1080, True)),
                patch("open_storyline.mvp.pipeline.extract_audio_for_stt", side_effect=lambda _source, target: target),
                patch("open_storyline.mvp.pipeline.detect_scene_boundaries", return_value=scene_report),
                patch("open_storyline.mvp.pipeline.sample_frames", return_value=frame_manifest),
                patch("open_storyline.mvp.pipeline.VisualUnderstandingPlanner", FakeVisualPlanner),
                patch("open_storyline.mvp.pipeline.AgenticEditPlanner", FakeStockEditPlanner),
                patch("open_storyline.mvp.pipeline.NineRouterClient.from_config", return_value=FakeRemoteClient()),
                patch("open_storyline.mvp.pipeline.PexelsClient.from_config", return_value=pexels),
                patch(
                    "open_storyline.mvp.pipeline.RemoteImageCascade.from_config",
                    side_effect=AssertionError("generated fallback called"),
                ),
                patch("open_storyline.mvp.pipeline.ShortsPlanner", FakePlanner),
                patch("open_storyline.mvp.pipeline.AgenticShortRenderer", FakeAssetAwareRenderer),
                patch("open_storyline.mvp.pipeline.CPUShortRenderer", side_effect=AssertionError("legacy renderer called")),
            ):
                await processor("f" * 32, store)

            registered = dict(store.registered)
            manifest = json.loads((root / "output" / "manifest.json").read_text(encoding="utf-8"))
            asset_manifest = json.loads(
                (root / "output" / "asset_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(pexels.calls, ["stock-1"])
            self.assertEqual(registered["asset-stock-1.png"], "stock_image")
            self.assertTrue(FakeAssetAwareRenderer.resolved_assets["stock-1"].is_file())
            self.assertEqual(asset_manifest["provider_call_counts"], {"9router": 0, "pexels": 1})
            self.assertEqual(manifest["agentic"]["asset_policy"]["stock_effective"], "auto")


if __name__ == "__main__":
    unittest.main()
