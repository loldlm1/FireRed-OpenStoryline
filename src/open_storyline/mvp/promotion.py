from __future__ import annotations

from typing import Any, Literal, Sequence
import os


RENDER_PROMOTION_VERSION = "render_promotion.v1"
PromotionMode = Literal["off", "report", "enforce"]


class RenderPromotionError(RuntimeError):
    def __init__(
        self,
        blocker_codes: Sequence[str],
        *,
        code: str = "RENDER_PROMOTION_BLOCKED",
        message: str = "rendered candidate failed deterministic promotion checks",
    ) -> None:
        self.code = code
        self.blocker_codes = tuple(sorted({str(code)[:80] for code in blocker_codes if code}))
        super().__init__(f"{self.code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "blocker_codes": list(self.blocker_codes)}


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


def _blocker_codes(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return []
    return [
        str(finding.get("code") or "")[:80]
        for finding in report.get("findings") or []
        if finding.get("severity") == "blocker" and finding.get("code")
    ]


def build_render_promotion_report(
    *,
    mode: PromotionMode,
    frame_quality: dict[str, Any] | None,
    render_qa: dict[str, Any] | None,
    creative_conformance: dict[str, Any] | None,
    caption_footprints: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    blockers = _blocker_codes(frame_quality)
    blockers.extend(_blocker_codes(render_qa))
    if (frame_quality or {}).get("status") in {None, "off", "unavailable"} and mode != "off":
        blockers.append("FRAME_QUALITY_UNAVAILABLE")
    if (render_qa or {}).get("status") in {None, "unavailable"} and mode != "off":
        blockers.append("RENDER_STRUCTURE_UNAVAILABLE")
    if (creative_conformance or {}).get("status") in {None, "unavailable"} and mode != "off":
        blockers.append("CREATIVE_CONFORMANCE_UNAVAILABLE")
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
            blockers.append(code.upper())
        elif finding.get("severity") == "blocker" and code:
            blockers.append(code[:80])
    for footprint in caption_footprints:
        if footprint.get("status") == "blocked":
            blockers.extend(
                str(code)[:80]
                for code in (footprint.get("summary") or {}).get("blocker_codes") or []
            )
    blockers = sorted({code for code in blockers if code})
    if mode == "off":
        decision = "off"
    elif blockers and mode == "enforce":
        decision = "block"
    elif blockers:
        decision = "observe"
    else:
        decision = "promote"
    return {
        "version": RENDER_PROMOTION_VERSION,
        "mode": mode,
        "decision": decision,
        "status": "blocked" if blockers else "pass",
        "blocker_codes": blockers,
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
        raise RenderPromotionError(report.get("blocker_codes") or [])
