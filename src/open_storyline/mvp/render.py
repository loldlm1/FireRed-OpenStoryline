from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
import base64
import json
import re
import subprocess

from open_storyline.mvp.shorts import ShortCandidate


class RenderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int
    height: int
    has_audio: bool


@dataclass(frozen=True)
class RenderSettings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    preset: str = "veryfast"
    crf: int = 23
    timeout: float = 1800.0


@dataclass(frozen=True)
class RenderedShort:
    video_path: Path
    subtitle_path: Path | None
    clip: ShortCandidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "video": self.video_path.name,
            "subtitles": self.subtitle_path.name if self.subtitle_path else None,
            "clip": self.clip.to_dict(),
        }


def _reason(value: str, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[-limit:]


def probe_media(path: str | Path) -> MediaInfo:
    source = Path(path)
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(source),
        ], capture_output=True, text=True, check=False, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RenderError("FFPROBE_UNAVAILABLE", str(exc)) from exc
    if result.returncode != 0:
        raise RenderError("MEDIA_PROBE_FAILED", _reason(result.stderr))
    try:
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        video = next(item for item in streams if item.get("codec_type") == "video")
        duration = float((payload.get("format") or {}).get("duration") or video.get("duration"))
        info = MediaInfo(
            duration_ms=int(round(duration * 1000)),
            width=int(video["width"]),
            height=int(video["height"]),
            has_audio=any(item.get("codec_type") == "audio" for item in streams),
        )
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        raise RenderError("MEDIA_PROBE_INVALID", "FFprobe returned incomplete media metadata") from exc
    if info.duration_ms <= 0 or info.width <= 0 or info.height <= 0:
        raise RenderError("MEDIA_PROBE_INVALID", "media duration or dimensions are invalid")
    return info


def extract_frame_data_urls(
    source: str | Path,
    *,
    duration_ms: int,
    count: int,
    max_width: int = 512,
) -> list[str]:
    if count <= 0:
        return []
    positions = [duration_ms * (index + 1) / (count + 1) for index in range(count)]
    frames: list[str] = []
    for position_ms in positions:
        try:
            result = subprocess.run([
                "ffmpeg", "-v", "error", "-ss", f"{position_ms / 1000:.3f}",
                "-i", str(source), "-frames:v", "1", "-vf", f"scale={max_width}:-2",
                "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
            ], capture_output=True, check=False, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise RenderError("FRAME_EXTRACTION_FAILED", str(exc)) from exc
        if result.returncode != 0 or not result.stdout:
            raise RenderError("FRAME_EXTRACTION_FAILED", _reason(result.stderr.decode("utf-8", "ignore")))
        frames.append("data:image/jpeg;base64," + base64.b64encode(result.stdout).decode("ascii"))
    return frames


def _srt_clock(milliseconds: int) -> str:
    total_seconds, millis = divmod(max(0, int(milliseconds)), 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def write_clip_subtitles(
    destination: str | Path,
    *,
    clip: ShortCandidate,
    transcript_segments: Sequence[dict[str, Any]],
) -> Path | None:
    blocks: list[str] = []
    for segment in transcript_segments:
        start = max(clip.start_ms, int(segment.get("start") or 0))
        end = min(clip.end_ms, int(segment.get("end") or 0))
        text = re.sub(r"\s+", " ", str(segment.get("text") or "")).strip()
        if not text or end - start < 200:
            continue
        local_start = start - clip.start_ms
        local_end = end - clip.start_ms
        blocks.append(
            f"{len(blocks) + 1}\n{_srt_clock(local_start)} --> {_srt_clock(local_end)}\n{text}\n"
        )
    if not blocks:
        return None
    path = Path(destination)
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


class CPUShortRenderer:
    def __init__(self, settings: RenderSettings | None = None) -> None:
        self.settings = settings or RenderSettings()

    def render(
        self,
        *,
        source: str | Path,
        clip: ShortCandidate,
        transcript_segments: Sequence[dict[str, Any]],
        destination_dir: str | Path,
        index: int,
    ) -> RenderedShort:
        output_dir = Path(destination_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"short-{index:02d}"
        video_path = output_dir / f"{stem}.mp4"
        subtitle_path = write_clip_subtitles(
            output_dir / f"{stem}.srt",
            clip=clip,
            transcript_segments=transcript_segments,
        )
        settings = self.settings
        filters = [
            f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase",
            f"crop={settings.width}:{settings.height}",
            "setsar=1",
        ]
        if subtitle_path is not None:
            style = "FontName=DejaVu Sans,FontSize=20,Outline=2,Shadow=1,Alignment=2,MarginV=100"
            filters.append(f"subtitles=filename='{subtitle_path.name}':force_style='{style}'")
        command = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{clip.start_ms / 1000:.3f}",
            "-t", f"{clip.duration_ms / 1000:.3f}",
            "-i", str(Path(source).resolve()),
            "-map", "0:v:0", "-map", "0:a?",
            "-vf", ",".join(filters),
            "-r", str(settings.fps),
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(video_path.name),
        ]
        try:
            result = subprocess.run(
                command,
                cwd=output_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=settings.timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise RenderError("VIDEO_RENDER_FAILED", str(exc)) from exc
        if result.returncode != 0 or not video_path.is_file():
            raise RenderError("VIDEO_RENDER_FAILED", _reason(result.stderr))
        return RenderedShort(video_path=video_path, subtitle_path=subtitle_path, clip=clip)

    def render_plan(
        self,
        *,
        source: str | Path,
        clips: Sequence[ShortCandidate],
        transcript_segments: Sequence[dict[str, Any]],
        destination_dir: str | Path,
    ) -> list[RenderedShort]:
        return [
            self.render(
                source=source,
                clip=clip,
                transcript_segments=transcript_segments,
                destination_dir=destination_dir,
                index=index,
            )
            for index, clip in enumerate(clips, start=1)
        ]
