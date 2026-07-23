from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Sequence
import json

from pydantic import ValidationError

from open_storyline.mvp.prompts import (
    CANDIDATE_COMPARISON_SYSTEM_PROMPT,
    CANDIDATE_COMPARISON_SYSTEM_PROMPT_VERSION,
)
from open_storyline.mvp.security import sanitize_text
from open_storyline.mvp.structured_outputs import (
    CANDIDATE_COMPARISON_SCHEMA,
    CandidateComparisonResponseWire,
    structured_output,
)


CANDIDATE_COMPARISON_VERSION = "candidate_comparison.v1"
MAX_COMPARISON_FINDINGS = 24


class CandidateComparisonError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _hash_json(value: Any) -> str:
    return sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _candidate_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    findings = []
    for item in (report.get("findings") or ())[:MAX_COMPARISON_FINDINGS]:
        if not isinstance(item, Mapping):
            continue
        findings.append({
            "finding_id": str(item.get("finding_id") or "")[:80],
            "category": str(item.get("category") or "")[:32],
            "severity": str(item.get("severity") or "")[:16],
            "classification": str(item.get("classification") or "")[:16],
            "confidence": max(0.0, min(1.0, float(item.get("confidence") or 0.0))),
            "clip_index": int(item.get("clip_index") or 0),
            "start_ms": max(0, int(item.get("start_ms") or 0)),
            "end_ms": max(0, int(item.get("end_ms") or 0)),
            "evidence_ids": [str(value)[:80] for value in (item.get("evidence_ids") or ())[:8]],
            "repairable": item.get("repairable") is True,
        })
    return {
        "status": str(report.get("status") or "unavailable")[:20],
        "candidate_fingerprint": str(report.get("candidate_fingerprint") or "")[:64],
        "findings": findings,
    }


def comparison_call_fingerprint(
    *,
    original_report: Mapping[str, Any],
    repaired_report: Mapping[str, Any],
    model: str = "unknown",
    reasoning_effort: str = "unknown",
) -> str:
    return _hash_json({
        "version": CANDIDATE_COMPARISON_VERSION,
        "prompt_version": CANDIDATE_COMPARISON_SYSTEM_PROMPT_VERSION,
        "prompt_sha256": sha256(
            CANDIDATE_COMPARISON_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "schema_sha256": structured_output(CANDIDATE_COMPARISON_SCHEMA).fingerprint,
        "model": str(model or "unknown")[:80],
        "reasoning_effort": str(reasoning_effort or "unknown")[:20],
        "original": _candidate_summary(original_report),
        "repaired": _candidate_summary(repaired_report),
    })


def build_candidate_comparison_prompt(
    *,
    original_report: Mapping[str, Any],
    repaired_report: Mapping[str, Any],
) -> str:
    return json.dumps({
        "task": (
            "Compare two rendered candidates using only their supplied critic "
            "evidence. Select the more effective creative candidate or declare a tie. "
            "Deterministic technical QA has already passed and cannot be overridden."
        ),
        "original": _candidate_summary(original_report),
        "repaired": _candidate_summary(repaired_report),
        "constraints": {
            "evidence_ids_only": True,
            "no_technical_override": True,
            "no_commands_paths_urls_or_private_data": True,
            "selection_is_advisory": True,
        },
        "required_output": {
            "selection": "original|repaired|tie",
            "confidence": 0.0,
            "rationale": "bounded evidence-grounded rationale",
            "evidence_ids": ["ev-..."],
            "uncertainty": "low|medium|high",
        },
    }, ensure_ascii=True, separators=(",", ":"))


def _validate_response(
    raw: Mapping[str, Any],
    *,
    original_report: Mapping[str, Any],
    repaired_report: Mapping[str, Any],
    call_fingerprint: str,
) -> dict[str, Any]:
    try:
        response = CandidateComparisonResponseWire.model_validate(raw)
    except ValidationError as exc:
        raise CandidateComparisonError(
            "CANDIDATE_COMPARISON_RESPONSE_INVALID",
            "candidate comparison response does not match the strict schema",
        ) from exc
    known_ids = {
        str(item.get("evidence_id") or "")
        for report in (original_report, repaired_report)
        for item in (report.get("findings") or ())
        if isinstance(item, Mapping)
        for _ in [0]
        for value in (item.get("evidence_ids") or ())
        for item in [{"evidence_id": value}]
    }
    if not response.evidence_ids or not set(response.evidence_ids) <= known_ids:
        raise CandidateComparisonError(
            "CANDIDATE_COMPARISON_EVIDENCE_INVALID",
            "comparison referenced evidence outside the critic reports",
        )
    return {
        "version": CANDIDATE_COMPARISON_VERSION,
        "prompt_version": CANDIDATE_COMPARISON_SYSTEM_PROMPT_VERSION,
        "response_schema": CANDIDATE_COMPARISON_SCHEMA,
        "response_schema_sha256": structured_output(
            CANDIDATE_COMPARISON_SCHEMA
        ).fingerprint,
        "selection": response.selection,
        "confidence": response.confidence,
        "rationale": sanitize_text(response.rationale, limit=600),
        "evidence_ids": list(response.evidence_ids),
        "uncertainty": response.uncertainty,
        "call_fingerprint": call_fingerprint,
        "original_candidate_fingerprint": str(
            original_report.get("candidate_fingerprint") or ""
        )[:64],
        "repaired_candidate_fingerprint": str(
            repaired_report.get("candidate_fingerprint") or ""
        )[:64],
        "provider_calls": 1,
    }


def comparison_from_checkpoint(
    payload: Mapping[str, Any],
    *,
    expected_call_fingerprint: str,
) -> dict[str, Any]:
    report = dict(payload)
    if (
        report.get("version") != CANDIDATE_COMPARISON_VERSION
        or report.get("call_fingerprint") != expected_call_fingerprint
        or report.get("selection") not in {"original", "repaired", "tie"}
        or not isinstance(report.get("evidence_ids"), list)
    ):
        raise CandidateComparisonError(
            "CANDIDATE_COMPARISON_RESPONSE_INVALID",
            "candidate comparison checkpoint does not match the candidates",
        )
    report["provider_calls"] = 0
    report["checkpoint_reused"] = True
    return report


async def compare_rendered_candidates(
    *,
    original_report: Mapping[str, Any],
    repaired_report: Mapping[str, Any],
    client: Any,
) -> dict[str, Any]:
    original_fp = str(original_report.get("candidate_fingerprint") or "")
    repaired_fp = str(repaired_report.get("candidate_fingerprint") or "")
    if not original_fp or original_fp == repaired_fp:
        return {
            "version": CANDIDATE_COMPARISON_VERSION,
            "status": "skipped",
            "selection": "original",
            "reason": "single_or_unchanged_candidate",
            "provider_calls": 0,
        }
    call_fingerprint = comparison_call_fingerprint(
        original_report=original_report,
        repaired_report=repaired_report,
        model=getattr(client, "model", "unknown"),
        reasoning_effort=getattr(client, "reasoning_effort", "unknown"),
    )
    try:
        raw = await client.complete_structured(
            schema_name=CANDIDATE_COMPARISON_SCHEMA,
            system_prompt=CANDIDATE_COMPARISON_SYSTEM_PROMPT,
            user_prompt=build_candidate_comparison_prompt(
                original_report=original_report,
                repaired_report=repaired_report,
            ),
            image_data_urls=(),
        )
        report = _validate_response(
            raw,
            original_report=original_report,
            repaired_report=repaired_report,
            call_fingerprint=call_fingerprint,
        )
        report["status"] = "completed"
        report["model"] = sanitize_text(getattr(client, "model", "unknown"), limit=80)
        return report
    except CandidateComparisonError:
        raise
    except Exception as exc:
        return {
            "version": CANDIDATE_COMPARISON_VERSION,
            "status": "unavailable",
            "selection": "tie",
            "call_fingerprint": call_fingerprint,
            "provider_calls": 1,
            "error_code": str(getattr(exc, "code", "CANDIDATE_COMPARISON_UNAVAILABLE"))[:80],
        }


__all__ = [
    "CANDIDATE_COMPARISON_VERSION",
    "CandidateComparisonError",
    "build_candidate_comparison_prompt",
    "compare_rendered_candidates",
    "comparison_call_fingerprint",
    "comparison_from_checkpoint",
]
