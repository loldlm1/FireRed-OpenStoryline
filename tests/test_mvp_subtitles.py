from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import unittest

from open_storyline.mvp.shorts import ShortCandidate
from open_storyline.mvp.subtitles import (
    SubtitleError,
    build_subtitle_cues,
    build_subtitle_style,
    measure_caption_footprint,
    write_subtitle_artifacts,
)


class SubtitleLayoutTests(unittest.TestCase):
    def setUp(self):
        self.clip = ShortCandidate(1_000, 9_000, "Synthetic", "Hook", "Reason", 1.0)

    def test_long_segments_split_without_overlap_or_text_loss(self):
        text = (
            "A complete caption sentence needs deterministic wrapping and timing "
            "without losing any of the spoken words from the transcript."
        )
        cues = build_subtitle_cues(
            clip=self.clip,
            transcript_segments=[{"start": 1_200, "end": 8_500, "text": text}],
        )

        self.assertGreater(len(cues), 1)
        self.assertEqual(
            " ".join(cue.text for cue in cues),
            text,
        )
        self.assertTrue(all(len(cue.lines) <= 2 for cue in cues))
        self.assertTrue(all(cue.end_ms - cue.start_ms <= 4_000 for cue in cues))
        self.assertTrue(all(cue.reading_speed_cps <= 24 for cue in cues))
        self.assertTrue(all(left.end_ms <= right.start_ms for left, right in zip(cues, cues[1:])))

    def test_impossible_reading_speed_fails_closed(self):
        with self.assertRaises(SubtitleError) as caught:
            build_subtitle_cues(
                clip=self.clip,
                transcript_segments=[{
                    "start": 1_000,
                    "end": 2_000,
                    "text": "This caption contains far too many characters for one second.",
                }],
            )
        self.assertEqual(caught.exception.code, "CAPTION_READING_SPEED_EXCEEDED")

    def test_style_scales_with_output_resolution(self):
        full = build_subtitle_style(width=1080, height=1920)
        smaller = build_subtitle_style(width=720, height=1280)

        self.assertEqual((full.play_res_x, full.play_res_y), (1080, 1920))
        self.assertEqual((smaller.play_res_x, smaller.play_res_y), (720, 1280))
        self.assertAlmostEqual(full.font_size / smaller.font_size, 1.5, delta=0.08)
        self.assertAlmostEqual(full.margin_vertical / smaller.margin_vertical, 1.5, delta=0.08)

    def test_srt_is_ordered_and_ass_uses_centiseconds_and_safe_line_breaks(self):
        with TemporaryDirectory() as directory:
            artifacts = write_subtitle_artifacts(
                Path(directory) / "short-01.srt",
                clip=self.clip,
                transcript_segments=[{
                    "start": 1_123,
                    "end": 5_678,
                    "text": "First {safe} caption with enough words to wrap cleanly across lines.",
                }],
                width=1080,
                height=1920,
            )

            srt = artifacts.srt_path.read_text(encoding="utf-8")
            ass = artifacts.ass_path.read_text(encoding="utf-8")
            self.assertIn("00:00:00,123 -->", srt)
            self.assertIn("Dialogue: 0,00:00:00.12,", ass)
            self.assertIn(r"\{safe\}", ass)
            self.assertNotIn(r"\\N", ass)


@unittest.skipUnless(
    shutil.which("ffmpeg"),
    "FFmpeg with libass is required",
)
class CaptionFootprintTests(unittest.TestCase):
    def test_corrected_caption_passes_and_empty_caption_is_safe(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip = ShortCandidate(0, 5_000, "Synthetic", "Hook", "Reason", 1.0)
            artifacts = write_subtitle_artifacts(
                root / "caption.srt",
                clip=clip,
                transcript_segments=[{
                    "start": 500,
                    "end": 3_500,
                    "text": "A representative footer caption stays below the subject.",
                }],
                width=1080,
                height=1920,
            )
            report = measure_caption_footprint(artifacts, width=1080, height=1920)
            self.assertEqual(report.status, "pass")
            self.assertTrue(report.bounds)
            self.assertTrue(all(item.top_ratio >= 0.72 for item in report.bounds))

            empty = write_subtitle_artifacts(
                root / "empty.srt",
                clip=clip,
                transcript_segments=[],
                width=1080,
                height=1920,
            )
            empty_report = measure_caption_footprint(empty, width=1080, height=1920)
            self.assertEqual(empty_report.status, "empty")
            self.assertEqual(empty_report.bounds, ())

    def test_incident_style_fails_footer_safe_zone(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip = ShortCandidate(0, 4_000, "Synthetic", "Hook", "Reason", 1.0)
            artifacts = write_subtitle_artifacts(
                root / "legacy.srt",
                clip=clip,
                transcript_segments=[{
                    "start": 0,
                    "end": 3_000,
                    "text": "The incident subtitle occupies the wrong coordinate system.",
                }],
                width=1080,
                height=1920,
            )
            artifacts.ass_path.write_text(
                "\n".join([
                    "[Script Info]",
                    "ScriptType: v4.00+",
                    "PlayResX: 384",
                    "PlayResY: 288",
                    "",
                    "[V4+ Styles]",
                    "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
                    "Style: Default,DejaVu Sans,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,10,10,100,1",
                    "",
                    "[Events]",
                    "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
                    "Dialogue: 0,00:00:00.00,00:00:03.00,Default,,0,0,0,,Incident caption",
                    "",
                ]),
                encoding="utf-8",
            )

            report = measure_caption_footprint(artifacts, width=1080, height=1920)
            self.assertEqual(report.status, "blocked")
            self.assertIn("CAPTION_OUTSIDE_FOOTER_SAFE_ZONE", report.blocker_codes)


if __name__ == "__main__":
    unittest.main()
