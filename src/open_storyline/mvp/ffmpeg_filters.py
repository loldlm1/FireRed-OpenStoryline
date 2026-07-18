from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence
import re


MAX_FILTERGRAPH_LENGTH = 65_536


class FilterGraphError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def validate_output_dimensions(width: int, height: int) -> tuple[int, int]:
    if not 128 <= int(width) <= 4320 or not 128 <= int(height) <= 4320:
        raise FilterGraphError(
            "FILTER_DIMENSIONS_INVALID",
            "output dimensions must be between 128 and 4320 pixels",
        )
    if int(width) % 2 or int(height) % 2:
        raise FilterGraphError(
            "FILTER_DIMENSIONS_INVALID",
            "output dimensions must be even for H.264 rendering",
        )
    return int(width), int(height)


def validate_subtitle_filename(value: str | Path) -> str:
    name = str(value)
    if Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.srt", name):
        raise FilterGraphError(
            "FILTER_SUBTITLE_PATH_INVALID",
            "subtitle filename must be a server-generated SRT basename",
        )
    return name


def crop_scale_filter(crop: Any, *, output_width: int, output_height: int) -> str:
    width, height = validate_output_dimensions(output_width, output_height)
    values = (int(crop.width), int(crop.height), int(crop.x), int(crop.y))
    if min(values[:2]) <= 0 or min(values[2:]) < 0:
        raise FilterGraphError("FILTER_CROP_INVALID", "crop dimensions and offsets are invalid")
    return f"crop={values[0]}:{values[1]}:{values[2]}:{values[3]},scale={width}:{height}"


def fit_filter(*, output_width: int, output_height: int, color: str = "black") -> str:
    width, height = validate_output_dimensions(output_width, output_height)
    if color not in {"black", "white"}:
        raise FilterGraphError("FILTER_COLOR_INVALID", "padding color is unsupported")
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={color}"
    )


def build_reframe_filtergraph(
    segments: Sequence[Any],
    *,
    output_width: int,
    output_height: int,
    subtitle_filename: str | None,
    has_audio: bool,
) -> tuple[str, str, str]:
    if not has_audio:
        raise FilterGraphError(
            "FILTER_AUDIO_REQUIRED",
            "agentic rendering requires the source audio stream used by transcription",
        )
    if not 1 <= len(segments) <= 48:
        raise FilterGraphError("FILTER_SEGMENT_LIMIT", "filtergraph requires 1 to 48 segments")
    validate_output_dimensions(output_width, output_height)

    graph: list[str] = []
    if len(segments) > 1:
        video_labels = "".join(f"[vsrc{index}]" for index in range(len(segments)))
        audio_labels = "".join(f"[asrc{index}]" for index in range(len(segments)))
        graph.append(f"[0:v]split={len(segments)}{video_labels}")
        graph.append(f"[0:a]asplit={len(segments)}{audio_labels}")

    for index, segment in enumerate(segments):
        source = segment.source_window
        video_input = f"[vsrc{index}]" if len(segments) > 1 else "[0:v]"
        audio_input = f"[asrc{index}]" if len(segments) > 1 else "[0:a]"
        if segment.strategy == "crop":
            composition = crop_scale_filter(
                segment.crop,
                output_width=output_width,
                output_height=output_height,
            )
        elif segment.strategy in {"fit", "letterbox"}:
            composition = fit_filter(
                output_width=output_width,
                output_height=output_height,
            )
        else:
            raise FilterGraphError(
                "FILTER_STRATEGY_UNSUPPORTED",
                f"unsupported reframe strategy: {segment.strategy}",
            )
        start = source.start_ms / 1000
        end = source.end_ms / 1000
        graph.append(
            f"{video_input}trim=start={start:.3f}:end={end:.3f},"
            f"setpts=PTS-STARTPTS,{composition},setsar=1[v{index}]"
        )
        graph.append(
            f"{audio_input}atrim=start={start:.3f}:end={end:.3f},"
            f"asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0[a{index}]"
        )

    if len(segments) == 1:
        video_output = "v0"
        audio_output = "a0"
    else:
        inputs = "".join(f"[v{index}][a{index}]" for index in range(len(segments)))
        graph.append(f"{inputs}concat=n={len(segments)}:v=1:a=1[vcat][acat]")
        video_output = "vcat"
        audio_output = "acat"

    if subtitle_filename:
        name = validate_subtitle_filename(subtitle_filename)
        style = "FontName=DejaVu Sans,FontSize=20,Outline=2,Shadow=1,Alignment=2,MarginV=100"
        graph.append(
            f"[{video_output}]subtitles=filename='{name}':force_style='{style}'[vout]"
        )
        video_output = "vout"

    filtergraph = ";".join(graph)
    if len(filtergraph) > MAX_FILTERGRAPH_LENGTH:
        raise FilterGraphError(
            "FILTERGRAPH_TOO_LARGE",
            "generated filtergraph exceeds the safe command budget",
        )
    return filtergraph, video_output, audio_output
