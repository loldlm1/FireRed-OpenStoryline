from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import subprocess
import unittest

from open_storyline.mvp.render import CPUShortRenderer, RenderSettings, probe_media
from open_storyline.mvp.shorts import ShortCandidate


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class CPUShortRendererTests(unittest.TestCase):
    def test_renders_vertical_h264_short_with_subtitles(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.mp4"
            generated = subprocess.run([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
                "-t", "19", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
            ], capture_output=True, text=True, check=False, timeout=120)
            self.assertEqual(generated.returncode, 0, generated.stderr)

            clip = ShortCandidate(
                start_ms=0,
                end_ms=18_000,
                title="Synthetic",
                hook="Test",
                reason="Smoke test",
                score=1.0,
            )
            renderer = CPUShortRenderer(RenderSettings(
                width=180,
                height=320,
                fps=24,
                preset="ultrafast",
                crf=30,
                timeout=120,
            ))
            rendered = renderer.render(
                source=source,
                clip=clip,
                transcript_segments=[
                    {"start": 500, "end": 3_000, "text": "Hola mundo"},
                    {"start": 10_000, "end": 15_000, "text": "Segundo subtítulo"},
                ],
                destination_dir=root / "output",
                index=1,
            )

            info = probe_media(rendered.video_path)
            self.assertEqual((info.width, info.height), (180, 320))
            self.assertGreaterEqual(info.duration_ms, 17_800)
            self.assertLessEqual(info.duration_ms, 18_300)
            self.assertTrue(info.has_audio)
            self.assertIsNotNone(rendered.subtitle_path)
            self.assertIn("Hola mundo", rendered.subtitle_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
