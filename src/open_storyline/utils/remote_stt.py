from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
import math
import mimetypes
import os
import re
import subprocess

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
    def __init__(self, code: str, message: str, attempts: Iterable[STTAttempt] = ()) -> None:
        self.code = code
        self.attempts = list(attempts)
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


def parse_mistral_api_keys(raw: str | None) -> list[str]:
    keys: list[str] = []
    for item in str(raw or "").split(","):
        key = item.strip()
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
    ) -> None:
        self.api_keys = [str(key).strip() for key in api_keys if str(key).strip()]
        self.timeout = float(timeout)
        self.transport = transport
        if not self.api_keys:
            raise RemoteSTTError("STT_CONFIG_INVALID", "MISTRAL_API_KEYS is required")
        if len(self.api_keys) != 1:
            raise RemoteSTTError(
                "STT_CONFIG_INVALID",
                "exactly one Mistral API key is supported until key failover is enabled",
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

        attempts: list[STTAttempt] = []
        api_key = self.api_keys[0]
        headers = {"Authorization": f"Bearer {api_key}"}
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            try:
                with path.open("rb") as stream:
                    response = await client.post(
                        self.endpoint,
                        headers=headers,
                        data={
                            "model": MISTRAL_STT_MODEL,
                            "timestamp_granularities": "segment",
                        },
                        files={"file": (path.name, stream, content_type)},
                    )
                if response.status_code >= 400:
                    attempts.append(STTAttempt(
                        model=MISTRAL_STT_MODEL,
                        success=False,
                        status_code=response.status_code,
                        reason=_sanitize_reason(response.text, self.api_keys),
                    ))
                else:
                    try:
                        payload = response.json()
                    except ValueError:
                        attempts.append(STTAttempt(
                            MISTRAL_STT_MODEL,
                            False,
                            response.status_code,
                            "invalid JSON response",
                        ))
                    else:
                        if not isinstance(payload, dict):
                            payload = {}
                        text = str(payload.get("text") or "").strip()
                        segments = normalize_segments(payload)
                        reason = "ok"
                        if not text:
                            reason = "empty transcript"
                        elif not segments:
                            reason = "transcript has no timestamped segments"
                        if reason == "ok":
                            attempts.append(STTAttempt(
                                MISTRAL_STT_MODEL,
                                True,
                                response.status_code,
                                reason,
                            ))
                            return STTResult(
                                model=MISTRAL_STT_MODEL,
                                text=text,
                                segments=segments,
                                attempts=attempts,
                            )
                        attempts.append(STTAttempt(
                            MISTRAL_STT_MODEL,
                            False,
                            response.status_code,
                            reason,
                        ))
            except (httpx.HTTPError, OSError) as exc:
                attempts.append(STTAttempt(
                    model=MISTRAL_STT_MODEL,
                    success=False,
                    status_code=None,
                    reason=_sanitize_reason(str(exc), self.api_keys),
                ))

        summary = "; ".join(f"{item.model}: {item.reason}" for item in attempts)
        raise RemoteSTTError("STT_ALL_PROVIDERS_FAILED", summary or "direct Mistral STT failed", attempts)


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
