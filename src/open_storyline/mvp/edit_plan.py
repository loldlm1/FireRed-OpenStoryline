from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence
import os
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from open_storyline.mvp.shorts import ShortCandidate


EDIT_PLAN_VERSION = "edit_plan.v1"
SHADOW_PLANNER_VERSION = "legacy-shadow.v1"

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
LayoutMode = Literal["crop", "fit", "letterbox", "pip", "split", "source"]
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
    semantic_role: str = Field(default="", max_length=80)

    @field_validator("region_id", "track_id", "semantic_role")
    @classmethod
    def clean_identifier(cls, value: str) -> str:
        return _safe_text(value, limit=80)

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
        return _safe_text(value, limit=limit)

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
        return _safe_text(value, limit=80 if info.field_name == "id" else 500)

    @field_validator("evidence_ids")
    @classmethod
    def clean_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_safe_text(value, limit=80) for value in values)
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
            if segment.timeline_window.start_ms < last_timeline_end:
                raise ValueError("primary timeline segments must not overlap")
            last_timeline_end = segment.timeline_window.end_ms
        if self.segments[0].timeline_window.start_ms != 0:
            raise ValueError("clip timeline must start at zero")
        if last_timeline_end != self.source_window.duration_ms:
            raise ValueError("clip timeline must cover the complete selected duration")
        for asset in self.asset_requests:
            if asset.timeline_window.end_ms > self.source_window.duration_ms:
                raise ValueError("asset timing must stay inside the clip timeline")
        return self


class EditPlan(PlanModel):
    version: Literal[EDIT_PLAN_VERSION] = EDIT_PLAN_VERSION
    planner_version: str = Field(min_length=1, max_length=80)
    source_duration_ms: int = Field(gt=0)
    requested_capabilities: tuple[str, ...] = Field(default=(), max_length=32)
    clips: tuple[ClipEditPlan, ...] = Field(min_length=1, max_length=50)

    @field_validator("planner_version")
    @classmethod
    def clean_planner_version(cls, value: str) -> str:
        return _safe_text(value, limit=80)

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
        requested_capabilities=("crop", "subtitles"),
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
    shorts_plan: str = "shorts_plan.json"
    edit_plan: str = "edit_plan.json"
    preflight: str = "edit_preflight.json"
