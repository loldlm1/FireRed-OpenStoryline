from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable
import os

from open_storyline.mvp.edit_plan import EditPlan, required_capabilities
from open_storyline.mvp.visual_coverage import ClipVisualCoverageReport


FALLBACK_LEDGER_VERSION = "fallback_ledger.v1"
BASELINE_COMPILER_VERSION = "baseline_compiler.v1"


class FallbackConfigurationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class FallbackEntry:
    code: str
    clip_index: int
    segment_id: str
    requested: str
    executed: str
    reason: str
    retryable: bool = True
    retry_action: str = "retry_defects"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompilationResult:
    plan: EditPlan
    entries: tuple[FallbackEntry, ...]

    @property
    def limited(self) -> bool:
        return bool(self.entries)

    def ledger(self) -> dict[str, Any]:
        return {
            "version": FALLBACK_LEDGER_VERSION,
            "compiler_version": BASELINE_COMPILER_VERSION,
            "status": "with_limitations" if self.entries else "unchanged",
            "summary": {
                "fallbacks": len(self.entries),
                "codes": sorted({entry.code for entry in self.entries}),
            },
            "entries": [entry.to_dict() for entry in self.entries],
        }


def baseline_fallbacks_enabled(config: Any) -> bool:
    raw = os.getenv("OPENSTORYLINE_BASELINE_FALLBACKS_ENABLED")
    if raw is None:
        return bool(getattr(config, "baseline_fallbacks_enabled", False))
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise FallbackConfigurationError(
        "BASELINE_FALLBACK_CONFIG_INVALID",
        "OPENSTORYLINE_BASELINE_FALLBACKS_ENABLED must be true or false",
    )


def _coverage_blockers(
    report: ClipVisualCoverageReport | None,
) -> dict[tuple[int, str], tuple[str, ...]]:
    if report is None:
        return {}
    return {
        (segment.clip_index, segment.segment_id): segment.blocker_codes
        for segment in report.segments
        if segment.blocker_codes
    }


def _required_capabilities(payload: dict[str, Any]) -> tuple[str, ...]:
    candidate = EditPlan.model_validate(
        {
            **payload,
            "requested_capabilities": payload.get("requested_capabilities") or (),
        }
    )
    return tuple(sorted(required_capabilities(candidate)))


def compile_baseline_plan(
    plan: EditPlan,
    *,
    visual_coverage: ClipVisualCoverageReport | None = None,
    available_capabilities: Iterable[str] = (),
    omitted_asset_ids: Iterable[str] = (),
    force_minimal: bool = False,
    cause_code: str = "",
) -> CompilationResult:
    payload = plan.to_dict()
    available = {str(value) for value in available_capabilities}
    omitted_assets = {str(value) for value in omitted_asset_ids}
    blockers = _coverage_blockers(visual_coverage)
    entries: list[FallbackEntry] = []

    for clip in payload["clips"]:
        clip_index = int(clip["clip_index"])
        if force_minimal:
            source_window = dict(clip["source_window"])
            duration_ms = int(source_window["end_ms"]) - int(source_window["start_ms"])
            previous_segments = list(clip["segments"])
            clip["segments"] = [{
                "id": f"baseline-{clip_index:02d}",
                "source_window": source_window,
                "timeline_window": {"start_ms": 0, "end_ms": duration_ms},
                "layout": {
                    "mode": "fit",
                    "focal_target": None,
                    "fallback": "fit",
                    "allow_full_frame_fallback": True,
                    "safe_margin_ratio": 0.08,
                    "max_zoom": 1.0,
                },
                "transition_in": {"kind": "cut", "duration_ms": 0},
                "overlays": [],
                "reason": "Deterministic content-preserving baseline after FFmpeg preflight failure.",
                "evidence_ids": [],
            }]
            clip["asset_requests"] = []
            entries.append(FallbackEntry(
                code="RENDER_PREFLIGHT_FALLBACK",
                clip_index=clip_index,
                segment_id=str(previous_segments[0].get("id") or "clip"),
                requested="compiled_agentic_filtergraph",
                executed="single_segment_full_frame_fit",
                reason=str(cause_code or "FFmpeg preflight rejected the compiled filtergraph")[:240],
            ))
            continue

        removed_asset_ids = set(omitted_assets)
        transition_unsupported = False
        for segment in clip["segments"]:
            segment_id = str(segment["id"])
            layout = segment["layout"]
            coverage_codes = blockers.get((clip_index, segment_id), ())
            if coverage_codes and layout.get("mode") == "crop":
                layout.update({
                    "mode": "fit",
                    "focal_target": None,
                    "fallback": "fit",
                    "allow_full_frame_fallback": True,
                    "max_zoom": 1.0,
                })
                entries.append(FallbackEntry(
                    code="VISUAL_REFRAME_FALLBACK",
                    clip_index=clip_index,
                    segment_id=segment_id,
                    requested="semantic_crop",
                    executed="content_preserving_fit",
                    reason=",".join(coverage_codes)[:240],
                ))
            if layout.get("mode") == "crop" and available and "crop" not in available:
                layout.update({
                    "mode": "fit",
                    "focal_target": None,
                    "fallback": "fit",
                    "allow_full_frame_fallback": True,
                    "max_zoom": 1.0,
                })
                entries.append(FallbackEntry(
                    code="VISUAL_REFRAME_FALLBACK",
                    clip_index=clip_index,
                    segment_id=segment_id,
                    requested="crop",
                    executed="content_preserving_fit",
                    reason="The installed renderer does not advertise crop support.",
                ))
            if float(layout.get("max_zoom") or 1) > 1 and available and "focus_zoom" not in available:
                layout["max_zoom"] = 1.0
                entries.append(FallbackEntry(
                    code="EFFECT_OMITTED",
                    clip_index=clip_index,
                    segment_id=segment_id,
                    requested="focus_zoom",
                    executed="static_layout",
                    reason="The installed renderer does not advertise focus zoom support.",
                ))
            transition = segment["transition_in"]
            transition_capability = {
                "cut": "hard_cut",
                "fade": "fade",
                "xfade": "xfade",
            }[str(transition["kind"])]
            if available and transition_capability not in available:
                transition_unsupported = True
            kept_overlays = []
            for overlay in segment.get("overlays") or []:
                capability = {
                    "text": "text_emphasis",
                    "image": "image_overlay",
                    "source": "source_cutaway",
                    "pip": "pip",
                }[str(overlay["kind"])]
                if overlay.get("asset_id") in omitted_assets:
                    removed_asset_ids.add(str(overlay["asset_id"]))
                    entries.append(FallbackEntry(
                        code="EXTERNAL_ASSET_OMITTED",
                        clip_index=clip_index,
                        segment_id=segment_id,
                        requested=f"asset:{overlay['asset_id']}",
                        executed="source_media",
                        reason=str(cause_code or "The optional external asset was unavailable")[:240],
                    ))
                    continue
                if available and capability not in available:
                    if overlay.get("asset_id"):
                        removed_asset_ids.add(str(overlay["asset_id"]))
                    entries.append(FallbackEntry(
                        code="EFFECT_OMITTED",
                        clip_index=clip_index,
                        segment_id=segment_id,
                        requested=capability,
                        executed="segment_without_overlay",
                        reason="The installed renderer does not advertise this overlay capability.",
                    ))
                    continue
                if (
                    overlay.get("protect_subtitles")
                    and overlay.get("position") in {"bottom", "bottom_left", "bottom_right"}
                ):
                    original = str(overlay["position"])
                    overlay["position"] = {
                        "bottom": "top",
                        "bottom_left": "top_left",
                        "bottom_right": "top_right",
                    }[original]
                    entries.append(FallbackEntry(
                        code="CAPTION_SAFE_ZONE_FALLBACK",
                        clip_index=clip_index,
                        segment_id=segment_id,
                        requested=original,
                        executed=str(overlay["position"]),
                        reason="The overlay was moved out of the protected subtitle zone.",
                    ))
                kept_overlays.append(overlay)
            segment["overlays"] = kept_overlays
        if transition_unsupported:
            source_window = dict(clip["source_window"])
            duration_ms = int(source_window["end_ms"]) - int(source_window["start_ms"])
            first_segment_id = str(clip["segments"][0]["id"])
            clip["segments"] = [{
                "id": f"transition-fallback-{clip_index:02d}",
                "source_window": source_window,
                "timeline_window": {"start_ms": 0, "end_ms": duration_ms},
                "layout": {
                    "mode": "fit",
                    "focal_target": None,
                    "fallback": "fit",
                    "allow_full_frame_fallback": True,
                    "safe_margin_ratio": 0.08,
                    "max_zoom": 1.0,
                },
                "transition_in": {"kind": "cut", "duration_ms": 0},
                "overlays": [],
                "reason": "Unsupported transition replaced by the deterministic baseline.",
                "evidence_ids": [],
            }]
            removed_asset_ids.update(
                str(asset["id"]) for asset in clip.get("asset_requests") or []
            )
            entries.append(FallbackEntry(
                code="TRANSITION_FALLBACK",
                clip_index=clip_index,
                segment_id=first_segment_id,
                requested="unsupported_transition_sequence",
                executed="hard_cut_full_frame_fit",
                reason="The installed renderer cannot execute the requested transition sequence.",
            ))
        clip["asset_requests"] = [
            asset
            for asset in clip.get("asset_requests") or []
            if str(asset["id"]) not in removed_asset_ids
        ]

    payload["degraded"] = bool(entries)
    payload["degradation_reason"] = (
        "deterministic_baseline_fallbacks" if entries else ""
    )
    payload["requested_capabilities"] = _required_capabilities(payload)
    return CompilationResult(
        plan=EditPlan.model_validate(payload),
        entries=tuple(entries),
    )


def merge_fallback_entries(*groups: Iterable[FallbackEntry]) -> tuple[FallbackEntry, ...]:
    merged: list[FallbackEntry] = []
    seen: set[tuple[str, int, str, str, str]] = set()
    for group in groups:
        for entry in group:
            key = (
                entry.code,
                entry.clip_index,
                entry.segment_id,
                entry.requested,
                entry.executed,
            )
            if key not in seen:
                seen.add(key)
                merged.append(entry)
    return tuple(merged)
