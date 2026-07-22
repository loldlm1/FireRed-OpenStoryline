from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import unittest

from open_storyline.mvp.ffmpega_contracts import FFMPEGA_SOURCE_COMMIT
from open_storyline.mvp.ffmpega_service import (
    ServiceError,
    ServiceState,
    _dry_run_timeout,
    parse_prompt_request,
)


def workflow(source: Path, destination: Path, **overrides):
    inputs = {
        "prompt": "",
        "video_path": str(source),
        "llm_model": "none",
        "no_llm_mode": "manual",
        "quality_preset": "high",
        "seed": 0,
        "pipeline_json": json.dumps({
            "effects_mode": "skills",
            "pipeline": [
                {"skill": "vignette", "params": {}},
                {"skill": "saturation", "params": {"value": 1.2}},
            ],
            "raw_ffmpeg": "",
            "sam3": None,
        }),
        "advanced_options": True,
        "save_output": True,
        "output_path": str(destination),
        "use_vision": False,
        "verify_output": False,
        "allow_model_downloads": False,
    }
    inputs.update(overrides)
    return {"prompt": {"1": {"class_type": "FFMPEGAgent", "inputs": inputs}}}


class FFMPEGAServiceContractTests(unittest.TestCase):
    def test_dry_run_timeout_allows_production_resolution_preflight(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_dry_run_timeout(1800), 180)
        with patch.dict(
            os.environ,
            {"FFMPEGA_DRY_RUN_TIMEOUT_SECONDS": "240"},
            clear=True,
        ):
            self.assertEqual(_dry_run_timeout(1800), 240)
            self.assertEqual(_dry_run_timeout(120), 120)
        with patch.dict(
            os.environ,
            {"FFMPEGA_DRY_RUN_TIMEOUT_SECONDS": "10"},
            clear=True,
        ):
            with self.assertRaises(ServiceError) as caught:
                _dry_run_timeout(1800)
            self.assertEqual(caught.exception.code, "FFMPEGA_CONFIG_INVALID")

    def test_accepts_only_manual_model_free_shared_paths(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            source = root / "source.mp4"
            destination = root / "result.mp4"
            source.write_bytes(b"video")

            job = parse_prompt_request(
                workflow(source, destination),
                shared_root=root,
            )

            self.assertEqual(job.source, source)
            self.assertEqual(job.destination, destination)
            self.assertEqual(job.effects[0]["skill"], "vignette")
            self.assertEqual(job.effects[0]["params"]["intensity"], 0.3)

    def test_rejects_model_downloads_and_paths_outside_shared_root(self):
        with TemporaryDirectory() as tmpdir, TemporaryDirectory() as outside:
            root = Path(tmpdir).resolve()
            source = root / "source.mp4"
            source.write_bytes(b"video")
            destination = root / "result.mp4"

            with self.assertRaises(ServiceError) as downloads:
                parse_prompt_request(
                    workflow(source, destination, allow_model_downloads=True),
                    shared_root=root,
                )
            self.assertEqual(downloads.exception.code, "FFMPEGA_PARAMETER_BLOCKED")

            outside_destination = Path(outside).resolve() / "result.mp4"
            with self.assertRaises(ServiceError) as path:
                parse_prompt_request(
                    workflow(source, outside_destination),
                    shared_root=root,
                )
            self.assertEqual(path.exception.code, "FFMPEGA_PATH_NOT_SHARED")

    def test_queue_records_only_sanitized_completion_status(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            source = root / "source.mp4"
            destination = root / "result.mp4"
            source.write_bytes(b"video")
            state = ServiceState(root)

            def fake_render(job):
                job.destination.write_bytes(b"rendered")

            with patch(
                "open_storyline.mvp.ffmpega_service.render_with_upstream",
                side_effect=fake_render,
            ):
                prompt_id = state.submit(workflow(source, destination))
                state.queue.join()

            record = state.get_history(prompt_id)[prompt_id]
            self.assertTrue(record["status"]["completed"])
            self.assertEqual(record["status"]["status_str"], "success")
            self.assertNotIn(str(source), json.dumps(record))

    def test_dockerfile_pins_upstream_without_model_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile.ffmpega").read_text(encoding="utf-8")
        self.assertIn(FFMPEGA_SOURCE_COMMIT, dockerfile)
        self.assertIn("--no-checkout", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("FFMPEGA_DRY_RUN_TIMEOUT_SECONDS=180", dockerfile)
        self.assertIn("pyyaml==6.0.2", dockerfile)
        self.assertNotIn("COPY src/open_storyline/mvp/__init__.py", dockerfile)
        self.assertNotIn("openai-whisper", dockerfile)
        self.assertNotIn("accelerate", dockerfile)
        self.assertNotIn("torch", dockerfile)


if __name__ == "__main__":
    unittest.main()
