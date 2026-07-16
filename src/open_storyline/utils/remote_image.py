from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional
import base64
import binascii
import os
import re

import httpx


DEFAULT_IMAGE_MODELS = (
    "gemini/gemini-3-pro-image-preview",
    "xai/grok-imagine-image",
)

_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"RIFF", "webp", "image/webp"),
)


@dataclass(frozen=True)
class ImageAttempt:
    model: str
    success: bool
    status_code: Optional[int]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RemoteImageResult:
    model: str
    content: bytes
    extension: str
    content_type: str
    attempts: list[ImageAttempt]


class RemoteImageError(RuntimeError):
    def __init__(self, code: str, message: str, attempts: Iterable[ImageAttempt] = ()) -> None:
        self.code = code
        self.attempts = list(attempts)
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


def _split_models(raw: str | None, fallback: Iterable[str]) -> list[str]:
    values = (raw or "").split(",") if raw is not None else list(fallback)
    return [str(item).strip() for item in values if str(item).strip()]


def _sanitize_reason(value: str, secrets: Iterable[str], limit: int = 600) -> str:
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+\-/=]+", "Bearer ***", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _image_type(content: bytes) -> tuple[str, str] | None:
    for signature, extension, content_type in _IMAGE_SIGNATURES:
        if content.startswith(signature):
            if extension != "webp" or content[8:12] == b"WEBP":
                return extension, content_type
    return None


def _decode_image_response(response: httpx.Response, max_bytes: int) -> tuple[bytes, str, str]:
    content = response.content
    image_type = _image_type(content)
    if image_type is not None:
        if len(content) > max_bytes:
            raise ValueError(f"image exceeds {max_bytes} bytes")
        extension, content_type = image_type
        return content, extension, content_type

    try:
        payload = response.json()
        first = (payload.get("data") or [])[0]
        encoded = first.get("b64_json") if isinstance(first, dict) else None
    except (ValueError, IndexError, AttributeError, TypeError):
        encoded = None
    if not encoded:
        raise ValueError("response did not contain PNG, JPEG, WebP, or b64_json image data")
    try:
        decoded = base64.b64decode(str(encoded), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("response contained invalid b64_json image data") from exc
    if len(decoded) > max_bytes:
        raise ValueError(f"image exceeds {max_bytes} bytes")
    image_type = _image_type(decoded)
    if image_type is None:
        raise ValueError("decoded image is not PNG, JPEG, or WebP")
    extension, content_type = image_type
    return decoded, extension, content_type


class RemoteImageCascade:
    """9Router image cascade with catalog validation and no local fallback."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        models: Iterable[str],
        timeout: float = 180.0,
        max_bytes: int = 25 * 1024 * 1024,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "")
        self.models = [str(model).strip() for model in models if str(model).strip()]
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)
        self.transport = transport
        self._available_models: Optional[set[str]] = None
        if not self.base_url:
            raise RemoteImageError("IMAGE_CONFIG_INVALID", "NINEROUTER_URL is required")
        if not self.api_key:
            raise RemoteImageError("IMAGE_CONFIG_INVALID", "NINEROUTER_KEY is required")
        if not self.models:
            raise RemoteImageError("IMAGE_CONFIG_INVALID", "at least one remote image model is required")
        if self.timeout <= 0 or self.max_bytes <= 0:
            raise RemoteImageError("IMAGE_CONFIG_INVALID", "timeout and max_bytes must be positive")

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> "RemoteImageCascade":
        base_url = os.getenv("NINEROUTER_URL") or getattr(config, "base_url", "")
        api_key = os.getenv("NINEROUTER_KEY") or getattr(config, "api_key", "")
        models = _split_models(
            os.getenv("OPENSTORYLINE_IMAGE_MODELS"),
            getattr(config, "models", DEFAULT_IMAGE_MODELS),
        )
        timeout = float(os.getenv("OPENSTORYLINE_IMAGE_TIMEOUT") or getattr(config, "timeout", 180.0))
        max_bytes = int(
            os.getenv("OPENSTORYLINE_IMAGE_MAX_BYTES")
            or getattr(config, "max_bytes", 25 * 1024 * 1024)
        )
        return cls(
            base_url=base_url,
            api_key=api_key,
            models=models,
            timeout=timeout,
            max_bytes=max_bytes,
            **kwargs,
        )

    @property
    def models_endpoint(self) -> str:
        prefix = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
        return f"{prefix}/models/image"

    @property
    def generations_endpoint(self) -> str:
        prefix = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
        return f"{prefix}/images/generations"

    async def discover_models(self, *, refresh: bool = False) -> set[str]:
        if self._available_models is not None and not refresh:
            return set(self._available_models)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(self.models_endpoint, headers=headers)
        except httpx.HTTPError as exc:
            reason = _sanitize_reason(str(exc), [self.api_key])
            raise RemoteImageError("IMAGE_DISCOVERY_FAILED", reason or "9Router image catalog failed") from exc
        if response.status_code >= 400:
            reason = _sanitize_reason(response.text, [self.api_key])
            raise RemoteImageError(
                "IMAGE_DISCOVERY_FAILED",
                f"9Router image catalog returned HTTP {response.status_code}: {reason}",
            )
        try:
            payload = response.json()
            raw_models = payload.get("data") or []
            available = {
                str(item.get("id")).strip()
                for item in raw_models
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
        except (ValueError, AttributeError, TypeError) as exc:
            raise RemoteImageError("IMAGE_DISCOVERY_FAILED", "9Router returned an invalid image catalog") from exc
        if not available:
            raise RemoteImageError("IMAGE_DISCOVERY_FAILED", "9Router returned an empty image catalog")
        self._available_models = available
        return set(available)

    async def generate(self, prompt: str, *, size: str = "1024x1024") -> RemoteImageResult:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise RemoteImageError("IMAGE_INPUT_INVALID", "image prompt is required")
        if len(clean_prompt) > 8000:
            raise RemoteImageError("IMAGE_INPUT_INVALID", "image prompt exceeds 8000 characters")
        match = re.fullmatch(r"([1-9]\d{2,3})x([1-9]\d{2,3})", str(size or ""))
        if match is None or any(not 256 <= int(value) <= 4096 for value in match.groups()):
            raise RemoteImageError("IMAGE_INPUT_INVALID", "size must be WIDTHxHEIGHT between 256 and 4096")

        available = await self.discover_models()
        candidates = [model for model in self.models if model in available]
        if not candidates:
            configured = ", ".join(self.models)
            raise RemoteImageError(
                "IMAGE_MODELS_UNAVAILABLE",
                f"none of the configured image models are exposed by 9Router: {configured}",
            )

        attempts: list[ImageAttempt] = []
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        endpoint = f"{self.generations_endpoint}?response_format=binary"
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for model in candidates:
                try:
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        json={"model": model, "prompt": clean_prompt, "n": 1, "size": size},
                    )
                    if response.status_code >= 400:
                        attempts.append(ImageAttempt(
                            model=model,
                            success=False,
                            status_code=response.status_code,
                            reason=_sanitize_reason(response.text, [self.api_key]),
                        ))
                        continue
                    try:
                        content, extension, content_type = _decode_image_response(response, self.max_bytes)
                    except ValueError as exc:
                        attempts.append(ImageAttempt(
                            model=model,
                            success=False,
                            status_code=response.status_code,
                            reason=_sanitize_reason(str(exc), [self.api_key]),
                        ))
                        continue
                    attempts.append(ImageAttempt(model, True, response.status_code, "ok"))
                    return RemoteImageResult(
                        model=model,
                        content=content,
                        extension=extension,
                        content_type=content_type,
                        attempts=attempts,
                    )
                except httpx.HTTPError as exc:
                    attempts.append(ImageAttempt(
                        model=model,
                        success=False,
                        status_code=None,
                        reason=_sanitize_reason(str(exc), [self.api_key]),
                    ))

        summary = "; ".join(f"{item.model}: {item.reason}" for item in attempts)
        raise RemoteImageError(
            "IMAGE_ALL_PROVIDERS_FAILED",
            summary or "all remote image models failed",
            attempts,
        )
