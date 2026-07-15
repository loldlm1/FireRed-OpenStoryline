from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import httpx

from open_storyline.mvp.ffmpega import (
    EffectsPlanner,
    FFMPEGAClient,
    FFMPEGAError,
    validate_effects,
)


class EffectsPolicyTests(unittest.TestCase):
    def test_accepts_only_deterministic_effects(self):
        plan = validate_effects({"effects": [
            {"skill": "vignette", "params": {}},
            {"skill": "saturation", "params": {"amount": 1.2}},
        ]})
        pipeline = plan.to_ffmpega_pipeline()
        self.assertEqual(pipeline["effects_mode"], "skills")
        self.assertEqual(pipeline["pipeline"][1]["skill"], "saturation")
        self.assertEqual(pipeline["raw_ffmpeg"], "")
        self.assertIsNone(pipeline["sam3"])

    def test_blocks_model_skills_and_sensitive_parameters(self):
        for payload in [
            {"effects": [{"skill": "auto_transcribe", "params": {}}]},
            {"effects": [{"skill": "ai_upscale", "params": {}}]},
            {"effects": [{"skill": "blur", "params": {"model_path": "/tmp/model"}}]},
        ]:
            with self.subTest(payload=payload), self.assertRaises(FFMPEGAError):
                validate_effects(payload)


class FakePlannerClient:
    def __init__(self, response):
        self.response = response

    async def complete_json(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class EffectsPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_planner_is_followed_by_local_policy(self):
        client = FakePlannerClient({"effects": [{"skill": "vignette", "params": {}}]})
        plan = await EffectsPlanner(client).plan("make it cinematic")
        self.assertEqual(plan.effects[0].skill, "vignette")
        self.assertIn("allowlist", client.kwargs["system_prompt"])

    async def test_remote_planner_cannot_enable_local_model(self):
        planner = EffectsPlanner(FakePlannerClient({
            "effects": [{"skill": "auto_transcribe", "params": {}}],
        }))
        with self.assertRaises(FFMPEGAError) as caught:
            await planner.plan("transcribe locally")
        self.assertEqual(caught.exception.code, "FFMPEGA_SKILL_BLOCKED")


class FFMPEGAClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_submits_manual_no_model_workflow_and_waits_for_output(self):
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.mp4"
            destination = Path(tmpdir) / "result.mp4"
            source.write_bytes(b"source")
            captured = {}

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/prompt":
                    captured.update(json.loads(request.content))
                    return httpx.Response(200, json={"prompt_id": "prompt-1"})
                if request.url.path == "/history/prompt-1":
                    destination.write_bytes(b"result")
                    return httpx.Response(200, json={
                        "prompt-1": {"status": {"status_str": "success", "completed": True}},
                    })
                return httpx.Response(404)

            client = FFMPEGAClient(
                base_url="http://comfy.test",
                poll_interval=0.01,
                transport=httpx.MockTransport(handler),
            )
            result = await client.apply(
                source=source,
                destination=destination,
                plan=validate_effects({"effects": [{"skill": "vignette", "params": {}}]}),
            )

            inputs = captured["prompt"]["1"]["inputs"]
            pipeline = json.loads(inputs["pipeline_json"])
            self.assertEqual(result, destination)
            self.assertEqual(inputs["llm_model"], "none")
            self.assertEqual(inputs["no_llm_mode"], "manual")
            self.assertFalse(inputs["allow_model_downloads"])
            self.assertEqual(pipeline["pipeline"][0]["skill"], "vignette")


if __name__ == "__main__":
    unittest.main()
