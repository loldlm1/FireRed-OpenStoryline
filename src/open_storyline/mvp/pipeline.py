from __future__ import annotations

from pathlib import Path
from typing import Any
import asyncio
import json

from open_storyline.config import Settings
from open_storyline.mvp.edit_plan import (
    AgenticArtifactNames,
    EditPlanError,
    SUPPORTED_CAPABILITIES,
    build_shadow_edit_plan,
    resolve_agentic_server_mode,
)
from open_storyline.mvp.ffmpega import EffectsPlanner, FFMPEGAClient, ffmpega_enabled
from open_storyline.mvp.frame_sampling import sample_frames
from open_storyline.mvp.jobs import JobStore
from open_storyline.mvp.ninerouter import NineRouterClient
from open_storyline.mvp.render import (
    CPUShortRenderer,
    RenderSettings,
    extract_frame_data_urls,
    probe_media,
)
from open_storyline.mvp.preflight import build_preflight
from open_storyline.mvp.scene_boundaries import detect_scene_boundaries
from open_storyline.mvp.shorts import ShortsPlanner
from open_storyline.mvp.visual_understanding import VisualUnderstandingPlanner
from open_storyline.utils.remote_stt import MistralSTTClient, RemoteSTTError, extract_audio_for_stt


class MVPJobProcessor:
    """Remote-inference pipeline; local work is restricted to deterministic FFmpeg."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.stt = MistralSTTClient.from_config(config.remote_asr)

    async def __call__(self, job_id: str, store: JobStore) -> dict[str, Any]:
        state = await store.load(job_id)
        request = state.get("request") or {}
        agentic_requested = request.get("edit_mode") == "agentic"
        server_mode = None
        if agentic_requested:
            server_mode = resolve_agentic_server_mode(self.config.agentic_editing)
            if server_mode == "off":
                raise EditPlanError(
                    "AGENTIC_EDITING_DISABLED",
                    "agentic editing is disabled on this server",
                )
            if server_mode == "render":
                raise EditPlanError(
                    "AGENTIC_RENDER_UNAVAILABLE",
                    "agentic rendering is not available until the compositor sprint",
                )
        source = await store.source_path(job_id)
        work_dir = store.work_dir(job_id)
        output_dir = store.output_dir(job_id)

        media = await asyncio.to_thread(probe_media, source)
        if not media.has_audio:
            raise RemoteSTTError("MEDIA_HAS_NO_AUDIO", "source video must contain an audio stream")
        await store.update(job_id, progress=0.18, stage="extracting_audio")
        audio = await asyncio.to_thread(extract_audio_for_stt, source, work_dir / "audio.mp3")

        await store.update(job_id, progress=0.28, stage="remote_transcription")
        transcript = await self.stt.transcribe(audio, language=self.config.remote_asr.language)
        transcript_path = output_dir / "transcript.json"
        transcript_path.write_text(json.dumps({
            "model": transcript.model,
            "text": transcript.text,
            "segments": transcript.segments,
            "attempts": [attempt.to_dict() for attempt in transcript.attempts],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        await store.register_artifact(job_id, transcript_path, kind="transcript")

        names = AgenticArtifactNames()
        scene_report = None
        frame_manifest = None
        visual_understanding = None
        remote_client = NineRouterClient.from_config(self.config.ninerouter)
        if agentic_requested:
            agentic_config = self.config.agentic_editing
            await store.update(job_id, progress=0.42, stage="detecting_scenes")
            scene_report = await asyncio.to_thread(
                detect_scene_boundaries,
                source,
                source_duration_ms=media.duration_ms,
                threshold=agentic_config.scene_threshold,
                min_scene_duration_ms=agentic_config.min_scene_duration_ms,
                max_scenes=agentic_config.max_scenes,
            )
            scene_path = output_dir / names.scene_boundaries
            scene_path.write_text(
                json.dumps(scene_report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, scene_path, kind="scene_boundaries")

            await store.update(job_id, progress=0.48, stage="sampling_agentic_frames")
            frame_manifest = await asyncio.to_thread(
                sample_frames,
                source,
                scene_report=scene_report,
                source_width=media.width,
                source_height=media.height,
                max_frames=agentic_config.vision_frame_count,
                max_width=agentic_config.vision_frame_max_width,
                max_height=agentic_config.vision_frame_max_height,
                max_frame_bytes=agentic_config.vision_frame_max_bytes,
            )
            await store.update(job_id, progress=0.54, stage="remote_visual_understanding")
            visual_understanding = await VisualUnderstandingPlanner(remote_client).plan(
                frame_manifest=frame_manifest,
                scene_report=scene_report,
                editing_prompt=state["prompt"],
                transcript_text=transcript.text,
            )
            visual_path = output_dir / names.visual_understanding
            visual_path.write_text(
                json.dumps(visual_understanding.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, visual_path, kind="visual_understanding")
            frames = frame_manifest.image_data_urls
        else:
            await store.update(job_id, progress=0.48, stage="sampling_frames")
            frames = await asyncio.to_thread(
                extract_frame_data_urls,
                source,
                duration_ms=media.duration_ms,
                count=self.config.mvp.frame_count,
            )

        await store.update(job_id, progress=0.58, stage="remote_planning")
        planner = ShortsPlanner(remote_client)
        plan = await planner.plan(
            editing_prompt=state["prompt"],
            transcript_text=transcript.text,
            transcript_segments=transcript.segments,
            source_duration_ms=media.duration_ms,
            max_clips=int((state.get("request") or {}).get("max_clips") or 8),
            frame_data_urls=frames,
        )

        agentic_manifest = None
        if agentic_requested:
            shorts_plan_path = output_dir / names.shorts_plan
            shorts_plan_path.write_text(
                json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, shorts_plan_path, kind="shorts_plan")

            edit_plan = build_shadow_edit_plan(
                plan.clips,
                source_duration_ms=media.duration_ms,
            )
            edit_plan_path = output_dir / names.edit_plan
            edit_plan_path.write_text(
                json.dumps(edit_plan.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, edit_plan_path, kind="edit_plan")

            preflight = build_preflight(
                edit_plan,
                available_capabilities=SUPPORTED_CAPABILITIES,
                asset_policy=str(request.get("asset_policy") or "auto"),
            )
            preflight_path = output_dir / names.preflight
            preflight_path.write_text(
                json.dumps(preflight.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, preflight_path, kind="edit_preflight")
            if preflight.blocking:
                raise EditPlanError("EDIT_PREFLIGHT_BLOCKED", "agentic edit preflight is blocked")
            agentic_manifest = {
                "mode": server_mode,
                "scene_boundaries": names.scene_boundaries,
                "visual_understanding": names.visual_understanding,
                "vision_frame_count": len(frame_manifest.frames) if frame_manifest else 0,
                "vision_call_count": 1 if visual_understanding else 0,
                "edit_plan": names.edit_plan,
                "preflight": names.preflight,
                "preflight_status": preflight.status,
            }

        await store.update(job_id, progress=0.68, stage="rendering")
        renderer = CPUShortRenderer(RenderSettings(
            width=self.config.mvp.render_width,
            height=self.config.mvp.render_height,
            fps=self.config.mvp.render_fps,
            preset=self.config.mvp.render_preset,
            crf=self.config.mvp.render_crf,
        ))
        rendered = await asyncio.to_thread(
            renderer.render_plan,
            source=source,
            clips=plan.clips,
            transcript_segments=transcript.segments,
            destination_dir=output_dir,
        )
        effects_plan = None
        final_outputs = []
        ffmpega = None
        if ffmpega_enabled(self.config.ffmpega):
            await store.update(job_id, progress=0.88, stage="planning_effects")
            effects_plan = await EffectsPlanner(
                NineRouterClient.from_config(self.config.ninerouter)
            ).plan(state["prompt"])
            if effects_plan.effects:
                ffmpega = FFMPEGAClient.from_config(self.config.ffmpega)
        for item in rendered:
            final_video = item.video_path
            if ffmpega is not None and effects_plan is not None:
                enhanced = item.video_path.with_name(f"{item.video_path.stem}-effects.mp4")
                final_video = await ffmpega.apply(
                    source=item.video_path,
                    destination=enhanced,
                    plan=effects_plan,
                )
                item.video_path.unlink(missing_ok=True)
            await store.register_artifact(job_id, final_video, kind="video")
            if item.subtitle_path is not None:
                await store.register_artifact(job_id, item.subtitle_path, kind="subtitles")
            final_outputs.append({
                "video": final_video.name,
                "subtitles": item.subtitle_path.name if item.subtitle_path else None,
                "clip": item.clip.to_dict(),
            })

        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "job_id": job_id,
            "source": {
                "filename": state["input"]["original_filename"],
                "duration_ms": media.duration_ms,
                "width": media.width,
                "height": media.height,
            },
            "stt": {
                "model": transcript.model,
                "attempts": [attempt.to_dict() for attempt in transcript.attempts],
            },
            "plan": plan.to_dict(),
            "agentic": agentic_manifest,
            "effects": effects_plan.to_dict() if effects_plan is not None else {"effects": []},
            "outputs": final_outputs,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        await store.register_artifact(job_id, manifest_path, kind="manifest")
        return {
            "stage": "completed",
            "stt_model": transcript.model,
            "clip_count": len(rendered),
        }
