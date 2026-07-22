from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def validate_rollout_flags(**overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "KAMAL_ENV_FILE": str(ROOT / ".missing-rollout-test-env"),
        "OPENSTORYLINE_POSTGRES_ADMIN_MODE": "local",
        "OPENSTORYLINE_STRUCTURED_OUTPUT_MODE": "json_object",
        "OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES": "",
        "OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED": "false",
        "OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE": "off",
        "OPENSTORYLINE_AGENTIC_EDITING_MODE": "off",
        "OPENSTORYLINE_SEMANTIC_QA_ENABLED": "false",
        "OPENSTORYLINE_FFMPEGA_ENABLED": "false",
        "OPENSTORYLINE_DELIVERY_POLICY": "qa_enforced",
        "OPENSTORYLINE_RETRY_UX_ENABLED": "false",
        "OPENSTORYLINE_RENDER_PROMOTION_MODE": "report",
        "OPENSTORYLINE_CREATIVE_QA_ENABLED": "true",
        "OPENSTORYLINE_CREATIVE_QA_STRICT": "true",
        **overrides,
    }
    return subprocess.run(
        [str(ROOT / "bin" / "kamal-mvp"), "rollout", "validate"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def render_sample(*, domain: str = "", http_port: str = "80") -> str:
    text = (ROOT / "config" / "deploy.yml").read_text(encoding="utf-8")
    values = {
        "KAMAL_HOST": "203.0.113.10",
        "KAMAL_DOMAIN": domain,
        "KAMAL_HTTP_PORT": http_port,
        "NINEROUTER_URL": "https://router.example.test",
        "OPENSTORYLINE_PUBLIC_ORIGIN": (
            f"https://{domain}" if domain else "http://203.0.113.10"
        ),
    }

    conditional = re.compile(
        r'<% unless ENV\.fetch\("KAMAL_DOMAIN", ""\)\.strip\.empty\? %>\n(.*?)<% end %>\n?',
        re.DOTALL,
    )
    match = conditional.search(text)
    assert match is not None
    text = conditional.sub(lambda item: item.group(1) if domain else "", text)

    ip_conditional = re.compile(
        r'<% if ENV\.fetch\("KAMAL_DOMAIN", ""\)\.strip\.empty\? %>\n(.*?)<% end %>\n?',
        re.DOTALL,
    )
    text = ip_conditional.sub(
        lambda item: "" if domain else item.group(1),
        text,
    )

    expression = re.compile(
        r'<%= ENV\.fetch\("([A-Z0-9_]+)"(?:, "([^"]*)")?\) %>'
    )

    def replace(match: re.Match[str]) -> str:
        name, default = match.groups()
        if name in values:
            return values[name]
        if default is not None:
            return default
        raise AssertionError(f"sample value missing for {name}")

    rendered = expression.sub(replace, text)
    if "<%" in rendered:
        raise AssertionError("unrendered ERB remains")
    return rendered


class KamalConfigTests(unittest.TestCase):
    def test_wrapper_pins_supported_kamal_without_auto_install(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        self.assertIn('MINIMUM_KAMAL_VERSION="2.12.0"', wrapper)
        self.assertIn('KAMAL_CLI_VERSION="${KAMAL_CLI_VERSION:-2.12.0}"', wrapper)
        self.assertIn('kamal "_${KAMAL_CLI_VERSION}_"', wrapper)
        self.assertNotIn("gem install kamal --no-document", wrapper)

    def test_ip_mode_is_valid_yaml_without_host_or_ssl(self):
        config = yaml.safe_load(render_sample())
        self.assertEqual(config["secrets_path"], ".kamal/secrets")
        self.assertEqual(config["servers"]["web"]["hosts"], ["203.0.113.10"])
        self.assertNotIn("host", config["proxy"])
        self.assertNotIn("ssl", config["proxy"])
        self.assertEqual(config["proxy"]["run"]["http_port"], 80)
        self.assertIs(config["servers"]["web"]["proxy"], False)
        self.assertEqual(config["servers"]["web"]["options"]["publish"], "80:8000")

    def test_domain_mode_enables_automatic_https(self):
        config = yaml.safe_load(render_sample(domain="video.example.test"))
        self.assertEqual(config["proxy"]["host"], "video.example.test")
        self.assertIs(config["proxy"]["ssl"], True)
        self.assertIs(config["proxy"]["forward_headers"], True)
        self.assertIs(config["proxy"]["buffering"]["requests"], True)
        self.assertIs(config["proxy"]["buffering"]["responses"], False)
        self.assertEqual(config["proxy"]["response_timeout"], 3600)
        self.assertNotIn("proxy", config["servers"]["web"])
        self.assertNotIn("publish", config["servers"]["web"]["options"])

    def test_ip_mode_publishes_the_configured_custom_port(self):
        config = yaml.safe_load(render_sample(http_port="20129"))
        self.assertIs(config["servers"]["web"]["proxy"], False)
        self.assertEqual(
            config["servers"]["web"]["options"]["publish"],
            "20129:8000",
        )

    def test_deployment_uses_remote_only_image_and_secret_references(self):
        config = yaml.safe_load(render_sample())
        self.assertEqual(config["builder"]["dockerfile"], "Dockerfile.remote")
        self.assertEqual(config["registry"]["server"], "localhost:5555")
        self.assertEqual(
            config["env"]["secret"],
            [
                "DATABASE_URL",
                "OPENSTORYLINE_WEB_PASSWORD_HASH",
                "OPENSTORYLINE_SECURITY_PEPPER",
                "NINEROUTER_KEY",
                "MISTRAL_API_KEYS",
                "PEXELS_API_KEY",
            ],
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_IMAGE_MODELS"],
            "cx/gpt-5.5-image",
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_STRUCTURED_OUTPUT_MODE"],
            "json_object",
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES"],
            "",
        )
        self.assertIs(
            config["env"]["clear"][
                "OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED"
            ],
            False,
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE"],
            "off",
        )
        self.assertNotIn("OPENSTORYLINE_STT_MODELS", config["env"]["clear"])
        self.assertEqual(config["env"]["clear"]["MISTRAL_STT_TIMEOUT"], 180)
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_AUDIT_MAX_DOCUMENT_BYTES"],
            2097152,
        )
        self.assertIs(config["env"]["clear"]["OPENSTORYLINE_RETENTION_ENABLED"], False)
        self.assertEqual(config["env"]["clear"]["OPENSTORYLINE_MEDIA_RETENTION_DAYS"], 7)
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS"],
            24,
        )
        self.assertEqual(config["env"]["clear"]["OPENSTORYLINE_AUDIT_RETENTION_DAYS"], 30)
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_RETENTION_INTERVAL_SECONDS"],
            86400,
        )
        self.assertEqual(config["env"]["clear"]["OPENSTORYLINE_RETENTION_BATCH_SIZE"], 100)
        self.assertEqual(config["env"]["clear"]["OPENSTORYLINE_IMAGE_SIZE"], "1024x1024")
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_AGENTIC_EDITING_MODE"],
            "off",
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_SESSION_WORKSPACE_MODE"],
            "legacy",
        )
        self.assertIs(
            config["env"]["clear"]["OPENSTORYLINE_GENERATED_ASSETS_ENABLED"],
            False,
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_MAX_GENERATED_ASSETS_PER_CLIP"],
            2,
        )
        self.assertIs(config["env"]["clear"]["OPENSTORYLINE_PEXELS_ENABLED"], False)
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_MAX_STOCK_ASSETS_PER_CLIP"],
            2,
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_PEXELS_LICENSE_REVIEWED_AT"],
            "",
        )
        self.assertEqual(config["env"]["clear"]["OPENSTORYLINE_PEXELS_SEARCH_LIMIT"], 8)
        self.assertEqual(config["env"]["clear"]["OPENSTORYLINE_PEXELS_MAX_BYTES"], 83886080)
        self.assertIs(config["env"]["clear"]["OPENSTORYLINE_CREATIVE_QA_ENABLED"], True)
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_CREATIVE_CATALOG_PATH"],
            "/app/creative_catalog/manifest.json",
        )
        self.assertIs(
            config["env"]["clear"]["OPENSTORYLINE_CREATIVE_CATALOG_PLANNING_ENABLED"],
            False,
        )
        self.assertIs(config["env"]["clear"]["OPENSTORYLINE_CREATIVE_QA_STRICT"], True)
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_RENDER_PROMOTION_MODE"],
            "report",
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_COMPLETION_POLICY"],
            "strict",
        )
        self.assertIs(
            config["env"]["clear"]["OPENSTORYLINE_LIMITED_OUTPUT_PROMOTION_ENABLED"],
            False,
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_DELIVERY_POLICY"],
            "qa_enforced",
        )
        self.assertIs(
            config["env"]["clear"]["OPENSTORYLINE_RETRY_UX_ENABLED"],
            False,
        )
        self.assertIs(config["env"]["clear"]["OPENSTORYLINE_SEMANTIC_QA_ENABLED"], False)
        self.assertEqual(config["env"]["clear"]["OPENSTORYLINE_SEMANTIC_QA_MAX_FRAMES"], 4)
        self.assertEqual(
            config["env"]["clear"]["FFMPEGA_URL"],
            "http://openstoryline-mvp-ffmpega:8188",
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_RENDER_QUALITY_PROFILE"],
            "high",
        )
        self.assertEqual(config["env"]["clear"]["OPENSTORYLINE_RENDER_FPS_CAP"], 60)
        secrets = (ROOT / ".kamal" / "secrets.example").read_text(encoding="utf-8")
        kamal_env = (ROOT / ".env.kamal.example").read_text(encoding="utf-8")
        local_env = (ROOT / ".env.mvp.example").read_text(encoding="utf-8")
        self.assertIn(
            "OPENSTORYLINE_WEB_PASSWORD_HASH=$OPENSTORYLINE_WEB_PASSWORD_HASH",
            secrets,
        )
        self.assertIn(
            "OPENSTORYLINE_SECURITY_PEPPER=$OPENSTORYLINE_SECURITY_PEPPER",
            secrets,
        )
        self.assertIn("DATABASE_URL=$DATABASE_URL", secrets)
        self.assertIn(
            "OPENSTORYLINE_DATABASE_PASSWORD=$OPENSTORYLINE_DATABASE_PASSWORD",
            secrets,
        )
        self.assertIn("POSTGRES_PASSWORD=$POSTGRES_PASSWORD", secrets)
        self.assertIn("MISTRAL_API_KEYS=$MISTRAL_API_KEYS", secrets)
        self.assertIn("PEXELS_API_KEY=$PEXELS_API_KEY", secrets)
        self.assertNotIn("replace-with", secrets)
        self.assertIn("MISTRAL_QA_STT_AUDIO=", kamal_env)
        self.assertIn("OPENSTORYLINE_SESSION_WORKSPACE_MODE=legacy", kamal_env)
        self.assertIn("OPENSTORYLINE_SESSION_WORKSPACE_MODE=legacy", local_env)
        self.assertIn("OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_object", kamal_env)
        self.assertIn("OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_object", local_env)
        self.assertIn("OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES=", kamal_env)
        self.assertIn("OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off", kamal_env)
        self.assertIn("OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off", local_env)
        self.assertIn(
            "OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED=false",
            kamal_env,
        )
        self.assertIn("OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS=24", kamal_env)
        self.assertIn("OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS=24", local_env)
        self.assertIn("OPENSTORYLINE_PEXELS_ENABLED=false", kamal_env)
        self.assertIn("OPENSTORYLINE_PEXELS_LICENSE_REVIEWED_AT=", kamal_env)
        self.assertIn("OPENSTORYLINE_RENDER_QUALITY_PROFILE=high", kamal_env)
        self.assertIn("OPENSTORYLINE_RENDER_QUALITY_PROFILE=high", local_env)
        self.assertIn("OPENSTORYLINE_RENDER_PROMOTION_MODE=report", kamal_env)
        self.assertIn("OPENSTORYLINE_RENDER_PROMOTION_MODE=report", local_env)
        self.assertIn("OPENSTORYLINE_COMPLETION_POLICY=strict", kamal_env)
        self.assertIn("OPENSTORYLINE_COMPLETION_POLICY=strict", local_env)
        self.assertIn(
            "OPENSTORYLINE_LIMITED_OUTPUT_PROMOTION_ENABLED=false",
            kamal_env,
        )
        self.assertIn("OPENSTORYLINE_DELIVERY_POLICY=qa_enforced", kamal_env)
        self.assertIn("OPENSTORYLINE_DELIVERY_POLICY=qa_enforced", local_env)
        self.assertIn("OPENSTORYLINE_RETRY_UX_ENABLED=false", kamal_env)
        self.assertIn(
            "OPENSTORYLINE_CREATIVE_CATALOG_PATH=/app/creative_catalog/manifest.json",
            kamal_env,
        )
        self.assertIn(
            "OPENSTORYLINE_CREATIVE_CATALOG_PATH=./creative_catalog/manifest.json",
            local_env,
        )
        self.assertIn(
            "OPENSTORYLINE_CREATIVE_CATALOG_PLANNING_ENABLED=false",
            kamal_env,
        )
        self.assertIn(
            "OPENSTORYLINE_CREATIVE_CATALOG_PLANNING_ENABLED=false",
            local_env,
        )

    def test_remote_image_installs_and_validates_the_offline_catalog(self):
        dockerfile = (ROOT / "Dockerfile.remote").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn("apt-get install -y --no-install-recommends ffmpeg curl fontconfig", dockerfile)
        self.assertIn("COPY creative_catalog/ ./creative_catalog/", dockerfile)
        self.assertIn("fc-cache -f", dockerfile)
        self.assertIn("generate_creative_catalog_manifest.py --check", dockerfile)
        self.assertIn("python -m open_storyline.mvp.catalog", dockerfile)
        self.assertIn("!creative_catalog/**", dockerignore)

    def test_pexels_release_gate_is_conditional_and_offline(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        release_scan = wrapper.index('for arg in "$@"')
        pexels_gate = wrapper.index('validate_pexels_release_config "$release_command"')
        ninerouter_gate = wrapper.index('run_ninerouter_release_gate "$release_command"')

        self.assertLess(release_scan, pexels_gate)
        self.assertLess(pexels_gate, ninerouter_gate)
        self.assertIn("require_value PEXELS_API_KEY", wrapper)
        self.assertIn("require_value OPENSTORYLINE_PEXELS_LICENSE_REVIEWED_AT", wrapper)
        self.assertIn("PexelsClient.from_config", wrapper)
        self.assertNotIn("api.pexels.com/v1/search", wrapper)

    def test_ffmpega_release_gate_requires_the_private_pinned_service(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        service = (ROOT / "scripts" / "mvp-ffmpega-service.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('require_exact_value FFMPEGA_URL "http://openstoryline-mvp-ffmpega:8188"', wrapper)
        self.assertIn('run_ffmpega_release_gate "$release_command"', wrapper)
        self.assertIn('"${1:-}" == "ffmpega"', wrapper)
        self.assertIn("0cfe2db05df104f95c98cc45e11f129fa5ef5193", service)
        self.assertIn("--security-opt no-new-privileges", service)
        self.assertIn("--cap-drop ALL", service)
        self.assertIn("--read-only", service)
        self.assertNotIn("--publish", service)

    def test_postgres_accessory_is_private_persistent_and_health_checked(self):
        config = yaml.safe_load(render_sample())
        database = config["accessories"]["db"]

        self.assertRegex(database["image"], r"^postgres:17-bookworm@sha256:[a-f0-9]{64}$")
        self.assertEqual(database["host"], "203.0.113.10")
        self.assertEqual(database["network"], "kamal")
        self.assertNotIn("port", database)
        self.assertEqual(database["env"]["clear"]["POSTGRES_USER"], "postgres")
        self.assertEqual(
            database["env"]["secret"],
            ["OPENSTORYLINE_DATABASE_PASSWORD", "POSTGRES_PASSWORD"],
        )
        self.assertEqual(database["options"]["restart"], "unless-stopped")
        self.assertIn("pg_isready", database["options"]["health-cmd"])
        remotes = {item["remote"] for item in database["directories"]}
        self.assertEqual(remotes, {"/var/lib/postgresql/data", "/backups"})
        self.assertEqual(
            database["files"][0]["remote"],
            "/docker-entrypoint-initdb.d/10-openstoryline-app.sh",
        )

    def test_database_commands_bypass_provider_release_gates(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        dispatch = wrapper.index('if [[ "${1:-}" == "db" ]]')
        provider_requirements = wrapper.index("require_value NINEROUTER_URL")
        release_scan = wrapper.index('for arg in "$@"')

        self.assertLess(dispatch, provider_requirements)
        self.assertLess(dispatch, release_scan)
        self.assertIn("migrate|current|readiness|backup|restore-check", wrapper)
        self.assertIn("docker run --rm --network kamal", wrapper)
        self.assertNotIn("app exec --primary alembic", wrapper)

    def test_release_hooks_prepare_non_root_outputs_and_check_readiness(self):
        pre_deploy = (ROOT / ".kamal" / "hooks" / "pre-deploy").read_text(
            encoding="utf-8"
        )
        post_deploy = (ROOT / ".kamal" / "hooks" / "post-deploy").read_text(
            encoding="utf-8"
        )

        self.assertIn("APP_UID=65532", pre_deploy)
        self.assertIn("APP_GID=65532", pre_deploy)
        self.assertIn('as_root install -d -m 0750 -o "$app_uid" -g "$app_gid"', pre_deploy)
        self.assertIn('as_root find "$resolved_outputs" -xdev', pre_deploy)
        self.assertIn('readlink -m -- "$outputs_dir"', pre_deploy)
        self.assertIn('paths_overlap "$outputs_dir" "$postgres_data_dir"', pre_deploy)
        self.assertIn('paths_overlap "$outputs_dir" "$postgres_backup_dir"', pre_deploy)
        self.assertLess(
            post_deploy.index('check_endpoint "/up"'),
            post_deploy.index('check_endpoint "/health"'),
        )
        self.assertIn("for attempt in 1 2 3 4 5 6 7 8 9 10", post_deploy)

    def test_pre_deploy_rejects_outputs_overlapping_postgres_storage(self):
        hook = ROOT / ".kamal" / "hooks" / "pre-deploy"
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "kamal.env"
            env_file.write_text(
                "KAMAL_HOSTS=example.test\n"
                "KAMAL_DOMAIN=example.test\n"
                "KAMAL_OUTPUTS_DIR=/var/lib/openstoryline/postgres/jobs\n"
                "KAMAL_POSTGRES_DATA_DIR=/var/lib/openstoryline/postgres\n"
                "KAMAL_POSTGRES_BACKUP_DIR=/var/lib/openstoryline/backups\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [str(hook)],
                env={
                    **os.environ,
                    "KAMAL_ENV_FILE": str(env_file),
                    "KAMAL_COMMAND": "rollback",
                },
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a safe dedicated absolute path", result.stderr)

    def test_rollback_requires_target_image_database_readiness(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        rollback_gate = wrapper.index('if [[ "${1:-}" == "rollback" ]]')
        release_scan = wrapper.index('for arg in "$@"')

        self.assertLess(rollback_gate, release_scan)
        self.assertIn("explicit target version", wrapper)
        self.assertIn("run_remote_database_command readiness", wrapper)
        self.assertIn("result = asyncio.run(database.readiness())", wrapper)

    def test_password_hash_command_is_local_and_precedes_env_loading(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        dispatch = wrapper.index('"${1:-}" == "auth"')
        env_loading = wrapper.index('if [[ -f "$ENV_FILE" ]]')

        self.assertLess(dispatch, env_loading)
        self.assertIn("open_storyline.mvp.auth hash-password", wrapper)

    def test_admin_commands_use_the_primary_container_without_provider_gates(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        dispatch = wrapper.index(
            'if [[ "${1:-}" == "audit" || "${1:-}" == "retention" || "${1:-}" == "workspace" ]]'
        )
        provider_requirements = wrapper.index("require_value NINEROUTER_URL")
        release_scan = wrapper.index('for arg in "$@"')

        self.assertLess(dispatch, provider_requirements)
        self.assertLess(dispatch, release_scan)
        self.assertIn("app exec --primary --reuse", wrapper)
        self.assertIn('open_storyline.mvp.admin "$admin_command"', wrapper)
        self.assertIn('"${1:-}" == "workspace"', wrapper)

    def test_delivery_policy_is_validated_before_release_commands(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        validation = wrapper.index('OPENSTORYLINE_DELIVERY_POLICY:-qa_enforced')
        release_scan = wrapper.index('for arg in "$@"')

        self.assertLess(validation, release_scan)
        self.assertIn("qa_enforced|technical_pass_guaranteed", wrapper)

    def test_agentic_rollout_validator_accepts_defaults_and_complete_rollout(self):
        default = validate_rollout_flags()
        complete = validate_rollout_flags(
            OPENSTORYLINE_STRUCTURED_OUTPUT_MODE="json_schema",
            OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED="true",
            OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES=(
                "shorts_selection.v1,visual_understanding.v1,edit_plan.v1,"
                "edit_plan_repair.v1,semantic_qa.v1,"
                "ffmpega_agentic_finishing.v1,ffmpega_deterministic_effects.v1"
            ),
            OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE="enforce",
            OPENSTORYLINE_AGENTIC_EDITING_MODE="render",
            OPENSTORYLINE_SEMANTIC_QA_ENABLED="true",
            OPENSTORYLINE_FFMPEGA_ENABLED="true",
            OPENSTORYLINE_DELIVERY_POLICY="technical_pass_guaranteed",
            OPENSTORYLINE_RETRY_UX_ENABLED="true",
            OPENSTORYLINE_RENDER_PROMOTION_MODE="enforce",
        )

        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(complete.returncode, 0, complete.stderr)
        self.assertIn("internally consistent", complete.stdout)

    def test_agentic_rollout_validator_rejects_out_of_order_flags(self):
        cases = (
            (
                {"OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES": "shorts_selection.v1"},
                "require OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_schema",
            ),
            (
                {
                    "OPENSTORYLINE_STRUCTURED_OUTPUT_MODE": "json_schema",
                    "OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED": "true",
                    "OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES": (
                        "shorts_selection.v1,semantic_qa.v1"
                    ),
                },
                "requires the edit-plan strict schemas first",
            ),
            (
                {
                    "OPENSTORYLINE_STRUCTURED_OUTPUT_MODE": "json_schema",
                    "OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED": "true",
                    "OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES": (
                        "shorts_selection.v1,visual_understanding.v1,"
                        "edit_plan.v1,edit_plan_repair.v1"
                    ),
                    "OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE": "report",
                    "OPENSTORYLINE_AGENTIC_EDITING_MODE": "shadow",
                },
                "requires every strict-schema boundary first",
            ),
            (
                {
                    "OPENSTORYLINE_DELIVERY_POLICY": "technical_pass_guaranteed",
                },
                "requires strict schema and enforced repair first",
            ),
            (
                {"OPENSTORYLINE_RETRY_UX_ENABLED": "true"},
                "must be the final rollout flag",
            ),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                result = validate_rollout_flags(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
