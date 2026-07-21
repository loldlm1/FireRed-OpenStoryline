from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
import asyncio
import json
import os
import re
import uuid

import httpx
from pydantic import ValidationError

from open_storyline.mvp.ffmpega_contracts import (
    AGENTIC_FINISHING_SKILLS,
    DETERMINISTIC_SKILLS,
    validate_typed_effects,
)
from open_storyline.mvp.ninerouter import NineRouterClient
from open_storyline.mvp.structured_outputs import (
    FFMPEGA_AGENTIC_SCHEMA,
    FFMPEGA_DETERMINISTIC_SCHEMA,
)


BLOCKED_PARAM_PARTS = {"command", "device", "filter", "model", "path", "script", "target", "url"}


class FFMPEGAError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


@dataclass(frozen=True)
class EffectStep:
    skill: str
    params: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EffectsPlan:
    effects: list[EffectStep]

    def to_ffmpega_pipeline(self) -> dict[str, Any]:
        return {
            "effects_mode": "skills" if self.effects else "empty",
            "pipeline": [effect.to_dict() for effect in self.effects],
            "raw_ffmpeg": "",
            "sam3": None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"effects": [effect.to_dict() for effect in self.effects]}


def validate_effects(
    value: Any,
    *,
    allowed_skills: frozenset[str] = DETERMINISTIC_SKILLS,
) -> EffectsPlan:
    raw_effects = value.get("effects") if isinstance(value, dict) else None
    if not isinstance(raw_effects, list):
        raise FFMPEGAError("FFMPEGA_PLAN_INVALID", "effects must be an array")
    if len(raw_effects) > 5:
        raise FFMPEGAError("FFMPEGA_PLAN_INVALID", "at most five effects are allowed")
    for raw in raw_effects:
        if not isinstance(raw, dict):
            raise FFMPEGAError("FFMPEGA_PLAN_INVALID", "each effect must be an object")
        skill = str(raw.get("skill") or "").strip()
        if skill not in allowed_skills:
            raise FFMPEGAError("FFMPEGA_SKILL_BLOCKED", f"skill is not deterministic or allowed: {skill}")
        params = raw.get("params")
        if not isinstance(params, dict):
            raise FFMPEGAError("FFMPEGA_PLAN_INVALID", f"invalid parameters for {skill}")
        for key in params:
            normalized = str(key).lower()
            if any(part in normalized for part in BLOCKED_PARAM_PARTS):
                raise FFMPEGAError("FFMPEGA_PARAMETER_BLOCKED", f"blocked parameter for {skill}: {key}")
    try:
        typed_effects = validate_typed_effects(
            value,
            allowed_skills=allowed_skills,
        )
    except (ValidationError, ValueError) as exc:
        raise FFMPEGAError(
            "FFMPEGA_PLAN_INVALID",
            "effect parameters do not match the pinned typed contract",
        ) from exc
    effects = [
        EffectStep(skill=effect["skill"], params=effect["params"])
        for effect in typed_effects
    ]
    return EffectsPlan(effects=effects)


class EffectsPlanner:
    def __init__(self, client: NineRouterClient) -> None:
        self.client = client

    async def plan(
        self,
        editing_prompt: str,
        *,
        allowed_skills: frozenset[str] = DETERMINISTIC_SKILLS,
    ) -> EffectsPlan:
        allowed = ", ".join(sorted(allowed_skills))
        schema_name = (
            FFMPEGA_AGENTIC_SCHEMA
            if allowed_skills == AGENTIC_FINISHING_SKILLS
            else FFMPEGA_DETERMINISTIC_SCHEMA
        )
        response = await self.client.complete_structured(
            schema_name=schema_name,
            system_prompt=(
                "Select zero to five deterministic visual/audio finishing effects for a social video. "
                "Return the registered typed effects object and include every parameter key; "
                "use null only when the schema permits the authoritative upstream default. "
                "Use an empty array if the user did not request a relevant finishing effect. "
                "Never request transcription, segmentation, generation, upscaling, masking, raw FFmpeg, "
                f"or any skill outside this allowlist: {allowed}."
            ),
            user_prompt=str(editing_prompt or "")[:12_000],
        )
        return validate_effects(response, allowed_skills=allowed_skills)


def ffmpega_enabled(config: Any) -> bool:
    raw = os.getenv("OPENSTORYLINE_FFMPEGA_ENABLED")
    if raw is None:
        return bool(getattr(config, "enabled", False))
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class FFMPEGAClient:
    """ComfyUI prompt-API adapter restricted to FFMPEGA manual mode."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 1800.0,
        poll_interval: float = 1.0,
        quality_preset: str = "high",
        shared_local_root: str = "",
        shared_remote_root: str = "",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self.quality_preset = str(quality_preset or "high")
        self.shared_local_root = Path(shared_local_root).expanduser().resolve() if shared_local_root else None
        self.shared_remote_root = Path(shared_remote_root).expanduser() if shared_remote_root else None
        self.transport = transport
        if not self.base_url.startswith(("http://", "https://")):
            raise FFMPEGAError("FFMPEGA_CONFIG_INVALID", "FFMPEGA_URL must be an HTTP URL")

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> "FFMPEGAClient":
        return cls(
            base_url=os.getenv("FFMPEGA_URL") or getattr(config, "base_url", "http://127.0.0.1:8188"),
            timeout=float(getattr(config, "timeout", 1800.0)),
            poll_interval=float(getattr(config, "poll_interval", 1.0)),
            quality_preset=getattr(config, "quality_preset", "high"),
            shared_local_root=(
                os.getenv("FFMPEGA_LOCAL_OUTPUT_ROOT")
                or getattr(config, "shared_local_root", "")
            ),
            shared_remote_root=(
                os.getenv("FFMPEGA_REMOTE_OUTPUT_ROOT")
                or getattr(config, "shared_remote_root", "")
            ),
            **kwargs,
        )

    def _comfy_path(self, path: Path) -> Path:
        if self.shared_local_root is None and self.shared_remote_root is None:
            return path
        if self.shared_local_root is None or self.shared_remote_root is None:
            raise FFMPEGAError(
                "FFMPEGA_CONFIG_INVALID",
                "both FFMPEGA_LOCAL_OUTPUT_ROOT and FFMPEGA_REMOTE_OUTPUT_ROOT are required",
            )
        try:
            relative = path.relative_to(self.shared_local_root)
        except ValueError as exc:
            raise FFMPEGAError(
                "FFMPEGA_PATH_NOT_SHARED",
                f"path is outside the configured shared root: {path.name}",
            ) from exc
        return self.shared_remote_root / relative

    async def apply(
        self,
        *,
        source: str | Path,
        destination: str | Path,
        plan: EffectsPlan,
    ) -> Path:
        source_path = Path(source).resolve()
        destination_path = Path(destination).resolve()
        if not source_path.is_file():
            raise FFMPEGAError("FFMPEGA_INPUT_MISSING", "input video does not exist")
        if not plan.effects:
            raise FFMPEGAError("FFMPEGA_PLAN_EMPTY", "no effects were selected")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.unlink(missing_ok=True)
        comfy_source = self._comfy_path(source_path)
        comfy_destination = self._comfy_path(destination_path)

        pipeline_json = json.dumps(plan.to_ffmpega_pipeline(), ensure_ascii=False)
        workflow = {
            "1": {
                "class_type": "FFMPEGAgent",
                "inputs": {
                    "prompt": "",
                    "video_path": str(comfy_source),
                    "llm_model": "none",
                    "no_llm_mode": "manual",
                    "quality_preset": self.quality_preset,
                    "seed": 0,
                    "pipeline_json": pipeline_json,
                    "advanced_options": True,
                    "save_output": True,
                    "output_path": str(comfy_destination),
                    "use_vision": False,
                    "verify_output": False,
                    "allow_model_downloads": False,
                },
            },
        }
        client_id = uuid.uuid4().hex
        timeout = httpx.Timeout(self.timeout)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            try:
                queued = await client.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": workflow, "client_id": client_id},
                )
            except httpx.HTTPError as exc:
                raise FFMPEGAError("FFMPEGA_UNAVAILABLE", _clean_reason(exc)) from exc
            if queued.status_code >= 400:
                raise FFMPEGAError("FFMPEGA_QUEUE_FAILED", _clean_reason(queued.text))
            try:
                prompt_id = str(queued.json()["prompt_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise FFMPEGAError("FFMPEGA_RESPONSE_INVALID", "ComfyUI did not return a prompt_id") from exc

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.timeout
            while loop.time() < deadline:
                try:
                    history = await client.get(f"{self.base_url}/history/{prompt_id}")
                except httpx.HTTPError as exc:
                    raise FFMPEGAError("FFMPEGA_UNAVAILABLE", _clean_reason(exc)) from exc
                if history.status_code >= 400:
                    raise FFMPEGAError("FFMPEGA_HISTORY_FAILED", _clean_reason(history.text))
                try:
                    record = history.json().get(prompt_id)
                except (AttributeError, ValueError) as exc:
                    raise FFMPEGAError("FFMPEGA_RESPONSE_INVALID", "invalid ComfyUI history response") from exc
                if record is not None:
                    status = record.get("status") or {}
                    if status.get("status_str") == "error" or status.get("completed") is False:
                        raise FFMPEGAError("FFMPEGA_EXECUTION_FAILED", _clean_reason(status))
                    if destination_path.is_file():
                        return destination_path
                    raise FFMPEGAError(
                        "FFMPEGA_OUTPUT_MISSING",
                        "ComfyUI completed but the shared output file was not found",
                    )
                await asyncio.sleep(self.poll_interval)
        raise FFMPEGAError("FFMPEGA_TIMEOUT", "ComfyUI did not complete before the timeout")


def _clean_reason(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]
