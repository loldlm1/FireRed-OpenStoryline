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
                "!Dockerfile.quality",
                "!requirements-remote.txt",
                "!requirements-quality.txt",
                "!config.toml",
                "!mvp_fastapi.py",
                "!alembic.ini",
                "!scripts/",
                "!scripts/quality_metrics.py",
                "!migrations/",
                "!migrations/**",
                "!src/",
                "!src/**",
                "!web/",
                "!web/mvp.html",
                "!web/mvp-legacy.html",
                "!web/static/",
                "!web/static/mvp/",
                "!web/static/mvp/**",
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
        self.assertIn("http://127.0.0.1:8000/up", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn('"--no-proxy-headers"', dockerfile)
        self.assertNotIn('"--proxy-headers"', dockerfile)
        self.assertIn('path: /up', deploy)
        self.assertIn('proxy: false', deploy)
        self.assertIn('publish: "<%= ENV.fetch("KAMAL_HTTP_PORT", "80") %>:8000"', deploy)
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

    def test_remote_container_packages_only_the_scoped_workspace_assets(self):
        dockerfile = (ROOT / "Dockerfile.remote").read_text(encoding="utf-8")

        self.assertIn("COPY web/mvp.html web/mvp-legacy.html ./web/", dockerfile)
        self.assertIn("COPY web/static/mvp/ ./web/static/mvp/", dockerfile)
        self.assertNotIn("COPY web/ ./web/", dockerfile)
        for module in (
            "activity.js",
            "api.js",
            "app.js",
            "messages.js",
            "styles.css",
            "upload.js",
            "views.js",
        ):
            with self.subTest(module=module):
                self.assertTrue((ROOT / "web" / "static" / "mvp" / module).is_file())

    def test_domain_proxy_disables_response_buffering_for_sse(self):
        deploy = (ROOT / "config" / "deploy.yml").read_text(encoding="utf-8")

        self.assertIn("response_timeout: 3600", deploy)
        self.assertRegex(
            deploy,
            re.compile(
                r"buffering:\s+requests: true\s+responses: false",
                re.MULTILINE,
            ),
        )
        self.assertIn(
            'max_request_body: <%= ENV.fetch("OPENSTORYLINE_MAX_UPLOAD_BYTES", "8589934592") %>',
            deploy,
        )

    def test_remote_image_contains_database_migrations_without_init_secrets(self):
        dockerfile = (ROOT / "Dockerfile.remote").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements-remote.txt").read_text(encoding="utf-8")

        self.assertIn("COPY migrations/ ./migrations/", dockerfile)
        self.assertIn("COPY config.toml mvp_fastapi.py alembic.ini ./", dockerfile)
        self.assertNotIn("mvp-postgres-init.sh", dockerfile)
        for dependency in ("SQLAlchemy", "psycopg[binary]", "alembic", "argon2-cffi"):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, requirements)

    def test_kamal_keeps_provider_secrets_out_of_clear_environment(self):
        deploy = (ROOT / "config" / "deploy.yml").read_text(encoding="utf-8")
        secrets = (ROOT / ".kamal" / "secrets.example").read_text(encoding="utf-8")

        self.assertIn(
            "  secret:\n    - DATABASE_URL\n    - OPENSTORYLINE_WEB_PASSWORD_HASH\n    - OPENSTORYLINE_SECURITY_PEPPER\n    - NINEROUTER_KEY\n    - MISTRAL_API_KEYS\n    - PEXELS_API_KEY",
            deploy,
        )
        self.assertNotRegex(deploy, r"(?m)^\s+NINEROUTER_KEY:")
        self.assertNotRegex(deploy, r"(?m)^\s+MISTRAL_API_KEYS:")
        self.assertNotRegex(deploy, r"(?m)^\s+PEXELS_API_KEY:")
        self.assertNotRegex(deploy, r"(?m)^\s+OPENSTORYLINE_WEB_PASSWORD_HASH:")
        self.assertNotRegex(deploy, r"(?m)^\s+OPENSTORYLINE_SECURITY_PEPPER:")
        self.assertEqual(
            secrets.splitlines()[1:],
            [
                "DATABASE_URL=$DATABASE_URL",
                "OPENSTORYLINE_DATABASE_PASSWORD=$OPENSTORYLINE_DATABASE_PASSWORD",
                "POSTGRES_PASSWORD=$POSTGRES_PASSWORD",
                "OPENSTORYLINE_WEB_PASSWORD_HASH=$OPENSTORYLINE_WEB_PASSWORD_HASH",
                "OPENSTORYLINE_SECURITY_PEPPER=$OPENSTORYLINE_SECURITY_PEPPER",
                "NINEROUTER_KEY=$NINEROUTER_KEY",
                "MISTRAL_API_KEYS=$MISTRAL_API_KEYS",
                "PEXELS_API_KEY=$PEXELS_API_KEY",
            ],
        )

    def test_release_commands_require_both_strict_live_provider_gates(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        release_scan = wrapper.index('for arg in "$@"')
        ninerouter_gate = wrapper.index('run_ninerouter_release_gate "$release_command"')
        mistral_gate = wrapper.index('run_mistral_release_gate "$release_command"')
        kamal_exec = wrapper.rindex('exec kamal "_${KAMAL_CLI_VERSION}_"')

        self.assertLess(release_scan, ninerouter_gate)
        self.assertLess(ninerouter_gate, mistral_gate)
        self.assertLess(mistral_gate, kamal_exec)
        self.assertIn("setup|deploy|redeploy", wrapper)
        self.assertIn('for arg in "$@"', wrapper)
        self.assertIn('run_ninerouter_release_gate "$release_command"', wrapper)
        self.assertIn('run_mistral_release_gate "$release_command"', wrapper)
        self.assertIn("MISTRAL_QA_STT_AUDIO", wrapper)
        self.assertIn("qa_mistral_stt.py", wrapper)
        self.assertIn("--each-key", wrapper)
        self.assertIn("--strict-models", wrapper)
        self.assertIn("--live-inference", wrapper)
        self.assertIn('OPENSTORYLINE_LLM_MODEL "cx/gpt-5.6-sol"', wrapper)
        self.assertIn('OPENSTORYLINE_IMAGE_MODELS "cx/gpt-5.5-image"', wrapper)
        self.assertNotIn("OPENSTORYLINE_STT_MODELS", wrapper)

    def test_direct_port_predeploy_stops_only_the_current_mvp_container(self):
        hook = (ROOT / ".kamal" / "hooks" / "pre-deploy").read_text(
            encoding="utf-8"
        )
        self.assertIn('[[ -n "${KAMAL_DOMAIN:-}" ]] && exit 0', hook)
        self.assertIn("label=service=openstoryline-mvp", hook)
        self.assertIn("label=role=web", hook)
        self.assertIn("docker stop -t 30", hook)
        self.assertIn('OPENSTORYLINE_APP_VERSION="${KAMAL_VERSION:', hook)
        self.assertLess(hook.index("db migrate"), hook.index("docker stop -t 30"))
        self.assertIn('"${KAMAL_COMMAND:-}" != "rollback"', hook)
        self.assertNotIn("20128", hook)
        self.assertNotIn("9router", hook.lower())

if __name__ == "__main__":
    unittest.main()
