from __future__ import annotations

from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional
import asyncio
import math
import mimetypes
import os
import re
import subprocess
import time

import httpx


MISTRAL_STT_ENDPOINT = "https://api.mistral.ai/v1/audio/transcriptions"
MISTRAL_STT_MODEL = "voxtral-mini-2602"
MAX_MISTRAL_API_KEYS = 8


@dataclass(frozen=True)
class STTAttempt:
    model: str
    success: bool
    status_code: Optional[int]
    reason: str
    key_ordinal: str = ""
    category: str = ""
    latency_ms: int = 0
    retry_after_seconds: int | None = None
    request_sent: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class STTResult:
    model: str
    text: str
    segments: list[dict[str, Any]]
    attempts: list[STTAttempt]

    @property
    def timestamps(self) -> list[list[int]]:
        return [[item["start"], item["end"]] for item in self.segments]


class RemoteSTTError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        attempts: Iterable[STTAttempt] = (),
        *,
        category: str = "",
    ) -> None:
        self.code = code
        self.attempts = list(attempts)
        self.category = category
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "category": self.category,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


def parse_mistral_api_keys(raw: str | None) -> list[str]:
    return _normalize_mistral_api_keys(str(raw or "").split(","))


def _normalize_mistral_api_keys(values: Iterable[str]) -> list[str]:
    keys: list[str] = []
    for item in values:
        key = str(item).strip()
        if not key or key in keys:
            continue
        if key.lower().startswith("replace-"):
            raise RemoteSTTError(
                "STT_CONFIG_INVALID",
                "MISTRAL_API_KEYS still contains an example value",
            )
        keys.append(key)
        if len(keys) > MAX_MISTRAL_API_KEYS:
            raise RemoteSTTError(
                "STT_CONFIG_INVALID",
                f"MISTRAL_API_KEYS supports at most {MAX_MISTRAL_API_KEYS} values",
            )
    return keys


def _status_category(status: int) -> str:
    if status in {401, 402, 403}:
        return "auth"
    if status == 404:
        return "entitlement"
    if status == 429:
        return "rate_limited"
    if status == 408:
        return "timeout"
    if status in {400, 413, 422}:
        return "input_invalid"
    if status >= 500:
        return "upstream"
    return "http"


def _status_reason(category: str) -> str:
    return {
        "auth": "provider authentication rejected",
        "entitlement": "model entitlement rejected",
        "rate_limited": "provider rate limit reached",
        "timeout": "provider request timed out",
        "input_invalid": "provider rejected the audio request",
        "upstream": "provider temporarily unavailable",
        "http": "provider request failed",
    }[category]


def _retry_after_seconds(value: str | None, *, wall_time: float) -> int:
    default_seconds = 60
    maximum_seconds = 3600
    raw = str(value or "").strip()
    if not raw:
        return default_seconds
    try:
        seconds = float(raw)
    except ValueError:
        try:
            target = parsedate_to_datetime(raw).timestamp()
        except (TypeError, ValueError, OverflowError):
            return default_seconds
        seconds = target - wall_time
    if not math.isfinite(seconds) or seconds <= 0:
        return default_seconds
    return min(maximum_seconds, max(1, int(math.ceil(seconds))))


def _sanitize_reason(value: str, secrets: Iterable[str], limit: int = 600) -> str:
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+\-/=]+", "Bearer ***", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _milliseconds(value: Any) -> int | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return int(round(seconds * 1000))


def normalize_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    raw_segments = payload.get("segments") or payload.get("words") or []
    for index, item in enumerate(raw_segments):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            text = str(item.get("word") or "").strip()
        start_value = item.get("start")
        end_value = item.get("end")
        timestamp = item.get("timestamp")
        if isinstance(timestamp, (list, tuple)) and len(timestamp) >= 2:
            start_value = timestamp[0] if start_value is None else start_value
            end_value = timestamp[1] if end_value is None else end_value
        start = _milliseconds(start_value)
        end = _milliseconds(end_value)
        if not text or start is None or end is None or end <= start:
            continue
        normalized.append({
            "id": item.get("id", index),
            "text": text,
            "start": start,
            "end": end,
        })
    return normalized


class MistralSTTClient:
    """Direct Mistral Voxtral client for the remote-only MVP."""

    def __init__(
        self,
        *,
        api_keys: Iterable[str],
        timeout: float = 180.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        minimum_request_interval: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.api_keys = _normalize_mistral_api_keys(api_keys)
        try:
            self.timeout = float(timeout)
            self.minimum_request_interval = float(minimum_request_interval)
        except (TypeError, ValueError) as exc:
            raise RemoteSTTError(
                "STT_CONFIG_INVALID",
                "Mistral STT timing values must be positive numbers",
            ) from exc
        self.transport = transport
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._sleep = sleep
        self._request_lock = asyncio.Lock()
        self._last_request_started: float | None = None
        self._cooldowns: dict[int, float] = {}
        self._disabled_keys: set[int] = set()
        if not self.api_keys:
            raise RemoteSTTError("STT_CONFIG_INVALID", "MISTRAL_API_KEYS is required")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise RemoteSTTError(
                "STT_CONFIG_INVALID",
                "MISTRAL_STT_TIMEOUT must be a positive number",
            )
        if not math.isfinite(self.minimum_request_interval) or self.minimum_request_interval < 0:
            raise RemoteSTTError(
                "STT_CONFIG_INVALID",
                "Mistral STT request interval must be a non-negative number",
            )

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> "MistralSTTClient":
        api_keys = parse_mistral_api_keys(os.getenv("MISTRAL_API_KEYS"))
        try:
            timeout = float(os.getenv("MISTRAL_STT_TIMEOUT") or getattr(config, "timeout", 180.0))
        except (TypeError, ValueError) as exc:
            raise RemoteSTTError(
                "STT_CONFIG_INVALID",
                "MISTRAL_STT_TIMEOUT must be a positive number",
            ) from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise RemoteSTTError(
                "STT_CONFIG_INVALID",
                "MISTRAL_STT_TIMEOUT must be a positive number",
            )
        kwargs.setdefault("minimum_request_interval", 1.0)
        return cls(
            api_keys=api_keys,
            timeout=timeout,
            **kwargs,
        )

    @property
    def endpoint(self) -> str:
        return MISTRAL_STT_ENDPOINT

    async def transcribe(self, audio_path: str | Path, *, language: str = "") -> STTResult:
        path = Path(audio_path)
        if not path.is_file():
            raise RemoteSTTError("STT_INPUT_MISSING", f"audio file not found: {path.name}")
        if str(language or "").strip():
            raise RemoteSTTError(
                "STT_LANGUAGE_UNSUPPORTED",
                "explicit language cannot be combined with required Mistral segment timestamps",
            )

        async with self._request_lock:
            return await self._transcribe_locked(path)

    def _attempt(
        self,
        *,
        key_index: int,
        success: bool,
        status_code: int | None,
        reason: str,
        category: str,
        started_at: float | None = None,
        retry_after_seconds: int | None = None,
        request_sent: bool = True,
    ) -> STTAttempt:
        latency_ms = 0
        if started_at is not None:
            latency_ms = max(0, int(round((self._monotonic() - started_at) * 1000)))
        return STTAttempt(
            model=MISTRAL_STT_MODEL,
            success=success,
            status_code=status_code,
            reason=reason,
            key_ordinal=f"key_{key_index + 1}",
            category=category,
            latency_ms=latency_ms,
            retry_after_seconds=retry_after_seconds,
            request_sent=request_sent,
        )

    def _terminal_error(self, attempts: list[STTAttempt], category: str) -> RemoteSTTError:
        summary = "; ".join(
            f"{item.key_ordinal} {item.category}: {item.reason}" for item in attempts
        )
        return RemoteSTTError(
            "STT_ALL_PROVIDERS_FAILED",
            summary or "direct Mistral STT failed",
            attempts,
            category=category,
        )

    async def _pace_request(self) -> None:
        now = self._monotonic()
        if self._last_request_started is not None:
            remaining = self.minimum_request_interval - (now - self._last_request_started)
            if remaining > 0:
                await self._sleep(remaining)
                now = self._monotonic()
        self._last_request_started = now

    async def _transcribe_locked(self, path: Path) -> STTResult:
        attempts: list[STTAttempt] = []
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for key_index, api_key in enumerate(self.api_keys):
                now = self._monotonic()
                if key_index in self._disabled_keys:
                    attempts.append(self._attempt(
                        key_index=key_index,
                        success=False,
                        status_code=None,
                        reason="key is disabled for this process",
                        category="disabled",
                        request_sent=False,
                    ))
                    continue
                cooldown_until = self._cooldowns.get(key_index, 0)
                if cooldown_until > now:
                    attempts.append(self._attempt(
                        key_index=key_index,
                        success=False,
                        status_code=None,
                        reason="key is cooling down",
                        category="cooldown",
                        retry_after_seconds=max(1, int(math.ceil(cooldown_until - now))),
                        request_sent=False,
                    ))
                    continue
                self._cooldowns.pop(key_index, None)

                for transient_attempt in range(2):
                    await self._pace_request()
                    started_at = self._monotonic()
                    try:
                        with path.open("rb") as stream:
                            response = await client.post(
                                self.endpoint,
                                headers={"Authorization": f"Bearer {api_key}"},
                                data={
                                    "model": MISTRAL_STT_MODEL,
                                    "timestamp_granularities": "segment",
                                },
                                files={"file": (path.name, stream, content_type)},
                            )
                    except (httpx.HTTPError, OSError) as exc:
                        attempts.append(self._attempt(
                            key_index=key_index,
                            success=False,
                            status_code=None,
                            reason=_sanitize_reason(
                                f"transport error ({exc.__class__.__name__})",
                                self.api_keys,
                            ),
                            category="transport",
                            started_at=started_at,
                        ))
                        if transient_attempt == 0:
                            continue
                        break

                    status = response.status_code
                    if status >= 400:
                        category = _status_category(status)
                        reason = _status_reason(category)
                        retry_after = None
                        if status == 429:
                            retry_after = _retry_after_seconds(
                                response.headers.get("Retry-After"),
                                wall_time=self._wall_time(),
                            )
                            self._cooldowns[key_index] = self._monotonic() + retry_after
                        elif status in {401, 402, 403, 404}:
                            self._disabled_keys.add(key_index)

                        attempts.append(self._attempt(
                            key_index=key_index,
                            success=False,
                            status_code=status,
                            reason=reason,
                            category=category,
                            started_at=started_at,
                            retry_after_seconds=retry_after,
                        ))

                        if status in {400, 413, 422}:
                            raise self._terminal_error(attempts, "input_invalid")
                        if status == 429 or status in {401, 402, 403, 404}:
                            break
                        if status == 408 or status >= 500:
                            if transient_attempt == 0:
                                continue
                            break
                        raise self._terminal_error(attempts, category)

                    try:
                        payload = response.json()
                    except ValueError:
                        attempts.append(self._attempt(
                            key_index=key_index,
                            success=False,
                            status_code=status,
                            reason="invalid JSON response",
                            category="contract_invalid",
                            started_at=started_at,
                        ))
                        raise self._terminal_error(attempts, "contract_invalid")

                    if not isinstance(payload, dict):
                        payload = {}
                    text = str(payload.get("text") or "").strip()
                    segments = normalize_segments(payload)
                    if not text:
                        reason = "empty transcript"
                    elif not segments:
                        reason = "transcript has no timestamped segments"
                    else:
                        attempts.append(self._attempt(
                            key_index=key_index,
                            success=True,
                            status_code=status,
                            reason="ok",
                            category="success",
                            started_at=started_at,
                        ))
                        return STTResult(
                            model=MISTRAL_STT_MODEL,
                            text=text,
                            segments=segments,
                            attempts=attempts,
                        )
                    attempts.append(self._attempt(
                        key_index=key_index,
                        success=False,
                        status_code=status,
                        reason=reason,
                        category="contract_invalid",
                        started_at=started_at,
                    ))
                    raise self._terminal_error(attempts, "contract_invalid")

        categories = {attempt.category for attempt in attempts}
        if categories & {"rate_limited", "cooldown"}:
            category = "rate_limited"
        elif categories & {"auth", "entitlement", "disabled"}:
            category = "auth"
        elif categories & {"transport", "timeout", "upstream"}:
            category = "upstream_unavailable"
        else:
            category = "provider_failed"
        raise self._terminal_error(attempts, category)


def extract_audio_for_stt(video_path: str | Path, output_path: str | Path) -> Path:
    source = Path(video_path)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    if not probe.stdout.strip():
        raise RemoteSTTError("MEDIA_HAS_NO_AUDIO", f"no audio stream found in {source.name}")
    render = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
            "-codec:a", "libmp3lame", "-b:a", "48k", str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if render.returncode != 0 or not target.is_file():
        reason = _sanitize_reason(render.stderr, [])
        raise RemoteSTTError("AUDIO_EXTRACTION_FAILED", reason or "FFmpeg audio extraction failed")
    return target
