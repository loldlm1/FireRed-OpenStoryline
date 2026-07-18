from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from open_storyline.mvp.edit_plan import AssetPolicy, EditPlan, required_capabilities


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
    known_region_ids: Iterable[str] = (),
    known_track_ids: Iterable[str] = (),
    known_evidence_ids: Iterable[str] = (),
    known_evidence_ids_by_clip: dict[int, Iterable[str]] | None = None,
    max_segments_per_clip: int = 48,
    max_overlays_per_clip: int = 16,
    max_assets_per_clip: int = 8,
) -> PreflightReport:
    available = tuple(sorted({str(value) for value in available_capabilities}))
    available_set = set(available)
    resolved = {str(value) for value in resolved_asset_ids}
    regions = {str(value) for value in known_region_ids}
    tracks = {str(value) for value in known_track_ids}
    evidence = {str(value) for value in known_evidence_ids}
    evidence_by_clip = {
        int(index): {str(value) for value in values}
        for index, values in (known_evidence_ids_by_clip or {}).items()
    }
    findings: list[PreflightFinding] = []

    for capability in plan.requested_capabilities:
        if capability not in available_set:
            findings.append(PreflightFinding(
                "block",
                "CAPABILITY_UNAVAILABLE",
                f"requested_capabilities.{capability}",
                f"Renderer capability is unavailable: {capability}",
            ))
    for capability in sorted(required_capabilities(plan) - set(plan.requested_capabilities)):
        findings.append(PreflightFinding(
            "block",
            "CAPABILITY_UNDECLARED",
            f"requested_capabilities.{capability}",
            f"Plan operations require an undeclared capability: {capability}",
        ))

    for clip in plan.clips:
        clip_evidence = evidence_by_clip.get(clip.clip_index, evidence)
        if len(clip.segments) > max_segments_per_clip:
            findings.append(PreflightFinding(
                "block",
                "SEGMENT_BUDGET_EXCEEDED",
                f"clips.{clip.clip_index}.segments",
                "The clip exceeds the configured segment budget.",
            ))
        if sum(len(segment.overlays) for segment in clip.segments) > max_overlays_per_clip:
            findings.append(PreflightFinding(
                "block",
                "OVERLAY_BUDGET_EXCEEDED",
                f"clips.{clip.clip_index}.overlays",
                "The clip exceeds the configured overlay budget.",
            ))
        if len(clip.asset_requests) > max_assets_per_clip:
            findings.append(PreflightFinding(
                "block",
                "ASSET_BUDGET_EXCEEDED",
                f"clips.{clip.clip_index}.asset_requests",
                "The clip exceeds the configured asset budget.",
            ))
        for segment in clip.segments:
            if segment.layout.mode == "crop" and segment.layout.focal_target is None:
                findings.append(PreflightFinding(
                    "warn",
                    "CROP_TARGET_MISSING",
                    f"clips.{clip.clip_index}.segments.{segment.id}.layout",
                    "Crop has no semantic focal target and must use its explicit fallback policy.",
                ))
            target = segment.layout.focal_target
            if target is not None:
                if target.region_id and target.region_id not in regions:
                    findings.append(PreflightFinding(
                        "block",
                        "REGION_REFERENCE_UNKNOWN",
                        f"clips.{clip.clip_index}.segments.{segment.id}.layout.focal_target",
                        "The focal target references an unknown region.",
                    ))
                elif target.region_id and target.region_id not in clip_evidence:
                    findings.append(PreflightFinding(
                        "block",
                        "REGION_REFERENCE_OUTSIDE_CLIP",
                        f"clips.{clip.clip_index}.segments.{segment.id}.layout.focal_target",
                        "The focal target region is outside the selected clip evidence.",
                    ))
                if target.track_id and target.track_id not in tracks:
                    findings.append(PreflightFinding(
                        "block",
                        "TRACK_REFERENCE_UNKNOWN",
                        f"clips.{clip.clip_index}.segments.{segment.id}.layout.focal_target",
                        "The focal target references an unknown track.",
                    ))
                elif target.track_id and target.track_id not in clip_evidence:
                    findings.append(PreflightFinding(
                        "block",
                        "TRACK_REFERENCE_OUTSIDE_CLIP",
                        f"clips.{clip.clip_index}.segments.{segment.id}.layout.focal_target",
                        "The focal target track is outside the selected clip evidence.",
                    ))
            unknown_evidence = sorted(set(segment.evidence_ids) - clip_evidence)
            if unknown_evidence:
                findings.append(PreflightFinding(
                    "block",
                    "EVIDENCE_REFERENCE_UNKNOWN",
                    f"clips.{clip.clip_index}.segments.{segment.id}.evidence_ids",
                    "The segment references evidence outside its validated evidence catalog.",
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
