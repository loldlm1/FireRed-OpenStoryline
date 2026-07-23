from __future__ import annotations

from collections import Counter
from datetime import datetime
from math import isfinite, sqrt
from statistics import median
from typing import Any, Iterable, Sequence
import os
import re

from open_storyline.mvp.defects import (
    DEFECT_REGISTRY_SHA256,
    DEFECT_REGISTRY_VERSION,
    defect_public_metadata,
)
from open_storyline.mvp.fallbacks import FallbackEntry


OUTCOME_REPORT_VERSION = "outcome_report.v2"
OUTCOME_REPORT_VERSIONS = frozenset({"outcome_report.v1", OUTCOME_REPORT_VERSION})
OUTCOME_GRADES = frozenset({
    "enhanced",
    "with_limitations",
    "retryable_failure",
    "terminal_failure",
})
QUALITY_FEEDBACK_ERROR_CODES = frozenset({
    "EDIT_PLAN_VISUAL_COVERAGE_INSUFFICIENT",
})
ROLLOUT_REVIEW_THRESHOLDS = {
    "max_repair_provider_latency_p95_ms": 180_000,
    "max_repair_cost_per_trigger_usd": 0.25,
    "max_new_defect_rate": 0.0,
    "min_playable_output_rate": 0.99,
}
_ATTRIBUTION_TOKEN = re.compile(r"^[A-Za-z0-9._:/+-]{1,160}$")
_HASH = re.compile(r"^[a-f0-9]{64}$")


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
    presentation = defect_public_metadata(code)
    return {
        "code": code,
        "stage": "qa",
        "severity": "limitation",
        "description": presentation["en"]["description"],
        "evidence_code": code,
        "retryable": presentation["retryable"],
        "recommended_retry_action": presentation["retry_action"],
        "presentation": presentation,
    }


def _metric_int(value: Any, maximum: int = 100_000_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(parsed, maximum))


def _metric_float(value: Any, maximum: float = 1_000_000.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not isfinite(parsed) or parsed < 0:
        return 0.0
    return round(min(parsed, maximum), 8)


def _repair_rollout_metrics(
    repair: dict[str, Any],
    *,
    attribution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stages = [
        item for item in (repair.get("stages") or [])[:3]
        if isinstance(item, dict)
    ]
    attempts = [
        attempt
        for stage in stages
        for attempt in (stage.get("attempts") or [])[:16]
        if isinstance(attempt, dict)
    ]
    semantic_stages = [
        stage for stage in stages
        if stage.get("attempts") and stage.get("checkpoint_reused") is not True
    ]
    rollout = attribution if isinstance(attribution, dict) else {}
    strict_boundaries = set(rollout.get("structured_output_boundaries") or ())
    strict_schema_by_stage = {
        "visual_understanding": "visual_understanding.v1",
        "plan_repair": "edit_plan_repair.v1",
    }
    strict_stages = [
        stage
        for stage in semantic_stages
        if rollout.get("structured_output_mode") == "json_schema"
        and strict_schema_by_stage.get(str(stage.get("stage") or ""))
        in strict_boundaries
    ]
    predictions = [
        item for item in (repair.get("predictive_findings") or [])[:32]
        if isinstance(item, dict)
    ]
    summary = repair.get("summary") if isinstance(repair.get("summary"), dict) else {}
    attempt_ledger = [
        item for item in (repair.get("attempt_ledger") or [])[:64]
        if isinstance(item, dict)
    ]
    fallbacks = [
        item for item in (repair.get("fallbacks") or [])[:64]
        if isinstance(item, dict)
    ]
    semantic_valid = sum(stage.get("status") == "repaired" for stage in semantic_stages)
    strict_valid = sum(
        any(str(attempt.get("reason") or "") == "ok" for attempt in stage.get("attempts") or ())
        for stage in strict_stages
    )
    by_original_code: dict[str, Counter[str]] = {}
    for stage in semantic_stages:
        resolution = (
            stage.get("resolution")
            if isinstance(stage.get("resolution"), dict)
            else {}
        )
        original_codes = _codes(resolution.get("original_codes") or ())
        if not original_codes:
            original_codes = _codes(
                item.get("code")
                for item in (stage.get("dispositions") or [])[:32]
                if isinstance(item, dict) and item.get("eligible") is True
            )
        for code in original_codes:
            counts = by_original_code.setdefault(code, Counter())
            counts["attempts"] += 1
            counts["successes"] += int(stage.get("status") == "repaired")

    predictive_objective = sum(item.get("objective") is True for item in predictions)
    predictive_advisory = sum(item.get("objective") is not True for item in predictions)
    primary_stages = [
        stage for stage in semantic_stages
        if stage.get("stage") == "plan_repair"
        and str(stage.get("repair_round") or "primary") == "primary"
    ]
    contingency_stages = [
        stage for stage in semantic_stages
        if stage.get("stage") == "plan_repair"
        and stage.get("repair_round") == "contingency"
    ]
    return {
        "triggered": bool(stages),
        "semantic_calls": len(semantic_stages),
        "transport_attempts": len(attempts),
        "strict_schema_attempts": len(strict_stages),
        "strict_schema_valid": strict_valid,
        "semantic_valid": semantic_valid,
        "successful_repairs": sum(stage.get("status") == "repaired" for stage in semantic_stages),
        "visual_calls": sum(
            stage.get("stage") == "visual_understanding" for stage in semantic_stages
        ),
        "visual_successes": sum(
            stage.get("stage") == "visual_understanding"
            and stage.get("status") == "repaired"
            for stage in semantic_stages
        ),
        "plan_calls": sum(stage.get("stage") == "plan_repair" for stage in semantic_stages),
        "plan_successes": sum(
            stage.get("stage") == "plan_repair" and stage.get("status") == "repaired"
            for stage in semantic_stages
        ),
        "primary_calls": len(primary_stages),
        "contingency_calls": len(contingency_stages),
        "defects_presented": sum(
            len((stage.get("request") or {}).get("defect_instance_ids") or ())
            or len((stage.get("request") or {}).get("objective_codes") or ())
            for stage in semantic_stages
            if isinstance(stage.get("request"), dict)
        ),
        "fallback_after_attempt_count": _metric_int(
            summary.get("fallback_after_attempt_count"),
            64,
        ),
        "provider_failures": sum(
            bool(stage.get("attempts"))
            and stage.get("status") == "failed"
            for stage in semantic_stages
        ),
        "candidate_rejections": sum(
            stage.get("candidate_disposition") == "rejected"
            for stage in semantic_stages
        ),
        "late_authoritative_findings": sum(
            len((stage.get("request") or {}).get("defect_instance_ids") or ())
            or len((stage.get("request") or {}).get("objective_codes") or ())
            for stage in stages
            if stage.get("repair_round") == "contingency"
            and isinstance(stage.get("request"), dict)
        ),
        "repair_invariant_violation_count": _metric_int(
            summary.get("repair_invariant_violation_count"),
            32,
        ),
        "jobs_at_two_call_cap": _metric_int(
            summary.get("jobs_at_two_call_cap")
            or int(any(
                item.get("round") == "contingency"
                for item in attempt_ledger
            )),
            1,
        ),
        "predictive_objective_findings": predictive_objective,
        "predictive_advisory_findings": predictive_advisory,
        "predictive_advisory_attached": int(
            predictive_objective > 0 and predictive_advisory > 0
        ),
        "fallback_count": len(fallbacks),
        "ffmpega_omission_count": sum(
            str(item.get("code") or "") == "EFFECT_OMITTED"
            or "ffmpega" in str(item.get("requested") or "").lower()
            for item in fallbacks
        ),
        "new_defect_count": len(_codes(summary.get("introduced_codes") or ())),
        "checkpoint_reuse_count": sum(
            stage.get("checkpoint_reused") is True for stage in stages
        ),
        "input_tokens": sum(_metric_int(item.get("input_tokens")) for item in attempts),
        "output_tokens": sum(_metric_int(item.get("output_tokens")) for item in attempts),
        "reasoning_tokens": sum(_metric_int(item.get("reasoning_tokens")) for item in attempts),
        "total_tokens": sum(_metric_int(item.get("total_tokens")) for item in attempts),
        "cost_usd": round(sum(_metric_float(item.get("cost_usd")) for item in attempts), 8),
        "provider_latency_ms": sum(_metric_int(item.get("duration_ms"), 3_600_000) for item in attempts),
        "by_original_code": [
            {
                "code": code,
                "attempts": counts["attempts"],
                "successes": counts["successes"],
            }
            for code, counts in sorted(by_original_code.items())
        ][:64],
    }


def _safe_attribution_token(value: Any, *, fallback: str = "unknown") -> str:
    candidate = str(value or "")[:160]
    return candidate if _ATTRIBUTION_TOKEN.fullmatch(candidate) else fallback


def _rollout_attribution(
    value: dict[str, Any] | None,
    *,
    repair: dict[str, Any],
) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    schema_hashes = sorted(
        {
            str(item)
            for item in source.get("schema_hashes") or ()
            if _HASH.fullmatch(str(item))
        }
        | {
            str((stage.get("request") or {}).get("response_schema_sha256") or "")
            for stage in (repair.get("stages") or [])[:3]
            if isinstance(stage, dict)
            and isinstance(stage.get("request"), dict)
            and _HASH.fullmatch(
                str((stage.get("request") or {}).get("response_schema_sha256") or "")
            )
        }
    )
    prompt_hashes = sorted(
        {
            str(item)
            for item in source.get("prompt_hashes") or ()
            if _HASH.fullmatch(str(item))
        }
        | {
            str((stage.get("request") or {}).get("repair_prompt_sha256") or "")
            for stage in (repair.get("stages") or [])[:3]
            if isinstance(stage, dict)
            and isinstance(stage.get("request"), dict)
            and _HASH.fullmatch(
                str((stage.get("request") or {}).get("repair_prompt_sha256") or "")
            )
        }
    )
    boundaries = source.get("structured_output_boundaries")
    return {
        "model": _safe_attribution_token(source.get("model")),
        "reasoning_effort": _safe_attribution_token(source.get("reasoning_effort")),
        "structured_output_mode": _safe_attribution_token(
            source.get("structured_output_mode")
        ),
        "structured_output_boundaries": sorted({
            token
            for item in (boundaries if isinstance(boundaries, (list, tuple, set)) else ())
            if (token := _safe_attribution_token(item, fallback=""))
        })[:16],
        "repair_mode": _safe_attribution_token(
            source.get("repair_mode") or repair.get("mode") or "off"
        ),
        "delivery_policy": _safe_attribution_token(source.get("delivery_policy")),
        "catalog_version": _safe_attribution_token(source.get("catalog_version")),
        "renderer_profile": _safe_attribution_token(source.get("renderer_profile")),
        "registry_version": _safe_attribution_token(
            repair.get("registry_version") or DEFECT_REGISTRY_VERSION
        ),
        "schema_hashes": schema_hashes,
        "prompt_hashes": prompt_hashes,
    }


def _compact_repair_metrics(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "triggered": source.get("triggered") is True,
        "semantic_calls": _metric_int(source.get("semantic_calls"), 3),
        "transport_attempts": _metric_int(source.get("transport_attempts"), 32),
        "strict_schema_attempts": _metric_int(source.get("strict_schema_attempts"), 3),
        "strict_schema_valid": _metric_int(source.get("strict_schema_valid"), 3),
        "semantic_valid": _metric_int(source.get("semantic_valid"), 3),
        "successful_repairs": _metric_int(source.get("successful_repairs"), 3),
        "visual_calls": _metric_int(source.get("visual_calls"), 1),
        "visual_successes": _metric_int(source.get("visual_successes"), 1),
        "plan_calls": _metric_int(source.get("plan_calls"), 2),
        "plan_successes": _metric_int(source.get("plan_successes"), 2),
        "primary_calls": _metric_int(source.get("primary_calls"), 1),
        "contingency_calls": _metric_int(source.get("contingency_calls"), 1),
        "defects_presented": _metric_int(source.get("defects_presented"), 96),
        "fallback_after_attempt_count": _metric_int(
            source.get("fallback_after_attempt_count"),
            64,
        ),
        "provider_failures": _metric_int(source.get("provider_failures"), 3),
        "candidate_rejections": _metric_int(source.get("candidate_rejections"), 3),
        "late_authoritative_findings": _metric_int(
            source.get("late_authoritative_findings"),
            32,
        ),
        "repair_invariant_violation_count": _metric_int(
            source.get("repair_invariant_violation_count"),
            32,
        ),
        "jobs_at_two_call_cap": _metric_int(
            source.get("jobs_at_two_call_cap"),
            1,
        ),
        "predictive_objective_findings": _metric_int(
            source.get("predictive_objective_findings"), 32
        ),
        "predictive_advisory_findings": _metric_int(
            source.get("predictive_advisory_findings"), 32
        ),
        "predictive_advisory_attached": _metric_int(
            source.get("predictive_advisory_attached"), 1
        ),
        "fallback_count": _metric_int(source.get("fallback_count"), 64),
        "ffmpega_omission_count": _metric_int(
            source.get("ffmpega_omission_count"), 64
        ),
        "new_defect_count": _metric_int(source.get("new_defect_count"), 32),
        "checkpoint_reuse_count": _metric_int(
            source.get("checkpoint_reuse_count"), 3
        ),
        "input_tokens": _metric_int(source.get("input_tokens")),
        "output_tokens": _metric_int(source.get("output_tokens")),
        "reasoning_tokens": _metric_int(source.get("reasoning_tokens")),
        "total_tokens": _metric_int(source.get("total_tokens")),
        "cost_usd": _metric_float(source.get("cost_usd")),
        "provider_latency_ms": _metric_int(
            source.get("provider_latency_ms"), 7_200_000
        ),
        "by_original_code": [
            {
                "code": defect_public_metadata(item.get("code"))["raw_code"],
                "attempts": _metric_int(item.get("attempts"), 3),
                "successes": _metric_int(item.get("successes"), 3),
            }
            for item in (source.get("by_original_code") or [])[:64]
            if isinstance(item, dict) and item.get("code")
        ],
    }


def _semantic_qa_summary(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    status = str(source.get("status") or "unavailable")[:40]
    if status not in {"disabled", "pass", "review", "unavailable"}:
        status = "unavailable"
    attempts = [
        item for item in (source.get("attempts") or [])[:6]
        if isinstance(item, dict)
    ]
    observations = [
        item for item in (source.get("observations") or [])[:8]
        if isinstance(item, dict)
    ]
    compact_metrics = (
        source.get("metrics") if isinstance(source.get("metrics"), dict) else None
    )
    metrics = (
        {
            "attempts": _metric_int(compact_metrics.get("attempts"), 6),
            "provider_latency_ms": _metric_int(
                compact_metrics.get("provider_latency_ms"),
                3_600_000,
            ),
            "input_tokens": _metric_int(compact_metrics.get("input_tokens")),
            "output_tokens": _metric_int(compact_metrics.get("output_tokens")),
            "reasoning_tokens": _metric_int(
                compact_metrics.get("reasoning_tokens")
            ),
            "total_tokens": _metric_int(compact_metrics.get("total_tokens")),
            "cost_usd": _metric_float(compact_metrics.get("cost_usd")),
        }
        if compact_metrics is not None
        else {
            "attempts": len(attempts),
            "provider_latency_ms": sum(
                _metric_int(item.get("duration_ms"), 3_600_000)
                for item in attempts
            ),
            "input_tokens": sum(
                _metric_int(item.get("input_tokens")) for item in attempts
            ),
            "output_tokens": sum(
                _metric_int(item.get("output_tokens")) for item in attempts
            ),
            "reasoning_tokens": sum(
                _metric_int(item.get("reasoning_tokens")) for item in attempts
            ),
            "total_tokens": sum(
                _metric_int(item.get("total_tokens")) for item in attempts
            ),
            "cost_usd": round(
                sum(_metric_float(item.get("cost_usd")) for item in attempts),
                8,
            ),
        }
    )
    return {
        "status": status,
        "schema_valid": status in {"pass", "review"},
        "provider_calls": _metric_int(source.get("provider_calls"), 1),
        "frame_count": _metric_int(source.get("frame_count"), 8),
        "observation_count": (
            _metric_int(source.get("observation_count"), 8)
            if source.get("observation_count") is not None
            else len(observations)
        ),
        "error_code": str(source.get("error_code") or "")[:80],
        "metrics": metrics,
    }


def _render_critic_summary(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    status = str(source.get("status") or "disabled")[:20]
    if status not in {"disabled", "pass", "review", "unavailable", "skipped"}:
        status = "unavailable"
    findings = []
    for item in (source.get("findings") or [])[:64]:
        if not isinstance(item, dict):
            continue
        finding_id = str(item.get("finding_id") or "")[:80]
        fingerprint = str(item.get("finding_fingerprint") or "")
        if not finding_id or not _HASH.fullmatch(fingerprint):
            continue
        findings.append({
            "finding_id": finding_id,
            "finding_fingerprint": fingerprint,
            "defect_code": str(item.get("defect_code") or "RENDER_CRITIC_FINDING")[:80],
            "category": str(item.get("category") or "")[:40],
            "severity": str(item.get("severity") or "")[:40],
            "classification": str(item.get("classification") or "")[:40],
            "clip_index": _metric_int(item.get("clip_index"), 50),
            "start_ms": _metric_int(item.get("start_ms"), 86_400_000),
            "end_ms": _metric_int(item.get("end_ms"), 86_400_000),
            "repairable": item.get("repairable") is True,
            "lifecycle": str(item.get("lifecycle") or "observed")[:40],
        })
    call_fingerprint = str(source.get("call_fingerprint") or "")
    candidate_fingerprint = str(source.get("candidate_fingerprint") or "")
    return {
        "version": str(source.get("version") or "")[:80],
        "mode": str(source.get("mode") or "off")[:20],
        "status": status,
        "non_mutating": source.get("non_mutating") is True,
        "call_fingerprint": call_fingerprint if _HASH.fullmatch(call_fingerprint) else "",
        "candidate_fingerprint": candidate_fingerprint if _HASH.fullmatch(candidate_fingerprint) else "",
        "provider_calls": _metric_int(source.get("provider_calls"), 1),
        "finding_count": len(findings),
        "findings": findings,
    }


def _post_render_repair_summary(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    status = str(source.get("status") or "disabled")[:32]
    if status not in {
        "disabled",
        "not_needed",
        "attempted",
        "accepted",
        "rejected",
        "unavailable",
        "no_change",
        "deferred_effect_review",
    }:
        status = "unavailable"
    improvement = source.get("improvement")
    compact_improvement = {}
    if isinstance(improvement, dict):
        compact_improvement = {
            "demonstrated": improvement.get("demonstrated") is True,
            "original_finding_count": _metric_int(
                improvement.get("original_finding_count"),
            ),
            "repaired_finding_count": _metric_int(
                improvement.get("repaired_finding_count"),
            ),
            "new_blocker_finding_ids": [
                str(item)[:80]
                for item in improvement.get("new_blocker_finding_ids") or ()
            ][:16],
        }
    return {
        "version": str(source.get("version") or "")[:80],
        "mode": str(source.get("mode") or "off")[:20],
        "status": status,
        "selected_candidate": str(source.get("selected_candidate") or "original")[:20],
        "provider_calls": _metric_int(source.get("provider_calls"), 2),
        "rounds": _metric_int(source.get("rounds"), 2),
        "checkpoint_reused": source.get("checkpoint_reused") is True,
        "affected_clip_indexes": [
            _metric_int(item, 50)
            for item in source.get("affected_clip_indexes") or ()
        ][:8],
        "error_code": str(source.get("error_code") or "")[:80],
        "improvement": compact_improvement,
    }


def repair_defect_lifecycle(repair: dict[str, Any]) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}

    def record(code: Any) -> dict[str, Any]:
        presentation = defect_public_metadata(code)
        canonical = presentation["canonical_code"]
        return records.setdefault(canonical, {
            "code": canonical,
            "strategy": presentation["repair_strategy"],
            "eligible": False,
            "repair_attempted": False,
            "dispositions": set(),
            "stage_statuses": [],
            "fallbacks": [],
            "presentation": presentation,
        })

    summary = repair.get("summary") if isinstance(repair.get("summary"), dict) else {}
    for key, disposition in (
        ("resolved_codes", "resolved"),
        ("remaining_codes", "remaining"),
        ("introduced_codes", "new"),
        ("fallback_applied_codes", "fallback_applied"),
        ("not_repairable_codes", "not_repairable"),
    ):
        for code in summary.get(key) or ():
            record(code)["dispositions"].add(disposition)

    for stage in (repair.get("stages") or [])[:3]:
        if not isinstance(stage, dict):
            continue
        stage_name = str(stage.get("stage") or "")[:40]
        status = str(stage.get("status") or "")[:40]
        attempts = [
            item for item in (stage.get("attempts") or [])[:16]
            if isinstance(item, dict)
        ]
        checkpoint_reused = stage.get("checkpoint_reused") is True
        for disposition in (stage.get("dispositions") or [])[:32]:
            if not isinstance(disposition, dict):
                continue
            item = record(disposition.get("code"))
            eligible = disposition.get("eligible") is True
            item["eligible"] = item["eligible"] or eligible
            item["repair_attempted"] = item["repair_attempted"] or bool(
                eligible
                and (
                    attempts
                    or status in {"repaired", "rejected", "failed"}
                    or checkpoint_reused
                )
            )
            if not eligible:
                item["dispositions"].add("not_repairable")
            stage_status = {
                "stage": stage_name,
                "status": status,
                "checkpoint_reused": checkpoint_reused,
                "repair_round": str(stage.get("repair_round") or "")[:20],
                "provider_outcome": str(
                    stage.get("provider_outcome") or ""
                )[:120],
            }
            if stage_status not in item["stage_statuses"]:
                item["stage_statuses"].append(stage_status)

    for fallback in (repair.get("fallbacks") or [])[:64]:
        if not isinstance(fallback, dict):
            continue
        item = record(fallback.get("code"))
        item["dispositions"].add("fallback_applied")
        fallback_state = {
            "requested": str(fallback.get("requested") or "")[:120],
            "executed": str(fallback.get("executed") or "")[:120],
        }
        if fallback_state not in item["fallbacks"]:
            item["fallbacks"].append(fallback_state)

    return [
        {
            **item,
            "dispositions": sorted(item["dispositions"]),
            "stage_statuses": item["stage_statuses"][:4],
            "fallbacks": item["fallbacks"][:8],
        }
        for _code, item in sorted(records.items())
    ][:64]


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
    repair_report: dict[str, Any] | None = None,
    rollout_attribution: dict[str, Any] | None = None,
    semantic_review: dict[str, Any] | None = None,
    render_critic: dict[str, Any] | None = None,
    post_render_repair: dict[str, Any] | None = None,
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
            "presentation": defect_public_metadata(entry.code),
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
            "presentation": defect_public_metadata(code),
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
    repair = repair_report if isinstance(repair_report, dict) else {}
    repair_summary = (
        repair.get("summary") if isinstance(repair.get("summary"), dict) else {}
    )
    repair_defects = repair_defect_lifecycle(repair)
    attribution = _rollout_attribution(rollout_attribution, repair=repair)
    repair_metrics = _repair_rollout_metrics(repair, attribution=attribution)
    delivery_policy = str(
        promotion.get("delivery_policy")
        or (
            "technical_pass_guaranteed"
            if promotion.get("effective_policy") == "baseline_guaranteed"
            else "qa_enforced"
        )
    )
    delivery_decision = str(
        promotion.get("delivery_decision")
        or (
            "publish_with_limitations"
            if creative_codes and not technical_codes
            else "publish_enhanced"
            if not technical_codes
            else "withhold_technical"
        )
    )
    return {
        "version": OUTCOME_REPORT_VERSION,
        "registry_version": DEFECT_REGISTRY_VERSION,
        "registry_sha256": DEFECT_REGISTRY_SHA256,
        "attribution": attribution,
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
            "strict_decision": (
                promotion.get("strict_decision")
                or (promotion.get("policy_decisions") or {}).get("strict")
            ),
            "baseline_guaranteed_decision": (
                promotion.get("policy_decisions") or {}
            ).get("baseline_guaranteed"),
        },
        "strict_qa": {
            "decision": (
                promotion.get("strict_decision")
                or (promotion.get("policy_decisions") or {}).get("strict")
            ),
            "blocker_codes": _codes(promotion.get("blocker_codes") or ()),
        },
        "semantic_qa": _semantic_qa_summary(semantic_review),
        "creative_review": _render_critic_summary(render_critic),
        "post_render_repair": _post_render_repair_summary(post_render_repair),
        "delivery": {
            "policy": delivery_policy,
            "decision": delivery_decision,
            "download_available": bool(outputs)
            and promotion.get("download_available") is not False,
        },
        "repair": {
            "report_version": str(repair.get("version") or "")[:80],
            "registry_version": str(
                repair.get("registry_version") or DEFECT_REGISTRY_VERSION
            )[:80],
            "mode": str(repair.get("mode") or "off")[:20],
            "stages": [
                {
                    "stage": str(item.get("stage") or "")[:40],
                    "status": str(item.get("status") or "")[:40],
                    "checkpoint_reused": item.get("checkpoint_reused") is True,
                    "repair_round": str(item.get("repair_round") or "")[:20],
                    "provider_outcome": str(
                        item.get("provider_outcome") or ""
                    )[:120],
                    "schema_valid": item.get("schema_valid") is True,
                    "semantic_valid": item.get("semantic_valid") is True,
                    "candidate_disposition": str(
                        item.get("candidate_disposition") or ""
                    )[:40],
                    "fallback_authorized": (
                        item.get("fallback_authorized") is True
                    ),
                }
                for item in (repair.get("stages") or [])[:3]
                if isinstance(item, dict)
            ],
            "resolved_codes": _codes(repair_summary.get("resolved_codes") or ()),
            "remaining_codes": _codes(repair_summary.get("remaining_codes") or ()),
            "introduced_codes": _codes(repair_summary.get("introduced_codes") or ()),
            "fallback_applied_codes": _codes(
                repair_summary.get("fallback_applied_codes") or ()
            ),
            "not_repairable_codes": _codes(
                repair_summary.get("not_repairable_codes") or ()
            ),
            "defects": repair_defects,
            "metrics": repair_metrics,
        },
        "retry": {
            "supported": True,
            "quality_feedback_supported": bool(limitations or fatal_errors),
            "recommended_action": (
                "retry_defects" if limitations or fatal_errors else "rerun"
            ),
            "unavailable_reason": "",
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
    repair_report: dict[str, Any] | None = None,
    rollout_attribution: dict[str, Any] | None = None,
    checkpoint_summary: dict[str, Any] | None = None,
    fallback_ledger: dict[str, Any] | None = None,
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
    repair = dict(repair_report) if isinstance(repair_report, dict) else {}
    repair_fallbacks = [
        dict(item)
        for item in (repair.get("fallbacks") or [])[:64]
        if isinstance(item, dict)
    ]
    ledger_entries = [
        dict(item)
        for item in (
            (fallback_ledger or {}).get("entries")
            if isinstance(fallback_ledger, dict)
            else ()
        ) or ()
        if isinstance(item, dict)
    ][:64]
    if ledger_entries:
        known_fallbacks = {
            (
                str(item.get("code") or ""),
                _metric_int(item.get("clip_index"), 50),
                str(item.get("segment_id") or ""),
            )
            for item in repair_fallbacks
        }
        repair_fallbacks.extend(
            item
            for item in ledger_entries
            if (
                str(item.get("code") or ""),
                _metric_int(item.get("clip_index"), 50),
                str(item.get("segment_id") or ""),
            ) not in known_fallbacks
        )
        repair["fallbacks"] = repair_fallbacks[:64]
    repair_summary = (
        repair.get("summary") if isinstance(repair.get("summary"), dict) else {}
    )
    repair_defects = {
        str(item.get("code") or ""): item
        for item in repair_defect_lifecycle(repair)
        if isinstance(item, dict) and item.get("code")
    }
    failure_stage = str(stage or "unknown")[:40]
    for item in current_codes:
        existing = repair_defects.get(item)
        if existing is not None:
            existing["dispositions"] = sorted({
                *existing.get("dispositions", ()),
                "remaining",
            })
            stage_state = {
                "stage": failure_stage,
                "status": "failed",
                "checkpoint_reused": False,
            }
            if stage_state not in existing.get("stage_statuses", ()):
                existing.setdefault("stage_statuses", []).append(stage_state)
            continue
        presentation = defect_public_metadata(item)
        strategy = presentation["repair_strategy"]
        repair_defects[item] = {
            "code": item,
            "strategy": strategy,
            "eligible": strategy != "terminal",
            "repair_attempted": False,
            "dispositions": (
                ["remaining", "not_repairable"]
                if strategy == "terminal"
                else ["remaining"]
            ),
            "stage_statuses": [{
                "stage": failure_stage,
                "status": "failed",
                "checkpoint_reused": False,
            }],
            "fallbacks": [],
            "presentation": presentation,
        }
    checkpoints = (
        checkpoint_summary
        if isinstance(checkpoint_summary, dict)
        else repair.get("checkpoints")
        if isinstance(repair.get("checkpoints"), dict)
        else {}
    )
    attribution_source = (
        rollout_attribution
        if isinstance(rollout_attribution, dict)
        else repair.get("attribution")
        if isinstance(repair.get("attribution"), dict)
        else None
    )
    fallback_codes = _codes([
        *(repair_summary.get("fallback_applied_codes") or ()),
        *(item.get("code") for item in repair_fallbacks),
    ])
    not_repairable_codes = set(
        _codes(repair_summary.get("not_repairable_codes") or ())
    )
    not_repairable_codes.update(
        item
        for item in current_codes
        if defect_public_metadata(item)["repair_strategy"] == "terminal"
    )
    repair_metrics = _repair_rollout_metrics(
        repair,
        attribution=_rollout_attribution(attribution_source, repair=repair),
    )
    return {
        "version": OUTCOME_REPORT_VERSION,
        "registry_version": DEFECT_REGISTRY_VERSION,
        "registry_sha256": DEFECT_REGISTRY_SHA256,
        "attribution": _rollout_attribution(attribution_source, repair=repair),
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
                "presentation": defect_public_metadata(item),
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
        "strict_qa": {
            "decision": "block",
            "blocker_codes": current_codes,
        },
        "delivery": {
            "policy": "qa_enforced",
            "decision": "withhold_technical" if failure_codes else "withhold_strict",
            "download_available": False,
        },
        "repair": {
            "report_version": str(repair.get("version") or "")[:80],
            "registry_version": str(
                repair.get("registry_version") or DEFECT_REGISTRY_VERSION
            )[:80],
            "mode": str(repair.get("mode") or "off")[:20],
            "stages": [
                {
                    "stage": str(item.get("stage") or "")[:40],
                    "status": str(item.get("status") or "")[:40],
                    "checkpoint_reused": item.get("checkpoint_reused") is True,
                    "repair_round": str(item.get("repair_round") or "")[:20],
                    "provider_outcome": str(
                        item.get("provider_outcome") or ""
                    )[:120],
                    "schema_valid": item.get("schema_valid") is True,
                    "semantic_valid": item.get("semantic_valid") is True,
                    "candidate_disposition": str(
                        item.get("candidate_disposition") or ""
                    )[:40],
                    "fallback_authorized": (
                        item.get("fallback_authorized") is True
                    ),
                    "transport_attempts": len(item.get("attempts") or ()),
                }
                for item in (repair.get("stages") or [])[:3]
                if isinstance(item, dict)
            ],
            "resolved_codes": _codes(repair_summary.get("resolved_codes") or ()),
            "remaining_codes": _codes([
                *(repair_summary.get("remaining_codes") or ()),
                *current_codes,
            ]),
            "introduced_codes": _codes(
                repair_summary.get("introduced_codes") or ()
            ),
            "fallback_applied_codes": fallback_codes,
            "not_repairable_codes": sorted(not_repairable_codes)[:64],
            "defects": [
                repair_defects[item]
                for item in sorted(repair_defects)[:64]
            ],
            "metrics": repair_metrics,
        },
        "retry": {
            "supported": True,
            "quality_feedback_supported": quality_feedback_supported,
            "recommended_action": (
                "retry_defects" if quality_feedback_supported else "rerun"
            ),
            "unavailable_reason": "",
            "reused_stage_names": _tokens(
                checkpoints.get("reused_stages") or ()
            ),
            "recomputed_stage_names": _tokens(
                checkpoints.get("recomputed_stages") or ()
            ),
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
            "presentation": defect_public_metadata(item.get("code")),
        }
        for item in (value.get("limitations") or [])[:24]
        if isinstance(item, dict) and item.get("code")
    ]
    fatal_errors = [
        {
            "code": str(item.get("code") or "")[:80],
            "stage": str(item.get("stage") or "")[:64],
            "retryable": bool(item.get("retryable")),
            "presentation": defect_public_metadata(item.get("code")),
        }
        for item in (value.get("fatal_errors") or [])[:24]
        if isinstance(item, dict) and item.get("code")
    ]
    retry = value.get("retry") if isinstance(value.get("retry"), dict) else {}
    promotion = (
        value.get("promotion") if isinstance(value.get("promotion"), dict) else {}
    )
    repair = value.get("repair") if isinstance(value.get("repair"), dict) else {}
    delivery = value.get("delivery") if isinstance(value.get("delivery"), dict) else {}
    strict_qa = value.get("strict_qa") if isinstance(value.get("strict_qa"), dict) else {}
    semantic_qa = (
        value.get("semantic_qa")
        if isinstance(value.get("semantic_qa"), dict)
        else {}
    )
    attribution = (
        value.get("attribution") if isinstance(value.get("attribution"), dict) else {}
    )
    return {
        "version": str(value.get("version") or "")[:80],
        "registry_version": str(value.get("registry_version") or "")[:80],
        "attribution": _rollout_attribution(attribution, repair=repair),
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
        "strict_qa": {
            "decision": str(
                strict_qa.get("decision")
                or promotion.get("strict_decision")
                or ""
            )[:40],
            "blocker_codes": _codes(strict_qa.get("blocker_codes") or ()),
        },
        "semantic_qa": _semantic_qa_summary(semantic_qa),
        "delivery": {
            "policy": str(delivery.get("policy") or "")[:40],
            "decision": str(delivery.get("decision") or "")[:40],
            "download_available": delivery.get("download_available") is True,
        },
        "repair": {
            "report_version": str(repair.get("report_version") or "")[:80],
            "registry_version": str(repair.get("registry_version") or "")[:80],
            "mode": str(repair.get("mode") or "")[:20],
            "stages": [
                {
                    "stage": str(item.get("stage") or "")[:40],
                    "status": str(item.get("status") or "")[:40],
                    "checkpoint_reused": item.get("checkpoint_reused") is True,
                    "repair_round": str(item.get("repair_round") or "")[:20],
                    "provider_outcome": str(
                        item.get("provider_outcome") or ""
                    )[:120],
                    "schema_valid": item.get("schema_valid") is True,
                    "semantic_valid": item.get("semantic_valid") is True,
                    "candidate_disposition": str(
                        item.get("candidate_disposition") or ""
                    )[:40],
                    "fallback_authorized": (
                        item.get("fallback_authorized") is True
                    ),
                }
                for item in (repair.get("stages") or [])[:3]
                if isinstance(item, dict)
            ],
            "resolved_codes": _codes(repair.get("resolved_codes") or ()),
            "remaining_codes": _codes(repair.get("remaining_codes") or ()),
            "introduced_codes": _codes(repair.get("introduced_codes") or ()),
            "fallback_applied_codes": _codes(
                repair.get("fallback_applied_codes") or ()
            ),
            "not_repairable_codes": _codes(
                repair.get("not_repairable_codes") or ()
            ),
            "defects": [
                {
                    "code": defect_public_metadata(item.get("code"))["raw_code"],
                    "strategy": defect_public_metadata(item.get("code"))[
                        "repair_strategy"
                    ],
                    "eligible": item.get("eligible") is True,
                    "repair_attempted": item.get("repair_attempted") is True,
                    "dispositions": _tokens(item.get("dispositions") or ()),
                    "stage_statuses": [
                        {
                            "stage": str(stage.get("stage") or "")[:40],
                            "status": str(stage.get("status") or "")[:40],
                            "checkpoint_reused": (
                                stage.get("checkpoint_reused") is True
                            ),
                            "repair_round": str(
                                stage.get("repair_round") or ""
                            )[:20],
                            "provider_outcome": str(
                                stage.get("provider_outcome") or ""
                            )[:120],
                        }
                        for stage in (item.get("stage_statuses") or [])[:4]
                        if isinstance(stage, dict)
                    ],
                    "fallbacks": [
                        {
                            "requested": str(fallback.get("requested") or "")[:120],
                            "executed": str(fallback.get("executed") or "")[:120],
                        }
                        for fallback in (item.get("fallbacks") or [])[:8]
                        if isinstance(fallback, dict)
                    ],
                    "presentation": defect_public_metadata(item.get("code")),
                }
                for item in (repair.get("defects") or [])[:64]
                if isinstance(item, dict) and item.get("code")
            ],
            "metrics": _compact_repair_metrics(repair.get("metrics")),
        },
        "retry": {
            "supported": bool(retry.get("supported")),
            "quality_feedback_supported": bool(
                retry.get("quality_feedback_supported")
            ),
            "recommended_action": str(retry.get("recommended_action") or "")[:40],
            "unavailable_reason": str(
                retry.get("unavailable_reason") or ""
            )[:80],
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
    repair_totals: Counter[str] = Counter()
    repair_triggered = 0
    repair_fallback_jobs = 0
    repair_provider_latency_ms: list[int] = []
    repair_by_code: dict[str, Counter[str]] = {}
    predictive_attachment_opportunities = 0
    technical_pass_candidates = 0
    technical_pass_published = 0
    attribution_counts: Counter[tuple[Any, ...]] = Counter()
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
        repair_metrics = summary["repair"]["metrics"]
        for name in (
            "semantic_calls",
            "transport_attempts",
            "strict_schema_attempts",
            "strict_schema_valid",
            "semantic_valid",
            "successful_repairs",
            "visual_calls",
            "visual_successes",
            "plan_calls",
            "plan_successes",
            "primary_calls",
            "contingency_calls",
            "defects_presented",
            "fallback_after_attempt_count",
            "provider_failures",
            "candidate_rejections",
            "late_authoritative_findings",
            "repair_invariant_violation_count",
            "jobs_at_two_call_cap",
            "predictive_objective_findings",
            "predictive_advisory_findings",
            "predictive_advisory_attached",
            "fallback_count",
            "ffmpega_omission_count",
            "new_defect_count",
            "checkpoint_reuse_count",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            repair_totals[name] += int(repair_metrics[name])
        repair_totals["cost_micro_usd"] += int(
            round(float(repair_metrics["cost_usd"]) * 1_000_000)
        )
        if repair_metrics["triggered"]:
            repair_triggered += 1
        if repair_metrics["semantic_calls"]:
            repair_provider_latency_ms.append(
                int(repair_metrics["provider_latency_ms"])
            )
        repair_fallback_jobs += int(repair_metrics["fallback_count"] > 0)
        predictive_attachment_opportunities += int(
            repair_metrics["predictive_objective_findings"] > 0
        )
        for item in repair_metrics["by_original_code"]:
            counts = repair_by_code.setdefault(item["code"], Counter())
            counts["attempts"] += item["attempts"]
            counts["successes"] += item["successes"]
        delivery = summary["delivery"]
        if delivery["policy"] == "technical_pass_guaranteed":
            technical_pass_candidates += 1
            technical_pass_published += int(delivery["download_available"])
        attribution = summary["attribution"]
        attribution_counts[(
            attribution["model"],
            attribution["reasoning_effort"],
            attribution["structured_output_mode"],
            tuple(attribution["structured_output_boundaries"]),
            attribution["repair_mode"],
            attribution["delivery_policy"],
            attribution["catalog_version"],
            attribution["renderer_profile"],
            attribution["registry_version"],
            tuple(attribution["schema_hashes"]),
            tuple(attribution["prompt_hashes"]),
        )] += 1
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
    repair_cost_usd = repair_totals["cost_micro_usd"] / 1_000_000
    repair_cost_per_trigger = (
        repair_cost_usd / repair_triggered if repair_triggered else None
    )
    repair_latency_p95 = _percentile(repair_provider_latency_ms, 0.95)
    new_defect_rate = (
        repair_totals["new_defect_count"] / repair_totals["semantic_calls"]
        if repair_totals["semantic_calls"]
        else None
    )
    review_signals = {
        "repair_provider_latency_p95_ms": repair_latency_p95,
        "repair_cost_per_trigger_usd": (
            round(repair_cost_per_trigger, 8)
            if repair_cost_per_trigger is not None
            else None
        ),
        "new_defect_rate": (
            round(new_defect_rate, 6) if new_defect_rate is not None else None
        ),
        "playable_output_rate": round(rate, 6) if rate is not None else None,
    }
    review_checks = {
        "repair_latency_within_threshold": (
            repair_latency_p95 is not None
            and repair_latency_p95
            <= ROLLOUT_REVIEW_THRESHOLDS["max_repair_provider_latency_p95_ms"]
        ),
        "repair_cost_within_threshold": (
            repair_cost_per_trigger is not None
            and repair_cost_per_trigger
            <= ROLLOUT_REVIEW_THRESHOLDS["max_repair_cost_per_trigger_usd"]
        ),
        "no_new_defect_regression": (
            new_defect_rate is not None
            and new_defect_rate
            <= ROLLOUT_REVIEW_THRESHOLDS["max_new_defect_rate"]
        ),
        "playable_output_rate_within_threshold": (
            rate is not None
            and rate >= ROLLOUT_REVIEW_THRESHOLDS["min_playable_output_rate"]
        ),
    }

    def metric_rate(successes: int, total: int) -> float | None:
        return round(successes / total, 6) if total else None

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
        "claim_gate": {
            "evidence_only": True,
            "enables_rollout": False,
            "minimum_sample_size": 100,
        },
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
        "repair": {
            "triggered_attempts": repair_triggered,
            "trigger_rate": metric_rate(repair_triggered, classified),
            "provider_calls": repair_totals["semantic_calls"],
            "transport_attempts": repair_totals["transport_attempts"],
            "strict_schema": {
                "attempts": repair_totals["strict_schema_attempts"],
                "valid": repair_totals["strict_schema_valid"],
                "validity_rate": metric_rate(
                    repair_totals["strict_schema_valid"],
                    repair_totals["strict_schema_attempts"],
                ),
            },
            "semantic_validity": {
                "valid": repair_totals["semantic_valid"],
                "rate": metric_rate(
                    repair_totals["semantic_valid"],
                    repair_totals["semantic_calls"],
                ),
            },
            "success": {
                "total": repair_totals["successful_repairs"],
                "rate": metric_rate(
                    repair_totals["successful_repairs"],
                    repair_totals["semantic_calls"],
                ),
                "visual": {
                    "attempts": repair_totals["visual_calls"],
                    "successes": repair_totals["visual_successes"],
                    "rate": metric_rate(
                        repair_totals["visual_successes"],
                        repair_totals["visual_calls"],
                    ),
                },
                "plan": {
                    "attempts": repair_totals["plan_calls"],
                    "successes": repair_totals["plan_successes"],
                    "rate": metric_rate(
                        repair_totals["plan_successes"],
                        repair_totals["plan_calls"],
                    ),
                },
                "by_original_code": [
                    {
                        "code": code,
                        "attempts": counts["attempts"],
                        "successes": counts["successes"],
                        "rate": metric_rate(counts["successes"], counts["attempts"]),
                    }
                    for code, counts in sorted(repair_by_code.items())
                ],
            },
            "rounds": {
                "primary_calls": repair_totals["primary_calls"],
                "contingency_calls": repair_totals["contingency_calls"],
                "contingency_rate": metric_rate(
                    repair_totals["contingency_calls"],
                    repair_totals["primary_calls"],
                ),
                "jobs_at_two_call_cap": repair_totals["jobs_at_two_call_cap"],
            },
            "predictive": {
                "objective_findings": repair_totals[
                    "predictive_objective_findings"
                ],
                "advisory_findings": repair_totals[
                    "predictive_advisory_findings"
                ],
                "advisory_attachment_rate": metric_rate(
                    repair_totals["predictive_advisory_attached"],
                    predictive_attachment_opportunities,
                ),
            },
            "fallbacks": {
                "count": repair_totals["fallback_count"],
                "after_attempt_count": repair_totals[
                    "fallback_after_attempt_count"
                ],
                "job_rate": metric_rate(repair_fallback_jobs, classified),
                "ffmpega_omission_count": repair_totals["ffmpega_omission_count"],
                "ffmpega_omission_rate": metric_rate(
                    repair_totals["ffmpega_omission_count"],
                    classified,
                ),
            },
            "new_defect_count": repair_totals["new_defect_count"],
            "defects_presented": repair_totals["defects_presented"],
            "provider_failures": repair_totals["provider_failures"],
            "candidate_rejections": repair_totals["candidate_rejections"],
            "late_authoritative_findings": repair_totals[
                "late_authoritative_findings"
            ],
            "repair_invariant_violation_count": repair_totals[
                "repair_invariant_violation_count"
            ],
            "new_defect_rate": review_signals["new_defect_rate"],
            "checkpoint_reuse_count": repair_totals["checkpoint_reuse_count"],
            "checkpoint_reuse_rate": metric_rate(
                repair_totals["checkpoint_reuse_count"],
                repair_totals["semantic_calls"]
                + repair_totals["checkpoint_reuse_count"],
            ),
            "tokens": {
                "input": repair_totals["input_tokens"],
                "output": repair_totals["output_tokens"],
                "reasoning": repair_totals["reasoning_tokens"],
                "total": repair_totals["total_tokens"],
            },
            "cost_usd": round(repair_cost_usd, 8),
            "cost_per_trigger_usd": review_signals[
                "repair_cost_per_trigger_usd"
            ],
            "provider_latency_ms": {
                "p95": repair_latency_p95,
                "total": sum(repair_provider_latency_ms),
            },
        },
        "delivery": {
            "technical_pass_candidates": technical_pass_candidates,
            "technical_pass_published": technical_pass_published,
            "technical_pass_publication_rate": metric_rate(
                technical_pass_published,
                technical_pass_candidates,
            ),
        },
        "attribution": [
            {
                "model": key[0],
                "reasoning_effort": key[1],
                "structured_output_mode": key[2],
                "structured_output_boundaries": list(key[3]),
                "repair_mode": key[4],
                "delivery_policy": key[5],
                "catalog_version": key[6],
                "renderer_profile": key[7],
                "registry_version": key[8],
                "schema_hashes": list(key[9]),
                "prompt_hashes": list(key[10]),
                "sample_size": count,
            }
            for key, count in sorted(
                attribution_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:20]
        ],
        "rollout_review": {
            "thresholds": dict(ROLLOUT_REVIEW_THRESHOLDS),
            "signals": review_signals,
            "checks": review_checks,
            "operator_approval_required": True,
            "automatic_enablement": False,
        },
        "time_to_playable_ms": {
            "median": int(median(durations)) if durations else None,
            "p95": _percentile(durations, 0.95),
        },
    }
