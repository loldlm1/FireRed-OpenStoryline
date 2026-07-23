from __future__ import annotations

import asyncio
import unittest

from open_storyline.mvp.candidate_comparison import (
    CandidateComparisonError,
    build_candidate_comparison_prompt,
    compare_rendered_candidates,
)


def report(fingerprint: str, *, finding_id: str = "finding-a") -> dict:
    return {
        "status": "review",
        "candidate_fingerprint": fingerprint,
        "findings": [{
            "finding_id": finding_id,
            "category": "pacing",
            "severity": "warning",
            "classification": "creative",
            "confidence": 0.8,
            "clip_index": 1,
            "start_ms": 0,
            "end_ms": 1_000,
            "evidence_ids": ["ev-123"],
            "repairable": True,
        }],
    }


class Client:
    model = "test-model"
    reasoning_effort = "medium"

    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def complete_structured(self, **kwargs):
        self.calls += 1
        return self.response


class CandidateComparisonTests(unittest.TestCase):
    def test_prompt_is_bounded_and_evidence_grounded(self):
        prompt = build_candidate_comparison_prompt(
            original_report=report("a" * 64),
            repaired_report=report("b" * 64, finding_id="finding-b"),
        )
        self.assertIn("candidate_fingerprint", prompt)
        self.assertIn("evidence_ids_only", prompt)
        self.assertNotIn("/private", prompt)

    def test_identical_candidates_make_no_provider_call(self):
        client = Client({})
        result = asyncio.run(compare_rendered_candidates(
            original_report=report("a" * 64),
            repaired_report=report("a" * 64),
            client=client,
        ))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(client.calls, 0)

    def test_comparison_requires_supplied_evidence(self):
        client = Client({
            "selection": "repaired",
            "confidence": 0.9,
            "rationale": "The repaired candidate is clearer.",
            "evidence_ids": ["ev-unknown"],
            "uncertainty": "low",
        })
        with self.assertRaises(CandidateComparisonError):
            asyncio.run(compare_rendered_candidates(
                original_report=report("a" * 64),
                repaired_report=report("b" * 64),
                client=client,
            ))

    def test_valid_comparison_is_attributable(self):
        client = Client({
            "selection": "repaired",
            "confidence": 0.9,
            "rationale": "The repaired candidate is clearer.",
            "evidence_ids": ["ev-123"],
            "uncertainty": "low",
        })
        result = asyncio.run(compare_rendered_candidates(
            original_report=report("a" * 64),
            repaired_report=report("b" * 64),
            client=client,
        ))
        self.assertEqual(result["selection"], "repaired")
        self.assertEqual(result["provider_calls"], 1)
        self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
