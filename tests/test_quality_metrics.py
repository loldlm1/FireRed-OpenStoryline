from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location(
    "openstoryline_quality_metrics",
    ROOT / "scripts" / "quality_metrics.py",
)
assert SPEC is not None and SPEC.loader is not None
QUALITY_METRICS = module_from_spec(SPEC)
SPEC.loader.exec_module(QUALITY_METRICS)


class ReferenceQualityMetricsTests(unittest.TestCase):
    def test_merges_zero_based_vif_with_one_based_metrics(self):
        frames = QUALITY_METRICS._merge_frames(
            {
                "vif": [{"n": 0, "scale_0": 0.91}],
                "vmaf": [{"n": 1, "vmaf": 98.5}],
                "ssim": [{"n": 1, "ssim_avg": 0.99}],
                "psnr": [{"n": 1, "psnr_avg": 42.0}],
            },
            [{"n": 1, "xpsnr_min": 40.0}],
            frame_rate=24,
            segments=[{
                "clip_index": 0,
                "segment_id": "segment-1",
                "operation": "crop",
                "strategy": "crop",
                "start_ms": 0,
                "end_ms": 2000,
            }],
        )

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["frame"], 1)
        self.assertEqual(frames[0]["timestamp_ms"], 0)
        self.assertEqual(frames[0]["vif_scale_0"], 0.91)
        self.assertEqual(frames[0]["vmaf"], 98.5)
        self.assertEqual(frames[0]["operation"], "crop")

    def test_execution_mapping_uses_the_synthetic_allowlist_fixture(self):
        segments = QUALITY_METRICS._safe_execution(
            ROOT / "tests" / "fixtures" / "quality" / "render-execution.json"
        )

        self.assertEqual(segments, [{
            "clip_index": 0,
            "segment_id": "segment-1",
            "operation": "crop",
            "strategy": "crop",
            "start_ms": 0,
            "end_ms": 2000,
        }])


if __name__ == "__main__":
    unittest.main()
