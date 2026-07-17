from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RemoteProfileTests(unittest.TestCase):
    def test_requirements_exclude_local_inference_packages(self):
        requirements = (ROOT / "requirements-remote.txt").read_text(encoding="utf-8").lower()
        forbidden = [
            "torch",
            "funasr",
            "transnet",
            "sentence-transformers",
            "faiss",
            "transformers",
            "langchain-huggingface",
        ]
        for package in forbidden:
            with self.subTest(package=package):
                self.assertNotIn(package, requirements)

    def test_remote_container_never_downloads_model_archive(self):
        dockerfile = (ROOT / "Dockerfile.remote").read_text(encoding="utf-8")
        self.assertIn("requirements-remote.txt", dockerfile)
        self.assertNotIn("download.sh", dockerfile)
        self.assertNotIn("requirements.txt\n", dockerfile)

    def test_remote_build_context_is_an_explicit_allowlist(self):
        dockerignore = (
            (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        )
        self.assertEqual(dockerignore[0], "**")
        self.assertEqual(
            set(dockerignore[1:]),
            {
                "!.dockerignore",
                "!Dockerfile.remote",
                "!requirements-remote.txt",
                "!config.toml",
                "!mvp_fastapi.py",
                "!src/",
                "!src/**",
                "!web/",
                "!web/mvp.html",
            },
        )
        self.assertFalse(
            any(".env" in item or ".kamal" in item for item in dockerignore[1:])
        )

    def test_remote_container_has_public_health_checks_and_cpu_runtime(self):
        dockerfile = (ROOT / "Dockerfile.remote").read_text(encoding="utf-8")
        app = (ROOT / "mvp_fastapi.py").read_text(encoding="utf-8")
        deploy = (ROOT / "config" / "deploy.yml").read_text(encoding="utf-8")

        self.assertIn("EXPOSE 8000", dockerfile)
        self.assertIn("http://127.0.0.1:8000/health", dockerfile)
        self.assertIn('path: /up', deploy)
        self.assertRegex(
            app,
            re.compile(
                r'@app\.get\("/health"\).*?"inference": "remote-only"',
                re.DOTALL,
            ),
        )
        self.assertRegex(
            app,
            re.compile(r'@app\.get\("/up".*?"status": "ok"', re.DOTALL),
        )
        self.assertIn('"renderer": "ffmpeg-cpu"', app)

    def test_kamal_keeps_provider_secrets_out_of_clear_environment(self):
        deploy = (ROOT / "config" / "deploy.yml").read_text(encoding="utf-8")
        secrets = (ROOT / ".kamal" / "secrets.example").read_text(encoding="utf-8")

        self.assertIn(
            "  secret:\n    - OPENSTORYLINE_WEB_TOKEN\n    - NINEROUTER_KEY",
            deploy,
        )
        self.assertNotRegex(deploy, r"(?m)^\s+NINEROUTER_KEY:")
        self.assertNotRegex(deploy, r"(?m)^\s+OPENSTORYLINE_WEB_TOKEN:")
        self.assertEqual(
            secrets.splitlines()[1:],
            [
                "OPENSTORYLINE_WEB_TOKEN=$OPENSTORYLINE_WEB_TOKEN",
                "NINEROUTER_KEY=$NINEROUTER_KEY",
            ],
        )

    def test_release_commands_require_the_strict_live_ninerouter_gate(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        release_scan = wrapper.index('for arg in "$@"')
        gate_call = wrapper.index('run_ninerouter_release_gate "$release_command"')
        kamal_exec = wrapper.index('exec kamal "_${KAMAL_VERSION}_"')

        self.assertLess(release_scan, gate_call)
        self.assertLess(gate_call, kamal_exec)
        self.assertIn("setup|deploy|redeploy", wrapper)
        self.assertIn('for arg in "$@"', wrapper)
        self.assertIn('run_ninerouter_release_gate "$release_command"', wrapper)
        self.assertIn("NINEROUTER_QA_STT_AUDIO", wrapper)
        self.assertIn("--strict-models", wrapper)
        self.assertIn("--live-inference", wrapper)
        self.assertIn("--stt-audio", wrapper)
        self.assertIn('OPENSTORYLINE_LLM_MODEL "cx/gpt-5.6-sol"', wrapper)
        self.assertIn('OPENSTORYLINE_IMAGE_MODELS "cx/gpt-5.5-image"', wrapper)
        self.assertIn('OPENSTORYLINE_STT_MODELS "mistral/voxtral-mini-2602"', wrapper)

if __name__ == "__main__":
    unittest.main()
