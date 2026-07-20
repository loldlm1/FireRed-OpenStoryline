from __future__ import annotations

from pathlib import Path
from typing import Any
import asyncio
import json

from open_storyline.config import Settings
from open_storyline.mvp.activity import ActivityService, STAGES, retryable_error
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
from open_storyline.mvp.frame_sampling import FrameManifest, sample_frames
from open_storyline.mvp.frame_quality import (
    FRAME_QUALITY_VERSION,
    build_frame_quality_report,
)
from open_storyline.mvp.compositor import REFRAME_RENDER_CAPABILITIES
from open_storyline.mvp.creative_intent import (
    build_creative_intent,
    validate_creative_intent_conformance,
    validate_intent_capabilities,
)
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
from open_storyline.mvp.observability import emit_event
from open_storyline.mvp.render import (
    AgenticShortRenderer,
    CPUShortRenderer,
    RENDER_QUALITY_PROFILE_VERSION,
    extract_frame_data_urls,
    probe_media,
    render_settings_from_config,
)
from open_storyline.mvp.preflight import build_preflight
from open_storyline.mvp.promotion import (
    build_render_promotion_report,
    enforce_render_promotion,
    render_promotion_mode,
)
from open_storyline.mvp.scene_boundaries import detect_scene_boundaries
from open_storyline.mvp.shorts import ShortsPlanner, build_shorts_plan_artifact
from open_storyline.mvp.stock import PexelsClient, pexels_enabled, pexels_server_cap
from open_storyline.mvp.visual_coverage import build_clip_visual_coverage
from open_storyline.mvp.visual_understanding import (
    VisualUnderstanding,
    VisualUnderstandingPlanner,
    merge_visual_understandings,
    scope_visual_understanding,
)
from open_storyline.utils.remote_stt import MistralSTTClient, RemoteSTTError, extract_audio_for_stt
from open_storyline.utils.remote_image import RemoteImageCascade


class MVPJobProcessor:
    """Remote-inference pipeline; local work is restricted to deterministic FFmpeg."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.stt = MistralSTTClient.from_config(config.remote_asr)

    async def __call__(self, job_id: str, store: JobStore) -> dict[str, Any]:
        state = await store.load(job_id)
        activity = ActivityService(store)
        request = state.get("request") or {}
        prior_quality_feedback = request.get("prior_attempt_quality_feedback")
        if not isinstance(prior_quality_feedback, dict):
            prior_quality_feedback = {}
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
            requested_asset_policy = str(
                request.get("asset_policy") or "auto"
            ).strip().lower()
            if (
                requested_asset_policy in {"auto", "required"}
                and generated_assets_enabled(self.config.agentic_editing)
                and effective_generated_asset_cap > 0
            ):
                effective_asset_policy = requested_asset_policy
            elif requested_asset_policy == "required":
                raise EditPlanError(
                    "CREATIVE_INTENT_CAPABILITY_UNAVAILABLE",
                    "required generated-image capability is unavailable",
                )
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
            requested_stock_policy = str(
                request.get("stock_policy") or "off"
            ).strip().lower()
            if (
                requested_stock_policy in {"auto", "required"}
                and pexels_enabled(self.config.agentic_editing)
                and effective_stock_asset_cap > 0
            ):
                pexels_client = PexelsClient.from_config(self.config.agentic_editing)
                effective_stock_policy = requested_stock_policy
            elif requested_stock_policy == "required":
                raise EditPlanError(
                    "CREATIVE_INTENT_CAPABILITY_UNAVAILABLE",
                    "required Pexels capability is unavailable",
                )
            preliminary_intent = build_creative_intent(
                state["prompt"],
                request,
                selected_clip_count=1,
            )
            try:
                validate_intent_capabilities(
                    preliminary_intent,
                    generated_available=effective_asset_policy != "off",
                    stock_available=effective_stock_policy != "off",
                )
            except ValueError as exc:
                raise EditPlanError(
                    "CREATIVE_INTENT_CAPABILITY_UNAVAILABLE",
                    str(exc),
                ) from exc
        source = await store.source_path(job_id)
        work_dir = store.work_dir(job_id)
        output_dir = store.output_dir(job_id)

        media = await asyncio.to_thread(probe_media, source)
        if not media.has_audio:
            raise RemoteSTTError("MEDIA_HAS_NO_AUDIO", "source video must contain an audio stream")
        await activity.stage(job_id, "extracting_audio")
        audio = await asyncio.to_thread(extract_audio_for_stt, source, work_dir / "audio.mp3")
        await activity.emit_safely(
            job_id,
            stage="extracting_audio",
            category="analysis",
            status="completed",
            message_key="activity.analysis.audio_ready",
            progress=STAGES["extracting_audio"].progress,
            tool="FFmpeg",
        )

        await activity.stage(job_id, "remote_transcription")
        transcript = await self.stt.transcribe(audio, language=self.config.remote_asr.language)
        await activity.emit_safely(
            job_id,
            stage="remote_transcription",
            category="provider",
            status="completed",
            message_key="activity.provider.transcription_ready",
            progress=STAGES["remote_transcription"].progress,
            provider="Mistral",
            tool="Voxtral",
            attempt_number=max(1, len(transcript.attempts)),
        )
        transcript_path = output_dir / "transcript.json"
        transcript_path.write_text(json.dumps({
            "model": transcript.model,
            "text": transcript.text,
            "segments": transcript.segments,
            "attempts": [attempt.to_dict() for attempt in transcript.attempts],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        await store.register_artifact(job_id, transcript_path, kind="transcript")

        names = AgenticArtifactNames()
        creative_intent = None
        creative_conformance = None
        scene_report = None
        frame_manifest = None
        global_visual_understanding = None
        visual_understanding = None
        clip_frame_manifests: dict[int, FrameManifest] = {}
        clip_visual_understandings: dict[int, VisualUnderstanding] = {}
        clip_vision_call_count = 0
        visual_attempts: list[dict[str, Any]] = []
        shorts_attempts: list[dict[str, Any]] = []
        edit_planner_attempts: list[dict[str, Any]] = []
        remote_client = NineRouterClient.from_config(self.config.ninerouter)
        if agentic_requested:
            agentic_config = self.config.agentic_editing
            await activity.stage(job_id, "detecting_scenes")
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
            await activity.emit_safely(
                job_id,
                stage="detecting_scenes",
                category="analysis",
                status="completed",
                message_key="activity.analysis.scenes_ready",
                progress=STAGES["detecting_scenes"].progress,
                tool="FFmpeg",
            )

            await activity.stage(job_id, "sampling_agentic_frames")
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
            await activity.emit_safely(
                job_id,
                stage="sampling_agentic_frames",
                category="analysis",
                status="completed",
                message_key="activity.analysis.frames_ready",
                progress=STAGES["sampling_agentic_frames"].progress,
                tool="FFmpeg",
                sampled_frames=len(frame_manifest.frames),
            )
            await activity.stage(job_id, "remote_visual_understanding")
            global_visual_understanding = await VisualUnderstandingPlanner(remote_client).plan(
                frame_manifest=frame_manifest,
                scene_report=scene_report,
                editing_prompt=state["prompt"],
                transcript_text=transcript.text,
            )
            visual_attempts = [
                attempt.to_dict()
                for attempt in getattr(remote_client, "last_attempts", ())
            ]
            await activity.emit_safely(
                job_id,
                stage="remote_visual_understanding",
                category="provider",
                status="completed",
                message_key="activity.provider.video_understood",
                progress=STAGES["remote_visual_understanding"].progress,
                provider="9Router",
                tool="Visual understanding",
                attempt_number=max(1, len(visual_attempts)),
            )
            visual_understanding = global_visual_understanding
            frames = frame_manifest.image_data_urls
        else:
            await activity.stage(job_id, "sampling_frames")
            frames = await asyncio.to_thread(
                extract_frame_data_urls,
                source,
                duration_ms=media.duration_ms,
                count=self.config.mvp.frame_count,
            )
            await activity.emit_safely(
                job_id,
                stage="sampling_frames",
                category="analysis",
                status="completed",
                message_key="activity.analysis.frames_ready",
                progress=STAGES["sampling_frames"].progress,
                tool="FFmpeg",
                sampled_frames=len(frames),
            )
            await activity.emit_safely(
                job_id,
                stage="sampling_frames",
                category="provider",
                status="skipped",
                message_key="activity.provider.visual_understanding_skipped",
                progress=STAGES["sampling_frames"].progress,
            )

        await activity.stage(job_id, "remote_planning")
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
        await activity.emit_safely(
            job_id,
            stage="remote_planning",
            category="planning",
            status="completed",
            message_key="activity.planning.clips_selected",
            progress=STAGES["remote_planning"].progress,
            provider="9Router",
            tool="Clip planner",
            attempt_number=max(1, len(shorts_attempts)),
            selected_clips=len(plan.clips),
        )

        agentic_manifest = None
        if agentic_requested:
            agentic_config = self.config.agentic_editing

            async def analyze_clip_windows(
                clip_indexes: set[int],
                *,
                max_frames: int,
            ) -> None:
                nonlocal clip_vision_call_count
                for clip_index, clip in enumerate(plan.clips, start=1):
                    if clip_index not in clip_indexes:
                        continue
                    local_manifest = await asyncio.to_thread(
                        sample_frames,
                        source,
                        scene_report=scene_report,
                        source_width=media.width,
                        source_height=media.height,
                        max_frames=max_frames,
                        max_width=agentic_config.vision_frame_max_width,
                        max_height=agentic_config.vision_frame_max_height,
                        max_frame_bytes=agentic_config.vision_frame_max_bytes,
                        clip_start_ms=clip.start_ms,
                        clip_end_ms=clip.end_ms,
                        id_prefix=f"clip-{clip_index:02d}-",
                    )
                    local_understanding = await VisualUnderstandingPlanner(remote_client).plan(
                        frame_manifest=local_manifest,
                        scene_report=scene_report,
                        editing_prompt=state["prompt"],
                        transcript_text=" ".join(
                            str(segment.get("text") or "").strip()
                            for segment in transcript.segments
                            if int(segment.get("end") or 0) > clip.start_ms
                            and int(segment.get("start") or 0) < clip.end_ms
                        ),
                    )
                    clip_vision_call_count += 1
                    clip_frame_manifests[clip_index] = local_manifest
                    clip_visual_understandings[clip_index] = scope_visual_understanding(
                        local_understanding,
                        clip_index=clip_index,
                    )
                    visual_attempts.extend(
                        attempt.to_dict()
                        for attempt in getattr(remote_client, "last_attempts", ())
                    )

            await activity.stage(job_id, "sampling_agentic_frames")
            await analyze_clip_windows(
                set(range(1, len(plan.clips) + 1)),
                max_frames=agentic_config.vision_clip_frame_count,
            )
            visual_understanding = merge_visual_understandings(
                global_visual_understanding,
                tuple(
                    clip_visual_understandings[index]
                    for index in sorted(clip_visual_understandings)
                ),
            )
            visual_path = output_dir / names.visual_understanding
            visual_path.write_text(
                json.dumps(visual_understanding.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, visual_path, kind="visual_understanding")
            await activity.emit_safely(
                job_id,
                stage="sampling_agentic_frames",
                category="analysis",
                status="completed",
                message_key="activity.analysis.frames_ready",
                progress=STAGES["sampling_agentic_frames"].progress,
                tool="FFmpeg + 9Router clip-local analysis",
                sampled_frames=sum(
                    len(manifest.frames) for manifest in clip_frame_manifests.values()
                ),
            )

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

            creative_intent = build_creative_intent(
                state["prompt"],
                request,
                selected_clip_count=len(plan.clips),
            )
            creative_intent_path = output_dir / names.creative_intent
            creative_intent_path.write_text(
                json.dumps(creative_intent.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(
                job_id,
                creative_intent_path,
                kind="creative_intent",
            )

            await activity.stage(job_id, "planning_agentic_edit")
            async def plan_agentic_edit(
                visual_coverage_feedback: dict[str, Any] | None = None,
            ):
                return await AgenticEditPlanner(remote_client).plan(
                    editing_prompt=state["prompt"],
                    shorts_plan=plan,
                    shorts_plan_artifact=shorts_plan_artifact,
                    transcript_segments=transcript.segments,
                    scene_report=scene_report,
                    visual_understanding=visual_understanding,
                    source_duration_ms=media.duration_ms,
                    asset_policy=effective_asset_policy,
                    max_segments_per_clip=agentic_config.max_segments_per_clip,
                    max_overlays_per_clip=agentic_config.max_overlays_per_clip,
                    max_assets_per_clip=min(
                        agentic_config.max_assets_per_clip,
                        effective_generated_asset_cap + effective_stock_asset_cap,
                    ),
                    max_generated_assets_per_clip=effective_generated_asset_cap,
                    max_stock_assets_per_clip=effective_stock_asset_cap,
                    stock_policy=effective_stock_policy,
                    creative_intent=creative_intent,
                    allow_degraded_fallback=(server_mode == "shadow"),
                    visual_coverage_feedback=visual_coverage_feedback,
                    prior_attempt_quality_feedback=prior_quality_feedback,
                    renderer_capabilities=REFRAME_RENDER_CAPABILITIES,
                )

            edit_plan = await plan_agentic_edit()
            visual_coverage = build_clip_visual_coverage(
                edit_plan,
                visual=visual_understanding,
                clip_frame_manifests=clip_frame_manifests,
                min_observations=agentic_config.crop_coverage_min_observations,
                min_temporal_coverage_ratio=agentic_config.crop_coverage_min_ratio,
                max_observation_gap_ms=agentic_config.crop_coverage_max_gap_ms,
            )
            if visual_coverage.blocking:
                initial_blocker_codes = visual_coverage.blocker_codes
                repair_frame_count = max(
                    agentic_config.vision_clip_frame_count,
                    agentic_config.vision_clip_repair_frame_count,
                )
                await analyze_clip_windows(
                    set(visual_coverage.affected_clip_indexes),
                    max_frames=repair_frame_count,
                )
                visual_understanding = merge_visual_understandings(
                    global_visual_understanding,
                    tuple(
                        clip_visual_understandings[index]
                        for index in sorted(clip_visual_understandings)
                    ),
                )
                visual_path.write_text(
                    json.dumps(
                        visual_understanding.to_dict(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                shorts_plan_artifact = build_shorts_plan_artifact(
                    plan,
                    transcript_segments=transcript.segments,
                    scene_report=scene_report,
                    visual_understanding=visual_understanding,
                )
                shorts_plan_path.write_text(
                    json.dumps(shorts_plan_artifact, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                edit_plan = await plan_agentic_edit(
                    visual_coverage.compact_feedback(),
                )
                visual_coverage = build_clip_visual_coverage(
                    edit_plan,
                    visual=visual_understanding,
                    clip_frame_manifests=clip_frame_manifests,
                    min_observations=agentic_config.crop_coverage_min_observations,
                    min_temporal_coverage_ratio=agentic_config.crop_coverage_min_ratio,
                    max_observation_gap_ms=agentic_config.crop_coverage_max_gap_ms,
                    repair_attempted=True,
                    initial_blocker_codes=initial_blocker_codes,
                )
            if edit_plan.degraded:
                creative_conformance = {
                    "version": creative_intent.version,
                    "status": "degraded",
                    "error_code": "EDIT_PLAN_REPAIR_EXHAUSTED",
                }
            else:
                try:
                    creative_conformance = validate_creative_intent_conformance(
                        edit_plan,
                        creative_intent,
                    ).to_dict()
                except ValueError as exc:
                    raise EditPlanError(
                        "EDIT_PLAN_INTENT_MISMATCH",
                        str(exc),
                    ) from exc
            edit_planner_attempts = [
                attempt.to_dict()
                for attempt in getattr(remote_client, "last_attempts", ())
            ]
            await activity.emit_safely(
                job_id,
                stage="planning_agentic_edit",
                category="planning",
                status="completed",
                message_key="activity.planning.edit_ready",
                progress=STAGES["planning_agentic_edit"].progress,
                provider="9Router",
                tool="Edit planner",
                attempt_number=max(1, len(edit_planner_attempts)),
                clip_count=len(edit_plan.clips),
            )
            edit_plan_path = output_dir / names.edit_plan
            edit_plan_path.write_text(
                json.dumps(edit_plan.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, edit_plan_path, kind="edit_plan")
            visual_coverage_path = output_dir / names.clip_visual_coverage
            visual_coverage_path.write_text(
                json.dumps(visual_coverage.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(
                job_id,
                visual_coverage_path,
                kind="clip_visual_coverage",
            )
            if visual_coverage.blocking and server_mode == "render":
                raise EditPlanError(
                    "EDIT_PLAN_VISUAL_COVERAGE_INSUFFICIENT",
                    "crop evidence remained insufficient after one bounded clip-local repair",
                )

            planned_asset_ids = {
                asset.id
                for clip in edit_plan.clips
                for asset in clip.asset_requests
            }
            pending_asset_ids = (
                planned_asset_ids
                if (
                    effective_asset_policy in {"auto", "required"}
                    or effective_stock_policy in {"auto", "required"}
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
                visual_coverage=visual_coverage,
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
                    await activity.stage(
                        job_id,
                        "resolving_assets",
                        asset_count=len(planned_asset_ids),
                    )
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
                await activity.emit_safely(
                    job_id,
                    stage="resolving_assets",
                    category="asset",
                    status="completed" if planned_asset_ids else "skipped",
                    message_key=(
                        "activity.asset.resolved"
                        if planned_asset_ids
                        else "activity.asset.not_requested"
                    ),
                    progress=STAGES["resolving_assets"].progress,
                    tool="Asset resolver",
                    asset_count=len(asset_result.paths),
                    attempt_number=int(asset_result.provider_call_count),
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
                    visual_coverage=visual_coverage,
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
                await activity.emit_safely(
                    job_id,
                    stage="planning_agentic_edit",
                    category="asset",
                    status="skipped",
                    message_key="activity.asset.shadow_mode",
                    progress=STAGES["resolving_assets"].progress,
                    asset_count=len(planned_asset_ids),
                )

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
                "clip_visual_coverage": names.clip_visual_coverage,
                "clip_vision_frame_count": sum(
                    len(manifest.frames) for manifest in clip_frame_manifests.values()
                ),
                "vision_call_count": (
                    (1 if visual_understanding else 0) + clip_vision_call_count
                ),
                "visual_attempts": visual_attempts,
                "shorts_attempts": shorts_attempts,
                "edit_plan": names.edit_plan,
                "edit_planner": {
                    "model": remote_client.model,
                    "schema_version": edit_plan.version,
                    "planner_version": edit_plan.planner_version,
                    "prompt_version": edit_plan.prompt_version,
                    "attempts": edit_planner_attempts,
                    "prior_attempt_quality_feedback": {
                        "version": prior_quality_feedback.get("version"),
                        "prior_attempt_id": prior_quality_feedback.get("prior_attempt_id"),
                        "prior_attempt_number": prior_quality_feedback.get(
                            "prior_attempt_number"
                        ),
                        "blocker_codes": prior_quality_feedback.get("blocker_codes", []),
                    },
                },
                "preflight": names.preflight,
                "preflight_status": preflight.status,
                "shadow_blocked": bool(preflight.blocking and shadow_allows_blocked),
                "asset_manifest": names.asset_manifest,
                "creative_intent": names.creative_intent,
                "creative_intent_conformance": creative_conformance,
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

        if not agentic_requested:
            await activity.emit_safely(
                job_id,
                stage="remote_planning",
                category="asset",
                status="skipped",
                message_key="activity.asset.not_requested",
                progress=STAGES["resolving_assets"].progress,
                asset_count=0,
            )
        render_stage = await activity.stage(
            job_id,
            "rendering",
            total=len(plan.clips),
            current=0,
        )
        render_floor = float(
            render_stage.get("progress", STAGES["rendering"].progress)
        )
        render_settings = render_settings_from_config(self.config.mvp)
        loop = asyncio.get_running_loop()

        def render_activity(phase: str, current: int, total: int) -> None:
            completed = current if phase == "completed" else current - 1
            progress = max(
                render_floor,
                min(0.87, 0.68 + (0.18 * max(0, completed) / max(1, total))),
            )
            future = asyncio.run_coroutine_threadsafe(
                activity.emit_safely(
                    job_id,
                    stage="rendering",
                    category="render",
                    status="completed" if phase == "completed" else "progress",
                    message_key=(
                        "activity.render.clip_completed"
                        if phase == "completed"
                        else "activity.render.rendering_clip"
                    ),
                    progress=progress,
                    current=current,
                    total=total,
                    tool="FFmpeg",
                ),
                loop,
            )
            try:
                future.result(timeout=10)
            except Exception:
                emit_event(
                    "render_activity_callback_failed",
                    job_id=job_id,
                    stage="rendering",
                    error_code="RENDER_ACTIVITY_CALLBACK_FAILED",
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
                progress_callback=render_activity,
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
                progress_callback=render_activity,
            )
        render_quality_path = output_dir / names.render_quality_profile
        render_quality_path.write_text(
            json.dumps({
                "version": RENDER_QUALITY_PROFILE_VERSION,
                "configured_profile": render_settings.quality_profile,
                "clips": [
                    item.render_quality
                    for item in rendered
                    if item.render_quality is not None
                ],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await store.register_artifact(
            job_id,
            render_quality_path,
            kind="render_quality_profile",
        )
        agentic_manifest["render_quality_profile"] = names.render_quality_profile
        effects_plan = None
        final_outputs = []
        qa_inputs: list[QAInput] = []
        pending_artifacts: list[tuple[Path, str]] = []
        ffmpega = None
        if ffmpega_enabled(self.config.ffmpega):
            await activity.stage(job_id, "planning_effects")
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
            await activity.emit_safely(
                job_id,
                stage="planning_effects",
                category="planning",
                status="completed",
                message_key="activity.planning.effects_ready",
                progress=STAGES["planning_effects"].progress,
                provider="9Router",
                tool="Effects planner",
            )
        else:
            await activity.emit_safely(
                job_id,
                stage="rendering",
                category="planning",
                status="skipped",
                message_key="activity.planning.effects_skipped",
                progress=STAGES["planning_effects"].progress,
            )
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
            pending_artifacts.append((final_video, "video"))
            if item.subtitle_path is not None:
                pending_artifacts.append((item.subtitle_path, "subtitles"))
            if item.subtitle_layout_path is not None:
                pending_artifacts.append((item.subtitle_layout_path, "subtitle_layout"))
            if item.caption_footprint_path is not None:
                pending_artifacts.append((item.caption_footprint_path, "caption_footprint"))
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

        render_qa_report: dict[str, Any] | None = None
        creative_conformance_report: dict[str, Any] | None = None
        if agentic_requested and server_mode == "render":
            qa_manifest: dict[str, Any] = {"enabled": False, "status": "disabled"}
            try:
                if creative_qa_enabled(self.config.agentic_editing):
                    await activity.stage(job_id, "post_render_qa")
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
                    render_qa_report = qa_artifacts.render_qa
                    creative_conformance_report = qa_artifacts.conformance
                    await activity.emit_safely(
                        job_id,
                        stage="post_render_qa",
                        category="qa",
                        status="completed",
                        message_key="activity.qa.completed",
                        progress=STAGES["post_render_qa"].progress,
                        tool="Deterministic QA",
                        clip_count=len(qa_inputs),
                    )
                else:
                    await activity.emit_safely(
                        job_id,
                        stage="planning_effects" if ffmpega_enabled(self.config.ffmpega) else "rendering",
                        category="qa",
                        status="skipped",
                        message_key="activity.qa.skipped",
                        progress=STAGES["post_render_qa"].progress,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_code = str(getattr(exc, "code", "CREATIVE_QA_UNAVAILABLE"))[:80]
                qa_manifest = {
                    "enabled": True,
                    "status": "unavailable",
                    "error_code": error_code,
                }
                await activity.emit_safely(
                    job_id,
                    stage="post_render_qa",
                    category="qa",
                    status="warning",
                    message_key="activity.qa.unavailable",
                    progress=STAGES["post_render_qa"].progress,
                    error_code=error_code,
                    retryable=retryable_error(error_code),
                )
            agentic_manifest["qa"] = qa_manifest
        else:
            await activity.emit_safely(
                job_id,
                stage="planning_effects" if ffmpega_enabled(self.config.ffmpega) else "rendering",
                category="qa",
                status="skipped",
                message_key="activity.qa.skipped",
                progress=STAGES["post_render_qa"].progress,
            )

        if agentic_requested and server_mode == "render":
            promotion_mode = render_promotion_mode(self.config.agentic_editing)
            if promotion_mode == "off":
                frame_quality_report = {
                    "version": FRAME_QUALITY_VERSION,
                    "status": "off",
                    "findings": [],
                    "summary": {"clips_analyzed": 0, "blockers": 0, "warnings": 0},
                }
            else:
                frame_quality_report = await asyncio.to_thread(
                    build_frame_quality_report,
                    qa_inputs,
                    source=source,
                    render_execution=agentic_result.execution,
                    expected_width=render_settings.width,
                    expected_height=render_settings.height,
                    strict=True,
                )
            frame_quality_path = output_dir / names.frame_quality_qa
            frame_quality_path.write_text(
                json.dumps(frame_quality_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(
                job_id,
                frame_quality_path,
                kind="frame_quality_qa",
            )
            caption_footprints = []
            for item in rendered:
                if item.caption_footprint_path is None:
                    continue
                try:
                    caption_footprints.append(json.loads(
                        item.caption_footprint_path.read_text(encoding="utf-8")
                    ))
                except (OSError, json.JSONDecodeError):
                    caption_footprints.append({
                        "status": "blocked",
                        "summary": {"blocker_codes": ["CAPTION_FOOTPRINT_UNAVAILABLE"]},
                    })
            promotion_report = build_render_promotion_report(
                mode=promotion_mode,
                frame_quality=frame_quality_report,
                render_qa=render_qa_report,
                creative_conformance=creative_conformance_report,
                caption_footprints=caption_footprints,
            )
            if promotion_report["decision"] == "block":
                removed = 0
                for path, kind in pending_artifacts:
                    if kind == "video" and path.is_file():
                        path.unlink()
                        removed += 1
                promotion_report["candidate_cleanup"] = {
                    "video_candidates_removed": removed,
                }
            else:
                promotion_report["candidate_cleanup"] = {
                    "video_candidates_removed": 0,
                }
            promotion_path = output_dir / names.render_promotion
            promotion_path.write_text(
                json.dumps(promotion_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(
                job_id,
                promotion_path,
                kind="render_promotion",
            )
            agentic_manifest["frame_quality_qa"] = names.frame_quality_qa
            agentic_manifest["render_promotion"] = {
                "artifact": names.render_promotion,
                "mode": promotion_mode,
                "decision": promotion_report["decision"],
                "blocker_codes": promotion_report["blocker_codes"],
            }
            await activity.emit_safely(
                job_id,
                stage="post_render_qa",
                category="qa",
                status=(
                    "failed" if promotion_report["decision"] == "block"
                    else "warning" if promotion_report["decision"] == "observe"
                    else "completed"
                ),
                message_key="activity.qa.completed",
                progress=STAGES["post_render_qa"].progress,
                tool="Render promotion gate",
                error_code=(
                    promotion_report["blocker_codes"][0]
                    if promotion_report["blocker_codes"]
                    else None
                ),
                retryable=False,
            )
            enforce_render_promotion(promotion_report)

        for path, kind in pending_artifacts:
            await store.register_artifact(job_id, path, kind=kind)

        await activity.stage(job_id, "packaging", clip_count=len(rendered))
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "job_id": job_id,
            "run": {
                "prompt_version_id": state.get("prompt_version_id"),
                "attempt_number": state.get("attempt_number"),
                "settings_version": request.get("settings_version"),
                "is_favorite": bool(state.get("is_favorite")),
                "prior_attempt_quality_feedback": {
                    "version": prior_quality_feedback.get("version"),
                    "prior_attempt_id": prior_quality_feedback.get("prior_attempt_id"),
                    "prior_attempt_number": prior_quality_feedback.get(
                        "prior_attempt_number"
                    ),
                },
            },
            "source": {
                "input_video_id": state["input"].get("input_video_id"),
                "filename": state["input"]["original_filename"],
                "sha256": state["input"].get("sha256"),
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
