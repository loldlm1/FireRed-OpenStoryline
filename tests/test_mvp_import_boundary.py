import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from open_storyline.mvp.settings import load_mvp_settings


ROOT = Path(__file__).resolve().parents[1]


class MVPImportBoundaryTests(unittest.TestCase):
    def test_remote_app_imports_without_full_agent_modules(self):
        script = textwrap.dedent(
            """
            import builtins
            import sys

            forbidden = (
                "open_storyline.agent",
                "open_storyline.config",
                "open_storyline.mcp",
                "open_storyline.nodes",
                "open_storyline.skills",
                "open_storyline.storage",
                "open_storyline.utils",
                "langchain",
            )
            original_import = builtins.__import__

            def guarded_import(name, *args, **kwargs):
                if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden):
                    raise AssertionError(f"remote import crossed local boundary: {name}")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = guarded_import
            import mvp_fastapi
            import open_storyline.mvp.pipeline

            loaded = tuple(sys.modules)
            unexpected = [
                name for name in loaded
                if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden)
            ]
            if unexpected:
                raise AssertionError(unexpected)
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_mvp_settings_ignore_local_only_config_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            config.write_text(
                textwrap.dedent(
                    """
                    [project]
                    media_dir = "./unused-media"
                    bgm_dir = "./unused-bgm"
                    outputs_dir = "./outputs"

                    [llm]
                    model = "unused"
                    base_url = "unused"
                    api_key = "unused"

                    [local_mcp_server]
                    port = 8001

                    [ninerouter]
                    model = "cx/gpt-5.6-sol"
                    """
                ),
                encoding="utf-8",
            )

            settings = load_mvp_settings(config)

        self.assertEqual(settings.project.outputs_dir, root / "outputs")
        self.assertEqual(settings.ninerouter.model, "cx/gpt-5.6-sol")
        self.assertFalse(hasattr(settings, "llm"))
        self.assertFalse(hasattr(settings, "local_mcp_server"))

    def test_remote_dockerfile_copies_only_mvp_python_scope(self):
        dockerfile = (ROOT / "Dockerfile.remote").read_text(encoding="utf-8")
        self.assertNotIn("COPY src/ ./src/", dockerfile)
        self.assertIn("COPY src/open_storyline/mvp/ ./src/open_storyline/mvp/", dockerfile)
        self.assertNotIn("src/open_storyline/agent.py", dockerfile)
        self.assertNotIn("src/open_storyline/config.py", dockerfile)
        self.assertNotIn("src/open_storyline/mcp/", dockerfile)
        self.assertNotIn("src/open_storyline/nodes/", dockerfile)
        self.assertNotIn("src/open_storyline/utils/", dockerfile)
