from __future__ import annotations

from pathlib import Path
from typing import Any
import asyncio
import json
import re

from open_storyline.config import Settings
from open_storyline.mvp.activity import ActivityService, STAGES, retryable_error
from open_storyline.mvp.assets import (
    AssetResolutionError,
    generated_asset_server_cap,
    generated_asset_size,
    generated_assets_enabled,
    resolve_assets,
    write_asset_manifest,
)
from open_storyline.mvp.checkpoints import (
    CheckpointError,
    CheckpointHit,
    CheckpointStore,
    checkpoint_fingerprint,
)
from open_storyline.mvp.catalog import (
    CreativeCatalog,
    build_catalog_usage,
    catalog_candidate_snapshot,
    creative_catalog_planning_enabled,
)
from open_storyline.mvp.edit_plan import (
    AgenticEditPlanner,
    AgenticArtifactNames,
    EditPlanError,
    merge_repaired_edit_plan_response,
    resolve_agentic_server_mode,
)
from open_storyline.mvp.defects import DEFECT_REGISTRY_VERSION, defect_definition
from open_storyline.mvp.ffmpega import (
    AGENTIC_FINISHING_SKILLS,
    DETERMINISTIC_SKILLS,
    EffectsPlanner,
    FFMPEGAClient,
    FFMPEGAError,
    ffmpega_enabled,
)
from open_storyline.mvp.frame_sampling import FrameManifest, SampledFrame, sample_frames
from open_storyline.mvp.frame_quality import (
    FRAME_QUALITY_VERSION,
    build_frame_quality_report,
)
from open_storyline.mvp.fallbacks import (
    FallbackEntry,
    FallbackDirective,
    baseline_fallbacks_enabled,
    compile_baseline_plan,
    merge_fallback_entries,
)
from open_storyline.mvp.compositor import REFRAME_RENDER_CAPABILITIES
from open_storyline.mvp.creative_intent import (
    build_creative_intent,
    creative_intent_conformance_evidence,
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
from open_storyline.mvp.ninerouter import NineRouterClient, NineRouterError
from open_storyline.mvp.observability import compact_repair_observability, emit_event
from open_storyline.mvp.outcomes import build_completed_outcome_report
from open_storyline.mvp.render import (
    AgenticShortRenderer,
    CPUShortRenderer,
    RENDER_QUALITY_PROFILE_VERSION,
    RenderError,
    extract_frame_data_urls,
    probe_media,
    render_settings_from_config,
)
from open_storyline.mvp.preflight import build_preflight
from open_storyline.mvp.prompts import REPAIR_SYSTEM_PROMPT, REPAIR_SYSTEM_PROMPT_VERSION
from open_storyline.mvp.repair import (
    REPAIR_BATCH_REQUEST_VERSION,
    REPAIR_REPORT_VERSION,
    RepairBudget,
    RepairContractError,
    RepairMode,
    RepairStage,
    TranscriptExcerpt,
    bounded_repair_findings,
    build_repair_batch,
    build_repair_report,
    compute_repair_resolution,
    evaluate_repair_quality_floor,
    make_repair_finding,
    predict_plan_findings,
    repair_findings_from_preflight,
    repair_findings_from_visual_coverage,
    resolve_repair_mode,
)
from open_storyline.mvp.promotion import (
    build_render_promotion_report,
    completion_policy,
    delivery_policy,
    enforce_render_promotion,
    limited_output_promotion_enabled,
    render_promotion_mode,
)
from open_storyline.mvp.scene_boundaries import (
    SceneBoundaryReport,
    SceneInterval,
    detect_scene_boundaries,
)
from open_storyline.mvp.shorts import (
    ShortCandidate,
    ShortsPlan,
    ShortsPlanner,
    build_shorts_plan_artifact,
)
from open_storyline.mvp.stock import PexelsClient, pexels_enabled, pexels_server_cap
from open_storyline.mvp.visual_coverage import build_clip_visual_coverage
from open_storyline.mvp.visual_understanding import (
    VisualUnderstanding,
    VisualUnderstandingError,
    VisualUnderstandingPlanner,
    merge_visual_understandings,
    scope_visual_understanding,
    validate_visual_understanding,
)
from open_storyline.mvp.structured_outputs import (
    EDIT_PLAN_REPAIR_SCHEMA,
    VISUAL_UNDERSTANDING_SCHEMA,
    structured_output,
)
from open_storyline.utils.remote_stt import (
    MISTRAL_STT_MODEL,
    MistralSTTClient,
    RemoteSTTError,
    STTAttempt,
    STTResult,
    extract_audio_for_stt,
)
from open_storyline.utils.remote_image import RemoteImageCascade


TRANSCRIPT_CHECKPOINT_VERSION = "transcript_checkpoint.v1"
SCENE_CHECKPOINT_VERSION = "scene_checkpoint.v1"
GLOBAL_ANALYSIS_CHECKPOINT_VERSION = "global_analysis_checkpoint.v1"
CLIP_ANALYSIS_CHECKPOINT_VERSION = "clip_analysis_checkpoint.v1"
VISUAL_REPAIR_CHECKPOINT_VERSION = "visual_repair_checkpoint.v1"
PLAN_REPAIR_CHECKPOINT_VERSION = "plan_repair_checkpoint.v1"


def _stt_result(payload: dict[str, Any]) -> STTResult:
    attempts = [
        STTAttempt(
            model=str(item.get("model") or MISTRAL_STT_MODEL),
            success=bool(item.get("success")),
            status_code=(
                int(item["status_code"])
                if item.get("status_code") is not None
                else None
            ),
            reason=str(item.get("reason") or "")[:600],
            key_ordinal=str(item.get("key_ordinal") or "")[:32],
            category=str(item.get("category") or "")[:64],
            latency_ms=max(0, int(item.get("latency_ms") or 0)),
            retry_after_seconds=(
                int(item["retry_after_seconds"])
                if item.get("retry_after_seconds") is not None
                else None
            ),
            request_sent=bool(item.get("request_sent", True)),
        )
        for item in payload.get("attempts") or []
        if isinstance(item, dict)
    ]
    text = str(payload.get("text") or "").strip()
    segments = [dict(item) for item in payload.get("segments") or [] if isinstance(item, dict)]
    if not text or not segments:
        raise ValueError("cached transcript is incomplete")
    return STTResult(
        model=str(payload.get("model") or MISTRAL_STT_MODEL),
        text=text,
        segments=segments,
        attempts=attempts,
    )


def _scene_report(payload: dict[str, Any]) -> SceneBoundaryReport:
    scenes = tuple(
        SceneInterval(
            id=str(item["id"]),
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
        )
        for item in payload.get("scenes") or []
        if isinstance(item, dict)
    )
    if not scenes:
        raise ValueError("cached scene report has no scenes")
    summary = payload.get("summary") or {}
    return SceneBoundaryReport(
        source_duration_ms=int(payload["source_duration_ms"]),
        threshold=float(payload["threshold"]),
        min_scene_duration_ms=int(payload["min_scene_duration_ms"]),
        raw_boundary_count=int(summary.get("raw_boundaries") or 0),
        boundaries_ms=tuple(int(value) for value in payload.get("boundaries_ms") or []),
        scenes=scenes,
        warnings=tuple(
            dict(item) for item in payload.get("warnings") or [] if isinstance(item, dict)
        ),
        version=str(payload.get("version") or "scene_boundaries.v1"),
    )


def _frame_manifest(payload: dict[str, Any]) -> FrameManifest:
    frames = tuple(
        SampledFrame(
            id=str(item["id"]),
            timestamp_ms=int(item["timestamp_ms"]),
            scene_id=str(item["scene_id"]),
            width=int(item["width"]),
            height=int(item["height"]),
            extraction_reason=str(item.get("extraction_reason") or "checkpoint"),
            encoded_bytes=int(item.get("encoded_bytes") or 0),
            data_url="",
        )
        for item in payload.get("frames") or []
        if isinstance(item, dict)
    )
    return FrameManifest(
        source_duration_ms=int(payload["source_duration_ms"]),
        source_width=int(payload["source_width"]),
        source_height=int(payload["source_height"]),
        frames=frames,
        warnings=tuple(
            dict(item) for item in payload.get("warnings") or [] if isinstance(item, dict)
        ),
        version=str(payload.get("version") or "frame_manifest.v1"),
    )


def _shorts_plan(payload: dict[str, Any]) -> ShortsPlan:
    clips = [
        ShortCandidate(
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            title=str(item.get("title") or "")[:120],
            hook=str(item.get("hook") or "")[:240],
            reason=str(item.get("reason") or "")[:400],
            score=float(item.get("score") or 0),
        )
        for item in payload.get("clips") or []
        if isinstance(item, dict)
    ]
    if not clips:
        raise ValueError("cached shorts plan has no clips")
    return ShortsPlan(
        clips=clips,
        rejected=[
            dict(item) for item in payload.get("rejected") or [] if isinstance(item, dict)
        ],
    )


def _checkpoint_job_id(value: Any) -> str | None:
    candidate = str(value or "")
    return candidate if re.fullmatch(r"[a-f0-9]{32}", candidate) else None


class MVPJobProcessor:
    """Remote-inference pipeline; local work is restricted to deterministic FFmpeg."""

    def __init__(
        self,
        config: Settings,
        *,
        creative_catalog: CreativeCatalog | None = None,
    ) -> None:
        self.config = config
        self.stt = MistralSTTClient.from_config(config.remote_asr)
        self.creative_catalog = creative_catalog
        self.caption_font_family = (
            creative_catalog.require("font.caption.core").font_family
            if creative_catalog is not None
            else "DejaVu Sans"
        )

    async def __call__(self, job_id: str, store: JobStore) -> dict[str, Any]:
        state = await store.load(job_id)
        activity = ActivityService(store)
        checkpoints = CheckpointStore(store)
        checkpoint_summary: dict[str, Any] = {
            "enabled": checkpoints.enabled,
            "reused_stages": [],
            "recomputed_stages": [],
            "errors": [],
        }
        fallback_enabled = baseline_fallbacks_enabled(self.config.agentic_editing)
        fallback_entries: tuple[FallbackEntry, ...] = ()

        def track_checkpoint(stage: str, *, reused: bool) -> None:
            key = "reused_stages" if reused else "recomputed_stages"
            if stage not in checkpoint_summary[key]:
                checkpoint_summary[key].append(stage)

        async def load_session_checkpoint(**kwargs: Any) -> CheckpointHit | None:
            try:
                return await checkpoints.load_session(**kwargs)
            except CheckpointError as exc:
                checkpoint_summary["errors"].append(exc.code)
                emit_event(
                    "checkpoint_load_failed",
                    job_id=job_id,
                    stage=str(kwargs.get("stage") or "checkpoint"),
                    error_code=exc.code,
                )
                return None

        async def save_session_checkpoint(**kwargs: Any) -> None:
            try:
                await checkpoints.save_session(**kwargs)
            except (CheckpointError, OSError) as exc:
                code = str(getattr(exc, "code", "CHECKPOINT_WRITE_FAILED"))[:80]
                checkpoint_summary["errors"].append(code)
                emit_event(
                    "checkpoint_write_failed",
                    job_id=job_id,
                    stage=str(kwargs.get("stage") or "checkpoint"),
                    error_code=code,
                )

        async def load_job_checkpoint(**kwargs: Any) -> CheckpointHit | None:
            try:
                return await checkpoints.load_job(**kwargs)
            except CheckpointError as exc:
                checkpoint_summary["errors"].append(exc.code)
                emit_event(
                    "checkpoint_load_failed",
                    job_id=job_id,
                    stage=str(kwargs.get("stage") or "checkpoint"),
                    error_code=exc.code,
                )
                return None

        async def save_job_checkpoint(**kwargs: Any) -> None:
            try:
                await checkpoints.save_job(**kwargs)
            except (CheckpointError, OSError) as exc:
                code = str(getattr(exc, "code", "CHECKPOINT_WRITE_FAILED"))[:80]
                checkpoint_summary["errors"].append(code)
                emit_event(
                    "checkpoint_write_failed",
                    job_id=job_id,
                    stage=str(kwargs.get("stage") or "checkpoint"),
                    error_code=code,
                )

        request = state.get("request") or {}
        creative_catalog = getattr(self, "creative_catalog", None)
        prior_quality_feedback = request.get("prior_attempt_quality_feedback")
        if not isinstance(prior_quality_feedback, dict):
            prior_quality_feedback = {}
        agentic_requested = request.get("edit_mode") == "agentic"
        catalog_snapshot: dict[str, Any] | None = None
        if (
            agentic_requested
            and creative_catalog is not None
            and creative_catalog_planning_enabled()
        ):
            catalog_snapshot = catalog_candidate_snapshot(
                creative_catalog,
                editing_prompt=state["prompt"],
                aspect_ratio="9:16",
            )
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
                if not fallback_enabled:
                    raise EditPlanError(
                        "CREATIVE_INTENT_CAPABILITY_UNAVAILABLE",
                        "required generated-image capability is unavailable",
                    )
                fallback_entries = merge_fallback_entries(
                    fallback_entries,
                    (FallbackEntry(
                        code="EXTERNAL_ASSET_OMITTED",
                        clip_index=1,
                        segment_id="capability",
                        requested="generated_image",
                        executed="source_media",
                        reason="The generated-image capability is unavailable.",
                    ),),
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
                if not fallback_enabled:
                    raise EditPlanError(
                        "CREATIVE_INTENT_CAPABILITY_UNAVAILABLE",
                        "required Pexels capability is unavailable",
                    )
                fallback_entries = merge_fallback_entries(
                    fallback_entries,
                    (FallbackEntry(
                        code="EXTERNAL_ASSET_OMITTED",
                        clip_index=1,
                        segment_id="capability",
                        requested="stock_asset",
                        executed="source_media",
                        reason="The stock-media capability is unavailable.",
                    ),),
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
                if not fallback_enabled:
                    raise EditPlanError(
                        "CREATIVE_INTENT_CAPABILITY_UNAVAILABLE",
                        str(exc),
                    ) from exc
                fallback_entries = merge_fallback_entries(
                    fallback_entries,
                    (FallbackEntry(
                        code="CREATIVE_INTENT_UNMET",
                        clip_index=1,
                        segment_id="capability",
                        requested="unavailable_optional_capability",
                        executed="installed_baseline_capabilities",
                        reason=str(exc)[:240],
                    ),),
                )
        source = await store.source_path(job_id)
        work_dir = store.work_dir(job_id)
        output_dir = store.output_dir(job_id)

        media = await asyncio.to_thread(probe_media, source)
        if not media.has_audio:
            raise RemoteSTTError("MEDIA_HAS_NO_AUDIO", "source video must contain an audio stream")
        editing_session_id = str(state.get("editing_session_id") or "")
        input_video_id = str((state.get("input") or {}).get("input_video_id") or "")
        source_hash = str((state.get("input") or {}).get("sha256") or "")
        transcript_fingerprint = checkpoint_fingerprint({
            "contract_version": TRANSCRIPT_CHECKPOINT_VERSION,
            "source_sha256": source_hash,
            "model": MISTRAL_STT_MODEL,
            "language": str(self.config.remote_asr.language or ""),
        })
        transcript_hit = await load_session_checkpoint(
            editing_session_id=editing_session_id,
            input_video_id=input_video_id,
            stage="transcript",
            fingerprint=transcript_fingerprint,
        )
        transcript = None
        transcript_reused = False
        if transcript_hit is not None:
            try:
                transcript = _stt_result(transcript_hit.payload)
            except (KeyError, TypeError, ValueError):
                transcript = None
        if transcript is not None:
            transcript_reused = True
            track_checkpoint("transcript", reused=True)
            await activity.stage(job_id, "extracting_audio")
            await activity.emit_safely(
                job_id,
                stage="extracting_audio",
                category="analysis",
                status="skipped",
                message_key="activity.analysis.audio_ready",
                progress=STAGES["extracting_audio"].progress,
                tool="Session checkpoint",
            )
            await activity.stage(job_id, "remote_transcription")
            await activity.emit_safely(
                job_id,
                stage="remote_transcription",
                category="provider",
                status="completed",
                message_key="activity.provider.transcription_ready",
                progress=STAGES["remote_transcription"].progress,
                provider="Session checkpoint",
                tool="Transcript cache",
                attempt_number=1,
            )
        else:
            track_checkpoint("transcript", reused=False)
            await activity.stage(job_id, "extracting_audio")
            audio = await asyncio.to_thread(
                extract_audio_for_stt,
                source,
                work_dir / "audio.mp3",
            )
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
            transcript = await self.stt.transcribe(
                audio,
                language=self.config.remote_asr.language,
            )
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
            await save_session_checkpoint(
                editing_session_id=editing_session_id,
                input_video_id=input_video_id,
                stage="transcript",
                contract_version=TRANSCRIPT_CHECKPOINT_VERSION,
                fingerprint=transcript_fingerprint,
                payload={
                    "model": transcript.model,
                    "text": transcript.text,
                    "segments": transcript.segments,
                    "attempts": [attempt.to_dict() for attempt in transcript.attempts],
                },
                metadata={"model": transcript.model},
            )
        transcript_path = output_dir / "transcript.json"
        transcript_path.write_text(json.dumps({
            "model": transcript.model,
            "text": transcript.text,
            "segments": transcript.segments,
            "attempts": [attempt.to_dict() for attempt in transcript.attempts],
            "checkpoint": {
                "reused": transcript_reused,
                "fingerprint": transcript_fingerprint,
            },
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
        repair_mode = resolve_repair_mode() if agentic_requested else RepairMode.OFF
        visual_repair_attempts_used = 0
        repair_checkpoint_reports: dict[str, dict[str, Any]] = {}
        repair_stage_records: dict[str, dict[str, Any]] = {}
        predictive_repair_findings: tuple[Any, ...] = ()

        async def persist_partial_repair_report() -> None:
            if not agentic_requested:
                return
            report = build_repair_report(
                mode=repair_mode,
                stage_records=repair_stage_records.values(),
                predictive_findings=predictive_repair_findings,
                fallback_entries=(),
                reused_stages=checkpoint_summary["reused_stages"],
                recomputed_stages=checkpoint_summary["recomputed_stages"],
            )
            path = output_dir / names.repair_report
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, path, kind="repair_report")

        async def registry_visual_repair_handler(
            *,
            invalid_response: dict[str, Any],
            error: Any,
            user_payload: dict[str, Any],
            frame_manifest: FrameManifest,
            scene_report: SceneBoundaryReport,
        ) -> dict[str, Any]:
            nonlocal visual_repair_attempts_used
            clip_match = re.match(
                r"clip-(\d{2})-",
                frame_manifest.frames[0].id if frame_manifest.frames else "",
            )
            clip_index = int(clip_match.group(1)) if clip_match else 1
            finding = make_repair_finding(
                str(error.code),
                clip_index=clip_index,
                objective=True,
                values={
                    "observed": str(error.code),
                    "count": len(invalid_response.get("regions") or ()),
                },
                source="visual_validator",
            )
            transcript_text = str(user_payload.get("transcript_context") or "")[:12_000]
            excerpts = (
                TranscriptExcerpt(
                    clip_index=clip_index,
                    start_ms=0,
                    end_ms=frame_manifest.source_duration_ms,
                    text=transcript_text,
                ),
            ) if transcript_text else ()
            request, dispositions = build_repair_batch(
                stage=RepairStage.VISUAL_UNDERSTANDING,
                mode=repair_mode,
                findings=(finding,),
                budget=RepairBudget(
                    visual_attempts_used=visual_repair_attempts_used,
                ),
                candidate_clips={clip_index: invalid_response},
                available_capabilities=("visual_understanding",),
                catalog_context={},
                immutable_constraints={
                    "source_duration_ms": frame_manifest.source_duration_ms,
                    "frame_ids": [frame.id for frame in frame_manifest.frames],
                    "scene_ids": [scene.id for scene in scene_report.scenes],
                },
                editing_prompt=str(user_payload.get("editing_context") or ""),
                transcript_excerpts=excerpts,
            )
            report = compact_repair_observability(request.to_report_dict())
            fingerprint = checkpoint_fingerprint({
                "contract_version": VISUAL_REPAIR_CHECKPOINT_VERSION,
                "source_sha256": source_hash,
                "request_fingerprint": report["request_fingerprint"],
                "registry_version": DEFECT_REGISTRY_VERSION,
                "schema_fingerprint": structured_output(
                    VISUAL_UNDERSTANDING_SCHEMA
                ).fingerprint,
                "repair_prompt_version": REPAIR_SYSTEM_PROMPT_VERSION,
                "mode": repair_mode.value,
            })
            hit = await load_job_checkpoint(
                job_id=job_id,
                stage="visual_repair",
                fingerprint=fingerprint,
            )
            if hit is not None:
                repair_checkpoint_reports["visual_repair"] = compact_repair_observability(
                    dict(hit.payload.get("report") or {})
                )
                if hit.payload.get("status") == "repaired":
                    cached_visual = hit.payload.get("visual_understanding")
                    if not isinstance(cached_visual, dict):
                        hit = None
                    if hit is not None:
                        try:
                            validated = validate_visual_understanding(
                                cached_visual,
                                frame_manifest=frame_manifest,
                                scene_report=scene_report,
                                model=remote_client.model,
                            )
                        except (TypeError, ValueError, VisualUnderstandingError):
                            hit = None
                        else:
                            visual_repair_attempts_used = 1
                            track_checkpoint("visual_repair", reused=True)
                            repair_stage_records["visual_repair"] = {
                                "stage": RepairStage.VISUAL_UNDERSTANDING.value,
                                "status": "repaired",
                                "request": repair_checkpoint_reports["visual_repair"],
                                "dispositions": [
                                    item.to_dict() for item in dispositions
                                ],
                                "resolution": compute_repair_resolution(
                                    (finding.code,), ()
                                ).to_dict(),
                                "quality_floor": {
                                    "accepted": True,
                                    "violation_codes": [],
                                },
                                "checkpoint_reused": True,
                            }
                            remote_client.last_attempts = ()
                            return validated.to_dict()
                else:
                    visual_repair_attempts_used = 1
                    track_checkpoint("visual_repair", reused=True)
                    cached_status = str(hit.payload.get("status") or "failed")
                    repair_stage_records["visual_repair"] = {
                        "stage": RepairStage.VISUAL_UNDERSTANDING.value,
                        "status": (
                            cached_status
                            if cached_status in {"report_only", "failed", "rejected"}
                            else "failed"
                        ),
                        "request": repair_checkpoint_reports["visual_repair"],
                        "dispositions": [item.to_dict() for item in dispositions],
                        "resolution": compute_repair_resolution(
                            (finding.code,), (finding.code,)
                        ).to_dict(),
                        "quality_floor": {"accepted": False, "violation_codes": []},
                        "checkpoint_reused": True,
                    }
                    await persist_partial_repair_report()
                    raise error
            track_checkpoint("visual_repair", reused=False)
            visual_repair_attempts_used += 1
            repair_checkpoint_reports["visual_repair"] = report
            if repair_mode is RepairMode.REPORT:
                await save_job_checkpoint(
                    job_id=job_id,
                    stage="visual_repair",
                    contract_version=VISUAL_REPAIR_CHECKPOINT_VERSION,
                    fingerprint=fingerprint,
                    payload={"status": "report_only", "report": report},
                    metadata={"mode": repair_mode.value, "code": str(error.code)},
                )
                repair_stage_records["visual_repair"] = {
                    "stage": RepairStage.VISUAL_UNDERSTANDING.value,
                    "status": "report_only",
                    "request": report,
                    "dispositions": [item.to_dict() for item in dispositions],
                    "resolution": compute_repair_resolution(
                        (finding.code,), (finding.code,)
                    ).to_dict(),
                    "quality_floor": {"accepted": False, "violation_codes": []},
                    "checkpoint_reused": False,
                }
                await persist_partial_repair_report()
                raise error
            try:
                repaired = await remote_client.complete_structured(
                    schema_name=VISUAL_UNDERSTANDING_SCHEMA,
                    system_prompt=REPAIR_SYSTEM_PROMPT,
                    user_prompt=json.dumps(request.to_provider_dict(), ensure_ascii=False),
                    image_data_urls=frame_manifest.image_data_urls,
                )
                validated = validate_visual_understanding(
                    repaired,
                    frame_manifest=frame_manifest,
                    scene_report=scene_report,
                    model=remote_client.model,
                )
            except (
                NineRouterError,
                RepairContractError,
                VisualUnderstandingError,
                ValueError,
            ) as exc:
                failed_attempts = [
                    {**attempt.to_dict(), "category": "visual_repair"}
                    for attempt in (
                        tuple(getattr(exc, "attempts", ()))
                        or tuple(getattr(remote_client, "last_attempts", ()))
                    )
                ]
                visual_attempts.extend(failed_attempts)
                await save_job_checkpoint(
                    job_id=job_id,
                    stage="visual_repair",
                    contract_version=VISUAL_REPAIR_CHECKPOINT_VERSION,
                    fingerprint=fingerprint,
                    payload={
                        "status": "failed",
                        "report": report,
                        "error_code": str(getattr(exc, "code", "VISUAL_RESPONSE_INVALID")),
                    },
                    metadata={"mode": repair_mode.value, "code": str(error.code)},
                )
                repair_stage_records["visual_repair"] = {
                    "stage": RepairStage.VISUAL_UNDERSTANDING.value,
                    "status": "failed",
                    "request": report,
                    "dispositions": [item.to_dict() for item in dispositions],
                    "resolution": compute_repair_resolution(
                        (finding.code,), (finding.code,)
                    ).to_dict(),
                    "quality_floor": {"accepted": False, "violation_codes": []},
                    "attempts": failed_attempts,
                    "checkpoint_reused": False,
                }
                await persist_partial_repair_report()
                raise
            await save_job_checkpoint(
                job_id=job_id,
                stage="visual_repair",
                contract_version=VISUAL_REPAIR_CHECKPOINT_VERSION,
                fingerprint=fingerprint,
                payload={
                    "status": "repaired",
                    "report": report,
                    "visual_understanding": validated.to_dict(),
                },
                metadata={"mode": repair_mode.value, "code": str(error.code)},
            )
            repair_stage_records["visual_repair"] = {
                "stage": RepairStage.VISUAL_UNDERSTANDING.value,
                "status": "repaired",
                "request": report,
                "dispositions": [item.to_dict() for item in dispositions],
                "resolution": compute_repair_resolution(
                    (finding.code,), ()
                ).to_dict(),
                "quality_floor": {"accepted": True, "violation_codes": []},
                "checkpoint_reused": False,
            }
            return validated.to_dict()

        visual_planner = VisualUnderstandingPlanner(remote_client)
        visual_planner.registry_repair_handler = (
            registry_visual_repair_handler
            if repair_mode in {RepairMode.REPORT, RepairMode.ENFORCE}
            else None
        )
        visual_planner.legacy_repair_enabled = repair_mode is RepairMode.OFF

        def classified_visual_attempts() -> list[dict[str, Any]]:
            attempts = tuple(getattr(remote_client, "last_attempts", ()))
            categories = tuple(
                getattr(visual_planner, "last_attempt_categories", ())
            )
            return [
                {
                    **attempt.to_dict(),
                    "category": (
                        categories[index]
                        if index < len(categories)
                        else "initial_generation"
                    ),
                }
                for index, attempt in enumerate(attempts)
            ]

        plan: ShortsPlan | None = None
        frames: tuple[str, ...] | list[str] = ()
        global_analysis_fingerprint = ""
        if agentic_requested:
            agentic_config = self.config.agentic_editing
            scene_fingerprint = checkpoint_fingerprint({
                "contract_version": SCENE_CHECKPOINT_VERSION,
                "source_sha256": source_hash,
                "duration_ms": media.duration_ms,
                "threshold": agentic_config.scene_threshold,
                "min_scene_duration_ms": agentic_config.min_scene_duration_ms,
                "max_scenes": agentic_config.max_scenes,
            })
            scene_hit = await load_session_checkpoint(
                editing_session_id=editing_session_id,
                input_video_id=input_video_id,
                stage="scene_boundaries",
                fingerprint=scene_fingerprint,
            )
            if scene_hit is not None:
                try:
                    scene_report = _scene_report(scene_hit.payload)
                except (KeyError, TypeError, ValueError):
                    scene_report = None
            await activity.stage(job_id, "detecting_scenes")
            if scene_report is None:
                track_checkpoint("scene_boundaries", reused=False)
                scene_report = await asyncio.to_thread(
                    detect_scene_boundaries,
                    source,
                    source_duration_ms=media.duration_ms,
                    threshold=agentic_config.scene_threshold,
                    min_scene_duration_ms=agentic_config.min_scene_duration_ms,
                    max_scenes=agentic_config.max_scenes,
                )
                await save_session_checkpoint(
                    editing_session_id=editing_session_id,
                    input_video_id=input_video_id,
                    stage="scene_boundaries",
                    contract_version=SCENE_CHECKPOINT_VERSION,
                    fingerprint=scene_fingerprint,
                    payload=scene_report.to_dict(),
                    metadata={"method": "ffmpeg_scene_detection"},
                )
                scene_tool = "FFmpeg"
            else:
                track_checkpoint("scene_boundaries", reused=True)
                scene_tool = "Session checkpoint"
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
                tool=scene_tool,
            )

            global_analysis_fingerprint = checkpoint_fingerprint({
                "contract_version": GLOBAL_ANALYSIS_CHECKPOINT_VERSION,
                "source_sha256": source_hash,
                "prompt_sha256": checkpoint_fingerprint({"prompt": state["prompt"]}),
                "prompt_version_id": state.get("prompt_version_id"),
                "transcript_fingerprint": transcript_fingerprint,
                "scene_fingerprint": scene_fingerprint,
                "model": remote_client.model,
                "max_clips": int(request.get("max_clips") or 8),
                "vision": {
                    "frame_count": agentic_config.vision_frame_count,
                    "max_width": agentic_config.vision_frame_max_width,
                    "max_height": agentic_config.vision_frame_max_height,
                    "max_bytes": agentic_config.vision_frame_max_bytes,
                },
            })
            global_hit = await load_session_checkpoint(
                editing_session_id=editing_session_id,
                input_video_id=input_video_id,
                stage="agentic_global_analysis",
                fingerprint=global_analysis_fingerprint,
            )
            if global_hit is not None:
                try:
                    frame_manifest = _frame_manifest(global_hit.payload["frame_manifest"])
                    global_visual_understanding = VisualUnderstanding.model_validate(
                        global_hit.payload["visual_understanding"]
                    )
                    plan = _shorts_plan(global_hit.payload["shorts_plan"])
                    visual_attempts = [
                        dict(item)
                        for item in global_hit.payload.get("visual_attempts") or []
                        if isinstance(item, dict)
                    ]
                    shorts_attempts = [
                        dict(item)
                        for item in global_hit.payload.get("shorts_attempts") or []
                        if isinstance(item, dict)
                    ]
                except (KeyError, TypeError, ValueError):
                    frame_manifest = None
                    global_visual_understanding = None
                    plan = None
            if plan is not None and global_visual_understanding is not None:
                track_checkpoint("agentic_global_analysis", reused=True)
                visual_understanding = global_visual_understanding
                await activity.stage(job_id, "sampling_agentic_frames")
                await activity.emit_safely(
                    job_id,
                    stage="sampling_agentic_frames",
                    category="analysis",
                    status="completed",
                    message_key="activity.analysis.frames_ready",
                    progress=STAGES["sampling_agentic_frames"].progress,
                    tool="Session checkpoint",
                    sampled_frames=len(frame_manifest.frames) if frame_manifest else 0,
                )
                await activity.stage(job_id, "remote_visual_understanding")
                await activity.emit_safely(
                    job_id,
                    stage="remote_visual_understanding",
                    category="provider",
                    status="completed",
                    message_key="activity.provider.video_understood",
                    progress=STAGES["remote_visual_understanding"].progress,
                    provider="Session checkpoint",
                    tool="Global analysis cache",
                    attempt_number=1,
                )
            else:
                track_checkpoint("agentic_global_analysis", reused=False)
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
                global_visual_understanding = await visual_planner.plan(
                    frame_manifest=frame_manifest,
                    scene_report=scene_report,
                    editing_prompt=state["prompt"],
                    transcript_text=transcript.text,
                )
                visual_attempts = classified_visual_attempts()
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
        if plan is None:
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
            planning_provider = "9Router"
            planning_tool = "Clip planner"
            if agentic_requested:
                await save_session_checkpoint(
                    editing_session_id=editing_session_id,
                    input_video_id=input_video_id,
                    stage="agentic_global_analysis",
                    contract_version=GLOBAL_ANALYSIS_CHECKPOINT_VERSION,
                    fingerprint=global_analysis_fingerprint,
                    payload={
                        "frame_manifest": frame_manifest.to_dict(),
                        "visual_understanding": global_visual_understanding.to_dict(),
                        "shorts_plan": plan.to_dict(),
                        "visual_attempts": visual_attempts,
                        "shorts_attempts": shorts_attempts,
                    },
                    metadata={"model": remote_client.model},
                )
        else:
            planning_provider = "Session checkpoint"
            planning_tool = "Global analysis cache"
        await activity.emit_safely(
            job_id,
            stage="remote_planning",
            category="planning",
            status="completed",
            message_key="activity.planning.clips_selected",
            progress=STAGES["remote_planning"].progress,
            provider=planning_provider,
            tool=planning_tool,
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
                focus_windows_by_clip: dict[int, tuple[tuple[int, int], ...]] | None = None,
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
                        focus_windows=(focus_windows_by_clip or {}).get(clip_index, ()),
                    )
                    local_understanding = await visual_planner.plan(
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
                    visual_attempts.extend(classified_visual_attempts())

            clip_analysis_fingerprint = checkpoint_fingerprint({
                "contract_version": CLIP_ANALYSIS_CHECKPOINT_VERSION,
                "global_analysis_fingerprint": global_analysis_fingerprint,
                "clips": [
                    {"start_ms": clip.start_ms, "end_ms": clip.end_ms}
                    for clip in plan.clips
                ],
                "vision": {
                    "frame_count": agentic_config.vision_clip_frame_count,
                    "max_width": agentic_config.vision_frame_max_width,
                    "max_height": agentic_config.vision_frame_max_height,
                    "max_bytes": agentic_config.vision_frame_max_bytes,
                },
            })
            prior_attempt_id = _checkpoint_job_id(
                prior_quality_feedback.get("prior_attempt_id")
            )
            clip_hit = None
            if prior_attempt_id is not None:
                clip_hit = await load_job_checkpoint(
                    job_id=prior_attempt_id,
                    stage="clip_visual_analysis",
                    fingerprint=clip_analysis_fingerprint,
                )
            if clip_hit is not None:
                try:
                    clip_frame_manifests = {
                        int(index): _frame_manifest(payload)
                        for index, payload in (
                            clip_hit.payload.get("clip_frame_manifests") or {}
                        ).items()
                        if isinstance(payload, dict)
                    }
                    clip_visual_understandings = {
                        int(index): VisualUnderstanding.model_validate(payload)
                        for index, payload in (
                            clip_hit.payload.get("clip_visual_understandings") or {}
                        ).items()
                        if isinstance(payload, dict)
                    }
                    visual_understanding = VisualUnderstanding.model_validate(
                        clip_hit.payload["visual_understanding"]
                    )
                    clip_vision_call_count = int(
                        clip_hit.payload.get("clip_vision_call_count") or 0
                    )
                    visual_attempts.extend(
                        dict(item)
                        for item in clip_hit.payload.get("visual_attempts") or []
                        if isinstance(item, dict)
                    )
                    if not clip_frame_manifests or not clip_visual_understandings:
                        raise ValueError("cached clip analysis is incomplete")
                except (KeyError, TypeError, ValueError):
                    clip_hit = None
                    clip_frame_manifests = {}
                    clip_visual_understandings = {}

            await activity.stage(job_id, "sampling_agentic_frames")
            if clip_hit is None:
                track_checkpoint("clip_visual_analysis", reused=False)
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
                clip_analysis_tool = "FFmpeg + 9Router clip-local analysis"
            else:
                track_checkpoint("clip_visual_analysis", reused=True)
                clip_analysis_tool = "Prior-attempt checkpoint"
            clip_checkpoint_payload = {
                "visual_understanding": visual_understanding.to_dict(),
                "clip_frame_manifests": {
                    str(index): manifest.to_dict()
                    for index, manifest in clip_frame_manifests.items()
                },
                "clip_visual_understandings": {
                    str(index): understanding.to_dict()
                    for index, understanding in clip_visual_understandings.items()
                },
                "clip_vision_call_count": clip_vision_call_count,
                "visual_attempts": visual_attempts,
            }
            await save_job_checkpoint(
                job_id=job_id,
                stage="clip_visual_analysis",
                contract_version=CLIP_ANALYSIS_CHECKPOINT_VERSION,
                fingerprint=clip_analysis_fingerprint,
                payload=clip_checkpoint_payload,
                metadata={"clip_count": len(plan.clips)},
                reused_from_job_id=prior_attempt_id if clip_hit is not None else None,
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
                tool=clip_analysis_tool,
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
            edit_planner = AgenticEditPlanner(remote_client)
            async def plan_agentic_edit(
                visual_coverage_feedback: dict[str, Any] | None = None,
            ):
                return await edit_planner.plan(
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
                    catalog_snapshot=catalog_snapshot,
                    renderer_capabilities=REFRAME_RENDER_CAPABILITIES,
                    defer_registry_repair=repair_mode in {
                        RepairMode.REPORT,
                        RepairMode.ENFORCE,
                    },
                )

            edit_plan = await plan_agentic_edit()
            edit_planner_attempts = [
                {**attempt.to_dict(), "category": "initial_generation"}
                for attempt in getattr(remote_client, "last_attempts", ())
            ]
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
                focus_windows_by_clip: dict[int, tuple[tuple[int, int], ...]] = {}
                for segment in visual_coverage.segments:
                    if not segment.blocker_codes:
                        continue
                    focus_windows_by_clip.setdefault(segment.clip_index, ())
                    focus_windows_by_clip[segment.clip_index] += (
                        (segment.source_start_ms, segment.source_end_ms),
                    )
                repair_frame_count = max(
                    agentic_config.vision_clip_frame_count,
                    agentic_config.vision_clip_repair_frame_count,
                )
                await analyze_clip_windows(
                    set(visual_coverage.affected_clip_indexes),
                    max_frames=repair_frame_count,
                    focus_windows_by_clip=focus_windows_by_clip,
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
                await store.register_artifact(
                    job_id,
                    visual_path,
                    kind="visual_understanding",
                )
                await store.register_artifact(
                    job_id,
                    shorts_plan_path,
                    kind="shorts_plan",
                )
                if repair_mode is RepairMode.OFF:
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
                await save_job_checkpoint(
                    job_id=job_id,
                    stage="clip_visual_analysis",
                    contract_version=CLIP_ANALYSIS_CHECKPOINT_VERSION,
                    fingerprint=clip_analysis_fingerprint,
                    payload={
                        "visual_understanding": visual_understanding.to_dict(),
                        "clip_frame_manifests": {
                            str(index): manifest.to_dict()
                            for index, manifest in clip_frame_manifests.items()
                        },
                        "clip_visual_understandings": {
                            str(index): understanding.to_dict()
                            for index, understanding in clip_visual_understandings.items()
                        },
                        "clip_vision_call_count": clip_vision_call_count,
                        "visual_attempts": visual_attempts,
                    },
                    metadata={
                        "clip_count": len(plan.clips),
                        "repair_attempted": True,
                    },
                    reused_from_job_id=(
                        prior_attempt_id if clip_hit is not None else None
                    ),
                )
            unresolved_repair_findings = ()
            if repair_mode in {RepairMode.REPORT, RepairMode.ENFORCE}:
                known_evidence_ids_by_clip = {
                    int(clip["clip_index"]): clip["evidence_ids"]
                    for clip in shorts_plan_artifact["clips"]
                }

                def plan_objective_findings(candidate: Any, coverage: Any):
                    nonlocal predictive_repair_findings
                    candidate_assets = {
                        asset.id
                        for clip in candidate.clips
                        for asset in clip.asset_requests
                    }
                    candidate_preflight = build_preflight(
                        candidate,
                        available_capabilities=REFRAME_RENDER_CAPABILITIES,
                        asset_policy=effective_asset_policy,
                        stock_policy=effective_stock_policy,
                        pending_asset_ids=(
                            candidate_assets
                            if (
                                effective_asset_policy in {"auto", "required"}
                                or effective_stock_policy in {"auto", "required"}
                            )
                            else set()
                        ),
                        known_region_ids=(
                            region.id for region in visual_understanding.regions
                        ),
                        known_track_ids=(
                            track.id for track in visual_understanding.tracks
                        ),
                        known_evidence_ids_by_clip=known_evidence_ids_by_clip,
                        visual_coverage=coverage,
                        max_segments_per_clip=agentic_config.max_segments_per_clip,
                        max_overlays_per_clip=agentic_config.max_overlays_per_clip,
                        max_assets_per_clip=agentic_config.max_assets_per_clip,
                    )
                    predictions = predict_plan_findings(
                        candidate,
                        source_aspect_ratios={
                            clip.clip_index: media.width / max(1, media.height)
                            for clip in candidate.clips
                        },
                    )
                    if not predictive_repair_findings:
                        predictive_repair_findings = tuple(predictions)
                    findings = [
                        *repair_findings_from_preflight(candidate_preflight),
                        *repair_findings_from_visual_coverage(coverage),
                        *(
                            item.to_repair_finding()
                            for item in predictions
                        ),
                    ]
                    try:
                        validate_creative_intent_conformance(candidate, creative_intent)
                    except ValueError:
                        findings.append(make_repair_finding(
                            "EDIT_PLAN_INTENT_MISMATCH",
                            clip_index=1,
                            objective=True,
                            values={"observed": "intent_mismatch"},
                            source="creative_intent",
                        ))
                    return candidate_preflight, findings

                _, repair_findings = plan_objective_findings(
                    edit_plan,
                    visual_coverage,
                )
                deferred_by_clip = {
                    defect.clip_index: defect
                    for defect in edit_planner.deferred_defects
                }
                for defect in edit_planner.deferred_defects:
                    issue_count = int(
                        (defect.evidence.get("validation") or {}).get("issue_count")
                        or 0
                    )
                    repair_findings.append(make_repair_finding(
                        defect.code,
                        clip_index=defect.clip_index,
                        objective=True,
                        values={
                            "observed": defect.code,
                            "count": issue_count,
                        },
                        source="edit_plan_validator",
                    ))
                selected_findings, overflow_findings = bounded_repair_findings(
                    repair_findings
                )
                plan_original_codes = tuple(sorted({
                    finding.code
                    for finding in selected_findings
                    if finding.objective
                }))
                plan_stage_status = "not_triggered"
                plan_quality_floor: dict[str, Any] = {
                    "accepted": False,
                    "violation_codes": [],
                }
                plan_checkpoint_reused = False
                unresolved_repair_findings = overflow_findings
                candidate_clips = {}
                for clip in edit_plan.clips:
                    candidate = clip.model_dump(mode="json")
                    deferred = deferred_by_clip.get(clip.clip_index)
                    if deferred is not None:
                        candidate["invalid_candidate"] = deferred.invalid_candidate
                    candidate_clips[clip.clip_index] = candidate
                excerpts = []
                for clip_index, selected_clip in enumerate(plan.clips, start=1):
                    text = " ".join(
                        str(segment.get("text") or "").strip()
                        for segment in transcript.segments
                        if int(segment.get("end") or 0) > selected_clip.start_ms
                        and int(segment.get("start") or 0) < selected_clip.end_ms
                    )[:1_500]
                    if text:
                        excerpts.append(TranscriptExcerpt(
                            clip_index=clip_index,
                            start_ms=selected_clip.start_ms,
                            end_ms=selected_clip.end_ms,
                            text=text,
                        ))
                try:
                    repair_request, repair_dispositions = build_repair_batch(
                        stage=RepairStage.PLAN_REPAIR,
                        mode=repair_mode,
                        findings=selected_findings,
                        budget=RepairBudget(),
                        candidate_clips=candidate_clips,
                        available_capabilities=REFRAME_RENDER_CAPABILITIES,
                        catalog_context=catalog_snapshot or {},
                        immutable_constraints={
                            "source_duration_ms": media.duration_ms,
                            "selected_source_windows": [
                                {
                                    "clip_index": index,
                                    "start_ms": selected_clip.start_ms,
                                    "end_ms": selected_clip.end_ms,
                                }
                                for index, selected_clip in enumerate(plan.clips, start=1)
                            ],
                            "max_segments_per_clip": agentic_config.max_segments_per_clip,
                            "max_overlays_per_clip": agentic_config.max_overlays_per_clip,
                            "max_assets_per_clip": agentic_config.max_assets_per_clip,
                            "max_generated_assets_per_clip": effective_generated_asset_cap,
                            "max_stock_assets_per_clip": effective_stock_asset_cap,
                            "asset_policy": effective_asset_policy,
                            "stock_policy": effective_stock_policy,
                            "subtitles_required": True,
                        },
                        editing_prompt=state["prompt"],
                        transcript_excerpts=tuple(excerpts),
                    )
                except RepairContractError as exc:
                    if exc.code != "REPAIR_NOT_ELIGIBLE":
                        raise
                else:
                    repair_report = compact_repair_observability(
                        repair_request.to_report_dict()
                    )
                    repair_checkpoint_reports["plan_repair"] = repair_report
                    plan_repair_fingerprint = checkpoint_fingerprint({
                        "contract_version": PLAN_REPAIR_CHECKPOINT_VERSION,
                        "source_sha256": source_hash,
                        "request_fingerprint": repair_report["request_fingerprint"],
                        "registry_version": DEFECT_REGISTRY_VERSION,
                        "schema_fingerprint": structured_output(
                            EDIT_PLAN_REPAIR_SCHEMA
                        ).fingerprint,
                        "repair_prompt_version": REPAIR_SYSTEM_PROMPT_VERSION,
                        "catalog_version": edit_plan.catalog_version,
                        "catalog_manifest_sha256": edit_plan.catalog_manifest_sha256,
                        "renderer_capabilities": sorted(REFRAME_RENDER_CAPABILITIES),
                        "mode": repair_mode.value,
                    })
                    affected_clip_indexes = tuple(
                        repair_report["affected_clip_ids"]
                    )

                    def validate_repaired_response(value: Any):
                        return merge_repaired_edit_plan_response(
                            value,
                            base_plan=edit_plan,
                            affected_clip_indexes=affected_clip_indexes,
                            selected_clips=plan.clips,
                            known_region_ids=(
                                region.id for region in visual_understanding.regions
                            ),
                            known_track_ids=(
                                track.id for track in visual_understanding.tracks
                            ),
                            known_evidence_ids_by_clip=known_evidence_ids_by_clip,
                            max_segments_per_clip=agentic_config.max_segments_per_clip,
                            max_overlays_per_clip=agentic_config.max_overlays_per_clip,
                            max_assets_per_clip=agentic_config.max_assets_per_clip,
                            max_generated_assets_per_clip=effective_generated_asset_cap,
                            max_stock_assets_per_clip=effective_stock_asset_cap,
                            asset_policy=effective_asset_policy,
                            stock_policy=effective_stock_policy,
                            renderer_capabilities=REFRAME_RENDER_CAPABILITIES,
                            catalog_snapshot=catalog_snapshot,
                            creative_intent=creative_intent,
                        )

                    repair_hit = await load_job_checkpoint(
                        job_id=job_id,
                        stage="plan_repair",
                        fingerprint=plan_repair_fingerprint,
                    )
                    repaired_plan = None
                    if repair_hit is not None:
                        repair_checkpoint_reports["plan_repair"] = (
                            compact_repair_observability(
                                dict(repair_hit.payload.get("report") or {})
                            )
                        )
                        if repair_hit.payload.get("status") == "repaired":
                            try:
                                cached_plan = repair_hit.payload["edit_plan"]
                                repaired_plan = validate_repaired_response({
                                    "requested_capabilities": cached_plan[
                                        "requested_capabilities"
                                    ],
                                    "clips": [
                                        clip
                                        for clip in cached_plan["clips"]
                                        if int(clip["clip_index"])
                                        in affected_clip_indexes
                                    ],
                                })
                            except (KeyError, TypeError, ValueError, EditPlanError):
                                repair_hit = None
                                repaired_plan = None
                                repair_checkpoint_reports["plan_repair"] = repair_report
                        if repair_hit is not None:
                            track_checkpoint("plan_repair", reused=True)
                            plan_checkpoint_reused = True
                            plan_stage_status = str(
                                repair_hit.payload.get("status") or "failed"
                            )
                            cached_quality = repair_hit.payload.get("quality")
                            if isinstance(cached_quality, dict):
                                plan_quality_floor = cached_quality
                    if repair_hit is None:
                        track_checkpoint("plan_repair", reused=False)
                    if repair_hit is None and repair_mode is RepairMode.REPORT:
                        plan_stage_status = "report_only"
                        unresolved_repair_findings = tuple(
                            finding
                            for finding in selected_findings
                            if finding.objective
                        ) + overflow_findings
                        await save_job_checkpoint(
                            job_id=job_id,
                            stage="plan_repair",
                            contract_version=PLAN_REPAIR_CHECKPOINT_VERSION,
                            fingerprint=plan_repair_fingerprint,
                            payload={
                                "status": "report_only",
                                "report": repair_report,
                                "dispositions": [
                                    item.to_dict() for item in repair_dispositions
                                ],
                            },
                            metadata={"mode": repair_mode.value},
                        )
                    elif repair_hit is None:
                        try:
                            repaired_response = await remote_client.complete_structured(
                                schema_name=EDIT_PLAN_REPAIR_SCHEMA,
                                system_prompt=REPAIR_SYSTEM_PROMPT,
                                user_prompt=json.dumps(
                                    repair_request.to_provider_dict(),
                                    ensure_ascii=False,
                                ),
                                reasoning_effort=getattr(
                                    remote_client,
                                    "reasoning_effort",
                                    "medium",
                                ),
                            )
                            edit_planner_attempts.extend(
                                {
                                    **attempt.to_dict(),
                                    "category": "plan_repair",
                                }
                                for attempt in getattr(
                                    remote_client,
                                    "last_attempts",
                                    (),
                                )
                            )
                            repaired_plan = validate_repaired_response(
                                repaired_response
                            )
                            repaired_coverage = build_clip_visual_coverage(
                                repaired_plan,
                                visual=visual_understanding,
                                clip_frame_manifests=clip_frame_manifests,
                                min_observations=agentic_config.crop_coverage_min_observations,
                                min_temporal_coverage_ratio=agentic_config.crop_coverage_min_ratio,
                                max_observation_gap_ms=agentic_config.crop_coverage_max_gap_ms,
                                repair_attempted=True,
                                initial_blocker_codes=visual_coverage.blocker_codes,
                            )
                            _, repaired_findings = plan_objective_findings(
                                repaired_plan,
                                repaired_coverage,
                            )
                            repaired_objective_codes = {
                                finding.code
                                for finding in repaired_findings
                                if finding.objective
                            }
                            original_objective_codes = {
                                finding.code
                                for finding in selected_findings
                                if finding.objective
                                and any(
                                    disposition.code == finding.code
                                    and disposition.eligible
                                    for disposition in repair_dispositions
                                )
                            }
                            affected_operation_ids = set()
                            allow_all_operations = set()
                            for finding in selected_findings:
                                if not finding.objective or finding.clip_index is None:
                                    continue
                                operation_ids = {
                                    str(record.values.get(key))
                                    for record in finding.evidence
                                    for key in ("operation_id", "segment_id")
                                    if record.values.get(key)
                                }
                                if operation_ids:
                                    affected_operation_ids.update(operation_ids)
                                else:
                                    allow_all_operations.add(finding.clip_index)
                            for clip in edit_plan.clips:
                                if clip.clip_index in allow_all_operations:
                                    affected_operation_ids.update(
                                        segment.id for segment in clip.segments
                                    )
                                    affected_operation_ids.update(
                                        overlay.id
                                        for segment in clip.segments
                                        for overlay in segment.overlays
                                    )
                                    affected_operation_ids.update(
                                        asset.id for asset in clip.asset_requests
                                    )
                            quality = evaluate_repair_quality_floor(
                                edit_plan,
                                repaired_plan,
                                original_codes=original_objective_codes,
                                repaired_codes=repaired_objective_codes,
                                available_capabilities=REFRAME_RENDER_CAPABILITIES,
                                affected_clip_indexes=affected_clip_indexes,
                                affected_operation_ids=affected_operation_ids,
                                allow_catalog_change_clip_indexes={
                                    finding.clip_index
                                    for finding in selected_findings
                                    if "CATALOG" in finding.code
                                    and finding.clip_index is not None
                                },
                            )
                            if quality.accepted:
                                edit_plan = repaired_plan
                                visual_coverage = repaired_coverage
                                unresolved_repair_findings = tuple(
                                    finding
                                    for finding in repaired_findings
                                    if finding.objective
                                ) + overflow_findings
                                repair_status = "repaired"
                            else:
                                repaired_plan = None
                                unresolved_repair_findings = tuple(
                                    finding
                                    for finding in selected_findings
                                    if finding.objective
                                ) + overflow_findings
                                repair_status = "rejected"
                            plan_stage_status = repair_status
                            plan_quality_floor = quality.to_dict()
                            await save_job_checkpoint(
                                job_id=job_id,
                                stage="plan_repair",
                                contract_version=PLAN_REPAIR_CHECKPOINT_VERSION,
                                fingerprint=plan_repair_fingerprint,
                                payload={
                                    "status": repair_status,
                                    "report": repair_report,
                                    "quality": quality.to_dict(),
                                    **(
                                        {"edit_plan": edit_plan.to_dict()}
                                        if repair_status == "repaired"
                                        else {}
                                    ),
                                },
                                metadata={"mode": repair_mode.value},
                            )
                        except (
                            EditPlanError,
                            NineRouterError,
                            RepairContractError,
                            ValueError,
                        ) as exc:
                            failed_attempts = [
                                {**attempt.to_dict(), "category": "plan_repair"}
                                for attempt in (
                                    tuple(getattr(exc, "attempts", ()))
                                    or tuple(getattr(remote_client, "last_attempts", ()))
                                )
                            ]
                            edit_planner_attempts.extend(failed_attempts)
                            plan_stage_status = "failed"
                            unresolved_repair_findings = tuple(
                                finding
                                for finding in selected_findings
                                if finding.objective
                            ) + overflow_findings
                            await save_job_checkpoint(
                                job_id=job_id,
                                stage="plan_repair",
                                contract_version=PLAN_REPAIR_CHECKPOINT_VERSION,
                                fingerprint=plan_repair_fingerprint,
                                payload={
                                    "status": "failed",
                                    "report": repair_report,
                                    "error_code": str(
                                        getattr(exc, "code", "EDIT_PLAN_INVALID")
                                    ),
                                },
                                metadata={"mode": repair_mode.value},
                            )
                    elif repaired_plan is not None:
                        edit_plan = repaired_plan
                        visual_coverage = build_clip_visual_coverage(
                            edit_plan,
                            visual=visual_understanding,
                            clip_frame_manifests=clip_frame_manifests,
                            min_observations=agentic_config.crop_coverage_min_observations,
                            min_temporal_coverage_ratio=agentic_config.crop_coverage_min_ratio,
                            max_observation_gap_ms=agentic_config.crop_coverage_max_gap_ms,
                            repair_attempted=True,
                            initial_blocker_codes=visual_coverage.blocker_codes,
                        )
                        _, repaired_findings = plan_objective_findings(
                            edit_plan,
                            visual_coverage,
                        )
                        unresolved_repair_findings = tuple(
                            finding
                            for finding in repaired_findings
                            if finding.objective
                        ) + overflow_findings
                    else:
                        unresolved_repair_findings = tuple(
                            finding
                            for finding in selected_findings
                            if finding.objective
                        ) + overflow_findings

                    plan_resolution = compute_repair_resolution(
                        plan_original_codes,
                        (
                            finding.code
                            for finding in unresolved_repair_findings
                            if finding.objective
                        ),
                    )
                    repair_stage_records["plan_repair"] = {
                        "stage": RepairStage.PLAN_REPAIR.value,
                        "status": (
                            plan_stage_status
                            if plan_stage_status in {
                                "report_only", "repaired", "rejected", "failed"
                            }
                            else "failed"
                        ),
                        "request": repair_checkpoint_reports["plan_repair"],
                        "dispositions": [
                            item.to_dict() for item in repair_dispositions
                        ],
                        "resolution": plan_resolution.to_dict(),
                        "quality_floor": plan_quality_floor,
                        "attempts": [
                            item
                            for item in edit_planner_attempts
                            if item.get("category") == "plan_repair"
                        ],
                        "checkpoint_reused": plan_checkpoint_reused,
                    }
                    if plan_stage_status in {"report_only", "rejected", "failed"}:
                        await persist_partial_repair_report()

            proposed_edit_plan = edit_plan
            if (
                server_mode == "render"
                and fallback_enabled
            ):
                compilation = compile_baseline_plan(
                    edit_plan,
                    visual_coverage=visual_coverage,
                    available_capabilities=REFRAME_RENDER_CAPABILITIES,
                    remaining_defects=tuple(
                        FallbackDirective(
                            code=finding.code,
                            clip_index=finding.clip_index,
                            segment_id=next((
                                str(record.values.get("segment_id"))
                                for record in finding.evidence
                                if record.values.get("segment_id")
                            ), ""),
                        )
                        for finding in unresolved_repair_findings
                        if finding.objective
                    ),
                    max_segments_per_clip=agentic_config.max_segments_per_clip,
                    max_overlays_per_clip=agentic_config.max_overlays_per_clip,
                    max_assets_per_clip=agentic_config.max_assets_per_clip,
                )
                edit_plan = compilation.plan
                fallback_entries = merge_fallback_entries(
                    fallback_entries,
                    compilation.entries,
                )
                if compilation.entries:
                    visual_coverage = build_clip_visual_coverage(
                        edit_plan,
                        visual=visual_understanding,
                        clip_frame_manifests=clip_frame_manifests,
                        min_observations=agentic_config.crop_coverage_min_observations,
                        min_temporal_coverage_ratio=agentic_config.crop_coverage_min_ratio,
                        max_observation_gap_ms=agentic_config.crop_coverage_max_gap_ms,
                        repair_attempted=True,
                        initial_blocker_codes=visual_coverage.blocker_codes,
                    )
            if proposed_edit_plan.degraded:
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
                    if not (server_mode == "render" and fallback_enabled):
                        raise EditPlanError(
                            "EDIT_PLAN_INTENT_MISMATCH",
                            str(exc),
                            evidence={
                                "intent_conformance": (
                                    creative_intent_conformance_evidence(exc)
                                ),
                            },
                        ) from exc
                    creative_conformance = {
                        "version": creative_intent.version,
                        "status": "degraded",
                        "error_code": "EDIT_PLAN_INTENT_MISMATCH",
                        "evidence": creative_intent_conformance_evidence(exc),
                    }
                    fallback_entries = merge_fallback_entries(
                        fallback_entries,
                        (FallbackEntry(
                            code="CREATIVE_INTENT_UNMET",
                            clip_index=1,
                            segment_id="plan",
                            requested="creative_intent",
                            executed="validated_baseline_plan",
                            reason="The proposed plan did not fully satisfy the creative intent contract.",
                        ),),
                    )
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
            proposed_edit_plan_path = output_dir / names.proposed_edit_plan
            proposed_edit_plan_path.write_text(
                json.dumps(proposed_edit_plan.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(
                job_id,
                proposed_edit_plan_path,
                kind="proposed_edit_plan",
            )
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
                    evidence={
                        "visual_coverage": visual_coverage.compact_feedback(),
                    },
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
                try:
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
                except AssetResolutionError as exc:
                    if not fallback_enabled:
                        raise
                    compilation = compile_baseline_plan(
                        edit_plan,
                        available_capabilities=REFRAME_RENDER_CAPABILITIES,
                        omitted_asset_ids=planned_asset_ids,
                        cause_code=exc.code,
                    )
                    edit_plan = compilation.plan
                    fallback_entries = merge_fallback_entries(
                        fallback_entries,
                        compilation.entries,
                    )
                    planned_asset_ids = set()
                    asset_result = write_asset_manifest(
                        edit_plan,
                        output_dir=output_dir,
                        asset_policy=effective_asset_policy,
                        stock_policy=effective_stock_policy,
                        status=f"fallback_omitted:{exc.code}"[:80],
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

            edit_plan_path = output_dir / names.edit_plan
            edit_plan_path.write_text(
                json.dumps(edit_plan.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(job_id, edit_plan_path, kind="edit_plan")
            catalog_usage = (
                build_catalog_usage(creative_catalog, edit_plan)
                if creative_catalog is not None
                else None
            )
            if catalog_usage is not None:
                catalog_usage_path = output_dir / names.creative_catalog_usage
                catalog_usage_path.write_text(
                    json.dumps(catalog_usage, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                await store.register_artifact(
                    job_id,
                    catalog_usage_path,
                    kind="creative_catalog_usage",
                )
            fallback_ledger_path = output_dir / names.fallback_ledger
            fallback_ledger = {
                "version": "fallback_ledger.v1",
                "status": "with_limitations" if fallback_entries else "unchanged",
                "summary": {
                    "fallbacks": len(fallback_entries),
                    "codes": sorted({entry.code for entry in fallback_entries}),
                },
                "entries": [entry.to_dict() for entry in fallback_entries],
            }
            fallback_ledger_path.write_text(
                json.dumps(fallback_ledger, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(
                job_id,
                fallback_ledger_path,
                kind="fallback_ledger",
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
                "proposed_edit_plan": names.proposed_edit_plan,
                "fallback_ledger": names.fallback_ledger,
                "creative_catalog_usage": (
                    names.creative_catalog_usage if catalog_usage is not None else None
                ),
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
        render_settings = render_settings_from_config(
            self.config.mvp,
            caption_font_family=getattr(
                self,
                "caption_font_family",
                "DejaVu Sans",
            ),
        )
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
        agentic_renderer = None
        if agentic_requested and server_mode == "render":
            agentic_renderer = AgenticShortRenderer(
                render_settings,
                creative_catalog=creative_catalog,
            )
            if fallback_enabled:
                try:
                    ffmpeg_preflight = await asyncio.to_thread(
                        agentic_renderer.preflight_plan,
                        source=source,
                        edit_plan=edit_plan,
                        selected_clips=plan.clips,
                        visual_understanding=visual_understanding,
                        transcript_segments=transcript.segments,
                        destination_dir=output_dir,
                        source_media=media,
                        crop_hysteresis_ratio=(
                            self.config.agentic_editing.crop_hysteresis_ratio
                        ),
                        crop_smoothing_alpha=(
                            self.config.agentic_editing.crop_smoothing_alpha
                        ),
                        max_crop_velocity_ratio_per_second=(
                            self.config.agentic_editing.max_crop_velocity_ratio_per_second
                        ),
                        resolved_assets=asset_result.paths,
                    )
                except RenderError as exc:
                    compilation = compile_baseline_plan(
                        edit_plan,
                        available_capabilities=REFRAME_RENDER_CAPABILITIES,
                        force_minimal=True,
                        cause_code=exc.code,
                    )
                    edit_plan = compilation.plan
                    fallback_entries = merge_fallback_entries(
                        fallback_entries,
                        compilation.entries,
                    )
                    for path in asset_result.paths.values():
                        Path(path).unlink(missing_ok=True)
                    asset_result = write_asset_manifest(
                        edit_plan,
                        output_dir=output_dir,
                        asset_policy=effective_asset_policy,
                        stock_policy=effective_stock_policy,
                        status=f"preflight_fallback:{exc.code}"[:80],
                    )
                    preflight = build_preflight(
                        edit_plan,
                        available_capabilities=REFRAME_RENDER_CAPABILITIES,
                        asset_policy=effective_asset_policy,
                        stock_policy=effective_stock_policy,
                        known_region_ids=(
                            region.id for region in visual_understanding.regions
                        ),
                        known_track_ids=(
                            track.id for track in visual_understanding.tracks
                        ),
                        known_evidence_ids_by_clip={
                            int(clip["clip_index"]): clip["evidence_ids"]
                            for clip in shorts_plan_artifact["clips"]
                        },
                        visual_coverage=visual_coverage,
                        max_segments_per_clip=(
                            self.config.agentic_editing.max_segments_per_clip
                        ),
                        max_overlays_per_clip=(
                            self.config.agentic_editing.max_overlays_per_clip
                        ),
                        max_assets_per_clip=(
                            self.config.agentic_editing.max_assets_per_clip
                        ),
                    )
                    if preflight.blocking:
                        raise EditPlanError(
                            "EDIT_PREFLIGHT_BLOCKED",
                            "deterministic baseline preflight is blocked",
                        )
                    ffmpeg_preflight = await asyncio.to_thread(
                        agentic_renderer.preflight_plan,
                        source=source,
                        edit_plan=edit_plan,
                        selected_clips=plan.clips,
                        visual_understanding=visual_understanding,
                        transcript_segments=transcript.segments,
                        destination_dir=output_dir,
                        source_media=media,
                        crop_hysteresis_ratio=(
                            self.config.agentic_editing.crop_hysteresis_ratio
                        ),
                        crop_smoothing_alpha=(
                            self.config.agentic_editing.crop_smoothing_alpha
                        ),
                        max_crop_velocity_ratio_per_second=(
                            self.config.agentic_editing.max_crop_velocity_ratio_per_second
                        ),
                        resolved_assets=asset_result.paths,
                    )
                    ffmpeg_preflight["simplified_after"] = exc.code
                    edit_plan_path.write_text(
                        json.dumps(edit_plan.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    fallback_ledger["status"] = "with_limitations"
                    fallback_ledger["summary"] = {
                        "fallbacks": len(fallback_entries),
                        "codes": sorted({entry.code for entry in fallback_entries}),
                    }
                    fallback_ledger["entries"] = [
                        entry.to_dict() for entry in fallback_entries
                    ]
                    fallback_ledger_path.write_text(
                        json.dumps(fallback_ledger, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    await store.register_artifact(job_id, edit_plan_path, kind="edit_plan")
                    await store.register_artifact(
                        job_id,
                        fallback_ledger_path,
                        kind="fallback_ledger",
                    )
                    preflight_path.write_text(
                        json.dumps(preflight.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    await store.register_artifact(
                        job_id,
                        preflight_path,
                        kind="edit_preflight",
                    )
                    agentic_manifest["preflight_status"] = preflight.status
                ffmpeg_preflight_path = output_dir / names.ffmpeg_preflight
                ffmpeg_preflight_path.write_text(
                    json.dumps(ffmpeg_preflight, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                await store.register_artifact(
                    job_id,
                    ffmpeg_preflight_path,
                    kind="ffmpeg_preflight",
                )
                agentic_manifest["ffmpeg_preflight"] = names.ffmpeg_preflight

        if agentic_requested:
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
            agentic_manifest["assets"] = {
                "requested": int(asset_result.manifest["requested_count"]),
                "resolved": int(asset_result.manifest["resolved_count"]),
                "provider_calls": asset_result.provider_call_count,
            }
            agentic_manifest["fallbacks"] = fallback_ledger["summary"]
        if agentic_requested and server_mode == "render":
            agentic_result = await asyncio.to_thread(
                agentic_renderer.render_plan,
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
            try:
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
            except (FFMPEGAError, NineRouterError) as exc:
                effects_plan = None
                fallback_entries = merge_fallback_entries(
                    fallback_entries,
                    (FallbackEntry(
                        code="EFFECT_OMITTED",
                        clip_index=1,
                        segment_id="finishing",
                        requested="ffmpega_effect_plan",
                        executed="native_ffmpeg_render",
                        reason=str(getattr(exc, "code", "EFFECT_PLANNING_FAILED"))[:240],
                    ),),
                )
            if effects_plan is not None and effects_plan.effects:
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
                try:
                    final_video = await ffmpega.apply(
                        source=item.video_path,
                        destination=enhanced,
                        plan=effects_plan,
                    )
                except FFMPEGAError as exc:
                    fallback_entries = merge_fallback_entries(
                        fallback_entries,
                        (FallbackEntry(
                            code="EFFECT_OMITTED",
                            clip_index=len(final_outputs) + 1,
                            segment_id="finishing",
                            requested="ffmpega_finishing",
                            executed="native_ffmpeg_render",
                            reason=exc.code,
                        ),),
                    )
                    enhanced.unlink(missing_ok=True)
                else:
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
        promotion_report: dict[str, Any] | None = None
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
                        resolved_assets=asset_result.paths,
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
                policy=completion_policy(self.config.agentic_editing),
                limited_output_enabled=limited_output_promotion_enabled(),
                delivery=delivery_policy(self.config.agentic_editing),
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

        if agentic_requested:
            fallback_ledger["status"] = (
                "with_limitations" if fallback_entries else "unchanged"
            )
            fallback_ledger["summary"] = {
                "fallbacks": len(fallback_entries),
                "codes": sorted({entry.code for entry in fallback_entries}),
            }
            fallback_ledger["entries"] = [
                entry.to_dict() for entry in fallback_entries
            ]
            fallback_ledger_path.write_text(
                json.dumps(fallback_ledger, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(
                job_id,
                fallback_ledger_path,
                kind="fallback_ledger",
            )
            agentic_manifest["fallbacks"] = fallback_ledger["summary"]

        repair_report = None
        if agentic_requested:
            visual_stage = repair_stage_records.get("visual_repair")
            if visual_stage is not None:
                visual_stage["attempts"] = [
                    item
                    for item in visual_attempts
                    if item.get("category") == "visual_repair"
                ]
            plan_stage = repair_stage_records.get("plan_repair")
            if plan_stage is not None:
                plan_stage["attempts"] = [
                    item
                    for item in edit_planner_attempts
                    if item.get("category") == "plan_repair"
                ]
            repair_report = build_repair_report(
                mode=repair_mode,
                stage_records=repair_stage_records.values(),
                predictive_findings=predictive_repair_findings,
                fallback_entries=fallback_entries,
                reused_stages=checkpoint_summary["reused_stages"],
                recomputed_stages=checkpoint_summary["recomputed_stages"],
            )
            repair_report_path = output_dir / names.repair_report
            repair_report_path.write_text(
                json.dumps(repair_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await store.register_artifact(
                job_id,
                repair_report_path,
                kind="repair_report",
            )
            agentic_manifest["repair_report"] = names.repair_report

        outcome_report = build_completed_outcome_report(
            outputs=final_outputs,
            fallback_entries=fallback_entries,
            qa_blocker_codes=(promotion_report or {}).get("blocker_codes") or (),
            promotion_report=promotion_report,
            fingerprints={
                "source": source_hash,
                "prompt": checkpoint_fingerprint({"prompt": state["prompt"]}),
                "renderer": checkpoint_fingerprint({
                    "profile": render_settings.quality_profile,
                    "width": render_settings.width,
                    "height": render_settings.height,
                }),
                "catalog": (
                    creative_catalog.manifest_sha256
                    if creative_catalog is not None
                    else ""
                ),
            },
            reused_stages=checkpoint_summary["reused_stages"],
            recomputed_stages=checkpoint_summary["recomputed_stages"],
            prior_limitation_codes=prior_quality_feedback.get(
                "retry_reason_codes",
                (),
            ),
            repair_report=repair_report,
        )
        outcome_report_path = output_dir / names.outcome_report
        outcome_report_path.write_text(
            json.dumps(outcome_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await store.register_artifact(
            job_id,
            outcome_report_path,
            kind="outcome_report",
        )
        if agentic_manifest is not None:
            agentic_manifest["outcome_report"] = names.outcome_report

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
                "checkpoints": checkpoint_summary,
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
            "outcome": outcome_report,
            "effects": effects_plan.to_dict() if effects_plan is not None else {"effects": []},
            "outputs": final_outputs,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        await store.register_artifact(job_id, manifest_path, kind="manifest")
        return {
            "stage": "completed",
            "stt_model": transcript.model,
            "clip_count": len(rendered),
            "checkpoints": checkpoint_summary,
            "outcome": outcome_report,
        }
