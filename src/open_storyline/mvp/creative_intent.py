from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, Sequence
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CREATIVE_INTENT_VERSION = "creative_intent.v2"
ALLOWED_OMISSION_REASONS = frozenset({
    "source_satisfies_intent",
    "no_evidence_backed_gap",
    "duplicate_visual_purpose",
})

IntentSource = Literal["settings", "user_prompt", "clip_selection", "planner"]
IntentRequirement = Literal["required", "optional"]
IntentScope = Literal["plan", "per_clip"]
AssetIntentKind = Literal["generated_image", "stock_image", "stock_video"]
AssetIntentProvider = Literal["9router", "pexels"]
OperationIntentKind = Literal[
    "portrait_reframe",
    "footer_captions",
    "opening_title",
    "reframe_sequence",
    "restrained_transitions",
]


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreativeAssetIntent(IntentModel):
    id: str = Field(min_length=1, max_length=80)
    source: IntentSource
    requirement: IntentRequirement
    scope: IntentScope
    clip_index: int | None = Field(default=None, ge=1, le=50)
    kind: AssetIntentKind
    provider: AssetIntentProvider
    count: int = Field(ge=1, le=8)
    purpose: str = Field(min_length=1, max_length=240)
    duration_min_ms: int = Field(default=0, ge=0, le=25_000)
    duration_max_ms: int = Field(default=0, ge=0, le=25_000)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", text):
            raise ValueError("intent id contains unsafe characters")
        return text

    @field_validator("purpose")
    @classmethod
    def clean_purpose(cls, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:240]

    @model_validator(mode="after")
    def validate_contract(self) -> "CreativeAssetIntent":
        if self.scope == "per_clip" and self.clip_index is None:
            raise ValueError("per-clip intent requires clip_index")
        if self.scope == "plan" and self.clip_index is not None:
            raise ValueError("plan intent cannot declare clip_index")
        if self.kind == "generated_image" and self.provider != "9router":
            raise ValueError("generated image intent must use 9router")
        if self.kind.startswith("stock_") and self.provider != "pexels":
            raise ValueError("stock intent must use pexels")
        if bool(self.duration_min_ms) != bool(self.duration_max_ms):
            raise ValueError("asset duration bounds must be both set or both zero")
        if self.duration_max_ms and self.duration_max_ms < self.duration_min_ms:
            raise ValueError("asset duration maximum must not precede its minimum")
        return self


class CreativeOperationIntent(IntentModel):
    id: str = Field(min_length=1, max_length=80)
    source: IntentSource
    requirement: IntentRequirement
    scope: IntentScope
    clip_index: int | None = Field(default=None, ge=1, le=50)
    kind: OperationIntentKind
    purpose: str = Field(min_length=1, max_length=240)
    count_min: int = Field(default=1, ge=1, le=16)
    count_max: int = Field(default=32, ge=1, le=32)
    start_max_ms: int = Field(default=0, ge=0, le=15_000)
    duration_min_ms: int = Field(default=0, ge=0, le=15_000)
    duration_max_ms: int = Field(default=0, ge=0, le=15_000)

    @model_validator(mode="after")
    def validate_scope(self) -> "CreativeOperationIntent":
        if self.scope == "per_clip" and self.clip_index is None:
            raise ValueError("per-clip intent requires clip_index")
        if self.scope == "plan" and self.clip_index is not None:
            raise ValueError("plan intent cannot declare clip_index")
        if self.count_max < self.count_min:
            raise ValueError("operation count maximum must not precede its minimum")
        if bool(self.duration_min_ms) != bool(self.duration_max_ms):
            raise ValueError("operation duration bounds must be both set or both zero")
        if self.duration_max_ms and self.duration_max_ms < self.duration_min_ms:
            raise ValueError("operation duration maximum must not precede its minimum")
        return self


class CreativeIntentDecision(IntentModel):
    intent_id: str = Field(min_length=1, max_length=80)
    decision: Literal["execute", "omit"]
    asset_ids: tuple[str, ...] = Field(default=(), max_length=8)
    operation_ids: tuple[str, ...] = Field(default=(), max_length=32)
    omission_reason: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def validate_decision(self) -> "CreativeIntentDecision":
        if self.decision == "execute" and not (self.asset_ids or self.operation_ids):
            raise ValueError("executed intent decision needs an asset or operation mapping")
        if self.decision == "omit":
            if self.asset_ids or self.operation_ids:
                raise ValueError("omitted intent decision cannot map executable IDs")
            if self.omission_reason not in ALLOWED_OMISSION_REASONS:
                raise ValueError("omission reason is not allowlisted")
        elif self.omission_reason:
            raise ValueError("executed intent decision cannot include an omission reason")
        return self


class CreativeIntent(IntentModel):
    version: Literal[CREATIVE_INTENT_VERSION] = CREATIVE_INTENT_VERSION
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    settings_version: int = Field(default=1, ge=1)
    asset_intents: tuple[CreativeAssetIntent, ...] = Field(default=(), max_length=32)
    operation_intents: tuple[CreativeOperationIntent, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_ids(self) -> "CreativeIntent":
        ids = [item.id for item in (*self.asset_intents, *self.operation_intents)]
        if len(ids) != len(set(ids)):
            raise ValueError("creative intent IDs must be unique")
        return self

    @property
    def has_required_assets(self) -> bool:
        return any(item.requirement == "required" for item in self.asset_intents)

    def planner_payload(self, *, clip_index: int) -> dict[str, Any]:
        scoped = self.for_clip(clip_index)
        return {
            "version": self.version,
            "asset_intents": [
                item.model_dump(mode="json") for item in scoped.asset_intents
            ],
            "operation_intents": [
                item.model_dump(mode="json") for item in scoped.operation_intents
            ],
        }

    def for_clip(self, clip_index: int) -> "CreativeIntent":
        return CreativeIntent(
            prompt_sha256=self.prompt_sha256,
            settings_version=self.settings_version,
            asset_intents=tuple(
                item
                for item in self.asset_intents
                if item.scope == "plan" and clip_index == 1
                or item.scope == "per_clip" and item.clip_index == clip_index
            ),
            operation_intents=tuple(
                item
                for item in self.operation_intents
                if item.scope == "plan" and clip_index == 1
                or item.scope == "per_clip" and item.clip_index == clip_index
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class CreativeIntentConformance:
    required: dict[str, int]
    requested: dict[str, int]
    used: dict[str, int]
    operations: dict[str, int]
    decision_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": CREATIVE_INTENT_VERSION,
            "status": "conformant",
            "counts": {
                "required": dict(self.required),
                "requested": dict(self.requested),
                "used": dict(self.used),
                "operations": dict(self.operations),
            },
            "decision_count": self.decision_count,
        }


_ONE = r"(?:exactly\s+)?(?:one|1|a\s+single|una?\s+sola?|exactamente\s+una?)"
_REQUIRED = r"(?:must\s+(?:use|include|add)|use|include|add|requiere|usa|incluye|agrega)"
_GENERATED_IMAGE = r"(?:generated|ai[- ]generated|gpt[- ]generated|generada?|editorial)\s+(?:editorial\s+)?(?:image|still|visual|imagen)"
_PEXELS_VIDEO = r"(?:vertical\s+)?(?:pexels\s+(?:stock\s+)?video|(?:stock\s+)?video\s+(?:from|de)\s+pexels|video\s+pexels)"
_OPENING_TITLE_PATTERNS = (
    r"\b(?:opening|intro(?:ductory)?)\s+(?:title|title\s+card)\b",
    r"\b(?:title|title\s+card)\b.{0,40}\b(?:opening|beginning|start)\b",
    r"\b(?:titulo|cartela|placa|tarjeta)\s+(?:de\s+)?(?:apertura|inicial|introductori[ao])\b",
    r"\b(?:titulo|cartela|placa|tarjeta)\b.{0,40}\b(?:al\s+inicio|al\s+principio|de\s+entrada)\b",
    r"\b(?:abre|empieza|comienza)\b.{0,60}\b(?:titulo|cartela|placa|tarjeta)\b",
)
_REFRAME_OPERATION = r"(?:reencuadr\w*|refram\w*|zooms?|acercamientos?|push[- ]?ins?)"
_REFRAME_RANGE_PATTERNS = (
    rf"\b(?:between|entre)\s+(?:2|two|dos)\s+(?:and|y)\s+(?:4|four|cuatro)\b.{{0,60}}\b{_REFRAME_OPERATION}\b",
    rf"\b(?:2|two|dos)\s*(?:-|to|a)\s*(?:4|four|cuatro)\b.{{0,60}}\b{_REFRAME_OPERATION}\b",
    rf"\b{_REFRAME_OPERATION}\b.{{0,60}}\b(?:between|entre)?\s*(?:2|two|dos)\s*(?:-|to|a|and|y)\s*(?:4|four|cuatro)\b",
)
_RESTRAINED_TRANSITION_PATTERNS = (
    r"\b(?:subtle|restrained|gentle|smooth|soft|sutil(?:es)?|discret(?:as|os)?|suav(?:es)?)\b.{0,30}\b(?:transitions?|transiciones?)\b",
    r"\b(?:transitions?|transiciones?)\b.{0,30}\b(?:subtle|restrained|gentle|smooth|soft|sutil(?:es)?|discret(?:as|os)?|suav(?:es)?)\b",
)

_CONFORMANCE_ERROR_PATTERNS = (
    (r"^intent decisions must be unique across the edit plan$", "intent_decisions_not_unique"),
    (r"^intent decision references an unknown intent$", "unknown_intent_decision"),
    (r"^required intent (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) is not fully executable$", "required_asset_not_executable"),
    (r"^required intent (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) lacks an execute decision$", "required_asset_decision_missing"),
    (r"^optional intent (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) lacks a decision$", "optional_asset_decision_missing"),
    (r"^intent decision (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) does not map its asset requests$", "asset_decision_request_mismatch"),
    (r"^intent decision (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) references unknown operations$", "intent_operation_unknown"),
    (r"^intent decision (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) does not map executed overlays$", "asset_decision_overlay_mismatch"),
    (r"^intent (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) asset duration is outside its contract$", "asset_duration_outside_contract"),
    (r"^intent (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) visible duration is outside its contract$", "asset_visible_duration_outside_contract"),
    (r"^required operation intent (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) lacks an execute decision$", "required_operation_decision_missing"),
    (r"^required operation intent (?P<intent_id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}) lacks valid segment mappings$", "required_operation_mapping_invalid"),
    (r"^required footer captions are absent from the plan$", "footer_captions_absent"),
    (r"^required portrait reframe is absent from the plan$", "portrait_reframe_absent"),
    (r"^required opening title is absent or outside its timing contract$", "opening_title_invalid"),
    (r"^required reframe sequence count is outside its contract$", "reframe_sequence_count_invalid"),
    (r"^required restrained transitions are absent or outside their contract$", "restrained_transitions_invalid"),
    (r"^plan narrative claims a generated image without an executable request$", "generated_asset_narrative_mismatch"),
    (r"^plan narrative claims stock media without an executable request$", "stock_asset_narrative_mismatch"),
)


def creative_intent_conformance_evidence(exc: ValueError) -> dict[str, str]:
    message = str(exc)
    for pattern, code in _CONFORMANCE_ERROR_PATTERNS:
        match = re.fullmatch(pattern, message)
        if match is None:
            continue
        evidence = {"constraint_code": code}
        intent_id = match.groupdict().get("intent_id")
        if intent_id:
            evidence["intent_id"] = intent_id
        return evidence
    return {"constraint_code": "intent_conformance_failed"}


def _explicit_one(prompt: str, asset_pattern: str) -> bool:
    patterns = (
        rf"\b{_REQUIRED}\b.{{0,50}}\b{_ONE}\b.{{0,100}}\b{asset_pattern}\b",
        rf"\b{_ONE}\b.{{0,100}}\b{asset_pattern}\b",
        rf"\b{asset_pattern}\b.{{0,100}}\b{_ONE}\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, prompt, flags=re.IGNORECASE):
            prefix = prompt[max(0, match.start() - 120):match.start()]
            prefix = re.split(r"[.!?;\n]", prefix)[-1]
            if not re.search(
                r"\b(?:do\s+not|don't|no\s+usar|sin)\b",
                prefix,
                re.IGNORECASE,
            ):
                return True
    return False


def _fold_prompt(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(
        r"\s+",
        " ",
        folded.replace("\u2013", "-").replace("\u2014", "-").lower(),
    ).strip()


def _positive_pattern(prompt: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        for match in re.finditer(pattern, prompt, flags=re.IGNORECASE):
            prefix = prompt[max(0, match.start() - 80):match.start()]
            prefix = re.split(r"[.!?;\n]", prefix)[-1]
            if not re.search(
                r"\b(?:do\s+not|don't|without|avoid|no|sin|evita|evitar)\b",
                prefix,
                flags=re.IGNORECASE,
            ):
                return True
    return False


def _duration_bounds(prompt: str, asset_pattern: str, default: tuple[int, int]) -> tuple[int, int]:
    match = re.search(asset_pattern, prompt, flags=re.IGNORECASE)
    if not match:
        return default
    start = max(0, match.start() - 120)
    end = min(len(prompt), match.end() + 160)
    nearby = prompt[start:end]
    durations = list(re.finditer(
        r"(?:approximately|about|aproximadamente|durante)?\s*(\d{1,2})\s*(?:-|–|to|a)\s*(\d{1,2})\s*(?:seconds?|secs?|s|segundos?)\b",
        nearby,
        flags=re.IGNORECASE,
    ))
    if not durations:
        return default
    asset_center = (match.start() + match.end()) / 2 - start
    duration = min(
        durations,
        key=lambda item: abs(((item.start() + item.end()) / 2) - asset_center),
    )
    minimum, maximum = int(duration.group(1)), int(duration.group(2))
    if not 1 <= minimum <= maximum <= 25:
        return default
    return minimum * 1000, maximum * 1000


def build_creative_intent(
    editing_prompt: str,
    settings: dict[str, Any],
    *,
    selected_clip_count: int,
) -> CreativeIntent:
    stored_prompt = str(editing_prompt or "").strip()
    prompt = re.sub(r"\s+", " ", stored_prompt)
    folded_prompt = _fold_prompt(prompt)
    prompt_hash = sha256(stored_prompt.encode("utf-8")).hexdigest()
    assets: list[CreativeAssetIntent] = []
    operations: list[CreativeOperationIntent] = []

    generated_policy = str(settings.get("asset_policy") or "auto").strip().lower()
    stock_policy = str(settings.get("stock_policy") or "off").strip().lower()
    generated_count = max(0, int(settings.get("max_generated_assets_per_clip") or 0))
    stock_count = max(0, int(settings.get("max_stock_assets_per_clip") or 0))

    if generated_policy == "required" and generated_count:
        for clip_index in range(1, selected_clip_count + 1):
            assets.append(CreativeAssetIntent(
                id=f"settings-generated-image-clip-{clip_index:02d}",
                source="settings",
                requirement="required",
                scope="per_clip",
                clip_index=clip_index,
                kind="generated_image",
                provider="9router",
                count=generated_count,
                purpose="close evidence-backed conceptual visual gaps",
            ))
    elif _explicit_one(prompt, _GENERATED_IMAGE):
        minimum, maximum = _duration_bounds(prompt, _GENERATED_IMAGE, (2000, 4000))
        assets.append(CreativeAssetIntent(
            id="prompt-generated-image",
            source="user_prompt",
            requirement="required",
            scope="plan",
            kind="generated_image",
            provider="9router",
            count=1,
            purpose="close the explicit conceptual visual gap",
            duration_min_ms=minimum,
            duration_max_ms=maximum,
        ))

    if stock_policy == "required" and stock_count:
        stock_kind = str(settings.get("stock_asset_kind") or "video").strip().lower()
        kind: AssetIntentKind = "stock_image" if stock_kind == "image" else "stock_video"
        for clip_index in range(1, selected_clip_count + 1):
            assets.append(CreativeAssetIntent(
                id=f"settings-{kind.replace('_', '-')}-clip-{clip_index:02d}",
                source="settings",
                requirement="required",
                scope="per_clip",
                clip_index=clip_index,
                kind=kind,
                provider="pexels",
                count=stock_count,
                purpose="close evidence-backed real-world visual gaps",
            ))
    elif _explicit_one(prompt, _PEXELS_VIDEO):
        minimum, maximum = _duration_bounds(prompt, _PEXELS_VIDEO, (3000, 5000))
        assets.append(CreativeAssetIntent(
            id="prompt-pexels-video",
            source="user_prompt",
            requirement="required",
            scope="plan",
            kind="stock_video",
            provider="pexels",
            count=1,
            purpose="close the explicit real-world visual gap",
            duration_min_ms=minimum,
            duration_max_ms=maximum,
        ))

    if re.search(r"\b(?:footer[- ]safe|footer|pie)\b.{0,60}\b(?:captions?|subtitles?|subtitulos?)\b", folded_prompt, re.IGNORECASE):
        operations.append(CreativeOperationIntent(
            id="prompt-footer-captions",
            source="user_prompt",
            requirement="required",
            scope="plan",
            kind="footer_captions",
            purpose="keep captions readable inside the footer safe zone",
        ))
    if re.search(r"\b(?:portrait|vertical|9:16)\b.{0,80}\b(?:crop|refram|encuadr|recort)\w*\b", folded_prompt, re.IGNORECASE):
        operations.append(CreativeOperationIntent(
            id="prompt-portrait-reframe",
            source="user_prompt",
            requirement="required",
            scope="plan",
            kind="portrait_reframe",
            purpose="fill the portrait canvas while preserving the primary subject",
        ))

    if _positive_pattern(folded_prompt, _OPENING_TITLE_PATTERNS):
        operations.append(CreativeOperationIntent(
            id="prompt-opening-title",
            source="user_prompt",
            requirement="required",
            scope="plan",
            kind="opening_title",
            purpose="render a concise title during the opening hook",
            count_min=1,
            count_max=1,
            start_max_ms=3500,
            duration_min_ms=800,
            duration_max_ms=5000,
        ))
    if _positive_pattern(folded_prompt, _REFRAME_RANGE_PATTERNS):
        operations.append(CreativeOperationIntent(
            id="prompt-reframe-sequence",
            source="user_prompt",
            requirement="required",
            scope="plan",
            kind="reframe_sequence",
            purpose="apply the requested bounded sequence of visible reframes or focus zooms",
            count_min=2,
            count_max=4,
        ))
    if _positive_pattern(folded_prompt, _RESTRAINED_TRANSITION_PATTERNS):
        operations.append(CreativeOperationIntent(
            id="prompt-restrained-transitions",
            source="user_prompt",
            requirement="required",
            scope="plan",
            kind="restrained_transitions",
            purpose="use short restrained transitions between editorial segments",
            count_min=1,
            count_max=4,
            duration_min_ms=100,
            duration_max_ms=650,
        ))

    return CreativeIntent(
        prompt_sha256=prompt_hash,
        settings_version=max(1, int(settings.get("settings_version") or 1)),
        asset_intents=tuple(assets),
        operation_intents=tuple(operations),
    )


def validate_intent_capabilities(
    intent: CreativeIntent,
    *,
    generated_available: bool,
    stock_available: bool,
) -> None:
    required_assets = tuple(
        item for item in intent.asset_intents if item.requirement == "required"
    )
    if any(item.provider == "9router" for item in required_assets) and not generated_available:
        raise ValueError("required generated-image capability is unavailable")
    if any(item.provider == "pexels" for item in required_assets) and not stock_available:
        raise ValueError("required Pexels capability is unavailable")


def validate_creative_intent_conformance(
    plan: Any,
    intent: CreativeIntent,
) -> CreativeIntentConformance:
    clips = tuple(getattr(plan, "clips", ()))
    requests = [
        (clip.clip_index, request)
        for clip in clips
        for request in clip.asset_requests
    ]
    overlays = {
        overlay.id: (clip.clip_index, overlay)
        for clip in clips
        for segment in clip.segments
        for overlay in segment.overlays
    }
    segments = {
        segment.id: (clip.clip_index, segment)
        for clip in clips
        for segment in clip.segments
    }
    used_asset_ids = {
        overlay.asset_id
        for _clip_index, overlay in overlays.values()
        if overlay.kind == "image"
    }
    decisions = [decision for clip in clips for decision in clip.intent_decisions]
    decision_by_intent = {decision.intent_id: decision for decision in decisions}
    if len(decision_by_intent) != len(decisions):
        raise ValueError("intent decisions must be unique across the edit plan")

    known_intent_ids = {
        item.id for item in (*intent.asset_intents, *intent.operation_intents)
    }
    unknown_decisions = sorted(set(decision_by_intent) - known_intent_ids)
    if unknown_decisions:
        raise ValueError("intent decision references an unknown intent")

    required_counts: dict[str, int] = {}
    requested_counts: dict[str, int] = {}
    used_counts: dict[str, int] = {}
    for item in intent.asset_intents:
        matches = [
            request
            for clip_index, request in requests
            if request.kind == item.kind
            and request.provider == item.provider
            and (item.scope == "plan" or clip_index == item.clip_index)
        ]
        used = [request for request in matches if request.id in used_asset_ids]
        key = f"{item.provider}:{item.kind}"
        if item.requirement == "required":
            required_counts[key] = required_counts.get(key, 0) + item.count
        requested_counts[key] = requested_counts.get(key, 0) + len(matches)
        used_counts[key] = used_counts.get(key, 0) + len(used)

        decision = decision_by_intent.get(item.id)
        if item.requirement == "required":
            if len(matches) != item.count or len(used) != item.count:
                raise ValueError(f"required intent {item.id} is not fully executable")
            if decision is None or decision.decision != "execute":
                raise ValueError(f"required intent {item.id} lacks an execute decision")
        elif decision is not None and decision.decision == "omit":
            continue
        elif decision is None and matches:
            raise ValueError(f"optional intent {item.id} lacks a decision")

        if decision is not None and decision.decision == "execute":
            matched_ids = {request.id for request in matches}
            if set(decision.asset_ids) != matched_ids:
                raise ValueError(f"intent decision {item.id} does not map its asset requests")
            if not set(decision.operation_ids) <= set(overlays):
                raise ValueError(f"intent decision {item.id} references unknown operations")
            mapped_assets = {
                overlays[operation_id][1].asset_id
                for operation_id in decision.operation_ids
                if overlays[operation_id][1].kind == "image"
            }
            if mapped_assets != matched_ids:
                raise ValueError(f"intent decision {item.id} does not map executed overlays")

        if item.duration_max_ms:
            for request in matches:
                request_duration = request.timeline_window.duration_ms
                if not item.duration_min_ms <= request_duration <= item.duration_max_ms:
                    raise ValueError(f"intent {item.id} asset duration is outside its contract")
                overlay_duration = sum(
                    overlay.timeline_window.duration_ms
                    for _clip_index, overlay in overlays.values()
                    if overlay.kind == "image" and overlay.asset_id == request.id
                )
                if not item.duration_min_ms <= overlay_duration <= item.duration_max_ms:
                    raise ValueError(f"intent {item.id} visible duration is outside its contract")

    required_operations = {
        item.id: item for item in intent.operation_intents if item.requirement == "required"
    }
    operation_counts: dict[str, int] = {}
    for intent_id, item in required_operations.items():
        decision = decision_by_intent.get(intent_id)
        if decision is None or decision.decision != "execute":
            raise ValueError(f"required operation intent {intent_id} lacks an execute decision")
        scoped_clip = item.clip_index if item.scope == "per_clip" else None
        scoped_segments = {
            segment_id: segment
            for segment_id, (clip_index, segment) in segments.items()
            if scoped_clip is None or clip_index == scoped_clip
        }
        scoped_overlays = {
            overlay_id: overlay
            for overlay_id, (clip_index, overlay) in overlays.items()
            if scoped_clip is None or clip_index == scoped_clip
        }
        mapped_ids = set(decision.operation_ids)
        mapping_domain = (
            set(scoped_overlays)
            if item.kind == "opening_title"
            else set(scoped_segments)
        )
        if not mapped_ids or not mapped_ids <= mapping_domain:
            raise ValueError(
                f"required operation intent {intent_id} lacks valid segment mappings"
            )
        if item.kind == "footer_captions":
            if (
                "subtitles" not in plan.requested_capabilities
            ):
                raise ValueError("required footer captions are absent from the plan")
        elif item.kind == "portrait_reframe":
            if not mapped_ids or any(
                segment_id not in scoped_segments
                or scoped_segments[segment_id].layout.mode != "crop"
                for segment_id in mapped_ids
            ):
                raise ValueError("required portrait reframe is absent from the plan")
        elif item.kind == "opening_title":
            valid_titles = {
                overlay_id
                for overlay_id, overlay in scoped_overlays.items()
                if overlay.kind == "text"
                and overlay.timeline_window.start_ms <= item.start_max_ms
                and item.duration_min_ms
                <= overlay.timeline_window.duration_ms
                <= item.duration_max_ms
            }
            if (
                len(mapped_ids) < item.count_min
                or len(mapped_ids) > item.count_max
                or not mapped_ids <= valid_titles
            ):
                raise ValueError("required opening title is absent or outside its timing contract")
        elif item.kind == "reframe_sequence":
            valid_reframes = {
                segment_id
                for segment_id, segment in scoped_segments.items()
                if segment.layout.mode == "crop"
            }
            if (
                len(mapped_ids) < item.count_min
                or len(mapped_ids) > item.count_max
                or not mapped_ids <= valid_reframes
            ):
                raise ValueError("required reframe sequence count is outside its contract")
        elif item.kind == "restrained_transitions":
            initial_ids = {
                clip.segments[0].id for clip in clips if clip.segments
            }
            valid_transitions = {
                segment_id
                for segment_id, segment in scoped_segments.items()
                if segment_id not in initial_ids
                and segment.transition_in.kind in {"fade", "xfade"}
                and item.duration_min_ms
                <= segment.transition_in.duration_ms
                <= item.duration_max_ms
            }
            if (
                len(mapped_ids) < item.count_min
                or len(mapped_ids) > item.count_max
                or not mapped_ids <= valid_transitions
            ):
                raise ValueError("required restrained transitions are absent or outside their contract")
        else:
            raise ValueError(f"required operation intent {intent_id} lacks valid segment mappings")
        operation_counts[item.kind] = operation_counts.get(item.kind, 0) + len(mapped_ids)

    for clip in clips:
        narrative = " ".join(segment.reason for segment in clip.segments).lower()
        clip_kinds = {request.kind for request in clip.asset_requests}
        if re.search(
            r"\b(?:insert|use|show|overlay|cut\s+to)\b.{0,80}\b(?:generated|gpt|ai-generated)\s+(?:image|visual)\b",
            narrative,
        ) and "generated_image" not in clip_kinds:
            raise ValueError("plan narrative claims a generated image without an executable request")
        if re.search(
            r"\b(?:insert|use|show|overlay|cut\s+to)\b.{0,80}\b(?:pexels|stock)\s+(?:image|video|cutaway)\b",
            narrative,
        ) and not clip_kinds & {"stock_image", "stock_video"}:
            raise ValueError("plan narrative claims stock media without an executable request")

    return CreativeIntentConformance(
        required=required_counts,
        requested=requested_counts,
        used=used_counts,
        operations=operation_counts,
        decision_count=len(decisions),
    )
