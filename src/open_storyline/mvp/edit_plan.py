from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence
import json
import os
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from open_storyline.mvp.ninerouter import NineRouterClient
from open_storyline.mvp.prompts import EDIT_PLAN_PROMPT_VERSION, EDIT_PLAN_SYSTEM_PROMPT
from open_storyline.mvp.scene_boundaries import SceneBoundaryReport
from open_storyline.mvp.shorts import ShortCandidate, ShortsPlan
from open_storyline.mvp.visual_understanding import VisualUnderstanding


EDIT_PLAN_VERSION = "edit_plan.v1"
SHADOW_PLANNER_VERSION = "legacy-shadow.v1"
AGENTIC_PLANNER_VERSION = "agentic-editor.v1"

SUPPORTED_CAPABILITIES = frozenset({
    "crop",
    "fit",
    "letterbox",
    "focus_zoom",
    "source_cutaway",
    "image_overlay",
    "pip",
    "text_emphasis",
    "hard_cut",
    "fade",
    "xfade",
    "subtitles",
})

EditMode = Literal["legacy", "agentic"]
AssetPolicy = Literal["off", "auto"]
AgenticServerMode = Literal["off", "shadow", "render"]
LayoutMode = Literal["crop", "fit", "letterbox", "pip", "source"]
TransitionKind = Literal["cut", "fade", "xfade"]
OverlayKind = Literal["text", "image", "source", "pip"]
AssetKind = Literal["generated_image", "stock_image", "stock_video"]
AssetProvider = Literal["9router", "pexels"]


class EditPlanError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def _safe_text(value: str, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if "\x00" in text:
        raise ValueError("text contains a null byte")
    return text[:limit]


def _safe_identifier(value: str, *, limit: int = 80) -> str:
    text = _safe_text(value, limit=limit)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", text):
        raise ValueError("identifier contains unsafe characters")
    return text


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeWindow(PlanModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "TimeWindow":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class FocalTarget(PlanModel):
    region_id: str = Field(default="", max_length=80)
    track_id: str = Field(default="", max_length=80)
    semantic_role: Literal[
        "",
        "speaker",
        "screen",
        "text",
        "object",
        "demonstration_target",
        "background",
    ] = ""

    @field_validator("region_id", "track_id", "semantic_role")
    @classmethod
    def clean_identifier(cls, value: str) -> str:
        return _safe_identifier(value)

    @model_validator(mode="after")
    def require_reference(self) -> "FocalTarget":
        if not (self.region_id or self.track_id or self.semantic_role):
            raise ValueError("a focal target needs a region, track, or semantic role")
        return self


class LayoutSpec(PlanModel):
    mode: LayoutMode
    focal_target: FocalTarget | None = None
    fallback: Literal["crop", "fit", "letterbox", "source"] = "fit"
    safe_margin_ratio: float = Field(default=0.08, ge=0, le=0.35, allow_inf_nan=False)
    max_zoom: float = Field(default=1.0, ge=1.0, le=4.0, allow_inf_nan=False)


class TransitionSpec(PlanModel):
    kind: TransitionKind = "cut"
    duration_ms: int = Field(default=0, ge=0, le=1500)

    @model_validator(mode="after")
    def validate_duration(self) -> "TransitionSpec":
        if self.kind == "cut" and self.duration_ms != 0:
            raise ValueError("cut transitions must have zero duration")
        if self.kind != "cut" and self.duration_ms <= 0:
            raise ValueError("fade transitions require a positive duration")
        return self


class OverlaySpec(PlanModel):
    id: str = Field(min_length=1, max_length=80)
    kind: OverlayKind
    timeline_window: TimeWindow
    text: str = Field(default="", max_length=500)
    asset_id: str = Field(default="", max_length=80)
    opacity: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    width_ratio: float = Field(default=0.35, ge=0.08, le=1, allow_inf_nan=False)
    position: Literal["center", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"] = "center"

    @field_validator("id", "text", "asset_id")
    @classmethod
    def clean_text_fields(cls, value: str, info: Any) -> str:
        limit = 500 if info.field_name == "text" else 80
        return _safe_text(value, limit=limit) if info.field_name == "text" else _safe_identifier(value)

    @model_validator(mode="after")
    def validate_payload(self) -> "OverlaySpec":
        if self.kind == "text" and not self.text:
            raise ValueError("text overlays require text")
        if self.kind == "image" and not self.asset_id:
            raise ValueError("image overlays require asset_id")
        return self


class AssetRequest(PlanModel):
    id: str = Field(min_length=1, max_length=80)
    kind: AssetKind
    provider: AssetProvider
    timeline_window: TimeWindow
    purpose: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=500)
    prompt: str = Field(default="", max_length=8000)
    orientation: Literal["portrait", "landscape"] = "portrait"
    required: bool = True
    fallback: Literal["source", "fit", "omit"] = "source"

    @field_validator("id", "purpose", "rationale", "prompt")
    @classmethod
    def clean_asset_text(cls, value: str, info: Any) -> str:
        limits = {"id": 80, "purpose": 240, "rationale": 500, "prompt": 8000}
        if info.field_name == "id":
            return _safe_identifier(value)
        return _safe_text(value, limit=limits[info.field_name])

    @model_validator(mode="after")
    def validate_provider(self) -> "AssetRequest":
        if self.kind == "generated_image" and self.provider != "9router":
            raise ValueError("generated images must use 9router")
        if self.kind.startswith("stock_") and self.provider != "pexels":
            raise ValueError("stock assets must use pexels")
        if self.kind == "generated_image" and not self.prompt:
            raise ValueError("generated images require a prompt")
        return self


class EditSegment(PlanModel):
    id: str = Field(min_length=1, max_length=80)
    source_window: TimeWindow
    timeline_window: TimeWindow
    layout: LayoutSpec
    transition_in: TransitionSpec = Field(default_factory=TransitionSpec)
    overlays: tuple[OverlaySpec, ...] = Field(default=(), max_length=16)
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("id", "reason")
    @classmethod
    def clean_segment_text(cls, value: str, info: Any) -> str:
        return _safe_identifier(value) if info.field_name == "id" else _safe_text(value, limit=500)

    @field_validator("evidence_ids")
    @classmethod
    def clean_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_safe_identifier(value) for value in values)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("evidence IDs must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_durations(self) -> "EditSegment":
        if self.source_window.duration_ms != self.timeline_window.duration_ms:
            raise ValueError("source and timeline durations must match")
        for overlay in self.overlays:
            if (
                overlay.timeline_window.start_ms < self.timeline_window.start_ms
                or overlay.timeline_window.end_ms > self.timeline_window.end_ms
            ):
                raise ValueError("overlay timing must stay inside the segment")
        return self


class ClipEditPlan(PlanModel):
    clip_index: int = Field(ge=1, le=50)
    title: str = Field(default="", max_length=120)
    source_window: TimeWindow
    output_name: str = Field(min_length=1, max_length=120)
    segments: tuple[EditSegment, ...] = Field(min_length=1, max_length=48)
    asset_requests: tuple[AssetRequest, ...] = Field(default=(), max_length=8)

    @field_validator("title", "output_name")
    @classmethod
    def clean_clip_text(cls, value: str, info: Any) -> str:
        if info.field_name == "output_name":
            name = _safe_text(value, limit=120)
            if Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.mp4", name):
                raise ValueError("output_name must be a safe MP4 filename")
            return name
        return _safe_text(value, limit=120)

    @model_validator(mode="after")
    def validate_clip(self) -> "ClipEditPlan":
        segment_ids = [segment.id for segment in self.segments]
        asset_ids = [asset.id for asset in self.asset_requests]
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("segment IDs must be unique")
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("asset request IDs must be unique")

        last_timeline_end = 0
        for segment in self.segments:
            if (
                segment.source_window.start_ms < self.source_window.start_ms
                or segment.source_window.end_ms > self.source_window.end_ms
            ):
                raise ValueError("segment source timing must stay inside the clip")
            if segment.timeline_window.start_ms != last_timeline_end:
                raise ValueError("primary timeline segments must be contiguous and non-overlapping")
            last_timeline_end = segment.timeline_window.end_ms
        if self.segments[0].timeline_window.start_ms != 0:
            raise ValueError("clip timeline must start at zero")
        if last_timeline_end != self.source_window.duration_ms:
            raise ValueError("clip timeline must cover the complete selected duration")
        for asset in self.asset_requests:
            if asset.timeline_window.end_ms > self.source_window.duration_ms:
                raise ValueError("asset timing must stay inside the clip timeline")
        overlay_ids = [overlay.id for segment in self.segments for overlay in segment.overlays]
        if len(set(overlay_ids)) != len(overlay_ids):
            raise ValueError("overlay IDs must be unique inside a clip")
        return self


class EditPlan(PlanModel):
    version: Literal[EDIT_PLAN_VERSION] = EDIT_PLAN_VERSION
    planner_version: str = Field(min_length=1, max_length=80)
    prompt_version: str = Field(default=EDIT_PLAN_PROMPT_VERSION, min_length=1, max_length=80)
    source_duration_ms: int = Field(gt=0)
    requested_capabilities: tuple[str, ...] = Field(default=(), max_length=32)
    clips: tuple[ClipEditPlan, ...] = Field(min_length=1, max_length=50)

    @field_validator("planner_version", "prompt_version")
    @classmethod
    def clean_planner_version(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("requested_capabilities")
    @classmethod
    def validate_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_safe_text(value, limit=80) for value in values)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("requested capabilities must be unique")
        unknown = sorted(set(cleaned) - SUPPORTED_CAPABILITIES)
        if unknown:
            raise ValueError(f"unsupported capabilities: {', '.join(unknown)}")
        return cleaned

    @model_validator(mode="after")
    def validate_plan(self) -> "EditPlan":
        indexes = [clip.clip_index for clip in self.clips]
        if len(set(indexes)) != len(indexes):
            raise ValueError("clip indexes must be unique")
        for clip in self.clips:
            if clip.source_window.end_ms > self.source_duration_ms:
                raise ValueError("clip timing exceeds source duration")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def validate_edit_plan(value: Any, *, source_duration_ms: int | None = None) -> EditPlan:
    try:
        plan = EditPlan.model_validate(value)
    except ValidationError as exc:
        raise EditPlanError("EDIT_PLAN_INVALID", str(exc)[:2000]) from exc
    if source_duration_ms is not None and plan.source_duration_ms != int(source_duration_ms):
        raise EditPlanError("EDIT_PLAN_SOURCE_MISMATCH", "plan source duration does not match media")
    return plan


def required_capabilities(plan: EditPlan) -> frozenset[str]:
    capabilities = {"subtitles"}
    layout_capabilities = {
        "crop": "crop",
        "fit": "fit",
        "letterbox": "letterbox",
        "pip": "pip",
        "source": "source_cutaway",
    }
    overlay_capabilities = {
        "text": "text_emphasis",
        "image": "image_overlay",
        "source": "source_cutaway",
        "pip": "pip",
    }
    transition_capabilities = {"cut": "hard_cut", "fade": "fade", "xfade": "xfade"}
    for clip in plan.clips:
        for segment in clip.segments:
            capabilities.add(layout_capabilities[segment.layout.mode])
            if segment.layout.max_zoom > 1:
                capabilities.add("focus_zoom")
            capabilities.add(transition_capabilities[segment.transition_in.kind])
            capabilities.update(overlay_capabilities[item.kind] for item in segment.overlays)
    return frozenset(capabilities)


def validate_edit_plan_context(
    plan: EditPlan,
    *,
    selected_clips: Sequence[ShortCandidate],
    known_region_ids: Iterable[str],
    known_track_ids: Iterable[str],
    known_evidence_ids_by_clip: dict[int, Iterable[str]],
    max_segments_per_clip: int,
    max_overlays_per_clip: int,
    max_assets_per_clip: int,
) -> EditPlan:
    if len(plan.clips) != len(selected_clips):
        raise EditPlanError(
            "EDIT_PLAN_CLIP_MISMATCH",
            "the edit plan must contain exactly one entry per selected clip",
        )
    selected = {index: clip for index, clip in enumerate(selected_clips, start=1)}
    regions = set(known_region_ids)
    tracks = set(known_track_ids)
    for clip in plan.clips:
        expected = selected.get(clip.clip_index)
        if expected is None or (
            clip.source_window.start_ms != expected.start_ms
            or clip.source_window.end_ms != expected.end_ms
        ):
            raise EditPlanError(
                "EDIT_PLAN_CLIP_BOUNDS_INVALID",
                f"clip {clip.clip_index} must preserve its selected source bounds",
            )
        if len(clip.segments) > max_segments_per_clip:
            raise EditPlanError(
                "EDIT_PLAN_SEGMENT_BUDGET_EXCEEDED",
                f"clip {clip.clip_index} exceeds the configured segment budget",
            )
        if sum(len(segment.overlays) for segment in clip.segments) > max_overlays_per_clip:
            raise EditPlanError(
                "EDIT_PLAN_OVERLAY_BUDGET_EXCEEDED",
                f"clip {clip.clip_index} exceeds the configured overlay budget",
            )
        if len(clip.asset_requests) > max_assets_per_clip:
            raise EditPlanError(
                "EDIT_PLAN_ASSET_BUDGET_EXCEEDED",
                f"clip {clip.clip_index} exceeds the configured asset budget",
            )
        evidence = {str(item) for item in known_evidence_ids_by_clip.get(clip.clip_index, ())}
        for segment in clip.segments:
            unknown = sorted(set(segment.evidence_ids) - evidence)
            if unknown:
                raise EditPlanError(
                    "EDIT_PLAN_EVIDENCE_UNKNOWN",
                    f"segment {segment.id} references unknown evidence: {', '.join(unknown)}",
                )
            target = segment.layout.focal_target
            if target is not None:
                if target.region_id and target.region_id not in regions:
                    raise EditPlanError(
                        "EDIT_PLAN_REGION_UNKNOWN",
                        f"segment {segment.id} references unknown region {target.region_id}",
                    )
                if target.region_id and target.region_id not in evidence:
                    raise EditPlanError(
                        "EDIT_PLAN_REGION_OUTSIDE_CLIP",
                        f"segment {segment.id} references a region outside its selected clip",
                    )
                if target.track_id and target.track_id not in tracks:
                    raise EditPlanError(
                        "EDIT_PLAN_TRACK_UNKNOWN",
                        f"segment {segment.id} references unknown track {target.track_id}",
                    )
                if target.track_id and target.track_id not in evidence:
                    raise EditPlanError(
                        "EDIT_PLAN_TRACK_OUTSIDE_CLIP",
                        f"segment {segment.id} references a track outside its selected clip",
                    )

    required = required_capabilities(plan)
    undeclared = sorted(required - set(plan.requested_capabilities))
    if undeclared:
        raise EditPlanError(
            "EDIT_PLAN_CAPABILITY_UNDECLARED",
            f"plan operations require undeclared capabilities: {', '.join(undeclared)}",
        )
    return plan


def _clip_context(
    clip: ShortCandidate,
    *,
    clip_index: int,
    transcript_segments: Sequence[dict[str, Any]],
    scene_report: SceneBoundaryReport,
    visual_understanding: VisualUnderstanding,
    shorts_plan_artifact: dict[str, Any],
) -> dict[str, Any]:
    transcript = []
    for index, segment in enumerate(transcript_segments, start=1):
        start_ms = int(segment.get("start") or 0)
        end_ms = int(segment.get("end") or 0)
        if end_ms > clip.start_ms and start_ms < clip.end_ms:
            transcript.append({
                "id": f"transcript-{index:04d}",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": _safe_text(segment.get("text"), limit=1000),
            })
    frames = [
        frame
        for frame in (visual_understanding.frame_manifest.get("frames") or [])
        if clip.start_ms <= int(frame.get("timestamp_ms") or -1) < clip.end_ms
    ]
    frame_ids = {str(frame.get("id")) for frame in frames}
    clip_artifact = next((
        item
        for item in shorts_plan_artifact.get("clips") or []
        if int(item.get("clip_index") or 0) == clip_index
    ), None)
    if clip_artifact is None:
        raise EditPlanError(
            "SHORTS_PLAN_ARTIFACT_INVALID",
            f"shorts plan evidence is missing clip {clip_index}",
        )
    evidence_ids = {str(item) for item in clip_artifact.get("evidence_ids") or []}
    return {
        "clip_index": clip_index,
        "title": clip.title,
        "hook": clip.hook,
        "selection_reason": clip.reason,
        "source_window": {"start_ms": clip.start_ms, "end_ms": clip.end_ms},
        "output_name": f"short-{clip_index:02d}.mp4",
        "evidence_ids": sorted(evidence_ids),
        "transcript": [item for item in transcript if item["id"] in evidence_ids],
        "scenes": [
            scene.to_dict()
            for scene in scene_report.scenes
            if scene.id in evidence_ids
        ],
        "frames": [frame for frame in frames if str(frame.get("id")) in evidence_ids],
        "regions": [
            region.model_dump(mode="json")
            for region in visual_understanding.regions
            if region.frame_id in frame_ids and region.id in evidence_ids
        ],
        "tracks": [
            track.model_dump(mode="json")
            for track in visual_understanding.tracks
            if track.id in evidence_ids
        ],
    }


class AgenticEditPlanner:
    def __init__(self, client: NineRouterClient) -> None:
        self.client = client

    async def plan(
        self,
        *,
        editing_prompt: str,
        shorts_plan: ShortsPlan,
        shorts_plan_artifact: dict[str, Any],
        transcript_segments: Sequence[dict[str, Any]],
        scene_report: SceneBoundaryReport,
        visual_understanding: VisualUnderstanding,
        source_duration_ms: int,
        asset_policy: AssetPolicy,
        max_segments_per_clip: int,
        max_overlays_per_clip: int,
        max_assets_per_clip: int,
        renderer_capabilities: Iterable[str] = SUPPORTED_CAPABILITIES,
    ) -> EditPlan:
        available_capabilities = frozenset(str(value) for value in renderer_capabilities)
        if not available_capabilities or not available_capabilities <= SUPPORTED_CAPABILITIES:
            raise EditPlanError(
                "EDIT_PLAN_CAPABILITY_CONFIG_INVALID",
                "renderer capabilities must be a non-empty supported subset",
            )
        clip_contexts = [
            _clip_context(
                clip,
                clip_index=index,
                transcript_segments=transcript_segments,
                scene_report=scene_report,
                visual_understanding=visual_understanding,
                shorts_plan_artifact=shorts_plan_artifact,
            )
            for index, clip in enumerate(shorts_plan.clips, start=1)
        ]
        user_payload = {
            "editing_prompt": _safe_text(editing_prompt, limit=12_000),
            "source_duration_ms": source_duration_ms,
            "asset_policy": asset_policy,
            "renderer_capabilities": sorted(available_capabilities),
            "budgets": {
                "max_segments_per_clip": max_segments_per_clip,
                "max_overlays_per_clip": max_overlays_per_clip,
                "max_assets_per_clip": max_assets_per_clip,
            },
            "rules": [
                "Preserve every selected clip source window exactly.",
                "Cover each clip timeline contiguously from zero to its full selected duration.",
                "Use evidence IDs and semantic targets only from the supplied clip context.",
                "Use source evidence when it satisfies the visual intent.",
                "Request an asset only for a specific unresolved visual gap and only when policy is auto.",
                "Never return FFmpeg expressions, commands, paths, or unsupported operations.",
            ],
            "clips": clip_contexts,
        }
        response = await self.client.complete_json(
            system_prompt=EDIT_PLAN_SYSTEM_PROMPT,
            user_prompt=json.dumps(user_payload, ensure_ascii=False),
        )
        payload = dict(response)
        payload.update({
            "version": EDIT_PLAN_VERSION,
            "planner_version": AGENTIC_PLANNER_VERSION,
            "prompt_version": EDIT_PLAN_PROMPT_VERSION,
            "source_duration_ms": source_duration_ms,
        })
        plan = validate_edit_plan(payload, source_duration_ms=source_duration_ms)
        unavailable = sorted(set(plan.requested_capabilities) - available_capabilities)
        if unavailable:
            raise EditPlanError(
                "EDIT_PLAN_CAPABILITY_UNAVAILABLE",
                f"planner requested unavailable capabilities: {', '.join(unavailable)}",
            )
        known_evidence_ids_by_clip = {
            int(clip.get("clip_index") or 0): {
                str(evidence_id)
                for evidence_id in clip.get("evidence_ids") or []
            }
            for clip in shorts_plan_artifact.get("clips") or []
        }
        return validate_edit_plan_context(
            plan,
            selected_clips=shorts_plan.clips,
            known_region_ids=(region.id for region in visual_understanding.regions),
            known_track_ids=(track.id for track in visual_understanding.tracks),
            known_evidence_ids_by_clip=known_evidence_ids_by_clip,
            max_segments_per_clip=max_segments_per_clip,
            max_overlays_per_clip=max_overlays_per_clip,
            max_assets_per_clip=max_assets_per_clip,
        )


def build_shadow_edit_plan(
    clips: Sequence[ShortCandidate],
    *,
    source_duration_ms: int,
) -> EditPlan:
    planned: list[ClipEditPlan] = []
    for index, clip in enumerate(clips, start=1):
        timeline = TimeWindow(start_ms=0, end_ms=clip.duration_ms)
        planned.append(ClipEditPlan(
            clip_index=index,
            title=clip.title,
            source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
            output_name=f"short-{index:02d}.mp4",
            segments=(EditSegment(
                id=f"clip-{index:02d}-segment-01",
                source_window=TimeWindow(start_ms=clip.start_ms, end_ms=clip.end_ms),
                timeline_window=timeline,
                layout=LayoutSpec(mode="crop", fallback="fit"),
                reason="Characterize the legacy center-crop render in shadow mode.",
            ),),
        ))
    return EditPlan(
        planner_version=SHADOW_PLANNER_VERSION,
        source_duration_ms=source_duration_ms,
        requested_capabilities=("crop", "hard_cut", "subtitles"),
        clips=tuple(planned),
    )


def resolve_agentic_server_mode(config: Any) -> AgenticServerMode:
    value = (
        os.getenv("OPENSTORYLINE_AGENTIC_EDITING_MODE")
        or getattr(config, "mode", "off")
    ).strip().lower()
    if value not in {"off", "shadow", "render"}:
        raise EditPlanError(
            "AGENTIC_EDITING_CONFIG_INVALID",
            "OPENSTORYLINE_AGENTIC_EDITING_MODE must be off, shadow, or render",
        )
    return value  # type: ignore[return-value]


def validate_job_controls(edit_mode: str, asset_policy: str) -> tuple[EditMode, AssetPolicy]:
    normalized_edit = str(edit_mode or "legacy").strip().lower()
    normalized_assets = str(asset_policy or "auto").strip().lower()
    if normalized_edit not in {"legacy", "agentic"}:
        raise EditPlanError("EDIT_MODE_INVALID", "edit_mode must be legacy or agentic")
    if normalized_assets not in {"off", "auto"}:
        raise EditPlanError("ASSET_POLICY_INVALID", "asset_policy must be off or auto")
    return normalized_edit, normalized_assets  # type: ignore[return-value]


@dataclass(frozen=True)
class AgenticArtifactNames:
    scene_boundaries: str = "scene_boundaries.json"
    visual_understanding: str = "visual_understanding.json"
    shorts_plan: str = "shorts_plan.json"
    edit_plan: str = "edit_plan.json"
    preflight: str = "edit_preflight.json"
    render_execution: str = "render_execution.json"
