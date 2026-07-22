from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any
import json
import logging
import math
import re
import uuid

from open_storyline.mvp.defects import DEFECT_REGISTRY
from open_storyline.mvp.security import sanitize_text


LOGGER = logging.getLogger("openstoryline.mvp")
REQUEST_ID = ContextVar("openstoryline_mvp_request_id", default="")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SAFE_CODE = re.compile(r"^[A-Z0-9_]{1,120}$")
SAFE_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
QUALITY_FEEDBACK_VERSION = "quality_feedback.v1"
REPAIR_OBSERVABILITY_VERSION = "repair_observability.v1"
REPAIR_EVIDENCE_TYPES = frozenset(
    evidence_type
    for definition in DEFECT_REGISTRY.values()
    for evidence_type in definition.evidence_requirements
)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _records(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:limit] if isinstance(item, dict)]


def _number(value: Any, *, minimum: float = 0, maximum: float = 1_000_000) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 6) if math.isfinite(parsed) and minimum <= parsed <= maximum else None


def _integer(value: Any, *, minimum: int = 0, maximum: int = 86_400_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return minimum
    return min(max(parsed, minimum), maximum)


def _token(value: Any, *, limit: int = 80) -> str:
    candidate = str(value or "")
    return candidate[:limit] if SAFE_ID.fullmatch(candidate) else ""


def _codes(values: Any, *, limit: int = 32) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted({
        value
        for value in values
        if isinstance(value, str) and SAFE_CODE.fullmatch(value)
    })[:limit]


def _versions(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted({
        value
        for value in values
        if isinstance(value, str) and SAFE_VERSION.fullmatch(value)
    })[:16]


def compact_prior_attempt_quality_feedback(
    *,
    prior_attempt_id: str,
    prior_attempt_number: int,
    documents: dict[str, Any],
) -> dict[str, Any]:
    promotion = _mapping(documents.get("render_promotion.json"))
    frame_quality = _mapping(documents.get("frame_quality_qa.json"))
    visual_coverage = _mapping(documents.get("clip_visual_coverage.json"))
    conformance = _mapping(documents.get("creative_conformance.json"))
    outcome_report = _mapping(documents.get("outcome_report.json"))
    fallback_ledger = _mapping(documents.get("fallback_ledger.json"))
    blocker_codes = set(_codes(promotion.get("blocker_codes")))
    limitations: list[dict[str, Any]] = []
    for limitation in _records(outcome_report.get("limitations"), limit=64):
        code = str(limitation.get("code") or "")
        if not SAFE_CODE.fullmatch(code):
            continue
        blocker_codes.add(code)
        limitations.append({
            "code": code,
            "stage": _token(limitation.get("stage"), limit=40),
            "clip_index": _integer(limitation.get("clip_index"), maximum=50),
            "segment_id": _token(limitation.get("segment_id")),
            "requested": _token(limitation.get("requested"), limit=120),
            "executed": _token(limitation.get("executed"), limit=120),
            "recommended_retry_action": _token(
                limitation.get("recommended_retry_action"),
                limit=40,
            ),
        })
    for entry in _records(fallback_ledger.get("entries"), limit=64):
        code = str(entry.get("code") or "")
        if not SAFE_CODE.fullmatch(code) or any(
            item["code"] == code
            and item["segment_id"] == _token(entry.get("segment_id"))
            for item in limitations
        ):
            continue
        blocker_codes.add(code)
        limitations.append({
            "code": code,
            "stage": "compile",
            "clip_index": _integer(entry.get("clip_index"), maximum=50),
            "segment_id": _token(entry.get("segment_id")),
            "requested": _token(entry.get("requested"), limit=120),
            "executed": _token(entry.get("executed"), limit=120),
            "recommended_retry_action": _token(entry.get("retry_action"), limit=40),
        })
    asset_findings: list[dict[str, str]] = []
    for finding in _records(conformance.get("findings"), limit=64):
        code = str(finding.get("code") or "")
        if SAFE_CODE.fullmatch(code.upper()) and "ASSET" in code.upper():
            severity = str(finding.get("severity") or "warning").lower()
            asset_findings.append({
                "code": code.upper(),
                "severity": severity if severity in {"info", "warning", "blocker"} else "warning",
            })
            blocker_codes.add(code.upper())
    crop_windows: list[dict[str, Any]] = []
    for segment in _records(visual_coverage.get("segments"), limit=64):
        codes = _codes(segment.get("blocker_codes"))
        if not codes:
            continue
        blocker_codes.update(codes)
        crop_windows.append({
            "clip_index": _integer(segment.get("clip_index"), maximum=8),
            "segment_id": _token(segment.get("segment_id")),
            "source_start_ms": _integer(segment.get("source_start_ms")),
            "source_end_ms": _integer(segment.get("source_end_ms")),
            "codes": codes,
            "observation_count": _integer(segment.get("observation_count"), maximum=10_000),
            "maximum_gap_ms": _integer(segment.get("maximum_gap_ms")),
        })
    active_picture: list[dict[str, Any]] = []
    metric_samples: list[dict[str, Any]] = []
    for clip in _records(frame_quality.get("clips"), limit=8):
        clip_index = _integer(clip.get("clip_index"), maximum=8)
        active_summary = _mapping(_mapping(clip.get("active_picture")).get("summary"))
        active_picture.append({
            "clip_index": clip_index,
            "median_active_area_ratio": _number(
                active_summary.get("median_active_area_ratio"), maximum=1
            ),
            "minimum_active_area_ratio": _number(
                active_summary.get("minimum_active_area_ratio"), maximum=1
            ),
            "median_active_height_ratio": _number(
                active_summary.get("median_active_height_ratio"), maximum=1
            ),
        })
        samples = _mapping(clip.get("reference_metrics")).get("samples")
        for sample in _records(samples, limit=16):
            metric_samples.append({
                "clip_index": clip_index,
                "timestamp_ms": _integer(sample.get("timestamp_ms")),
                "segment_id": _token(sample.get("segment_id")),
                "operation": _token(sample.get("operation"), limit=40),
                "strategy": _token(sample.get("strategy"), limit=40),
                "ssim": _number(sample.get("ssim"), maximum=1),
                "psnr": _number(sample.get("psnr"), maximum=1000),
            })
        for finding in _records(clip.get("findings"), limit=32):
            code = str(finding.get("code") or "")
            if SAFE_CODE.fullmatch(code):
                blocker_codes.add(code)
    metric_samples.sort(key=lambda item: (
        item["ssim"] if item["ssim"] is not None else 2,
        item["psnr"] if item["psnr"] is not None else 2000,
        item["timestamp_ms"],
    ))
    caption_footprints: list[dict[str, Any]] = []
    for name, document in sorted(documents.items()):
        if not name.endswith(".caption-footprint.json") or not isinstance(document, dict):
            continue
        summary = _mapping(document.get("summary"))
        codes = _codes(summary.get("blocker_codes"))
        blocker_codes.update(codes)
        caption_footprints.append({
            "blocker_codes": codes,
            "maximum_width_ratio": _number(summary.get("maximum_width_ratio"), maximum=1),
            "maximum_height_ratio": _number(summary.get("maximum_height_ratio"), maximum=1),
            "worst_cue_index": _integer(summary.get("worst_cue_index"), maximum=10_000),
        })
    return {
        "version": QUALITY_FEEDBACK_VERSION,
        "prior_attempt_id": prior_attempt_id,
        "prior_attempt_number": _integer(prior_attempt_number, minimum=1, maximum=1_000_000),
        "evidence_versions": _versions([
            promotion.get("version"),
            frame_quality.get("version"),
            visual_coverage.get("version"),
            conformance.get("version"),
            outcome_report.get("version"),
            fallback_ledger.get("version"),
        ]),
        "blocker_codes": sorted(blocker_codes)[:32],
        "retry_reason_codes": sorted({item["code"] for item in limitations})[:32],
        "prior_outcome_grade": _token(outcome_report.get("grade"), limit=40),
        "limitations": limitations[:24],
        "asset_findings": asset_findings[:16],
        "crop_windows": crop_windows[:16],
        "active_picture": active_picture,
        "caption_footprints": caption_footprints[:8],
        "worst_metric_samples": metric_samples[:12],
    }


def compact_repair_observability(report: dict[str, Any]) -> dict[str, Any]:
    """Allowlist repair telemetry so transient editorial context cannot persist."""
    source = _mapping(report)
    hashes = {
        key: str(source.get(key) or "").lower()
        for key in (
            "repair_prompt_sha256",
            "response_schema_sha256",
            "request_fingerprint",
            "editing_prompt_sha256",
            "transcript_sha256",
            "candidate_sha256",
        )
    }
    affected_values = source.get("affected_clip_ids")
    evidence_values = source.get("evidence_types")
    evidence_ids = source.get("evidence_ids")
    objective_codes = [
        code for code in _codes(source.get("objective_codes")) if code in DEFECT_REGISTRY
    ]
    advisory_codes = [
        code for code in _codes(source.get("advisory_codes")) if code in DEFECT_REGISTRY
    ]
    stage = str(source.get("stage") or "")
    mode = str(source.get("mode") or "")
    return {
        "version": REPAIR_OBSERVABILITY_VERSION,
        "report_version": (
            "repair_report.v1" if source.get("version") == "repair_report.v1" else ""
        ),
        "request_version": (
            "repair_batch_request.v1"
            if source.get("request_version") == "repair_batch_request.v1"
            else ""
        ),
        "stage": stage if stage in {"visual_understanding", "plan_repair"} else "",
        "mode": mode if mode in {"off", "report", "enforce"} else "",
        "semantic_attempt": _integer(source.get("semantic_attempt"), maximum=1),
        "response_schema": _token(source.get("response_schema")),
        "repair_prompt_version": _token(source.get("repair_prompt_version")),
        **{
            key: value if re.fullmatch(r"[a-f0-9]{64}", value) else ""
            for key, value in hashes.items()
        },
        "editing_prompt_bytes": _integer(source.get("editing_prompt_bytes"), maximum=12_000),
        "transcript_bytes": _integer(source.get("transcript_bytes"), maximum=16_000),
        "affected_clip_ids": sorted({
            value
            for value in (
                affected_values
                if isinstance(affected_values, (list, tuple, set))
                else ()
            )
            if isinstance(value, int) and 1 <= value <= 8
        }),
        "objective_codes": objective_codes,
        "advisory_codes": advisory_codes,
        "evidence_types": sorted({
            value
            for value in (
                evidence_values
                if isinstance(evidence_values, (list, tuple, set))
                else ()
            )
            if isinstance(value, str) and value in REPAIR_EVIDENCE_TYPES
        })[:32],
        "evidence_ids": sorted({
            value
            for value in (
                evidence_ids
                if isinstance(evidence_ids, (list, tuple, set))
                else ()
            )
            if isinstance(value, str) and SAFE_ID.fullmatch(value)
        })[:64],
        "evidence_count": _integer(source.get("evidence_count"), maximum=64),
        "would_call": source.get("would_call") is True,
        "call_allowed": source.get("call_allowed") is True,
    }


def start_request(request_id: str | None = None) -> tuple[str, Token[str]]:
    candidate = str(request_id or "").strip()
    identifier = candidate if SAFE_ID.fullmatch(candidate) else uuid.uuid4().hex
    return identifier, REQUEST_ID.set(identifier)


def finish_request(token: Token[str]) -> None:
    REQUEST_ID.reset(token)


def emit_event(
    event_name: str,
    *,
    editing_session_id: str | None = None,
    job_id: str | None = None,
    stage: str | None = None,
    duration_ms: int | None = None,
    outcome: str | None = None,
    error_code: str | None = None,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event": sanitize_text(event_name, limit=80),
        "request_id": REQUEST_ID.get() or None,
        "editing_session_id": editing_session_id,
        "job_id": job_id,
        "stage": sanitize_text(stage, limit=64) if stage else None,
        "duration_ms": max(0, int(duration_ms)) if duration_ms is not None else None,
        "outcome": sanitize_text(outcome, limit=40) if outcome else None,
        "error_code": sanitize_text(error_code, limit=120) if error_code else None,
    }
    for key, value in fields.items():
        if value is None or isinstance(value, (bool, int, float)):
            payload[sanitize_text(key, limit=80)] = value
        elif isinstance(value, str):
            payload[sanitize_text(key, limit=80)] = sanitize_text(value, limit=200)
    try:
        LOGGER.info(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    except Exception:
        # Logging is diagnostic only; durable events remain in PostgreSQL.
        return
