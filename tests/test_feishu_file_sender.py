from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / ".claude"
    / "skills"
    / "openstoryline-use"
    / "scripts"
    / "feishu_file_sender.py"
)
SPEC = importlib.util.spec_from_file_location("feishu_file_sender", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
feishu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feishu)


class FeishuFileSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.file_path = Path(self.temp_dir.name) / "output.mp4"
        self.file_path.write_bytes(b"synthetic-video")
        self.args = [
            "--file",
            str(self.file_path),
            "--receive-id",
            "oc_confirmed_destination",
            "--receive-id-type",
            "chat_id",
            "--confirm-send",
        ]
        self.config = {
            "channels": {
                "feishu": {
                    "appId": "cli_test_app",
                    "appSecret": "synthetic-secret-marker",
                }
            }
        }

    def run_main(
        self,
        handler: httpx.MockTransport,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with httpx.Client(transport=handler) as client:
            with patch.object(feishu, "load_openclaw_config", return_value=self.config):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    result = feishu.main(self.args, client=client)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_confirmation_is_required_before_config_or_network_access(self) -> None:
        args = self.args[:-1]
        stderr = io.StringIO()

        with patch.object(feishu, "load_openclaw_config") as load_config:
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                feishu.main(args)

        self.assertEqual(raised.exception.code, 2)
        load_config.assert_not_called()
        self.assertIn("--confirm-send", stderr.getvalue())

    def test_provider_failures_are_bounded_and_redacted(self) -> None:
        marker = "synthetic-secret-marker"
        cases = (
            (
                "token",
                [httpx.Response(401, json={"code": 999, "msg": marker})],
                999,
            ),
            (
                "upload",
                [
                    httpx.Response(200, json={"code": 0, "tenant_access_token": marker}),
                    httpx.Response(500, text=marker),
                ],
                None,
            ),
            (
                "send",
                [
                    httpx.Response(200, json={"code": 0, "tenant_access_token": marker}),
                    httpx.Response(
                        200,
                        json={"code": 0, "data": {"file_key": "file_key_1"}},
                    ),
                    httpx.Response(400, json={"code": 40001, "msg": marker}),
                ],
                40001,
            ),
        )

        for expected_stage, responses, expected_code in cases:
            with self.subTest(stage=expected_stage):
                queued = list(responses)

                def handler(request: httpx.Request) -> httpx.Response:
                    return queued.pop(0)

                result, stdout, stderr = self.run_main(httpx.MockTransport(handler))

                self.assertEqual(result, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn(marker, stderr)
                payload = json.loads(stderr)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["stage"], expected_stage)
                if expected_code is None:
                    self.assertNotIn("provider_code", payload)
                else:
                    self.assertEqual(payload["provider_code"], expected_code)
                self.assertEqual(queued, [])

    def test_success_output_contains_only_stable_non_secret_fields(self) -> None:
        marker = "synthetic-secret-marker"
        responses = [
            httpx.Response(200, json={"code": 0, "tenant_access_token": marker}),
            httpx.Response(
                200,
                json={"code": 0, "data": {"file_key": "file_key_1"}},
            ),
            httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": marker,
                    "data": {"message_id": "om_safe_123", "debug": marker},
                },
            ),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        result, stdout, stderr = self.run_main(httpx.MockTransport(handler))

        self.assertEqual(result, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertNotIn(marker, stdout)
        self.assertNotIn("oc_confirmed_destination", stdout)
        self.assertEqual(
            json.loads(stdout),
            {
                "message_id": "om_safe_123",
                "ok": True,
                "receive_id_type": "chat_id",
            },
        )
        self.assertEqual(responses, [])


if __name__ == "__main__":
    unittest.main()
