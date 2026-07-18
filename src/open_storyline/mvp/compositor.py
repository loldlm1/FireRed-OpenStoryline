from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence
import math

from open_storyline.mvp.edit_plan import ClipEditPlan, EditSegment, TimeWindow
from open_storyline.mvp.visual_understanding import RegionObservation, VisualUnderstanding


REFRAME_RENDER_CAPABILITIES = frozenset({
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
RENDER_EXECUTION_VERSION = "render_execution.v1"


class CompositionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class CropRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ResolvedOverlay:
    id: str
    kind: str
    timeline_window: TimeWindow
    source_window: TimeWindow | None
    text: str
    asset_id: str
    opacity: float
    width_ratio: float
    margin_ratio: float
    transition_ms: int
    z_index: int
    position: str


@dataclass(frozen=True)
class ResolvedSegment:
    id: str
    source_window: TimeWindow
    timeline_window: TimeWindow
    operation: str
    strategy: str
    crop: CropRect | None
    target_region_ids: tuple[str, ...]
    transition_kind: str
    transition_duration_ms: int
    overlays: tuple[ResolvedOverlay, ...]
    reason: str
    fallback_used: bool
    smoothed: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_window"] = self.source_window.model_dump(mode="json")
        value["timeline_window"] = self.timeline_window.model_dump(mode="json")
        return value


@dataclass(frozen=True)
class ClipComposition:
    clip_index: int
    output_name: str
    segments: tuple[ResolvedSegment, ...]

    @property
    def fallback_count(self) -> int:
        return sum(1 for segment in self.segments if segment.fallback_used)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_index": self.clip_index,
            "output_name": self.output_name,
            "fallback_count": self.fallback_count,
            "segments": [segment.to_dict() for segment in self.segments],
        }


def _crop_dimensions(source_width: int, source_height: int, output_width: int, output_height: int) -> tuple[int, int]:
    source_ratio = source_width / source_height
    output_ratio = output_width / output_height
    if source_ratio > output_ratio:
        crop_width, crop_height = int(round(source_height * output_ratio)), source_height
    else:
        crop_width, crop_height = source_width, int(round(source_width / output_ratio))
    crop_width = max(2, min(source_width, crop_width - crop_width % 2))
    crop_height = max(2, min(source_height, crop_height - crop_height % 2))
    return crop_width, crop_height


def _frame_times(visual: VisualUnderstanding) -> dict[str, int]:
    return {
        str(frame["id"]): int(frame["timestamp_ms"])
        for frame in visual.frame_manifest.get("frames") or []
        if isinstance(frame, dict) and frame.get("id") is not None
    }


def _target_regions(segment: EditSegment, visual: VisualUnderstanding) -> list[RegionObservation]:
    target = segment.layout.focal_target
    if target is None:
        return []
    region_ids: set[str] = set()
    if target.region_id:
        region_ids.add(target.region_id)
    if target.track_id:
        track = next((item for item in visual.tracks if item.id == target.track_id), None)
        if track is not None:
            region_ids.update(track.region_ids)
    frame_times = _frame_times(visual)
    regions = []
    for region in visual.regions:
        timestamp = frame_times.get(region.frame_id)
        if timestamp is None or not (
            segment.source_window.start_ms <= timestamp < segment.source_window.end_ms
        ):
            continue
        if region_ids and region.id not in region_ids:
            continue
        if not region_ids and target.semantic_role and region.role != target.semantic_role:
            continue
        regions.append(region)
    return regions


def _union_box(regions: Sequence[RegionObservation], margin_ratio: float) -> tuple[float, float, float, float]:
    x1 = min(region.bbox.x for region in regions)
    y1 = min(region.bbox.y for region in regions)
    x2 = max(region.bbox.x + region.bbox.width for region in regions)
    y2 = max(region.bbox.y + region.bbox.height for region in regions)
    margin_x = (x2 - x1) * margin_ratio
    margin_y = (y2 - y1) * margin_ratio
    return (
        max(0.0, x1 - margin_x),
        max(0.0, y1 - margin_y),
        min(1.0, x2 + margin_x),
        min(1.0, y2 + margin_y),
    )


def _crop_at_focus(
    focus_x: float,
    focus_y: float,
    *,
    source_width: int,
    source_height: int,
    crop_width: int,
    crop_height: int,
) -> CropRect:
    x = int(round(focus_x - crop_width / 2))
    y = int(round(focus_y - crop_height / 2))
    x = max(0, min(source_width - crop_width, x))
    y = max(0, min(source_height - crop_height, y))
    x -= x % 2
    y -= y % 2
    return CropRect(x=x, y=y, width=crop_width, height=crop_height)


def _fallback_strategy(segment: EditSegment) -> str:
    if segment.layout.fallback in {"fit", "letterbox"}:
        return segment.layout.fallback
    return "crop"


def _resolve_overlays(segment: EditSegment) -> tuple[ResolvedOverlay, ...]:
    overlays = [
        ResolvedOverlay(
            id=overlay.id,
            kind=overlay.kind,
            timeline_window=overlay.timeline_window,
            source_window=overlay.source_window,
            text=overlay.text,
            asset_id=overlay.asset_id,
            opacity=overlay.opacity,
            width_ratio=overlay.width_ratio,
            margin_ratio=overlay.margin_ratio,
            transition_ms=overlay.transition_ms,
            z_index=overlay.z_index,
            position=overlay.position,
        )
        for overlay in segment.overlays
    ]
    return tuple(sorted(overlays, key=lambda item: (item.z_index, item.id)))


def _resolve_segment(
    segment: EditSegment,
    *,
    visual: VisualUnderstanding,
    source_width: int,
    source_height: int,
    output_width: int,
    output_height: int,
) -> ResolvedSegment:
    if segment.layout.mode in {"fit", "letterbox"}:
        return ResolvedSegment(
            id=segment.id,
            source_window=segment.source_window,
            timeline_window=segment.timeline_window,
            operation=segment.layout.mode,
            strategy=segment.layout.mode,
            crop=None,
            target_region_ids=(),
            transition_kind=segment.transition_in.kind,
            transition_duration_ms=segment.transition_in.duration_ms,
            overlays=_resolve_overlays(segment),
            reason="The validated plan explicitly preserves the full source frame.",
            fallback_used=False,
            smoothed=False,
        )
    if segment.layout.mode == "source":
        return ResolvedSegment(
            id=segment.id,
            source_window=segment.source_window,
            timeline_window=segment.timeline_window,
            operation="source_cutaway",
            strategy="fit",
            crop=None,
            target_region_ids=(),
            transition_kind=segment.transition_in.kind,
            transition_duration_ms=segment.transition_in.duration_ms,
            overlays=_resolve_overlays(segment),
            reason="The segment is an explicit source cutaway rendered full-frame.",
            fallback_used=False,
            smoothed=False,
        )
    if segment.layout.mode != "crop":
        raise CompositionError(
            "COMPOSITION_LAYOUT_UNSUPPORTED",
            f"Sprint 4 compositor cannot execute layout {segment.layout.mode}",
        )

    crop_width, crop_height = _crop_dimensions(
        source_width,
        source_height,
        output_width,
        output_height,
    )
    if segment.layout.max_zoom > 1:
        crop_width = max(2, int(round(crop_width / segment.layout.max_zoom)))
        crop_height = max(2, int(round(crop_height / segment.layout.max_zoom)))
        crop_width -= crop_width % 2
        crop_height -= crop_height % 2
    regions = _target_regions(segment, visual)
    if not regions:
        fallback = _fallback_strategy(segment)
        crop = None
        if fallback == "crop":
            crop = _crop_at_focus(
                source_width / 2,
                source_height / 2,
                source_width=source_width,
                source_height=source_height,
                crop_width=crop_width,
                crop_height=crop_height,
            )
        return ResolvedSegment(
            id=segment.id,
            source_window=segment.source_window,
            timeline_window=segment.timeline_window,
            operation="focus_zoom" if segment.layout.max_zoom > 1 else "crop",
            strategy=fallback,
            crop=crop,
            target_region_ids=(),
            transition_kind=segment.transition_in.kind,
            transition_duration_ms=segment.transition_in.duration_ms,
            overlays=_resolve_overlays(segment),
            reason="No matching visual observation exists in this source window; explicit fallback applied.",
            fallback_used=True,
            smoothed=False,
        )

    x1, y1, x2, y2 = _union_box(regions, segment.layout.safe_margin_ratio)
    target_width = (x2 - x1) * source_width
    target_height = (y2 - y1) * source_height
    if target_width > crop_width * 1.05 or target_height > crop_height * 1.05:
        fallback = _fallback_strategy(segment)
        if fallback == "crop":
            fallback = "letterbox"
        return ResolvedSegment(
            id=segment.id,
            source_window=segment.source_window,
            timeline_window=segment.timeline_window,
            operation="focus_zoom" if segment.layout.max_zoom > 1 else "crop",
            strategy=fallback,
            crop=None,
            target_region_ids=tuple(region.id for region in regions),
            transition_kind=segment.transition_in.kind,
            transition_duration_ms=segment.transition_in.duration_ms,
            overlays=_resolve_overlays(segment),
            reason="The protected visual union is wider than a safe portrait crop; full-frame fallback applied.",
            fallback_used=True,
            smoothed=False,
        )
    focus_x = ((x1 + x2) / 2) * source_width
    focus_y = ((y1 + y2) / 2) * source_height
    return ResolvedSegment(
        id=segment.id,
        source_window=segment.source_window,
        timeline_window=segment.timeline_window,
        operation="focus_zoom" if segment.layout.max_zoom > 1 else "crop",
        strategy="crop",
        crop=_crop_at_focus(
            focus_x,
            focus_y,
            source_width=source_width,
            source_height=source_height,
            crop_width=crop_width,
            crop_height=crop_height,
        ),
        target_region_ids=tuple(region.id for region in regions),
        transition_kind=segment.transition_in.kind,
        transition_duration_ms=segment.transition_in.duration_ms,
        overlays=_resolve_overlays(segment),
        reason=(
            "Focus zoom is centered on the validated semantic target union."
            if segment.layout.max_zoom > 1
            else "Portrait crop is centered on the validated semantic target union."
        ),
        fallback_used=False,
        smoothed=False,
    )


def _smooth_crops(
    segments: Sequence[ResolvedSegment],
    *,
    source_width: int,
    source_height: int,
    hysteresis_ratio: float,
    smoothing_alpha: float,
    max_crop_velocity_ratio_per_second: float,
) -> tuple[ResolvedSegment, ...]:
    smoothed: list[ResolvedSegment] = []
    previous: ResolvedSegment | None = None
    for segment in segments:
        if (
            previous is None
            or previous.strategy != "crop"
            or segment.strategy != "crop"
            or previous.crop is None
            or segment.crop is None
        ):
            smoothed.append(segment)
            previous = segment
            continue
        delta_x = segment.crop.x - previous.crop.x
        delta_y = segment.crop.y - previous.crop.y
        if (
            abs(delta_x) <= source_width * hysteresis_ratio
            and abs(delta_y) <= source_height * hysteresis_ratio
        ):
            crop = CropRect(
                x=previous.crop.x,
                y=previous.crop.y,
                width=segment.crop.width,
                height=segment.crop.height,
            )
        else:
            previous_center = (
                previous.timeline_window.start_ms + previous.timeline_window.end_ms
            ) / 2
            current_center = (
                segment.timeline_window.start_ms + segment.timeline_window.end_ms
            ) / 2
            elapsed_seconds = max(0.1, (current_center - previous_center) / 1000)
            max_x = source_width * max_crop_velocity_ratio_per_second * elapsed_seconds
            max_y = source_height * max_crop_velocity_ratio_per_second * elapsed_seconds
            move_x = max(-max_x, min(max_x, delta_x * smoothing_alpha))
            move_y = max(-max_y, min(max_y, delta_y * smoothing_alpha))
            x = int(round(previous.crop.x + move_x))
            y = int(round(previous.crop.y + move_y))
            x = max(0, min(source_width - segment.crop.width, x))
            y = max(0, min(source_height - segment.crop.height, y))
            crop = CropRect(
                x=x - x % 2,
                y=y - y % 2,
                width=segment.crop.width,
                height=segment.crop.height,
            )
        updated = ResolvedSegment(
            **{
                **segment.__dict__,
                "crop": crop,
                "smoothed": crop != segment.crop,
            }
        )
        smoothed.append(updated)
        previous = updated
    return tuple(smoothed)


def resolve_clip_composition(
    clip: ClipEditPlan,
    *,
    visual: VisualUnderstanding,
    source_media: Any,
    output_width: int,
    output_height: int,
    hysteresis_ratio: float = 0.03,
    smoothing_alpha: float = 0.65,
    max_crop_velocity_ratio_per_second: float = 0.45,
) -> ClipComposition:
    values = (hysteresis_ratio, smoothing_alpha, max_crop_velocity_ratio_per_second)
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise CompositionError("COMPOSITION_CONFIG_INVALID", "smoothing values must be finite and non-negative")
    if not 0 <= smoothing_alpha <= 1 or hysteresis_ratio > 0.25 or max_crop_velocity_ratio_per_second > 2:
        raise CompositionError("COMPOSITION_CONFIG_INVALID", "smoothing values exceed safe bounds")
    resolved = tuple(
        _resolve_segment(
            segment,
            visual=visual,
            source_width=source_media.width,
            source_height=source_media.height,
            output_width=output_width,
            output_height=output_height,
        )
        for segment in clip.segments
    )
    return ClipComposition(
        clip_index=clip.clip_index,
        output_name=clip.output_name,
        segments=_smooth_crops(
            resolved,
            source_width=source_media.width,
            source_height=source_media.height,
            hysteresis_ratio=hysteresis_ratio,
            smoothing_alpha=smoothing_alpha,
            max_crop_velocity_ratio_per_second=max_crop_velocity_ratio_per_second,
        ),
    )
