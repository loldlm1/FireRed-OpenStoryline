from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable
import os

from open_storyline.mvp.defects import RepairStrategy, defect_definition
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


@dataclass(frozen=True)
class FallbackDirective:
    code: str
    clip_index: int | None = None
    segment_id: str = ""
    attempt_evidenced: bool = False


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
    remaining_defects: Iterable[FallbackDirective] = (),
    enforce_attempt_gate: bool = False,
    max_segments_per_clip: int = 48,
    max_overlays_per_clip: int = 16,
    max_assets_per_clip: int = 8,
) -> CompilationResult:
    payload = plan.to_dict()
    available = {str(value) for value in available_capabilities}
    omitted_assets = {str(value) for value in omitted_asset_ids}
    blockers = _coverage_blockers(visual_coverage)
    directives = tuple(remaining_defects)
    for directive in directives:
        if (
            enforce_attempt_gate
            and defect_definition(directive.code).repair_strategy in {
                RepairStrategy.LLM_PLAN_REPAIR,
                RepairStrategy.CONDITIONAL_LLM_OR_FALLBACK,
            }
            and not directive.attempt_evidenced
        ):
            raise FallbackConfigurationError(
                "REPAIR_ATTEMPT_REQUIRED",
                "deterministic fallback requires matching outbound LLM attempt evidence",
            )
    entries: list[FallbackEntry] = []

    for clip in payload["clips"]:
        clip_index = int(clip["clip_index"])
        clip_directives = tuple(
            directive
            for directive in directives
            if directive.clip_index in {None, clip_index}
        )
        directive_codes = {directive.code for directive in clip_directives}
        clip_wide_codes = {
            directive.code for directive in clip_directives if not directive.segment_id
        }
        if force_minimal or (
            len(clip["segments"]) > max_segments_per_clip
            and directive_codes
            & {"EDIT_PLAN_SEGMENT_BUDGET_EXCEEDED", "SEGMENT_BUDGET_EXCEEDED"}
        ):
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
            for directive in clip_directives:
                fallback_code = defect_definition(directive.code).safe_fallback_code
                if fallback_code is None:
                    continue
                entries.append(FallbackEntry(
                    code=fallback_code,
                    clip_index=clip_index,
                    segment_id=directive.segment_id or "plan",
                    requested=directive.code,
                    executed=fallback_code,
                    reason="The clip required a deterministic bounded baseline fallback.",
                ))
            continue

        removed_asset_ids = set(omitted_assets)
        transition_unsupported = False
        required_reframe_segment_ids = {
            str(operation_id)
            for decision in clip.get("intent_decisions") or []
            if isinstance(decision, dict)
            and decision.get("decision") == "execute"
            and decision.get("intent_id") == "prompt-reframe-sequence"
            for operation_id in decision.get("operation_ids") or []
        }
        if directive_codes & {
            "EDIT_PLAN_CATALOG_ID_UNKNOWN",
            "EDIT_PLAN_CATALOG_KIND_INVALID",
            "EDIT_PLAN_CATALOG_STYLE_MISMATCH",
            "EDIT_PLAN_CATALOG_TRANSITION_MISMATCH",
        }:
            clip["catalog_selection"] = {
                "style_profile_id": "",
                "caption_treatment_id": "",
                "color_treatment_id": "",
                "recipe_ids": [],
            }
            for segment in clip["segments"]:
                segment["transition_in"]["catalog_id"] = ""
        if len(clip.get("asset_requests") or []) > max_assets_per_clip:
            removed_asset_ids.update(
                str(asset["id"])
                for asset in clip["asset_requests"][max_assets_per_clip:]
            )
        overlay_budget = max_overlays_per_clip
        for segment in clip["segments"]:
            segment_id = str(segment["id"])
            layout = segment["layout"]
            coverage_codes = blockers.get((clip_index, segment_id), ())
            segment_codes = clip_wide_codes | {
                directive.code
                for directive in clip_directives
                if directive.segment_id == segment_id
            }
            preserve_required_reframe = (
                segment_id in required_reframe_segment_ids
                and layout.get("mode") == "crop"
                and (not available or "crop" in available)
            )
            if segment_codes & {
                "COMPOSITION_CROP_TARGET_TOO_WIDE",
                "COMPOSITION_LAYOUT_UNSUPPORTED",
                "EDIT_PLAN_REGION_UNKNOWN",
                "EDIT_PLAN_REGION_OUTSIDE_CLIP",
                "EDIT_PLAN_TRACK_UNKNOWN",
                "EDIT_PLAN_TRACK_OUTSIDE_CLIP",
                "EDIT_PLAN_EVIDENCE_UNKNOWN",
                "REGION_REFERENCE_UNKNOWN",
                "REGION_REFERENCE_OUTSIDE_CLIP",
                "TRACK_REFERENCE_UNKNOWN",
                "TRACK_REFERENCE_OUTSIDE_CLIP",
                "EVIDENCE_REFERENCE_UNKNOWN",
                "FULL_FRAME_FALLBACK_UNAPPROVED",
            }:
                layout.update(
                    {
                        "mode": "crop",
                        "focal_target": None,
                        "fallback": "crop",
                        "allow_full_frame_fallback": False,
                    }
                    if preserve_required_reframe
                    else {
                        "mode": "fit",
                        "focal_target": None,
                        "fallback": "fit",
                        "allow_full_frame_fallback": True,
                        "max_zoom": 1.0,
                    }
                )
                segment["evidence_ids"] = []
            if (
                "PREDICTIVE_ACTIVE_PICTURE_RISK" in segment_codes
                and layout.get("mode") == "letterbox"
            ):
                layout.update({
                    "mode": "fit",
                    "focal_target": None,
                    "fallback": "fit",
                    "allow_full_frame_fallback": True,
                    "max_zoom": 1.0,
                })
            directed_coverage_codes = tuple(
                code
                for code in coverage_codes
                if not enforce_attempt_gate or code in segment_codes
            )
            if directed_coverage_codes and layout.get("mode") == "crop":
                layout.update(
                    {
                        "mode": "crop",
                        "focal_target": None,
                        "fallback": "crop",
                        "allow_full_frame_fallback": False,
                    }
                    if preserve_required_reframe
                    else {
                        "mode": "fit",
                        "focal_target": None,
                        "fallback": "fit",
                        "allow_full_frame_fallback": True,
                        "max_zoom": 1.0,
                    }
                )
                segment["evidence_ids"] = []
                entries.append(FallbackEntry(
                    code="VISUAL_REFRAME_FALLBACK",
                    clip_index=clip_index,
                    segment_id=segment_id,
                    requested="semantic_crop",
                    executed=(
                        "bounded_center_reframe"
                        if preserve_required_reframe
                        else "content_preserving_fit"
                    ),
                    reason=",".join(directed_coverage_codes)[:240],
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
            if segment_codes & {
                "TRANSITION_TOO_LONG",
                "OVERLAY_TRANSITION_TOO_LONG",
                "EDIT_PLAN_CATALOG_TRANSITION_MISMATCH",
            }:
                transition.update({"kind": "cut", "duration_ms": 0, "catalog_id": ""})
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
                if "PREDICTIVE_OVERLAY_OPACITY_LOW" in segment_codes:
                    overlay["opacity"] = max(0.15, float(overlay.get("opacity") or 0))
                if "PREDICTIVE_OVERLAY_GEOMETRY_INVALID" in segment_codes:
                    margin = float(overlay.get("margin_ratio") or 0)
                    overlay["width_ratio"] = min(
                        float(overlay.get("width_ratio") or 0.35),
                        max(0.08, 1 - (2 * margin)),
                    )
                if "OVERLAY_TRANSITION_TOO_LONG" in segment_codes:
                    duration_ms = (
                        int(overlay["timeline_window"]["end_ms"])
                        - int(overlay["timeline_window"]["start_ms"])
                    )
                    overlay["transition_ms"] = min(
                        int(overlay.get("transition_ms") or 0),
                        max(0, duration_ms // 2),
                    )
                if overlay_budget <= 0:
                    if overlay.get("asset_id"):
                        removed_asset_ids.add(str(overlay["asset_id"]))
                    continue
                overlay_budget -= 1
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

        for directive in clip_directives:
            fallback_code = defect_definition(directive.code).safe_fallback_code
            if fallback_code is None:
                continue
            entries.append(FallbackEntry(
                code=fallback_code,
                clip_index=clip_index,
                segment_id=directive.segment_id or "plan",
                requested=directive.code,
                executed=(
                    "bounded_center_reframe"
                    if (
                        fallback_code == "VISUAL_REFRAME_FALLBACK"
                        and directive.segment_id in required_reframe_segment_ids
                        and any(
                            str(segment.get("id")) == directive.segment_id
                            and segment.get("layout", {}).get("mode") == "crop"
                            and segment.get("layout", {}).get("fallback") == "crop"
                            for segment in clip.get("segments") or []
                        )
                    )
                    else fallback_code
                ),
                reason=(
                    "The bounded semantic repair left this registered creative "
                    "defect unresolved, so its deterministic fallback was applied."
                ),
            ))

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
