from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from open_storyline.mvp.edit_plan import AssetPolicy, EditPlan


PREFLIGHT_VERSION = "edit_preflight.v1"


@dataclass(frozen=True)
class PreflightFinding:
    severity: str
    code: str
    source: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PreflightReport:
    status: str
    findings: tuple[PreflightFinding, ...]
    plan_version: str
    available_capabilities: tuple[str, ...]

    @property
    def blocking(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "block")

    @property
    def warnings(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "warn")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": PREFLIGHT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": self.status,
            "plan_version": self.plan_version,
            "summary": {
                "blocking": self.blocking,
                "warnings": self.warnings,
                "checks": len(self.findings),
            },
            "available_capabilities": list(self.available_capabilities),
            "findings": [finding.to_dict() for finding in self.findings],
        }


def build_preflight(
    plan: EditPlan,
    *,
    available_capabilities: Iterable[str],
    asset_policy: AssetPolicy,
    resolved_asset_ids: Iterable[str] = (),
) -> PreflightReport:
    available = tuple(sorted({str(value) for value in available_capabilities}))
    available_set = set(available)
    resolved = {str(value) for value in resolved_asset_ids}
    findings: list[PreflightFinding] = []

    for capability in plan.requested_capabilities:
        if capability not in available_set:
            findings.append(PreflightFinding(
                "block",
                "CAPABILITY_UNAVAILABLE",
                f"requested_capabilities.{capability}",
                f"Renderer capability is unavailable: {capability}",
            ))

    for clip in plan.clips:
        for segment in clip.segments:
            if segment.layout.mode == "crop" and segment.layout.focal_target is None:
                findings.append(PreflightFinding(
                    "warn",
                    "CROP_TARGET_MISSING",
                    f"clips.{clip.clip_index}.segments.{segment.id}.layout",
                    "Crop has no semantic focal target and must use its explicit fallback policy.",
                ))
            if segment.transition_in.duration_ms >= segment.timeline_window.duration_ms:
                findings.append(PreflightFinding(
                    "block",
                    "TRANSITION_TOO_LONG",
                    f"clips.{clip.clip_index}.segments.{segment.id}.transition_in",
                    "Transition duration must be shorter than the segment.",
                ))

        for asset in clip.asset_requests:
            source = f"clips.{clip.clip_index}.asset_requests.{asset.id}"
            if asset_policy == "off":
                findings.append(PreflightFinding(
                    "block",
                    "ASSET_POLICY_BLOCKED",
                    source,
                    "The job does not permit external assets.",
                ))
            elif asset.required and asset.id not in resolved:
                findings.append(PreflightFinding(
                    "block",
                    "ASSET_UNRESOLVED",
                    source,
                    "A required asset has not been resolved.",
                ))

    blocking = sum(1 for finding in findings if finding.severity == "block")
    warnings = sum(1 for finding in findings if finding.severity == "warn")
    status = "blocked" if blocking else ("warn" if warnings else "ready")
    return PreflightReport(
        status=status,
        findings=tuple(findings),
        plan_version=plan.version,
        available_capabilities=available,
    )
