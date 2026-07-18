from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional, Sequence
import json
import os
import re

import httpx


APPROVED_LLM_MODEL = "cx/gpt-5.6-sol"


@dataclass(frozen=True)
class NineRouterAttempt:
    number: int
    status_code: Optional[int]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NineRouterError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        attempts: list[NineRouterAttempt] | None = None,
    ) -> None:
        self.code = code
        self.attempts = list(attempts or [])
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


def _sanitize(value: Any, secret: str, limit: int = 800) -> str:
    text = str(value or "")
    if secret:
        text = text.replace(secret, "***")
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+\-/=]+", "Bearer ***", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        value = item.get("text") or item.get("content")
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def parse_json_object(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response does not contain a JSON object")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("response root must be a JSON object")
    return parsed


class NineRouterClient:
    """Small OpenAI-compatible client for remote planning and frame analysis."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = APPROVED_LLM_MODEL,
        reasoning_effort: str = "medium",
        timeout: float = 180.0,
        max_retries: int = 2,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "")
        self.model = str(model or "").strip()
        self.reasoning_effort = str(reasoning_effort or "medium").lower()
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.transport = transport
        self.last_attempts: tuple[NineRouterAttempt, ...] = ()
        if not self.base_url:
            raise NineRouterError("NINEROUTER_CONFIG_INVALID", "NINEROUTER_URL is required")
        if not self.api_key:
            raise NineRouterError("NINEROUTER_CONFIG_INVALID", "NINEROUTER_KEY is required")
        if not self.model:
            raise NineRouterError("NINEROUTER_CONFIG_INVALID", "a remote model is required")
        if self.model != APPROVED_LLM_MODEL:
            raise NineRouterError(
                "NINEROUTER_CONFIG_INVALID",
                f"remote text and vision must use {APPROVED_LLM_MODEL}",
            )
        if self.reasoning_effort not in {"low", "medium", "high"}:
            raise NineRouterError("NINEROUTER_CONFIG_INVALID", "reasoning effort must be low, medium, or high")

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> "NineRouterClient":
        return cls(
            base_url=os.getenv("NINEROUTER_URL") or getattr(config, "base_url", ""),
            api_key=os.getenv("NINEROUTER_KEY") or getattr(config, "api_key", ""),
            model=os.getenv("OPENSTORYLINE_LLM_MODEL") or getattr(config, "model", APPROVED_LLM_MODEL),
            reasoning_effort=(
                os.getenv("OPENSTORYLINE_REASONING_EFFORT")
                or getattr(config, "reasoning_effort", "medium")
            ),
            timeout=float(os.getenv("OPENSTORYLINE_LLM_TIMEOUT") or getattr(config, "timeout", 180.0)),
            max_retries=int(getattr(config, "max_retries", 2)),
            **kwargs,
        )

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_data_urls: Sequence[str] = (),
    ) -> dict[str, Any]:
        self.last_attempts = ()
        user_content: Any = user_prompt
        if image_data_urls:
            user_content = [{"type": "text", "text": user_prompt}]
            user_content.extend({
                "type": "image_url",
                "image_url": {"url": str(image_url)},
            } for image_url in image_data_urls)
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "reasoning_effort": self.reasoning_effort,
            "response_format": {"type": "json_object"},
        }
        attempts: list[NineRouterAttempt] = []
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for number in range(1, self.max_retries + 2):
                try:
                    response = await client.post(self.endpoint, headers=headers, json=request_body)
                except httpx.HTTPError as exc:
                    attempts.append(NineRouterAttempt(number, None, _sanitize(exc, self.api_key)))
                    continue

                if response.status_code >= 400:
                    attempts.append(NineRouterAttempt(
                        number,
                        response.status_code,
                        _sanitize(response.text, self.api_key),
                    ))
                    if response.status_code < 500 and response.status_code != 429:
                        break
                    continue

                try:
                    payload = response.json()
                    content = payload["choices"][0]["message"]["content"]
                    parsed = parse_json_object(_message_text(content))
                    attempts.append(NineRouterAttempt(number, response.status_code, "ok"))
                    self.last_attempts = tuple(attempts)
                    return parsed
                except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    attempts.append(NineRouterAttempt(
                        number,
                        response.status_code,
                        _sanitize(exc, self.api_key),
                    ))

        last_status = attempts[-1].status_code if attempts else None
        code = "NINEROUTER_RESPONSE_INVALID" if last_status == 200 else "NINEROUTER_REQUEST_FAILED"
        summary = "; ".join(f"attempt {item.number}: {item.reason}" for item in attempts)
        self.last_attempts = tuple(attempts)
        raise NineRouterError(code, summary or "9Router request failed", attempts=attempts)
