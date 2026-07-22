from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, Iterable, Mapping
import json
import math
import os
import re

from open_storyline.mvp.defects import (
    DEFECT_REGISTRY_SHA256,
    DEFECT_REGISTRY_VERSION,
    RepairStrategy,
    defect_definition,
)
from open_storyline.mvp.edit_plan import EditPlan
from open_storyline.mvp.prompts import (
    REPAIR_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT_VERSION,
)
from open_storyline.mvp.structured_outputs import (
    EDIT_PLAN_REPAIR_SCHEMA,
    VISUAL_UNDERSTANDING_SCHEMA,
    structured_output,
)


REPAIR_BATCH_REQUEST_VERSION = "repair_batch_request.v1"
REPAIR_REPORT_VERSION = "repair_report.v1"
REPAIR_RESOLUTION_VERSION = "repair_resolution.v1"
MAX_REPAIR_CLIPS = 8
MAX_REPAIR_CODES = 32
MAX_REPAIR_EVIDENCE_RECORDS = 64
MAX_REPAIR_PROMPT_BYTES = 12_000
MAX_REPAIR_TRANSCRIPT_BYTES = 16_000
MAX_REPAIR_CANDIDATE_BYTES = 192_000
MAX_REPAIR_REQUEST_BYTES = 256_000
MAX_REPAIR_REPORT_BYTES = 256_000
MAX_REPAIR_CONTEXT_DEPTH = 12

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_SAFE_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_BLOCKED_CONTEXT_KEY_PARTS = frozenset({
    "command",
    "credential",
    "device",
    "path",
    "provider_body",
    "provider_response",
    "secret",
    "token",
    "url",
})
_EVIDENCE_FIELDS = frozenset({
    "available",
    "capability",
    "clip_index",
    "code",
    "confidence",
    "count",
    "duration_ms",
    "end_ms",
    "evidence_id",
    "expected",
    "height_ratio",
    "margin_ratio",
    "maximum",
    "maximum_gap_ms",
    "minimum",
    "observed",
    "observation_count",
    "opacity",
    "operation_id",
    "position",
    "region_id",
    "requested",
    "segment_id",
    "source",
    "start_ms",
    "threshold",
    "track_id",
    "width_ratio",
})


class RepairMode(StrEnum):
    OFF = "off"
    REPORT = "report"
    ENFORCE = "enforce"


class RepairStage(StrEnum):
    VISUAL_UNDERSTANDING = "visual_understanding"
    PLAN_REPAIR = "plan_repair"


class RepairContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class RepairBudget:
    visual_attempts_used: int = 0
    plan_attempts_used: int = 0

    def available(self, stage: RepairStage) -> bool:
        if stage is RepairStage.VISUAL_UNDERSTANDING:
            return self.visual_attempts_used < 1
        return self.plan_attempts_used < 1


@dataclass(frozen=True)
class RepairEvidence:
    evidence_type: str
    values: Mapping[str, Any]
    clip_index: int | None = None
    source: str = "deterministic_validator"

    def to_dict(self) -> dict[str, Any]:
        if not _SAFE_FIELD.fullmatch(self.evidence_type):
            raise RepairContractError("REPAIR_EVIDENCE_INVALID", "evidence type is invalid")
        if self.clip_index is not None and not 1 <= int(self.clip_index) <= MAX_REPAIR_CLIPS:
            raise RepairContractError("REPAIR_EVIDENCE_INVALID", "evidence clip is out of bounds")
        if not _SAFE_TOKEN.fullmatch(self.source):
            raise RepairContractError("REPAIR_EVIDENCE_INVALID", "evidence source is invalid")
        clean_values: dict[str, Any] = {}
        for key, value in self.values.items():
            name = str(key)
            if name not in _EVIDENCE_FIELDS:
                raise RepairContractError(
                    "REPAIR_EVIDENCE_INVALID",
                    f"evidence field is not allowlisted: {name}",
                )
            clean_values[name] = _safe_scalar(value)
        return {
            "evidence_type": self.evidence_type,
            "clip_index": int(self.clip_index) if self.clip_index is not None else None,
            "source": self.source,
            "values": clean_values,
        }


@dataclass(frozen=True)
class RepairFinding:
    code: str
    objective: bool
    evidence: tuple[RepairEvidence, ...]
    clip_index: int | None = None
    required_capabilities: tuple[str, ...] = ()

    @property
    def evidence_types(self) -> frozenset[str]:
        return frozenset(item.evidence_type for item in self.evidence)


@dataclass(frozen=True)
class RepairDisposition:
    code: str
    stage: str
    mode: str
    eligible: bool
    would_call: bool
    call_allowed: bool
    reason: str
    fallback_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TranscriptExcerpt:
    clip_index: int
    start_ms: int
    end_ms: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        text = str(self.text or "").strip()
        if not 1 <= int(self.clip_index) <= MAX_REPAIR_CLIPS:
            raise RepairContractError("REPAIR_TRANSCRIPT_INVALID", "clip index is out of bounds")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise RepairContractError("REPAIR_TRANSCRIPT_INVALID", "excerpt timing is invalid")
        if "\x00" in text:
            raise RepairContractError("REPAIR_TRANSCRIPT_INVALID", "excerpt contains a null byte")
        return {
            "clip_index": int(self.clip_index),
            "start_ms": int(self.start_ms),
            "end_ms": int(self.end_ms),
            "text": text,
        }


@dataclass(frozen=True)
class RepairBatchRequest:
    stage: RepairStage
    mode: RepairMode
    defects: tuple[dict[str, Any], ...]
    supplemental_advisories: tuple[dict[str, Any], ...]
    candidate_clips: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    available_capabilities: tuple[str, ...]
    catalog_context: dict[str, Any]
    immutable_constraints: dict[str, Any]
    editing_prompt: str
    transcript_excerpts: tuple[dict[str, Any], ...]

    @property
    def response_schema(self) -> str:
        if self.stage is RepairStage.VISUAL_UNDERSTANDING:
            return VISUAL_UNDERSTANDING_SCHEMA
        return EDIT_PLAN_REPAIR_SCHEMA

    @property
    def system_prompt_sha256(self) -> str:
        return _digest(REPAIR_SYSTEM_PROMPT)

    @property
    def editing_prompt_sha256(self) -> str:
        return _digest(self.editing_prompt)

    @property
    def transcript_sha256(self) -> str:
        return _digest(_canonical_json(list(self.transcript_excerpts)))

    def to_provider_dict(self) -> dict[str, Any]:
        payload = {
            "version": REPAIR_BATCH_REQUEST_VERSION,
            "stage": self.stage.value,
            "semantic_attempt": 1,
            "response_schema": self.response_schema,
            "repair_prompt_version": REPAIR_SYSTEM_PROMPT_VERSION,
            "repair_prompt_sha256": self.system_prompt_sha256,
            "affected_clip_ids": sorted({
                int(item["clip_index"])
                for item in self.candidate_clips
            }),
            "defects": list(self.defects),
            "supplemental_advisories": list(self.supplemental_advisories),
            "candidate_clips": list(self.candidate_clips),
            "evidence": list(self.evidence),
            "available_capabilities": list(self.available_capabilities),
            "catalog_context": self.catalog_context,
            "immutable_constraints": self.immutable_constraints,
            "editing_prompt": self.editing_prompt,
            "transcript_excerpts": list(self.transcript_excerpts),
        }
        if _json_size(payload) > MAX_REPAIR_REQUEST_BYTES:
            raise RepairContractError("REPAIR_REQUEST_TOO_LARGE", "repair request exceeds its byte budget")
        return payload

    def to_report_dict(self) -> dict[str, Any]:
        provider_payload = self.to_provider_dict()
        evidence_ids = sorted({
            _safe_token((item.get("values") or {}).get("evidence_id"))
            or _digest(_canonical_json(item))
            for item in provider_payload["evidence"]
        })
        fingerprint_payload = {
            key: value
            for key, value in provider_payload.items()
            if key not in {"editing_prompt", "transcript_excerpts", "candidate_clips"}
        }
        fingerprint_payload.update({
            "editing_prompt_sha256": self.editing_prompt_sha256,
            "transcript_sha256": self.transcript_sha256,
            "candidate_sha256": _digest(_canonical_json(list(self.candidate_clips))),
        })
        return {
            "version": REPAIR_REPORT_VERSION,
            "request_version": REPAIR_BATCH_REQUEST_VERSION,
            "stage": self.stage.value,
            "mode": self.mode.value,
            "semantic_attempt": 1,
            "response_schema": self.response_schema,
            "response_schema_sha256": structured_output(
                self.response_schema
            ).fingerprint,
            "repair_prompt_version": REPAIR_SYSTEM_PROMPT_VERSION,
            "repair_prompt_sha256": self.system_prompt_sha256,
            "request_fingerprint": _digest(_canonical_json(fingerprint_payload)),
            "editing_prompt_sha256": self.editing_prompt_sha256,
            "editing_prompt_bytes": len(self.editing_prompt.encode("utf-8")),
            "transcript_sha256": self.transcript_sha256,
            "transcript_bytes": len(
                _canonical_json(list(self.transcript_excerpts)).encode("utf-8")
            ),
            "candidate_sha256": fingerprint_payload["candidate_sha256"],
            "affected_clip_ids": provider_payload["affected_clip_ids"],
            "objective_codes": [item["code"] for item in self.defects],
            "advisory_codes": [item["code"] for item in self.supplemental_advisories],
            "evidence_types": sorted({item["evidence_type"] for item in self.evidence}),
            "evidence_ids": evidence_ids[:MAX_REPAIR_EVIDENCE_RECORDS],
            "evidence_count": len(self.evidence),
            "would_call": True,
            "call_allowed": self.mode is RepairMode.ENFORCE,
        }


@dataclass(frozen=True)
class RepairResolution:
    original_codes: tuple[str, ...]
    repaired_codes: tuple[str, ...]
    resolved_codes: tuple[str, ...]
    remaining_codes: tuple[str, ...]
    introduced_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"version": REPAIR_RESOLUTION_VERSION, **asdict(self)}


@dataclass(frozen=True)
class RepairQualityFloor:
    accepted: bool
    violation_codes: tuple[str, ...]
    resolution: RepairResolution

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "violation_codes": list(self.violation_codes),
            "resolution": self.resolution.to_dict(),
        }


@dataclass(frozen=True)
class PredictiveFinding:
    code: str
    clip_index: int
    segment_id: str
    objective: bool
    confidence: float
    detector: str
    threshold: str
    evidence: dict[str, Any]

    def to_repair_finding(self) -> RepairFinding:
        definition = defect_definition(self.code)
        records = tuple(
            RepairEvidence(
                evidence_type=evidence_type,
                clip_index=self.clip_index,
                source=self.detector,
                values={
                    "code": self.code,
                    "clip_index": self.clip_index,
                    "segment_id": self.segment_id,
                    "confidence": self.confidence,
                    "threshold": self.threshold,
                    **self.evidence,
                },
            )
            for evidence_type in definition.evidence_requirements
        )
        return RepairFinding(
            code=self.code,
            objective=self.objective,
            clip_index=self.clip_index,
            evidence=records,
        )


def resolve_repair_mode(value: str | None = None) -> RepairMode:
    raw = str(
        value
        if value is not None
        else os.getenv("OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE", "off")
    ).strip().lower()
    try:
        return RepairMode(raw)
    except ValueError as exc:
        raise RepairContractError(
            "REPAIR_MODE_INVALID",
            "repair mode must be off, report, or enforce",
        ) from exc


def repair_disposition(
    finding: RepairFinding,
    *,
    stage: RepairStage,
    mode: RepairMode,
    budget: RepairBudget,
    available_capabilities: Iterable[str] = (),
    rendering_started: bool = False,
) -> RepairDisposition:
    definition = defect_definition(finding.code)
    fallback = definition.safe_fallback_code

    def result(reason: str, *, eligible: bool = False) -> RepairDisposition:
        would_call = eligible and mode in {RepairMode.REPORT, RepairMode.ENFORCE}
        return RepairDisposition(
            code=definition.code,
            stage=stage.value,
            mode=mode.value,
            eligible=eligible,
            would_call=would_call,
            call_allowed=would_call and mode is RepairMode.ENFORCE,
            reason=reason,
            fallback_code=fallback,
        )

    if mode is RepairMode.OFF:
        return result("repair_disabled")
    if definition.code == "UNKNOWN_DEFECT":
        return result("unknown_code")
    if rendering_started:
        return result("rendering_started")
    if finding.code.startswith("FFMPEGA_") or finding.code == "EFFECT_PLANNING_FAILED":
        return result("ffmpega_is_deterministic_fallback_only")
    expected_stage = (
        RepairStage.VISUAL_UNDERSTANDING
        if definition.repair_strategy is RepairStrategy.LLM_VISUAL_REPAIR
        else RepairStage.PLAN_REPAIR
        if definition.repair_strategy in {
            RepairStrategy.LLM_PLAN_REPAIR,
            RepairStrategy.CONDITIONAL_LLM_OR_FALLBACK,
        }
        else None
    )
    if expected_stage is None or expected_stage is not stage:
        return result("strategy_or_stage_ineligible")
    if not finding.objective:
        return result("advisory_cannot_trigger")
    if definition.detector and not definition.trigger_eligible:
        return result("predictive_advisory_cannot_trigger")
    missing_evidence = set(definition.evidence_requirements) - finding.evidence_types
    if missing_evidence:
        return result("required_evidence_missing")
    available = {str(item) for item in available_capabilities}
    if set(finding.required_capabilities) - available:
        return result("required_capability_unavailable")
    if not budget.available(stage):
        return result("semantic_budget_exhausted")
    return result("report_only" if mode is RepairMode.REPORT else "eligible", eligible=True)


def make_repair_finding(
    code: str,
    *,
    clip_index: int | None,
    objective: bool,
    values: Mapping[str, Any] | None = None,
    source: str = "deterministic_validator",
    required_capabilities: Iterable[str] = (),
) -> RepairFinding:
    definition = defect_definition(code)
    safe_values = {
        "code": definition.code,
        **({"clip_index": int(clip_index)} if clip_index is not None else {}),
        **dict(values or {}),
    }
    evidence = tuple(
        RepairEvidence(
            evidence_type=evidence_type,
            values=safe_values,
            clip_index=clip_index,
            source=source,
        )
        for evidence_type in definition.evidence_requirements
    )
    return RepairFinding(
        code=definition.code,
        objective=objective,
        evidence=evidence,
        clip_index=clip_index,
        required_capabilities=tuple(str(item) for item in required_capabilities),
    )


def bounded_repair_findings(
    findings: Iterable[RepairFinding],
) -> tuple[tuple[RepairFinding, ...], tuple[RepairFinding, ...]]:
    unique: dict[tuple[str, int | None, bool], RepairFinding] = {}
    for finding in findings:
        key = (defect_definition(finding.code).code, finding.clip_index, finding.objective)
        unique.setdefault(key, finding)
    ordered = tuple(sorted(
        unique.values(),
        key=lambda item: (
            0
            if item.objective
            and defect_definition(item.code).repair_strategy in {
                RepairStrategy.LLM_VISUAL_REPAIR,
                RepairStrategy.LLM_PLAN_REPAIR,
                RepairStrategy.CONDITIONAL_LLM_OR_FALLBACK,
            }
            else 1 if item.objective else 2,
            item.code,
            item.clip_index or 0,
        ),
    ))
    return ordered[:MAX_REPAIR_CODES], ordered[MAX_REPAIR_CODES:]


def repair_findings_from_preflight(report: Any) -> tuple[RepairFinding, ...]:
    findings: list[RepairFinding] = []
    for item in tuple(getattr(report, "findings", ())):
        source = str(getattr(item, "source", "") or "")
        match = re.search(r"(?:^|\.)clips\.(\d+)(?:\.|$)", source)
        clip_index = int(match.group(1)) if match else None
        segment_match = re.search(r"(?:^|\.)segments\.([A-Za-z0-9._:-]+)", source)
        values: dict[str, Any] = {
            "observed": str(getattr(item, "severity", "") or "finding"),
            "source": source[:120],
        }
        if segment_match:
            values["segment_id"] = segment_match.group(1)
        findings.append(make_repair_finding(
            str(getattr(item, "code", "") or ""),
            clip_index=clip_index,
            objective=str(getattr(item, "severity", "")) == "block",
            values=values,
            source="edit_preflight",
        ))
    return tuple(findings)


def repair_findings_from_visual_coverage(report: Any) -> tuple[RepairFinding, ...]:
    findings: list[RepairFinding] = []
    for segment in tuple(getattr(report, "segments", ())):
        for code in tuple(getattr(segment, "blocker_codes", ())):
            findings.append(make_repair_finding(
                str(code),
                clip_index=int(segment.clip_index),
                objective=True,
                values={
                    "segment_id": str(segment.segment_id),
                    "start_ms": int(segment.source_start_ms),
                    "end_ms": int(segment.source_end_ms),
                    "observation_count": int(segment.observation_count),
                    "maximum_gap_ms": int(segment.maximum_gap_ms),
                },
                source="visual_coverage",
            ))
    return tuple(findings)


def build_repair_report(
    *,
    mode: RepairMode,
    stage_records: Iterable[Mapping[str, Any]] = (),
    predictive_findings: Iterable[PredictiveFinding | Mapping[str, Any]] = (),
    fallback_entries: Iterable[Any] = (),
    reused_stages: Iterable[str] = (),
    recomputed_stages: Iterable[str] = (),
) -> dict[str, Any]:
    stages = [_repair_stage_record(item) for item in tuple(stage_records)[:2]]
    predictions = [
        _predictive_report_record(item)
        for item in tuple(predictive_findings)[:MAX_REPAIR_CODES]
    ]
    fallbacks = [_fallback_report_record(item) for item in tuple(fallback_entries)[:64]]
    report = _assemble_repair_report(
        mode=mode,
        stages=stages,
        predictions=predictions,
        fallbacks=fallbacks,
        reused_stages=reused_stages,
        recomputed_stages=recomputed_stages,
    )
    if _json_size(report) > MAX_REPAIR_REPORT_BYTES:
        raise RepairContractError("REPAIR_REPORT_TOO_LARGE", "repair report exceeds its byte budget")
    return report


def _assemble_repair_report(
    *,
    mode: RepairMode,
    stages: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    fallbacks: list[dict[str, Any]],
    reused_stages: Iterable[str],
    recomputed_stages: Iterable[str],
) -> dict[str, Any]:
    resolved = {
        code
        for stage in stages
        for code in stage["resolution"]["resolved_codes"]
    }
    remaining = {
        code
        for stage in stages
        for code in stage["resolution"]["remaining_codes"]
    }
    introduced = {
        code
        for stage in stages
        for code in stage["resolution"]["introduced_codes"]
    }
    not_repairable = {
        item["code"]
        for stage in stages
        for item in stage["dispositions"]
        if not item["eligible"]
    }
    fallback_applied = {
        _canonical_registered_code(item["requested"]) or item["code"]
        for item in fallbacks
    }
    return {
        "version": REPAIR_REPORT_VERSION,
        "registry_version": DEFECT_REGISTRY_VERSION,
        "registry_sha256": DEFECT_REGISTRY_SHA256,
        "mode": mode.value,
        "stages": stages,
        "predictive_findings": predictions,
        "fallbacks": fallbacks,
        "checkpoints": {
            "reused_stages": _safe_stage_names(reused_stages),
            "recomputed_stages": _safe_stage_names(recomputed_stages),
        },
        "summary": {
            "resolved_codes": sorted(resolved),
            "remaining_codes": sorted(remaining),
            "introduced_codes": sorted(introduced),
            "fallback_applied_codes": sorted(fallback_applied),
            "not_repairable_codes": sorted(not_repairable),
        },
    }


def validate_repair_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("version") != REPAIR_REPORT_VERSION:
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair report version is invalid")
    if (
        value.get("registry_version") != DEFECT_REGISTRY_VERSION
        or value.get("registry_sha256") != DEFECT_REGISTRY_SHA256
    ):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair registry metadata is invalid")
    try:
        mode = RepairMode(str(value.get("mode") or ""))
    except ValueError as exc:
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair report mode is invalid") from exc
    stages_value = value.get("stages")
    predictions_value = value.get("predictive_findings")
    fallbacks_value = value.get("fallbacks")
    if not isinstance(stages_value, list) or len(stages_value) > 2:
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair report stages are invalid")
    if not isinstance(predictions_value, list) or len(predictions_value) > MAX_REPAIR_CODES:
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair predictions are invalid")
    if not isinstance(fallbacks_value, list) or len(fallbacks_value) > 64:
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair fallbacks are invalid")
    checkpoints = value.get("checkpoints")
    if not isinstance(checkpoints, Mapping):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair checkpoints are invalid")
    if not isinstance(checkpoints.get("reused_stages"), list) or not isinstance(
        checkpoints.get("recomputed_stages"), list
    ):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair checkpoint stages are invalid")
    try:
        normalized = _assemble_repair_report(
            mode=mode,
            stages=[_repair_stage_record(item) for item in stages_value],
            predictions=[_predictive_report_record(item) for item in predictions_value],
            fallbacks=[_fallback_report_record(item) for item in fallbacks_value],
            reused_stages=checkpoints["reused_stages"],
            recomputed_stages=checkpoints["recomputed_stages"],
        )
    except RepairContractError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise RepairContractError(
            "REPAIR_REPORT_INVALID",
            "repair report contains invalid bounded values",
        ) from exc
    if _json_size(normalized) > MAX_REPAIR_REPORT_BYTES:
        raise RepairContractError("REPAIR_REPORT_TOO_LARGE", "repair report exceeds its byte budget")
    return normalized


def _repair_stage_record(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair stage must be an object")
    stage = str(value.get("stage") or "")
    status = str(value.get("status") or "not_triggered")
    if stage not in {"visual_understanding", "plan_repair"}:
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair stage is invalid")
    if status not in {"not_triggered", "report_only", "repaired", "rejected", "failed"}:
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair stage status is invalid")
    request = value.get("request")
    if not isinstance(request, Mapping):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair request metadata is invalid")
    for key in (
        "affected_clip_ids",
        "objective_codes",
        "advisory_codes",
        "evidence_types",
        "evidence_ids",
    ):
        if not isinstance(request.get(key), (list, tuple, set)):
            raise RepairContractError("REPAIR_REPORT_INVALID", "repair request lists are invalid")
    disposition_values = value.get("dispositions")
    if not isinstance(disposition_values, (list, tuple)):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair dispositions are invalid")
    dispositions = []
    for item in tuple(disposition_values)[:MAX_REPAIR_CODES]:
        if not isinstance(item, Mapping):
            continue
        code = defect_definition(str(item.get("code") or "")).code
        dispositions.append({
            "code": code,
            "strategy": defect_definition(code).repair_strategy.value,
            "eligible": item.get("eligible") is True,
            "would_call": item.get("would_call") is True,
            "call_allowed": item.get("call_allowed") is True,
            "reason": _safe_token(item.get("reason")),
            "fallback_code": (
                defect_definition(str(item.get("fallback_code") or "")).code
                if item.get("fallback_code")
                else None
            ),
        })
    resolution = value.get("resolution")
    if not isinstance(resolution, Mapping):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair resolution is invalid")
    for key in (
        "original_codes",
        "resolved_codes",
        "remaining_codes",
        "introduced_codes",
    ):
        if not isinstance(resolution.get(key), (list, tuple, set)):
            raise RepairContractError("REPAIR_REPORT_INVALID", "repair resolution lists are invalid")
    quality_floor = value.get("quality_floor")
    if not isinstance(quality_floor, Mapping):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair quality floor is invalid")
    if not isinstance(quality_floor.get("violation_codes"), (list, tuple, set)):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair quality violations are invalid")
    attempt_values = value.get("attempts") or ()
    if not isinstance(attempt_values, (list, tuple)):
        raise RepairContractError("REPAIR_REPORT_INVALID", "repair attempts are invalid")
    attempts = [
        _repair_attempt_record(item)
        for item in tuple(attempt_values)[:16]
        if isinstance(item, Mapping)
    ]
    return {
        "stage": stage,
        "status": status,
        "request": _repair_request_record(request),
        "dispositions": dispositions,
        "resolution": {
            "original_codes": _registered_codes(resolution.get("original_codes")),
            "resolved_codes": _registered_codes(resolution.get("resolved_codes")),
            "remaining_codes": _registered_codes(resolution.get("remaining_codes")),
            "introduced_codes": _registered_codes(resolution.get("introduced_codes")),
        },
        "quality_floor": {
            "accepted": quality_floor.get("accepted") is True,
            "violation_codes": sorted({
                _safe_token(code)
                for code in quality_floor.get("violation_codes") or ()
                if _safe_token(code)
            })[:32],
        },
        "attempts": attempts,
        "checkpoint_reused": value.get("checkpoint_reused") is True,
    }


def _repair_request_record(value: Mapping[str, Any]) -> dict[str, Any]:
    hashes = {}
    for key in (
        "repair_prompt_sha256",
        "response_schema_sha256",
        "request_fingerprint",
        "editing_prompt_sha256",
        "transcript_sha256",
        "candidate_sha256",
    ):
        candidate = str(value.get(key) or "").lower()
        hashes[key] = candidate if re.fullmatch(r"[a-f0-9]{64}", candidate) else ""
    return {
        "report_version": _safe_token(value.get("report_version")),
        "request_version": _safe_token(value.get("request_version")),
        "response_schema": _safe_token(value.get("response_schema")),
        "repair_prompt_version": _safe_token(value.get("repair_prompt_version")),
        **hashes,
        "affected_clip_ids": sorted({
            parsed
            for item in value.get("affected_clip_ids") or ()
            if (parsed := _bounded_int(item, 1, MAX_REPAIR_CLIPS)) is not None
        }),
        "objective_codes": _registered_codes(value.get("objective_codes")),
        "advisory_codes": _registered_codes(value.get("advisory_codes")),
        "evidence_types": sorted({
            _safe_token(item)
            for item in value.get("evidence_types") or ()
            if _safe_token(item)
        })[:32],
        "evidence_ids": sorted({
            _safe_token(item)
            for item in value.get("evidence_ids") or ()
            if _safe_token(item)
        })[:MAX_REPAIR_EVIDENCE_RECORDS],
        "evidence_count": max(0, min(int(value.get("evidence_count") or 0), 64)),
        "would_call": value.get("would_call") is True,
        "call_allowed": value.get("call_allowed") is True,
    }


def _repair_attempt_record(value: Mapping[str, Any]) -> dict[str, Any]:
    number = _bounded_int(value.get("number") or 0, 0, 20)
    status_code = (
        _bounded_int(value.get("status_code"), 0, 999)
        if value.get("status_code") is not None
        else None
    )
    duration_ms = _bounded_int(value.get("duration_ms") or 0, 0, 3_600_000)
    return {
        "category": _safe_token(value.get("category")),
        "number": number if number is not None else 0,
        "status_code": status_code,
        "reason": _safe_token(value.get("reason")),
        "duration_ms": duration_ms if duration_ms is not None else 0,
        "input_tokens": _optional_nonnegative_int(value.get("input_tokens")),
        "output_tokens": _optional_nonnegative_int(value.get("output_tokens")),
        "reasoning_tokens": _optional_nonnegative_int(value.get("reasoning_tokens")),
        "total_tokens": _optional_nonnegative_int(value.get("total_tokens")),
        "cost_usd": _optional_cost(value.get("cost_usd")),
    }


def _predictive_report_record(value: PredictiveFinding | Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, (PredictiveFinding, Mapping)):
        raise RepairContractError("REPAIR_REPORT_INVALID", "predictive finding is invalid")
    source = value if isinstance(value, Mapping) else asdict(value)
    code = defect_definition(str(source.get("code") or "")).code
    clip_index = _bounded_int(source.get("clip_index") or 1, 1, MAX_REPAIR_CLIPS)
    confidence = _finite_number(source.get("confidence") or 0)
    return {
        "code": code,
        "clip_index": clip_index if clip_index is not None else 1,
        "segment_id": _safe_token(source.get("segment_id")),
        "objective": source.get("objective") is True,
        "confidence": max(0.0, min(confidence if confidence is not None else 0.0, 1.0)),
        "detector": _safe_token(source.get("detector")),
        "threshold": str(source.get("threshold") or "")[:120],
    }


def _fallback_report_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) and not hasattr(value, "__dataclass_fields__"):
        raise RepairContractError("REPAIR_REPORT_INVALID", "fallback entry is invalid")
    source = value if isinstance(value, Mapping) else asdict(value)
    code = defect_definition(str(source.get("code") or "")).code
    requested = str(source.get("requested") or "")
    clip_index = _bounded_int(source.get("clip_index") or 0, 0, 50)
    return {
        "code": code,
        "clip_index": clip_index if clip_index is not None else 0,
        "segment_id": _safe_token(source.get("segment_id")),
        "requested": _canonical_registered_code(requested) or _safe_token(requested),
        "executed": _safe_token(source.get("executed")),
    }


def _canonical_registered_code(value: Any) -> str:
    candidate = str(value or "")
    if not candidate:
        return ""
    canonical = defect_definition(candidate).code
    return "" if canonical == "UNKNOWN_DEFECT" and candidate != canonical else canonical


def _registered_codes(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted({defect_definition(str(code)).code for code in values})[:MAX_REPAIR_CODES]


def _safe_stage_names(values: Iterable[str]) -> list[str]:
    return sorted({
        _safe_token(value)
        for value in values
        if _safe_token(value)
    })[:32]


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    return _bounded_int(value, 0, 100_000_000)


def _optional_cost(value: Any) -> float | None:
    if value is None:
        return None
    parsed = _finite_number(value)
    if parsed is None:
        return None
    if parsed < 0:
        return None
    return round(min(parsed, 1_000_000), 8)


def build_repair_batch(
    *,
    stage: RepairStage,
    mode: RepairMode,
    findings: Iterable[RepairFinding],
    budget: RepairBudget,
    candidate_clips: Mapping[int, Mapping[str, Any]],
    available_capabilities: Iterable[str],
    catalog_context: Mapping[str, Any] | None,
    immutable_constraints: Mapping[str, Any],
    editing_prompt: str,
    transcript_excerpts: Iterable[TranscriptExcerpt] = (),
    rendering_started: bool = False,
) -> tuple[RepairBatchRequest, tuple[RepairDisposition, ...]]:
    all_findings = tuple(findings)
    capabilities = tuple(str(item) for item in available_capabilities)
    if len(all_findings) > MAX_REPAIR_CODES:
        raise RepairContractError("REPAIR_CODE_LIMIT_EXCEEDED", "too many repair findings")
    dispositions = tuple(
        repair_disposition(
            finding,
            stage=stage,
            mode=mode,
            budget=budget,
            available_capabilities=capabilities,
            rendering_started=rendering_started,
        )
        for finding in all_findings
    )
    eligible = [
        finding
        for finding, disposition in zip(all_findings, dispositions)
        if disposition.eligible
    ]
    if not eligible:
        raise RepairContractError("REPAIR_NOT_ELIGIBLE", "no objective finding can enter this repair stage")
    objective = eligible[:MAX_REPAIR_CODES]
    advisory = [
        finding
        for finding in all_findings
        if not finding.objective
        and defect_definition(finding.code).repair_strategy is RepairStrategy.ADVISORY
        and set(defect_definition(finding.code).evidence_requirements)
        <= finding.evidence_types
    ][: max(0, MAX_REPAIR_CODES - len(objective))]
    affected = sorted({
        int(finding.clip_index)
        for finding in objective
        if finding.clip_index is not None
    })
    if not affected:
        affected = sorted(int(index) for index in candidate_clips)
    if (
        not affected
        or len(affected) > MAX_REPAIR_CLIPS
        or any(index < 1 or index > MAX_REPAIR_CLIPS for index in affected)
    ):
        raise RepairContractError("REPAIR_CLIP_LIMIT_EXCEEDED", "affected clips are out of bounds")
    clips: list[dict[str, Any]] = []
    for clip_index in affected:
        candidate = candidate_clips.get(clip_index)
        if candidate is None:
            raise RepairContractError("REPAIR_CANDIDATE_MISSING", "affected clip candidate is missing")
        clip = _bounded_context(candidate, label="candidate clip")
        clip["clip_index"] = clip_index
        clips.append(clip)
    if _json_size(clips) > MAX_REPAIR_CANDIDATE_BYTES:
        raise RepairContractError("REPAIR_CANDIDATE_TOO_LARGE", "candidate clips exceed their byte budget")
    evidence = [
        record.to_dict()
        for finding in (*objective, *advisory)
        for record in finding.evidence
    ]
    if len(evidence) > MAX_REPAIR_EVIDENCE_RECORDS:
        raise RepairContractError("REPAIR_EVIDENCE_LIMIT_EXCEEDED", "too many evidence records")
    prompt = str(editing_prompt or "").strip()
    if not prompt or len(prompt.encode("utf-8")) > MAX_REPAIR_PROMPT_BYTES or "\x00" in prompt:
        raise RepairContractError("REPAIR_PROMPT_INVALID", "editing prompt is empty or exceeds its byte budget")
    excerpts = tuple(
        excerpt.to_dict()
        for excerpt in transcript_excerpts
        if excerpt.clip_index in affected
    )
    if len(excerpts) > 32 or len(_canonical_json(list(excerpts)).encode("utf-8")) > MAX_REPAIR_TRANSCRIPT_BYTES:
        raise RepairContractError("REPAIR_TRANSCRIPT_TOO_LARGE", "transcript excerpts exceed their bounds")
    request = RepairBatchRequest(
        stage=stage,
        mode=mode,
        defects=tuple(_defect_payload(finding) for finding in objective),
        supplemental_advisories=tuple(_defect_payload(finding) for finding in advisory),
        candidate_clips=tuple(clips),
        evidence=tuple(evidence),
        available_capabilities=tuple(sorted({_required_safe_token(item) for item in capabilities})),
        catalog_context=_compact_catalog_context(catalog_context or {}),
        immutable_constraints=_bounded_context(
            immutable_constraints,
            label="immutable constraints",
        ),
        editing_prompt=prompt,
        transcript_excerpts=excerpts,
    )
    request.to_provider_dict()
    return request, dispositions


def compute_repair_resolution(
    original_codes: Iterable[str],
    repaired_codes: Iterable[str],
) -> RepairResolution:
    original = tuple(sorted({str(code) for code in original_codes}))
    repaired = tuple(sorted({str(code) for code in repaired_codes}))
    original_set = set(original)
    repaired_set = set(repaired)
    return RepairResolution(
        original_codes=original,
        repaired_codes=repaired,
        resolved_codes=tuple(sorted(original_set - repaired_set)),
        remaining_codes=tuple(sorted(original_set & repaired_set)),
        introduced_codes=tuple(sorted(repaired_set - original_set)),
    )


def evaluate_repair_quality_floor(
    original: EditPlan,
    repaired: EditPlan,
    *,
    original_codes: Iterable[str],
    repaired_codes: Iterable[str],
    available_capabilities: Iterable[str],
    affected_clip_indexes: Iterable[int],
    affected_operation_ids: Iterable[str] = (),
    allow_catalog_change_clip_indexes: Iterable[int] = (),
) -> RepairQualityFloor:
    violations: set[str] = set()
    resolution = compute_repair_resolution(original_codes, repaired_codes)
    original_by_clip = {clip.clip_index: clip for clip in original.clips}
    repaired_by_clip = {clip.clip_index: clip for clip in repaired.clips}
    if set(original_by_clip) != set(repaired_by_clip):
        violations.add("REPAIR_OUTPUT_COUNT_CHANGED")
    affected = {int(item) for item in affected_clip_indexes}
    allowed_catalog_changes = {int(item) for item in allow_catalog_change_clip_indexes}
    affected_operations = {str(item) for item in affected_operation_ids}
    for clip_index, original_clip in original_by_clip.items():
        repaired_clip = repaired_by_clip.get(clip_index)
        if repaired_clip is None:
            continue
        if original_clip.source_window != repaired_clip.source_window:
            violations.add("REPAIR_SOURCE_WINDOW_CHANGED")
        if clip_index not in affected and original_clip != repaired_clip:
            violations.add("REPAIR_UNAFFECTED_OPERATION_REMOVED")
        if (
            clip_index not in allowed_catalog_changes
            and not _catalog_selection_preserved(
                original_clip.catalog_selection,
                repaired_clip.catalog_selection,
            )
        ):
            violations.add("REPAIR_CATALOG_STYLE_LOST")
        protected = _operation_ids(original_clip) - affected_operations
        original_operations = _operations_by_id(original_clip)
        repaired_operations = _operations_by_id(repaired_clip)
        if any(
            operation_id not in repaired_operations
            or repaired_operations[operation_id] != original_operations[operation_id]
            for operation_id in protected
        ):
            violations.add("REPAIR_UNAFFECTED_OPERATION_REMOVED")
    if "subtitles" in original.requested_capabilities and "subtitles" not in repaired.requested_capabilities:
        violations.add("REPAIR_SUBTITLE_REQUIREMENT_LOST")
    if set(repaired.requested_capabilities) - {str(item) for item in available_capabilities}:
        violations.add("REPAIR_CAPABILITY_UNSUPPORTED")
    if resolution.introduced_codes:
        violations.add("REPAIR_NEW_DEFECT_INTRODUCED")
    return RepairQualityFloor(
        accepted=not violations,
        violation_codes=tuple(sorted(violations)),
        resolution=resolution,
    )


def predict_plan_findings(
    plan: EditPlan | Mapping[str, Any],
    *,
    source_aspect_ratios: Mapping[int, float] | None = None,
) -> tuple[PredictiveFinding, ...]:
    payload = plan.to_dict() if isinstance(plan, EditPlan) else dict(plan)
    findings: list[PredictiveFinding] = []
    ratios = source_aspect_ratios or {}
    for clip in payload.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        clip_index = _bounded_int(clip.get("clip_index"), 1, MAX_REPAIR_CLIPS)
        if clip_index is None:
            continue
        segments = [item for item in clip.get("segments") or [] if isinstance(item, dict)]
        overlay_ids: set[str] = set()
        for segment in segments:
            segment_id = _safe_token(segment.get("id"))
            segment_window = _window(segment.get("timeline_window"))
            duration_ms = max(0, segment_window[1] - segment_window[0]) if segment_window else 0
            overlays = [item for item in segment.get("overlays") or [] if isinstance(item, dict)]
            layout = segment.get("layout") if isinstance(segment.get("layout"), dict) else {}
            if duration_ms >= 8_000 and not overlays and layout.get("mode") in {"fit", "source"}:
                findings.append(_predictive(
                    "PREDICTIVE_LONG_VISUAL_HOLD_RISK",
                    clip_index,
                    segment_id,
                    0.65,
                    {"duration_ms": duration_ms},
                ))
            for overlay in overlays:
                overlay_id = _safe_token(overlay.get("id"))
                if overlay_id in overlay_ids:
                    findings.append(_predictive(
                        "PREDICTIVE_OVERLAY_DUPLICATE",
                        clip_index,
                        segment_id,
                        1.0,
                        {"operation_id": overlay_id},
                    ))
                overlay_ids.add(overlay_id)
                opacity = _finite_number(overlay.get("opacity"))
                if opacity is not None and opacity < 0.15:
                    findings.append(_predictive(
                        "PREDICTIVE_OVERLAY_OPACITY_LOW",
                        clip_index,
                        segment_id,
                        1.0,
                        {"opacity": opacity, "operation_id": overlay_id},
                    ))
                width = _finite_number(overlay.get("width_ratio"))
                margin = _finite_number(overlay.get("margin_ratio"))
                if width is not None and margin is not None and width + (2 * margin) > 1:
                    findings.append(_predictive(
                        "PREDICTIVE_OVERLAY_GEOMETRY_INVALID",
                        clip_index,
                        segment_id,
                        1.0,
                        {"width_ratio": width, "margin_ratio": margin},
                    ))
                overlay_window = _window(overlay.get("timeline_window"))
                if (
                    segment_window is None
                    or overlay_window is None
                    or overlay_window[0] < segment_window[0]
                    or overlay_window[1] > segment_window[1]
                ):
                    findings.append(_predictive(
                        "PREDICTIVE_OVERLAY_TIMING_INVALID",
                        clip_index,
                        segment_id,
                        1.0,
                        {"operation_id": overlay_id},
                    ))
                if overlay.get("protect_subtitles") and overlay.get("position") in {
                    "bottom",
                    "bottom_left",
                    "bottom_right",
                }:
                    findings.append(_predictive(
                        "PREDICTIVE_SUBTITLE_SAFE_ZONE_CONFLICT",
                        clip_index,
                        segment_id,
                        1.0,
                        {"position": overlay.get("position")},
                    ))
            ratio = _finite_number(ratios.get(clip_index))
            if layout.get("mode") in {"fit", "letterbox"} and ratio and ratio > 0:
                predicted_height = min(1.0, (9 / 16) / ratio)
                if predicted_height < 0.35:
                    findings.append(_predictive(
                        "PREDICTIVE_ACTIVE_PICTURE_RISK",
                        clip_index,
                        segment_id,
                        0.95,
                        {"height_ratio": round(predicted_height, 6)},
                    ))
        if segments:
            first = segments[0]
            first_layout = first.get("layout") if isinstance(first.get("layout"), dict) else {}
            if not first.get("overlays") and first_layout.get("mode") in {"fit", "source"}:
                findings.append(_predictive(
                    "PREDICTIVE_INACTIVE_HOOK_RISK",
                    clip_index,
                    _safe_token(first.get("id")),
                    0.55,
                    {"duration_ms": 3_000},
                ))
            if len(segments) == 1 and (_window(first.get("timeline_window")) or (0, 0))[1] >= 12_000:
                findings.append(_predictive(
                    "PREDICTIVE_RHYTHM_RISK",
                    clip_index,
                    _safe_token(first.get("id")),
                    0.6,
                    {"count": 1},
                ))
            for segment in segments:
                window = _window(segment.get("timeline_window"))
                if window and window[1] - window[0] >= 6_000 and not segment.get("overlays"):
                    findings.append(_predictive(
                        "PREDICTIVE_ATTENTION_GAP_RISK",
                        clip_index,
                        _safe_token(segment.get("id")),
                        0.55,
                        {"duration_ms": window[1] - window[0]},
                    ))
                    break
    unique = {
        (item.code, item.clip_index, item.segment_id): item
        for item in findings
    }
    return tuple(unique[key] for key in sorted(unique))


def _predictive(
    code: str,
    clip_index: int,
    segment_id: str,
    confidence: float,
    evidence: dict[str, Any],
) -> PredictiveFinding:
    definition = defect_definition(code)
    return PredictiveFinding(
        code=code,
        clip_index=clip_index,
        segment_id=segment_id,
        objective=definition.trigger_eligible,
        confidence=confidence,
        detector=str(definition.detector or "predictive_plan"),
        threshold=str(definition.threshold or ""),
        evidence=evidence,
    )


def _defect_payload(finding: RepairFinding) -> dict[str, Any]:
    definition = defect_definition(finding.code)
    return {
        "code": definition.code,
        "clip_index": finding.clip_index,
        "description": definition.description_en,
        "repair_strategy": definition.repair_strategy.value,
        "fallback_code": definition.safe_fallback_code,
    }


def _compact_catalog_context(value: Mapping[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for item in list(value.get("entries") or [])[:32]:
        if not isinstance(item, dict):
            continue
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        entries.append({
            "id": _safe_token(item.get("id")),
            "kind": _safe_token(item.get("kind")),
            "operation": _safe_token(config.get("operation")),
            "catalog_ids": [
                _safe_token(entry_id)
                for entry_id in list(config.get("catalog_ids") or [])[:16]
            ],
        })
    return {
        "catalog_version": _safe_token(value.get("catalog_version")),
        "manifest_sha256": _safe_hash(value.get("manifest_sha256")),
        "entries": entries,
    }


def _bounded_context(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    def clean(item: Any, depth: int = 0) -> Any:
        # A candidate wraps the strict edit-plan response, whose overlay windows
        # legitimately reach nine levels while still fitting the byte budget.
        if depth > MAX_REPAIR_CONTEXT_DEPTH:
            raise RepairContractError("REPAIR_CONTEXT_INVALID", f"{label} is too deeply nested")
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise RepairContractError("REPAIR_CONTEXT_INVALID", f"{label} has a non-finite number")
            return item
        if isinstance(item, str):
            if "\x00" in item:
                raise RepairContractError("REPAIR_CONTEXT_INVALID", f"{label} contains a null byte")
            return item[:7_000]
        if isinstance(item, (list, tuple)):
            if len(item) > 64:
                raise RepairContractError("REPAIR_CONTEXT_INVALID", f"{label} list is too large")
            return [clean(value, depth + 1) for value in item]
        if isinstance(item, Mapping):
            if len(item) > 64:
                raise RepairContractError("REPAIR_CONTEXT_INVALID", f"{label} object is too large")
            result: dict[str, Any] = {}
            for key, nested in item.items():
                name = str(key)
                normalized = name.lower()
                if any(part in normalized for part in _BLOCKED_CONTEXT_KEY_PARTS):
                    raise RepairContractError(
                        "REPAIR_CONTEXT_PRIVATE",
                        f"{label} contains a blocked field: {name}",
                    )
                result[name[:80]] = clean(nested, depth + 1)
            return result
        raise RepairContractError("REPAIR_CONTEXT_INVALID", f"{label} has an unsupported value")

    cleaned = clean(value)
    if not isinstance(cleaned, dict):
        raise RepairContractError("REPAIR_CONTEXT_INVALID", f"{label} must be an object")
    return cleaned


def _operation_ids(clip: Any) -> set[str]:
    return {
        item
        for item in (
            *(segment.id for segment in clip.segments),
            *(overlay.id for segment in clip.segments for overlay in segment.overlays),
            *(asset.id for asset in clip.asset_requests),
        )
        if item
    }


def _operations_by_id(clip: Any) -> dict[str, dict[str, Any]]:
    operations: dict[str, dict[str, Any]] = {}
    for segment in clip.segments:
        operations[segment.id] = segment.model_dump(mode="json")
        for overlay in segment.overlays:
            operations[overlay.id] = overlay.model_dump(mode="json")
    for asset in clip.asset_requests:
        operations[asset.id] = asset.model_dump(mode="json")
    return operations


def _catalog_selection_preserved(original: Any, repaired: Any) -> bool:
    for field in (
        "style_profile_id",
        "caption_treatment_id",
        "color_treatment_id",
        "recipe_ids",
    ):
        original_value = getattr(original, field)
        if original_value and getattr(repaired, field) != original_value:
            return False
    return True


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return round(value, 6)
        raise RepairContractError("REPAIR_EVIDENCE_INVALID", "evidence number is not finite")
    if isinstance(value, str):
        return _safe_token(value)
    raise RepairContractError("REPAIR_EVIDENCE_INVALID", "evidence value must be scalar")


def _safe_token(value: Any) -> str:
    token = str(value or "").strip()[:120]
    return token if _SAFE_TOKEN.fullmatch(token) else ""


def _required_safe_token(value: Any) -> str:
    token = _safe_token(value)
    if not token:
        raise RepairContractError("REPAIR_CONTEXT_INVALID", "context token is invalid")
    return token


def _safe_hash(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if re.fullmatch(r"[a-f0-9]{64}", candidate) else ""


def _finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _bounded_int(value: Any, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _window(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        start = int(value["start_ms"])
        end = int(value["end_ms"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return (start, end) if 0 <= start < end else None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_size(value: Any) -> int:
    return len(_canonical_json(value).encode("utf-8"))


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "MAX_REPAIR_CLIPS",
    "MAX_REPAIR_CODES",
    "REPAIR_BATCH_REQUEST_VERSION",
    "REPAIR_REPORT_VERSION",
    "REPAIR_RESOLUTION_VERSION",
    "PredictiveFinding",
    "RepairBatchRequest",
    "RepairBudget",
    "RepairContractError",
    "RepairDisposition",
    "RepairEvidence",
    "RepairFinding",
    "RepairMode",
    "RepairQualityFloor",
    "RepairResolution",
    "RepairStage",
    "TranscriptExcerpt",
    "build_repair_batch",
    "build_repair_report",
    "bounded_repair_findings",
    "compute_repair_resolution",
    "evaluate_repair_quality_floor",
    "make_repair_finding",
    "predict_plan_findings",
    "repair_findings_from_preflight",
    "repair_findings_from_visual_coverage",
    "repair_disposition",
    "resolve_repair_mode",
    "validate_repair_report",
]
