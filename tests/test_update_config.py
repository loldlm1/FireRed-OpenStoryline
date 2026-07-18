from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_config.py"


class UpdateConfigCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / "config.toml"
        self.config_path.write_text(
            """
[service]
api_key = "placeholder" # keep this comment
enabled = false
workers = 2
ratio = 1.5
""".lstrip(),
            encoding="utf-8",
        )

    def run_cli(
        self,
        *args: str,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--config",
                str(self.config_path),
                *args,
            ],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_set_stdin_updates_secret_without_echoing_it(self) -> None:
        marker = "synthetic-secret-marker"

        result = self.run_cli("--set-stdin", "service.api_key", stdin=marker + "\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Updated: service.api_key")
        self.assertNotIn(marker, result.stdout)
        self.assertNotIn(marker, result.stderr)
        updated = self.config_path.read_text(encoding="utf-8")
        self.assertIn(f'api_key = "{marker}" # keep this comment', updated)

    def test_set_preserves_scalar_coercion_without_printing_values(self) -> None:
        cases = (
            ("service.enabled=true", "enabled = true"),
            ("service.workers=4", "workers = 4"),
            ("service.ratio=2.25", "ratio = 2.25"),
        )

        for assignment, expected in cases:
            with self.subTest(assignment=assignment):
                result = self.run_cli("--set", assignment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(assignment.split("=", 1)[1], result.stdout)
                self.assertIn(expected, self.config_path.read_text(encoding="utf-8"))

    def test_set_and_set_stdin_are_mutually_exclusive_and_required(self) -> None:
        both = self.run_cli(
            "--set",
            "service.enabled=true",
            "--set-stdin",
            "service.api_key",
            stdin="unused",
        )
        omitted = self.run_cli()

        self.assertEqual(both.returncode, 2)
        self.assertEqual(omitted.returncode, 2)
        self.assertIn("not allowed with argument", both.stderr)
        self.assertIn("one of the arguments", omitted.stderr)

    def test_failures_do_not_echo_stdin_values(self) -> None:
        marker = "synthetic-secret-marker"

        result = self.run_cli("--set-stdin", "missing.api_key", stdin=marker)

        self.assertEqual(result.returncode, 2)
        self.assertIn("Configuration item does not exist", result.stderr)
        self.assertNotIn(marker, result.stdout)
        self.assertNotIn(marker, result.stderr)

    def test_malformed_toml_error_does_not_echo_stdin_values(self) -> None:
        marker = "synthetic-secret-marker"
        self.config_path.write_text("[service\napi_key = \"placeholder\"\n", encoding="utf-8")

        result = self.run_cli("--set-stdin", "service.api_key", stdin=marker)

        self.assertEqual(result.returncode, 2)
        self.assertIn("Update failed:", result.stderr)
        self.assertNotIn(marker, result.stdout)
        self.assertNotIn(marker, result.stderr)


if __name__ == "__main__":
    unittest.main()
