from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

from open_storyline.mvp.creative_qa import QAInput
from open_storyline.mvp.frame_quality import (
    FRAME_QUALITY_VERSION,
    build_frame_quality_report,
)
from open_storyline.mvp.promotion import (
    RenderPromotionError,
    build_render_promotion_report,
    enforce_render_promotion,
    render_promotion_mode,
)


def execution(*, strategy: str, expected_ratio: float) -> dict:
    return {
        "version": "render_execution.v1",
        "clips": [{
            "clip_index": 1,
            "video": "short-01.mp4",
            "segments": [{
                "id": "segment-1",
                "operation": "crop" if strategy == "crop" else "fit",
                "strategy": strategy,
                "source_window": {"start_ms": 0, "end_ms": 3000},
                "timeline_window": {"start_ms": 0, "end_ms": 3000},
                "crop": (
                    {"x": 109, "y": 0, "width": 102, "height": 180}
                    if strategy == "crop" else None
                ),
                "transition_kind": "cut",
                "transition_duration_ms": 0,
                "overlays": [],
                "expected_active_area_ratio": expected_ratio,
            }],
        }],
    }


class RenderPromotionTests(unittest.TestCase):
    def test_report_observes_and_enforce_blocks_the_same_codes(self):
        frame_quality = {
            "status": "blocker",
            "findings": [{"code": "ACTIVE_PICTURE_TOO_SMALL", "severity": "blocker"}],
        }
        conformance = {
            "status": "blocker",
            "findings": [
                {"code": "requested_assets_missing", "severity": "warning"},
                {"code": "asset_overlay_not_visible", "severity": "warning"},
            ],
        }
        footprint = {
            "status": "blocked",
            "summary": {"blocker_codes": ["CAPTION_WIDTH_EXCEEDED"]},
        }
        render_qa = {"status": "pass", "findings": []}

        report = build_render_promotion_report(
            mode="report",
            frame_quality=frame_quality,
            render_qa=render_qa,
            creative_conformance=conformance,
            caption_footprints=[footprint],
        )
        enforce = build_render_promotion_report(
            mode="enforce",
            frame_quality=frame_quality,
            render_qa=render_qa,
            creative_conformance=conformance,
            caption_footprints=[footprint],
        )

        self.assertEqual(report["decision"], "observe")
        self.assertEqual(enforce["decision"], "block")
        self.assertEqual(report["blocker_codes"], enforce["blocker_codes"])
        self.assertIn("ASSET_OVERLAY_NOT_VISIBLE", report["blocker_codes"])
        with self.assertRaises(RenderPromotionError) as caught:
            enforce_render_promotion(enforce)
        self.assertEqual(caught.exception.code, "RENDER_PROMOTION_BLOCKED")

    def test_unavailable_deterministic_evidence_blocks_enforcement(self):
        report = build_render_promotion_report(
            mode="enforce",
            frame_quality=None,
            render_qa=None,
            creative_conformance=None,
            caption_footprints=[],
        )
        self.assertEqual(report["decision"], "block")
        self.assertEqual(
            set(report["blocker_codes"]),
            {
                "FRAME_QUALITY_UNAVAILABLE",
                "RENDER_STRUCTURE_UNAVAILABLE",
                "CREATIVE_CONFORMANCE_UNAVAILABLE",
            },
        )

    def test_promotion_environment_control_is_strict(self):
        config = SimpleNamespace(render_promotion_mode="report")
        with patch.dict(os.environ, {"OPENSTORYLINE_RENDER_PROMOTION_MODE": "enforce"}):
            self.assertEqual(render_promotion_mode(config), "enforce")
        with patch.dict(os.environ, {"OPENSTORYLINE_RENDER_PROMOTION_MODE": "invalid"}):
            with self.assertRaises(RenderPromotionError) as caught:
                render_promotion_mode(config)
        self.assertEqual(caught.exception.code, "RENDER_PROMOTION_CONFIG_INVALID")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class FrameQualityRenderTests(unittest.TestCase):
    def test_fill_letterbox_and_degraded_outputs_are_classified_from_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            generated = subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=320x180:rate=24:duration=3",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
                "-pix_fmt", "yuv420p", str(source),
            ], capture_output=True, text=True, check=False, timeout=120)
            self.assertEqual(generated.returncode, 0, generated.stderr)

            filters = {
                "fill": "crop=102:180:109:0,scale=180:320",
                "letterbox": (
                    "scale=180:320:force_original_aspect_ratio=decrease,"
                    "pad=180:320:(ow-iw)/2:(oh-ih)/2:black"
                ),
                "degraded": (
                    "crop=102:180:109:0,scale=4:8:flags=area,"
                    "scale=180:320:flags=neighbor"
                ),
            }
            outputs = {}
            for name, filtergraph in filters.items():
                output = root / f"{name}.mp4"
                rendered = subprocess.run([
                    "ffmpeg", "-y", "-v", "error", "-i", str(source),
                    "-vf", filtergraph, "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p", str(output),
                ], capture_output=True, text=True, check=False, timeout=120)
                self.assertEqual(rendered.returncode, 0, rendered.stderr)
                outputs[name] = output

            fill = build_frame_quality_report(
                [QAInput(1, outputs["fill"], 3000)],
                source=source,
                render_execution=execution(strategy="crop", expected_ratio=1.0),
                expected_width=180,
                expected_height=320,
                timeout=60,
            )
            incident = build_frame_quality_report(
                [QAInput(1, outputs["letterbox"], 3000)],
                source=source,
                render_execution=execution(strategy="crop", expected_ratio=1.0),
                expected_width=180,
                expected_height=320,
                timeout=60,
            )
            intentional_fit = build_frame_quality_report(
                [QAInput(1, outputs["letterbox"], 3000)],
                source=source,
                render_execution=execution(strategy="fit", expected_ratio=0.3125),
                expected_width=180,
                expected_height=320,
                timeout=60,
            )
            degraded = build_frame_quality_report(
                [QAInput(1, outputs["degraded"], 3000)],
                source=source,
                render_execution=execution(strategy="crop", expected_ratio=1.0),
                expected_width=180,
                expected_height=320,
                timeout=60,
            )

        self.assertEqual(fill["version"], FRAME_QUALITY_VERSION)
        self.assertEqual(fill["status"], "pass")
        self.assertEqual(fill["clips"][0]["frame_rate"]["decoded_frames"], 72)
        self.assertEqual(intentional_fit["status"], "pass")
        self.assertEqual(intentional_fit["clips"][0]["active_picture"]["summary"]["fill_samples"], 0)
        incident_codes = {item["code"] for item in incident["findings"]}
        self.assertIn("ACTIVE_PICTURE_TOO_SMALL", incident_codes)
        self.assertAlmostEqual(
            incident["clips"][0]["active_picture"]["summary"]["median_active_height_ratio"],
            0.3125,
            delta=0.02,
        )
        degraded_codes = {item["code"] for item in degraded["findings"]}
        self.assertIn("REFERENCE_QUALITY_CATASTROPHIC", degraded_codes)
        serialized = json.dumps(degraded)
        self.assertNotIn(str(source), serialized)
        self.assertNotIn("data:image", serialized)
        self.assertLess(len(serialized.encode("utf-8")), 2 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
