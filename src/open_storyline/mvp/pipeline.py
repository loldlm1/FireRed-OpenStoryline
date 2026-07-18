from __future__ import annotations

from pathlib import Path
from typing import Any
import asyncio
import json

from open_storyline.config import Settings
from open_storyline.mvp.assets import (
    generated_asset_server_cap,
    generated_asset_size,
    generated_assets_enabled,
    resolve_assets,
    write_asset_manifest,
)
from open_storyline.mvp.edit_plan import (
    AgenticEditPlanner,
    AgenticArtifactNames,
    EditPlanError,
    resolve_agentic_server_mode,
)
from open_storyline.mvp.ffmpega import (
    AGENTIC_FINISHING_SKILLS,
    DETERMINISTIC_SKILLS,
    EffectsPlanner,
    FFMPEGAClient,
    ffmpega_enabled,
)
from open_storyline.mvp.frame_sampling import sample_frames
from open_storyline.mvp.compositor import REFRAME_RENDER_CAPABILITIES
from open_storyline.mvp.creative_qa import (
    QAInput,
    creative_qa_enabled,
    creative_qa_strict,
    generate_creative_qa_artifacts,
    semantic_qa_enabled,
    semantic_qa_frame_limit,
)
from open_storyline.mvp.jobs import JobStore
from open_storyline.mvp.ninerouter import NineRouterClient
from open_storyline.mvp.render import (
    AgenticShortRenderer,
    CPUShortRenderer,
    RenderSettings,
    extract_frame_data_urls,
    probe_media,
)
from open_storyline.mvp.preflight import build_preflight
from open_storyline.mvp.scene_boundaries import detect_scene_boundaries
from open_storyline.mvp.shorts import ShortsPlanner, build_shorts_plan_artifact
from open_storyline.mvp.stock import PexelsClient, pexels_enabled, pexels_server_cap
from open_storyline.mvp.visual_understanding import VisualUnderstandingPlanner
from open_storyline.utils.remote_stt import MistralSTTClient, RemoteSTTError, extract_audio_for_stt
from open_storyline.utils.remote_image import RemoteImageCascade


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
        effective_asset_policy = "off"
        effective_generated_asset_cap = 0
        effective_stock_policy = "off"
        effective_stock_asset_cap = 0
        pexels_client = None
        if agentic_requested:
            server_mode = resolve_agentic_server_mode(self.config.agentic_editing)
            if server_mode == "off":
                raise EditPlanError(
                    "AGENTIC_EDITING_DISABLED",
                    "agentic editing is disabled on this server",
                )
            server_asset_cap = generated_asset_server_cap(self.config.agentic_editing)
            job_asset_cap = int(
                request.get("max_generated_assets_per_clip")
                if request.get("max_generated_assets_per_clip") is not None
                else server_asset_cap
            )
            effective_generated_asset_cap = min(
                max(0, job_asset_cap),
                server_asset_cap,
                self.config.agentic_editing.max_assets_per_clip,
            )
            if (
                str(request.get("asset_policy") or "auto") == "auto"
                and generated_assets_enabled(self.config.agentic_editing)
                and effective_generated_asset_cap > 0
            ):
                effective_asset_policy = "auto"
            stock_server_cap = pexels_server_cap(self.config.agentic_editing)
            job_stock_cap = int(
                request.get("max_stock_assets_per_clip")
                if request.get("max_stock_assets_per_clip") is not None
                else stock_server_cap
            )
            effective_stock_asset_cap = min(
                max(0, job_stock_cap),
                stock_server_cap,
                self.config.agentic_editing.max_assets_per_clip,
            )
            if (
                str(request.get("stock_policy") or "off") == "auto"
                and pexels_enabled(self.config.agentic_editing)
                and effective_stock_asset_cap > 0
            ):
                pexels_client = PexelsClient.from_config(self.config.agentic_editing)
                effective_stock_policy = "auto"
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
        visual_attempts: list[dict[str, Any]] = []
        shorts_attempts: list[dict[str, Any]] = []
        edit_planner_attempts: list[dict[str, Any]] = []
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
            visual_attempts = [
                attempt.to_dict()
                for attempt in getattr(remote_client, "last_attempts", ())
            ]
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
        shorts_attempts = [
            attempt.to_dict()
            for attempt in getattr(remote_client, "last_attempts", ())
        ]

        agentic_manifest = None
        if agentic_requested:
            shorts_plan_artifact = build_shorts_plan_artifact(
                plan,
                transcript_segments=transcript.segments,
                scene_report=scene_report,
                visual_understanding=visual_understanding,
            )
            shorts_plan_path = output_dir / names.shorts_plan
            shorts_plan_path.write_text(
                json.dumps(shorts_plan_artifact, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, shorts_plan_path, kind="shorts_plan")

            await store.update(job_id, progress=0.62, stage="planning_agentic_edit")
            edit_plan = await AgenticEditPlanner(remote_client).plan(
                editing_prompt=state["prompt"],
                shorts_plan=plan,
                shorts_plan_artifact=shorts_plan_artifact,
                transcript_segments=transcript.segments,
                scene_report=scene_report,
                visual_understanding=visual_understanding,
                source_duration_ms=media.duration_ms,
                asset_policy=effective_asset_policy,
                max_segments_per_clip=self.config.agentic_editing.max_segments_per_clip,
                max_overlays_per_clip=self.config.agentic_editing.max_overlays_per_clip,
                max_assets_per_clip=min(
                    self.config.agentic_editing.max_assets_per_clip,
                    effective_generated_asset_cap + effective_stock_asset_cap,
                ),
                max_generated_assets_per_clip=effective_generated_asset_cap,
                max_stock_assets_per_clip=effective_stock_asset_cap,
                stock_policy=effective_stock_policy,
                renderer_capabilities=REFRAME_RENDER_CAPABILITIES,
            )
            edit_planner_attempts = [
                attempt.to_dict()
                for attempt in getattr(remote_client, "last_attempts", ())
            ]
            edit_plan_path = output_dir / names.edit_plan
            edit_plan_path.write_text(
                json.dumps(edit_plan.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, edit_plan_path, kind="edit_plan")

            planned_asset_ids = {
                asset.id
                for clip in edit_plan.clips
                for asset in clip.asset_requests
            }
            pending_asset_ids = (
                planned_asset_ids
                if (
                    effective_asset_policy == "auto"
                    or effective_stock_policy == "auto"
                )
                else set()
            )
            preliminary_preflight = build_preflight(
                edit_plan,
                available_capabilities=REFRAME_RENDER_CAPABILITIES,
                asset_policy=effective_asset_policy,
                stock_policy=effective_stock_policy,
                pending_asset_ids=pending_asset_ids,
                known_region_ids=(region.id for region in visual_understanding.regions),
                known_track_ids=(track.id for track in visual_understanding.tracks),
                known_evidence_ids_by_clip={
                    int(clip["clip_index"]): clip["evidence_ids"]
                    for clip in shorts_plan_artifact["clips"]
                },
                max_segments_per_clip=self.config.agentic_editing.max_segments_per_clip,
                max_overlays_per_clip=self.config.agentic_editing.max_overlays_per_clip,
                max_assets_per_clip=self.config.agentic_editing.max_assets_per_clip,
            )
            shadow_allows_blocked = (
                server_mode == "shadow"
                and self.config.agentic_editing.shadow_allow_blocked_plans
            )
            if preliminary_preflight.blocking and not shadow_allows_blocked:
                raise EditPlanError("EDIT_PREFLIGHT_BLOCKED", "agentic edit preflight is blocked")

            if server_mode == "render":
                if planned_asset_ids:
                    await store.update(job_id, progress=0.66, stage="resolving_assets")
                generated_asset_ids = {
                    asset.id
                    for clip in edit_plan.clips
                    for asset in clip.asset_requests
                    if asset.kind == "generated_image"
                }
                stock_asset_ids = planned_asset_ids - generated_asset_ids
                asset_result = await resolve_assets(
                    edit_plan,
                    output_dir=output_dir,
                    asset_policy=effective_asset_policy,
                    stock_policy=effective_stock_policy,
                    max_generated_assets_per_clip=effective_generated_asset_cap,
                    max_stock_assets_per_clip=effective_stock_asset_cap,
                    cascade=(
                        RemoteImageCascade.from_config(self.config.remote_image)
                        if generated_asset_ids
                        else None
                    ),
                    pexels=(pexels_client if stock_asset_ids else None),
                    size=(
                        generated_asset_size(self.config.remote_image)
                        if generated_asset_ids
                        else "1024x1024"
                    ),
                )
                resolved_asset_ids = set(asset_result.paths)
                preflight = build_preflight(
                    edit_plan,
                    available_capabilities=REFRAME_RENDER_CAPABILITIES,
                    asset_policy=effective_asset_policy,
                    stock_policy=effective_stock_policy,
                    resolved_asset_ids=resolved_asset_ids,
                    known_region_ids=(region.id for region in visual_understanding.regions),
                    known_track_ids=(track.id for track in visual_understanding.tracks),
                    known_evidence_ids_by_clip={
                        int(clip["clip_index"]): clip["evidence_ids"]
                        for clip in shorts_plan_artifact["clips"]
                    },
                    max_segments_per_clip=self.config.agentic_editing.max_segments_per_clip,
                    max_overlays_per_clip=self.config.agentic_editing.max_overlays_per_clip,
                    max_assets_per_clip=self.config.agentic_editing.max_assets_per_clip,
                )
                if preflight.blocking:
                    raise EditPlanError("EDIT_PREFLIGHT_BLOCKED", "resolved agentic edit preflight is blocked")
            else:
                asset_result = write_asset_manifest(
                    edit_plan,
                    output_dir=output_dir,
                    asset_policy=effective_asset_policy,
                    stock_policy=effective_stock_policy,
                    status="shadow_planned" if planned_asset_ids else "no_requests",
                )
                preflight = preliminary_preflight

            asset_kinds = {
                asset.id: asset.kind
                for clip in edit_plan.clips
                for asset in clip.asset_requests
            }
            for asset_id, path in asset_result.paths.items():
                await store.register_artifact(
                    job_id,
                    path,
                    kind=asset_kinds[asset_id],
                )
            await store.register_artifact(
                job_id,
                asset_result.manifest_path,
                kind="asset_manifest",
            )
            preflight_path = output_dir / names.preflight
            preflight_path.write_text(
                json.dumps(preflight.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, preflight_path, kind="edit_preflight")
            agentic_manifest = {
                "mode": server_mode,
                "scene_boundaries": names.scene_boundaries,
                "visual_understanding": names.visual_understanding,
                "vision_frame_count": len(frame_manifest.frames) if frame_manifest else 0,
                "vision_call_count": 1 if visual_understanding else 0,
                "visual_attempts": visual_attempts,
                "shorts_attempts": shorts_attempts,
                "edit_plan": names.edit_plan,
                "edit_planner": {
                    "model": remote_client.model,
                    "schema_version": edit_plan.version,
                    "planner_version": edit_plan.planner_version,
                    "prompt_version": edit_plan.prompt_version,
                    "attempts": edit_planner_attempts,
                },
                "preflight": names.preflight,
                "preflight_status": preflight.status,
                "shadow_blocked": bool(preflight.blocking and shadow_allows_blocked),
                "asset_manifest": names.asset_manifest,
                "asset_policy": {
                    "requested": str(request.get("asset_policy") or "auto"),
                    "effective": effective_asset_policy,
                    "max_generated_assets_per_clip": effective_generated_asset_cap,
                    "stock_requested": str(request.get("stock_policy") or "off"),
                    "stock_effective": effective_stock_policy,
                    "max_stock_assets_per_clip": effective_stock_asset_cap,
                },
                "assets": {
                    "requested": int(asset_result.manifest["requested_count"]),
                    "resolved": int(asset_result.manifest["resolved_count"]),
                    "provider_calls": asset_result.provider_call_count,
                },
            }

        await store.update(job_id, progress=0.68, stage="rendering")
        render_settings = RenderSettings(
            width=self.config.mvp.render_width,
            height=self.config.mvp.render_height,
            fps=self.config.mvp.render_fps,
            preset=self.config.mvp.render_preset,
            crf=self.config.mvp.render_crf,
        )
        if agentic_requested and server_mode == "render":
            agentic_result = await asyncio.to_thread(
                AgenticShortRenderer(render_settings).render_plan,
                source=source,
                edit_plan=edit_plan,
                selected_clips=plan.clips,
                visual_understanding=visual_understanding,
                transcript_segments=transcript.segments,
                destination_dir=output_dir,
                source_media=media,
                crop_hysteresis_ratio=self.config.agentic_editing.crop_hysteresis_ratio,
                crop_smoothing_alpha=self.config.agentic_editing.crop_smoothing_alpha,
                max_crop_velocity_ratio_per_second=(
                    self.config.agentic_editing.max_crop_velocity_ratio_per_second
                ),
                resolved_assets=asset_result.paths,
            )
            rendered = list(agentic_result.rendered)
            render_execution_path = output_dir / names.render_execution
            render_execution_path.write_text(
                json.dumps(agentic_result.execution, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(
                job_id,
                render_execution_path,
                kind="render_execution",
            )
            agentic_manifest["render_execution"] = names.render_execution
        else:
            rendered = await asyncio.to_thread(
                CPUShortRenderer(render_settings).render_plan,
                source=source,
                clips=plan.clips,
                transcript_segments=transcript.segments,
                destination_dir=output_dir,
            )
        effects_plan = None
        final_outputs = []
        qa_inputs: list[QAInput] = []
        ffmpega = None
        if ffmpega_enabled(self.config.ffmpega):
            await store.update(job_id, progress=0.88, stage="planning_effects")
            effects_plan = await EffectsPlanner(
                NineRouterClient.from_config(self.config.ninerouter)
            ).plan(
                state["prompt"],
                allowed_skills=(
                    AGENTIC_FINISHING_SKILLS
                    if agentic_requested
                    else DETERMINISTIC_SKILLS
                ),
            )
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
            qa_inputs.append(QAInput(
                clip_index=len(qa_inputs) + 1,
                video_path=final_video,
                expected_duration_ms=item.clip.duration_ms,
                subtitle_path=item.subtitle_path,
            ))

        if agentic_requested and server_mode == "render":
            qa_manifest: dict[str, Any] = {"enabled": False, "status": "disabled"}
            try:
                if creative_qa_enabled(self.config.agentic_editing):
                    await store.update(job_id, progress=0.94, stage="post_render_qa")
                    strict_qa = creative_qa_strict(self.config.agentic_editing)
                    semantic_enabled = semantic_qa_enabled(self.config.agentic_editing)
                    qa_artifacts = await generate_creative_qa_artifacts(
                        output_dir=output_dir,
                        inputs=qa_inputs,
                        edit_plan=edit_plan.to_dict(),
                        render_execution=agentic_result.execution,
                        expected_width=render_settings.width,
                        expected_height=render_settings.height,
                        strict=strict_qa,
                        semantic_enabled=semantic_enabled,
                        semantic_max_frames=semantic_qa_frame_limit(
                            self.config.agentic_editing
                        ),
                        semantic_client=remote_client,
                    )
                    registered = []
                    for path, kind in (
                        (qa_artifacts.render_qa_path, "render_qa"),
                        (qa_artifacts.rhythm_qa_path, "retention_rhythm_qa"),
                        (qa_artifacts.conformance_path, "creative_conformance"),
                    ):
                        await store.register_artifact(job_id, path, kind=kind)
                        registered.append(path.name)
                    qa_manifest = {
                        "enabled": True,
                        "strict": strict_qa,
                        "semantic_enabled": semantic_enabled,
                        "status": qa_artifacts.conformance.get("status", "unavailable"),
                        "render_qa": names.render_qa,
                        "retention_rhythm_qa": names.retention_rhythm_qa,
                        "creative_conformance": names.creative_conformance,
                        "registered": registered,
                    }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                qa_manifest = {
                    "enabled": True,
                    "status": "unavailable",
                    "error_code": str(getattr(exc, "code", "CREATIVE_QA_UNAVAILABLE"))[:80],
                }
            agentic_manifest["qa"] = qa_manifest

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
