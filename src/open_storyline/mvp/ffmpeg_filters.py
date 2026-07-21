from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence
import re


MAX_FILTERGRAPH_LENGTH = 65_536
SAFE_XFADE_TRANSITIONS = frozenset({
    "fade", "wipeleft", "wiperight", "slideleft", "slideright",
    "circleopen", "dissolve",
})


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
    if Path(name).name != name or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*\.(?:srt|ass)",
        name,
    ):
        raise FilterGraphError(
            "FILTER_SUBTITLE_PATH_INVALID",
            "subtitle filename must be a server-generated SRT or ASS basename",
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


def _escape_drawtext(value: str) -> str:
    text = str(value or "")
    replacements = (
        ("\\", "\\\\"),
        ("'", "\\'"),
        (":", "\\:"),
        ("%", "\\%"),
        (",", "\\,"),
        ("[", "\\["),
        ("]", "\\]"),
        (";", "\\;"),
    )
    for source, replacement in replacements:
        text = text.replace(source, replacement)
    return text


def _overlay_position(position: str, *, margin_ratio: float) -> tuple[str, str]:
    margin = f"{margin_ratio:.4f}"
    horizontal = {
        "top_left": f"W*{margin}",
        "bottom_left": f"W*{margin}",
        "top_right": f"W-w-W*{margin}",
        "bottom_right": f"W-w-W*{margin}",
        "top": "(W-w)/2",
        "bottom": "(W-w)/2",
        "center": "(W-w)/2",
    }
    vertical = {
        "top_left": f"H*{margin}",
        "top_right": f"H*{margin}",
        "top": f"H*{margin}",
        "bottom_left": f"H-h-H*{margin}",
        "bottom_right": f"H-h-H*{margin}",
        "bottom": f"H-h-H*{margin}",
        "center": "(H-h)/2",
    }
    try:
        return horizontal[position], vertical[position]
    except KeyError as exc:
        raise FilterGraphError("FILTER_POSITION_INVALID", "overlay position is unsupported") from exc


def _text_position(position: str, *, margin_ratio: float) -> tuple[str, str]:
    margin = f"{margin_ratio:.4f}"
    horizontal = {
        "top_left": f"w*{margin}",
        "bottom_left": f"w*{margin}",
        "top_right": f"w-text_w-w*{margin}",
        "bottom_right": f"w-text_w-w*{margin}",
        "top": "(w-text_w)/2",
        "bottom": "(w-text_w)/2",
        "center": "(w-text_w)/2",
    }
    vertical = {
        "top_left": f"h*{margin}",
        "top_right": f"h*{margin}",
        "top": f"h*{margin}",
        "bottom_left": f"h-text_h-h*{margin}",
        "bottom_right": f"h-text_h-h*{margin}",
        "bottom": f"h-text_h-h*{margin}",
        "center": "(h-text_h)/2",
    }
    try:
        return horizontal[position], vertical[position]
    except KeyError as exc:
        raise FilterGraphError("FILTER_POSITION_INVALID", "text position is unsupported") from exc


def _overlay_alpha_filters(*, opacity: float, duration: float, transition: float) -> str:
    filters = ["format=rgba", f"colorchannelmixer=aa={opacity:.4f}"]
    if transition > 0:
        transition = min(transition, duration / 2)
        filters.append(f"fade=t=in:st=0:d={transition:.3f}:alpha=1")
        filters.append(
            f"fade=t=out:st={max(0.0, duration - transition):.3f}:d={transition:.3f}:alpha=1"
        )
    return ",".join(filters)


def build_reframe_filtergraph(
    segments: Sequence[Any],
    *,
    output_width: int,
    output_height: int,
    subtitle_filename: str | None,
    has_audio: bool,
    asset_input_indexes: dict[str, int] | None = None,
    asset_input_kinds: dict[str, str] | None = None,
    color_filter: str = "",
) -> tuple[str, str, str]:
    if not has_audio:
        raise FilterGraphError(
            "FILTER_AUDIO_REQUIRED",
            "agentic rendering requires the source audio stream used by transcription",
        )
    if not 1 <= len(segments) <= 48:
        raise FilterGraphError("FILTER_SEGMENT_LIMIT", "filtergraph requires 1 to 48 segments")
    validate_output_dimensions(output_width, output_height)
    if color_filter and (
        len(color_filter) > 240
        or not re.fullmatch(
            r"(?:eq|colorbalance|curves)=[A-Za-z0-9_.:=+-]+",
            color_filter,
        )
    ):
        raise FilterGraphError(
            "FILTER_COLOR_TREATMENT_INVALID",
            "catalog color treatment is invalid",
        )

    assets = dict(asset_input_indexes or {})
    asset_kinds = dict(asset_input_kinds or {})
    source_overlays = [
        (segment_index, overlay)
        for segment_index, segment in enumerate(segments)
        for overlay in getattr(segment, "overlays", ())
        if overlay.kind in {"source", "pip"}
    ]
    graph: list[str] = []
    video_uses = len(segments) + len(source_overlays)
    if video_uses > 1:
        video_labels = "".join(f"[vsrc{index}]" for index in range(video_uses))
        audio_labels = "".join(f"[asrc{index}]" for index in range(len(segments)))
        graph.append(f"[0:v]split={video_uses}{video_labels}")
    if len(segments) > 1:
        graph.append(f"[0:a]asplit={len(segments)}{audio_labels}")

    source_overlay_labels = {
        (segment_index, overlay.id): f"vsrc{len(segments) + overlay_index}"
        for overlay_index, (segment_index, overlay) in enumerate(source_overlays)
    }
    for index, segment in enumerate(segments):
        source = segment.source_window
        video_input = f"[vsrc{index}]" if video_uses > 1 else "[0:v]"
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
        treatment = f",{color_filter}" if color_filter else ""
        graph.append(
            f"{video_input}trim=start={start:.3f}:end={end:.3f},"
            f"settb=AVTB,setpts=PTS-STARTPTS,{composition}{treatment},setsar=1[vbase{index}]"
        )
        graph.append(
            f"{audio_input}atrim=start={start:.3f}:end={end:.3f},"
            f"asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0[a{index}]"
        )

        current_label = f"vbase{index}"
        for overlay_index, overlay in enumerate(getattr(segment, "overlays", ())):
            local_start = (
                overlay.timeline_window.start_ms - segment.timeline_window.start_ms
            ) / 1000
            local_end = (
                overlay.timeline_window.end_ms - segment.timeline_window.start_ms
            ) / 1000
            duration = local_end - local_start
            next_label = f"v{index}o{overlay_index}"
            if overlay.kind == "text":
                text = _escape_drawtext(overlay.text)
                transition = min(overlay.transition_ms / 1000, duration / 2)
                alpha = f"{overlay.opacity:.4f}"
                if transition > 0:
                    alpha = (
                        f"if(lt(t\\,{local_start + transition:.3f})\\,"
                        f"((t-{local_start:.3f})/{transition:.3f})*{overlay.opacity:.4f}\\,"
                        f"if(gt(t\\,{local_end - transition:.3f})\\,"
                        f"(({local_end:.3f}-t)/{transition:.3f})*{overlay.opacity:.4f}\\,"
                        f"{overlay.opacity:.4f}))"
                    )
                x, y = _text_position(
                    overlay.position,
                    margin_ratio=overlay.margin_ratio,
                )
                graph.append(
                    f"[{current_label}]drawtext=text='{text}':fontcolor=white:fontsize=h/16:"
                    "box=1:boxcolor=black@0.65:boxborderw=12:"
                    f"x={x}:y={y}:alpha='{alpha}':"
                    f"enable='between(t,{local_start:.3f},{local_end:.3f})'"
                    f"[{next_label}]"
                )
            elif overlay.kind in {"source", "pip", "image"}:
                overlay_width = max(2, int(round(output_width * overlay.width_ratio)))
                overlay_width -= overlay_width % 2
                transition = overlay.transition_ms / 1000
                alpha = _overlay_alpha_filters(
                    opacity=overlay.opacity,
                    duration=duration,
                    transition=transition,
                )
                if overlay.kind in {"source", "pip"}:
                    source_label = source_overlay_labels[(index, overlay.id)]
                    overlay_start = overlay.source_window.start_ms / 1000
                    overlay_end = overlay.source_window.end_ms / 1000
                    graph.append(
                        f"[{source_label}]trim=start={overlay_start:.3f}:end={overlay_end:.3f},"
                        f"setpts=PTS-STARTPTS,scale={overlay_width}:-2,{alpha},"
                        f"setpts=PTS+{local_start:.3f}/TB[ov{index}_{overlay_index}]"
                    )
                else:
                    input_index = assets.get(overlay.asset_id)
                    if input_index is None or input_index < 1:
                        raise FilterGraphError(
                            "FILTER_ASSET_UNRESOLVED",
                            f"image overlay asset is unresolved: {overlay.asset_id}",
                        )
                    asset_kind = asset_kinds.get(overlay.asset_id, "generated_image")
                    if asset_kind not in {"generated_image", "stock_image", "stock_video"}:
                        raise FilterGraphError(
                            "FILTER_ASSET_KIND_INVALID",
                            f"overlay asset kind is unsupported: {overlay.asset_id}",
                        )
                    graph.append(
                        f"[{input_index}:v]trim=duration={duration:.3f},"
                        f"setpts=PTS-STARTPTS,scale={overlay_width}:-2,{alpha},"
                        f"setpts=PTS+{local_start:.3f}/TB[ov{index}_{overlay_index}]"
                    )
                x, y = _overlay_position(overlay.position, margin_ratio=overlay.margin_ratio)
                graph.append(
                    f"[{current_label}][ov{index}_{overlay_index}]overlay=x='{x}':y='{y}':"
                    f"eof_action=pass:shortest=0:enable='between(t,{local_start:.3f},{local_end:.3f})'"
                    f"[{next_label}]"
                )
            else:
                raise FilterGraphError(
                    "FILTER_OVERLAY_UNSUPPORTED",
                    f"unsupported overlay kind: {overlay.kind}",
                )
            current_label = next_label
        if current_label != f"v{index}":
            graph.append(f"[{current_label}]null[v{index}]")

    video_output = "v0"
    audio_output = "a0"
    chain_duration = segments[0].source_window.duration_ms / 1000
    for index, segment in enumerate(segments[1:], start=1):
        segment_duration = segment.source_window.duration_ms / 1000
        kind = getattr(segment, "transition_kind", "cut")
        transition = getattr(segment, "transition_duration_ms", 0) / 1000
        next_video = f"vchain{index}"
        next_audio = f"achain{index}"
        if kind == "cut":
            graph.append(
                f"[{video_output}][v{index}]concat=n=2:v=1:a=0[{next_video}]"
            )
            graph.append(
                f"[{audio_output}][a{index}]concat=n=2:v=0:a=1[{next_audio}]"
            )
            chain_duration += segment_duration
        elif kind == "fade":
            half = transition / 2
            color = getattr(segment, "transition_color", "black")
            if color not in {"black", "white"}:
                raise FilterGraphError(
                    "FILTER_TRANSITION_INVALID",
                    "fade transition color is invalid",
                )
            graph.append(
                f"[{video_output}]fade=t=out:st={max(0.0, chain_duration - half):.3f}:"
                f"d={half:.3f}:color={color}[vfprev{index}]"
            )
            graph.append(
                f"[v{index}]fade=t=in:st=0:d={half:.3f}:color={color}[vfnext{index}]"
            )
            graph.append(
                f"[vfprev{index}][vfnext{index}]concat=n=2:v=1:a=0[{next_video}]"
            )
            graph.append(
                f"[{audio_output}]afade=t=out:st={max(0.0, chain_duration - half):.3f}:d={half:.3f}[afprev{index}]"
            )
            graph.append(f"[a{index}]afade=t=in:st=0:d={half:.3f}[afnext{index}]")
            graph.append(
                f"[afprev{index}][afnext{index}]concat=n=2:v=0:a=1[{next_audio}]"
            )
            chain_duration += segment_duration
        elif kind == "xfade":
            offset = chain_duration - transition
            if transition <= 0 or offset < 0:
                raise FilterGraphError("FILTER_TRANSITION_INVALID", "crossfade timing is invalid")
            transition_name = getattr(segment, "transition_name", "fade") or "fade"
            if transition_name not in SAFE_XFADE_TRANSITIONS:
                raise FilterGraphError(
                    "FILTER_TRANSITION_INVALID",
                    "catalog crossfade transition is invalid",
                )
            graph.append(
                f"[{video_output}][v{index}]xfade=transition={transition_name}:duration={transition:.3f}:"
                f"offset={offset:.3f}[{next_video}]"
            )
            graph.append(
                f"[{audio_output}][a{index}]acrossfade=d={transition:.3f}:c1=tri:c2=tri[{next_audio}]"
            )
            chain_duration += segment_duration - transition
        else:
            raise FilterGraphError(
                "FILTER_TRANSITION_UNSUPPORTED",
                f"unsupported transition kind: {kind}",
            )
        video_output = next_video
        audio_output = next_audio

    expected_duration = getattr(
        segments[-1],
        "timeline_window",
        segments[-1].source_window,
    ).end_ms / 1000
    if abs(chain_duration - expected_duration) > 0.005:
        raise FilterGraphError(
            "FILTER_TIMELINE_MISMATCH",
            "transition graph duration does not match the validated timeline",
        )

    if subtitle_filename:
        name = validate_subtitle_filename(subtitle_filename)
        filter_name = "ass" if name.endswith(".ass") else "subtitles"
        graph.append(f"[{video_output}]{filter_name}=filename='{name}'[vout]")
        video_output = "vout"

    filtergraph = ";".join(graph)
    if len(filtergraph) > MAX_FILTERGRAPH_LENGTH:
        raise FilterGraphError(
            "FILTERGRAPH_TOO_LARGE",
            "generated filtergraph exceeds the safe command budget",
        )
    return filtergraph, video_output, audio_output
