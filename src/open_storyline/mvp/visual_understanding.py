from __future__ import annotations

from dataclasses import replace
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
from open_storyline.mvp.structured_outputs import VISUAL_UNDERSTANDING_SCHEMA


VISUAL_UNDERSTANDING_VERSION = "visual_understanding.v1"
MOTION_VALUES = frozenset({"static", "low", "medium", "high", "unknown"})
SEMANTIC_ROLES = (
    "speaker",
    "screen",
    "text",
    "object",
    "demonstration_target",
    "background",
)

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


def select_target_regions(
    visual: VisualUnderstanding,
    *,
    target: Any,
    start_ms: int,
    end_ms: int,
) -> tuple[tuple[RegionObservation, ...], str]:
    if target is None:
        return (), "center"
    frame_times = {
        str(frame.get("id")): int(frame.get("timestamp_ms"))
        for frame in visual.frame_manifest.get("frames") or []
        if isinstance(frame, dict)
        and frame.get("id") is not None
        and frame.get("timestamp_ms") is not None
    }
    in_window = tuple(
        region
        for region in visual.regions
        if region.frame_id in frame_times
        and start_ms <= frame_times[region.frame_id] < end_ms
    )
    if target.region_id:
        return (
            tuple(region for region in in_window if region.id == target.region_id),
            "region",
        )

    track_regions: tuple[RegionObservation, ...] = ()
    if target.track_id:
        track = next(
            (item for item in visual.tracks if item.id == target.track_id),
            None,
        )
        if track is not None:
            track_ids = set(track.region_ids)
            track_regions = tuple(
                region for region in in_window if region.id in track_ids
            )
    semantic_regions = (
        tuple(region for region in in_window if region.role == target.semantic_role)
        if target.semantic_role
        else ()
    )
    if target.track_id and semantic_regions:
        def temporal_score(regions: tuple[RegionObservation, ...]) -> tuple[int, int]:
            timestamps = sorted({frame_times[region.frame_id] for region in regions})
            return (
                len(timestamps),
                timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else 0,
            )

        if temporal_score(semantic_regions) > temporal_score(track_regions):
            return semantic_regions, "semantic_role_fallback"
    if target.track_id:
        return track_regions, "track"
    if target.semantic_role:
        return semantic_regions, "semantic_role"
    return (), "center"


def scope_visual_understanding(
    understanding: VisualUnderstanding,
    *,
    clip_index: int,
) -> VisualUnderstanding:
    prefix = f"clip-{int(clip_index):02d}"
    raw_frames = list(understanding.frame_manifest.get("frames") or [])
    frame_ids = {
        str(frame.get("id")): f"{prefix}-frame-{index:03d}"
        for index, frame in enumerate(raw_frames, start=1)
        if isinstance(frame, dict) and frame.get("id")
    }
    scoped_frames = [
        {**frame, "id": frame_ids.get(str(frame.get("id")), str(frame.get("id")))}
        for frame in raw_frames
        if isinstance(frame, dict)
    ]
    region_ids = {
        region.id: f"{prefix}-region-{index:03d}"
        for index, region in enumerate(understanding.regions, start=1)
    }
    track_ids = {
        track.id: f"{prefix}-track-{index:03d}"
        for index, track in enumerate(understanding.tracks, start=1)
    }
    regions = tuple(
        region.model_copy(update={
            "id": region_ids[region.id],
            "frame_id": frame_ids.get(region.frame_id, region.frame_id),
        })
        for region in understanding.regions
    )
    tracks = tuple(
        track.model_copy(update={
            "id": track_ids[track.id],
            "region_ids": tuple(region_ids[item] for item in track.region_ids),
        })
        for track in understanding.tracks
    )
    scenes = tuple(
        scene.model_copy(update={
            "salient_region_ids": tuple(
                region_ids[item]
                for item in scene.salient_region_ids
                if item in region_ids
            ),
        })
        for scene in understanding.scenes
    )
    manifest = {
        **understanding.frame_manifest,
        "frame_count": len(scoped_frames),
        "frames": scoped_frames,
        "scope": {"kind": "selected_clip", "clip_index": int(clip_index)},
    }
    return understanding.model_copy(update={
        "frame_manifest": manifest,
        "regions": regions,
        "tracks": tracks,
        "scenes": scenes,
    })


def merge_visual_understandings(
    global_understanding: VisualUnderstanding,
    clip_understandings: tuple[VisualUnderstanding, ...],
) -> VisualUnderstanding:
    frame_manifest = dict(global_understanding.frame_manifest)
    frames = list(frame_manifest.get("frames") or [])
    regions = list(global_understanding.regions)
    tracks = list(global_understanding.tracks)
    warnings = list(global_understanding.warnings)
    for understanding in clip_understandings:
        frames.extend(understanding.frame_manifest.get("frames") or [])
        regions.extend(understanding.regions)
        tracks.extend(understanding.tracks)
        warnings.extend(understanding.warnings)
    frame_ids = [str(frame.get("id")) for frame in frames if isinstance(frame, dict)]
    region_ids = [region.id for region in regions]
    track_ids = [track.id for track in tracks]
    if (
        len(frame_ids) != len(set(frame_ids))
        or len(region_ids) != len(set(region_ids))
        or len(track_ids) != len(set(track_ids))
    ):
        raise VisualUnderstandingError(
            "VISUAL_SCOPE_COLLISION",
            "clip-local visual IDs must be unique",
        )
    frame_manifest.update({
        "frame_count": len(frames),
        "frames": frames,
        "global_frame_count": len(global_understanding.frame_manifest.get("frames") or []),
        "clip_local_frame_count": len(frames) - len(global_understanding.frame_manifest.get("frames") or []),
    })
    return global_understanding.model_copy(update={
        "frame_manifest": frame_manifest,
        "regions": tuple(regions),
        "tracks": tuple(tracks),
        "warnings": tuple(warnings[:64]),
    })


def _normalize_tracks(
    raw_tracks: Any,
    *,
    raw_regions: Any,
    frame_manifest: FrameManifest,
    scene_report: SceneBoundaryReport,
) -> tuple[Any, int, int, int]:
    if not isinstance(raw_tracks, (list, tuple)):
        return raw_tracks, 0, 0, 0
    frames = {frame.id: frame for frame in frame_manifest.frames}
    scenes = {scene.id: scene for scene in scene_report.scenes}
    region_frames: dict[str, Any] = {}
    region_roles: dict[str, str] = {}
    if isinstance(raw_regions, (list, tuple)):
        for region in raw_regions:
            if not isinstance(region, dict):
                continue
            region_id = region.get("id")
            frame = frames.get(region.get("frame_id"))
            if isinstance(region_id, str) and frame is not None:
                region_frames[region_id] = frame
            role = region.get("role")
            if isinstance(region_id, str) and role in SEMANTIC_ROLES:
                region_roles[region_id] = role
    normalized_tracks: list[Any] = []
    normalized_motion_count = 0
    normalized_timing_count = 0
    normalized_role_count = 0
    for item in raw_tracks:
        if not isinstance(item, dict):
            normalized_tracks.append(item)
            continue
        track = dict(item)
        raw_motion = track.get("motion", "unknown")
        motion = raw_motion.strip().lower() if isinstance(raw_motion, str) else ""
        if motion not in MOTION_VALUES:
            motion = "unknown"
            normalized_motion_count += 1
        track["motion"] = motion
        region_ids = track.get("region_ids")
        referenced_roles = (
            [region_roles.get(region_id) for region_id in region_ids]
            if isinstance(region_ids, (list, tuple)) and region_ids
            else []
        )
        if (
            referenced_roles
            and all(role is not None for role in referenced_roles)
            and len(set(referenced_roles)) == 1
            and track.get("role") != referenced_roles[0]
        ):
            track["role"] = referenced_roles[0]
            normalized_role_count += 1
        referenced_frames = (
            [region_frames.get(region_id) for region_id in region_ids]
            if isinstance(region_ids, (list, tuple)) and region_ids
            else []
        )
        if referenced_frames and all(frame is not None for frame in referenced_frames):
            start_ms = track.get("start_ms")
            end_ms = track.get("end_ms")
            timestamps = [frame.timestamp_ms for frame in referenced_frames]
            invalid_window = (
                not isinstance(start_ms, int)
                or isinstance(start_ms, bool)
                or not isinstance(end_ms, int)
                or isinstance(end_ms, bool)
                or start_ms < 0
                or end_ms <= start_ms
                or end_ms > frame_manifest.source_duration_ms
                or any(timestamp < start_ms or timestamp > end_ms for timestamp in timestamps)
            )
            referenced_scenes = [scenes.get(frame.scene_id) for frame in referenced_frames]
            if invalid_window and all(scene is not None for scene in referenced_scenes):
                track["start_ms"] = min(scene.start_ms for scene in referenced_scenes)
                track["end_ms"] = max(scene.end_ms for scene in referenced_scenes)
                normalized_timing_count += 1
        normalized_tracks.append(track)
    return (
        normalized_tracks,
        normalized_motion_count,
        normalized_timing_count,
        normalized_role_count,
    )


def _normalize_scenes(
    raw_scenes: Any,
    *,
    raw_regions: Any,
    frame_manifest: FrameManifest,
) -> tuple[Any, int]:
    if not isinstance(raw_scenes, (list, tuple)):
        return raw_scenes, 0
    frames = {frame.id: frame for frame in frame_manifest.frames}
    region_scenes: dict[str, str] = {}
    if isinstance(raw_regions, (list, tuple)):
        for region in raw_regions:
            if not isinstance(region, dict):
                continue
            region_id = region.get("id")
            frame = frames.get(region.get("frame_id"))
            if isinstance(region_id, str) and frame is not None:
                region_scenes[region_id] = frame.scene_id
    normalized_scenes: list[Any] = []
    removed_count = 0
    for item in raw_scenes:
        if not isinstance(item, dict):
            normalized_scenes.append(item)
            continue
        scene = dict(item)
        salient_region_ids = scene.get("salient_region_ids")
        if isinstance(salient_region_ids, (list, tuple)):
            filtered = [
                region_id
                for region_id in salient_region_ids
                if region_scenes.get(region_id) == scene.get("scene_id")
            ]
            removed_count += len(salient_region_ids) - len(filtered)
            scene["salient_region_ids"] = filtered
        normalized_scenes.append(scene)
    return normalized_scenes, removed_count


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
    raw_regions = raw.get("regions") or ()
    (
        tracks,
        normalized_motion_count,
        normalized_timing_count,
        normalized_role_count,
    ) = _normalize_tracks(
        raw.get("tracks") or (),
        raw_regions=raw_regions,
        frame_manifest=frame_manifest,
        scene_report=scene_report,
    )
    scenes, removed_scene_region_count = _normalize_scenes(
        raw.get("scenes") or (),
        raw_regions=raw_regions,
        frame_manifest=frame_manifest,
    )
    warnings = raw.get("warnings") or ()
    if isinstance(warnings, (list, tuple)):
        normalization_warnings = []
        if normalized_motion_count:
            normalization_warnings.append(
                f"Normalized unsupported motion values for {normalized_motion_count} track(s)."
            )
        if normalized_timing_count:
            normalization_warnings.append(
                f"Normalized invalid timing windows for {normalized_timing_count} track(s)."
            )
        if normalized_role_count:
            normalization_warnings.append(
                f"Aligned semantic roles for {normalized_role_count} track(s) "
                "with unanimous referenced observations."
            )
        if removed_scene_region_count:
            normalization_warnings.append(
                "Removed "
                f"{removed_scene_region_count} invalid salient region reference(s) "
                "from scene summaries."
            )
        warnings = [*warnings, *normalization_warnings][:64]
    payload = {
        "version": VISUAL_UNDERSTANDING_VERSION,
        "prompt_version": VISUAL_UNDERSTANDING_PROMPT_VERSION,
        "model": model,
        "source_duration_ms": frame_manifest.source_duration_ms,
        "frame_manifest": frame_manifest.to_dict(),
        "regions": raw_regions,
        "tracks": tracks,
        "scenes": scenes,
        "warnings": warnings,
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
        if region.id in region_ids or region.id in frames or region.id in scene_ids or region.id.startswith("transcript-"):
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
        if (
            track.id in track_ids
            or track.id in region_ids
            or track.id in frames
            or track.id in scene_ids
            or track.id.startswith("transcript-")
        ):
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
            "allowed_roles": list(SEMANTIC_ROLES),
            "allowed_motion_values": sorted(MOTION_VALUES),
            "track_timing_constraints": [
                "start_ms and end_ms are integer source timestamps",
                "0 <= start_ms < end_ms <= source_duration_ms",
                "the window contains every frame referenced through region_ids",
            ],
            "scene_region_constraints": [
                "salient_region_ids must contain only known region IDs",
                "every salient region must come from a frame in that same scene_id",
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
        response = await self.client.complete_structured(
            schema_name=VISUAL_UNDERSTANDING_SCHEMA,
            system_prompt=VISUAL_UNDERSTANDING_SYSTEM_PROMPT,
            user_prompt=json.dumps(user_payload, ensure_ascii=False),
            image_data_urls=frame_manifest.image_data_urls,
        )
        first_attempts = tuple(getattr(self.client, "last_attempts", ()))
        try:
            return validate_visual_understanding(
                response,
                frame_manifest=frame_manifest,
                scene_report=scene_report,
                model=self.client.model,
            )
        except VisualUnderstandingError as exc:
            repair_payload = {
                **user_payload,
                "repair_feedback": {
                    "error_code": exc.code,
                    "instruction": (
                        "Return a complete replacement object that satisfies every "
                        "listed constraint. Each track may reference only regions that "
                        "all share exactly the track role; split mixed-role tracks or "
                        "omit them without relabeling valid region observations."
                    ),
                },
            }
            repaired = await self.client.complete_structured(
                schema_name=VISUAL_UNDERSTANDING_SCHEMA,
                system_prompt=VISUAL_UNDERSTANDING_SYSTEM_PROMPT,
                user_prompt=json.dumps(repair_payload, ensure_ascii=False),
                image_data_urls=frame_manifest.image_data_urls,
            )
            second_attempts = tuple(getattr(self.client, "last_attempts", ()))
            if first_attempts or second_attempts:
                self.client.last_attempts = tuple(
                    replace(item, number=index)
                    for index, item in enumerate(
                        (*first_attempts, *second_attempts),
                        start=1,
                    )
                )
            return validate_visual_understanding(
                repaired,
                frame_manifest=frame_manifest,
                scene_report=scene_report,
                model=self.client.model,
            )
