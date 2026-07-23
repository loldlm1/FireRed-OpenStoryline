#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Awaitable, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx

from open_storyline.mvp.remote_stt import (
    MISTRAL_STT_MODEL,
    MistralSTTClient,
    RemoteSTTError,
    parse_mistral_api_keys,
)


@dataclass(frozen=True)
class KeyCheck:
    key_ordinal: str
    ok: bool
    status: int | None
    category: str
    latency_ms: int
    segments: int
    request_attempts: int


def _last_sent_attempt(error: RemoteSTTError) -> Any:
    return next(
        (attempt for attempt in reversed(error.attempts) if attempt.request_sent),
        None,
    )


async def _check_key(
    *,
    key: str,
    key_ordinal: str,
    audio_path: Path,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None,
) -> KeyCheck:
    client = MistralSTTClient(
        api_keys=[key],
        timeout=timeout,
        transport=transport,
        minimum_request_interval=0,
    )
    try:
        result = await client.transcribe(audio_path)
    except RemoteSTTError as exc:
        attempt = _last_sent_attempt(exc)
        return KeyCheck(
            key_ordinal=key_ordinal,
            ok=False,
            status=attempt.status_code if attempt else None,
            category=exc.category or (attempt.category if attempt else "provider_failed"),
            latency_ms=sum(item.latency_ms for item in exc.attempts if item.request_sent),
            segments=0,
            request_attempts=sum(1 for item in exc.attempts if item.request_sent),
        )
    return KeyCheck(
        key_ordinal=key_ordinal,
        ok=True,
        status=result.attempts[-1].status_code,
        category="success",
        latency_ms=sum(item.latency_ms for item in result.attempts if item.request_sent),
        segments=len(result.segments),
        request_attempts=sum(1 for item in result.attempts if item.request_sent),
    )


async def run(
    args: argparse.Namespace,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[int, dict[str, Any]]:
    audio_path = Path(args.audio).expanduser()
    if not audio_path.is_file():
        return 2, {"ok": False, "error": "MISTRAL_QA_STT_AUDIO must be an existing file"}
    try:
        timeout = float(args.timeout)
    except (TypeError, ValueError):
        return 2, {"ok": False, "error": "MISTRAL_QA_TIMEOUT must be a positive number"}
    if timeout <= 0:
        return 2, {"ok": False, "error": "MISTRAL_QA_TIMEOUT must be a positive number"}
    try:
        keys = parse_mistral_api_keys(os.getenv("MISTRAL_API_KEYS"))
    except RemoteSTTError as exc:
        return 2, {"ok": False, "error": exc.code, "category": "invalid_config"}
    if not keys:
        return 2, {"ok": False, "error": "MISTRAL_API_KEYS is required"}

    checks: list[KeyCheck] = []
    if args.each_key:
        for index, key in enumerate(keys):
            if index:
                await sleep(1.0)
            checks.append(await _check_key(
                key=key,
                key_ordinal=f"key_{index + 1}",
                audio_path=audio_path,
                timeout=timeout,
                transport=transport,
            ))
    else:
        client = MistralSTTClient(
            api_keys=keys,
            timeout=timeout,
            transport=transport,
            minimum_request_interval=1.0,
            sleep=sleep,
        )
        try:
            result = await client.transcribe(audio_path)
        except RemoteSTTError as exc:
            sent = [item for item in exc.attempts if item.request_sent]
            checks.append(KeyCheck(
                key_ordinal=sent[-1].key_ordinal if sent else "none",
                ok=False,
                status=sent[-1].status_code if sent else None,
                category=exc.category or "provider_failed",
                latency_ms=sum(item.latency_ms for item in sent),
                segments=0,
                request_attempts=len(sent),
            ))
        else:
            sent = [item for item in result.attempts if item.request_sent]
            checks.append(KeyCheck(
                key_ordinal=sent[-1].key_ordinal,
                ok=True,
                status=sent[-1].status_code,
                category="success",
                latency_ms=sum(item.latency_ms for item in sent),
                segments=len(result.segments),
                request_attempts=len(sent),
            ))

    payload = {
        "ok": any(check.ok for check in checks),
        "model": MISTRAL_STT_MODEL,
        "checks": [asdict(check) for check in checks],
    }
    return (0 if payload["ok"] else 1), payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Redacted direct-Mistral timestamped STT preflight")
    parser.add_argument("--audio", required=True, help="short non-private speech fixture")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--each-key", action="store_true", help="validate every configured key sequentially")
    args = parser.parse_args()
    try:
        code, payload = asyncio.run(run(args))
    except Exception:
        code, payload = 1, {"ok": False, "error": "unexpected QA failure"}
    print(json.dumps(payload, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
