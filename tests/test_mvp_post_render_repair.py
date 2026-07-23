from __future__ import annotations

from types import SimpleNamespace
import copy
import json
import unittest

from open_storyline.mvp.edit_plan import build_shadow_edit_plan, validate_edit_plan
from open_storyline.mvp.ffmpega import EffectsPlan, validate_effects
from open_storyline.mvp.post_render_repair import (
    PostRenderRepairError,
    PostRenderRepairState,
    compare_critic_improvement,
    eligible_render_findings,
    post_render_repair_fingerprint,
    post_render_repair_from_checkpoint,
    request_post_render_repair,
)
from open_storyline.mvp.render_evidence import (
    EvidenceClip,
    EvidenceFrame,
    EvidenceLimits,
    RenderEvidenceManifest,
)
from open_storyline.mvp.shorts import ShortCandidate


def _plan():
    return build_shadow_edit_plan(
        (ShortCandidate(0, 8_000, "One", "Hook", "Reason", 0.9),),
        source_duration_ms=10_000,
    )


def _manifest() -> RenderEvidenceManifest:
    frame = EvidenceFrame(
        evidence_id="ev-" + "a" * 24,
        clip_index=1,
        timestamp_ms=1000,
        purpose=("opening_anchor",),
        source_artifact="short-01.mp4",
        width=320,
        height=180,
        encoded_bytes=4,
        sha256="b" * 64,
    )
    clip = EvidenceClip(
        clip_index=1,
        source_artifact="short-01.mp4",
        output_sha256="c" * 64,
        duration_ms=8_000,
        frames=(frame,),
        selected_reasons=("opening_anchor",),
    )
    return RenderEvidenceManifest(
        source_sha256="d" * 64,
        render_execution_sha256="e" * 64,
        plan_sha256="f" * 64,
        effects_sha256="0" * 64,
        candidate_fingerprint="1" * 64,
        call_fingerprint="1" * 64,
        limits=EvidenceLimits(),
        clips=(clip,),
        frame_count=1,
        burst_count=0,
        encoded_bytes=4,
    )


def _finding(*, severity="warning", confidence=0.9):
    return {
        "finding_id": "finding-" + "2" * 24,
        "finding_fingerprint": "3" * 64,
        "category": "framing",
        "severity": severity,
        "classification": "creative",
        "confidence": confidence,
        "clip_index": 1,
        "start_ms": 0,
        "end_ms": 2_000,
        "evidence_ids": ["ev-" + "a" * 24],
        "explanation": "The opening can emphasize the subject.",
        "repair_objective": "Use a restrained focus zoom.",
        "requested_capabilities": ["zoom"],
        "repairable": True,
    }


def _effect_finding():
    return {
        **_finding(),
        "finding_id": "finding-" + "4" * 24,
        "finding_fingerprint": "5" * 64,
        "category": "effects",
        "repair_objective": "Reduce the vignette so the frame stays open.",
        "requested_capabilities": ["effect"],
    }


def _response(base_plan, *, no_change=False):
    clip = copy.deepcopy(base_plan.clips[0].model_dump(mode="json"))
    if not no_change:
        clip["segments"][0]["layout"]["max_zoom"] = 1.2
    return {
        "status": "no_change" if no_change else "repair",
        "decisions": [{
            "finding_id": _finding()["finding_id"],
            "decision": "no_change" if no_change else "repair",
            "target": "none" if no_change else "clip_plan",
            "reason": "The typed zoom is supported." if not no_change else "No safe change.",
            "affected_clip_indexes": [] if no_change else [1],
        }],
        "requested_capabilities": (
            list(base_plan.requested_capabilities)
            if no_change
            else [*base_plan.requested_capabilities, "focus_zoom"]
        ),
        "clips": [] if no_change else [clip],
        "effect_action": "preserve",
        "effect_plan": {"effects": []},
    }


class FakeClient:
    model = "critic-repair-model"
    reasoning_effort = "high"

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.last_attempts = ()

    async def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        self.last_attempts = (
            SimpleNamespace(
                number=1,
                status_code=503 if self.error else 200,
                reason="Bearer private-token failed" if self.error else "ok",
                duration_ms=10,
                input_tokens=100,
                output_tokens=50,
                reasoning_tokens=20,
                total_tokens=170,
                cost_usd=0.01,
            ),
        )
        if self.error:
            raise self.error
        return self.response


class PostRenderRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plan = _plan()
        self.effects = EffectsPlan(effects=[])
        self.manifest = _manifest()
        self.images = {"ev-" + "a" * 24: "data:image/jpeg;base64,ZmFrZQ=="}

    def _validator(self, value, affected):
        payload = self.plan.to_dict()
        payload["requested_capabilities"] = value["requested_capabilities"]
        replacements = {item["clip_index"]: item for item in value["clips"]}
        payload["clips"] = [
            replacements.get(item["clip_index"], item) for item in payload["clips"]
        ]
        self.assertEqual(tuple(sorted(replacements)), affected)
        return validate_edit_plan(payload, source_duration_ms=self.plan.source_duration_ms)

    async def test_typed_repair_maps_every_finding_and_returns_material_plan(self):
        client = FakeClient(_response(self.plan))
        proposal = await request_post_render_repair(
            manifest=self.manifest,
            image_data_urls=self.images,
            base_plan=self.plan,
            base_effects=self.effects,
            findings=(_finding(),),
            editing_prompt="Improve the opening framing.",
            round_name="primary",
            client=client,
            plan_validator=self._validator,
        )
        self.assertEqual(proposal.status, "repair")
        self.assertEqual(proposal.affected_clip_indexes, (1,))
        self.assertIsNotNone(proposal.candidate_plan)
        self.assertEqual(
            proposal.candidate_plan.clips[0].segments[0].layout.max_zoom,
            1.2,
        )
        call = client.calls[0]
        self.assertEqual(call["schema_name"], "post_render_repair.v2")
        self.assertEqual(len(call["image_data_urls"]), 1)
        self.assertNotIn("data:image", json.dumps(proposal.to_report_dict()))

    async def test_no_change_is_a_no_render_decision(self):
        proposal = await request_post_render_repair(
            manifest=self.manifest,
            image_data_urls=self.images,
            base_plan=self.plan,
            base_effects=self.effects,
            findings=(_finding(),),
            editing_prompt="Keep it restrained.",
            round_name="primary",
            client=FakeClient(_response(self.plan, no_change=True)),
            plan_validator=self._validator,
        )
        self.assertEqual(proposal.status, "no_change")
        self.assertIsNone(proposal.candidate_plan)
        self.assertEqual(proposal.affected_clip_indexes, ())

    async def test_effect_repair_replaces_only_an_allowlisted_typed_plan(self):
        current = validate_effects({
            "effects": [{"skill": "vignette", "params": {"intensity": 0.8}}],
        })
        response = {
            "status": "repair",
            "decisions": [{
                "finding_id": _effect_finding()["finding_id"],
                "decision": "repair",
                "target": "effect_plan",
                "reason": "The finishing treatment is too strong.",
                "affected_clip_indexes": [1],
            }],
            "requested_capabilities": list(self.plan.requested_capabilities),
            "clips": [],
            "effect_action": "replace",
            "effect_plan": {
                "effects": [{
                    "skill": "vignette",
                    "params": {"intensity": 0.25},
                }],
            },
        }
        proposal = await request_post_render_repair(
            manifest=self.manifest,
            image_data_urls=self.images,
            base_plan=self.plan,
            base_effects=current,
            findings=(_effect_finding(),),
            editing_prompt="Use a subtle finish.",
            round_name="primary",
            client=FakeClient(response),
            plan_validator=self._validator,
        )
        self.assertIsNone(proposal.candidate_plan)
        self.assertEqual(proposal.effect_affected_clip_indexes, (1,))
        self.assertEqual(proposal.candidate_effects.effects[0].params["intensity"], 0.25)

        blocked = copy.deepcopy(response)
        blocked["effect_plan"] = {
            "effects": [{
                "skill": "fade",
                "params": {"type": "in", "start": 0.0, "duration": 1.0},
            }],
        }
        with self.assertRaises(PostRenderRepairError):
            await request_post_render_repair(
                manifest=self.manifest,
                image_data_urls=self.images,
                base_plan=self.plan,
                base_effects=current,
                findings=(_effect_finding(),),
                editing_prompt="Use a subtle finish.",
                round_name="primary",
                client=FakeClient(blocked),
                plan_validator=self._validator,
            )

    async def test_response_rejects_omitted_findings_and_protected_mutations(self):
        omitted = _response(self.plan)
        omitted["decisions"] = []
        unsafe = _response(self.plan)
        unsafe["clips"][0]["source_window"]["end_ms"] = 7_500
        for response in (omitted, unsafe):
            with self.subTest(response=response):
                with self.assertRaises(PostRenderRepairError):
                    await request_post_render_repair(
                        manifest=self.manifest,
                        image_data_urls=self.images,
                        base_plan=self.plan,
                        base_effects=self.effects,
                        findings=(_finding(),),
                        editing_prompt="safe prompt",
                        round_name="primary",
                        client=FakeClient(response),
                        plan_validator=self._validator,
                    )

    async def test_provider_failure_is_sanitized_and_checkpoint_round_trip_is_strict(self):
        unavailable = await request_post_render_repair(
            manifest=self.manifest,
            image_data_urls=self.images,
            base_plan=self.plan,
            base_effects=self.effects,
            findings=(_finding(),),
            editing_prompt="safe prompt",
            round_name="primary",
            client=FakeClient(error=RuntimeError("private provider body")),
            plan_validator=self._validator,
        )
        self.assertEqual(unavailable.status, "unavailable")
        self.assertNotIn("private-token", json.dumps(unavailable.to_report_dict()))
        self.assertNotIn("provider body", json.dumps(unavailable.to_report_dict()))

        repaired = await request_post_render_repair(
            manifest=self.manifest,
            image_data_urls=self.images,
            base_plan=self.plan,
            base_effects=self.effects,
            findings=(_finding(),),
            editing_prompt="safe prompt",
            round_name="primary",
            client=FakeClient(_response(self.plan)),
            plan_validator=self._validator,
        )
        restored = post_render_repair_from_checkpoint(
            repaired.to_checkpoint_payload(),
            expected_request_fingerprint=repaired.request_fingerprint,
            base_plan=self.plan,
            base_effects=self.effects,
        )
        self.assertTrue(restored.checkpoint_reused)
        self.assertEqual(restored.provider_calls, 0)
        with self.assertRaises(PostRenderRepairError):
            post_render_repair_from_checkpoint(
                repaired.to_checkpoint_payload(),
                expected_request_fingerprint="0" * 64,
                base_plan=self.plan,
                base_effects=self.effects,
            )

    def test_call_cap_contingency_gate_and_fingerprint_efficiency(self):
        state = PostRenderRepairState()
        state.authorize("primary")
        with self.assertRaises(PostRenderRepairError):
            state.authorize("contingency")
        state.authorize("contingency", introduced_objective_codes=("AUDIO_MISSING",))
        with self.assertRaises(PostRenderRepairError):
            state.authorize("contingency", introduced_objective_codes=("AUDIO_MISSING",))

        first = post_render_repair_fingerprint(
            manifest=self.manifest,
            base_plan=self.plan,
            base_effects=self.effects,
            findings=(_finding(),),
            editing_prompt="a" * 12_000,
            round_name="primary",
            model="model-a",
            reasoning_effort="high",
        )
        same_payload = post_render_repair_fingerprint(
            manifest=self.manifest,
            base_plan=self.plan,
            base_effects=self.effects,
            findings=(_finding(),),
            editing_prompt=("a" * 12_000) + "ignored",
            round_name="primary",
            model="model-a",
            reasoning_effort="high",
        )
        changed = post_render_repair_fingerprint(
            manifest=self.manifest,
            base_plan=self.plan,
            base_effects=self.effects,
            findings=(_finding(),),
            editing_prompt="a" * 12_000,
            round_name="primary",
            model="model-b",
            reasoning_effort="high",
        )
        self.assertEqual(first, same_payload)
        self.assertNotEqual(first, changed)

    def test_finding_eligibility_and_improvement_are_bounded(self):
        findings = eligible_render_findings(
            {"findings": [
                _finding(),
                {**_finding(), "finding_id": "finding-technical", "classification": "technical"},
                {**_finding(), "finding_id": "finding-unsupported", "requested_capabilities": ["shell"]},
            ]},
            supported_capabilities=("zoom",),
        )
        self.assertEqual([item["finding_id"] for item in findings], [_finding()["finding_id"]])
        improvement = compare_critic_improvement(
            {"status": "review", "findings": [_finding(severity="blocker", confidence=0.9)]},
            {"status": "review", "findings": [_finding(severity="advisory", confidence=0.2)]},
        )
        self.assertTrue(improvement["demonstrated"])


if __name__ == "__main__":
    unittest.main()
