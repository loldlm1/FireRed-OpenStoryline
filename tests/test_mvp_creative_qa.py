from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from copy import deepcopy
import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

from open_storyline.mvp.creative_qa import (
    ASSET_VISIBILITY_VERSION,
    CREATIVE_CONFORMANCE_VERSION,
    QAInput,
    RENDER_QA_VERSION,
    RETENTION_RHYTHM_QA_VERSION,
    build_creative_conformance_report,
    build_asset_visibility_report,
    build_render_qa_report,
    build_retention_rhythm_report,
    build_semantic_review,
    creative_qa_enabled,
    creative_qa_strict,
    generate_creative_qa_artifacts,
    semantic_qa_enabled,
    semantic_qa_frame_limit,
)
from open_storyline.mvp.frame_sampling import FrameManifest, SampledFrame
from open_storyline.mvp.ninerouter import NineRouterAttempt, NineRouterError


FIXTURES = Path(__file__).parent / "fixtures" / "mvp_agentic"


def edit_plan(*, with_asset: bool = False) -> dict:
    request = [{"id": "asset-1"}] if with_asset else []
    return {
        "version": "edit_plan.v2",
        "requested_capabilities": [
            "crop", "focus_zoom", "hard_cut", "subtitles",
            *(["image_overlay"] if with_asset else []),
        ],
        "clips": [{
            "clip_index": 1,
            "segments": [{
                "layout": {"mode": "crop", "max_zoom": 1.5},
                "transition_in": {"kind": "cut", "duration_ms": 0},
                "overlays": ([{"kind": "image", "asset_id": "asset-1"}] if with_asset else []),
            }],
            "asset_requests": request,
        }],
    }


def execution(*, with_asset: bool = False, fallback_reason: str = "") -> dict:
    return {
        "version": "render_execution.v1",
        "summary": {"clips": 1, "encodes": 1, "fallbacks": int(bool(fallback_reason))},
        "clips": [{
            "clip_index": 1,
            "video": "short-01.mp4",
            "subtitles": "short-01.srt",
            "asset_ids": ["asset-1"] if with_asset else [],
            "segments": [{
                "id": "segment-1",
                "operation": "focus_zoom",
                "strategy": "crop",
                "timeline_window": {"start_ms": 0, "end_ms": 8000},
                "transition_kind": "cut",
                "overlays": ([{
                    "id": "overlay-1",
                    "kind": "image",
                    "asset_id": "asset-1",
                    "timeline_window": {"start_ms": 1000, "end_ms": 2500},
                }] if with_asset else []),
                "fallback_used": bool(fallback_reason),
                "reason": fallback_reason,
            }],
        }],
    }


class FakeSemanticClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.last_attempts = ()

    async def complete_structured(self, **kwargs):
        self.calls += 1
        self.last_attempts = (NineRouterAttempt(
            1,
            200,
            "ok",
            duration_ms=1250,
            input_tokens=100,
            output_tokens=20,
            reasoning_tokens=10,
            total_tokens=130,
            cost_usd=0.01,
        ),)
        if self.fail:
            raise NineRouterError(
                "NINEROUTER_REQUEST_FAILED",
                "synthetic provider failure",
                attempts=[NineRouterAttempt(1, 503, "service unavailable")],
            )
        records = json.loads(kwargs["user_prompt"])["frames_in_image_order"]
        return {
            "status": "pass",
            "summary": "The planned focus is visible and relevant.",
            "observations": [
                {
                    "clip_index": record["clip_index"],
                    "frame_id": record["frame_id"],
                    "planned_focus_visible": True,
                    "relevant": True,
                    "confidence": 0.9,
                    "note": "The intended subject is visible.",
                }
                for record in records
            ],
        }


class IncompleteSemanticClient(FakeSemanticClient):
    async def complete_structured(self, **kwargs):
        result = await super().complete_structured(**kwargs)
        result["observations"] = result["observations"][:1]
        return result


class CreativeQATests(unittest.IsolatedAsyncioTestCase):
    def test_conformance_exposes_missing_operations_assets_and_fallbacks(self):
        rendered = execution(with_asset=False)
        rendered["clips"][0]["segments"][0]["fallback_used"] = True
        report = build_creative_conformance_report(
            edit_plan=edit_plan(with_asset=True),
            render_execution=rendered,
            strict=True,
        )

        self.assertEqual(report["version"], CREATIVE_CONFORMANCE_VERSION)
        self.assertEqual(report["status"], "blocker")
        self.assertEqual(report["assets"]["missing"], ["asset-1"])
        codes = {item["code"] for item in report["findings"]}
        self.assertIn("planned_operations_missing", codes)
        self.assertIn("requested_assets_missing", codes)
        self.assertIn("unexplained_fallback", codes)

    def test_conformance_accepts_executed_typed_operations_and_assets(self):
        report = build_creative_conformance_report(
            edit_plan=edit_plan(with_asset=True),
            render_execution=execution(with_asset=True),
            strict=True,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["operations"]["missing"], [])
        self.assertEqual(report["assets"]["missing"], [])

    def test_conformance_accepts_focus_zoom_clamped_to_safe_crop(self):
        rendered = execution(with_asset=True)
        rendered["clips"][0]["segments"][0]["operation"] = "crop"
        report = build_creative_conformance_report(
            edit_plan=edit_plan(with_asset=True),
            render_execution=rendered,
            strict=True,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["operations"]["conditional"], ["focus_zoom"])
        self.assertEqual(report["operations"]["missing"], [])

    def test_conformance_blocks_a_truthfully_reported_unmet_creative_intent(self):
        report = build_creative_conformance_report(
            edit_plan=edit_plan(),
            render_execution=execution(),
            intent_conformance={
                "version": "creative_intent.v2",
                "status": "degraded",
                "error_code": "EDIT_PLAN_INTENT_MISMATCH",
                "evidence": {
                    "constraint_code": "opening_title_invalid",
                    "intent_id": "prompt-opening-title",
                },
            },
            strict=True,
        )

        self.assertEqual(report["status"], "blocker")
        self.assertEqual(report["intent_conformance"], {
            "status": "degraded",
            "error_code": "EDIT_PLAN_INTENT_MISMATCH",
            "constraint_code": "opening_title_invalid",
            "intent_id": "prompt-opening-title",
        })
        self.assertIn(
            "creative_intent_unmet",
            {item["code"] for item in report["findings"]},
        )

    def test_duplicate_asset_visibility_blocks_conformance(self):
        rendered = execution(with_asset=True)
        duplicate = deepcopy(rendered["clips"][0]["segments"][0]["overlays"][0])
        duplicate["id"] = "overlay-2"
        duplicate["timeline_window"] = {"start_ms": 1500, "end_ms": 3000}
        rendered["clips"][0]["segments"][0]["overlays"].append(duplicate)
        with (
            patch(
                "open_storyline.mvp.creative_qa._probe_visual_asset",
                return_value={"width": 60, "height": 80, "duration_seconds": 0},
            ),
            patch(
                "open_storyline.mvp.creative_qa._measure_asset_visibility",
                return_value=0.99,
            ),
        ):
            visibility = build_asset_visibility_report(
                [QAInput(1, Path("short-01.mp4"), 8000)],
                render_execution=rendered,
                resolved_assets={"asset-1": Path(__file__)},
                expected_width=180,
                expected_height=320,
                strict=True,
            )
        report = build_creative_conformance_report(
            edit_plan=edit_plan(with_asset=True),
            render_execution=rendered,
            strict=True,
            asset_visibility=visibility,
        )

        self.assertEqual(visibility["version"], ASSET_VISIBILITY_VERSION)
        self.assertEqual(report["status"], "blocker")
        self.assertIn(
            "asset_overlay_duplicated",
            {item["code"] for item in report["findings"]},
        )

    def test_rhythm_report_measures_hook_holds_attention_and_subtitles(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            subtitles = root / "short-01.srt"
            subtitles.write_text(
                "1\n00:00:00,500 --> 00:00:02,000\nHook\n\n"
                "2\n00:00:05,000 --> 00:00:07,500\nConclusion\n",
                encoding="utf-8",
            )
            report = build_retention_rhythm_report(
                [QAInput(1, root / "short-01.mp4", 8000, subtitles)],
                render_execution=execution(),
                strict=True,
            )

        self.assertEqual(report["version"], RETENTION_RHYTHM_QA_VERSION)
        self.assertIn("do not predict", report["notice"])
        metrics = report["clips"][0]["metrics"]
        self.assertEqual(metrics["hook_attention_events"], 1)
        self.assertEqual(metrics["subtitle_cues"], 2)
        self.assertGreaterEqual(metrics["longest_visual_hold_ms"], 8000)

    def test_cumulative_short_speech_pauses_are_not_technical_blockers(self):
        media = {
            "duration_ms": 22_800,
            "width": 1080,
            "height": 1920,
            "video_codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
        }
        silence = [
            {"start": 3.946, "end": 5.858, "duration": 1.913},
            {"start": 7.147, "end": 9.342, "duration": 2.194},
            {"start": 15.066, "end": 17.110, "duration": 2.045},
            {"start": 20.770, "end": 22.800, "duration": 2.030},
        ]
        with patch("open_storyline.mvp.creative_qa._probe", return_value=media), patch(
            "open_storyline.mvp.creative_qa._detect",
            side_effect=(([], None), ([], None), (silence, None)),
        ):
            report = build_render_qa_report(
                [QAInput(1, Path("short-01.mp4"), 22_800)],
                expected_width=1080,
                expected_height=1920,
                strict=True,
            )

        finding = next(
            item for item in report["findings"] if item["code"] == "long_silence_detected"
        )
        self.assertEqual(report["status"], "warning")
        self.assertEqual(finding["severity"], "warning")
        self.assertAlmostEqual(
            finding["details"]["longest_segment_seconds"],
            2.194,
            places=3,
        )

    async def test_semantic_review_is_bounded_non_mutating_and_failure_is_non_blocking(self):
        frame = SampledFrame(
            id="frame-001",
            timestamp_ms=1000,
            scene_id="scene-001",
            width=180,
            height=320,
            extraction_reason="uniform_coverage",
            encoded_bytes=4,
            data_url="data:image/jpeg;base64,ZmFrZQ==",
        )
        second_frame = SampledFrame(
            id="frame-002",
            timestamp_ms=1500,
            scene_id="scene-001",
            width=180,
            height=320,
            extraction_reason="uniform_coverage",
            encoded_bytes=4,
            data_url="data:image/jpeg;base64,ZmFrZTI=",
        )
        manifest = FrameManifest(2000, 180, 320, (frame, second_frame))
        source = QAInput(1, Path("short-01.mp4"), 2000)
        media = {"duration_ms": 2000, "width": 180, "height": 320}

        success = FakeSemanticClient()
        with (
            patch("open_storyline.mvp.creative_qa._probe", return_value=media),
            patch("open_storyline.mvp.creative_qa.sample_frames", return_value=manifest),
        ):
            report = await build_semantic_review([source], client=success, max_frames=2)
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["non_mutating"])
        self.assertEqual(report["provider_calls"], 1)
        self.assertEqual(report["frame_count"], 2)
        self.assertEqual(len(report["observations"]), 2)
        self.assertEqual(report["attempts"][0]["duration_ms"], 1250)
        self.assertEqual(report["attempts"][0]["total_tokens"], 130)
        self.assertEqual(report["attempts"][0]["cost_usd"], 0.01)
        self.assertNotIn("data:image", json.dumps(report))

        incomplete = IncompleteSemanticClient()
        with (
            patch("open_storyline.mvp.creative_qa._probe", return_value=media),
            patch("open_storyline.mvp.creative_qa.sample_frames", return_value=manifest),
        ):
            rejected = await build_semantic_review(
                [source],
                client=incomplete,
                max_frames=2,
            )
        self.assertEqual(rejected["status"], "unavailable")
        self.assertEqual(rejected["error_code"], "SEMANTIC_QA_RESPONSE_INVALID")

        failure = FakeSemanticClient(fail=True)
        with (
            patch("open_storyline.mvp.creative_qa._probe", return_value=media),
            patch("open_storyline.mvp.creative_qa.sample_frames", return_value=manifest),
        ):
            unavailable = await build_semantic_review([source], client=failure, max_frames=2)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["error_code"], "NINEROUTER_REQUEST_FAILED")
        self.assertEqual(unavailable["attempts"][0]["reason"], "service unavailable")

    async def test_artifact_generation_writes_all_versions_when_semantic_is_disabled(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = QAInput(1, root / "short-01.mp4", 8000)
            render_report = {
                "version": RENDER_QA_VERSION,
                "status": "pass",
                "summary": {},
                "clips": [],
                "findings": [],
            }
            with patch(
                "open_storyline.mvp.creative_qa.build_render_qa_report",
                return_value=render_report,
            ):
                result = await generate_creative_qa_artifacts(
                    output_dir=root,
                    inputs=[source],
                    edit_plan=edit_plan(),
                    render_execution=execution(),
                    intent_conformance={
                        "version": "creative_intent.v2",
                        "status": "conformant",
                    },
                    expected_width=180,
                    expected_height=320,
                    strict=True,
                    semantic_enabled=False,
                    semantic_max_frames=4,
                    semantic_client=None,
                )

            self.assertEqual(json.loads(result.render_qa_path.read_text())["version"], RENDER_QA_VERSION)
            self.assertEqual(json.loads(result.rhythm_qa_path.read_text())["version"], RETENTION_RHYTHM_QA_VERSION)
            conformance = json.loads(result.conformance_path.read_text())
            self.assertEqual(conformance["version"], CREATIVE_CONFORMANCE_VERSION)
            self.assertEqual(conformance["asset_visibility"]["status"], "pass")
            self.assertEqual(conformance["semantic_review"]["status"], "disabled")
            self.assertEqual(
                conformance["intent_conformance"]["status"],
                "conformant",
            )

    def test_environment_controls_are_strict_and_bounded(self):
        config = SimpleNamespace(
            creative_qa_enabled=True,
            creative_qa_strict=True,
            semantic_qa_enabled=False,
            semantic_qa_max_frames=4,
        )
        with patch.dict(os.environ, {
            "OPENSTORYLINE_CREATIVE_QA_ENABLED": "false",
            "OPENSTORYLINE_CREATIVE_QA_STRICT": "false",
            "OPENSTORYLINE_SEMANTIC_QA_ENABLED": "true",
            "OPENSTORYLINE_SEMANTIC_QA_MAX_FRAMES": "6",
        }):
            self.assertFalse(creative_qa_enabled(config))
            self.assertFalse(creative_qa_strict(config))
            self.assertTrue(semantic_qa_enabled(config))
            self.assertEqual(semantic_qa_frame_limit(config), 6)

        with patch.dict(os.environ, {"OPENSTORYLINE_SEMANTIC_QA_MAX_FRAMES": "99"}):
            with self.assertRaises(Exception):
                semantic_qa_frame_limit(config)

    def test_cross_niche_fixtures_are_private_free_schema_outcomes(self):
        fixture_paths = sorted(FIXTURES.glob("*.json"))
        self.assertGreaterEqual(len(fixture_paths), 8)
        identifiers = set()
        covered_metrics = set()
        cross_niche_count = 0
        for path in fixture_paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            serialized = json.dumps(payload).lower()
            self.assertNotIn("sesion prueba 1", serialized)
            self.assertNotIn("api_key", serialized)
            self.assertNotIn("/home/", serialized)
            if payload.get("version") != "mvp_agentic_fixture.v1":
                continue
            cross_niche_count += 1
            identifiers.add(payload["id"])
            self.assertEqual(payload["version"], "mvp_agentic_fixture.v1")
            self.assertTrue(payload["expected_roles"])
            self.assertTrue(payload["expected_capabilities"])
            self.assertIn(payload["expected_asset_calls"], {0, 1})
            covered_metrics.update(payload["metrics"])
        self.assertEqual(len(identifiers), cross_niche_count)
        self.assertTrue({
            "two-speaker-interview",
            "product-presentation",
            "single-speaker-tutorial",
            "cooking-demonstration",
            "trading-screen",
            "multi-speaker-panel",
            "sparse-visual-monologue",
            "source-only-no-asset",
        } <= identifiers)
        self.assertTrue({
            "schema_validity", "source_bounds", "target_visibility",
            "center_fallbacks", "asset_calls", "plan_execution", "qa_status",
            "latency_ms", "provider_calls",
        } <= covered_metrics)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class CreativeQARenderTests(unittest.TestCase):
    def _asset_visibility_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        asset = root / "asset.png"
        visible = root / "visible.mp4"
        invisible = root / "invisible.mp4"
        commands = [
            [
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=red:size=60x80", "-frames:v", "1", str(asset),
            ],
            [
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=blue:size=180x320:rate=24:duration=3",
                "-loop", "1", "-i", str(asset), "-filter_complex",
                "[0:v][1:v]overlay=60:120:enable='between(t,1,2)'",
                "-t", "3", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", str(visible),
            ],
            [
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=black:size=180x320:rate=24:duration=3",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", str(invisible),
            ],
        ]
        for command in commands:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        return asset, visible, invisible

    def test_asset_visibility_passes_only_when_declared_pixels_are_rendered(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            asset, visible, invisible = self._asset_visibility_fixture(root)
            rendered = execution(with_asset=True)
            overlay = rendered["clips"][0]["segments"][0]["overlays"][0]
            overlay.update({
                "timeline_window": {"start_ms": 1000, "end_ms": 2000},
                "width_ratio": 1 / 3,
                "position": "center",
                "opacity": 1.0,
            })
            reports = [
                build_asset_visibility_report(
                    [QAInput(1, video, 3000)],
                    render_execution=rendered,
                    resolved_assets={"asset-1": asset},
                    expected_width=180,
                    expected_height=320,
                    strict=True,
                    timeout=60,
                )
                for video in (visible, invisible)
            ]

        self.assertEqual(reports[0]["status"], "pass")
        self.assertGreaterEqual(reports[0]["observations"][0]["ssim"], 0.6)
        self.assertEqual(reports[1]["status"], "blocker")
        self.assertIn(
            "asset_overlay_not_visible",
            {item["code"] for item in reports[1]["findings"]},
        )

    def test_asset_visibility_normalizes_mismatched_video_timebases_at_clip_start(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            asset = root / "asset.mp4"
            visible = root / "visible.mp4"
            commands = [
                [
                    "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:size=60x80:rate=30:duration=6",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(asset),
                ],
                [
                    "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:size=180x320:rate=60:duration=6",
                    "-i", str(asset),
                    "-filter_complex", "[0:v][1:v]overlay=60:120:enable='between(t,0,4)'",
                    "-t", "6", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(visible),
                ],
            ]
            for command in commands:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

            rendered = execution(with_asset=True)
            overlay = rendered["clips"][0]["segments"][0]["overlays"][0]
            overlay.update({
                "timeline_window": {"start_ms": 0, "end_ms": 4000},
                "width_ratio": 1 / 3,
                "position": "center",
                "opacity": 1.0,
            })
            report = build_asset_visibility_report(
                [QAInput(1, visible, 6000)],
                render_execution=rendered,
                resolved_assets={"asset-1": asset},
                expected_width=180,
                expected_height=320,
                strict=True,
                timeout=60,
            )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["observations"][0]["error_code"], None)
        self.assertGreaterEqual(report["observations"][0]["ssim"], 0.6)

    def test_structural_qa_probes_real_synthetic_output(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            video = root / "synthetic.mp4"
            generated = subprocess.run([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=24:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=3",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
            ], capture_output=True, text=True, check=False, timeout=120)
            self.assertEqual(generated.returncode, 0, generated.stderr)

            report = build_render_qa_report(
                [QAInput(1, video, 3000)],
                expected_width=180,
                expected_height=320,
                strict=True,
                timeout=60,
            )

        self.assertEqual(report["version"], RENDER_QA_VERSION)
        self.assertNotEqual(report["status"], "blocker")
        self.assertEqual(report["clips"][0]["media"]["video_codec"], "h264")
        self.assertTrue(report["clips"][0]["media"]["has_audio"])


if __name__ == "__main__":
    unittest.main()
