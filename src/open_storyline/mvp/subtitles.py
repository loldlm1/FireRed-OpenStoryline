from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
import math
import re
import subprocess
import time

from open_storyline.mvp.shorts import ShortCandidate


SUBTITLE_LAYOUT_VERSION = "subtitle_layout.v1"
CAPTION_FOOTPRINT_VERSION = "caption_footprint.v1"


class SubtitleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_ms: int
    end_ms: int
    lines: tuple[str, ...]
    character_count: int
    reading_speed_cps: float

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["lines"] = list(self.lines)
        return value


@dataclass(frozen=True)
class SubtitleStyle:
    play_res_x: int
    play_res_y: int
    font_family: str
    font_file: str
    font_size: int
    outline: int
    shadow: int
    margin_left: int
    margin_right: int
    margin_vertical: int
    alignment: int
    footer_top_ratio: float
    footer_bottom_ratio: float
    max_width_ratio: float
    max_height_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SubtitleArtifacts:
    srt_path: Path | None
    ass_path: Path | None
    cues: tuple[SubtitleCue, ...]
    style: SubtitleStyle

    def evidence(self) -> dict[str, Any]:
        return {
            "version": SUBTITLE_LAYOUT_VERSION,
            "srt": self.srt_path.name if self.srt_path else None,
            "render_ass": self.ass_path.name if self.ass_path else None,
            "cue_count": len(self.cues),
            "maximum_lines": max((len(cue.lines) for cue in self.cues), default=0),
            "maximum_reading_speed_cps": max(
                (cue.reading_speed_cps for cue in self.cues),
                default=0.0,
            ),
            "style": self.style.to_dict(),
        }


@dataclass(frozen=True)
class CaptionBounds:
    cue_index: int
    timestamp_ms: int
    x: int
    y: int
    width: int
    height: int
    width_ratio: float
    height_ratio: float
    top_ratio: float
    bottom_ratio: float
    blocker_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["blocker_codes"] = list(self.blocker_codes)
        return value


@dataclass(frozen=True)
class CaptionFootprintReport:
    width: int
    height: int
    bounds: tuple[CaptionBounds, ...]
    status: str

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(sorted({code for item in self.bounds for code in item.blocker_codes}))

    def to_dict(self) -> dict[str, Any]:
        worst = max(
            self.bounds,
            key=lambda item: (item.height_ratio, item.width_ratio),
            default=None,
        )
        return {
            "version": CAPTION_FOOTPRINT_VERSION,
            "status": self.status,
            "output": {"width": self.width, "height": self.height},
            "summary": {
                "cues_measured": len(self.bounds),
                "blocker_codes": list(self.blocker_codes),
                "worst_cue_index": worst.cue_index if worst else None,
                "maximum_width_ratio": max(
                    (item.width_ratio for item in self.bounds),
                    default=0.0,
                ),
                "maximum_height_ratio": max(
                    (item.height_ratio for item in self.bounds),
                    default=0.0,
                ),
            },
            "bounds": [item.to_dict() for item in self.bounds],
        }


def _clock(milliseconds: int, *, separator: str) -> str:
    total_seconds, millis = divmod(max(0, int(milliseconds)), 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def _split_once(text: str) -> tuple[str, str] | None:
    if len(text) < 2:
        return None
    midpoint = len(text) // 2
    spaces = [index for index, value in enumerate(text) if value.isspace()]
    if not spaces:
        return None
    split_at = min(spaces, key=lambda index: abs(index - midpoint))
    left = text[:split_at].strip()
    right = text[split_at:].strip()
    return (left, right) if left and right else None


def _layout_chunks(text: str, *, max_chars: int) -> list[str]:
    words = text.split()
    if len(words) <= 1:
        return [text[index:index + max_chars] for index in range(0, len(text), max_chars)]
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _balanced_chunks(text: str, *, target: int, max_chars: int) -> list[str]:
    target_chars = max(1, min(max_chars, int(math.ceil(len(text) / max(1, target)))))
    chunks = _layout_chunks(text, max_chars=target_chars)
    while len(chunks) < target:
        index = max(range(len(chunks)), key=lambda item: len(chunks[item]))
        split = _split_once(chunks[index])
        if split is None:
            break
        chunks[index:index + 1] = split
    return chunks


def _wrap_lines(text: str, *, max_chars_per_line: int, max_lines: int) -> tuple[str, ...]:
    chunks = _layout_chunks(text, max_chars=max_chars_per_line)
    if len(chunks) > max_lines:
        raise SubtitleError(
            "CAPTION_LINE_LIMIT_EXCEEDED",
            "caption text exceeds the configured two-line layout",
        )
    return tuple(chunks)


def build_subtitle_cues(
    *,
    clip: ShortCandidate,
    transcript_segments: Sequence[dict[str, Any]],
    max_chars_per_line: int = 32,
    max_lines: int = 2,
    max_cue_duration_ms: int = 4_000,
    max_reading_speed_cps: float = 24.0,
) -> tuple[SubtitleCue, ...]:
    if not 16 <= max_chars_per_line <= 64 or max_lines != 2:
        raise SubtitleError("CAPTION_CONFIG_INVALID", "caption line limits are invalid")
    if not 1_000 <= max_cue_duration_ms <= 8_000 or max_reading_speed_cps <= 0:
        raise SubtitleError("CAPTION_CONFIG_INVALID", "caption timing limits are invalid")

    cues: list[SubtitleCue] = []
    previous_end_ms = 0
    ordered_segments = sorted(
        transcript_segments,
        key=lambda item: (int(item.get("start") or 0), int(item.get("end") or 0)),
    )
    for segment in ordered_segments:
        source_start_ms = max(clip.start_ms, int(segment.get("start") or 0))
        source_end_ms = min(clip.end_ms, int(segment.get("end") or 0))
        text = re.sub(r"\s+", " ", str(segment.get("text") or "")).strip()
        if not text or source_end_ms - source_start_ms < 200:
            continue
        local_start_ms = max(previous_end_ms, source_start_ms - clip.start_ms)
        local_end_ms = source_end_ms - clip.start_ms
        if local_end_ms - local_start_ms < 200:
            continue
        duration_ms = local_end_ms - local_start_ms
        max_chars_per_cue = max_chars_per_line * max_lines
        compact_character_count = len(re.sub(r"\s+", "", text))
        overall_reading_speed = compact_character_count / (duration_ms / 1000)
        if overall_reading_speed > max_reading_speed_cps:
            raise SubtitleError(
                "CAPTION_READING_SPEED_EXCEEDED",
                "caption reading speed exceeds the configured safe limit",
            )
        target_count = max(
            int(math.ceil(len(text) / max_chars_per_cue)),
            int(math.ceil(duration_ms / max_cue_duration_ms)),
        )
        chunks = _balanced_chunks(
            text,
            target=target_count,
            max_chars=max_chars_per_cue,
        )
        weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
        total_weight = sum(weights)
        cue_durations = [
            min(
                max_cue_duration_ms,
                max(200, int(round(duration_ms * weight / total_weight))),
            )
            for weight in weights
        ]
        cursor = local_start_ms
        for chunk, cue_duration in zip(chunks, cue_durations):
            end_ms = min(local_end_ms, cursor + cue_duration)
            lines = _wrap_lines(
                chunk,
                max_chars_per_line=max_chars_per_line,
                max_lines=max_lines,
            )
            character_count = len(re.sub(r"\s+", "", chunk))
            actual_duration_ms = max(1, end_ms - cursor)
            reading_speed = character_count / (actual_duration_ms / 1000)
            if reading_speed > max_reading_speed_cps:
                raise SubtitleError(
                    "CAPTION_READING_SPEED_EXCEEDED",
                    "caption reading speed exceeds the configured safe limit",
                )
            cues.append(SubtitleCue(
                index=len(cues) + 1,
                start_ms=cursor,
                end_ms=end_ms,
                lines=lines,
                character_count=character_count,
                reading_speed_cps=round(reading_speed, 3),
            ))
            cursor = end_ms
        previous_end_ms = local_end_ms
    if len(cues) > 128:
        raise SubtitleError("CAPTION_CUE_LIMIT_EXCEEDED", "caption cue count exceeds 128")
    return tuple(cues)


def _resolve_font(font_family: str) -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["fc-match", font_family, "-f", "%{family}|%{file}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return font_family, ""
    if result.returncode != 0 or "|" not in result.stdout:
        return font_family, ""
    family, filename = result.stdout.strip().split("|", 1)
    clean_family = re.sub(r"[\r\n,]", " ", family).strip()[:120] or font_family
    return clean_family, Path(filename).name[:160]


def build_subtitle_style(
    *,
    width: int,
    height: int,
    font_family: str = "DejaVu Sans",
) -> SubtitleStyle:
    if width < 128 or height < 128:
        raise SubtitleError("CAPTION_DIMENSIONS_INVALID", "caption dimensions are invalid")
    resolved_family, font_file = _resolve_font(font_family)
    return SubtitleStyle(
        play_res_x=int(width),
        play_res_y=int(height),
        font_family=resolved_family,
        font_file=font_file,
        font_size=max(18, int(round(height * 0.024))),
        outline=max(2, int(round(height * 0.0015))),
        shadow=0,
        margin_left=max(20, int(round(width * 0.08))),
        margin_right=max(20, int(round(width * 0.08))),
        margin_vertical=max(24, int(round(height * 0.052))),
        alignment=2,
        footer_top_ratio=0.72,
        footer_bottom_ratio=0.96,
        max_width_ratio=0.9,
        max_height_ratio=0.22,
    )


def _ass_escape(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def _ass_clock(milliseconds: int) -> str:
    return _clock(milliseconds, separator=".")[:-1]


def write_subtitle_artifacts(
    destination: str | Path,
    *,
    clip: ShortCandidate,
    transcript_segments: Sequence[dict[str, Any]],
    width: int,
    height: int,
) -> SubtitleArtifacts:
    destination_path = Path(destination)
    if destination_path.suffix.lower() != ".srt":
        raise SubtitleError("CAPTION_PATH_INVALID", "subtitle destination must be an SRT file")
    cues = build_subtitle_cues(
        clip=clip,
        transcript_segments=transcript_segments,
    )
    style = build_subtitle_style(width=width, height=height)
    if not cues:
        return SubtitleArtifacts(None, None, (), style)
    destination_path.write_text(
        "\n\n".join(
            f"{cue.index}\n{_clock(cue.start_ms, separator=',')} --> "
            f"{_clock(cue.end_ms, separator=',')}\n" + "\n".join(cue.lines)
            for cue in cues
        ) + "\n",
        encoding="utf-8",
    )
    ass_path = destination_path.with_suffix(".render.ass")
    style_line = (
        "Style: Default,"
        f"{style.font_family},{style.font_size},&H00FFFFFF,&H00FFFFFF,"
        "&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{style.outline},{style.shadow},{style.alignment},"
        f"{style.margin_left},{style.margin_right},{style.margin_vertical},1"
    )
    events = [
        "Dialogue: 0,"
        f"{_ass_clock(cue.start_ms)},"
        f"{_ass_clock(cue.end_ms)},"
        "Default,,0,0,0,,"
        + r"\N".join(_ass_escape(line) for line in cue.lines)
        for cue in cues
    ]
    ass_path.write_text(
        "\n".join([
            "[Script Info]",
            f"ScriptType: v4.00+",
            f"PlayResX: {style.play_res_x}",
            f"PlayResY: {style.play_res_y}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            "",
            "[V4+ Styles]",
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
            style_line,
            "",
            "[Events]",
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
            *events,
            "",
        ]),
        encoding="utf-8",
    )
    return SubtitleArtifacts(destination_path, ass_path, cues, style)


_BBOX_PATTERN = re.compile(
    r"x1:(?P<x1>\d+)\s+x2:(?P<x2>\d+)\s+y1:(?P<y1>\d+)\s+y2:(?P<y2>\d+)\s+"
    r"w:(?P<width>\d+)\s+h:(?P<height>\d+)"
)


def measure_caption_footprint(
    artifacts: SubtitleArtifacts,
    *,
    width: int,
    height: int,
    timeout_per_cue: float = 30.0,
    total_timeout: float = 120.0,
) -> CaptionFootprintReport:
    if not artifacts.cues or artifacts.ass_path is None:
        return CaptionFootprintReport(width, height, (), "empty")
    if len(artifacts.cues) > 128:
        raise SubtitleError("CAPTION_CUE_LIMIT_EXCEEDED", "caption cue count exceeds 128")
    bounds: list[CaptionBounds] = []
    deadline = time.monotonic() + total_timeout
    for cue in artifacts.cues:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SubtitleError(
                "CAPTION_FOOTPRINT_TIMEOUT",
                "caption footprint measurement exceeded its total time budget",
            )
        timestamp_ms = cue.start_ms + (cue.end_ms - cue.start_ms) // 2
        filtergraph = (
            f"settb=1/1000,setpts=PTS+{timestamp_ms}/TB/1000,"
            f"ass=filename='{artifacts.ass_path.name}',bbox=min_val=16"
        )
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-v", "info",
                    "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=1:d=1",
                    "-vf", filtergraph,
                    "-frames:v", "1", "-f", "null", "-",
                ],
                cwd=artifacts.ass_path.parent,
                capture_output=True,
                text=True,
                check=False,
                timeout=min(timeout_per_cue, remaining),
            )
        except FileNotFoundError as exc:
            raise SubtitleError("CAPTION_FFMPEG_UNAVAILABLE", "FFmpeg is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise SubtitleError("CAPTION_FOOTPRINT_TIMEOUT", "caption footprint measurement timed out") from exc
        matches = list(_BBOX_PATTERN.finditer(result.stderr or ""))
        if result.returncode != 0 or not matches:
            raise SubtitleError(
                "CAPTION_FOOTPRINT_UNMEASURABLE",
                "FFmpeg could not measure the rendered caption bounds",
            )
        match = matches[-1]
        x = int(match.group("x1"))
        y = int(match.group("y1"))
        measured_width = int(match.group("width"))
        measured_height = int(match.group("height"))
        width_ratio = measured_width / width
        height_ratio = measured_height / height
        top_ratio = y / height
        bottom_ratio = (y + measured_height) / height
        blockers: list[str] = []
        if top_ratio < artifacts.style.footer_top_ratio:
            blockers.append("CAPTION_OUTSIDE_FOOTER_SAFE_ZONE")
        if bottom_ratio > artifacts.style.footer_bottom_ratio:
            blockers.append("CAPTION_OUTSIDE_FRAME_SAFE_ZONE")
        if width_ratio > artifacts.style.max_width_ratio:
            blockers.append("CAPTION_WIDTH_EXCEEDED")
        if height_ratio > artifacts.style.max_height_ratio:
            blockers.append("CAPTION_HEIGHT_EXCEEDED")
        bounds.append(CaptionBounds(
            cue_index=cue.index,
            timestamp_ms=timestamp_ms,
            x=x,
            y=y,
            width=measured_width,
            height=measured_height,
            width_ratio=round(width_ratio, 6),
            height_ratio=round(height_ratio, 6),
            top_ratio=round(top_ratio, 6),
            bottom_ratio=round(bottom_ratio, 6),
            blocker_codes=tuple(blockers),
        ))
    status = "blocked" if any(item.blocker_codes for item in bounds) else "pass"
    return CaptionFootprintReport(width, height, tuple(bounds), status)
