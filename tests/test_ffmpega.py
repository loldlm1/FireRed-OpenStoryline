from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import httpx

from open_storyline.mvp.ffmpega import (
    AGENTIC_FINISHING_SKILLS,
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

    def test_agentic_finishing_policy_excludes_structural_edits(self):
        plan = validate_effects(
            {"effects": [{"skill": "vignette", "params": {}}]},
            allowed_skills=AGENTIC_FINISHING_SKILLS,
        )
        self.assertEqual(plan.effects[0].skill, "vignette")
        for skill in ("fade", "letterbox", "rotate", "deshake"):
            with self.subTest(skill=skill), self.assertRaises(FFMPEGAError) as caught:
                validate_effects(
                    {"effects": [{"skill": skill, "params": {}}]},
                    allowed_skills=AGENTIC_FINISHING_SKILLS,
                )
            self.assertEqual(caught.exception.code, "FFMPEGA_SKILL_BLOCKED")


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

    async def test_agentic_planner_advertises_only_finishing_skills(self):
        client = FakePlannerClient({"effects": []})
        await EffectsPlanner(client).plan(
            "finish the completed timeline",
            allowed_skills=AGENTIC_FINISHING_SKILLS,
        )
        prompt = client.kwargs["system_prompt"]
        self.assertIn("vignette", prompt)
        self.assertNotIn("letterbox", prompt)
        self.assertNotIn("fade,", prompt)


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

    async def test_maps_shared_paths_for_host_comfyui(self):
        with TemporaryDirectory() as tmpdir:
            local_root = Path(tmpdir) / "container-outputs"
            remote_root = Path("/home/user/openstoryline/outputs")
            source = local_root / "job" / "source.mp4"
            destination = local_root / "job" / "result.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            captured = {}

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/prompt":
                    captured.update(json.loads(request.content))
                    return httpx.Response(200, json={"prompt_id": "mapped"})
                destination.write_bytes(b"result")
                return httpx.Response(200, json={
                    "mapped": {"status": {"status_str": "success", "completed": True}},
                })

            client = FFMPEGAClient(
                base_url="http://comfy.test",
                shared_local_root=str(local_root),
                shared_remote_root=str(remote_root),
                transport=httpx.MockTransport(handler),
            )
            await client.apply(
                source=source,
                destination=destination,
                plan=validate_effects({"effects": [{"skill": "vignette", "params": {}}]}),
            )
            inputs = captured["prompt"]["1"]["inputs"]
            self.assertEqual(inputs["video_path"], str(remote_root / "job" / "source.mp4"))
            self.assertEqual(inputs["output_path"], str(remote_root / "job" / "result.mp4"))


if __name__ == "__main__":
    unittest.main()
