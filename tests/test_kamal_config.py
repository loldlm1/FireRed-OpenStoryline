from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


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
        self.assertIn('kamal "_${KAMAL_VERSION}_"', wrapper)
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
            ],
        )
        self.assertEqual(
            config["env"]["clear"]["OPENSTORYLINE_IMAGE_MODELS"],
            "cx/gpt-5.5-image",
        )
        self.assertNotIn("OPENSTORYLINE_STT_MODELS", config["env"]["clear"])
        self.assertEqual(config["env"]["clear"]["MISTRAL_STT_TIMEOUT"], 180)
        self.assertEqual(config["env"]["clear"]["OPENSTORYLINE_IMAGE_SIZE"], "1024x1024")
        secrets = (ROOT / ".kamal" / "secrets.example").read_text(encoding="utf-8")
        kamal_env = (ROOT / ".env.kamal.example").read_text(encoding="utf-8")
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
        self.assertNotIn("replace-with", secrets)
        self.assertIn("MISTRAL_QA_STT_AUDIO=", kamal_env)

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
        self.assertIn("migrate|current|backup|restore-check", wrapper)

    def test_password_hash_command_is_local_and_precedes_env_loading(self):
        wrapper = (ROOT / "bin" / "kamal-mvp").read_text(encoding="utf-8")
        dispatch = wrapper.index('"${1:-}" == "auth"')
        env_loading = wrapper.index('if [[ -f "$ENV_FILE" ]]')

        self.assertLess(dispatch, env_loading)
        self.assertIn("open_storyline.mvp.auth hash-password", wrapper)


if __name__ == "__main__":
    unittest.main()
