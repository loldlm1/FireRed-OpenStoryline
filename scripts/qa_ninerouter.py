#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from urllib import error, parse, request


SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+\-/=]+"),
    re.compile(r"\b(?:sk|key)-[a-z0-9_-]{12,}\b", re.IGNORECASE),
)


@dataclass
class Check:
    name: str
    ok: bool
    status: int | None = None
    category: str = "ok"
    details: dict[str, Any] = field(default_factory=dict)


def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("NINEROUTER_URL must be an absolute http(s) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("NINEROUTER_URL must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def redact(value: str, secrets: Iterable[str] = ()) -> str:
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text[:20_000]


def catalog_ids(payload: Any) -> list[str]:
    items = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    return sorted({
        str(item.get("id") or "").strip()
        for item in items
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    })


def configured_models(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def http_json(
    url: str,
    *,
    api_key: str | None,
    timeout: float,
) -> tuple[int | None, Any, str]:
    headers = {"Accept": "application/json"}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read(2_000_000)
            status = response.status
    except error.HTTPError as exc:
        return exc.code, None, "auth" if exc.code in {401, 403} else "http"
    except (error.URLError, TimeoutError, OSError):
        return None, None, "transport"

    try:
        return status, json.loads(body), "ok"
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None, "invalid_json"


def catalog_check(
    base_url: str,
    route: str,
    api_key: str,
    configured: list[str],
    *,
    timeout: float,
    strict_models: bool,
) -> Check:
    status, payload, category = http_json(
        f"{base_url}{route}", api_key=api_key, timeout=timeout
    )
    ids = catalog_ids(payload)
    missing = [model for model in configured if model not in ids]
    ok = status == 200 and category == "ok" and (not strict_models or not missing)
    if status == 200 and category == "ok" and missing:
        category = "catalog_mismatch"
    return Check(
        name=f"catalog:{route.rsplit('/', 1)[-1]}",
        ok=ok,
        status=status,
        category=category,
        details={"count": len(ids), "configured": configured, "missing": missing},
    )


def ssh_checks(host: str, user: str, port: int, timeout: int) -> list[Check]:
    target = f"{user}@{host}"
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={timeout}",
        "-p", str(port),
        target,
        "docker version --format '{{.Server.Version}}'",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [
            Check("ssh", False, category="transport"),
            Check("remote_docker", False, category="not_checked"),
        ]
    if result.returncode != 0:
        return [
            Check("ssh", False, category="transport"),
            Check("remote_docker", False, category="not_checked"),
        ]
    version = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return [
        Check("ssh", True),
        Check("remote_docker", bool(version), category="ok" if version else "invalid_response"),
    ]


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    key = os.getenv("NINEROUTER_KEY", "").strip()
    if not key:
        return 2, {"ok": False, "error": "NINEROUTER_KEY is required"}
    try:
        base_url = normalize_base_url(os.getenv("NINEROUTER_URL", ""))
    except ValueError as exc:
        return 2, {"ok": False, "error": str(exc)}

    checks: list[Check] = []
    health_status, _, health_category = http_json(
        f"{base_url}/api/health", api_key=None, timeout=args.timeout
    )
    checks.append(Check("health", health_status == 200, health_status, health_category))

    missing_status, _, missing_category = http_json(
        f"{base_url}/v1/models", api_key=None, timeout=args.timeout
    )
    checks.append(Check(
        "auth_missing",
        missing_status == 401,
        missing_status,
        "ok" if missing_status == 401 else missing_category,
    ))
    invalid_status, _, invalid_category = http_json(
        f"{base_url}/v1/models", api_key="invalid-preflight-key", timeout=args.timeout
    )
    checks.append(Check(
        "auth_invalid",
        invalid_status == 401,
        invalid_status,
        "ok" if invalid_status == 401 else invalid_category,
    ))

    checks.extend([
        catalog_check(
            base_url,
            "/v1/models",
            key,
            configured_models("OPENSTORYLINE_LLM_MODEL"),
            timeout=args.timeout,
            strict_models=args.strict_models,
        ),
        catalog_check(
            base_url,
            "/v1/models/image",
            key,
            configured_models("OPENSTORYLINE_IMAGE_MODELS"),
            timeout=args.timeout,
            strict_models=args.strict_models,
        ),
        catalog_check(
            base_url,
            "/v1/models/stt",
            key,
            configured_models("OPENSTORYLINE_STT_MODELS"),
            timeout=args.timeout,
            strict_models=args.strict_models,
        ),
    ])

    if not args.skip_ssh:
        host = os.getenv("KAMAL_HOST", "").strip()
        user = os.getenv("KAMAL_SSH_USER", "root").strip() or "root"
        try:
            port = int(os.getenv("KAMAL_SSH_PORT", "22"))
        except ValueError:
            checks.append(Check("ssh", False, category="invalid_config"))
        else:
            if host:
                checks.extend(ssh_checks(host, user, port, max(1, int(args.timeout))))
            else:
                checks.append(Check("ssh", False, category="invalid_config"))

    payload = {
        "ok": all(check.ok for check in checks),
        "checks": [asdict(check) for check in checks],
    }
    return (0 if payload["ok"] else 1), payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Redacted 9Router/Kamal connectivity preflight")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--skip-ssh", action="store_true")
    parser.add_argument("--strict-models", action="store_true")
    args = parser.parse_args()
    code, payload = run(args)
    print(redact(json.dumps(payload, sort_keys=True), [os.getenv("NINEROUTER_KEY", "")]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
