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

from open_storyline.mvp.ninerouter import NineRouterClient


DETERMINISTIC_SKILLS = frozenset({
    "black_and_white",
    "blur",
    "brightness",
    "chromatic_aberration",
    "color_grade",
    "contrast",
    "deband",
    "denoise",
    "deshake",
    "fade",
    "film_grain",
    "gamma",
    "glow",
    "hue",
    "letterbox",
    "mirror",
    "noise_reduction",
    "normalize",
    "pixelate",
    "quality",
    "rotate",
    "saturation",
    "sharpen",
    "vignette",
    "vintage",
    "volume",
})
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


def _simple_value(value: Any, depth: int = 0) -> bool:
    if depth > 3:
        return False
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= 500
    if isinstance(value, list):
        return len(value) <= 20 and all(_simple_value(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return len(value) <= 20 and all(
            isinstance(key, str) and _simple_value(item, depth + 1)
            for key, item in value.items()
        )
    return False


def validate_effects(value: Any) -> EffectsPlan:
    raw_effects = value.get("effects") if isinstance(value, dict) else None
    if not isinstance(raw_effects, list):
        raise FFMPEGAError("FFMPEGA_PLAN_INVALID", "effects must be an array")
    if len(raw_effects) > 5:
        raise FFMPEGAError("FFMPEGA_PLAN_INVALID", "at most five effects are allowed")
    effects: list[EffectStep] = []
    for raw in raw_effects:
        if not isinstance(raw, dict):
            raise FFMPEGAError("FFMPEGA_PLAN_INVALID", "each effect must be an object")
        skill = str(raw.get("skill") or "").strip()
        if skill not in DETERMINISTIC_SKILLS:
            raise FFMPEGAError("FFMPEGA_SKILL_BLOCKED", f"skill is not deterministic or allowed: {skill}")
        params = raw.get("params") or {}
        if not isinstance(params, dict) or not _simple_value(params):
            raise FFMPEGAError("FFMPEGA_PLAN_INVALID", f"invalid parameters for {skill}")
        for key in params:
            normalized = str(key).lower()
            if any(part in normalized for part in BLOCKED_PARAM_PARTS):
                raise FFMPEGAError("FFMPEGA_PARAMETER_BLOCKED", f"blocked parameter for {skill}: {key}")
        effects.append(EffectStep(skill=skill, params=params))
    return EffectsPlan(effects=effects)


class EffectsPlanner:
    def __init__(self, client: NineRouterClient) -> None:
        self.client = client

    async def plan(self, editing_prompt: str) -> EffectsPlan:
        allowed = ", ".join(sorted(DETERMINISTIC_SKILLS))
        response = await self.client.complete_json(
            system_prompt=(
                "Select zero to five deterministic visual/audio finishing effects for a social video. "
                "Return only {\"effects\":[{\"skill\":string,\"params\":object}]}. "
                "Use an empty array if the user did not request a relevant finishing effect. "
                "Never request transcription, segmentation, generation, upscaling, masking, raw FFmpeg, "
                f"or any skill outside this allowlist: {allowed}."
            ),
            user_prompt=str(editing_prompt or "")[:12_000],
        )
        return validate_effects(response)


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
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self.quality_preset = str(quality_preset or "high")
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
            **kwargs,
        )

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

        pipeline_json = json.dumps(plan.to_ffmpega_pipeline(), ensure_ascii=False)
        workflow = {
            "1": {
                "class_type": "FFMPEGAgent",
                "inputs": {
                    "prompt": "",
                    "video_path": str(source_path),
                    "llm_model": "none",
                    "no_llm_mode": "manual",
                    "quality_preset": self.quality_preset,
                    "seed": 0,
                    "pipeline_json": pipeline_json,
                    "advanced_options": True,
                    "save_output": True,
                    "output_path": str(destination_path),
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
