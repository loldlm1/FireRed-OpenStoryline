from __future__ import annotations

from typing import Any, Iterable, Sequence

from open_storyline.mvp.fallbacks import FallbackEntry


OUTCOME_REPORT_VERSION = "outcome_report.v1"


def build_completed_outcome_report(
    *,
    outputs: Sequence[dict[str, Any]],
    fallback_entries: Iterable[FallbackEntry] = (),
    qa_blocker_codes: Iterable[str] = (),
    fingerprints: dict[str, str] | None = None,
    reused_stages: Iterable[str] = (),
    recomputed_stages: Iterable[str] = (),
) -> dict[str, Any]:
    fallbacks = tuple(fallback_entries)
    qa_codes = tuple(sorted({str(code)[:80] for code in qa_blocker_codes if code}))
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
    limitations.extend(
        {
            "code": "CREATIVE_INTENT_UNMET",
            "stage": "qa",
            "severity": "limitation",
            "description": f"Strict creative QA reported {code}.",
            "evidence_code": code,
            "retryable": True,
            "recommended_retry_action": "retry_defects",
        }
        for code in qa_codes
    )
    grade = "with_limitations" if limitations else "enhanced"
    return {
        "version": OUTCOME_REPORT_VERSION,
        "grade": grade,
        "technical_status": "pass",
        "outputs": [
            {
                "video": str(output.get("video") or ""),
                "subtitles": output.get("subtitles"),
            }
            for output in outputs
        ],
        "limitations": limitations,
        "fatal_errors": [],
        "retry": {
            "supported": bool(limitations),
            "recommended_action": "retry_defects" if limitations else "none",
            "reused_stage_names": sorted({str(value) for value in reused_stages}),
            "recomputed_stage_names": sorted({str(value) for value in recomputed_stages}),
        },
        "fingerprints": {
            str(key): str(value)
            for key, value in (fingerprints or {}).items()
            if value
        },
    }
