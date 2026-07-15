from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def render_sample(*, domain: str = "") -> str:
    text = (ROOT / "config" / "deploy.yml").read_text(encoding="utf-8")
    values = {
        "KAMAL_HOST": "203.0.113.10",
        "KAMAL_DOMAIN": domain,
        "NINEROUTER_URL": "https://router.example.test",
    }

    conditional = re.compile(
        r'<% unless ENV\.fetch\("KAMAL_DOMAIN", ""\)\.strip\.empty\? %>\n(.*?)<% end %>\n?',
        re.DOTALL,
    )
    match = conditional.search(text)
    assert match is not None
    text = conditional.sub(match.group(1) if domain else "", text)

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
    def test_ip_mode_is_valid_yaml_without_host_or_ssl(self):
        config = yaml.safe_load(render_sample())
        self.assertEqual(config["servers"]["web"]["hosts"], ["203.0.113.10"])
        self.assertNotIn("host", config["proxy"])
        self.assertNotIn("ssl", config["proxy"])
        self.assertEqual(config["proxy"]["run"]["http_port"], 80)

    def test_domain_mode_enables_automatic_https(self):
        config = yaml.safe_load(render_sample(domain="video.example.test"))
        self.assertEqual(config["proxy"]["host"], "video.example.test")
        self.assertIs(config["proxy"]["ssl"], True)
        self.assertIs(config["proxy"]["forward_headers"], True)

    def test_deployment_uses_remote_only_image_and_secret_references(self):
        config = yaml.safe_load(render_sample())
        self.assertEqual(config["builder"]["dockerfile"], "Dockerfile.remote")
        self.assertEqual(config["registry"]["server"], "localhost:5555")
        self.assertEqual(
            config["env"]["secret"],
            ["OPENSTORYLINE_WEB_TOKEN", "NINEROUTER_KEY"],
        )
        secrets = (ROOT / ".kamal" / "secrets.example").read_text(encoding="utf-8")
        self.assertIn("$OPENSTORYLINE_WEB_TOKEN", secrets)
        self.assertNotIn("replace-with", secrets)


if __name__ == "__main__":
    unittest.main()
