from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence
import math

from open_storyline.mvp.edit_plan import ClipEditPlan, EditSegment, TimeWindow
from open_storyline.mvp.visual_understanding import (
    RegionObservation,
    VisualUnderstanding,
    select_target_regions,
)


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
CROP_TARGET_MAX_OVERFLOW_RATIO = 1.12


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
class CropGeometryAssessment:
    target_selection: str
    target_region_ids: tuple[str, ...]
    source_width: int
    source_height: int
    base_crop_width: int
    base_crop_height: int
    target_width: float
    target_height: float
    crop_width: int
    crop_height: int
    width_overflow_ratio: float
    height_overflow_ratio: float
    resolved_zoom: float
    resolved_margin: float
    focus_x: float
    focus_y: float
    fallback: str
    fallback_allowed: bool
    overflowing: bool

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "observed": "protected_target_exceeds_crop",
            "source_width": self.source_width,
            "source_height": self.source_height,
            "target_width": round(self.target_width, 3),
            "target_height": round(self.target_height, 3),
            "crop_width": self.crop_width,
            "crop_height": self.crop_height,
            "width_ratio": round(self.width_overflow_ratio, 6),
            "height_ratio": round(self.height_overflow_ratio, 6),
            "threshold": CROP_TARGET_MAX_OVERFLOW_RATIO,
            "margin_ratio": round(self.resolved_margin, 6),
            "fallback": self.fallback,
            "fallback_allowed": self.fallback_allowed,
            "repair_scope": "segment_layout_reframe",
            "target_region_ids": ":".join(self.target_region_ids)[:120],
        }


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

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["timeline_window"] = self.timeline_window.model_dump(mode="json")
        value["source_window"] = (
            self.source_window.model_dump(mode="json")
            if self.source_window is not None
            else None
        )
        return value


@dataclass(frozen=True)
class ResolvedSegment:
    id: str
    source_window: TimeWindow
    timeline_window: TimeWindow
    operation: str
    strategy: str
    crop: CropRect | None
    requested_max_zoom: float
    resolved_zoom: float
    requested_safe_margin_ratio: float
    resolved_safe_margin_ratio: float
    target_region_ids: tuple[str, ...]
    transition_kind: str
    transition_duration_ms: int
    overlays: tuple[ResolvedOverlay, ...]
    reason: str
    fallback_used: bool
    fallback_allowed: bool
    fallback_cause: str
    expected_active_area_ratio: float
    smoothed: bool
    transition_name: str = ""
    transition_color: str = "black"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_window"] = self.source_window.model_dump(mode="json")
        value["timeline_window"] = self.timeline_window.model_dump(mode="json")
        value["overlays"] = [overlay.to_dict() for overlay in self.overlays]
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


def _fit_active_area_ratio(
    source_width: int,
    source_height: int,
    output_width: int,
    output_height: int,
) -> float:
    scale = min(output_width / source_width, output_height / source_height)
    active_area = (source_width * scale) * (source_height * scale)
    return round(active_area / (output_width * output_height), 6)


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


def _zoomed_crop_dimensions(
    crop_width: int,
    crop_height: int,
    zoom: float,
) -> tuple[int, int]:
    width = max(2, int(round(crop_width / zoom)))
    height = max(2, int(round(crop_height / zoom)))
    return width - width % 2, height - height % 2


def _validate_dimensions(*values: int) -> None:
    try:
        parsed = tuple(float(value) for value in values)
    except (TypeError, ValueError, OverflowError):
        parsed = ()
    if len(parsed) != len(values) or any(
        isinstance(value, bool)
        or not math.isfinite(number)
        or not number.is_integer()
        or number < 2
        for value, number in zip(values, parsed)
    ):
        raise CompositionError(
            "COMPOSITION_CONFIG_INVALID",
            "source and output dimensions must be finite integers of at least two pixels",
        )


def _fallback_strategy(segment: EditSegment) -> str:
    if (
        segment.layout.allow_full_frame_fallback
        and segment.layout.fallback in {"fit", "letterbox"}
    ):
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


def assess_segment_crop_geometry(
    segment: EditSegment,
    *,
    visual: VisualUnderstanding,
    source_width: int,
    source_height: int,
    output_width: int,
    output_height: int,
) -> CropGeometryAssessment | None:
    if segment.layout.mode != "crop":
        return None
    _validate_dimensions(source_width, source_height, output_width, output_height)
    base_crop_width, base_crop_height = _crop_dimensions(
        source_width,
        source_height,
        output_width,
        output_height,
    )
    regions, target_selection = select_target_regions(
        visual,
        target=segment.layout.focal_target,
        start_ms=segment.source_window.start_ms,
        end_ms=segment.source_window.end_ms,
    )
    fallback = _fallback_strategy(segment)
    fallback_allowed = (
        segment.layout.allow_full_frame_fallback
        and fallback in {"fit", "letterbox"}
    )
    if not regions:
        resolved_zoom = segment.layout.max_zoom
        crop_width, crop_height = _zoomed_crop_dimensions(
            base_crop_width,
            base_crop_height,
            resolved_zoom,
        )
        return CropGeometryAssessment(
            target_selection=target_selection,
            target_region_ids=(),
            source_width=source_width,
            source_height=source_height,
            base_crop_width=base_crop_width,
            base_crop_height=base_crop_height,
            target_width=0.0,
            target_height=0.0,
            crop_width=crop_width,
            crop_height=crop_height,
            width_overflow_ratio=0.0,
            height_overflow_ratio=0.0,
            resolved_zoom=resolved_zoom,
            resolved_margin=0.0,
            focus_x=source_width / 2,
            focus_y=source_height / 2,
            fallback=fallback,
            fallback_allowed=fallback_allowed,
            overflowing=False,
        )

    raw_x1, raw_y1, raw_x2, raw_y2 = _union_box(regions, 0.0)
    raw_width = (raw_x2 - raw_x1) * source_width
    raw_height = (raw_y2 - raw_y1) * source_height
    if raw_width <= 0 or raw_height <= 0:
        raise CompositionError(
            "COMPOSITION_CONFIG_INVALID",
            "protected target geometry must have positive dimensions",
        )
    horizontal_margin = max(0.0, (base_crop_width / raw_width - 1) / 2)
    vertical_margin = max(0.0, (base_crop_height / raw_height - 1) / 2)
    resolved_margin = min(
        segment.layout.safe_margin_ratio,
        horizontal_margin,
        vertical_margin,
    )
    x1, y1, x2, y2 = _union_box(regions, resolved_margin)
    target_width = (x2 - x1) * source_width
    target_height = (y2 - y1) * source_height
    if target_width <= 0 or target_height <= 0:
        raise CompositionError(
            "COMPOSITION_CONFIG_INVALID",
            "protected target geometry must have positive dimensions",
        )
    safe_zoom = min(
        segment.layout.max_zoom,
        base_crop_width / target_width,
        base_crop_height / target_height,
    )
    resolved_zoom = max(1.0, safe_zoom)
    crop_width, crop_height = _zoomed_crop_dimensions(
        base_crop_width,
        base_crop_height,
        resolved_zoom,
    )
    width_overflow_ratio = target_width / crop_width
    height_overflow_ratio = target_height / crop_height
    return CropGeometryAssessment(
        target_selection=target_selection,
        target_region_ids=tuple(region.id for region in regions),
        source_width=source_width,
        source_height=source_height,
        base_crop_width=base_crop_width,
        base_crop_height=base_crop_height,
        target_width=target_width,
        target_height=target_height,
        crop_width=crop_width,
        crop_height=crop_height,
        width_overflow_ratio=width_overflow_ratio,
        height_overflow_ratio=height_overflow_ratio,
        resolved_zoom=resolved_zoom,
        resolved_margin=resolved_margin,
        focus_x=((x1 + x2) / 2) * source_width,
        focus_y=((y1 + y2) / 2) * source_height,
        fallback=fallback,
        fallback_allowed=fallback_allowed,
        overflowing=(
            width_overflow_ratio > CROP_TARGET_MAX_OVERFLOW_RATIO
            or height_overflow_ratio > CROP_TARGET_MAX_OVERFLOW_RATIO
        ),
    )


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
            requested_max_zoom=segment.layout.max_zoom,
            resolved_zoom=1.0,
            requested_safe_margin_ratio=segment.layout.safe_margin_ratio,
            resolved_safe_margin_ratio=0.0,
            target_region_ids=(),
            transition_kind=segment.transition_in.kind,
            transition_duration_ms=segment.transition_in.duration_ms,
            overlays=_resolve_overlays(segment),
            reason="The validated plan explicitly preserves the full source frame.",
            fallback_used=False,
            fallback_allowed=False,
            fallback_cause="explicit_layout",
            expected_active_area_ratio=_fit_active_area_ratio(
                source_width,
                source_height,
                output_width,
                output_height,
            ),
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
            requested_max_zoom=segment.layout.max_zoom,
            resolved_zoom=1.0,
            requested_safe_margin_ratio=segment.layout.safe_margin_ratio,
            resolved_safe_margin_ratio=0.0,
            target_region_ids=(),
            transition_kind=segment.transition_in.kind,
            transition_duration_ms=segment.transition_in.duration_ms,
            overlays=_resolve_overlays(segment),
            reason="The segment is an explicit source cutaway rendered full-frame.",
            fallback_used=False,
            fallback_allowed=False,
            fallback_cause="explicit_source_cutaway",
            expected_active_area_ratio=_fit_active_area_ratio(
                source_width,
                source_height,
                output_width,
                output_height,
            ),
            smoothed=False,
        )
    if segment.layout.mode != "crop":
        raise CompositionError(
            "COMPOSITION_LAYOUT_UNSUPPORTED",
            f"Sprint 4 compositor cannot execute layout {segment.layout.mode}",
        )

    assessment = assess_segment_crop_geometry(
        segment,
        visual=visual,
        source_width=source_width,
        source_height=source_height,
        output_width=output_width,
        output_height=output_height,
    )
    if assessment is None:
        raise CompositionError(
            "COMPOSITION_LAYOUT_UNSUPPORTED",
            f"Sprint 4 compositor cannot execute layout {segment.layout.mode}",
        )
    if not assessment.target_region_ids:
        crop = None
        if assessment.fallback == "crop":
            crop = _crop_at_focus(
                assessment.focus_x,
                assessment.focus_y,
                source_width=source_width,
                source_height=source_height,
                crop_width=assessment.crop_width,
                crop_height=assessment.crop_height,
            )
        return ResolvedSegment(
            id=segment.id,
            source_window=segment.source_window,
            timeline_window=segment.timeline_window,
            operation="focus_zoom" if assessment.resolved_zoom > 1 else "crop",
            strategy=assessment.fallback,
            crop=crop,
            requested_max_zoom=segment.layout.max_zoom,
            resolved_zoom=assessment.resolved_zoom,
            requested_safe_margin_ratio=segment.layout.safe_margin_ratio,
            resolved_safe_margin_ratio=0.0,
            target_region_ids=(),
            transition_kind=segment.transition_in.kind,
            transition_duration_ms=segment.transition_in.duration_ms,
            overlays=_resolve_overlays(segment),
            reason="No matching visual observation exists in this source window; explicit fallback applied.",
            fallback_used=True,
            fallback_allowed=(
                segment.layout.allow_full_frame_fallback
                if assessment.fallback in {"fit", "letterbox"}
                else True
            ),
            fallback_cause="missing_same_window_observation",
            expected_active_area_ratio=(
                1.0
                if assessment.fallback == "crop"
                else _fit_active_area_ratio(
                    source_width,
                    source_height,
                    output_width,
                    output_height,
                )
            ),
            smoothed=False,
        )

    if assessment.overflowing:
        if assessment.fallback == "crop":
            raise CompositionError(
                "COMPOSITION_CROP_TARGET_TOO_WIDE",
                "the protected target cannot fit the portrait crop without an approved full-frame fallback",
            )
        return ResolvedSegment(
            id=segment.id,
            source_window=segment.source_window,
            timeline_window=segment.timeline_window,
            operation="focus_zoom" if assessment.resolved_zoom > 1 else "crop",
            strategy=assessment.fallback,
            crop=None,
            requested_max_zoom=segment.layout.max_zoom,
            resolved_zoom=assessment.resolved_zoom,
            requested_safe_margin_ratio=segment.layout.safe_margin_ratio,
            resolved_safe_margin_ratio=assessment.resolved_margin,
            target_region_ids=assessment.target_region_ids,
            transition_kind=segment.transition_in.kind,
            transition_duration_ms=segment.transition_in.duration_ms,
            overlays=_resolve_overlays(segment),
            reason="The protected visual union is wider than a safe portrait crop; full-frame fallback applied.",
            fallback_used=True,
            fallback_allowed=segment.layout.allow_full_frame_fallback,
            fallback_cause="protected_target_exceeds_crop",
            expected_active_area_ratio=_fit_active_area_ratio(
                source_width,
                source_height,
                output_width,
                output_height,
            ),
            smoothed=False,
        )
    return ResolvedSegment(
        id=segment.id,
        source_window=segment.source_window,
        timeline_window=segment.timeline_window,
        operation="focus_zoom" if assessment.resolved_zoom > 1 else "crop",
        strategy="crop",
        crop=_crop_at_focus(
            assessment.focus_x,
            assessment.focus_y,
            source_width=source_width,
            source_height=source_height,
            crop_width=assessment.crop_width,
            crop_height=assessment.crop_height,
        ),
        requested_max_zoom=segment.layout.max_zoom,
        resolved_zoom=assessment.resolved_zoom,
        requested_safe_margin_ratio=segment.layout.safe_margin_ratio,
        resolved_safe_margin_ratio=assessment.resolved_margin,
        target_region_ids=assessment.target_region_ids,
        transition_kind=segment.transition_in.kind,
        transition_duration_ms=segment.transition_in.duration_ms,
        overlays=_resolve_overlays(segment),
        reason=(
            "Portrait crop uses the explicit semantic-role fallback because it "
            "has stronger same-window temporal evidence."
            if assessment.target_selection == "semantic_role_fallback"
            else
            "Focus zoom is centered on the validated semantic target union."
            if segment.layout.max_zoom > 1
            else "Portrait crop is centered on the validated semantic target union."
        ),
        fallback_used=False,
        fallback_allowed=False,
        fallback_cause="",
        expected_active_area_ratio=1.0,
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
    transition_presets: Mapping[str, Mapping[str, Any]] | None = None,
) -> ClipComposition:
    values = (hysteresis_ratio, smoothing_alpha, max_crop_velocity_ratio_per_second)
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise CompositionError("COMPOSITION_CONFIG_INVALID", "smoothing values must be finite and non-negative")
    if not 0 <= smoothing_alpha <= 1 or hysteresis_ratio > 0.25 or max_crop_velocity_ratio_per_second > 2:
        raise CompositionError("COMPOSITION_CONFIG_INVALID", "smoothing values exceed safe bounds")
    presets = transition_presets or {}
    resolved_items = []
    for segment in clip.segments:
        resolved_segment = _resolve_segment(
            segment,
            visual=visual,
            source_width=source_media.width,
            source_height=source_media.height,
            output_width=output_width,
            output_height=output_height,
        )
        preset = presets.get(segment.transition_in.catalog_id) or {}
        default_name = "hard_cut" if segment.transition_in.kind == "cut" else "fade"
        resolved_items.append(replace(
            resolved_segment,
            transition_name=str(preset.get("operation") or default_name),
            transition_color=str(preset.get("color") or "black"),
        ))
    resolved = tuple(resolved_items)
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
