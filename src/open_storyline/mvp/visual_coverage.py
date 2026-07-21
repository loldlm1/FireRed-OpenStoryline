from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from open_storyline.mvp.edit_plan import EditPlan, EditSegment
from open_storyline.mvp.frame_sampling import FrameManifest
from open_storyline.mvp.visual_understanding import (
    VisualUnderstanding,
    select_target_regions,
)


CLIP_VISUAL_COVERAGE_VERSION = "clip_visual_coverage.v1"
TRACK_CONTINUITY_MIN_CONFIDENCE = 0.8
TRACK_CONTINUITY_MAX_CENTER_SPAN_RATIO = 0.12
TRACK_CONTINUITY_MAX_SIZE_SPAN_RATIO = 0.18
TRACK_CONTINUITY_MAX_BRACKET_GAP_MS = 12_000


@dataclass(frozen=True)
class SegmentVisualCoverage:
    clip_index: int
    segment_id: str
    source_start_ms: int
    source_end_ms: int
    target_kind: str
    target_id: str
    observation_count: int
    observation_timestamps_ms: tuple[int, ...]
    temporal_coverage_ratio: float
    maximum_gap_ms: int
    track_window_covers_segment: bool
    track_confidence: float | None
    geometry_center_span_ratio: float
    geometry_size_span_ratio: float
    track_brackets_segment: bool
    track_bracket_gap_ms: int | None
    continuity_override_applied: bool
    gap_override_applied: bool
    fallback: str
    full_frame_fallback_allowed: bool
    status: str
    blocker_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["observation_timestamps_ms"] = list(self.observation_timestamps_ms)
        value["blocker_codes"] = list(self.blocker_codes)
        return value


@dataclass(frozen=True)
class ClipVisualCoverageReport:
    segments: tuple[SegmentVisualCoverage, ...]
    sample_timestamps_by_clip: dict[int, tuple[int, ...]]
    min_observations: int
    min_temporal_coverage_ratio: float
    max_observation_gap_ms: int
    repair_attempted: bool = False
    initial_blocker_codes: tuple[str, ...] = ()

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(sorted({code for item in self.segments for code in item.blocker_codes}))

    @property
    def blocking(self) -> int:
        return sum(len(item.blocker_codes) for item in self.segments)

    @property
    def affected_clip_indexes(self) -> tuple[int, ...]:
        return tuple(sorted({item.clip_index for item in self.segments if item.blocker_codes}))

    def compact_feedback(self) -> dict[str, Any]:
        return {
            "version": CLIP_VISUAL_COVERAGE_VERSION,
            "blockers": [
                {
                    "clip_index": item.clip_index,
                    "segment_id": item.segment_id,
                    "codes": list(item.blocker_codes),
                    "observation_count": item.observation_count,
                    "maximum_gap_ms": item.maximum_gap_ms,
                }
                for item in self.segments
                if item.blocker_codes
            ],
            "instruction": (
                "Choose same-window track or semantic-role evidence with sufficient "
                "temporal coverage, or use an explicitly permitted full-frame layout."
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": CLIP_VISUAL_COVERAGE_VERSION,
            "status": "blocked" if self.blocking else "ready",
            "summary": {
                "segments": len(self.segments),
                "blocking": self.blocking,
                "affected_clips": list(self.affected_clip_indexes),
            },
            "thresholds": {
                "min_observations": self.min_observations,
                "min_temporal_coverage_ratio": self.min_temporal_coverage_ratio,
                "max_observation_gap_ms": self.max_observation_gap_ms,
                "track_continuity_min_confidence": TRACK_CONTINUITY_MIN_CONFIDENCE,
                "track_continuity_max_center_span_ratio": (
                    TRACK_CONTINUITY_MAX_CENTER_SPAN_RATIO
                ),
                "track_continuity_max_size_span_ratio": (
                    TRACK_CONTINUITY_MAX_SIZE_SPAN_RATIO
                ),
                "track_continuity_max_bracket_gap_ms": (
                    TRACK_CONTINUITY_MAX_BRACKET_GAP_MS
                ),
            },
            "repair": {
                "attempted": self.repair_attempted,
                "initial_blocker_codes": list(self.initial_blocker_codes),
            },
            "clips": [
                {
                    "clip_index": clip_index,
                    "sample_timestamps_ms": list(timestamps),
                }
                for clip_index, timestamps in sorted(self.sample_timestamps_by_clip.items())
            ],
            "segments": [item.to_dict() for item in self.segments],
        }


def _target_timestamps(
    segment: EditSegment,
    visual: VisualUnderstanding,
) -> tuple[str, str, tuple[int, ...]]:
    target = segment.layout.focal_target
    if target is None:
        return "center", "", ()
    frames = {
        str(frame.get("id")): int(frame.get("timestamp_ms"))
        for frame in visual.frame_manifest.get("frames") or []
        if isinstance(frame, dict)
        and frame.get("id") is not None
        and frame.get("timestamp_ms") is not None
    }
    regions, target_kind = select_target_regions(
        visual,
        target=target,
        start_ms=segment.source_window.start_ms,
        end_ms=segment.source_window.end_ms,
    )
    target_id = (
        target.region_id
        if target_kind == "region"
        else target.track_id
        if target_kind == "track"
        else target.semantic_role
    )
    timestamps = {
        frames[region.frame_id]
        for region in regions
        if region.frame_id in frames
    }
    return target_kind, target_id, tuple(sorted(timestamps))


def _track_continuity(
    segment: EditSegment,
    visual: VisualUnderstanding,
) -> tuple[bool, float | None, float, float, bool, int | None, bool]:
    target = segment.layout.focal_target
    track = next(
        (
            item
            for item in visual.tracks
            if target is not None and target.track_id and item.id == target.track_id
        ),
        None,
    )
    if track is None:
        return False, None, 0.0, 0.0, False, None, False
    frame_times = {
        str(frame.get("id")): int(frame.get("timestamp_ms"))
        for frame in visual.frame_manifest.get("frames") or []
        if isinstance(frame, dict)
        and frame.get("id") is not None
        and frame.get("timestamp_ms") is not None
    }
    track_ids = set(track.region_ids)
    track_regions = tuple(
        region
        for region in visual.regions
        if region.id in track_ids and region.frame_id in frame_times
    )
    if not track_regions:
        return False, round(track.confidence, 6), 0.0, 0.0, False, None, False
    center_x = [region.bbox.x + region.bbox.width / 2 for region in track_regions]
    center_y = [region.bbox.y + region.bbox.height / 2 for region in track_regions]
    widths = [region.bbox.width for region in track_regions]
    heights = [region.bbox.height for region in track_regions]
    center_span = max(max(center_x) - min(center_x), max(center_y) - min(center_y))
    size_span = max(max(widths) - min(widths), max(heights) - min(heights))
    covers_segment = (
        track.start_ms <= segment.source_window.start_ms
        and track.end_ms >= segment.source_window.end_ms
    )
    stable_geometry = (
        center_span <= TRACK_CONTINUITY_MAX_CENTER_SPAN_RATIO
        and size_span <= TRACK_CONTINUITY_MAX_SIZE_SPAN_RATIO
    )
    timestamps = sorted(frame_times[region.frame_id] for region in track_regions)
    before = [value for value in timestamps if value <= segment.source_window.start_ms]
    after = [value for value in timestamps if value >= segment.source_window.end_ms]
    if not before and timestamps[0] <= segment.source_window.start_ms + 250:
        before = [track.start_ms]
    if not after and timestamps[-1] >= segment.source_window.end_ms - 250:
        after = [track.end_ms]
    bracket_gap_ms = min(after) - max(before) if before and after else None
    brackets_segment = (
        bracket_gap_ms is not None
        and 0 <= bracket_gap_ms <= TRACK_CONTINUITY_MAX_BRACKET_GAP_MS
    )
    supported = (
        covers_segment
        and track.confidence >= TRACK_CONTINUITY_MIN_CONFIDENCE
        and stable_geometry
        and brackets_segment
    )
    return (
        covers_segment,
        round(track.confidence, 6),
        round(center_span, 6),
        round(size_span, 6),
        brackets_segment,
        bracket_gap_ms,
        supported,
    )


def _maximum_gap(start_ms: int, end_ms: int, timestamps: tuple[int, ...]) -> int:
    if not timestamps:
        return end_ms - start_ms
    points = (start_ms, *timestamps, end_ms)
    return max(right - left for left, right in zip(points, points[1:]))


def build_clip_visual_coverage(
    plan: EditPlan,
    *,
    visual: VisualUnderstanding,
    clip_frame_manifests: dict[int, FrameManifest],
    min_observations: int = 2,
    min_temporal_coverage_ratio: float = 0.5,
    max_observation_gap_ms: int = 8_000,
    repair_attempted: bool = False,
    initial_blocker_codes: tuple[str, ...] = (),
) -> ClipVisualCoverageReport:
    if not 1 <= int(min_observations) <= 16:
        raise ValueError("min_observations must be between 1 and 16")
    if not 0 <= float(min_temporal_coverage_ratio) <= 1:
        raise ValueError("min_temporal_coverage_ratio must be between 0 and 1")
    if not 250 <= int(max_observation_gap_ms) <= 60_000:
        raise ValueError("max_observation_gap_ms must be between 250 and 60000")

    segment_reports: list[SegmentVisualCoverage] = []
    for clip in plan.clips:
        for segment in clip.segments:
            if segment.layout.mode != "crop":
                continue
            target_kind, target_id, timestamps = _target_timestamps(
                segment,
                visual,
            )
            duration_ms = segment.source_window.duration_ms
            coverage_ratio = (
                (timestamps[-1] - timestamps[0]) / duration_ms
                if len(timestamps) >= 2
                else 0.0
            )
            max_gap_ms = _maximum_gap(
                segment.source_window.start_ms,
                segment.source_window.end_ms,
                timestamps,
            )
            full_frame_allowed = (
                segment.layout.allow_full_frame_fallback
                and segment.layout.fallback in {"fit", "letterbox"}
            )
            (
                track_window_covers_segment,
                track_confidence,
                geometry_center_span_ratio,
                geometry_size_span_ratio,
                track_brackets_segment,
                track_bracket_gap_ms,
                track_continuity_supported,
            ) = _track_continuity(segment, visual)
            gap_override_applied = (
                target_kind in {"track", "semantic_role_fallback"}
                and len(timestamps) >= min_observations
                and coverage_ratio >= min_temporal_coverage_ratio
                and max_gap_ms > max_observation_gap_ms
                and track_continuity_supported
            )
            continuity_override_applied = (
                target_kind in {"track", "semantic_role_fallback"}
                and track_continuity_supported
                and (
                    len(timestamps) < min_observations
                    or coverage_ratio < min_temporal_coverage_ratio
                    or max_gap_ms > max_observation_gap_ms
                )
            )
            blockers: list[str] = []
            if target_kind != "center" and not full_frame_allowed:
                if not timestamps and not continuity_override_applied:
                    blockers.append("CROP_VISUAL_OBSERVATION_MISSING")
                elif (
                    len(timestamps) < min_observations
                    and not continuity_override_applied
                ):
                    blockers.append("CROP_VISUAL_OBSERVATIONS_INSUFFICIENT")
                if (
                    timestamps
                    and coverage_ratio < min_temporal_coverage_ratio
                    and not continuity_override_applied
                ):
                    blockers.append("CROP_VISUAL_TEMPORAL_COVERAGE_LOW")
                if (
                    timestamps
                    and max_gap_ms > max_observation_gap_ms
                    and not continuity_override_applied
                ):
                    blockers.append("CROP_VISUAL_GAP_TOO_LARGE")
            segment_reports.append(SegmentVisualCoverage(
                clip_index=clip.clip_index,
                segment_id=segment.id,
                source_start_ms=segment.source_window.start_ms,
                source_end_ms=segment.source_window.end_ms,
                target_kind=target_kind,
                target_id=target_id,
                observation_count=len(timestamps),
                observation_timestamps_ms=timestamps,
                temporal_coverage_ratio=round(coverage_ratio, 6),
                maximum_gap_ms=max_gap_ms,
                track_window_covers_segment=track_window_covers_segment,
                track_confidence=track_confidence,
                geometry_center_span_ratio=geometry_center_span_ratio,
                geometry_size_span_ratio=geometry_size_span_ratio,
                track_brackets_segment=track_brackets_segment,
                track_bracket_gap_ms=track_bracket_gap_ms,
                continuity_override_applied=continuity_override_applied,
                gap_override_applied=gap_override_applied,
                fallback=segment.layout.fallback,
                full_frame_fallback_allowed=full_frame_allowed,
                status="blocked" if blockers else "ready",
                blocker_codes=tuple(blockers),
            ))

    sample_timestamps = {
        int(clip_index): tuple(sorted(frame.timestamp_ms for frame in manifest.frames))
        for clip_index, manifest in clip_frame_manifests.items()
    }
    return ClipVisualCoverageReport(
        segments=tuple(segment_reports),
        sample_timestamps_by_clip=sample_timestamps,
        min_observations=int(min_observations),
        min_temporal_coverage_ratio=float(min_temporal_coverage_ratio),
        max_observation_gap_ms=int(max_observation_gap_ms),
        repair_attempted=repair_attempted,
        initial_blocker_codes=initial_blocker_codes,
    )
