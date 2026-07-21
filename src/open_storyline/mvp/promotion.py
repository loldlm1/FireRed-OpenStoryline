from __future__ import annotations

from typing import Any, Literal, Sequence
import os

from open_storyline.mvp.defects import PromotionClass, promotion_class_for_code

RENDER_PROMOTION_VERSION = "render_promotion.v1"
PromotionMode = Literal["off", "report", "enforce"]
CompletionPolicy = Literal["strict", "baseline_guaranteed"]

TECHNICAL_RENDER_CODES = frozenset({
    "AUDIO_MISSING",
    "BLACK_FRAMES_DETECTED",
    "DURATION_MISMATCH",
    "FROZEN_VIDEO_DETECTED",
    "LONG_SILENCE_DETECTED",
    "OUTPUT_DIMENSIONS_MISMATCH",
    "QA_MEDIA_OUTPUT_TOO_LARGE",
    "QA_MEDIA_TOOL_TIMEOUT",
    "QA_MEDIA_TOOL_UNAVAILABLE",
    "QA_OUTPUT_MISSING",
    "QA_VIDEO_INVALID",
    "VIDEO_CODEC_UNEXPECTED",
})
TECHNICAL_FRAME_CODES = frozenset({
    "ACTIVE_PICTURE_UNAVAILABLE",
    "FRAME_EXECUTION_MISSING",
    "FRAME_QUALITY_CROPDETECT_FAILED",
    "FRAME_QUALITY_OUTPUT_MISSING",
    "FRAME_QUALITY_OUTPUT_TOO_LARGE",
    "FRAME_QUALITY_PROBE_FAILED",
    "FRAME_QUALITY_PROBE_INVALID",
    "FRAME_QUALITY_REFERENCE_FAILED",
    "FRAME_QUALITY_REFERENCE_INVALID",
    "FRAME_QUALITY_SIGNAL_FAILED",
    "FRAME_QUALITY_TIMEOUT",
    "FRAME_QUALITY_TIMEOUT_INVALID",
    "FRAME_QUALITY_TOOL_UNAVAILABLE",
    "FRAME_SIGNAL_COLLAPSED",
    "FRAME_SIGNAL_UNAVAILABLE",
    "REFERENCE_QUALITY_CATASTROPHIC",
})


class RenderPromotionError(RuntimeError):
    def __init__(
        self,
        blocker_codes: Sequence[str],
        *,
        technical_blocker_codes: Sequence[str] = (),
        creative_limitation_codes: Sequence[str] = (),
        code: str = "RENDER_PROMOTION_BLOCKED",
        message: str = "rendered candidate failed deterministic promotion checks",
    ) -> None:
        self.code = code
        self.blocker_codes = tuple(sorted({str(code)[:80] for code in blocker_codes if code}))
        self.technical_blocker_codes = tuple(
            sorted({str(code)[:80] for code in technical_blocker_codes if code})
        )
        self.creative_limitation_codes = tuple(
            sorted({str(code)[:80] for code in creative_limitation_codes if code})
        )
        super().__init__(f"{self.code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "blocker_codes": list(self.blocker_codes),
            "technical_blocker_codes": list(self.technical_blocker_codes),
            "creative_limitation_codes": list(self.creative_limitation_codes),
        }


def render_promotion_mode(config: Any) -> PromotionMode:
    value = os.getenv(
        "OPENSTORYLINE_RENDER_PROMOTION_MODE",
        str(getattr(config, "render_promotion_mode", "report")),
    ).strip().lower()
    if value not in {"off", "report", "enforce"}:
        raise RenderPromotionError(
            ["RENDER_PROMOTION_CONFIG_INVALID"],
            code="RENDER_PROMOTION_CONFIG_INVALID",
            message="render promotion mode must be off, report, or enforce",
        )
    return value  # type: ignore[return-value]


def completion_policy(config: Any) -> CompletionPolicy:
    value = os.getenv(
        "OPENSTORYLINE_COMPLETION_POLICY",
        str(getattr(config, "completion_policy", "strict")),
    ).strip().lower()
    if value not in {"strict", "baseline_guaranteed"}:
        raise RenderPromotionError(
            ["RENDER_PROMOTION_CONFIG_INVALID"],
            code="RENDER_PROMOTION_CONFIG_INVALID",
            message="completion policy must be strict or baseline_guaranteed",
        )
    return value  # type: ignore[return-value]


def limited_output_promotion_enabled() -> bool:
    value = os.getenv(
        "OPENSTORYLINE_LIMITED_OUTPUT_PROMOTION_ENABLED",
        "false",
    ).strip().lower()
    if value not in {"true", "false"}:
        raise RenderPromotionError(
            ["RENDER_PROMOTION_CONFIG_INVALID"],
            code="RENDER_PROMOTION_CONFIG_INVALID",
            message=(
                "OPENSTORYLINE_LIMITED_OUTPUT_PROMOTION_ENABLED must be true or false"
            ),
        )
    return value == "true"


def _blocker_codes(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return []
    return [
        str(finding.get("code") or "")[:80]
        for finding in report.get("findings") or []
        if finding.get("severity") == "blocker" and finding.get("code")
    ]


def _normalized_codes(values: Sequence[str]) -> list[str]:
    return sorted({str(code).strip().upper()[:80] for code in values if code})


def _is_technical(code: str, detector_codes: frozenset[str]) -> bool:
    if code in detector_codes:
        return True
    return promotion_class_for_code(code) in {
        PromotionClass.TECHNICAL_BLOCKER,
        PromotionClass.TERMINAL,
    }


def build_render_promotion_report(
    *,
    mode: PromotionMode,
    policy: CompletionPolicy = "strict",
    limited_output_enabled: bool = False,
    frame_quality: dict[str, Any] | None,
    render_qa: dict[str, Any] | None,
    creative_conformance: dict[str, Any] | None,
    caption_footprints: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    frame_blockers = _normalized_codes(_blocker_codes(frame_quality))
    render_blockers = _normalized_codes(_blocker_codes(render_qa))
    technical_blockers = {
        code for code in frame_blockers if _is_technical(code, TECHNICAL_FRAME_CODES)
    }
    technical_blockers.update(
        code for code in render_blockers if _is_technical(code, TECHNICAL_RENDER_CODES)
    )
    creative_limitations = set(frame_blockers) - technical_blockers
    creative_limitations.update(set(render_blockers) - technical_blockers)
    if (frame_quality or {}).get("status") in {None, "off", "unavailable"} and mode != "off":
        technical_blockers.add("FRAME_QUALITY_UNAVAILABLE")
    if (render_qa or {}).get("status") in {None, "unavailable"} and mode != "off":
        technical_blockers.add("RENDER_STRUCTURE_UNAVAILABLE")
    if (creative_conformance or {}).get("status") in {None, "unavailable"} and mode != "off":
        technical_blockers.add("CREATIVE_CONFORMANCE_UNAVAILABLE")
    conformance_blockers = {
        "asset_overlay_duplicated",
        "asset_overlay_not_visible",
        "asset_overlay_opacity_too_low",
        "asset_visibility_analysis_unavailable",
        "asset_visibility_asset_invalid",
        "asset_visibility_asset_unresolved",
        "asset_visibility_geometry_invalid",
        "asset_visibility_limit_reached",
        "asset_visibility_timing_invalid",
        "planned_operations_missing",
        "requested_assets_missing",
        "unrequested_assets_used",
        "unexplained_fallback",
    }
    for finding in (creative_conformance or {}).get("findings") or []:
        code = str(finding.get("code") or "")
        if code in conformance_blockers:
            creative_limitations.add(code.upper())
        elif finding.get("severity") == "blocker" and code:
            creative_limitations.add(code.upper()[:80])
    for footprint in caption_footprints:
        if footprint.get("status") == "blocked":
            creative_limitations.update(
                str(code).upper()[:80]
                for code in (footprint.get("summary") or {}).get("blocker_codes") or []
            )
    technical_codes = sorted(code for code in technical_blockers if code)
    creative_codes = sorted(
        code for code in creative_limitations if code and code not in technical_blockers
    )
    blockers = sorted({*technical_codes, *creative_codes})
    promotion_decision = (
        "block_technical"
        if technical_codes
        else "promote_with_limitations"
        if creative_codes
        else "promote_enhanced"
    )
    baseline_enforcement = "block" if technical_codes else "promote"
    strict_enforcement = "block" if blockers else "promote"
    effective_policy = (
        "baseline_guaranteed"
        if policy == "baseline_guaranteed" and limited_output_enabled
        else "strict"
    )
    effective_enforcement = (
        baseline_enforcement
        if effective_policy == "baseline_guaranteed"
        else strict_enforcement
    )
    if mode == "off":
        decision = "off"
    elif mode == "enforce":
        decision = effective_enforcement
    elif blockers:
        decision = "observe"
    else:
        decision = "promote"
    return {
        "version": RENDER_PROMOTION_VERSION,
        "mode": mode,
        "completion_policy": policy,
        "effective_policy": effective_policy,
        "limited_output_promotion_enabled": limited_output_enabled,
        "decision": decision,
        "promotion_decision": promotion_decision,
        "policy_decisions": {
            "strict": strict_enforcement,
            "baseline_guaranteed": baseline_enforcement,
        },
        "status": "blocked" if effective_enforcement == "block" else (
            "limited" if creative_codes else "pass"
        ),
        "technical_status": "blocked" if technical_codes else "pass",
        "blocker_codes": blockers,
        "technical_blocker_codes": technical_codes,
        "creative_limitation_codes": creative_codes,
        "checks": {
            "frame_quality": (frame_quality or {}).get("status", "unavailable"),
            "render_structure": (render_qa or {}).get("status", "unavailable"),
            "creative_conformance": (
                (creative_conformance or {}).get("status", "unavailable")
            ),
            "caption_footprints": [
                str(item.get("status") or "unavailable") for item in caption_footprints[:8]
            ],
        },
    }


def enforce_render_promotion(report: dict[str, Any]) -> None:
    if report.get("mode") == "enforce" and report.get("decision") == "block":
        raise RenderPromotionError(
            report.get("blocker_codes") or [],
            technical_blocker_codes=report.get("technical_blocker_codes") or [],
            creative_limitation_codes=report.get("creative_limitation_codes") or [],
        )
