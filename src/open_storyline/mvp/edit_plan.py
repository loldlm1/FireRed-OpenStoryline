from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence
import json
import os
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from open_storyline.mvp.creative_intent import (
    CreativeIntent,
    CreativeIntentDecision,
    CreativeOperationIntent,
    creative_intent_conformance_evidence,
    validate_creative_intent_conformance,
)
from open_storyline.mvp.ninerouter import NineRouterClient
from open_storyline.mvp.prompts import EDIT_PLAN_PROMPT_VERSION, EDIT_PLAN_SYSTEM_PROMPT
from open_storyline.mvp.scene_boundaries import SceneBoundaryReport
from open_storyline.mvp.shorts import ShortCandidate, ShortsPlan
from open_storyline.mvp.visual_understanding import VisualUnderstanding
from open_storyline.mvp.structured_outputs import (
    EDIT_PLAN_REPAIR_SCHEMA,
    EDIT_PLAN_SCHEMA,
)


EDIT_PLAN_VERSION = "edit_plan.v2"
SHADOW_PLANNER_VERSION = "legacy-shadow.v1"
AGENTIC_PLANNER_VERSION = "agentic-editor.v2"

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

AssetPolicy = Literal["off", "auto", "required"]
AgenticServerMode = Literal["off", "shadow", "render"]
LayoutMode = Literal["crop", "fit", "letterbox", "source"]
TransitionKind = Literal["cut", "fade", "xfade"]
OverlayKind = Literal["text", "image", "source", "pip"]
AssetKind = Literal["generated_image", "stock_image", "stock_video"]
AssetProvider = Literal["9router", "pexels"]
StockAssetKind = Literal["image", "video"]

_VALIDATION_CONSTRAINT_CODES = {
    "segment IDs must be unique": "segment_ids_not_unique",
    "asset request IDs must be unique": "asset_ids_not_unique",
    "intent decision IDs must be unique": "intent_ids_not_unique",
    "segment source timing must stay inside the clip": "segment_source_outside_clip",
    "the first segment must start at zero with a hard cut": "first_segment_invalid",
    "segment timing must match its declared transition overlap": "segment_timeline_gap",
    "clip timeline must cover the complete selected duration": "clip_timeline_incomplete",
    "overlay source timing must stay inside the selected clip": "overlay_source_outside_clip",
    "asset timing must stay inside the clip timeline": "asset_timeline_outside_clip",
    "overlay IDs must be unique inside a clip": "overlay_ids_not_unique",
    "image overlays and asset requests must reference the same asset IDs": "asset_overlay_ids_mismatch",
    "image overlay timing must stay inside its asset request window": "asset_overlay_timing_mismatch",
}


class EditPlanError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.evidence = evidence or {}
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.evidence:
            result["evidence"] = self.evidence
        return result


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
        return "" if value == "" else _safe_identifier(value)

    @model_validator(mode="after")
    def require_reference(self) -> "FocalTarget":
        if not (self.region_id or self.track_id or self.semantic_role):
            raise ValueError("a focal target needs a region, track, or semantic role")
        return self


class LayoutSpec(PlanModel):
    mode: LayoutMode
    focal_target: FocalTarget | None = None
    fallback: Literal["crop", "fit", "letterbox", "source"] = "crop"
    allow_full_frame_fallback: bool = False
    safe_margin_ratio: float = Field(default=0.08, ge=0, le=0.35, allow_inf_nan=False)
    max_zoom: float = Field(default=1.0, ge=1.0, le=4.0, allow_inf_nan=False)


class TransitionSpec(PlanModel):
    kind: TransitionKind = "cut"
    duration_ms: int = Field(default=0, ge=0, le=1500)
    catalog_id: str = Field(default="", max_length=80)

    @field_validator("catalog_id")
    @classmethod
    def clean_catalog_id(cls, value: str) -> str:
        return "" if value == "" else _safe_identifier(value)

    @model_validator(mode="after")
    def validate_duration(self) -> "TransitionSpec":
        if self.kind == "cut" and self.duration_ms != 0:
            raise ValueError("cut transitions must have zero duration")
        if self.kind != "cut" and self.duration_ms <= 0:
            raise ValueError("fade transitions require a positive duration")
        return self


class CatalogSelection(PlanModel):
    style_profile_id: str = Field(default="", max_length=80)
    caption_treatment_id: str = Field(default="", max_length=80)
    color_treatment_id: str = Field(default="", max_length=80)
    recipe_ids: tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator(
        "style_profile_id",
        "caption_treatment_id",
        "color_treatment_id",
    )
    @classmethod
    def clean_catalog_id(cls, value: str) -> str:
        return "" if value == "" else _safe_identifier(value)

    @field_validator("recipe_ids")
    @classmethod
    def clean_recipe_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_safe_identifier(value) for value in values)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("catalog recipe IDs must be unique")
        return cleaned


class OverlaySpec(PlanModel):
    id: str = Field(min_length=1, max_length=80)
    kind: OverlayKind
    timeline_window: TimeWindow
    source_window: TimeWindow | None = None
    text: str = Field(default="", max_length=500)
    asset_id: str = Field(default="", max_length=80)
    opacity: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    width_ratio: float = Field(default=0.35, ge=0.08, le=1, allow_inf_nan=False)
    margin_ratio: float = Field(default=0.035, ge=0, le=0.2, allow_inf_nan=False)
    transition_ms: int = Field(default=0, ge=0, le=800)
    z_index: int = Field(default=10, ge=1, le=100)
    protect_subtitles: bool = True
    position: Literal["center", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"] = "center"

    @field_validator("id", "text", "asset_id")
    @classmethod
    def clean_text_fields(cls, value: str, info: Any) -> str:
        limit = 500 if info.field_name == "text" else 80
        if info.field_name == "text":
            return _safe_text(value, limit=limit)
        if info.field_name == "asset_id" and value == "":
            return ""
        return _safe_identifier(value)

    @model_validator(mode="after")
    def validate_payload(self) -> "OverlaySpec":
        if self.kind == "text" and not self.text:
            raise ValueError("text overlays require text")
        if self.kind == "image" and not self.asset_id:
            raise ValueError("image overlays require asset_id")
        if self.kind in {"source", "pip"}:
            if self.source_window is None:
                raise ValueError("source and PiP overlays require source_window")
            if self.source_window.duration_ms != self.timeline_window.duration_ms:
                raise ValueError("source overlay duration must match its timeline duration")
        elif self.source_window is not None:
            raise ValueError("only source and PiP overlays may declare source_window")
        return self


class AssetRequest(PlanModel):
    id: str = Field(min_length=1, max_length=80)
    kind: AssetKind
    provider: AssetProvider
    timeline_window: TimeWindow
    visual_gap: str = Field(min_length=1, max_length=500)
    purpose: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=500)
    prompt: str = Field(default="", max_length=7000)
    orientation: Literal["portrait", "landscape"] = "portrait"
    required: bool = True
    fallback: Literal["source", "fit", "omit"] = "source"

    @field_validator("id", "visual_gap", "purpose", "rationale", "prompt")
    @classmethod
    def clean_asset_text(cls, value: str, info: Any) -> str:
        limits = {
            "id": 80,
            "visual_gap": 500,
            "purpose": 240,
            "rationale": 500,
            "prompt": 7000,
        }
        if info.field_name == "id":
            return _safe_identifier(value)
        return _safe_text(value, limit=limits[info.field_name])

    @model_validator(mode="after")
    def validate_provider(self) -> "AssetRequest":
        if self.kind == "generated_image" and self.provider != "9router":
            raise ValueError("generated images must use 9router")
        if self.kind.startswith("stock_") and self.provider != "pexels":
            raise ValueError("stock assets must use pexels")
        if not self.prompt:
            raise ValueError("external assets require a generation prompt or search query")
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
    intent_decisions: tuple[CreativeIntentDecision, ...] = Field(default=(), max_length=32)
    catalog_selection: CatalogSelection = Field(default_factory=CatalogSelection)

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
        intent_ids = [decision.intent_id for decision in self.intent_decisions]
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("segment IDs must be unique")
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("asset request IDs must be unique")
        if len(set(intent_ids)) != len(intent_ids):
            raise ValueError("intent decision IDs must be unique")

        last_timeline_end = 0
        for index, segment in enumerate(self.segments):
            if (
                segment.source_window.start_ms < self.source_window.start_ms
                or segment.source_window.end_ms > self.source_window.end_ms
            ):
                raise ValueError("segment source timing must stay inside the clip")
            if index == 0:
                if segment.timeline_window.start_ms != 0 or segment.transition_in.kind != "cut":
                    raise ValueError("the first segment must start at zero with a hard cut")
            else:
                overlap = segment.transition_in.duration_ms if segment.transition_in.kind == "xfade" else 0
                if segment.timeline_window.start_ms != last_timeline_end - overlap:
                    raise ValueError("segment timing must match its declared transition overlap")
            last_timeline_end = segment.timeline_window.end_ms
        if last_timeline_end != self.source_window.duration_ms:
            raise ValueError("clip timeline must cover the complete selected duration")
        for segment in self.segments:
            for overlay in segment.overlays:
                if overlay.source_window is not None and (
                    overlay.source_window.start_ms < self.source_window.start_ms
                    or overlay.source_window.end_ms > self.source_window.end_ms
                ):
                    raise ValueError("overlay source timing must stay inside the selected clip")
        for asset in self.asset_requests:
            if asset.timeline_window.end_ms > self.source_window.duration_ms:
                raise ValueError("asset timing must stay inside the clip timeline")
        overlay_ids = [overlay.id for segment in self.segments for overlay in segment.overlays]
        if len(set(overlay_ids)) != len(overlay_ids):
            raise ValueError("overlay IDs must be unique inside a clip")
        image_overlays = [
            overlay
            for segment in self.segments
            for overlay in segment.overlays
            if overlay.kind == "image"
        ]
        request_by_id = {asset.id: asset for asset in self.asset_requests}
        used_asset_ids = {overlay.asset_id for overlay in image_overlays}
        if used_asset_ids != set(request_by_id):
            raise ValueError("image overlays and asset requests must reference the same asset IDs")
        for overlay in image_overlays:
            request = request_by_id[overlay.asset_id]
            if (
                overlay.timeline_window.start_ms < request.timeline_window.start_ms
                or overlay.timeline_window.end_ms > request.timeline_window.end_ms
            ):
                raise ValueError("image overlay timing must stay inside its asset request window")
        return self


class EditPlan(PlanModel):
    version: Literal[EDIT_PLAN_VERSION] = EDIT_PLAN_VERSION
    planner_version: str = Field(min_length=1, max_length=80)
    prompt_version: str = Field(default=EDIT_PLAN_PROMPT_VERSION, min_length=1, max_length=80)
    source_duration_ms: int = Field(gt=0)
    requested_capabilities: tuple[str, ...] = Field(default=(), max_length=32)
    clips: tuple[ClipEditPlan, ...] = Field(min_length=1, max_length=50)
    degraded: bool = False
    degradation_reason: str = Field(default="", max_length=240)
    catalog_version: str = Field(default="", max_length=80)
    catalog_manifest_sha256: str = Field(default="", max_length=64)

    @field_validator("planner_version", "prompt_version", "catalog_version")
    @classmethod
    def clean_planner_version(cls, value: str) -> str:
        return "" if value == "" else _safe_identifier(value)

    @field_validator("catalog_manifest_sha256")
    @classmethod
    def validate_catalog_hash(cls, value: str) -> str:
        if value and not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("catalog manifest hash is invalid")
        return value

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
        asset_ids = [asset.id for clip in self.clips for asset in clip.asset_requests]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("asset request IDs must be unique across the edit plan")
        for clip in self.clips:
            if clip.source_window.end_ms > self.source_duration_ms:
                raise ValueError("clip timing exceeds source duration")
        if self.degraded != bool(self.degradation_reason):
            raise ValueError("degraded plans require exactly one degradation reason")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _edit_plan_field_contract() -> dict[str, list[str]]:
    models = (
        TimeWindow,
        FocalTarget,
        LayoutSpec,
        TransitionSpec,
        CatalogSelection,
        OverlaySpec,
        AssetRequest,
        CreativeIntentDecision,
        EditSegment,
        ClipEditPlan,
        EditPlan,
    )
    return {
        model.__name__: list(model.model_fields)
        for model in models
    }


def _catalog_candidate_map(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not snapshot:
        return {}
    entries = snapshot.get("entries")
    if not isinstance(entries, list) or len(entries) > 32:
        raise EditPlanError("EDIT_PLAN_CATALOG_INVALID", "catalog candidates are invalid")
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise EditPlanError("EDIT_PLAN_CATALOG_INVALID", "catalog candidate is invalid")
        entry_id = str(entry.get("id") or "")
        kind = str(entry.get("kind") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,79}", entry_id):
            raise EditPlanError("EDIT_PLAN_CATALOG_INVALID", "catalog candidate ID is invalid")
        if entry_id in result or kind not in {
            "style_profile", "caption_treatment", "color_treatment", "transition", "recipe",
        }:
            raise EditPlanError("EDIT_PLAN_CATALOG_INVALID", "catalog candidate kind is invalid")
        result[entry_id] = entry
    return result


def _catalog_template(
    snapshot: dict[str, Any] | None,
    *,
    prefer_restrained_transitions: bool = False,
) -> dict[str, Any]:
    candidates = _catalog_candidate_map(snapshot)
    if not candidates:
        return {
            "selection": {
                "style_profile_id": "",
                "caption_treatment_id": "",
                "color_treatment_id": "",
                "recipe_ids": [],
            },
            "cut_transition_id": "",
            "restrained_transition_id": "",
            "restrained_transition_kind": "fade",
        }
    styles = [
        entry for entry in candidates.values() if entry["kind"] == "style_profile"
    ]

    def style_ids(entry: dict[str, Any]) -> list[str]:
        return [
            str(value)
            for value in (entry.get("config") or {}).get("catalog_ids") or []
            if str(value) in candidates
        ]

    def restrained_transition(entry: dict[str, Any]) -> str:
        return next(
            (
                entry_id
                for entry_id in style_ids(entry)
                if candidates[entry_id]["kind"] == "transition"
                and (candidates[entry_id].get("config") or {}).get("operation")
                == "fade"
                and entry_id.startswith("transition.fade-")
            ),
            "",
        )

    style = (
        next((entry for entry in styles if restrained_transition(entry)), styles[0])
        if prefer_restrained_transitions
        else styles[0]
    )
    selected_style_ids = style_ids(style)

    def select(kind: str) -> str:
        selected = next(
            (
                entry_id
                for entry_id in selected_style_ids
                if candidates[entry_id]["kind"] == kind
            ),
            "",
        )
        if selected:
            return selected
        return next(
            entry_id
            for entry_id, entry in candidates.items()
            if entry["kind"] == kind
        )

    cut_id = next(
        entry_id
        for entry_id, entry in candidates.items()
        if entry["kind"] == "transition"
        and (entry.get("config") or {}).get("operation") == "hard_cut"
    )
    restrained_id = restrained_transition(style)
    return {
        "selection": {
            "style_profile_id": str(style["id"]),
            "caption_treatment_id": select("caption_treatment"),
            "color_treatment_id": select("color_treatment"),
            "recipe_ids": [],
        },
        "cut_transition_id": cut_id,
        "restrained_transition_id": restrained_id,
        "restrained_transition_kind": "fade",
    }


def _opening_title_text(editing_prompt: str, fallback: str) -> str:
    prompt = str(editing_prompt or "")
    patterns = (
        r"(?:with\s+(?:the\s+)?(?:text|words)|that\s+says?|saying)\s*[:=-]?\s*['\"]([^'\"]{1,120})['\"]",
        r"(?:con\s+el\s+texto|que\s+diga|titulado)\s*[:=-]?\s*['\"]([^'\"]{1,120})['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if match is not None:
            return _safe_text(match.group(1), limit=120)
    return _safe_text(fallback, limit=120)


def _valid_clip_plan_template(
    clip_context: dict[str, Any],
    *,
    catalog_snapshot: dict[str, Any] | None = None,
    creative_intent: CreativeIntent | None = None,
    opening_title_text: str = "",
) -> dict[str, Any]:
    source_window = dict(clip_context["source_window"])
    duration_ms = int(source_window["end_ms"]) - int(source_window["start_ms"])
    focal_target = None
    evidence_ids: list[str] = []
    regions = clip_context.get("regions") or []
    tracks = clip_context.get("tracks") or []
    valid_regions = [item for item in regions if isinstance(item, dict) and item.get("id")]
    valid_tracks = [item for item in tracks if isinstance(item, dict) and item.get("id")]
    if valid_tracks:
        track = max(valid_tracks, key=lambda item: float(item.get("confidence") or 0))
        focal_target = {
            "region_id": "",
            "track_id": str(track["id"]),
            "semantic_role": str(track.get("role") or ""),
        }
        evidence_ids.append(str(track["id"]))
    elif valid_regions:
        region = max(
            valid_regions,
            key=lambda item: (
                float(item.get("salience") or 0),
                float(item.get("confidence") or 0),
            ),
        )
        focal_target = {
            "region_id": str(region["id"]),
            "track_id": "",
            "semantic_role": str(region.get("role") or ""),
        }
        evidence_ids.append(str(region["id"]))
    layout = {
        "mode": "crop" if focal_target else "fit",
        "focal_target": focal_target,
        "fallback": "crop",
        "allow_full_frame_fallback": False,
        "safe_margin_ratio": 0.08,
        "max_zoom": 1.0,
    }
    operation_intents = tuple(
        item
        for item in (creative_intent.operation_intents if creative_intent else ())
        if item.requirement == "required"
    )
    reframe_intent = next(
        (item for item in operation_intents if item.kind == "reframe_sequence"),
        None,
    )
    transition_intent = next(
        (item for item in operation_intents if item.kind == "restrained_transitions"),
        None,
    )
    title_intent = next(
        (item for item in operation_intents if item.kind == "opening_title"),
        None,
    )
    catalog_defaults = _catalog_template(
        catalog_snapshot,
        prefer_restrained_transitions=transition_intent is not None,
    )
    segment_count = 1
    if reframe_intent is not None and focal_target is not None:
        segment_count = max(segment_count, int(reframe_intent.count_min))
    if transition_intent is not None:
        segment_count = max(segment_count, int(transition_intent.count_min) + 1)
    segment_count = min(segment_count, max(1, duration_ms))
    title_duration_ms = 0
    if title_intent is not None and duration_ms >= title_intent.duration_min_ms:
        title_duration_ms = min(
            int(title_intent.duration_max_ms),
            max(int(title_intent.duration_min_ms), min(2200, duration_ms)),
        )
    minimum_first_duration = title_duration_ms or 1
    if duration_ms < minimum_first_duration + segment_count - 1:
        segment_count = 1

    lengths: list[int] = []
    if segment_count == 1:
        lengths = [duration_ms]
    else:
        first_length = max(duration_ms // segment_count, minimum_first_duration)
        remaining = duration_ms - first_length
        base, extra = divmod(remaining, segment_count - 1)
        lengths = [first_length] + [
            base + (1 if index < extra else 0)
            for index in range(segment_count - 1)
        ]

    segments: list[dict[str, Any]] = []
    source_cursor = int(source_window["start_ms"])
    timeline_cursor = 0
    for index, length in enumerate(lengths, start=1):
        source_end = source_cursor + length
        timeline_end = timeline_cursor + length
        segment_layout = deepcopy(layout)
        if reframe_intent is not None and segment_layout["mode"] == "crop":
            segment_layout["max_zoom"] = (1.04, 1.08, 1.06, 1.1)[
                (index - 1) % 4
            ]
        segment_id = f"clip-{int(clip_context['clip_index']):02d}-segment-{index:02d}"
        overlays: list[dict[str, Any]] = []
        if index == 1 and title_intent is not None and title_duration_ms:
            overlays.append({
                "id": f"clip-{int(clip_context['clip_index']):02d}-opening-title",
                "kind": "text",
                "timeline_window": {
                    "start_ms": 0,
                    "end_ms": title_duration_ms,
                },
                "text": _safe_text(
                    opening_title_text or clip_context.get("title") or "Opening",
                    limit=120,
                ),
                "opacity": 1.0,
                "width_ratio": 0.82,
                "margin_ratio": 0.06,
                "transition_ms": min(180, title_duration_ms // 3),
                "z_index": 20,
                "protect_subtitles": True,
                "position": "top",
            })
        transition_enabled = (
            index > 1
            and transition_intent is not None
            and index - 1 <= int(transition_intent.count_max)
            and (
                not _catalog_candidate_map(catalog_snapshot)
                or bool(catalog_defaults["restrained_transition_id"])
            )
        )
        segments.append({
            "id": segment_id,
            "source_window": {
                "start_ms": source_cursor,
                "end_ms": source_end,
            },
            "timeline_window": {
                "start_ms": timeline_cursor,
                "end_ms": timeline_end,
            },
            "layout": segment_layout,
            "transition_in": {
                "kind": (
                    catalog_defaults["restrained_transition_kind"]
                    if transition_enabled
                    else "cut"
                ),
                "duration_ms": (
                    min(
                        int(transition_intent.duration_max_ms),
                        max(int(transition_intent.duration_min_ms), 220),
                    )
                    if transition_enabled
                    else 0
                ),
                "catalog_id": (
                    catalog_defaults["restrained_transition_id"]
                    if transition_enabled
                    else catalog_defaults["cut_transition_id"]
                ),
            },
            "overlays": overlays,
            "reason": (
                "Apply the bounded prompt-required creative operation after the "
                "remote planning attempt."
            ),
            "evidence_ids": evidence_ids,
        })
        source_cursor = source_end
        timeline_cursor = timeline_end

    segment_ids = [str(segment["id"]) for segment in segments]
    crop_ids = [
        str(segment["id"])
        for segment in segments
        if segment["layout"]["mode"] == "crop"
    ]
    transition_ids = [
        str(segment["id"])
        for segment in segments
        if segment["transition_in"]["kind"] in {"fade", "xfade"}
    ]
    title_ids = [
        str(overlay["id"])
        for segment in segments
        for overlay in segment["overlays"]
        if overlay["kind"] == "text"
    ]
    intent_decisions = []
    for intent in operation_intents:
        operation_ids = (
            title_ids
            if intent.kind == "opening_title"
            else crop_ids
            if intent.kind in {"portrait_reframe", "reframe_sequence"}
            else transition_ids
            if intent.kind == "restrained_transitions"
            else segment_ids
            if intent.kind == "footer_captions"
            else []
        )
        if intent.count_min and not (
            int(intent.count_min) <= len(operation_ids) <= int(intent.count_max)
        ):
            continue
        if operation_ids:
            intent_decisions.append({
                "intent_id": intent.id,
                "decision": "execute",
                "asset_ids": [],
                "operation_ids": operation_ids,
                "omission_reason": "",
            })
    requested_capabilities = {
        "crop" if focal_target else "fit",
        "hard_cut",
        "subtitles",
    }
    if any(segment["layout"]["max_zoom"] > 1 for segment in segments):
        requested_capabilities.add("focus_zoom")
    if transition_ids:
        requested_capabilities.add("fade")
    if title_ids:
        requested_capabilities.add("text_emphasis")
    return {
        "requested_capabilities": sorted(requested_capabilities),
        "clips": [{
            "clip_index": int(clip_context["clip_index"]),
            "title": str(clip_context.get("title") or ""),
            "source_window": source_window,
            "output_name": str(clip_context["output_name"]),
            "segments": segments,
            "asset_requests": [],
            "intent_decisions": intent_decisions,
            "catalog_selection": catalog_defaults["selection"],
        }],
    }


def _window_bounds(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        start_ms = int(value["start_ms"])
        end_ms = int(value["end_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    if start_ms < 0 or end_ms <= start_ms:
        return None
    return start_ms, end_ms


def _derive_missing_asset_overlays(clip: dict[str, Any]) -> None:
    asset_requests = clip.get("asset_requests")
    segments = clip.get("segments")
    if not isinstance(asset_requests, list) or not isinstance(segments, list):
        return

    existing_ids = {
        str(overlay.get("id"))
        for segment in segments
        if isinstance(segment, dict)
        for overlay in segment.get("overlays") or []
        if isinstance(overlay, dict) and overlay.get("id")
    }
    used_asset_ids = {
        str(overlay.get("asset_id"))
        for segment in segments
        if isinstance(segment, dict)
        for overlay in segment.get("overlays") or []
        if isinstance(overlay, dict)
        and overlay.get("kind") == "image"
        and overlay.get("asset_id")
    }
    derived_count = 0
    for asset in asset_requests:
        if not isinstance(asset, dict) or asset.get("kind") not in {
            "generated_image",
            "stock_image",
            "stock_video",
        }:
            continue
        asset_id = asset.get("id")
        asset_window = _window_bounds(asset.get("timeline_window"))
        if not isinstance(asset_id, str) or not asset_id or asset_window is None:
            continue
        if asset_id in used_asset_ids:
            continue
        containing_segments = [
            segment
            for segment in segments
            if isinstance(segment, dict)
            and isinstance(segment.get("overlays"), list)
            and (segment_window := _window_bounds(segment.get("timeline_window")))
            and segment_window[0] <= asset_window[0]
            and asset_window[1] <= segment_window[1]
        ]
        if len(containing_segments) != 1:
            continue
        derived_count += 1
        overlay_id = f"asset-overlay-{derived_count:02d}"
        while overlay_id in existing_ids:
            derived_count += 1
            overlay_id = f"asset-overlay-{derived_count:02d}"
        containing_segments[0]["overlays"].append({
            "id": overlay_id,
            "kind": "image",
            "timeline_window": dict(asset["timeline_window"]),
            "asset_id": asset_id,
            "position": "top_left" if derived_count % 2 else "top_right",
        })
        intent_decisions = clip.get("intent_decisions")
        if isinstance(intent_decisions, list):
            for decision in intent_decisions:
                if not isinstance(decision, dict) or decision.get("decision") != "execute":
                    continue
                asset_ids = decision.get("asset_ids") or []
                if not isinstance(asset_ids, (list, tuple)) or asset_id not in asset_ids:
                    continue
                operation_ids = decision.get("operation_ids")
                if operation_ids is None:
                    operation_ids = []
                    decision["operation_ids"] = operation_ids
                if isinstance(operation_ids, list) and overlay_id not in operation_ids:
                    operation_ids.append(overlay_id)
        existing_ids.add(overlay_id)
        used_asset_ids.add(asset_id)


def _operation_intent_candidates(
    segments: list[Any],
    intent: CreativeOperationIntent,
) -> set[str]:
    segment_ids = {
        str(segment.get("id"))
        for segment in segments
        if isinstance(segment, dict) and segment.get("id")
    }
    text_overlays = {
        str(overlay.get("id")): overlay
        for segment in segments
        if isinstance(segment, dict)
        for overlay in segment.get("overlays") or []
        if isinstance(overlay, dict)
        and overlay.get("id")
        and overlay.get("kind") == "text"
    }
    if intent.kind == "footer_captions":
        return segment_ids
    if intent.kind in {"portrait_reframe", "reframe_sequence"}:
        return {
            str(segment.get("id"))
            for segment in segments
            if isinstance(segment, dict)
            and isinstance(segment.get("layout"), dict)
            and segment["layout"].get("mode") == "crop"
            and segment.get("id")
        }
    if intent.kind == "opening_title":
        return {
            overlay_id
            for overlay_id, overlay in text_overlays.items()
            if (
                (window := _window_bounds(overlay.get("timeline_window")))
                and window[0] <= intent.start_max_ms
                and intent.duration_min_ms
                <= window[1] - window[0]
                <= intent.duration_max_ms
            )
        }
    if intent.kind == "restrained_transitions":
        return {
            str(segment.get("id"))
            for index, segment in enumerate(segments)
            if index > 0
            and isinstance(segment, dict)
            and segment.get("id")
            and isinstance(segment.get("transition_in"), dict)
            and segment["transition_in"].get("kind") in {"fade", "xfade"}
            and isinstance(segment["transition_in"].get("duration_ms"), int)
            and intent.duration_min_ms
            <= segment["transition_in"]["duration_ms"]
            <= intent.duration_max_ms
        }
    return set()


def _operation_count_valid(intent: CreativeOperationIntent, count: int) -> bool:
    if count <= 0:
        return False
    if intent.count_max == 0:
        return True
    return intent.count_min <= count <= intent.count_max


def _derive_missing_operation_intent_decisions(
    clip: dict[str, Any],
    *,
    creative_intent: CreativeIntent | None,
) -> None:
    if creative_intent is None:
        return
    segments = clip.get("segments")
    decisions = clip.get("intent_decisions")
    if not isinstance(segments, list) or not isinstance(decisions, list):
        return
    decided = {
        str(decision.get("intent_id"))
        for decision in decisions
        if isinstance(decision, dict) and decision.get("intent_id")
    }
    for intent in creative_intent.operation_intents:
        if intent.requirement != "required" or intent.id in decided:
            continue
        operation_ids = _operation_intent_candidates(segments, intent)
        if _operation_count_valid(intent, len(operation_ids)):
            decisions.append({
                "intent_id": intent.id,
                "decision": "execute",
                "asset_ids": [],
                "operation_ids": sorted(operation_ids),
                "omission_reason": "",
            })


def _derive_operation_intent_mappings(
    clip: dict[str, Any],
    *,
    creative_intent: CreativeIntent | None,
) -> None:
    if creative_intent is None:
        return
    segments = clip.get("segments")
    decisions = clip.get("intent_decisions")
    if not isinstance(segments, list) or not isinstance(decisions, list):
        return
    operation_intents = {
        item.id: item for item in creative_intent.operation_intents
    }
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("decision") != "execute":
            continue
        intent = operation_intents.get(decision.get("intent_id"))
        if intent is None:
            continue
        current_ids = decision.get("operation_ids")
        current_ids = current_ids if isinstance(current_ids, (list, tuple)) else []
        executable_ids = _operation_intent_candidates(segments, intent)
        count_valid = _operation_count_valid(intent, len(current_ids))
        current_mapping_valid = (
            bool(current_ids)
            and all(isinstance(item, str) for item in current_ids)
            and set(current_ids) <= executable_ids
            and count_valid
        )
        executable_count_valid = _operation_count_valid(
            intent,
            len(executable_ids),
        )
        if executable_ids and executable_count_valid and not current_mapping_valid:
            decision["operation_ids"] = sorted(executable_ids)


def _normalize_edit_plan_response(
    value: Any,
    *,
    creative_intent: CreativeIntent | None = None,
) -> Any:
    if not isinstance(value, dict):
        return value
    normalized = deepcopy(value)
    if normalized.get("degradation_reason") is None:
        normalized["degradation_reason"] = ""
    clips = normalized.get("clips")
    if not isinstance(clips, list):
        return normalized
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        asset_requests = clip.get("asset_requests")
        external_asset_ids = {
            str(asset.get("id"))
            for asset in asset_requests or []
            if isinstance(asset, dict) and asset.get("id")
        }
        if isinstance(asset_requests, list):
            for asset in asset_requests:
                if not isinstance(asset, dict):
                    continue
                if "orientation" in asset:
                    asset["orientation"] = {
                        "vertical": "portrait",
                        "horizontal": "landscape",
                    }.get(asset.get("orientation"), asset.get("orientation"))
                if "fallback" in asset:
                    raw_fallback = asset.get("fallback")
                    fallback_token = (
                        re.sub(r"[\s-]+", "_", raw_fallback.strip().lower())
                        if isinstance(raw_fallback, str)
                        else raw_fallback
                    )
                    fallback = {
                        "source": "source",
                        "fit": "fit",
                        "omit": "omit",
                        "error": "fail",
                        "error_if_unavailable": "fail",
                        "fail": "fail",
                        "fail_if_unavailable": "fail",
                        "fail_job": "fail",
                        "hard_fail": "fail",
                        "keep_source_video": "source",
                        "none": "omit",
                        "raise_error": "fail",
                        "source_only": "source",
                        "source_video": "source",
                        "use_source": "source",
                        "use_source_video": "source",
                    }.get(fallback_token, raw_fallback)
                    if fallback == "fail" and asset.get("required", True) is True:
                        fallback = "omit"
                    asset["fallback"] = fallback
        intent_decisions = clip.get("intent_decisions")
        if isinstance(intent_decisions, list):
            for decision in intent_decisions:
                if isinstance(decision, dict) and decision.get("omission_reason") is None:
                    decision["omission_reason"] = ""
        catalog_selection = clip.get("catalog_selection")
        if isinstance(catalog_selection, dict):
            for field in (
                "style_profile_id",
                "caption_treatment_id",
                "color_treatment_id",
            ):
                if catalog_selection.get(field) is None:
                    catalog_selection[field] = ""
        segments = clip.get("segments")
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            if segment.get("overlays") is None:
                segment["overlays"] = []
            if not isinstance(segment.get("overlays"), list):
                continue
            transition = segment.get("transition_in")
            if isinstance(transition, dict) and transition.get("kind") == "hard_cut":
                transition["kind"] = "cut"
            if isinstance(transition, dict) and transition.get("catalog_id") is None:
                transition["catalog_id"] = ""
            layout = segment.get("layout")
            focal_target = layout.get("focal_target") if isinstance(layout, dict) else None
            if isinstance(focal_target, dict):
                for field in ("region_id", "track_id", "semantic_role"):
                    if focal_target.get(field) is None:
                        focal_target[field] = ""
            normalized_overlays = []
            for overlay in segment["overlays"]:
                if not isinstance(overlay, dict):
                    normalized_overlays.append(overlay)
                    continue
                if overlay.get("kind") in {"subtitle", "subtitles"}:
                    continue
                if (
                    overlay.get("kind") == "image_overlay"
                    and isinstance(overlay.get("asset_id"), str)
                    and overlay["asset_id"]
                ):
                    overlay["kind"] = "image"
                if (
                    overlay.get("kind") in {"source", "pip"}
                    and overlay.get("asset_id") in external_asset_ids
                ):
                    overlay["kind"] = "image"
                for field in ("text", "asset_id"):
                    if overlay.get(field) is None:
                        overlay[field] = ""
                if overlay.get("kind") in {"text", "image"}:
                    overlay.pop("source_window", None)
                if overlay.get("protect_subtitles", True):
                    if overlay.get("position") == "upper_right":
                        overlay["position"] = "top_right"
                    safe_position = {
                        "bottom": "top",
                        "bottom_left": "top_left",
                        "bottom_right": "top_right",
                    }.get(overlay.get("position"))
                    if safe_position:
                        overlay["position"] = safe_position
                if (
                    overlay.get("kind") in {"source", "pip"}
                    and overlay.get("source_window") is None
                ):
                    inferred = _infer_overlay_source_window(segment, overlay)
                    if inferred is not None:
                        overlay["source_window"] = inferred
                normalized_overlays.append(overlay)
            segment["overlays"] = normalized_overlays
        _derive_missing_asset_overlays(clip)
        _derive_missing_operation_intent_decisions(
            clip,
            creative_intent=creative_intent,
        )
        _derive_operation_intent_mappings(
            clip,
            creative_intent=creative_intent,
        )
    return normalized


def _infer_overlay_source_window(
    segment: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, int] | None:
    source_window = segment.get("source_window")
    timeline_window = segment.get("timeline_window")
    overlay_window = overlay.get("timeline_window")
    if not all(
        isinstance(item, dict)
        for item in (source_window, timeline_window, overlay_window)
    ):
        return None
    try:
        source_start = int(source_window["start_ms"])
        source_end = int(source_window["end_ms"])
        timeline_start = int(timeline_window["start_ms"])
        timeline_end = int(timeline_window["end_ms"])
        overlay_start = int(overlay_window["start_ms"])
        overlay_end = int(overlay_window["end_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        source_end - source_start != timeline_end - timeline_start
        or overlay_start < timeline_start
        or overlay_end > timeline_end
        or overlay_end <= overlay_start
    ):
        return None
    offset_ms = source_start - timeline_start
    return {
        "start_ms": overlay_start + offset_ms,
        "end_ms": overlay_end + offset_ms,
    }


def _safe_invalid_literal(location: list[Any], value: Any) -> str | None:
    if not isinstance(value, str) or not location:
        return None
    field = location[-1]
    safe_field = (
        field in {"orientation", "fallback"} and "asset_requests" in location
        or field in {"kind", "position"} and "overlays" in location
        or field == "kind" and "transition_in" in location
    )
    text = value.strip().lower()
    if not safe_field or not re.fullmatch(r"[a-z][a-z0-9_ -]{0,23}", text):
        return None
    return text


def _validation_error_evidence(exc: ValidationError) -> dict[str, Any]:
    errors = exc.errors(include_input=True, include_url=False)
    issues = []
    for error in errors[:12]:
        location = []
        for item in error.get("loc", ())[:12]:
            if isinstance(item, int):
                location.append(item)
            else:
                location.append(re.sub(r"[^A-Za-z0-9_.-]+", "_", str(item))[:80])
        cause_code = re.sub(
            r"[^a-z0-9_.-]+",
            "_",
            str(error.get("type") or "validation_error").lower(),
        )[:80]
        issue = {"location": location, "cause_code": cause_code}
        observed_value = _safe_invalid_literal(location, error.get("input"))
        if observed_value is not None:
            issue["observed_value"] = observed_value
        context_error = str((error.get("ctx") or {}).get("error") or "")
        constraint_code = _VALIDATION_CONSTRAINT_CODES.get(context_error)
        if constraint_code is not None:
            issue["constraint_code"] = constraint_code
        issues.append(issue)
    return {
        "issue_count": len(errors),
        "issues": issues,
        "truncated": len(errors) > len(issues),
    }


def _repair_failure_evidence(*errors: EditPlanError) -> dict[str, Any]:
    attempts = []
    for phase, error in zip(("initial", "repair"), errors):
        item: dict[str, Any] = {
            "phase": phase,
            "cause_code": error.code,
        }
        for key in ("validation", "intent_conformance"):
            evidence = error.evidence.get(key)
            if isinstance(evidence, dict):
                item[key] = evidence
        attempts.append(item)
    return {"attempts": attempts}


def validate_edit_plan(value: Any, *, source_duration_ms: int | None = None) -> EditPlan:
    try:
        plan = EditPlan.model_validate(value)
    except ValidationError as exc:
        raise EditPlanError(
            "EDIT_PLAN_INVALID",
            "edit plan failed schema validation",
            evidence={"validation": _validation_error_evidence(exc)},
        ) from exc
    if source_duration_ms is not None and plan.source_duration_ms != int(source_duration_ms):
        raise EditPlanError("EDIT_PLAN_SOURCE_MISMATCH", "plan source duration does not match media")
    return plan


def required_capabilities(plan: EditPlan) -> frozenset[str]:
    capabilities = {"subtitles"}
    layout_capabilities = {
        "crop": "crop",
        "fit": "fit",
        "letterbox": "letterbox",
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
    selected_clip_indexes: Sequence[int] | None = None,
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
    indexes = tuple(selected_clip_indexes or range(1, len(selected_clips) + 1))
    if len(indexes) != len(selected_clips) or len(set(indexes)) != len(indexes):
        raise EditPlanError(
            "EDIT_PLAN_CLIP_MISMATCH",
            "selected clip indexes must be unique and aligned with selected clips",
        )
    selected = dict(zip(indexes, selected_clips))
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


def validate_catalog_plan_context(
    plan: EditPlan,
    catalog_snapshot: dict[str, Any] | None,
) -> EditPlan:
    candidates = _catalog_candidate_map(catalog_snapshot)
    selected_ids = {
        value
        for clip in plan.clips
        for value in (
            clip.catalog_selection.style_profile_id,
            clip.catalog_selection.caption_treatment_id,
            clip.catalog_selection.color_treatment_id,
            *clip.catalog_selection.recipe_ids,
            *(segment.transition_in.catalog_id for segment in clip.segments),
        )
        if value
    }
    if not candidates:
        if selected_ids:
            raise EditPlanError(
                "EDIT_PLAN_CATALOG_UNAVAILABLE",
                "the planner selected catalog IDs when catalog planning is disabled",
            )
        return plan
    if (
        plan.catalog_version != str(catalog_snapshot.get("catalog_version") or "")
        or plan.catalog_manifest_sha256
        != str(catalog_snapshot.get("manifest_sha256") or "")
    ):
        raise EditPlanError(
            "EDIT_PLAN_CATALOG_VERSION_MISMATCH",
            "the edit plan catalog snapshot does not match the advertised candidates",
        )

    def require_kind(entry_id: str, kind: str) -> dict[str, Any]:
        entry = candidates.get(entry_id)
        if entry is None:
            raise EditPlanError(
                "EDIT_PLAN_CATALOG_ID_UNKNOWN",
                f"planner selected an unknown catalog ID: {entry_id}",
            )
        if entry.get("kind") != kind:
            raise EditPlanError(
                "EDIT_PLAN_CATALOG_KIND_INVALID",
                f"catalog ID {entry_id} is not a {kind}",
            )
        return entry

    for clip in plan.clips:
        selection = clip.catalog_selection
        style = require_kind(selection.style_profile_id, "style_profile")
        require_kind(selection.caption_treatment_id, "caption_treatment")
        require_kind(selection.color_treatment_id, "color_treatment")
        for recipe_id in selection.recipe_ids:
            require_kind(recipe_id, "recipe")
        style_ids = {
            str(value)
            for value in (style.get("config") or {}).get("catalog_ids") or []
        }
        inconsistent = sorted({
            selection.caption_treatment_id,
            selection.color_treatment_id,
            *selection.recipe_ids,
        } - style_ids)
        if inconsistent:
            raise EditPlanError(
                "EDIT_PLAN_CATALOG_STYLE_MISMATCH",
                "catalog selections do not belong to the selected style profile: "
                + ", ".join(inconsistent),
            )
        for segment in clip.segments:
            transition_id = segment.transition_in.catalog_id
            transition = require_kind(transition_id, "transition")
            operation = str((transition.get("config") or {}).get("operation") or "")
            if operation != "hard_cut" and transition_id not in style_ids:
                raise EditPlanError(
                    "EDIT_PLAN_CATALOG_STYLE_MISMATCH",
                    f"transition {transition_id} does not belong to the selected style profile",
                )
            expected_kind = (
                "cut"
                if operation == "hard_cut"
                else "fade"
                if operation == "fade" and transition_id.startswith("transition.fade-")
                else "xfade"
            )
            if segment.transition_in.kind != expected_kind:
                raise EditPlanError(
                    "EDIT_PLAN_CATALOG_TRANSITION_MISMATCH",
                    f"transition {transition_id} does not match {segment.transition_in.kind}",
                )
    return plan


def merge_repaired_edit_plan_response(
    value: Any,
    *,
    base_plan: EditPlan,
    affected_clip_indexes: Iterable[int],
    selected_clips: Sequence[ShortCandidate],
    known_region_ids: Iterable[str],
    known_track_ids: Iterable[str],
    known_evidence_ids_by_clip: dict[int, Iterable[str]],
    max_segments_per_clip: int,
    max_overlays_per_clip: int,
    max_assets_per_clip: int,
    max_generated_assets_per_clip: int,
    max_stock_assets_per_clip: int,
    asset_policy: AssetPolicy,
    stock_policy: AssetPolicy,
    renderer_capabilities: Iterable[str],
    catalog_snapshot: dict[str, Any] | None = None,
    creative_intent: CreativeIntent | None = None,
) -> EditPlan:
    normalized = _normalize_edit_plan_response(
        value,
        creative_intent=creative_intent,
    )
    if not isinstance(normalized, dict) or not isinstance(normalized.get("clips"), list):
        raise EditPlanError("EDIT_PLAN_INVALID", "repair response must contain clip plans")
    affected = tuple(sorted({int(index) for index in affected_clip_indexes}))
    returned_indexes = tuple(sorted({
        int(clip.get("clip_index") or 0)
        for clip in normalized["clips"]
        if isinstance(clip, dict)
    }))
    if not affected or returned_indexes != affected or len(normalized["clips"]) != len(affected):
        raise EditPlanError(
            "EDIT_PLAN_CLIP_MISMATCH",
            "repair response must replace exactly the affected clips",
        )
    replacements = {
        int(clip["clip_index"]): clip
        for clip in normalized["clips"]
        if isinstance(clip, dict)
    }
    payload = base_plan.to_dict()
    payload.update({
        "planner_version": AGENTIC_PLANNER_VERSION,
        "prompt_version": EDIT_PLAN_PROMPT_VERSION,
        "degraded": False,
        "degradation_reason": "",
    })
    payload["clips"] = [
        replacements.get(int(clip["clip_index"]), clip)
        for clip in payload["clips"]
    ]
    response_capabilities = normalized.get("requested_capabilities")
    if not isinstance(response_capabilities, list):
        raise EditPlanError(
            "EDIT_PLAN_INVALID",
            "repair response must declare requested capabilities",
        )
    payload["requested_capabilities"] = sorted({
        *base_plan.requested_capabilities,
        *(str(value) for value in response_capabilities),
    })
    candidate = validate_edit_plan(payload, source_duration_ms=base_plan.source_duration_ms)
    candidate = candidate.model_copy(update={
        "requested_capabilities": tuple(sorted(
            set(candidate.requested_capabilities) | set(required_capabilities(candidate))
        )),
    })
    candidate = validate_edit_plan_context(
        candidate,
        selected_clips=selected_clips,
        known_region_ids=known_region_ids,
        known_track_ids=known_track_ids,
        known_evidence_ids_by_clip=known_evidence_ids_by_clip,
        max_segments_per_clip=max_segments_per_clip,
        max_overlays_per_clip=max_overlays_per_clip,
        max_assets_per_clip=max_assets_per_clip,
    )
    candidate = validate_catalog_plan_context(candidate, catalog_snapshot)
    generated_counts = {
        clip.clip_index: sum(
            asset.kind == "generated_image" for asset in clip.asset_requests
        )
        for clip in candidate.clips
    }
    stock_counts = {
        clip.clip_index: sum(
            asset.kind in {"stock_image", "stock_video"}
            for asset in clip.asset_requests
        )
        for clip in candidate.clips
    }
    if asset_policy == "off" and any(generated_counts.values()):
        raise EditPlanError(
            "EDIT_PLAN_ASSET_POLICY_BLOCKED",
            "repair requested generated images while that job policy is off",
        )
    if stock_policy == "off" and any(stock_counts.values()):
        raise EditPlanError(
            "EDIT_PLAN_STOCK_POLICY_BLOCKED",
            "repair requested stock assets while that job policy is off",
        )
    if any(count > max_generated_assets_per_clip for count in generated_counts.values()):
        raise EditPlanError(
            "EDIT_PLAN_GENERATED_ASSET_BUDGET_EXCEEDED",
            "repair exceeds the generated image budget",
        )
    if any(count > max_stock_assets_per_clip for count in stock_counts.values()):
        raise EditPlanError(
            "EDIT_PLAN_STOCK_ASSET_BUDGET_EXCEEDED",
            "repair exceeds the stock asset budget",
        )
    available = {str(value) for value in renderer_capabilities}
    unavailable = sorted(set(candidate.requested_capabilities) - available)
    if unavailable:
        raise EditPlanError(
            "EDIT_PLAN_CAPABILITY_UNAVAILABLE",
            "repair requested unavailable capabilities: " + ", ".join(unavailable),
        )
    if creative_intent is not None:
        try:
            validate_creative_intent_conformance(candidate, creative_intent)
        except ValueError as exc:
            raise EditPlanError(
                "EDIT_PLAN_INTENT_MISMATCH",
                _safe_text(str(exc), limit=1000),
                evidence={
                    "intent_conformance": creative_intent_conformance_evidence(exc),
                },
            ) from exc
    return candidate


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


@dataclass(frozen=True)
class DeferredEditPlanDefect:
    code: str
    clip_index: int
    evidence: dict[str, Any]
    invalid_candidate: dict[str, Any]


class AgenticEditPlanner:
    def __init__(self, client: NineRouterClient) -> None:
        self.client = client
        self.deferred_defects: tuple[DeferredEditPlanDefect, ...] = ()

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
        max_generated_assets_per_clip: int | None = None,
        max_stock_assets_per_clip: int = 0,
        stock_policy: AssetPolicy = "off",
        creative_intent: CreativeIntent | None = None,
        allow_degraded_fallback: bool = False,
        visual_coverage_feedback: dict[str, Any] | None = None,
        prior_attempt_quality_feedback: dict[str, Any] | None = None,
        catalog_snapshot: dict[str, Any] | None = None,
        renderer_capabilities: Iterable[str] = SUPPORTED_CAPABILITIES,
        defer_registry_repair: bool = False,
    ) -> EditPlan:
        self.deferred_defects = ()
        deferred_defects: list[DeferredEditPlanDefect] = []
        available_capabilities = frozenset(str(value) for value in renderer_capabilities)
        if not available_capabilities or not available_capabilities <= SUPPORTED_CAPABILITIES:
            raise EditPlanError(
                "EDIT_PLAN_CAPABILITY_CONFIG_INVALID",
                "renderer capabilities must be a non-empty supported subset",
            )
        generated_limit = (
            max_assets_per_clip
            if max_generated_assets_per_clip is None
            else int(max_generated_assets_per_clip)
        )
        stock_limit = int(max_stock_assets_per_clip)
        if not 0 <= generated_limit <= 8 or not 0 <= stock_limit <= 8:
            raise EditPlanError(
                "EDIT_PLAN_ASSET_BUDGET_INVALID",
                "generated and stock asset budgets must be between 0 and 8",
            )
        if asset_policy not in {"off", "auto", "required"}:
            raise EditPlanError(
                "EDIT_PLAN_ASSET_POLICY_INVALID",
                "asset_policy must be off, auto, or required",
            )
        if stock_policy not in {"off", "auto", "required"}:
            raise EditPlanError(
                "EDIT_PLAN_ASSET_POLICY_INVALID",
                "stock_policy must be off, auto, or required",
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
        base_user_payload = {
            "editing_prompt": _safe_text(editing_prompt, limit=12_000),
            "source_duration_ms": source_duration_ms,
            "asset_policy": asset_policy,
            "renderer_capabilities": sorted(available_capabilities),
            "budgets": {
                "max_segments_per_clip": max_segments_per_clip,
                "max_overlays_per_clip": max_overlays_per_clip,
                "max_assets_per_clip": max_assets_per_clip,
                "max_generated_assets_per_clip": generated_limit,
                "max_stock_assets_per_clip": stock_limit,
            },
            "stock_policy": stock_policy,
            "asset_providers": {
                "generated_image": (
                    ["9router"]
                    if asset_policy in {"auto", "required"} and generated_limit
                    else []
                ),
                "stock_image": (
                    ["pexels"]
                    if stock_policy in {"auto", "required"} and stock_limit
                    else []
                ),
                "stock_video": (
                    ["pexels"]
                    if stock_policy in {"auto", "required"} and stock_limit
                    else []
                ),
            },
            "rules": [
                "Preserve every selected clip source window exactly.",
                "Cover each clip timeline contiguously from zero to its full selected duration.",
                "Use evidence IDs and semantic targets only from the supplied clip context.",
                "Use source evidence when it satisfies the visual intent.",
                "Request an asset only for a specific unresolved visual gap and an enabled policy.",
                "Use only the asset kinds and providers explicitly available in asset_providers.",
                "Every creative_intent requirement needs an explicit intent_decision.",
                "Required asset intent must map exact-count asset_requests to executed image overlays.",
                "Opening-title intent must map one early text overlay that satisfies its timing bounds.",
                "Reframe-sequence intent must map the requested bounded count of distinct crop or focus-zoom segments.",
                "Restrained-transition intent must map non-initial fade or xfade segments within its duration bounds.",
                "Required intent cannot be omitted; optional omission reasons are allowlisted by the schema.",
                "Crop/focus segments should use same-window track evidence spanning the segment.",
                "A fit or letterbox crop fallback is valid only with allow_full_frame_fallback=true.",
                "Never return FFmpeg expressions, commands, paths, or unsupported operations.",
                "Choose catalog IDs only from creative_catalog.entries and keep style, caption, color, and transition choices consistent.",
            ],
            "visual_coverage_feedback": visual_coverage_feedback or {},
            "prior_attempt_quality_feedback": prior_attempt_quality_feedback or {},
            "creative_catalog": catalog_snapshot or {},
            "exact_field_contract": _edit_plan_field_contract(),
        }
        known_region_ids = tuple(region.id for region in visual_understanding.regions)
        known_track_ids = tuple(track.id for track in visual_understanding.tracks)
        known_evidence_ids_by_clip = {
            int(clip.get("clip_index") or 0): {
                str(evidence_id)
                for evidence_id in clip.get("evidence_ids") or []
            }
            for clip in shorts_plan_artifact.get("clips") or []
        }
        all_attempts: list[Any] = []

        async def complete(**kwargs: Any) -> dict[str, Any]:
            try:
                return await self.client.complete_structured(**kwargs)
            finally:
                all_attempts.extend(tuple(getattr(self.client, "last_attempts", ())))
                if hasattr(self.client, "last_attempts"):
                    self.client.last_attempts = tuple(all_attempts)

        planned_clips: list[ClipEditPlan] = []
        requested_capabilities: set[str] = set()
        degradation_reasons: set[str] = set()
        for clip_index, (selected_clip, clip_context) in enumerate(
            zip(shorts_plan.clips, clip_contexts),
            start=1,
        ):
            clip_intent = (
                creative_intent.for_clip(clip_index)
                if creative_intent is not None
                else None
            )
            user_payload = {
                **base_user_payload,
                "clip_task": {
                    "clip_index": clip_index,
                    "total_clips": len(clip_contexts),
                },
                "valid_output_template": _valid_clip_plan_template(
                    clip_context,
                    catalog_snapshot=catalog_snapshot,
                    creative_intent=clip_intent,
                    opening_title_text=_opening_title_text(
                        editing_prompt,
                        str(clip_context.get("title") or "Opening"),
                    ),
                ),
                "clips": [clip_context],
                "creative_intent": (
                    creative_intent.planner_payload(clip_index=clip_index)
                    if creative_intent is not None
                    else {"asset_intents": [], "operation_intents": []}
                ),
            }
            def validate_response(value: Any, *, enforce_intent: bool = True) -> EditPlan:
                payload = dict(_normalize_edit_plan_response(
                    value,
                    creative_intent=clip_intent,
                ))
                payload.update({
                    "version": EDIT_PLAN_VERSION,
                    "planner_version": AGENTIC_PLANNER_VERSION,
                    "prompt_version": EDIT_PLAN_PROMPT_VERSION,
                    "source_duration_ms": source_duration_ms,
                    "catalog_version": str(
                        (catalog_snapshot or {}).get("catalog_version") or ""
                    ),
                    "catalog_manifest_sha256": str(
                        (catalog_snapshot or {}).get("manifest_sha256") or ""
                    ),
                })
                clip_plan = validate_edit_plan(
                    payload,
                    source_duration_ms=source_duration_ms,
                )
                clip_plan = clip_plan.model_copy(update={
                    "requested_capabilities": tuple(sorted(
                        set(clip_plan.requested_capabilities)
                        | set(required_capabilities(clip_plan))
                    )),
                })
                clip_plan = validate_edit_plan_context(
                    clip_plan,
                    selected_clips=(selected_clip,),
                    selected_clip_indexes=(clip_index,),
                    known_region_ids=known_region_ids,
                    known_track_ids=known_track_ids,
                    known_evidence_ids_by_clip=known_evidence_ids_by_clip,
                    max_segments_per_clip=max_segments_per_clip,
                    max_overlays_per_clip=max_overlays_per_clip,
                    max_assets_per_clip=max_assets_per_clip,
                )
                clip_plan = validate_catalog_plan_context(
                    clip_plan,
                    catalog_snapshot,
                )
                generated_count = sum(
                    asset.kind == "generated_image"
                    for clip in clip_plan.clips
                    for asset in clip.asset_requests
                )
                stock_count = sum(
                    asset.kind in {"stock_image", "stock_video"}
                    for clip in clip_plan.clips
                    for asset in clip.asset_requests
                )
                if asset_policy == "off" and generated_count:
                    raise EditPlanError(
                        "EDIT_PLAN_ASSET_POLICY_BLOCKED",
                        "the planner requested generated images while that job policy is off",
                    )
                if stock_policy == "off" and stock_count:
                    raise EditPlanError(
                        "EDIT_PLAN_STOCK_POLICY_BLOCKED",
                        "the planner requested Pexels stock while that job policy is off",
                    )
                if generated_count > generated_limit:
                    raise EditPlanError(
                        "EDIT_PLAN_GENERATED_ASSET_BUDGET_EXCEEDED",
                        f"clip {clip_index} exceeds the generated image budget",
                    )
                if stock_count > stock_limit:
                    raise EditPlanError(
                        "EDIT_PLAN_STOCK_ASSET_BUDGET_EXCEEDED",
                        f"clip {clip_index} exceeds the Pexels stock budget",
                    )
                unavailable = sorted(
                    set(clip_plan.requested_capabilities) - available_capabilities
                )
                if unavailable:
                    raise EditPlanError(
                        "EDIT_PLAN_CAPABILITY_UNAVAILABLE",
                        "planner requested unavailable capabilities: "
                        + ", ".join(unavailable),
                    )
                if clip_intent is not None and enforce_intent:
                    try:
                        validate_creative_intent_conformance(clip_plan, clip_intent)
                    except ValueError as exc:
                        raise EditPlanError(
                            "EDIT_PLAN_INTENT_MISMATCH",
                            _safe_text(str(exc), limit=1000),
                            evidence={
                                "intent_conformance": (
                                    creative_intent_conformance_evidence(exc)
                                ),
                            },
                        ) from exc
                return clip_plan

            response = await complete(
                schema_name=EDIT_PLAN_SCHEMA,
                system_prompt=EDIT_PLAN_SYSTEM_PROMPT,
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                reasoning_effort=getattr(self.client, "reasoning_effort", "medium"),
            )
            try:
                clip_plan = validate_response(response)
            except EditPlanError as initial_error:
                if defer_registry_repair:
                    deferred_defects.append(DeferredEditPlanDefect(
                        code=initial_error.code,
                        clip_index=clip_index,
                        evidence=dict(initial_error.evidence),
                        invalid_candidate=deepcopy(response),
                    ))
                    fallback = deepcopy(user_payload["valid_output_template"])
                    fallback["clips"][0]["segments"][0]["reason"] = (
                        "Use the strongest validated source evidence while the bounded "
                        "job-level repair policy evaluates the invalid candidate."
                    )
                    clip_plan = validate_response(fallback, enforce_intent=False)
                    planned_clips.extend(clip_plan.clips)
                    requested_capabilities.update(clip_plan.requested_capabilities)
                    continue
                repair_payload = {
                    "repair_task": (
                        "Rewrite invalid_response using valid_output_template and "
                        "exact_field_contract exactly. Preserve every usable editorial "
                        "decision from invalid_response; do not collapse to the minimal "
                        "template unless no valid decision can be retained. Return only "
                        "the corrected JSON object."
                    ),
                    "validation_error": {
                        "code": initial_error.code,
                        "evidence": initial_error.evidence,
                    },
                    "authoritative_request": user_payload,
                    "invalid_response": response,
                }
                repaired = await complete(
                    schema_name=EDIT_PLAN_REPAIR_SCHEMA,
                    system_prompt=EDIT_PLAN_SYSTEM_PROMPT,
                    user_prompt=json.dumps(repair_payload, ensure_ascii=False),
                    reasoning_effort=getattr(self.client, "reasoning_effort", "medium"),
                )
                try:
                    clip_plan = validate_response(repaired)
                except EditPlanError as repair_error:
                    if not allow_degraded_fallback:
                        raise EditPlanError(
                            "EDIT_PLAN_REPAIR_EXHAUSTED",
                            "remote edit planning remained invalid after one repair attempt",
                            evidence=_repair_failure_evidence(initial_error, repair_error),
                        ) from repair_error
                    fallback = deepcopy(user_payload["valid_output_template"])
                    fallback["clips"][0]["segments"][0]["reason"] = (
                        "Use the strongest validated source evidence after remote "
                        "edit-plan validation failed."
                    )
                    clip_plan = validate_response(fallback, enforce_intent=False)
                    clip_plan = clip_plan.model_copy(update={
                        "degraded": True,
                        "degradation_reason": "schema_repair_exhausted_shadow_fallback",
                    })
            planned_clips.extend(clip_plan.clips)
            requested_capabilities.update(clip_plan.requested_capabilities)
            if clip_plan.degraded:
                degradation_reasons.add(clip_plan.degradation_reason)

        plan = EditPlan(
            planner_version=AGENTIC_PLANNER_VERSION,
            prompt_version=EDIT_PLAN_PROMPT_VERSION,
            source_duration_ms=source_duration_ms,
            requested_capabilities=tuple(sorted(requested_capabilities)),
            clips=tuple(planned_clips),
            degraded=bool(degradation_reasons),
            degradation_reason=";".join(sorted(degradation_reasons)),
            catalog_version=str(
                (catalog_snapshot or {}).get("catalog_version") or ""
            ),
            catalog_manifest_sha256=str(
                (catalog_snapshot or {}).get("manifest_sha256") or ""
            ),
        )
        self.deferred_defects = tuple(deferred_defects)
        generated_assets = [
            asset
            for clip in plan.clips
            for asset in clip.asset_requests
            if asset.kind == "generated_image"
        ]
        stock_assets = [
            asset
            for clip in plan.clips
            for asset in clip.asset_requests
            if asset.kind in {"stock_image", "stock_video"}
        ]
        if asset_policy == "off" and generated_assets:
            raise EditPlanError(
                "EDIT_PLAN_ASSET_POLICY_BLOCKED",
                "the planner requested generated images while that job policy is off",
            )
        if stock_policy == "off" and stock_assets:
            raise EditPlanError(
                "EDIT_PLAN_STOCK_POLICY_BLOCKED",
                "the planner requested Pexels stock while that job policy is off",
            )
        for clip in plan.clips:
            generated_count = sum(
                asset.kind == "generated_image" for asset in clip.asset_requests
            )
            stock_count = sum(
                asset.kind in {"stock_image", "stock_video"}
                for asset in clip.asset_requests
            )
            if generated_count > generated_limit:
                raise EditPlanError(
                    "EDIT_PLAN_GENERATED_ASSET_BUDGET_EXCEEDED",
                    f"clip {clip.clip_index} exceeds the generated image budget",
                )
            if stock_count > stock_limit:
                raise EditPlanError(
                    "EDIT_PLAN_STOCK_ASSET_BUDGET_EXCEEDED",
                    f"clip {clip.clip_index} exceeds the Pexels stock budget",
                )
        unavailable = sorted(set(plan.requested_capabilities) - available_capabilities)
        if unavailable:
            raise EditPlanError(
                "EDIT_PLAN_CAPABILITY_UNAVAILABLE",
                f"planner requested unavailable capabilities: {', '.join(unavailable)}",
            )
        plan = validate_edit_plan_context(
            plan,
            selected_clips=shorts_plan.clips,
            known_region_ids=known_region_ids,
            known_track_ids=known_track_ids,
            known_evidence_ids_by_clip=known_evidence_ids_by_clip,
            max_segments_per_clip=max_segments_per_clip,
            max_overlays_per_clip=max_overlays_per_clip,
            max_assets_per_clip=max_assets_per_clip,
        )
        return validate_catalog_plan_context(plan, catalog_snapshot)


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
                layout=LayoutSpec(mode="crop", fallback="crop"),
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


def validate_asset_policy(asset_policy: str) -> AssetPolicy:
    normalized_assets = str(asset_policy or "auto").strip().lower()
    if normalized_assets not in {"off", "auto", "required"}:
        raise EditPlanError(
            "ASSET_POLICY_INVALID",
            "asset_policy must be off, auto, or required",
        )
    return normalized_assets  # type: ignore[return-value]


def validate_generated_asset_limit(value: int) -> int:
    limit = int(value)
    if not 0 <= limit <= 8:
        raise EditPlanError(
            "GENERATED_ASSET_LIMIT_INVALID",
            "max_generated_assets_per_clip must be between 0 and 8",
        )
    return limit


def validate_stock_policy(value: str) -> AssetPolicy:
    normalized = str(value or "off").strip().lower()
    if normalized not in {"off", "auto", "required"}:
        raise EditPlanError(
            "STOCK_POLICY_INVALID",
            "stock_policy must be off, auto, or required",
        )
    return normalized  # type: ignore[return-value]


def validate_stock_asset_limit(value: int) -> int:
    limit = int(value)
    if not 0 <= limit <= 8:
        raise EditPlanError(
            "STOCK_ASSET_LIMIT_INVALID",
            "max_stock_assets_per_clip must be between 0 and 8",
        )
    return limit


def validate_stock_asset_kind(value: str) -> StockAssetKind:
    normalized = str(value or "video").strip().lower()
    if normalized not in {"image", "video"}:
        raise EditPlanError(
            "STOCK_ASSET_KIND_INVALID",
            "stock_asset_kind must be image or video",
        )
    return normalized  # type: ignore[return-value]


@dataclass(frozen=True)
class AgenticArtifactNames:
    creative_intent: str = "creative_intent.json"
    scene_boundaries: str = "scene_boundaries.json"
    visual_understanding: str = "visual_understanding.json"
    clip_visual_coverage: str = "clip_visual_coverage.json"
    shorts_plan: str = "shorts_plan.json"
    proposed_edit_plan: str = "proposed_edit_plan.json"
    edit_plan: str = "edit_plan.json"
    fallback_ledger: str = "fallback_ledger.json"
    preflight: str = "edit_preflight.json"
    ffmpeg_preflight: str = "ffmpeg_preflight.json"
    asset_manifest: str = "asset_manifest.json"
    creative_catalog_usage: str = "creative_catalog_usage.json"
    render_execution: str = "render_execution.json"
    render_quality_profile: str = "render_quality_profile.json"
    frame_quality_qa: str = "frame_quality_qa.json"
    render_evidence: str = "render_evidence.json"
    render_critic: str = "render_critic.json"
    post_render_repair: str = "post_render_repair.json"
    render_promotion: str = "render_promotion.json"
    repair_report: str = "repair_report.json"
    outcome_report: str = "outcome_report.json"
    render_qa: str = "render_qa.json"
    retention_rhythm_qa: str = "retention_rhythm_qa.json"
    creative_conformance: str = "creative_conformance.json"
