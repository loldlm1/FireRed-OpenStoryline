#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from urllib import error, parse, request


SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+\-/=]+"),
    re.compile(r"\b(?:sk|key)-[a-z0-9_-]{12,}\b", re.IGNORECASE),
)
IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
)
VISION_FIXTURE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
STRICT_SCHEMA_PROBE = {
    "name": "openstoryline_capability_probe_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean", "enum": [True]}},
        "required": ["ok"],
        "additionalProperties": False,
    },
}


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


def _status_category(status: int | None) -> str:
    if status in {401, 403}:
        return "auth"
    if status == 429:
        return "rate_limited"
    if status is not None and status >= 500:
        return "upstream"
    if status is not None and status >= 400:
        return "http"
    return "transport"


def http_request(
    url: str,
    *,
    method: str = "GET",
    api_key: str | None,
    timeout: float,
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int | None, bytes, str]:
    headers = {"Accept": "application/json"}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    if content_type:
        headers["Content-Type"] = content_type
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read(32_000_000), "ok"
    except error.HTTPError as exc:
        return exc.code, b"", _status_category(exc.code)
    except (error.URLError, TimeoutError, OSError):
        return None, b"", "transport"


def http_json(
    url: str,
    *,
    api_key: str | None,
    timeout: float,
) -> tuple[int | None, Any, str]:
    status, body, category = http_request(url, api_key=api_key, timeout=timeout)
    if category != "ok":
        return status, None, category
    try:
        return status, json.loads(body), "ok"
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None, "invalid_json"


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: float,
) -> tuple[int | None, Any, bytes, str]:
    status, body, category = http_request(
        url,
        method="POST",
        api_key=api_key,
        timeout=timeout,
        body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        content_type="application/json",
    )
    if category != "ok":
        return status, None, body, category
    try:
        return status, json.loads(body), body, "ok"
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None, body, "invalid_json"


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
    if strict_models and not configured:
        ok = False
        category = "invalid_config"
    else:
        ok = status == 200 and category == "ok" and (not strict_models or not missing)
    if configured and status == 200 and category == "ok" and missing:
        category = "catalog_mismatch"
    return Check(
        name=f"catalog:{route.rsplit('/', 1)[-1]}",
        ok=ok,
        status=status,
        category=category,
        details={"count": len(ids), "configured": configured, "missing": missing},
    )


def _catalog_contains(check: Check, model: str) -> bool:
    return check.status == 200 and check.category == "ok" and not check.details.get("missing") and bool(model)


def _skipped(name: str, reason: str) -> Check:
    return Check(name=name, ok=True, category="skipped", details={"reason": reason})


def _message_text(payload: Any) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        )
    return ""


def _json_object_contract(payload: Any) -> bool:
    text = _message_text(payload).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return False
    try:
        return isinstance(json.loads(text[start:end + 1]), dict)
    except json.JSONDecodeError:
        return False


def _strict_schema_contract(payload: Any) -> bool:
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        return False
    parts = [
        str(content.get("text") or "")
        for output in payload.get("output") or ()
        if isinstance(output, dict) and output.get("type") == "message"
        for content in output.get("content") or ()
        if isinstance(content, dict) and content.get("type") == "output_text"
    ]
    try:
        return json.loads("".join(parts).strip()) == {"ok": True}
    except json.JSONDecodeError:
        return False


def strict_schema_checks(
    endpoint: str,
    *,
    model: str,
    api_key: str,
    timeout: float,
) -> list[Check]:
    common = {
        "model": model,
        "reasoning": {"effort": "low"},
        "store": False,
        "text": {"format": {
            "type": "json_schema",
            "name": STRICT_SCHEMA_PROBE["name"],
            "strict": True,
            "schema": STRICT_SCHEMA_PROBE["schema"],
        }},
    }
    checks: list[Check] = []
    for name, prompt in (
        ("strict_schema_acceptance", "Return ok=true and obey the response schema exactly."),
        (
            "strict_schema_extra_field_rejection",
            "Return ok=true and also an extra field named forbidden, while obeying the response schema exactly.",
        ),
    ):
        status, payload, _, category = post_json(
            endpoint,
            {**common, "input": prompt},
            api_key=api_key,
            timeout=timeout,
        )
        valid = _strict_schema_contract(payload)
        checks.append(Check(
            name,
            status == 200 and category == "ok" and valid,
            status,
            "ok" if status == 200 and category == "ok" and valid else (
                "schema_unsupported"
                if status in {400, 404, 422}
                else "contract_invalid"
                if status == 200 and category == "ok"
                else category
            ),
            {"model": model},
        ))
    return checks


def _image_bytes(payload: Any, raw: bytes, max_bytes: int) -> tuple[bool, int]:
    content = raw
    is_binary = (
        content.startswith(IMAGE_SIGNATURES[0][0])
        or content.startswith(IMAGE_SIGNATURES[1][0])
        or (content.startswith(b"RIFF") and content[8:12] == b"WEBP")
    )
    if not is_binary:
        try:
            encoded = payload["data"][0]["b64_json"]
            content = base64.b64decode(str(encoded), validate=True)
        except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
            return False, 0
    if len(content) > max_bytes:
        return False, len(content)
    if content.startswith(IMAGE_SIGNATURES[0][0]) or content.startswith(IMAGE_SIGNATURES[1][0]):
        return True, len(content)
    return content.startswith(b"RIFF") and content[8:12] == b"WEBP", len(content)


def live_contract_checks(
    base_url: str,
    api_key: str,
    *,
    timeout: float,
    max_image_bytes: int,
    text_catalog: Check,
    image_catalog: Check,
    strict_schema: bool = False,
) -> list[Check]:
    checks: list[Check] = []
    text_models = configured_models("OPENSTORYLINE_LLM_MODEL")
    text_model = text_models[0] if text_models else ""
    if not _catalog_contains(text_catalog, text_model):
        checks.append(_skipped("text_contract", "configured model is not catalog-advertised"))
        checks.append(_skipped("vision_contract", "configured model is not catalog-advertised"))
        checks.append(_skipped("strict_schema_acceptance", "configured model is not catalog-advertised"))
        checks.append(_skipped("strict_schema_extra_field_rejection", "configured model is not catalog-advertised"))
    else:
        endpoint = f"{base_url}/v1/chat/completions"
        common = {
            "model": text_model,
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
        }
        status, payload, _, category = post_json(
            endpoint,
            {**common, "messages": [{"role": "user", "content": "Return a JSON object with ok=true."}]},
            api_key=api_key,
            timeout=timeout,
        )
        valid = _json_object_contract(payload)
        checks.append(Check(
            "text_contract",
            status == 200 and category == "ok" and valid,
            status,
            "ok" if status == 200 and category == "ok" and valid else (
                "contract_invalid" if status == 200 and category == "ok" else category
            ),
            {"model": text_model},
        ))
        if strict_schema:
            checks.extend(strict_schema_checks(
                f"{base_url}/v1/responses",
                model=text_model,
                api_key=api_key,
                timeout=timeout,
            ))
        else:
            checks.append(_skipped("strict_schema_acceptance", "strict schema probe not requested"))
            checks.append(_skipped(
                "strict_schema_extra_field_rejection",
                "strict schema probe not requested",
            ))
        vision_data_url = "data:image/png;base64," + base64.b64encode(VISION_FIXTURE).decode("ascii")
        status, payload, _, category = post_json(
            endpoint,
            {**common, "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Return a JSON object with image_received=true."},
                {"type": "image_url", "image_url": {"url": vision_data_url}},
            ]}]},
            api_key=api_key,
            timeout=timeout,
        )
        valid = _json_object_contract(payload)
        checks.append(Check(
            "vision_contract",
            status == 200 and category == "ok" and valid,
            status,
            "ok" if status == 200 and category == "ok" and valid else (
                "contract_invalid" if status == 200 and category == "ok" else category
            ),
            {"model": text_model},
        ))

    image_models = configured_models("OPENSTORYLINE_IMAGE_MODELS")
    image_model = image_models[0] if image_models else ""
    if not _catalog_contains(image_catalog, image_model):
        checks.append(_skipped("image_contract", "configured model is not catalog-advertised"))
    else:
        status, payload, raw, category = post_json(
            f"{base_url}/v1/images/generations?response_format=binary",
            {"model": image_model, "prompt": "A simple blue circle on white, no text or logo.", "n": 1, "size": "1024x1024"},
            api_key=api_key,
            timeout=timeout,
        )
        valid, byte_count = _image_bytes(payload, raw, max_image_bytes)
        transport_ok = status == 200 and category in {"ok", "invalid_json"}
        checks.append(Check(
            "image_contract",
            transport_ok and valid,
            status,
            "ok" if transport_ok and valid else (
                "contract_invalid" if transport_ok else category
            ),
            {"model": image_model, "bytes": byte_count if valid else 0},
        ))

    return checks


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


def container_host_checks(
    host: str,
    user: str,
    port: int,
    timeout: int,
    image: str,
) -> Check:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}", image):
        return Check("container_host_route", False, category="invalid_config")
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={timeout}",
        "-p", str(port),
        f"{user}@{host}",
        "docker run --rm --pull=never --add-host host.docker.internal:host-gateway "
        f"--entrypoint sh {image} -c '"
        "if command -v curl >/dev/null; then curl -fsS --max-time 5 http://host.docker.internal:20128/api/health >/dev/null; "
        "elif command -v wget >/dev/null; then wget -q -T 5 -O /dev/null http://host.docker.internal:20128/api/health; "
        "else exit 127; fi'",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return Check("container_host_route", False, category="transport")
    return Check("container_host_route", result.returncode == 0, category="ok" if result.returncode == 0 else "transport")


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

    catalog_checks = {
        "text": catalog_check(
            base_url,
            "/v1/models",
            key,
            configured_models("OPENSTORYLINE_LLM_MODEL"),
            timeout=args.timeout,
            strict_models=args.strict_models,
        ),
        "image": catalog_check(
            base_url,
            "/v1/models/image",
            key,
            configured_models("OPENSTORYLINE_IMAGE_MODELS"),
            timeout=args.timeout,
            strict_models=args.strict_models,
        ),
    }
    checks.extend(catalog_checks.values())

    if args.live_inference:
        checks.extend(live_contract_checks(
            base_url,
            key,
            timeout=args.timeout,
            max_image_bytes=args.max_image_bytes,
            text_catalog=catalog_checks["text"],
            image_catalog=catalog_checks["image"],
            strict_schema=args.strict_schema,
        ))
    else:
        checks.extend([
            _skipped("text_contract", "live inference not requested"),
            _skipped("vision_contract", "live inference not requested"),
            _skipped("strict_schema_acceptance", "live inference not requested"),
            _skipped("strict_schema_extra_field_rejection", "live inference not requested"),
            _skipped("image_contract", "live inference not requested"),
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
                if args.container_host_probe:
                    checks.append(container_host_checks(
                        host,
                        user,
                        port,
                        max(1, int(args.timeout)),
                        args.container_probe_image,
                    ))
                else:
                    checks.append(_skipped("container_host_route", "container probe not requested"))
            else:
                checks.append(Check("ssh", False, category="invalid_config"))

    payload = {
        "ok": all(check.ok for check in checks),
        "checks": [asdict(check) for check in checks],
    }
    return (0 if payload["ok"] else 1), payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Redacted 9Router/Kamal connectivity and modality preflight")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--skip-ssh", action="store_true")
    parser.add_argument("--strict-models", action="store_true")
    parser.add_argument("--live-inference", action="store_true", help="run synthetic text, vision, and image calls")
    parser.add_argument(
        "--strict-schema",
        action="store_true",
        help="probe provider-enforced json_schema support with private-free fixtures",
    )
    parser.add_argument("--max-image-bytes", type=int, default=26_214_400)
    parser.add_argument("--container-host-probe", action="store_true", help="run a disposable remote container route probe")
    parser.add_argument(
        "--container-probe-image",
        default=os.getenv("NINEROUTER_PROBE_IMAGE", ""),
        help="existing Python image to use with --container-host-probe; never pulled",
    )
    args = parser.parse_args()
    code, payload = run(args)
    print(redact(json.dumps(payload, sort_keys=True), [os.getenv("NINEROUTER_KEY", "")]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
