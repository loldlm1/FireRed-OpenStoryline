from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal
import json
import re

from pydantic import BaseModel, ConfigDict, Field

from open_storyline.mvp.ffmpega_contracts import (
    FFMPEGAAgenticFinishingResponse,
    FFMPEGADeterministicEffectsResponse,
)


SHORTS_SELECTION_SCHEMA = "shorts_selection.v1"
VISUAL_UNDERSTANDING_SCHEMA = "visual_understanding.v1"
EDIT_PLAN_SCHEMA = "edit_plan.v1"
EDIT_PLAN_REPAIR_SCHEMA = "edit_plan_repair.v1"
SEMANTIC_QA_SCHEMA = "semantic_qa.v1"
RENDER_CRITIC_SCHEMA = "render_critic.v1"
CANDIDATE_COMPARISON_SCHEMA = "candidate_comparison.v1"
POST_RENDER_REPAIR_COMPAT_SCHEMA = "post_render_repair.v1"
POST_RENDER_REPAIR_SCHEMA = "post_render_repair.v2"
FFMPEGA_AGENTIC_SCHEMA = "ffmpega_agentic_finishing.v1"
FFMPEGA_DETERMINISTIC_SCHEMA = "ffmpega_deterministic_effects.v1"


class StructuredOutputError(ValueError):
    pass


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ShortsCandidateWire(WireModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    title: str = Field(max_length=240)
    hook: str = Field(max_length=240)
    reason: str = Field(max_length=500)
    score: float = Field(ge=0, le=1, allow_inf_nan=False)


class ShortsSelectionWire(WireModel):
    clips: list[ShortsCandidateWire] = Field(max_length=150)


class NormalizedBoxWire(WireModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, allow_inf_nan=False)


SemanticRole = Literal[
    "speaker",
    "screen",
    "text",
    "object",
    "demonstration_target",
    "background",
]


class RegionObservationWire(WireModel):
    id: str = Field(min_length=1, max_length=80)
    frame_id: str = Field(min_length=1, max_length=80)
    role: SemanticRole
    bbox: NormalizedBoxWire
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    salience: float = Field(ge=0, le=1, allow_inf_nan=False)
    description: str = Field(max_length=240)


class TrackObservationWire(WireModel):
    id: str = Field(min_length=1, max_length=80)
    role: SemanticRole
    region_ids: list[str] = Field(min_length=1, max_length=64)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    motion: Literal["static", "low", "medium", "high", "unknown"]
    description: str = Field(max_length=240)


class SceneObservationWire(WireModel):
    scene_id: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    salient_region_ids: list[str] = Field(max_length=32)


class VisualUnderstandingWire(WireModel):
    regions: list[RegionObservationWire] = Field(max_length=256)
    tracks: list[TrackObservationWire] = Field(max_length=128)
    scenes: list[SceneObservationWire] = Field(max_length=128)
    warnings: list[str] = Field(max_length=64)


class TimeWindowWire(WireModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class FocalTargetWire(WireModel):
    region_id: str | None = Field(max_length=80)
    track_id: str | None = Field(max_length=80)
    semantic_role: Literal[
        "speaker",
        "screen",
        "text",
        "object",
        "demonstration_target",
        "background",
    ] | None


class LayoutWire(WireModel):
    mode: Literal["crop", "fit", "letterbox", "source"]
    focal_target: FocalTargetWire | None
    fallback: Literal["crop", "fit", "letterbox", "source"]
    allow_full_frame_fallback: bool
    safe_margin_ratio: float = Field(ge=0, le=0.35, allow_inf_nan=False)
    max_zoom: float = Field(ge=1, le=4, allow_inf_nan=False)


class TransitionWire(WireModel):
    kind: Literal["cut", "fade", "xfade"]
    duration_ms: int = Field(ge=0, le=1500)
    catalog_id: str | None = Field(max_length=80)


class CatalogSelectionWire(WireModel):
    style_profile_id: str | None = Field(max_length=80)
    caption_treatment_id: str | None = Field(max_length=80)
    color_treatment_id: str | None = Field(max_length=80)
    recipe_ids: list[str] = Field(max_length=8)


class OverlayWire(WireModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "image", "source", "pip"]
    timeline_window: TimeWindowWire
    source_window: TimeWindowWire | None
    text: str | None = Field(max_length=500)
    asset_id: str | None = Field(max_length=80)
    opacity: float = Field(ge=0, le=1, allow_inf_nan=False)
    width_ratio: float = Field(ge=0.08, le=1, allow_inf_nan=False)
    margin_ratio: float = Field(ge=0, le=0.2, allow_inf_nan=False)
    transition_ms: int = Field(ge=0, le=800)
    z_index: int = Field(ge=1, le=100)
    protect_subtitles: bool
    position: Literal[
        "center",
        "top",
        "bottom",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
    ]


class AssetRequestWire(WireModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["generated_image", "stock_image", "stock_video"]
    provider: Literal["9router", "pexels"]
    timeline_window: TimeWindowWire
    visual_gap: str = Field(min_length=1, max_length=500)
    purpose: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=7000)
    orientation: Literal["portrait", "landscape"]
    required: bool
    fallback: Literal["source", "fit", "omit"]


class IntentDecisionWire(WireModel):
    intent_id: str = Field(min_length=1, max_length=80)
    decision: Literal["execute", "omit"]
    asset_ids: list[str] = Field(max_length=8)
    operation_ids: list[str] = Field(max_length=32)
    omission_reason: str | None = Field(max_length=80)


class EditSegmentWire(WireModel):
    id: str = Field(min_length=1, max_length=80)
    source_window: TimeWindowWire
    timeline_window: TimeWindowWire
    layout: LayoutWire
    transition_in: TransitionWire
    overlays: list[OverlayWire] = Field(max_length=16)
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(max_length=32)


class ClipEditPlanWire(WireModel):
    clip_index: int = Field(ge=1, le=50)
    title: str = Field(max_length=120)
    source_window: TimeWindowWire
    output_name: str = Field(min_length=1, max_length=120)
    segments: list[EditSegmentWire] = Field(min_length=1, max_length=48)
    asset_requests: list[AssetRequestWire] = Field(max_length=8)
    intent_decisions: list[IntentDecisionWire] = Field(max_length=32)
    catalog_selection: CatalogSelectionWire


class EditPlanWire(WireModel):
    requested_capabilities: list[str] = Field(max_length=32)
    clips: list[ClipEditPlanWire] = Field(min_length=1, max_length=8)


class SemanticObservationWire(WireModel):
    clip_index: int = Field(ge=1, le=8)
    frame_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    planned_focus_visible: bool
    relevant: bool
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    note: str = Field(max_length=240)


class SemanticQAResponseWire(WireModel):
    status: Literal["pass", "review"]
    summary: str = Field(min_length=1, max_length=500)
    observations: list[SemanticObservationWire] = Field(max_length=8)


class RenderCriticFindingWire(WireModel):
    finding_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    category: Literal[
        "composition",
        "framing",
        "captions",
        "pacing",
        "narrative",
        "transitions",
        "effects",
        "visual_hierarchy",
        "relevance",
    ]
    severity: Literal["advisory", "warning", "blocker"]
    classification: Literal["creative", "objective", "technical", "advisory"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    clip_index: int = Field(ge=1, le=50)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)
    explanation: str = Field(min_length=1, max_length=600)
    repair_objective: str = Field(min_length=1, max_length=320)
    requested_capabilities: list[Literal[
        "crop",
        "fit",
        "letterbox",
        "subtitles",
        "hard_cut",
        "fade",
        "xfade",
        "image_overlay",
        "pip",
        "zoom",
        "effect",
    ]] = Field(max_length=8)
    repairable: bool


class RenderCriticResponseWire(WireModel):
    status: Literal["pass", "review"]
    scope: Literal["rendered_evidence_only"]
    non_mutating: bool
    summary: str = Field(min_length=1, max_length=600)
    findings: list[RenderCriticFindingWire] = Field(max_length=64)


class CandidateComparisonResponseWire(WireModel):
    selection: Literal["original", "repaired", "tie"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    rationale: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=32)
    uncertainty: Literal["low", "medium", "high"]


class PostRenderRepairDecisionWire(WireModel):
    finding_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^finding-[A-Za-z0-9._:-]+$",
    )
    decision: Literal["repair", "no_change"]
    reason: str = Field(min_length=1, max_length=320)
    affected_clip_indexes: list[int] = Field(max_length=8)


class PostRenderRepairResponseWire(WireModel):
    status: Literal["repair", "no_change"]
    decisions: list[PostRenderRepairDecisionWire] = Field(min_length=1, max_length=64)
    requested_capabilities: list[str] = Field(max_length=32)
    clips: list[ClipEditPlanWire] = Field(max_length=8)


class PostRenderRepairDecisionV2Wire(PostRenderRepairDecisionWire):
    target: Literal["clip_plan", "effect_plan", "none"]


class PostRenderRepairResponseV2Wire(WireModel):
    status: Literal["repair", "no_change"]
    decisions: list[PostRenderRepairDecisionV2Wire] = Field(min_length=1, max_length=64)
    requested_capabilities: list[str] = Field(max_length=32)
    clips: list[ClipEditPlanWire] = Field(max_length=8)
    effect_action: Literal["preserve", "replace"]
    effect_plan: FFMPEGAAgenticFinishingResponse


@dataclass(frozen=True)
class StructuredOutputDefinition:
    name: str
    provider_name: str
    version: int
    model: type[BaseModel]
    schema: dict[str, Any]
    fingerprint: str

    def validate(self, value: Any) -> dict[str, Any]:
        return self.model.model_validate(value).model_dump(mode="json")


_UNSUPPORTED_KEYWORDS = frozenset({
    "default",
    "dependentRequired",
    "dependentSchemas",
    "if",
    "maxContains",
    "minContains",
    "not",
    "patternProperties",
    "then",
    "unevaluatedProperties",
})


def validate_provider_schema(schema: dict[str, Any]) -> None:
    def visit(value: Any, path: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
            return
        if not isinstance(value, dict):
            return
        unsupported = sorted(_UNSUPPORTED_KEYWORDS & value.keys())
        if unsupported:
            raise StructuredOutputError(
                f"unsupported schema keyword at {path}: {', '.join(unsupported)}"
            )
        if value.get("type") == "object" or "properties" in value:
            properties = value.get("properties")
            if not isinstance(properties, dict):
                raise StructuredOutputError(f"object properties are missing at {path}")
            if value.get("additionalProperties") is not False:
                raise StructuredOutputError(f"additional properties must be forbidden at {path}")
            required = value.get("required", [])
            if set(required) != set(properties):
                raise StructuredOutputError(f"every object property must be required at {path}")
        for key, item in value.items():
            visit(item, f"{path}.{key}")

    visit(schema, "$")


def _definition(name: str, model: type[BaseModel]) -> StructuredOutputDefinition:
    match = re.fullmatch(r"([a-z0-9_]+)\.v([1-9][0-9]*)", name)
    if match is None:
        raise StructuredOutputError(f"invalid stable schema name: {name}")
    schema = model.model_json_schema(mode="validation")
    validate_provider_schema(schema)
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return StructuredOutputDefinition(
        name=name,
        provider_name=f"{match.group(1)}_v{match.group(2)}",
        version=int(match.group(2)),
        model=model,
        schema=schema,
        fingerprint=sha256(canonical.encode("utf-8")).hexdigest(),
    )


STRUCTURED_OUTPUTS = {
    item.name: item
    for item in (
        _definition(SHORTS_SELECTION_SCHEMA, ShortsSelectionWire),
        _definition(VISUAL_UNDERSTANDING_SCHEMA, VisualUnderstandingWire),
        _definition(EDIT_PLAN_SCHEMA, EditPlanWire),
        _definition(EDIT_PLAN_REPAIR_SCHEMA, EditPlanWire),
        _definition(SEMANTIC_QA_SCHEMA, SemanticQAResponseWire),
        _definition(RENDER_CRITIC_SCHEMA, RenderCriticResponseWire),
        _definition(CANDIDATE_COMPARISON_SCHEMA, CandidateComparisonResponseWire),
        _definition(POST_RENDER_REPAIR_COMPAT_SCHEMA, PostRenderRepairResponseWire),
        _definition(POST_RENDER_REPAIR_SCHEMA, PostRenderRepairResponseV2Wire),
        _definition(FFMPEGA_AGENTIC_SCHEMA, FFMPEGAAgenticFinishingResponse),
        _definition(FFMPEGA_DETERMINISTIC_SCHEMA, FFMPEGADeterministicEffectsResponse),
    )
}


def structured_output(name: str) -> StructuredOutputDefinition:
    try:
        return STRUCTURED_OUTPUTS[str(name)]
    except KeyError as exc:
        raise StructuredOutputError(f"unknown structured output: {name}") from exc


def parse_structured_output_boundaries(value: str | None) -> frozenset[str]:
    names = frozenset(item.strip() for item in str(value or "").split(",") if item.strip())
    unknown = sorted(names - STRUCTURED_OUTPUTS.keys())
    if unknown:
        raise StructuredOutputError(
            "unknown structured output boundaries: " + ", ".join(unknown)
        )
    return names


__all__ = [
    "EDIT_PLAN_REPAIR_SCHEMA",
    "EDIT_PLAN_SCHEMA",
    "FFMPEGA_AGENTIC_SCHEMA",
    "FFMPEGA_DETERMINISTIC_SCHEMA",
    "SEMANTIC_QA_SCHEMA",
    "RENDER_CRITIC_SCHEMA",
    "CANDIDATE_COMPARISON_SCHEMA",
    "POST_RENDER_REPAIR_SCHEMA",
    "POST_RENDER_REPAIR_COMPAT_SCHEMA",
    "SHORTS_SELECTION_SCHEMA",
    "STRUCTURED_OUTPUTS",
    "StructuredOutputDefinition",
    "StructuredOutputError",
    "VISUAL_UNDERSTANDING_SCHEMA",
    "parse_structured_output_boundaries",
    "structured_output",
    "validate_provider_schema",
]
