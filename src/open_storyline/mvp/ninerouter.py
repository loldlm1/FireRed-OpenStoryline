from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional, Sequence
import json
import os
import re
import time

import httpx
from pydantic import ValidationError

from open_storyline.mvp.structured_outputs import (
    StructuredOutputError,
    parse_structured_output_boundaries,
    structured_output,
)


APPROVED_LLM_MODEL = "cx/gpt-5.6-sol"


@dataclass(frozen=True)
class NineRouterAttempt:
    number: int
    status_code: Optional[int]
    reason: str
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

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


def _usage(payload: Any) -> dict[str, int | float | None]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}

    def integer(name: str, source: dict[str, Any] = usage) -> int | None:
        try:
            value = int(source.get(name))
        except (TypeError, ValueError, OverflowError):
            return None
        return value if 0 <= value <= 100_000_000 else None

    cost_value = usage.get("cost")
    if cost_value is None and isinstance(payload, dict):
        cost_value = payload.get("cost")
    try:
        cost = float(cost_value)
    except (TypeError, ValueError):
        cost = None
    if cost is not None and not 0 <= cost <= 100_000:
        cost = None
    return {
        "input_tokens": integer("prompt_tokens"),
        "output_tokens": integer("completion_tokens"),
        "reasoning_tokens": integer("reasoning_tokens", details),
        "total_tokens": integer("total_tokens"),
        "cost_usd": round(cost, 8) if cost is not None else None,
    }


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
        structured_output_mode: str | None = None,
        structured_output_boundaries: str | Sequence[str] | None = None,
        structured_output_capability_verified: bool | None = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "")
        self.model = str(model or "").strip()
        self.reasoning_effort = str(reasoning_effort or "medium").lower()
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.transport = transport
        self.last_attempts: tuple[NineRouterAttempt, ...] = ()
        self.structured_output_mode = str(
            structured_output_mode
            or os.getenv("OPENSTORYLINE_STRUCTURED_OUTPUT_MODE")
            or "json_object"
        ).strip().lower()
        raw_boundaries = structured_output_boundaries
        if raw_boundaries is None:
            raw_boundaries = os.getenv("OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES", "")
        if isinstance(raw_boundaries, str):
            boundary_value = raw_boundaries
        else:
            boundary_value = ",".join(str(item) for item in raw_boundaries)
        try:
            self.structured_output_boundaries = parse_structured_output_boundaries(
                boundary_value
            )
        except StructuredOutputError as exc:
            raise NineRouterError("NINEROUTER_CONFIG_INVALID", str(exc)) from exc
        if structured_output_capability_verified is None:
            structured_output_capability_verified = str(
                os.getenv(
                    "OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED",
                    "false",
                )
            ).strip().lower() in {"1", "true", "yes", "on"}
        self.structured_output_capability_verified = bool(
            structured_output_capability_verified
        )
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
        if self.structured_output_mode not in {"json_object", "json_schema"}:
            raise NineRouterError(
                "NINEROUTER_CONFIG_INVALID",
                "structured output mode must be json_object or json_schema",
            )
        if (
            self.structured_output_mode == "json_schema"
            and self.structured_output_boundaries
            and not self.structured_output_capability_verified
        ):
            raise NineRouterError(
                "NINEROUTER_SCHEMA_CAPABILITY_UNVERIFIED",
                "strict structured output requires a successful capability probe",
            )

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
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        return await self._complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_data_urls=image_data_urls,
            reasoning_effort=reasoning_effort,
            definition=None,
        )

    async def complete_structured(
        self,
        *,
        schema_name: str,
        system_prompt: str,
        user_prompt: str,
        image_data_urls: Sequence[str] = (),
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        try:
            definition = structured_output(schema_name)
        except StructuredOutputError as exc:
            raise NineRouterError("NINEROUTER_CONFIG_INVALID", str(exc)) from exc
        return await self._complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_data_urls=image_data_urls,
            reasoning_effort=reasoning_effort,
            definition=definition,
        )

    async def _complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_data_urls: Sequence[str],
        reasoning_effort: str | None,
        definition: Any,
    ) -> dict[str, Any]:
        self.last_attempts = ()
        effective_reasoning_effort = str(
            reasoning_effort or self.reasoning_effort
        ).strip().lower()
        if effective_reasoning_effort not in {"low", "medium", "high"}:
            raise NineRouterError(
                "NINEROUTER_CONFIG_INVALID",
                "reasoning effort must be low, medium, or high",
            )
        user_content: Any = user_prompt
        if image_data_urls:
            user_content = [{"type": "text", "text": user_prompt}]
            user_content.extend({
                "type": "image_url",
                "image_url": {"url": str(image_url)},
            } for image_url in image_data_urls)
        use_strict_schema = bool(
            definition is not None
            and self.structured_output_mode == "json_schema"
            and definition.name in self.structured_output_boundaries
        )
        response_format = {"type": "json_object"}
        if use_strict_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": definition.provider_name,
                    "strict": True,
                    "schema": definition.schema,
                },
            }
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "reasoning_effort": effective_reasoning_effort,
            "response_format": response_format,
        }
        attempts: list[NineRouterAttempt] = []
        terminal_code: str | None = None
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for number in range(1, self.max_retries + 2):
                started = time.monotonic()
                try:
                    response = await client.post(self.endpoint, headers=headers, json=request_body)
                except httpx.HTTPError as exc:
                    attempts.append(NineRouterAttempt(
                        number,
                        None,
                        _sanitize(exc, self.api_key),
                        duration_ms=max(0, int(round((time.monotonic() - started) * 1000))),
                    ))
                    continue

                if response.status_code >= 400:
                    attempts.append(NineRouterAttempt(
                        number,
                        response.status_code,
                        f"http_status_{response.status_code}",
                        duration_ms=max(0, int(round((time.monotonic() - started) * 1000))),
                    ))
                    if response.status_code < 500 and response.status_code != 429:
                        if use_strict_schema and response.status_code in {400, 404, 422}:
                            terminal_code = "NINEROUTER_SCHEMA_UNSUPPORTED"
                        break
                    continue

                try:
                    payload = response.json()
                    choice = payload["choices"][0]
                    message = choice["message"]
                    refusal = message.get("refusal")
                    if refusal:
                        terminal_code = "NINEROUTER_RESPONSE_REFUSED"
                        raise ValueError("provider refused the structured response")
                    finish_reason = choice.get("finish_reason", "stop")
                    if finish_reason not in {None, "stop"}:
                        terminal_code = "NINEROUTER_RESPONSE_INCOMPLETE"
                        raise ValueError("provider response did not finish normally")
                    text = _message_text(message["content"]).strip()
                    if use_strict_schema:
                        parsed = json.loads(text)
                        if not isinstance(parsed, dict):
                            raise TypeError("response root must be a JSON object")
                    else:
                        parsed = parse_json_object(text)
                    if use_strict_schema:
                        try:
                            parsed = definition.validate(parsed)
                        except ValidationError as exc:
                            terminal_code = "NINEROUTER_SCHEMA_MISMATCH"
                            raise ValueError(
                                "response did not match the registered schema"
                            ) from exc
                    attempts.append(NineRouterAttempt(
                        number,
                        response.status_code,
                        "ok",
                        duration_ms=max(0, int(round((time.monotonic() - started) * 1000))),
                        **_usage(payload),
                    ))
                    self.last_attempts = tuple(attempts)
                    return parsed
                except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    attempts.append(NineRouterAttempt(
                        number,
                        response.status_code,
                        _sanitize(exc, self.api_key),
                        duration_ms=max(0, int(round((time.monotonic() - started) * 1000))),
                    ))
                    if terminal_code is not None:
                        break

        last_status = attempts[-1].status_code if attempts else None
        code = terminal_code or (
            "NINEROUTER_RESPONSE_INVALID"
            if last_status == 200
            else "NINEROUTER_REQUEST_FAILED"
        )
        summary = "; ".join(f"attempt {item.number}: {item.reason}" for item in attempts)
        self.last_attempts = tuple(attempts)
        raise NineRouterError(code, summary or "9Router request failed", attempts=attempts)
