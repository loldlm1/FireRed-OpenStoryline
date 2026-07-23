from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Sequence
import json
import os

from pydantic import ValidationError

from open_storyline.mvp.prompts import (
    RENDER_CRITIC_SYSTEM_PROMPT,
    RENDER_CRITIC_SYSTEM_PROMPT_VERSION,
)
from open_storyline.mvp.render_evidence import RenderEvidenceManifest
from open_storyline.mvp.security import sanitize_text
from open_storyline.mvp.structured_outputs import (
    RENDER_CRITIC_SCHEMA,
    RenderCriticResponseWire,
    structured_output,
)


RENDER_CRITIC_VERSION = "render_critic.v1"
RENDER_CRITIC_PROMPT_VERSION = RENDER_CRITIC_SYSTEM_PROMPT_VERSION
POST_RENDER_REVIEW_MODES = frozenset({"off", "shadow", "report", "enforce"})
_SAFE_CAPABILITIES = frozenset({
    "crop", "fit", "letterbox", "subtitles", "hard_cut", "fade", "xfade",
    "image_overlay", "pip", "zoom", "effect",
})
_MAX_EDITING_PROMPT_CHARS = 12_000


class RenderCriticError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def render_review_mode(config: Any) -> str:
    value = os.getenv(
        "OPENSTORYLINE_POST_RENDER_REVIEW_MODE",
        str(getattr(config, "post_render_review_mode", "off")),
    ).strip().lower()
    if value not in POST_RENDER_REVIEW_MODES:
        raise RenderCriticError(
            "POST_RENDER_REVIEW_MODE_INVALID",
            "post-render review mode must be off, shadow, report, or enforce",
        )
    return value


def _hash_json(value: Any) -> str:
    return sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _bounded_editing_prompt(value: str) -> str:
    return str(value or "")[:_MAX_EDITING_PROMPT_CHARS]


def critic_call_fingerprint(
    manifest: RenderEvidenceManifest,
    *,
    editing_prompt: str,
    narrative_context: Mapping[str, Any] | None = None,
    model: str = "unknown",
    reasoning_effort: str = "unknown",
) -> str:
    return _hash_json({
        "version": RENDER_CRITIC_VERSION,
        "prompt_version": RENDER_CRITIC_PROMPT_VERSION,
        "prompt_sha256": sha256(RENDER_CRITIC_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "response_schema_sha256": structured_output(RENDER_CRITIC_SCHEMA).fingerprint,
        "model": str(model or "unknown")[:80],
        "reasoning_effort": str(reasoning_effort or "unknown")[:20],
        "candidate_fingerprint": manifest.candidate_fingerprint,
        "call_fingerprint": manifest.call_fingerprint,
        "plan_sha256": manifest.plan_sha256,
        "effects_sha256": manifest.effects_sha256,
        "editing_prompt_sha256": sha256(
            _bounded_editing_prompt(editing_prompt).encode("utf-8")
        ).hexdigest(),
        "narrative_context_sha256": _hash_json(narrative_context or {}),
    })


def _attempts(client: Any) -> list[dict[str, Any]]:
    result = []
    for item in getattr(client, "last_attempts", ())[:6]:
        result.append({
            "number": max(0, int(getattr(item, "number", 0) or 0)),
            "status_code": getattr(item, "status_code", None),
            "reason": sanitize_text(getattr(item, "reason", ""), limit=160),
            "duration_ms": max(0, int(getattr(item, "duration_ms", 0) or 0)),
            "input_tokens": getattr(item, "input_tokens", None),
            "output_tokens": getattr(item, "output_tokens", None),
            "reasoning_tokens": getattr(item, "reasoning_tokens", None),
            "total_tokens": getattr(item, "total_tokens", None),
            "cost_usd": getattr(item, "cost_usd", None),
        })
    return result


def build_render_critic_prompt(
    manifest: RenderEvidenceManifest,
    *,
    editing_prompt: str,
    narrative_context: Mapping[str, Any] | None = None,
) -> str:
    evidence = []
    for clip in manifest.clips:
        for frame in clip.frames:
            evidence.append({
                "image_index": len(evidence) + 1,
                "evidence_id": frame.evidence_id,
                "clip_index": frame.clip_index,
                "timestamp_ms": frame.timestamp_ms,
                "purpose": list(frame.purpose),
                "width": frame.width,
                "height": frame.height,
                "sha256": frame.sha256,
            })
    return json.dumps({
        "task": (
            "Review the supplied rendered-video evidence as a creative editor. "
            "Make recommendations only; do not execute edits or decide promotion. "
            "Assess composition, framing, captions, pacing, narrative coherence, "
            "transitions, effects, visual hierarchy, and relevance."
        ),
        "editing_prompt": _bounded_editing_prompt(editing_prompt),
        "narrative_context": narrative_context or {},
        "evidence": evidence,
        "effect_execution": [
            {
                "clip_index": clip.clip_index,
                **clip.effect_execution.model_dump(mode="json"),
            }
            for clip in manifest.clips
            if clip.effect_execution is not None
        ],
        "constraints": {
            "scope": "rendered_evidence_only",
            "non_mutating": True,
            "evidence_ids_only": True,
            "no_commands_paths_or_filters": True,
            "no_provider_body_or_private_data_echo": True,
            "supported_capabilities": sorted(_SAFE_CAPABILITIES),
        },
        "required_output": {
            "status": "pass|review",
            "scope": "rendered_evidence_only",
            "non_mutating": True,
            "summary": "bounded sanitized summary",
            "findings": [{
                "finding_key": "caption-contrast-1",
                "category": "captions",
                "severity": "advisory|warning|blocker",
                "classification": "creative|objective|technical|advisory",
                "confidence": 0.0,
                "clip_index": 1,
                "start_ms": 0,
                "end_ms": 1000,
                "evidence_ids": ["ev-..."],
                "explanation": "only what the evidence supports",
                "repair_objective": "bounded desired change",
                "requested_capabilities": [],
                "repairable": False,
            }],
        },
    }, ensure_ascii=True, separators=(",", ":"))


def _validate_response(
    raw: Mapping[str, Any],
    *,
    manifest: RenderEvidenceManifest,
    call_fingerprint: str,
) -> dict[str, Any]:
    try:
        response = RenderCriticResponseWire.model_validate(raw)
    except ValidationError as exc:
        raise RenderCriticError(
            "RENDER_CRITIC_RESPONSE_INVALID",
            "critic response does not match the strict schema",
        ) from exc
    if response.non_mutating is not True or response.scope != "rendered_evidence_only":
        raise RenderCriticError(
            "RENDER_CRITIC_RESPONSE_INVALID",
            "critic response must remain non-mutating and evidence-scoped",
        )
    if (response.status == "pass" and response.findings) or (
        response.status == "review" and not response.findings
    ):
        raise RenderCriticError(
            "RENDER_CRITIC_RESPONSE_INVALID",
            "critic status must agree with whether findings are present",
        )
    frames = {
        frame.evidence_id: frame
        for clip in manifest.clips
        for frame in clip.frames
    }
    clip_durations = {clip.clip_index: clip.duration_ms for clip in manifest.clips}
    fingerprints: set[str] = set()
    findings: list[dict[str, Any]] = []
    for item in response.findings:
        if (
            item.end_ms <= item.start_ms
            or item.clip_index not in clip_durations
            or item.end_ms > clip_durations[item.clip_index]
        ):
            raise RenderCriticError(
                "RENDER_CRITIC_EVIDENCE_INVALID",
                "critic finding window is invalid",
            )
        if not set(item.requested_capabilities) <= _SAFE_CAPABILITIES:
            raise RenderCriticError(
                "RENDER_CRITIC_EVIDENCE_INVALID",
                "critic requested an unsupported capability",
            )
        effect_evidence = next(
            (
                clip.effect_execution
                for clip in manifest.clips
                if clip.clip_index == item.clip_index
            ),
            None,
        )
        if item.category == "effects" and item.repairable and (
            "effect" not in item.requested_capabilities
            or effect_evidence is None
            or effect_evidence.status != "executed"
        ):
            raise RenderCriticError(
                "RENDER_CRITIC_EVIDENCE_INVALID",
                "repairable effect findings require executed effect evidence",
            )
        evidence_ids = tuple(dict.fromkeys(item.evidence_ids))
        if len(evidence_ids) != len(item.evidence_ids) or not evidence_ids:
            raise RenderCriticError(
                "RENDER_CRITIC_EVIDENCE_INVALID",
                "critic evidence IDs must be unique and non-empty",
            )
        referenced = [frames.get(evidence_id) for evidence_id in evidence_ids]
        if any(frame is None for frame in referenced):
            raise RenderCriticError(
                "RENDER_CRITIC_EVIDENCE_INVALID",
                "critic referenced evidence outside the supplied manifest",
            )
        if any(frame.clip_index != item.clip_index for frame in referenced if frame is not None):
            raise RenderCriticError(
                "RENDER_CRITIC_EVIDENCE_INVALID",
                "critic evidence clip does not match the finding",
            )
        if any(
            frame.timestamp_ms < item.start_ms or frame.timestamp_ms >= item.end_ms
            for frame in referenced
            if frame is not None
        ):
            raise RenderCriticError(
                "RENDER_CRITIC_EVIDENCE_INVALID",
                "critic evidence timestamp is outside the finding window",
            )
        fingerprint = _hash_json({
            "call_fingerprint": call_fingerprint,
            "category": item.category,
            "classification": item.classification,
            "clip_index": item.clip_index,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "evidence_ids": sorted(evidence_ids),
            "repair_objective": item.repair_objective,
        })
        if fingerprint in fingerprints:
            raise RenderCriticError(
                "RENDER_CRITIC_EVIDENCE_INVALID",
                "critic returned duplicate findings",
            )
        fingerprints.add(fingerprint)
        findings.append({
            "finding_id": "finding-" + fingerprint[:24],
            "finding_fingerprint": fingerprint,
            "defect_code": "RENDER_CRITIC_FINDING",
            "finding_key": sanitize_text(item.finding_key, limit=80),
            "category": item.category,
            "severity": item.severity,
            "classification": item.classification,
            "confidence": item.confidence,
            "clip_index": item.clip_index,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "evidence_ids": list(evidence_ids),
            "explanation": sanitize_text(item.explanation, limit=600),
            "repair_objective": sanitize_text(item.repair_objective, limit=320),
            "requested_capabilities": list(item.requested_capabilities),
            "repairable": item.repairable,
            "lifecycle": "observed",
        })
    return {
        "version": RENDER_CRITIC_VERSION,
        "prompt_version": RENDER_CRITIC_PROMPT_VERSION,
        "prompt_sha256": sha256(RENDER_CRITIC_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "response_schema": RENDER_CRITIC_SCHEMA,
        "response_schema_sha256": structured_output(RENDER_CRITIC_SCHEMA).fingerprint,
        "status": response.status,
        "scope": response.scope,
        "non_mutating": True,
        "summary": sanitize_text(response.summary, limit=600),
        "call_fingerprint": call_fingerprint,
        "candidate_fingerprint": manifest.candidate_fingerprint,
        "provider_calls": 1,
        "finding_count": len(findings),
        "findings": findings,
    }


def render_critic_report_from_checkpoint(
    payload: Mapping[str, Any],
    *,
    expected_call_fingerprint: str,
    expected_candidate_fingerprint: str,
) -> dict[str, Any]:
    report = dict(payload)
    if (
        report.get("version") != RENDER_CRITIC_VERSION
        or report.get("non_mutating") is not True
        or report.get("scope") != "rendered_evidence_only"
        or report.get("call_fingerprint") != expected_call_fingerprint
        or report.get("candidate_fingerprint") != expected_candidate_fingerprint
        or report.get("status") not in {"pass", "review", "unavailable", "skipped"}
        or not isinstance(report.get("findings"), list)
        or len(report["findings"]) > 64
    ):
        raise RenderCriticError(
            "RENDER_CRITIC_RESPONSE_INVALID",
            "critic checkpoint does not match the rendered candidate",
        )
    report["checkpoint_reused"] = True
    report["provider_calls"] = 0
    return report


async def review_render_evidence(
    manifest: RenderEvidenceManifest,
    *,
    image_data_urls: Mapping[str, str],
    client: Any,
    editing_prompt: str,
    narrative_context: Mapping[str, Any] | None = None,
    mode: str = "report",
    previous_call_fingerprint: str | None = None,
) -> dict[str, Any]:
    if mode not in POST_RENDER_REVIEW_MODES - {"off"}:
        raise RenderCriticError("POST_RENDER_REVIEW_MODE_INVALID", "critic mode is disabled or invalid")
    call_fingerprint = critic_call_fingerprint(
        manifest,
        editing_prompt=editing_prompt,
        narrative_context=narrative_context,
        model=getattr(client, "model", "unknown"),
        reasoning_effort=getattr(client, "reasoning_effort", "unknown"),
    )
    if previous_call_fingerprint and previous_call_fingerprint == call_fingerprint:
        return {
            "version": RENDER_CRITIC_VERSION,
            "prompt_version": RENDER_CRITIC_PROMPT_VERSION,
            "response_schema": RENDER_CRITIC_SCHEMA,
            "status": "skipped",
            "scope": "rendered_evidence_only",
            "non_mutating": True,
            "summary": "identical rendered evidence was already reviewed",
            "call_fingerprint": call_fingerprint,
            "candidate_fingerprint": manifest.candidate_fingerprint,
            "provider_calls": 0,
            "finding_count": 0,
            "findings": [],
            "skip_reason": "duplicate_call_fingerprint",
        }
    ordered_urls = []
    for clip in manifest.clips:
        for frame in clip.frames:
            value = image_data_urls.get(frame.evidence_id)
            if not value:
                raise RenderCriticError(
                    "RENDER_CRITIC_EVIDENCE_INVALID",
                    "transient frame data is unavailable for the critic",
                )
            ordered_urls.append(value)
    if not ordered_urls:
        raise RenderCriticError("RENDER_CRITIC_EVIDENCE_INVALID", "no rendered evidence frames are available")
    try:
        raw = await client.complete_structured(
            schema_name=RENDER_CRITIC_SCHEMA,
            system_prompt=RENDER_CRITIC_SYSTEM_PROMPT,
            user_prompt=build_render_critic_prompt(
                manifest,
                editing_prompt=editing_prompt,
                narrative_context=narrative_context,
            ),
            image_data_urls=tuple(ordered_urls),
        )
        report = _validate_response(
            raw,
            manifest=manifest,
            call_fingerprint=call_fingerprint,
        )
        report["mode"] = mode
        report["model"] = sanitize_text(getattr(client, "model", "unknown"), limit=80)
        report["reasoning_effort"] = sanitize_text(
            getattr(client, "reasoning_effort", "unknown"),
            limit=20,
        )
        report["attempts"] = _attempts(client)
        return report
    except RenderCriticError:
        raise
    except Exception as exc:
        return {
            "version": RENDER_CRITIC_VERSION,
            "prompt_version": RENDER_CRITIC_PROMPT_VERSION,
            "prompt_sha256": sha256(RENDER_CRITIC_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
            "response_schema": RENDER_CRITIC_SCHEMA,
            "response_schema_sha256": structured_output(RENDER_CRITIC_SCHEMA).fingerprint,
            "status": "unavailable",
            "scope": "rendered_evidence_only",
            "non_mutating": True,
            "summary": "creative rendered review was unavailable",
            "call_fingerprint": call_fingerprint,
            "candidate_fingerprint": manifest.candidate_fingerprint,
            "provider_calls": 1,
            "finding_count": 0,
            "findings": [],
            "mode": mode,
            "model": sanitize_text(getattr(client, "model", "unknown"), limit=80),
            "reasoning_effort": sanitize_text(
                getattr(client, "reasoning_effort", "unknown"),
                limit=20,
            ),
            "error_code": str(getattr(exc, "code", "RENDER_CRITIC_UNAVAILABLE"))[:80],
            "attempts": _attempts(client),
        }


__all__ = [
    "POST_RENDER_REVIEW_MODES",
    "RENDER_CRITIC_PROMPT_VERSION",
    "RENDER_CRITIC_VERSION",
    "RenderCriticError",
    "build_render_critic_prompt",
    "critic_call_fingerprint",
    "render_review_mode",
    "render_critic_report_from_checkpoint",
    "review_render_evidence",
]
