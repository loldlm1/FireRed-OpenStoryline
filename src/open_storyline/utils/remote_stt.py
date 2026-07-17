from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
import os
import re
import subprocess

import httpx


DEFAULT_STT_MODELS = (
    "mistral/voxtral-mini-2602",
)


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


def _milliseconds(value: Any) -> int:
    try:
        return max(0, int(round(float(value) * 1000)))
    except (TypeError, ValueError):
        return 0


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
        end = max(start, _milliseconds(end_value))
        if not text or end <= start:
            continue
        normalized.append({
            "id": item.get("id", index),
            "text": text,
            "start": start,
            "end": end,
        })
    return normalized


class RemoteSttCascade:
    """OpenAI-compatible remote STT client locked to the approved 9Router model."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        models: Iterable[str],
        timeout: float = 180.0,
        response_format: str = "verbose_json",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "")
        self.models = [str(model).strip() for model in models if str(model).strip()]
        self.timeout = float(timeout)
        self.response_format = response_format
        self.transport = transport
        if not self.base_url:
            raise RemoteSTTError("STT_CONFIG_INVALID", "NINEROUTER_URL or remote_asr.base_url is required")
        if not self.api_key:
            raise RemoteSTTError("STT_CONFIG_INVALID", "NINEROUTER_KEY or remote_asr.api_key is required")
        if not self.models:
            raise RemoteSTTError("STT_CONFIG_INVALID", "at least one remote STT model is required")
        if self.models != list(DEFAULT_STT_MODELS):
            raise RemoteSTTError(
                "STT_CONFIG_INVALID",
                f"remote STT must use only {DEFAULT_STT_MODELS[0]}",
            )

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> "RemoteSttCascade":
        base_url = os.getenv("NINEROUTER_URL") or getattr(config, "base_url", "")
        api_key = os.getenv("NINEROUTER_KEY") or getattr(config, "api_key", "")
        models = _split_models(
            os.getenv("OPENSTORYLINE_STT_MODELS"),
            getattr(config, "models", DEFAULT_STT_MODELS),
        )
        timeout = float(os.getenv("OPENSTORYLINE_STT_TIMEOUT") or getattr(config, "timeout", 180.0))
        response_format = getattr(config, "response_format", "verbose_json")
        return cls(
            base_url=base_url,
            api_key=api_key,
            models=models,
            timeout=timeout,
            response_format=response_format,
            **kwargs,
        )

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/audio/transcriptions"
        return f"{self.base_url}/v1/audio/transcriptions"

    async def transcribe(self, audio_path: str | Path, *, language: str = "") -> STTResult:
        path = Path(audio_path)
        if not path.is_file():
            raise RemoteSTTError("STT_INPUT_MISSING", f"audio file not found: {path.name}")

        attempts: list[STTAttempt] = []
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for model in self.models:
                try:
                    with path.open("rb") as stream:
                        response = await client.post(
                            self.endpoint,
                            headers=headers,
                            data={
                                "model": model,
                                "language": language,
                                "response_format": self.response_format,
                            },
                            files={"file": (path.name, stream, "audio/mpeg")},
                        )
                    if response.status_code >= 400:
                        attempts.append(STTAttempt(
                            model=model,
                            success=False,
                            status_code=response.status_code,
                            reason=_sanitize_reason(response.text, [self.api_key]),
                        ))
                        continue

                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {"text": response.text}
                    text = str(payload.get("text") or "").strip()
                    if not text:
                        attempts.append(STTAttempt(model, False, response.status_code, "empty transcript"))
                        continue
                    segments = normalize_segments(payload)
                    if self.response_format == "verbose_json" and not segments:
                        attempts.append(STTAttempt(
                            model,
                            False,
                            response.status_code,
                            "transcript has no timestamped segments",
                        ))
                        continue
                    attempts.append(STTAttempt(model, True, response.status_code, "ok"))
                    return STTResult(
                        model=model,
                        text=text,
                        segments=segments,
                        attempts=attempts,
                    )
                except (httpx.HTTPError, OSError) as exc:
                    attempts.append(STTAttempt(
                        model=model,
                        success=False,
                        status_code=None,
                        reason=_sanitize_reason(str(exc), [self.api_key]),
                    ))

        summary = "; ".join(f"{item.model}: {item.reason}" for item in attempts)
        raise RemoteSTTError("STT_ALL_PROVIDERS_FAILED", summary or "all remote STT models failed", attempts)


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
