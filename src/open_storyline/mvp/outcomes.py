from __future__ import annotations

from collections import Counter
from datetime import datetime
from math import sqrt
from statistics import median
from typing import Any, Iterable, Sequence
import os

from open_storyline.mvp.fallbacks import FallbackEntry


OUTCOME_REPORT_VERSION = "outcome_report.v1"
OUTCOME_GRADES = frozenset({
    "enhanced",
    "with_limitations",
    "retryable_failure",
    "terminal_failure",
})
QUALITY_FEEDBACK_ERROR_CODES = frozenset({
    "EDIT_PLAN_VISUAL_COVERAGE_INSUFFICIENT",
})


def retry_ux_enabled() -> bool:
    value = os.getenv("OPENSTORYLINE_RETRY_UX_ENABLED", "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("OPENSTORYLINE_RETRY_UX_ENABLED must be true or false")
    return value == "true"


def _codes(values: Iterable[Any]) -> list[str]:
    return sorted({str(value).strip().upper()[:80] for value in values if value})


def _tokens(values: Iterable[Any]) -> list[str]:
    return sorted({str(value).strip()[:80] for value in values if value})


def _limitation(code: str) -> dict[str, Any]:
    return {
        "code": code,
        "stage": "qa",
        "severity": "limitation",
        "description": f"Strict creative QA reported {code}.",
        "evidence_code": code,
        "retryable": True,
        "recommended_retry_action": "retry_defects",
    }


def build_completed_outcome_report(
    *,
    outputs: Sequence[dict[str, Any]],
    fallback_entries: Iterable[FallbackEntry] = (),
    qa_blocker_codes: Iterable[str] = (),
    promotion_report: dict[str, Any] | None = None,
    fingerprints: dict[str, str] | None = None,
    reused_stages: Iterable[str] = (),
    recomputed_stages: Iterable[str] = (),
    prior_limitation_codes: Iterable[str] = (),
) -> dict[str, Any]:
    fallbacks = tuple(fallback_entries)
    promotion = promotion_report if isinstance(promotion_report, dict) else {}
    technical_codes = _codes(promotion.get("technical_blocker_codes") or ())
    creative_codes = _codes(
        promotion.get("creative_limitation_codes") or qa_blocker_codes
    )
    limitations = [
        {
            "code": entry.code,
            "stage": "compile",
            "severity": "limitation",
            "clip_index": entry.clip_index,
            "segment_id": entry.segment_id,
            "requested": entry.requested,
            "executed": entry.executed,
            "description": entry.reason,
            "retryable": entry.retryable,
            "recommended_retry_action": entry.retry_action,
        }
        for entry in fallbacks
    ]
    limitations.extend(_limitation(code) for code in creative_codes)
    fatal_errors = [
        {
            "code": code,
            "stage": "qa",
            "retryable": True,
            "recommended_retry_action": "retry_defects",
        }
        for code in technical_codes
    ]
    grade = (
        "retryable_failure"
        if technical_codes
        else "with_limitations"
        if limitations
        else "enhanced"
    )
    current_codes = {
        str(item["code"]) for item in [*limitations, *fatal_errors]
    }
    prior_codes = {str(value) for value in prior_limitation_codes if value}
    return {
        "version": OUTCOME_REPORT_VERSION,
        "grade": grade,
        "technical_status": "blocked" if technical_codes else "pass",
        "outputs": [
            {
                "video": str(output.get("video") or ""),
                "subtitles": output.get("subtitles"),
            }
            for output in outputs
        ],
        "limitations": limitations,
        "fatal_errors": fatal_errors,
        "promotion": {
            "decision": promotion.get("promotion_decision"),
            "effective_policy": promotion.get("effective_policy"),
            "strict_decision": (promotion.get("policy_decisions") or {}).get("strict"),
            "baseline_guaranteed_decision": (
                promotion.get("policy_decisions") or {}
            ).get("baseline_guaranteed"),
        },
        "retry": {
            "supported": bool(limitations or fatal_errors),
            "quality_feedback_supported": bool(limitations or fatal_errors),
            "recommended_action": (
                "retry_defects" if limitations or fatal_errors else "none"
            ),
            "reused_stage_names": sorted({str(value) for value in reused_stages}),
            "recomputed_stage_names": sorted({str(value) for value in recomputed_stages}),
            "prior_limitation_codes": sorted(prior_codes),
            "resolved_limitation_codes": sorted(prior_codes - current_codes),
            "remaining_limitation_codes": sorted(prior_codes & current_codes),
            "new_limitation_codes": sorted(current_codes - prior_codes),
        },
        "fingerprints": {
            str(key): str(value)
            for key, value in (fingerprints or {}).items()
            if value
        },
    }


def build_failed_outcome_report(
    *,
    code: str,
    stage: str | None,
    retryable: bool,
    blocker_codes: Iterable[str] = (),
    technical_blocker_codes: Iterable[str] = (),
    creative_limitation_codes: Iterable[str] = (),
) -> dict[str, Any]:
    normalized_code = str(code or "JOB_PROCESSING_FAILED").strip().upper()[:80]
    evidence_codes = _codes(blocker_codes)
    technical_codes = _codes(technical_blocker_codes)
    creative_codes = _codes(creative_limitation_codes)
    if evidence_codes and not technical_codes and not creative_codes:
        technical_codes = evidence_codes
    failure_codes = technical_codes or ([normalized_code] if not creative_codes else [])
    current_codes = _codes([*technical_codes, *creative_codes, *failure_codes])
    quality_feedback_supported = bool(
        technical_codes
        or creative_codes
        or normalized_code in QUALITY_FEEDBACK_ERROR_CODES
    )
    return {
        "version": OUTCOME_REPORT_VERSION,
        "grade": "retryable_failure" if retryable else "terminal_failure",
        "technical_status": "blocked" if failure_codes else "pass",
        "outputs": [],
        "limitations": [_limitation(item) for item in creative_codes],
        "fatal_errors": [
            {
                "code": item,
                "stage": str(stage or "unknown")[:64],
                "retryable": retryable,
                "recommended_retry_action": "retry_defects" if retryable else "none",
            }
            for item in failure_codes
        ],
        "promotion": {
            "decision": (
                "block_technical"
                if technical_codes
                else "block_strict"
                if creative_codes
                else None
            ),
            "effective_policy": None,
            "strict_decision": "block",
            "baseline_guaranteed_decision": "block",
        },
        "retry": {
            "supported": retryable,
            "quality_feedback_supported": quality_feedback_supported,
            "recommended_action": "retry_defects" if retryable else "none",
            "reused_stage_names": [],
            "recomputed_stage_names": [],
            "prior_limitation_codes": [],
            "resolved_limitation_codes": [],
            "remaining_limitation_codes": [],
            "new_limitation_codes": current_codes,
        },
        "fingerprints": {},
    }


def outcome_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    grade = str(value.get("grade") or "")
    if grade not in OUTCOME_GRADES:
        return None
    limitations = [
        {
            "code": str(item.get("code") or "")[:80],
            "stage": str(item.get("stage") or "")[:64],
            "requested": str(item.get("requested") or "")[:120],
            "executed": str(item.get("executed") or "")[:120],
            "description": str(item.get("description") or "")[:240],
            "retryable": bool(item.get("retryable")),
            "recommended_retry_action": str(
                item.get("recommended_retry_action") or ""
            )[:40],
        }
        for item in (value.get("limitations") or [])[:24]
        if isinstance(item, dict) and item.get("code")
    ]
    fatal_errors = [
        {
            "code": str(item.get("code") or "")[:80],
            "stage": str(item.get("stage") or "")[:64],
            "retryable": bool(item.get("retryable")),
        }
        for item in (value.get("fatal_errors") or [])[:24]
        if isinstance(item, dict) and item.get("code")
    ]
    retry = value.get("retry") if isinstance(value.get("retry"), dict) else {}
    promotion = (
        value.get("promotion") if isinstance(value.get("promotion"), dict) else {}
    )
    return {
        "version": str(value.get("version") or "")[:80],
        "grade": grade,
        "technical_status": str(value.get("technical_status") or "")[:40],
        "output_count": len(value.get("outputs") or []),
        "limitations": limitations,
        "limitation_codes": _codes(item["code"] for item in limitations),
        "fatal_errors": fatal_errors,
        "fatal_error_codes": _codes(item["code"] for item in fatal_errors),
        "promotion": {
            "decision": str(promotion.get("decision") or "")[:40],
            "effective_policy": str(promotion.get("effective_policy") or "")[:40],
        },
        "retry": {
            "supported": bool(retry.get("supported")),
            "quality_feedback_supported": bool(
                retry.get("quality_feedback_supported")
            ),
            "recommended_action": str(retry.get("recommended_action") or "")[:40],
            "reused_stage_names": _tokens(retry.get("reused_stage_names") or ()),
            "recomputed_stage_names": _tokens(
                retry.get("recomputed_stage_names") or ()
            ),
            "resolved_limitation_codes": _codes(
                retry.get("resolved_limitation_codes") or ()
            ),
            "remaining_limitation_codes": _codes(
                retry.get("remaining_limitation_codes") or ()
            ),
            "new_limitation_codes": _codes(
                retry.get("new_limitation_codes") or ()
            ),
        },
    }


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _percentile(values: Sequence[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _wilson_interval(successes: int, total: int) -> dict[str, float | None]:
    if total <= 0:
        return {"low": None, "high": None}
    z = 1.959963984540054
    rate = successes / total
    denominator = 1 + (z * z / total)
    center = (rate + (z * z / (2 * total))) / denominator
    margin = z * sqrt(
        (rate * (1 - rate) / total) + (z * z / (4 * total * total))
    ) / denominator
    return {
        "low": round(max(0.0, center - margin), 6),
        "high": round(min(1.0, center + margin), 6),
    }


def build_outcome_slo_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    grades: Counter[str] = Counter()
    limitations: Counter[str] = Counter()
    retry_attempts = 0
    retry_successes = 0
    retry_by_code: dict[str, Counter[str]] = {}
    reused_stages = 0
    recomputed_stages = 0
    durations: list[int] = []
    classified = 0
    playable = 0
    unclassified = 0
    for row in rows:
        summary = outcome_summary(row.get("outcome"))
        if summary is None:
            unclassified += 1
            continue
        classified += 1
        grade = summary["grade"]
        grades[grade] += 1
        is_playable = (
            summary["technical_status"] == "pass"
            and summary["output_count"] > 0
            and grade in {"enhanced", "with_limitations"}
        )
        playable += int(is_playable)
        limitations.update(summary["limitation_codes"])
        retry = summary["retry"]
        reused_stages += len(retry["reused_stage_names"])
        recomputed_stages += len(retry["recomputed_stage_names"])
        if row.get("retry_of_attempt_id"):
            retry_attempts += 1
            retry_successes += int(is_playable)
            for code in _codes(row.get("prior_limitation_codes") or ()):
                counts = retry_by_code.setdefault(code, Counter())
                counts["attempts"] += 1
                counts["successes"] += int(is_playable)
        started = _parse_time(row.get("started_at"))
        completed = _parse_time(row.get("completed_at"))
        if is_playable and started is not None and completed is not None and completed >= started:
            durations.append(int((completed - started).total_seconds() * 1000))
    rate = playable / classified if classified else None
    confidence = _wilson_interval(playable, classified)
    return {
        "version": "outcome_slo_summary.v1",
        "target": 0.99,
        "sample_size": classified,
        "unclassified_attempts": unclassified,
        "playable_outputs": playable,
        "playable_output_rate": round(rate, 6) if rate is not None else None,
        "confidence_95": confidence,
        "claim_ready": bool(
            classified >= 100
            and confidence["low"] is not None
            and confidence["low"] >= 0.99
        ),
        "outcomes": dict(sorted(grades.items())),
        "outcome_rates": {
            grade: round(count / classified, 6) if classified else None
            for grade, count in sorted(grades.items())
        },
        "top_limitation_codes": [
            {"code": code, "count": count}
            for code, count in limitations.most_common(10)
        ],
        "retry": {
            "attempts": retry_attempts,
            "playable_successes": retry_successes,
            "success_rate": (
                round(retry_successes / retry_attempts, 6)
                if retry_attempts
                else None
            ),
            "by_prior_limitation_code": [
                {
                    "code": code,
                    "attempts": counts["attempts"],
                    "playable_successes": counts["successes"],
                    "success_rate": round(
                        counts["successes"] / counts["attempts"],
                        6,
                    ),
                }
                for code, counts in sorted(retry_by_code.items())
            ],
        },
        "checkpoints": {
            "reused_stage_count": reused_stages,
            "recomputed_stage_count": recomputed_stages,
        },
        "time_to_playable_ms": {
            "median": int(median(durations)) if durations else None,
            "p95": _percentile(durations, 0.95),
        },
    }
