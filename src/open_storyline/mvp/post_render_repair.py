from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Iterable, Mapping, Sequence
import json
import re

from pydantic import ValidationError

from open_storyline.mvp.edit_plan import EditPlan, validate_edit_plan
from open_storyline.mvp.ffmpega import EffectsPlan, validate_effects
from open_storyline.mvp.ffmpega_contracts import AGENTIC_FINISHING_SKILLS
from open_storyline.mvp.prompts import (
    POST_RENDER_REPAIR_SYSTEM_PROMPT,
    POST_RENDER_REPAIR_SYSTEM_PROMPT_VERSION,
)
from open_storyline.mvp.render_evidence import RenderEvidenceManifest
from open_storyline.mvp.security import sanitize_text
from open_storyline.mvp.structured_outputs import (
    POST_RENDER_REPAIR_SCHEMA,
    PostRenderRepairResponseV2Wire,
    structured_output,
)


POST_RENDER_REPAIR_VERSION = "post_render_repair.v2"
POST_RENDER_REPAIR_PROMPT_VERSION = POST_RENDER_REPAIR_SYSTEM_PROMPT_VERSION
MAX_POST_RENDER_REPAIR_ROUNDS = 2
_MAX_EDITING_PROMPT_CHARS = 12_000
_SAFE_FINDING_ID = re.compile(r"^finding-[A-Za-z0-9._:-]{1,72}$")
_SEVERITY_WEIGHT = {"advisory": 1.0, "warning": 2.0, "blocker": 4.0}


class PostRenderRepairError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class PostRenderRepairState:
    """Hard gate for one primary repair and one new-defect contingency."""

    def __init__(self) -> None:
        self._rounds: list[str] = []

    @property
    def rounds(self) -> tuple[str, ...]:
        return tuple(self._rounds)

    def authorize(
        self,
        round_name: str,
        *,
        introduced_objective_codes: Iterable[str] = (),
    ) -> None:
        codes = tuple(sorted({str(code) for code in introduced_objective_codes if code}))
        if round_name == "primary" and not self._rounds:
            self._rounds.append(round_name)
            return
        if round_name == "contingency" and self._rounds == ["primary"]:
            if not codes:
                raise PostRenderRepairError(
                    "POST_RENDER_REPAIR_CONTINGENCY_INELIGIBLE",
                    "contingency repair requires a newly introduced objective defect",
                )
            self._rounds.append(round_name)
            return
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_CALL_LIMIT_EXCEEDED",
            "no third post-render repair request is permitted",
        )


@dataclass(frozen=True)
class PostRenderRepairProposal:
    round_name: str
    status: str
    request_fingerprint: str
    base_plan_fingerprint: str
    candidate_plan_fingerprint: str
    affected_clip_indexes: tuple[int, ...]
    finding_ids: tuple[str, ...]
    decisions: tuple[dict[str, Any], ...]
    candidate_plan: EditPlan | None
    effect_action: str
    base_effects_fingerprint: str
    candidate_effects_fingerprint: str
    effect_affected_clip_indexes: tuple[int, ...]
    candidate_effects: EffectsPlan | None
    provider_calls: int
    attempts: tuple[dict[str, Any], ...]
    no_op: bool = False
    checkpoint_reused: bool = False
    error_code: str = ""

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "version": POST_RENDER_REPAIR_VERSION,
            "prompt_version": POST_RENDER_REPAIR_PROMPT_VERSION,
            "response_schema": POST_RENDER_REPAIR_SCHEMA,
            "response_schema_sha256": structured_output(
                POST_RENDER_REPAIR_SCHEMA
            ).fingerprint,
            "round": self.round_name,
            "status": self.status,
            "request_fingerprint": self.request_fingerprint,
            "base_plan_fingerprint": self.base_plan_fingerprint,
            "candidate_plan_fingerprint": self.candidate_plan_fingerprint,
            "affected_clip_indexes": list(self.affected_clip_indexes),
            "finding_ids": list(self.finding_ids),
            "decisions": list(self.decisions),
            "effect_action": self.effect_action,
            "base_effects_fingerprint": self.base_effects_fingerprint,
            "candidate_effects_fingerprint": self.candidate_effects_fingerprint,
            "effect_affected_clip_indexes": list(
                self.effect_affected_clip_indexes
            ),
            "candidate_effect_skills": [
                effect.skill for effect in (self.candidate_effects.effects if self.candidate_effects else ())
            ],
            "provider_calls": self.provider_calls,
            "attempts": list(self.attempts),
            "no_op": self.no_op,
            "checkpoint_reused": self.checkpoint_reused,
            "error_code": self.error_code,
        }

    def to_checkpoint_payload(self) -> dict[str, Any]:
        return {
            "report": self.to_report_dict(),
            **(
                {"candidate_plan": self.candidate_plan.to_dict()}
                if self.candidate_plan is not None
                else {}
            ),
            **(
                {"candidate_effects": self.candidate_effects.to_dict()}
                if self.candidate_effects is not None
                else {}
            ),
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_json(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _plan_fingerprint(plan: EditPlan) -> str:
    return _hash_json(plan.to_dict())


def _effects_fingerprint(plan: EffectsPlan) -> str:
    return _hash_json(plan.to_dict())


def _bounded_prompt(value: str) -> str:
    return str(value or "")[:_MAX_EDITING_PROMPT_CHARS]


def _attempts(client: Any) -> tuple[dict[str, Any], ...]:
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
    return tuple(result)


def eligible_render_findings(
    report: Mapping[str, Any],
    *,
    supported_capabilities: Iterable[str],
) -> tuple[dict[str, Any], ...]:
    available = {
        "focus_zoom" if str(item) == "zoom" else str(item)
        for item in supported_capabilities
    }
    selected: dict[str, dict[str, Any]] = {}
    for raw in report.get("findings") or ():
        if not isinstance(raw, Mapping) or raw.get("repairable") is not True:
            continue
        if str(raw.get("classification") or "") == "technical":
            continue
        finding_id = str(raw.get("finding_id") or "")
        clip_index = int(raw.get("clip_index") or 0)
        requested = tuple(
            "focus_zoom" if str(item) == "zoom" else str(item)
            for item in raw.get("requested_capabilities") or ()
        )
        if (
            not _SAFE_FINDING_ID.fullmatch(finding_id)
            or not 1 <= clip_index <= 8
            or set(requested) - available
        ):
            continue
        selected.setdefault(finding_id, {
            "finding_id": finding_id,
            "finding_fingerprint": str(raw.get("finding_fingerprint") or "")[:64],
            "category": str(raw.get("category") or "")[:40],
            "severity": str(raw.get("severity") or "advisory")[:20],
            "classification": str(raw.get("classification") or "creative")[:20],
            "confidence": max(0.0, min(1.0, float(raw.get("confidence") or 0.0))),
            "clip_index": clip_index,
            "start_ms": max(0, int(raw.get("start_ms") or 0)),
            "end_ms": max(1, int(raw.get("end_ms") or 1)),
            "evidence_ids": [str(item)[:80] for item in (raw.get("evidence_ids") or ())[:16]],
            "explanation": sanitize_text(raw.get("explanation"), limit=600),
            "repair_objective": sanitize_text(raw.get("repair_objective"), limit=320),
            "requested_capabilities": list(requested),
            "repairable": True,
        })
    return tuple(selected[key] for key in sorted(selected))


def objective_findings_for_contingency(
    codes: Iterable[str],
    *,
    clip_indexes: Iterable[int],
    manifest: RenderEvidenceManifest,
) -> tuple[dict[str, Any], ...]:
    indexes = tuple(sorted({int(index) for index in clip_indexes if 1 <= int(index) <= 8}))
    frames_by_clip = {
        clip.clip_index: [frame.evidence_id for frame in clip.frames[:4]]
        for clip in manifest.clips
    }
    findings = []
    for code in sorted({str(item)[:80] for item in codes if item}):
        category = (
            "captions"
            if code.startswith(("CAPTION_", "SUBTITLE_"))
            else "framing"
            if code.startswith(("ACTIVE_PICTURE", "CROP_", "FRAME_"))
            else "transitions"
            if "TRANSITION" in code
            else "composition"
        )
        for clip_index in indexes:
            digest = _hash_json({"code": code, "clip_index": clip_index})[:24]
            findings.append({
                "finding_id": f"finding-objective-{digest}",
                "finding_fingerprint": _hash_json({"code": code, "clip_index": clip_index}),
                "category": category,
                "severity": "blocker",
                "classification": "objective",
                "confidence": 1.0,
                "clip_index": clip_index,
                "start_ms": 0,
                "end_ms": next(
                    (clip.duration_ms for clip in manifest.clips if clip.clip_index == clip_index),
                    1,
                ),
                "evidence_ids": frames_by_clip.get(clip_index, []),
                "explanation": f"Deterministic verification introduced {code}.",
                "repair_objective": f"Resolve {code} without changing source bounds.",
                "requested_capabilities": [],
                "repairable": True,
            })
    return tuple(findings)


def post_render_repair_fingerprint(
    *,
    manifest: RenderEvidenceManifest,
    base_plan: EditPlan,
    base_effects: EffectsPlan,
    findings: Sequence[Mapping[str, Any]],
    editing_prompt: str,
    round_name: str,
    model: str,
    reasoning_effort: str,
) -> str:
    return _hash_json({
        "version": POST_RENDER_REPAIR_VERSION,
        "prompt_version": POST_RENDER_REPAIR_PROMPT_VERSION,
        "prompt_sha256": sha256(
            POST_RENDER_REPAIR_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "response_schema_sha256": structured_output(POST_RENDER_REPAIR_SCHEMA).fingerprint,
        "candidate_fingerprint": manifest.candidate_fingerprint,
        "base_plan_fingerprint": _plan_fingerprint(base_plan),
        "base_effects_fingerprint": _effects_fingerprint(base_effects),
        "findings": [
            {
                "finding_id": item.get("finding_id"),
                "finding_fingerprint": item.get("finding_fingerprint"),
                "clip_index": item.get("clip_index"),
                "repair_objective": item.get("repair_objective"),
            }
            for item in findings
        ],
        "editing_prompt_sha256": sha256(
            _bounded_prompt(editing_prompt).encode("utf-8")
        ).hexdigest(),
        "round": round_name,
        "model": str(model or "unknown")[:80],
        "reasoning_effort": str(reasoning_effort or "unknown")[:20],
    })


def build_post_render_repair_prompt(
    *,
    manifest: RenderEvidenceManifest,
    base_plan: EditPlan,
    base_effects: EffectsPlan,
    findings: Sequence[Mapping[str, Any]],
    editing_prompt: str,
    round_name: str,
) -> str:
    affected = sorted({int(item["clip_index"]) for item in findings})
    clips = [
        clip.model_dump(mode="json")
        for clip in base_plan.clips
        if clip.clip_index in affected
    ]
    evidence = []
    for clip in manifest.clips:
        for frame in clip.frames:
            evidence.append({
                "image_index": len(evidence) + 1,
                "evidence_id": frame.evidence_id,
                "clip_index": frame.clip_index,
                "timestamp_ms": frame.timestamp_ms,
                "purpose": list(frame.purpose),
            })
    return _canonical_json({
        "task": "Produce one bounded typed repair decision for every supplied finding.",
        "round": round_name,
        "editing_prompt": _bounded_prompt(editing_prompt),
        "findings": list(findings),
        "rendered_evidence": evidence,
        "current_clips": clips,
        "current_effect_plan": base_effects.to_dict(),
        "allowed_effect_skills": sorted(AGENTIC_FINISHING_SKILLS),
        "immutable_constraints": {
            "source_duration_ms": base_plan.source_duration_ms,
            "source_bounds_must_not_change": True,
            "unaffected_clips_must_not_change": True,
            "asset_requests_must_not_change": True,
            "intent_decisions_must_not_change": True,
            "effect_plan_must_use_registered_typed_skills": True,
            "effect_plan_is_global_across_output_clips": True,
            "no_commands_paths_urls_or_filters": True,
        },
    })


def _validate_candidate(
    base_plan: EditPlan,
    candidate: EditPlan,
    affected_clip_indexes: Sequence[int],
) -> None:
    affected = set(affected_clip_indexes)
    base_by_index = {clip.clip_index: clip for clip in base_plan.clips}
    candidate_by_index = {clip.clip_index: clip for clip in candidate.clips}
    if set(base_by_index) != set(candidate_by_index):
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "post-render repair cannot change the output count",
        )
    for clip_index, original in base_by_index.items():
        repaired = candidate_by_index[clip_index]
        if clip_index not in affected and repaired != original:
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "post-render repair changed an unaffected clip",
            )
        if original.source_window != repaired.source_window:
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "post-render repair changed source bounds",
            )
        if original.asset_requests != repaired.asset_requests:
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "post-render repair changed asset requests",
            )
        if original.intent_decisions != repaired.intent_decisions:
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "post-render repair changed creative intent decisions",
            )
        original_catalog = original.catalog_selection
        repaired_catalog = repaired.catalog_selection
        if (
            original_catalog.style_profile_id != repaired_catalog.style_profile_id
            or original_catalog.color_treatment_id != repaired_catalog.color_treatment_id
            or original_catalog.recipe_ids != repaired_catalog.recipe_ids
        ):
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "post-render repair changed protected catalog selections",
            )


def _validated_response(
    raw: Mapping[str, Any],
    *,
    base_plan: EditPlan,
    base_effects: EffectsPlan,
    findings: Sequence[Mapping[str, Any]],
    plan_validator: Callable[[dict[str, Any], tuple[int, ...]], EditPlan],
    allowed_effect_skills: frozenset[str],
) -> tuple[
    str,
    tuple[dict[str, Any], ...],
    tuple[int, ...],
    EditPlan | None,
    tuple[int, ...],
    EffectsPlan | None,
    bool,
]:
    try:
        response = PostRenderRepairResponseV2Wire.model_validate(raw)
    except ValidationError as exc:
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "post-render repair response does not match the strict schema",
        ) from exc
    finding_map = {str(item["finding_id"]): item for item in findings}
    decisions = []
    seen: set[str] = set()
    clip_affected: set[int] = set()
    effect_affected: set[int] = set()
    for item in response.decisions:
        finding = finding_map.get(item.finding_id)
        indexes = tuple(sorted(set(item.affected_clip_indexes)))
        if finding is None or item.finding_id in seen:
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "repair decisions must reference every supplied finding exactly once",
            )
        seen.add(item.finding_id)
        expected_index = int(finding["clip_index"])
        if item.decision == "no_change" and (
            item.target != "none" or indexes
        ):
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "no-change decisions cannot declare a repair target",
            )
        if item.decision == "repair" and indexes != (expected_index,):
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "a repair decision must stay inside its finding clip",
            )
        if item.decision == "repair" and item.target == "clip_plan":
            clip_affected.update(indexes)
        elif item.decision == "repair" and item.target == "effect_plan":
            if (
                str(finding.get("category") or "") != "effects"
                and "effect" not in set(finding.get("requested_capabilities") or ())
            ):
                raise PostRenderRepairError(
                    "POST_RENDER_REPAIR_RESPONSE_INVALID",
                    "effect repair must be grounded in an effect finding",
                )
            effect_affected.update(indexes)
        elif item.decision == "repair":
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "repair decisions require a typed clip or effect target",
            )
        decisions.append({
            "finding_id": item.finding_id,
            "decision": item.decision,
            "target": item.target,
            "reason": sanitize_text(item.reason, limit=320),
            "affected_clip_indexes": list(indexes),
        })
    if seen != set(finding_map):
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "repair decisions omitted one or more supplied findings",
        )
    returned_indexes = tuple(sorted(clip.clip_index for clip in response.clips))
    affected_indexes = tuple(sorted(clip_affected))
    effect_affected_indexes = tuple(sorted(effect_affected))
    if len(set(returned_indexes)) != len(returned_indexes):
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "post-render repair returned duplicate clip replacements",
        )
    try:
        candidate_effects = validate_effects(
            response.effect_plan.model_dump(mode="json"),
            allowed_skills=allowed_effect_skills,
        )
    except Exception as exc:
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "post-render effect repair failed the typed allowlist",
        ) from exc
    effect_changed = candidate_effects != base_effects
    if response.effect_action == "preserve" and effect_changed:
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "preserved effect plans must match the current plan",
        )
    if response.effect_action == "replace" and (
        not effect_affected_indexes or not effect_changed
    ):
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "effect replacement requires a material effect repair decision",
        )
    if effect_affected_indexes and response.effect_action != "replace":
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "effect repair decisions require a replacement effect plan",
        )
    if not effect_affected_indexes and response.effect_action != "preserve":
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "an unrelated response cannot replace the effect plan",
        )
    if response.status == "no_change":
        if affected_indexes or effect_affected_indexes or response.clips or any(
            item["decision"] != "no_change" for item in decisions
        ):
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "no-change response contains a repair mutation",
            )
        return response.status, tuple(decisions), (), None, (), None, False
    if not affected_indexes and not effect_affected_indexes:
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "repair response did not declare a material repair target",
        )
    if returned_indexes != affected_indexes:
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "repair response must replace exactly the affected clips",
        )
    candidate = None
    if affected_indexes:
        try:
            candidate = plan_validator({
                "requested_capabilities": response.requested_capabilities,
                "clips": [clip.model_dump(mode="json") for clip in response.clips],
            }, affected_indexes)
        except PostRenderRepairError:
            raise
        except Exception as exc:
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "post-render repair plan failed local validation",
            ) from exc
        _validate_candidate(base_plan, candidate, affected_indexes)
        if candidate == base_plan:
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "clip repair did not materially change the plan",
            )
    no_op = candidate is None and not effect_changed
    return (
        "no_change" if no_op else response.status,
        tuple(decisions),
        affected_indexes,
        candidate,
        effect_affected_indexes,
        candidate_effects if effect_changed else None,
        no_op,
    )


async def request_post_render_repair(
    *,
    manifest: RenderEvidenceManifest,
    image_data_urls: Mapping[str, str],
    base_plan: EditPlan,
    base_effects: EffectsPlan,
    findings: Sequence[Mapping[str, Any]],
    editing_prompt: str,
    round_name: str,
    client: Any,
    plan_validator: Callable[[dict[str, Any], tuple[int, ...]], EditPlan],
    allowed_effect_skills: frozenset[str] = AGENTIC_FINISHING_SKILLS,
) -> PostRenderRepairProposal:
    if round_name not in {"primary", "contingency"} or not findings:
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_REQUEST_INVALID",
            "post-render repair requires a valid round and at least one finding",
        )
    model = str(getattr(client, "model", "unknown"))
    reasoning_effort = str(getattr(client, "reasoning_effort", "unknown"))
    request_fingerprint = post_render_repair_fingerprint(
        manifest=manifest,
        base_plan=base_plan,
        base_effects=base_effects,
        findings=findings,
        editing_prompt=editing_prompt,
        round_name=round_name,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    ordered_urls = []
    for clip in manifest.clips:
        for frame in clip.frames:
            value = image_data_urls.get(frame.evidence_id)
            if not value:
                raise PostRenderRepairError(
                    "POST_RENDER_REPAIR_REQUEST_INVALID",
                    "transient rendered evidence is unavailable for repair",
                )
            ordered_urls.append(value)
    base_fingerprint = _plan_fingerprint(base_plan)
    base_effects_fingerprint = _effects_fingerprint(base_effects)
    try:
        raw = await client.complete_structured(
            schema_name=POST_RENDER_REPAIR_SCHEMA,
            system_prompt=POST_RENDER_REPAIR_SYSTEM_PROMPT,
            user_prompt=build_post_render_repair_prompt(
                manifest=manifest,
                base_plan=base_plan,
                base_effects=base_effects,
                findings=findings,
                editing_prompt=editing_prompt,
                round_name=round_name,
            ),
            image_data_urls=tuple(ordered_urls),
            reasoning_effort=reasoning_effort,
        )
        (
            status,
            decisions,
            affected,
            candidate,
            effect_affected,
            candidate_effects,
            no_op,
        ) = _validated_response(
            raw,
            base_plan=base_plan,
            base_effects=base_effects,
            findings=findings,
            plan_validator=plan_validator,
            allowed_effect_skills=allowed_effect_skills,
        )
        return PostRenderRepairProposal(
            round_name=round_name,
            status=status,
            request_fingerprint=request_fingerprint,
            base_plan_fingerprint=base_fingerprint,
            candidate_plan_fingerprint=(
                _plan_fingerprint(candidate) if candidate is not None else ""
            ),
            affected_clip_indexes=affected,
            finding_ids=tuple(str(item["finding_id"]) for item in findings),
            decisions=decisions,
            candidate_plan=candidate,
            effect_action="replace" if candidate_effects is not None else "preserve",
            base_effects_fingerprint=base_effects_fingerprint,
            candidate_effects_fingerprint=(
                _effects_fingerprint(candidate_effects)
                if candidate_effects is not None
                else ""
            ),
            effect_affected_clip_indexes=effect_affected,
            candidate_effects=candidate_effects,
            provider_calls=1,
            attempts=_attempts(client),
            no_op=no_op,
        )
    except PostRenderRepairError:
        raise
    except Exception as exc:
        return PostRenderRepairProposal(
            round_name=round_name,
            status="unavailable",
            request_fingerprint=request_fingerprint,
            base_plan_fingerprint=base_fingerprint,
            candidate_plan_fingerprint="",
            affected_clip_indexes=(),
            finding_ids=tuple(str(item["finding_id"]) for item in findings),
            decisions=(),
            candidate_plan=None,
            effect_action="preserve",
            base_effects_fingerprint=base_effects_fingerprint,
            candidate_effects_fingerprint="",
            effect_affected_clip_indexes=(),
            candidate_effects=None,
            provider_calls=1,
            attempts=_attempts(client),
            error_code=str(
                getattr(exc, "code", "POST_RENDER_REPAIR_UNAVAILABLE")
            )[:80],
        )


def post_render_repair_from_checkpoint(
    payload: Mapping[str, Any],
    *,
    expected_request_fingerprint: str,
    base_plan: EditPlan,
    base_effects: EffectsPlan,
    allowed_effect_skills: frozenset[str] = AGENTIC_FINISHING_SKILLS,
) -> PostRenderRepairProposal:
    report = payload.get("report")
    if not isinstance(report, Mapping):
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "post-render repair checkpoint report is invalid",
        )
    if (
        report.get("version") != POST_RENDER_REPAIR_VERSION
        or report.get("request_fingerprint") != expected_request_fingerprint
        or report.get("status") not in {"repair", "no_change", "unavailable"}
    ):
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "post-render repair checkpoint does not match the request",
        )
    affected = tuple(sorted({int(item) for item in report.get("affected_clip_indexes") or ()}))
    candidate = None
    if report.get("status") == "repair":
        candidate = validate_edit_plan(
            payload.get("candidate_plan"),
            source_duration_ms=base_plan.source_duration_ms,
        )
        _validate_candidate(base_plan, candidate, affected)
        if _plan_fingerprint(candidate) != report.get("candidate_plan_fingerprint"):
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "post-render repair checkpoint plan fingerprint is invalid",
            )
    candidate_effects = None
    effect_affected = tuple(sorted({
        int(item) for item in report.get("effect_affected_clip_indexes") or ()
    }))
    if report.get("effect_action") == "replace":
        try:
            candidate_effects = validate_effects(
                payload.get("candidate_effects"),
                allowed_skills=allowed_effect_skills,
            )
        except Exception as exc:
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "post-render repair checkpoint effects are invalid",
            ) from exc
        if (
            not effect_affected
            or candidate_effects == base_effects
            or _effects_fingerprint(candidate_effects)
            != report.get("candidate_effects_fingerprint")
        ):
            raise PostRenderRepairError(
                "POST_RENDER_REPAIR_RESPONSE_INVALID",
                "post-render repair checkpoint effect fingerprint is invalid",
            )
    elif report.get("effect_action") != "preserve" or effect_affected:
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "post-render repair checkpoint effect action is invalid",
        )
    if report.get("base_effects_fingerprint") != _effects_fingerprint(base_effects):
        raise PostRenderRepairError(
            "POST_RENDER_REPAIR_RESPONSE_INVALID",
            "post-render repair checkpoint base effects do not match",
        )
    return PostRenderRepairProposal(
        round_name=str(report.get("round") or ""),
        status=str(report.get("status") or "unavailable"),
        request_fingerprint=expected_request_fingerprint,
        base_plan_fingerprint=str(report.get("base_plan_fingerprint") or ""),
        candidate_plan_fingerprint=str(report.get("candidate_plan_fingerprint") or ""),
        affected_clip_indexes=affected,
        finding_ids=tuple(str(item) for item in report.get("finding_ids") or ()),
        decisions=tuple(
            dict(item) for item in report.get("decisions") or () if isinstance(item, Mapping)
        ),
        candidate_plan=candidate,
        effect_action=str(report.get("effect_action") or "preserve"),
        base_effects_fingerprint=str(report.get("base_effects_fingerprint") or ""),
        candidate_effects_fingerprint=str(
            report.get("candidate_effects_fingerprint") or ""
        ),
        effect_affected_clip_indexes=effect_affected,
        candidate_effects=candidate_effects,
        provider_calls=0,
        attempts=(),
        no_op=report.get("no_op") is True,
        checkpoint_reused=True,
        error_code=str(report.get("error_code") or "")[:80],
    )


def compare_critic_improvement(
    original_report: Mapping[str, Any],
    repaired_report: Mapping[str, Any],
) -> dict[str, Any]:
    def repairable(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return [
            item for item in report.get("findings") or ()
            if isinstance(item, Mapping)
            and item.get("repairable") is True
            and str(item.get("classification") or "") != "technical"
        ]

    original = repairable(original_report)
    repaired = repairable(repaired_report)

    def score(items: Sequence[Mapping[str, Any]]) -> float:
        return round(sum(
            _SEVERITY_WEIGHT.get(str(item.get("severity") or "advisory"), 1.0)
            * max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
            for item in items
        ), 4)

    original_score = score(original)
    repaired_score = score(repaired)
    new_blockers = [
        str(item.get("finding_id") or "")
        for item in repaired
        if item.get("severity") == "blocker"
        and float(item.get("confidence") or 0.0) >= 0.7
        and not any(
            prior.get("category") == item.get("category")
            and prior.get("clip_index") == item.get("clip_index")
            for prior in original
        )
    ]
    demonstrated = bool(original) and repaired_score <= max(0.0, original_score - 0.25)
    if new_blockers or repaired_report.get("status") == "unavailable":
        demonstrated = False
    return {
        "version": "post_render_improvement.v1",
        "demonstrated": demonstrated,
        "original_finding_count": len(original),
        "repaired_finding_count": len(repaired),
        "original_weighted_score": original_score,
        "repaired_weighted_score": repaired_score,
        "new_blocker_finding_ids": sorted(item for item in new_blockers if item),
    }


__all__ = [
    "MAX_POST_RENDER_REPAIR_ROUNDS",
    "POST_RENDER_REPAIR_PROMPT_VERSION",
    "POST_RENDER_REPAIR_VERSION",
    "PostRenderRepairError",
    "PostRenderRepairProposal",
    "PostRenderRepairState",
    "build_post_render_repair_prompt",
    "compare_critic_improvement",
    "eligible_render_findings",
    "objective_findings_for_contingency",
    "post_render_repair_fingerprint",
    "post_render_repair_from_checkpoint",
    "request_post_render_repair",
]
