from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import subprocess
import unittest
from unittest.mock import patch

from open_storyline.mvp.scene_boundaries import (
    build_scene_boundaries,
    detect_scene_boundaries,
    parse_scene_times_ms,
)


class SceneBoundaryTests(unittest.TestCase):
    def test_parses_deduplicates_and_builds_bounded_intervals(self):
        log = "pts_time:1.200 pts_time:1.200 pts_time:4.500 pts_time:nan"
        self.assertEqual(parse_scene_times_ms(log), [1200, 4500])

        report = build_scene_boundaries(
            [1200, 1500, 4500, 9900],
            source_duration_ms=10_000,
            threshold=0.35,
            min_scene_duration_ms=1000,
        )

        self.assertEqual(report.boundaries_ms, (1200, 4500))
        self.assertEqual(report.scenes[0].start_ms, 0)
        self.assertEqual(report.scenes[-1].end_ms, 10_000)
        self.assertTrue(all(scene.duration_ms > 0 for scene in report.scenes))

    def test_caps_dense_output_with_an_explicit_warning(self):
        report = build_scene_boundaries(
            list(range(1000, 10_000, 1000)),
            source_duration_ms=10_000,
            threshold=0.2,
            min_scene_duration_ms=100,
            max_scenes=4,
        )

        self.assertEqual(len(report.scenes), 4)
        self.assertEqual(report.warnings[0]["code"], "SCENE_BOUNDARIES_CAPPED")

        with self.assertRaisesRegex(RuntimeError, "SCENE_BOUNDARY_INVALID"):
            build_scene_boundaries(
                [float("nan")],
                source_duration_ms=10_000,
                threshold=0.2,
            )

    def test_detection_uses_ffmpeg_log_without_persisting_source_path(self):
        completed = SimpleNamespace(returncode=0, stderr="pts_time:2.000", stdout="")
        with patch("open_storyline.mvp.scene_boundaries.subprocess.run", return_value=completed) as run:
            report = detect_scene_boundaries(
                "/private/job/input.mp4",
                source_duration_ms=5000,
            )

        self.assertIn("/private/job/input.mp4", run.call_args.args[0])
        self.assertNotIn("/private/job/input.mp4", str(report.to_dict()))
        self.assertEqual(report.boundaries_ms, (2000,))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_detects_a_real_synthetic_scene_change_deterministically(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "scenes.mp4"
            generated = subprocess.run([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=red:size=320x180:rate=24:d=2",
                "-f", "lavfi", "-i", "color=c=blue:size=320x180:rate=24:d=2",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", str(source),
            ], capture_output=True, text=True, check=False, timeout=120)
            self.assertEqual(generated.returncode, 0, generated.stderr)

            first = detect_scene_boundaries(
                source,
                source_duration_ms=4000,
                threshold=0.2,
                min_scene_duration_ms=500,
            )
            second = detect_scene_boundaries(
                source,
                source_duration_ms=4000,
                threshold=0.2,
                min_scene_duration_ms=500,
            )

            self.assertEqual(first.boundaries_ms, second.boundaries_ms)
            self.assertTrue(any(1800 <= item <= 2200 for item in first.boundaries_ms))


if __name__ == "__main__":
    unittest.main()
