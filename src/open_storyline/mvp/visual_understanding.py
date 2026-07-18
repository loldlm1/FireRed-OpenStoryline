from __future__ import annotations

from typing import Any, Literal
import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from open_storyline.mvp.frame_sampling import FrameManifest
from open_storyline.mvp.ninerouter import NineRouterClient
from open_storyline.mvp.prompts import (
    VISUAL_UNDERSTANDING_PROMPT_VERSION,
    VISUAL_UNDERSTANDING_SYSTEM_PROMPT,
)
from open_storyline.mvp.scene_boundaries import SceneBoundaryReport


VISUAL_UNDERSTANDING_VERSION = "visual_understanding.v1"

SemanticRole = Literal[
    "speaker",
    "screen",
    "text",
    "object",
    "demonstration_target",
    "background",
]


class VisualUnderstandingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _safe_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if "\x00" in text:
        raise ValueError("text contains a null byte")
    return text[:limit]


class VisualModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NormalizedBox(VisualModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def stay_inside_frame(self) -> "NormalizedBox":
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("normalized box must stay inside the frame")
        return self


class RegionObservation(VisualModel):
    id: str = Field(min_length=1, max_length=80)
    frame_id: str = Field(min_length=1, max_length=80)
    role: SemanticRole
    bbox: NormalizedBox
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    salience: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    description: str = Field(default="", max_length=240)

    @field_validator("id", "frame_id", "description")
    @classmethod
    def clean_text(cls, value: str, info: Any) -> str:
        return _safe_text(value, limit=240 if info.field_name == "description" else 80)


class TrackObservation(VisualModel):
    id: str = Field(min_length=1, max_length=80)
    role: SemanticRole
    region_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    motion: Literal["static", "low", "medium", "high", "unknown"] = "unknown"
    description: str = Field(default="", max_length=240)

    @field_validator("id", "description")
    @classmethod
    def clean_text(cls, value: str, info: Any) -> str:
        return _safe_text(value, limit=240 if info.field_name == "description" else 80)

    @field_validator("region_ids")
    @classmethod
    def clean_regions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_safe_text(value, limit=80) for value in values)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("track region IDs must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_window(self) -> "TrackObservation":
        if self.end_ms <= self.start_ms:
            raise ValueError("track end_ms must be greater than start_ms")
        return self


class SceneObservation(VisualModel):
    scene_id: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    salient_region_ids: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("scene_id", "summary")
    @classmethod
    def clean_text(cls, value: str, info: Any) -> str:
        return _safe_text(value, limit=500 if info.field_name == "summary" else 80)

    @field_validator("salient_region_ids")
    @classmethod
    def clean_regions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_safe_text(value, limit=80) for value in values)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("salient region IDs must be unique")
        return cleaned


class VisualUnderstanding(VisualModel):
    version: Literal[VISUAL_UNDERSTANDING_VERSION] = VISUAL_UNDERSTANDING_VERSION
    prompt_version: Literal[VISUAL_UNDERSTANDING_PROMPT_VERSION] = VISUAL_UNDERSTANDING_PROMPT_VERSION
    model: str = Field(min_length=1, max_length=120)
    source_duration_ms: int = Field(gt=0)
    frame_manifest: dict[str, Any]
    regions: tuple[RegionObservation, ...] = Field(default=(), max_length=256)
    tracks: tuple[TrackObservation, ...] = Field(default=(), max_length=128)
    scenes: tuple[SceneObservation, ...] = Field(default=(), max_length=128)
    warnings: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("model")
    @classmethod
    def clean_model(cls, value: str) -> str:
        return _safe_text(value, limit=120)

    @field_validator("warnings")
    @classmethod
    def clean_warnings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_text(value, limit=300) for value in values)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def validate_visual_understanding(
    raw: Any,
    *,
    frame_manifest: FrameManifest,
    scene_report: SceneBoundaryReport,
    model: str,
) -> VisualUnderstanding:
    if not isinstance(raw, dict):
        raise VisualUnderstandingError(
            "VISUAL_RESPONSE_INVALID",
            "visual understanding response must be an object",
        )
    payload = {
        "version": VISUAL_UNDERSTANDING_VERSION,
        "prompt_version": VISUAL_UNDERSTANDING_PROMPT_VERSION,
        "model": model,
        "source_duration_ms": frame_manifest.source_duration_ms,
        "frame_manifest": frame_manifest.to_dict(),
        "regions": raw.get("regions") or (),
        "tracks": raw.get("tracks") or (),
        "scenes": raw.get("scenes") or (),
        "warnings": raw.get("warnings") or (),
    }
    try:
        understanding = VisualUnderstanding.model_validate(payload)
    except ValidationError as exc:
        raise VisualUnderstandingError("VISUAL_RESPONSE_INVALID", str(exc)[:2400]) from exc

    frames = {frame.id: frame for frame in frame_manifest.frames}
    if len(frames) != len(frame_manifest.frames):
        raise VisualUnderstandingError("VISUAL_FRAME_DUPLICATE", "frame IDs must be unique")
    scene_ids = {scene.id for scene in scene_report.scenes}
    for frame in frame_manifest.frames:
        if not 0 <= frame.timestamp_ms < frame_manifest.source_duration_ms:
            raise VisualUnderstandingError(
                "VISUAL_FRAME_TIMING_INVALID",
                f"frame {frame.id} is outside the source duration",
            )
        if frame.scene_id not in scene_ids:
            raise VisualUnderstandingError(
                "VISUAL_SCENE_UNKNOWN",
                f"frame {frame.id} references an unknown scene",
            )
    region_ids: set[str] = set()
    regions_by_id: dict[str, RegionObservation] = {}
    for region in understanding.regions:
        if region.id in region_ids:
            raise VisualUnderstandingError("VISUAL_REGION_DUPLICATE", f"duplicate region {region.id}")
        frame = frames.get(region.frame_id)
        if frame is None:
            raise VisualUnderstandingError(
                "VISUAL_FRAME_UNKNOWN",
                f"region {region.id} references an unknown frame",
            )
        if frame.scene_id not in scene_ids:
            raise VisualUnderstandingError(
                "VISUAL_SCENE_UNKNOWN",
                f"frame {frame.id} references an unknown scene",
            )
        region_ids.add(region.id)
        regions_by_id[region.id] = region

    track_ids: set[str] = set()
    for track in understanding.tracks:
        if track.id in track_ids:
            raise VisualUnderstandingError("VISUAL_TRACK_DUPLICATE", f"duplicate track {track.id}")
        if track.end_ms > frame_manifest.source_duration_ms:
            raise VisualUnderstandingError(
                "VISUAL_TRACK_TIMING_INVALID",
                f"track {track.id} exceeds the source duration",
            )
        missing = sorted(set(track.region_ids) - region_ids)
        if missing:
            raise VisualUnderstandingError(
                "VISUAL_REGION_UNKNOWN",
                f"track {track.id} references unknown regions: {', '.join(missing)}",
            )
        if any(regions_by_id[item].role != track.role for item in track.region_ids):
            raise VisualUnderstandingError(
                "VISUAL_TRACK_ROLE_INVALID",
                f"track {track.id} mixes semantic roles",
            )
        timestamps = [frames[regions_by_id[item].frame_id].timestamp_ms for item in track.region_ids]
        if any(timestamp < track.start_ms or timestamp > track.end_ms for timestamp in timestamps):
            raise VisualUnderstandingError(
                "VISUAL_TRACK_TIMING_INVALID",
                f"track {track.id} does not contain all referenced observations",
            )
        track_ids.add(track.id)

    observed_scenes: set[str] = set()
    for scene in understanding.scenes:
        if scene.scene_id in observed_scenes:
            raise VisualUnderstandingError(
                "VISUAL_SCENE_DUPLICATE",
                f"duplicate scene summary {scene.scene_id}",
            )
        if scene.scene_id not in scene_ids:
            raise VisualUnderstandingError(
                "VISUAL_SCENE_UNKNOWN",
                f"unknown scene summary {scene.scene_id}",
            )
        missing = sorted(set(scene.salient_region_ids) - region_ids)
        if missing:
            raise VisualUnderstandingError(
                "VISUAL_REGION_UNKNOWN",
                f"scene {scene.scene_id} references unknown regions: {', '.join(missing)}",
            )
        if any(
            frames[regions_by_id[item].frame_id].scene_id != scene.scene_id
            for item in scene.salient_region_ids
        ):
            raise VisualUnderstandingError(
                "VISUAL_SCENE_REGION_INVALID",
                f"scene {scene.scene_id} references regions from another scene",
            )
        observed_scenes.add(scene.scene_id)
    return understanding


class VisualUnderstandingPlanner:
    def __init__(self, client: NineRouterClient) -> None:
        self.client = client

    async def plan(
        self,
        *,
        frame_manifest: FrameManifest,
        scene_report: SceneBoundaryReport,
        editing_prompt: str,
        transcript_text: str,
    ) -> VisualUnderstanding:
        frame_order = [
            {
                "image_index": index,
                "frame_id": frame.id,
                "timestamp_ms": frame.timestamp_ms,
                "scene_id": frame.scene_id,
                "width": frame.width,
                "height": frame.height,
                "extraction_reason": frame.extraction_reason,
            }
            for index, frame in enumerate(frame_manifest.frames, start=1)
        ]
        user_payload = {
            "editing_context": _safe_text(editing_prompt, limit=12_000),
            "transcript_context": _safe_text(transcript_text, limit=24_000),
            "source_duration_ms": frame_manifest.source_duration_ms,
            "allowed_roles": [
                "speaker",
                "screen",
                "text",
                "object",
                "demonstration_target",
                "background",
            ],
            "scene_boundaries": scene_report.to_dict(),
            "attached_images_in_exact_order": frame_order,
            "output_contract": {
                "regions": [
                    "id",
                    "frame_id",
                    "role",
                    "bbox{x,y,width,height normalized 0..1}",
                    "confidence",
                    "salience",
                    "description",
                ],
                "tracks": [
                    "id",
                    "role",
                    "region_ids",
                    "start_ms",
                    "end_ms",
                    "confidence",
                    "motion",
                    "description",
                ],
                "scenes": ["scene_id", "summary", "salient_region_ids"],
                "warnings": ["bounded warning text"],
            },
        }
        response = await self.client.complete_json(
            system_prompt=VISUAL_UNDERSTANDING_SYSTEM_PROMPT,
            user_prompt=json.dumps(user_payload, ensure_ascii=False),
            image_data_urls=frame_manifest.image_data_urls,
        )
        return validate_visual_understanding(
            response,
            frame_manifest=frame_manifest,
            scene_report=scene_report,
            model=self.client.model,
        )
