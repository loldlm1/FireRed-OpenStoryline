from pathlib import Path
import unittest

import yaml


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

    def test_compose_profile_parses(self):
        compose = yaml.safe_load((ROOT / "docker-compose.mvp.yml").read_text(encoding="utf-8"))
        service = compose["services"]["openstoryline-mvp"]
        self.assertEqual(service["build"]["dockerfile"], "Dockerfile.remote")
        self.assertEqual(service["env_file"], ".env.mvp")


if __name__ == "__main__":
    unittest.main()
