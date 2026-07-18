#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

import httpx


FEISHU_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
FEISHU_UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/files"
FEISHU_SEND_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class FeishuSendError(Exception):
    def __init__(
        self,
        stage: str,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.status_code = status_code
        self.provider_code = provider_code

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "stage": self.stage,
            "message": self.message,
        }
        if self.status_code is not None:
            payload["status"] = self.status_code
        if self.provider_code is not None:
            payload["provider_code"] = self.provider_code
        return payload


def load_openclaw_config() -> dict[str, Any]:
    if not OPENCLAW_CONFIG.exists():
        raise FileNotFoundError("OpenClaw configuration is unavailable.")
    data = json.loads(OPENCLAW_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("OpenClaw configuration has an invalid shape.")
    return data


def is_path_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_agent_id(config: dict[str, Any]) -> str:
    cwd = Path.cwd().resolve()
    best_match: tuple[int, str | None] = (0, None)
    for agent in config.get("agents", {}).get("list", []):
        workspace = agent.get("workspace")
        agent_id = agent.get("id")
        if not workspace or not agent_id:
            continue
        workspace_path = Path(workspace).resolve()
        if is_path_within(cwd, workspace_path):
            match_len = len(str(workspace_path))
            if match_len > best_match[0]:
                best_match = (match_len, agent_id)
    if best_match[1]:
        return best_match[1]

    default_agent = (
        config.get("agent", {}).get("default")
        or config.get("agents", {}).get("default")
        or config.get("agents", {}).get("defaults")
    )
    if isinstance(default_agent, dict):
        workspace = default_agent.get("workspace")
        agent_id = default_agent.get("id")
        if workspace and agent_id:
            workspace_path = Path(workspace).resolve()
            if is_path_within(cwd, workspace_path):
                return agent_id

    raise RuntimeError("Unable to resolve a Feishu account for this workspace.")


def resolve_feishu_account(
    config: dict[str, Any], agent_id: str | None = None
) -> tuple[str, str]:
    feishu_config = config.get("channels", {}).get("feishu", {})
    app_id = feishu_config.get("appId")
    app_secret = feishu_config.get("appSecret")
    if app_id and app_secret:
        return app_id, app_secret

    if agent_id is None:
        agent_id = resolve_agent_id(config)

    account_id = None
    for binding in config.get("bindings", []):
        if binding.get("agentId") == agent_id:
            account_id = binding.get("match", {}).get("accountId")
            if account_id:
                break
    if not account_id:
        raise RuntimeError("No Feishu account is bound to this workspace.")

    account = feishu_config.get("accounts", {}).get(account_id)
    if not account:
        raise RuntimeError("The bound Feishu account is unavailable.")
    account_app_id = account.get("appId")
    account_app_secret = account.get("appSecret")
    if not account_app_id or not account_app_secret:
        raise RuntimeError("The bound Feishu account is incomplete.")
    return account_app_id, account_app_secret


def safe_provider_code(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,12}", value):
        return int(value)
    return None


def decode_provider_response(stage: str, response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise FeishuSendError(
            stage,
            "Provider returned invalid JSON.",
            status_code=response.status_code,
        ) from exc

    if not isinstance(data, dict):
        raise FeishuSendError(
            stage,
            "Provider returned an invalid response shape.",
            status_code=response.status_code,
        )

    provider_code = safe_provider_code(data.get("code"))
    if response.is_error:
        raise FeishuSendError(
            stage,
            "Provider request failed.",
            status_code=response.status_code,
            provider_code=provider_code,
        )
    if provider_code not in (None, 0):
        raise FeishuSendError(
            stage,
            "Provider rejected the request.",
            status_code=response.status_code,
            provider_code=provider_code,
        )
    return data


def get_tenant_access_token(
    client: httpx.Client, app_id: str, app_secret: str
) -> str:
    try:
        response = client.post(
            FEISHU_TOKEN_URL,
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15.0,
        )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise FeishuSendError("token", "Provider request failed.") from exc
    data = decode_provider_response("token", response)
    token = data.get("tenant_access_token")
    if not isinstance(token, str) or not token:
        raise FeishuSendError("token", "Provider response omitted the access token.")
    return token


def upload_file(
    client: httpx.Client,
    token: str,
    file_path: Path,
    file_type: str,
) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with file_path.open("rb") as file_handle:
            response = client.post(
                FEISHU_UPLOAD_URL,
                headers=headers,
                data={"file_type": file_type, "file_name": file_path.name},
                files={"file": (file_path.name, file_handle)},
                timeout=30.0,
            )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise FeishuSendError("upload", "Provider request failed.") from exc
    except OSError as exc:
        raise FeishuSendError("upload", "The selected file could not be read.") from exc

    data = decode_provider_response("upload", response)
    response_data = data.get("data")
    file_key = response_data.get("file_key") if isinstance(response_data, dict) else None
    if not isinstance(file_key, str) or not file_key:
        raise FeishuSendError("upload", "Provider response omitted the file key.")
    return file_key


def safe_message_id(data: dict[str, Any]) -> str | None:
    response_data = data.get("data")
    if not isinstance(response_data, dict):
        return None
    message_id = response_data.get("message_id")
    if message_id is None and isinstance(response_data.get("message"), dict):
        message_id = response_data["message"].get("message_id")
    if isinstance(message_id, str) and MESSAGE_ID_RE.fullmatch(message_id):
        return message_id
    return None


def send_file_message(
    client: httpx.Client,
    token: str,
    receive_id: str,
    receive_id_type: str,
    file_key: str,
) -> str | None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}),
    }
    try:
        response = client.post(
            FEISHU_SEND_MSG_URL,
            headers=headers,
            params={"receive_id_type": receive_id_type},
            json=payload,
            timeout=15.0,
        )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise FeishuSendError("send", "Provider request failed.") from exc
    data = decode_provider_response("send", response)
    return safe_message_id(data)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a file to Feishu and send it")
    parser.add_argument("--file", required=True, help="Local file path")
    parser.add_argument("--receive-id", required=True, help="Confirmed destination id")
    parser.add_argument(
        "--receive-id-type",
        required=True,
        choices=("chat_id", "open_id", "user_id"),
        help="Confirmed destination id type",
    )
    parser.add_argument(
        "--file-type",
        default="stream",
        help="Feishu upload file_type, default stream",
    )
    parser.add_argument(
        "--confirm-send",
        action="store_true",
        required=True,
        help="Confirm the file path, destination id, and destination type.",
    )
    return parser.parse_args(argv)


def send_confirmed_file(args: argparse.Namespace, client: httpx.Client) -> str | None:
    try:
        file_path = Path(args.file).expanduser().resolve()
        file_available = file_path.is_file()
    except (OSError, RuntimeError) as exc:
        raise FeishuSendError("input", "The selected file is unavailable.") from exc
    if not file_available:
        raise FeishuSendError("input", "The selected file is unavailable.")

    try:
        config = load_openclaw_config()
        app_id, app_secret = resolve_feishu_account(config)
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as exc:
        raise FeishuSendError(
            "config", "Feishu account configuration is unavailable."
        ) from exc

    token = get_tenant_access_token(client, app_id, app_secret)
    file_key = upload_file(client, token, file_path, args.file_type)
    return send_file_message(
        client,
        token,
        args.receive_id,
        args.receive_id_type,
        file_key,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    client: httpx.Client | None = None,
) -> int:
    args = parse_args(argv)
    try:
        if client is None:
            with httpx.Client() as owned_client:
                message_id = send_confirmed_file(args, owned_client)
        else:
            message_id = send_confirmed_file(args, client)
    except FeishuSendError as exc:
        print(json.dumps(exc.as_payload(), sort_keys=True), file=sys.stderr)
        return 2

    payload: dict[str, Any] = {
        "ok": True,
        "receive_id_type": args.receive_id_type,
    }
    if message_id is not None:
        payload["message_id"] = message_id
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
