import json
import unittest

from open_storyline.mvp.shorts import (
    ShortsPlanError,
    ShortsPlanner,
    format_transcript,
    validate_candidates,
)


class CandidateValidationTests(unittest.TestCase):
    def test_accepts_inclusive_duration_boundaries(self):
        plan = validate_candidates([
            {"start_ms": 0, "end_ms": 18_000, "title": "A", "score": 0.7},
            {"start_ms": 30_000, "end_ms": 55_000, "title": "B", "score": 0.6},
        ], source_duration_ms=60_000, max_clips=5)
        self.assertEqual([item.duration_ms for item in plan.clips], [18_000, 25_000])

    def test_rejects_bounds_and_invalid_durations(self):
        with self.assertRaises(ShortsPlanError) as caught:
            validate_candidates([
                {"start_ms": -1, "end_ms": 20_000, "score": 1},
                {"start_ms": 1_000, "end_ms": 17_000, "score": 1},
                {"start_ms": 50_000, "end_ms": 80_000, "score": 1},
            ], source_duration_ms=60_000, max_clips=3)
        self.assertEqual(caught.exception.code, "NO_VALID_SHORTS")
        self.assertEqual(len(caught.exception.rejected), 3)

    def test_ranks_deduplicates_overlap_and_limits_count(self):
        plan = validate_candidates([
            {"start_ms": 0, "end_ms": 20_000, "title": "low", "score": 0.4},
            {"start_ms": 1_000, "end_ms": 21_000, "title": "best", "score": 0.9},
            {"start_ms": 30_000, "end_ms": 50_000, "title": "second", "score": 0.8},
            {"start_ms": 60_000, "end_ms": 80_000, "title": "third", "score": 0.7},
        ], source_duration_ms=90_000, max_clips=2)
        self.assertEqual([item.title for item in plan.clips], ["best", "second"])
        self.assertTrue(any("overlaps" in item["reason"] for item in plan.rejected))

    def test_rejects_non_finite_score(self):
        with self.assertRaises(ShortsPlanError):
            validate_candidates([
                {"start_ms": 0, "end_ms": 20_000, "score": float("nan")},
            ], source_duration_ms=30_000, max_clips=1)


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.call = None

    async def complete_json(self, **kwargs):
        self.call = kwargs
        return self.response


class ShortsPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_sends_prompt_transcript_and_frames(self):
        client = FakeClient({"clips": [{
            "start_ms": 1_000,
            "end_ms": 21_000,
            "title": "Hook",
            "hook": "Listen",
            "reason": "Strong standalone idea",
            "score": 0.9,
        }]})
        planner = ShortsPlanner(client)
        result = await planner.plan(
            editing_prompt="focus on practical advice",
            transcript_text="fallback",
            transcript_segments=[{"start": 1_000, "end": 3_000, "text": "Hola mundo"}],
            source_duration_ms=30_000,
            max_clips=3,
            frame_data_urls=["data:image/jpeg;base64,ZmFrZQ=="],
        )

        payload = json.loads(client.call["user_prompt"])
        self.assertIn("practical advice", payload["editing_prompt"])
        self.assertIn("Hola mundo", payload["transcript"])
        self.assertEqual(len(client.call["image_data_urls"]), 1)
        self.assertEqual(result.clips[0].title, "Hook")

    async def test_missing_clips_array_is_typed_failure(self):
        planner = ShortsPlanner(FakeClient({"result": []}))
        with self.assertRaises(ShortsPlanError) as caught:
            await planner.plan(
                editing_prompt="shorts",
                transcript_text="text",
                transcript_segments=[],
                source_duration_ms=30_000,
                max_clips=1,
            )
        self.assertEqual(caught.exception.code, "SHORTS_RESPONSE_INVALID")

    def test_formats_millisecond_timestamps(self):
        rendered = format_transcript(
            [{"start": 61_250, "end": 63_500, "text": "hello"}],
            "fallback",
        )
        self.assertIn("00:01:01.250-00:01:03.500", rendered)


if __name__ == "__main__":
    unittest.main()
