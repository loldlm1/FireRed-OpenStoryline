from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import subprocess
import unittest

from types import SimpleNamespace

from open_storyline.mvp.edit_plan import (
    AssetRequest,
    ClipEditPlan,
    EditPlan,
    EditSegment,
    FocalTarget,
    LayoutSpec,
    OverlaySpec,
    TimeWindow,
    TransitionSpec,
)
from open_storyline.mvp.render import AgenticShortRenderer, CPUShortRenderer, RenderSettings, probe_media
from open_storyline.mvp.shorts import ShortCandidate
from open_storyline.mvp.visual_understanding import NormalizedBox, RegionObservation


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

    def test_agentic_renderer_keeps_a_right_side_subject_visible_in_one_encode(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "wide-subjects.mp4"
            generated = subprocess.run([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i",
                "color=c=black:size=640x360:rate=24:d=4,"
                "drawbox=x=0:y=0:w=240:h=360:color=red:t=fill,"
                "drawbox=x=400:y=0:w=240:h=360:color=blue:t=fill",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=4",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
            ], capture_output=True, text=True, check=False, timeout=120)
            self.assertEqual(generated.returncode, 0, generated.stderr)

            selected = ShortCandidate(0, 4000, "Synthetic", "Hook", "Reason", 1.0)
            edit_plan = EditPlan(
                planner_version="test.v1",
                source_duration_ms=4000,
                requested_capabilities=("crop", "hard_cut", "subtitles"),
                clips=(ClipEditPlan(
                    clip_index=1,
                    source_window=TimeWindow(start_ms=0, end_ms=4000),
                    output_name="short-01.mp4",
                    segments=(EditSegment(
                        id="segment-1",
                        source_window=TimeWindow(start_ms=0, end_ms=4000),
                        timeline_window=TimeWindow(start_ms=0, end_ms=4000),
                        layout=LayoutSpec(
                            mode="crop",
                            focal_target=FocalTarget(region_id="right-subject"),
                            fallback="fit",
                        ),
                        reason="keep the blue subject visible",
                        evidence_ids=("right-subject",),
                    ),),
                ),),
            )
            visual = SimpleNamespace(
                frame_manifest={"frames": [{"id": "frame-001", "timestamp_ms": 1000}]},
                regions=(RegionObservation(
                    id="right-subject",
                    frame_id="frame-001",
                    role="speaker",
                    bbox=NormalizedBox(x=0.7, y=0.1, width=0.2, height=0.8),
                    confidence=0.9,
                    salience=0.9,
                    description="blue subject",
                ),),
                tracks=(),
            )
            renderer = AgenticShortRenderer(RenderSettings(
                width=180,
                height=320,
                fps=24,
                preset="ultrafast",
                crf=30,
                timeout=120,
            ))
            result = renderer.render_plan(
                source=source,
                edit_plan=edit_plan,
                selected_clips=[selected],
                visual_understanding=visual,
                transcript_segments=[{"start": 200, "end": 1500, "text": "Target visible"}],
                destination_dir=root / "agentic",
            )

            rendered = result.rendered[0]
            info = probe_media(rendered.video_path)
            self.assertEqual((info.width, info.height), (180, 320))
            self.assertGreaterEqual(info.duration_ms, 3800)
            self.assertLessEqual(info.duration_ms, 4200)
            self.assertTrue(info.has_audio)
            self.assertEqual(result.execution["summary"]["encodes"], 1)
            self.assertEqual(result.execution["summary"]["fallbacks"], 0)
            self.assertNotIn(str(source), result.execution["clips"][0]["filtergraph"])

            pixel = subprocess.run([
                "ffmpeg", "-v", "error", "-ss", "1", "-i", str(rendered.video_path),
                "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
                "-pix_fmt", "rgb24", "pipe:1",
            ], capture_output=True, check=False, timeout=120)
            self.assertEqual(pixel.returncode, 0, pixel.stderr.decode("utf-8", "ignore"))
            red, _green, blue = pixel.stdout[:3]
            self.assertGreater(blue, red + 40)

    def test_agentic_renderer_executes_timeline_creative_primitives_in_one_encode(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "creative-source.mp4"
            asset = root / "overlay.png"
            generated = subprocess.run([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=6",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
            ], capture_output=True, text=True, check=False, timeout=120)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            generated_asset = subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=yellow:size=120x120", "-frames:v", "1", str(asset),
            ], capture_output=True, text=True, check=False, timeout=120)
            self.assertEqual(generated_asset.returncode, 0, generated_asset.stderr)

            selected = ShortCandidate(0, 6000, "Synthetic", "Hook", "Reason", 1.0)
            edit_plan = EditPlan(
                planner_version="test.v1",
                source_duration_ms=6000,
                requested_capabilities=(
                    "crop", "fit", "focus_zoom", "source_cutaway", "image_overlay",
                    "pip", "text_emphasis", "hard_cut", "fade", "xfade", "subtitles",
                ),
                clips=(ClipEditPlan(
                    clip_index=1,
                    source_window=TimeWindow(start_ms=0, end_ms=6000),
                    output_name="short-01.mp4",
                    segments=(
                        EditSegment(
                            id="source-cutaway",
                            source_window=TimeWindow(start_ms=0, end_ms=2000),
                            timeline_window=TimeWindow(start_ms=0, end_ms=2000),
                            layout=LayoutSpec(mode="source"),
                            overlays=(OverlaySpec(
                                id="presenter-pip",
                                kind="pip",
                                source_window=TimeWindow(start_ms=4000, end_ms=5500),
                                timeline_window=TimeWindow(start_ms=250, end_ms=1750),
                                position="top_right",
                                width_ratio=0.3,
                                transition_ms=150,
                            ),),
                            reason="open on the full source with a supporting PiP",
                        ),
                        EditSegment(
                            id="focus-zoom",
                            source_window=TimeWindow(start_ms=2000, end_ms=4000),
                            timeline_window=TimeWindow(start_ms=2000, end_ms=4000),
                            layout=LayoutSpec(
                                mode="crop",
                                focal_target=FocalTarget(region_id="focus-target"),
                                fallback="fit",
                                max_zoom=1.5,
                            ),
                            transition_in=TransitionSpec(kind="fade", duration_ms=400),
                            reason="zoom toward the validated focal target",
                            evidence_ids=("focus-target",),
                        ),
                        EditSegment(
                            id="visual-emphasis",
                            source_window=TimeWindow(start_ms=3500, end_ms=6000),
                            timeline_window=TimeWindow(start_ms=3500, end_ms=6000),
                            layout=LayoutSpec(mode="fit"),
                            transition_in=TransitionSpec(kind="xfade", duration_ms=500),
                            overlays=(
                                OverlaySpec(
                                    id="supporting-image",
                                    kind="image",
                                    timeline_window=TimeWindow(start_ms=4100, end_ms=5500),
                                    asset_id="asset-1",
                                    position="center",
                                    width_ratio=0.4,
                                    transition_ms=100,
                                    z_index=5,
                                ),
                                OverlaySpec(
                                    id="hook-text",
                                    kind="text",
                                    timeline_window=TimeWindow(start_ms=4000, end_ms=5200),
                                    text="Key idea",
                                    position="top_left",
                                    transition_ms=100,
                                    z_index=20,
                                ),
                            ),
                            reason="support the conclusion with visual and text emphasis",
                        ),
                    ),
                    asset_requests=(AssetRequest(
                        id="asset-1",
                        kind="generated_image",
                        provider="9router",
                        timeline_window=TimeWindow(start_ms=4100, end_ms=5500),
                        visual_gap="the source needs one supporting still",
                        purpose="support the conclusion",
                        rationale="a bounded still clarifies the final idea",
                        prompt="an original yellow editorial card",
                    ),),
                ),),
            )
            visual = SimpleNamespace(
                frame_manifest={"frames": [{"id": "frame-001", "timestamp_ms": 3000}]},
                regions=(RegionObservation(
                    id="focus-target",
                    frame_id="frame-001",
                    role="object",
                    bbox=NormalizedBox(x=0.68, y=0.2, width=0.16, height=0.4),
                    confidence=0.95,
                    salience=0.95,
                    description="validated focus target",
                ),),
                tracks=(),
            )
            result = AgenticShortRenderer(RenderSettings(
                width=180,
                height=320,
                fps=24,
                preset="ultrafast",
                crf=30,
                timeout=120,
            )).render_plan(
                source=source,
                edit_plan=edit_plan,
                selected_clips=[selected],
                visual_understanding=visual,
                transcript_segments=[{"start": 300, "end": 1800, "text": "Creative timeline"}],
                destination_dir=root / "agentic",
                resolved_assets={"asset-1": asset},
            )

            rendered = result.rendered[0]
            info = probe_media(rendered.video_path)
            self.assertEqual((info.width, info.height), (180, 320))
            self.assertGreaterEqual(info.duration_ms, 5800)
            self.assertLessEqual(info.duration_ms, 6200)
            self.assertTrue(info.has_audio)
            self.assertEqual(result.execution["summary"]["encodes"], 1)
            execution = result.execution["clips"][0]
            self.assertLess(execution["segments"][1]["crop"]["width"], 180)
            self.assertEqual(
                [item["id"] for item in execution["segments"][2]["overlays"]],
                ["supporting-image", "hook-text"],
            )
            for expected in ("drawtext=", "overlay=", "xfade=", "acrossfade=", "fade=t=in"):
                self.assertIn(expected, execution["filtergraph"])
            self.assertLess(execution["filtergraph_length"], 10_000)
            self.assertEqual(execution["asset_ids"], ["asset-1"])

            pixel = subprocess.run([
                "ffmpeg", "-v", "error", "-ss", "4.6", "-i", str(rendered.video_path),
                "-frames:v", "1", "-vf", "crop=2:2:89:159,scale=1:1", "-f", "rawvideo",
                "-pix_fmt", "rgb24", "pipe:1",
            ], capture_output=True, check=False, timeout=120)
            self.assertEqual(pixel.returncode, 0, pixel.stderr.decode("utf-8", "ignore"))
            red, green, blue = pixel.stdout[:3]
            self.assertGreater(red, blue + 80)
            self.assertGreater(green, blue + 80)


if __name__ == "__main__":
    unittest.main()
