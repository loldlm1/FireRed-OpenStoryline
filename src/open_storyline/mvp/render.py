from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Sequence
import base64
import hashlib
import json
import os
import re
import subprocess
import time

from open_storyline.mvp.shorts import ShortCandidate
from open_storyline.mvp.catalog import (
    CreativeCatalog,
    catalog_caption_font,
    catalog_color_filter,
    catalog_transition_presets,
)
from open_storyline.mvp.compositor import (
    RENDER_EXECUTION_VERSION,
    ClipComposition,
    resolve_clip_composition,
)
from open_storyline.mvp.edit_plan import EditPlan
from open_storyline.mvp.ffmpeg_filters import build_reframe_filtergraph
from open_storyline.mvp.observability import emit_event
from open_storyline.mvp.subtitles import (
    CaptionFootprintReport,
    SubtitleArtifacts,
    SubtitleError,
    measure_caption_footprint,
    write_subtitle_artifacts,
)
from open_storyline.mvp.visual_understanding import VisualUnderstanding


class RenderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


RenderProgressCallback = Callable[[str, int, int], None]


def _notify_render_progress(
    callback: RenderProgressCallback | None,
    phase: str,
    current: int,
    total: int,
) -> None:
    if callback is None:
        return
    try:
        callback(phase, current, total)
    except Exception:
        emit_event(
            "render_activity_callback_failed",
            stage="rendering",
            error_code="RENDER_ACTIVITY_CALLBACK_FAILED",
        )


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int
    height: int
    has_audio: bool
    frame_rate: float = 0.0
    bit_rate: int = 0


@dataclass(frozen=True)
class RenderQualityProfile:
    name: str
    preset: str
    crf: int
    fps_cap: int


RENDER_QUALITY_PROFILE_VERSION = "render_quality_profile.v1"
RENDER_QUALITY_PROFILES = {
    "legacy": RenderQualityProfile("legacy", "veryfast", 23, 30),
    "balanced": RenderQualityProfile("balanced", "fast", 20, 30),
    "high": RenderQualityProfile("high", "medium", 18, 60),
}


@dataclass(frozen=True)
class RenderSettings:
    width: int = 1080
    height: int = 1920
    quality_profile: str = "high"
    fps_cap: int | None = None
    fps: float | None = None
    preset: str | None = None
    crf: int | None = None
    timeout: float = 1800.0
    caption_font_family: str = "DejaVu Sans"

    def resolve(self, source_frame_rate: float) -> dict[str, Any]:
        try:
            profile = RENDER_QUALITY_PROFILES[self.quality_profile]
        except KeyError as exc:
            raise RenderError(
                "RENDER_QUALITY_PROFILE_INVALID",
                "render quality profile must be legacy, balanced, or high",
            ) from exc
        fps_cap = int(self.fps_cap or profile.fps_cap)
        if not 12 <= fps_cap <= 60:
            raise RenderError("RENDER_FPS_CAP_INVALID", "render FPS cap must be 12 to 60")
        output_fps = float(self.fps) if self.fps is not None else min(
            source_frame_rate if source_frame_rate > 0 else 30.0,
            float(fps_cap),
        )
        if not 1 <= output_fps <= 60:
            raise RenderError("RENDER_FPS_INVALID", "render FPS must be 1 to 60")
        preset = self.preset or profile.preset
        if preset not in {
            "ultrafast", "superfast", "veryfast", "faster", "fast",
            "medium", "slow", "slower", "veryslow",
        }:
            raise RenderError("RENDER_PRESET_INVALID", "render preset is unsupported")
        crf = profile.crf if self.crf is None else int(self.crf)
        if not 0 <= crf <= 51:
            raise RenderError("RENDER_CRF_INVALID", "render CRF must be 0 to 51")
        explicit_override = self.fps is not None or self.preset is not None or self.crf is not None
        return {
            "version": RENDER_QUALITY_PROFILE_VERSION,
            "name": "custom" if explicit_override else profile.name,
            "configured_profile": profile.name,
            "preset": preset,
            "crf": crf,
            "fps_cap": fps_cap,
            "source_fps": round(source_frame_rate, 3),
            "output_fps": round(output_fps, 3),
            "fps_conversion": (
                "unknown" if source_frame_rate <= 0 else
                "preserved" if abs(source_frame_rate - output_fps) < 0.01 else
                "capped"
            ),
        }


def render_settings_from_config(
    config: Any,
    *,
    caption_font_family: str = "DejaVu Sans",
) -> RenderSettings:
    quality_profile = os.getenv(
        "OPENSTORYLINE_RENDER_QUALITY_PROFILE",
        str(getattr(config, "render_quality_profile", "high")),
    ).strip().lower()
    raw_fps_cap = os.getenv(
        "OPENSTORYLINE_RENDER_FPS_CAP",
        str(getattr(config, "render_fps_cap", 60)),
    ).strip()
    try:
        fps_cap = int(raw_fps_cap)
    except ValueError as exc:
        raise RenderError(
            "RENDER_FPS_CAP_INVALID",
            "OPENSTORYLINE_RENDER_FPS_CAP must be an integer",
        ) from exc
    settings = RenderSettings(
        width=int(getattr(config, "render_width", 1080)),
        height=int(getattr(config, "render_height", 1920)),
        quality_profile=quality_profile,
        fps_cap=fps_cap,
        caption_font_family=caption_font_family,
    )
    settings.resolve(30.0)
    return settings


@dataclass(frozen=True)
class RenderedShort:
    video_path: Path
    subtitle_path: Path | None
    clip: ShortCandidate
    subtitle_layout_path: Path | None = None
    caption_footprint_path: Path | None = None
    render_quality: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "video": self.video_path.name,
            "subtitles": self.subtitle_path.name if self.subtitle_path else None,
            "clip": self.clip.to_dict(),
            "subtitle_layout": (
                self.subtitle_layout_path.name if self.subtitle_layout_path else None
            ),
            "caption_footprint": (
                self.caption_footprint_path.name if self.caption_footprint_path else None
            ),
            "render_quality": self.render_quality,
        }


@dataclass(frozen=True)
class AgenticRenderResult:
    rendered: tuple[RenderedShort, ...]
    execution: dict[str, Any]


def _reason(value: str, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[-limit:]


def _parse_frame_rate(value: Any) -> float:
    text = str(value or "0/0")
    try:
        numerator, denominator = text.split("/", 1)
        result = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return result if 0 < result <= 240 else 0.0


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
            frame_rate=_parse_frame_rate(
                video.get("avg_frame_rate") or video.get("r_frame_rate")
            ),
            bit_rate=int(video.get("bit_rate") or (payload.get("format") or {}).get("bit_rate") or 0),
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


def _write_caption_evidence(
    destination: Path,
    *,
    clip: ShortCandidate,
    transcript_segments: Sequence[dict[str, Any]],
    width: int,
    height: int,
    font_family: str,
) -> tuple[SubtitleArtifacts, Path, Path, CaptionFootprintReport]:
    try:
        artifacts = write_subtitle_artifacts(
            destination,
            clip=clip,
            transcript_segments=transcript_segments,
            width=width,
            height=height,
            font_family=font_family,
        )
        footprint = measure_caption_footprint(
            artifacts,
            width=width,
            height=height,
        )
    except SubtitleError as exc:
        raise RenderError(exc.code, str(exc).partition(": ")[2] or str(exc)) from exc
    layout_path = destination.with_name(f"{destination.stem}.subtitle-layout.json")
    footprint_path = destination.with_name(f"{destination.stem}.caption-footprint.json")
    layout_path.write_text(
        json.dumps(artifacts.evidence(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    footprint_path.write_text(
        json.dumps(footprint.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if footprint.status == "blocked":
        raise RenderError(
            footprint.blocker_codes[0] if footprint.blocker_codes else "CAPTION_FOOTPRINT_BLOCKED",
            "caption footprint violates the configured footer safe zone",
        )
    return artifacts, layout_path, footprint_path, footprint


def _encode_quality_evidence(
    *,
    profile: dict[str, Any],
    source: MediaInfo,
    output: Path,
    elapsed_seconds: float,
) -> dict[str, Any]:
    rendered = probe_media(output)
    return {
        **profile,
        "source": {
            "width": source.width,
            "height": source.height,
            "fps": round(source.frame_rate, 3),
            "bit_rate": source.bit_rate,
        },
        "output": {
            "width": rendered.width,
            "height": rendered.height,
            "fps": round(rendered.frame_rate, 3),
            "bit_rate": rendered.bit_rate,
            "bytes": output.stat().st_size,
        },
        "encode_time_ms": int(round(elapsed_seconds * 1000)),
    }


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
        settings = self.settings
        media = probe_media(source)
        profile = settings.resolve(media.frame_rate)
        subtitles, layout_path, footprint_path, _footprint = _write_caption_evidence(
            output_dir / f"{stem}.srt",
            clip=clip,
            transcript_segments=transcript_segments,
            width=settings.width,
            height=settings.height,
            font_family=settings.caption_font_family,
        )
        filters = [
            f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase",
            f"crop={settings.width}:{settings.height}",
            "setsar=1",
        ]
        if subtitles.ass_path is not None:
            filters.append(f"ass=filename='{subtitles.ass_path.name}'")
        command = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{clip.start_ms / 1000:.3f}",
            "-t", f"{clip.duration_ms / 1000:.3f}",
            "-i", str(Path(source).resolve()),
            "-map", "0:v:0", "-map", "0:a?",
            "-vf", ",".join(filters),
            "-r", str(profile["output_fps"]),
            "-c:v", "libx264", "-preset", profile["preset"], "-crf", str(profile["crf"]),
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(video_path.name),
        ]
        started = time.monotonic()
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
        quality = _encode_quality_evidence(
            profile=profile,
            source=media,
            output=video_path,
            elapsed_seconds=time.monotonic() - started,
        )
        return RenderedShort(
            video_path=video_path,
            subtitle_path=subtitles.srt_path,
            clip=clip,
            subtitle_layout_path=layout_path,
            caption_footprint_path=footprint_path,
            render_quality=quality,
        )

    def render_plan(
        self,
        *,
        source: str | Path,
        clips: Sequence[ShortCandidate],
        transcript_segments: Sequence[dict[str, Any]],
        destination_dir: str | Path,
        progress_callback: RenderProgressCallback | None = None,
    ) -> list[RenderedShort]:
        total = len(clips)
        rendered = []
        for index, clip in enumerate(clips, start=1):
            _notify_render_progress(progress_callback, "started", index, total)
            rendered.append(self.render(
                source=source,
                clip=clip,
                transcript_segments=transcript_segments,
                destination_dir=destination_dir,
                index=index,
            ))
            _notify_render_progress(progress_callback, "completed", index, total)
        return rendered


class AgenticShortRenderer:
    def __init__(
        self,
        settings: RenderSettings | None = None,
        *,
        creative_catalog: CreativeCatalog | None = None,
    ) -> None:
        self.settings = settings or RenderSettings()
        self.creative_catalog = creative_catalog
        self.transition_presets = (
            catalog_transition_presets(creative_catalog)
            if creative_catalog is not None
            else {}
        )

    def _catalog_rendering(self, clip_plan: Any) -> tuple[str, str]:
        if self.creative_catalog is None:
            return self.settings.caption_font_family, ""
        selection = clip_plan.catalog_selection
        caption_id = selection.caption_treatment_id or "caption.clean"
        font_family = catalog_caption_font(self.creative_catalog, caption_id)
        color = (
            catalog_color_filter(
                self.creative_catalog,
                selection.color_treatment_id,
            )
            if selection.color_treatment_id
            else ""
        )
        return font_family, color

    def preflight_plan(
        self,
        *,
        source: str | Path,
        edit_plan: EditPlan,
        selected_clips: Sequence[ShortCandidate],
        visual_understanding: VisualUnderstanding,
        transcript_segments: Sequence[dict[str, Any]],
        destination_dir: str | Path,
        source_media: MediaInfo | None = None,
        crop_hysteresis_ratio: float = 0.03,
        crop_smoothing_alpha: float = 0.65,
        max_crop_velocity_ratio_per_second: float = 0.45,
        resolved_assets: dict[str, str | Path] | None = None,
    ) -> dict[str, Any]:
        if len(edit_plan.clips) != len(selected_clips):
            raise RenderError(
                "AGENTIC_RENDER_CLIP_MISMATCH",
                "edit plan and selected clip counts do not match",
            )
        output_dir = Path(destination_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        media = source_media or probe_media(source)
        asset_paths = {
            str(asset_id): Path(path).resolve()
            for asset_id, path in (resolved_assets or {}).items()
        }
        reports = []
        with TemporaryDirectory(prefix=".ffmpeg-preflight-", dir=output_dir) as directory:
            temporary = Path(directory)
            for clip_plan, selected_clip in zip(edit_plan.clips, selected_clips):
                caption_font_family, color_filter = self._catalog_rendering(clip_plan)
                subtitles, _layout, _footprint_path, _footprint = _write_caption_evidence(
                    temporary / f"{clip_plan.output_name}.srt",
                    clip=selected_clip,
                    transcript_segments=transcript_segments,
                    width=self.settings.width,
                    height=self.settings.height,
                    font_family=caption_font_family,
                )
                composition = resolve_clip_composition(
                    clip_plan,
                    visual=visual_understanding,
                    source_media=media,
                    output_width=self.settings.width,
                    output_height=self.settings.height,
                    hysteresis_ratio=crop_hysteresis_ratio,
                    smoothing_alpha=crop_smoothing_alpha,
                    max_crop_velocity_ratio_per_second=(
                        max_crop_velocity_ratio_per_second
                    ),
                    transition_presets=self.transition_presets,
                )
                used_asset_ids = sorted({
                    overlay.asset_id
                    for segment in composition.segments
                    for overlay in segment.overlays
                    if overlay.kind == "image"
                })
                missing_assets = sorted(set(used_asset_ids) - set(asset_paths))
                if missing_assets:
                    raise RenderError(
                        "AGENTIC_RENDER_ASSET_MISSING",
                        f"resolved image assets are missing: {', '.join(missing_assets)}",
                    )
                asset_input_indexes = {
                    asset_id: index
                    for index, asset_id in enumerate(used_asset_ids, start=1)
                }
                asset_kinds = {
                    request.id: request.kind
                    for request in clip_plan.asset_requests
                    if request.id in used_asset_ids
                }
                filtergraph, video_label, audio_label = build_reframe_filtergraph(
                    composition.segments,
                    output_width=self.settings.width,
                    output_height=self.settings.height,
                    subtitle_filename=(
                        subtitles.ass_path.name if subtitles.ass_path is not None else None
                    ),
                    has_audio=media.has_audio,
                    asset_input_indexes=asset_input_indexes,
                    asset_input_kinds=asset_kinds,
                    color_filter=color_filter,
                )
                command = [
                    "ffmpeg", "-v", "error", "-i", str(Path(source).resolve()),
                ]
                for asset_id in used_asset_ids:
                    if asset_kinds[asset_id] == "stock_video":
                        command.extend(
                            ["-stream_loop", "-1", "-i", str(asset_paths[asset_id])]
                        )
                    else:
                        command.extend(["-loop", "1", "-i", str(asset_paths[asset_id])])
                command.extend([
                    "-filter_complex", filtergraph,
                    "-map", f"[{video_label}]", "-map", f"[{audio_label}]",
                    "-t", "0.750", "-frames:v", "2", "-shortest", "-f", "null", "-",
                ])
                try:
                    result = subprocess.run(
                        command,
                        cwd=temporary,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=min(float(self.settings.timeout), 120.0),
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                    raise RenderError("AGENTIC_PREFLIGHT_FAILED", str(exc)) from exc
                if result.returncode != 0:
                    raise RenderError("AGENTIC_PREFLIGHT_FAILED", _reason(result.stderr))
                reports.append({
                    "clip_index": clip_plan.clip_index,
                    "status": "pass",
                    "filtergraph_sha256": hashlib.sha256(
                        filtergraph.encode("utf-8")
                    ).hexdigest(),
                    "filtergraph_length": len(filtergraph),
                    "asset_ids": used_asset_ids,
                })
        return {
            "version": "ffmpeg_preflight.v1",
            "status": "pass",
            "clips": reports,
        }

    def render_plan(
        self,
        *,
        source: str | Path,
        edit_plan: EditPlan,
        selected_clips: Sequence[ShortCandidate],
        visual_understanding: VisualUnderstanding,
        transcript_segments: Sequence[dict[str, Any]],
        destination_dir: str | Path,
        source_media: MediaInfo | None = None,
        crop_hysteresis_ratio: float = 0.03,
        crop_smoothing_alpha: float = 0.65,
        max_crop_velocity_ratio_per_second: float = 0.45,
        resolved_assets: dict[str, str | Path] | None = None,
        progress_callback: RenderProgressCallback | None = None,
    ) -> AgenticRenderResult:
        if len(edit_plan.clips) != len(selected_clips):
            raise RenderError(
                "AGENTIC_RENDER_CLIP_MISMATCH",
                "edit plan and selected clip counts do not match",
            )
        output_dir = Path(destination_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        media = source_media or probe_media(source)
        asset_paths = {
            str(asset_id): Path(path).resolve()
            for asset_id, path in (resolved_assets or {}).items()
        }
        for asset_id, path in asset_paths.items():
            if not path.is_file():
                raise RenderError(
                    "AGENTIC_RENDER_ASSET_MISSING",
                    f"resolved image asset is missing: {asset_id}",
                )
        settings = self.settings
        profile = settings.resolve(media.frame_rate)
        rendered: list[RenderedShort] = []
        executions: list[dict[str, Any]] = []

        total = len(selected_clips)
        for index, (clip_plan, selected_clip) in enumerate(
            zip(edit_plan.clips, selected_clips),
            start=1,
        ):
            _notify_render_progress(progress_callback, "started", index, total)
            if (
                clip_plan.source_window.start_ms != selected_clip.start_ms
                or clip_plan.source_window.end_ms != selected_clip.end_ms
            ):
                raise RenderError(
                    "AGENTIC_RENDER_CLIP_MISMATCH",
                    f"clip {clip_plan.clip_index} source bounds changed after planning",
                )
            video_path = output_dir / clip_plan.output_name
            caption_font_family, color_filter = self._catalog_rendering(clip_plan)
            subtitles, layout_path, footprint_path, footprint = _write_caption_evidence(
                output_dir / f"{video_path.stem}.srt",
                clip=selected_clip,
                transcript_segments=transcript_segments,
                width=settings.width,
                height=settings.height,
                font_family=caption_font_family,
            )
            composition: ClipComposition = resolve_clip_composition(
                clip_plan,
                visual=visual_understanding,
                source_media=media,
                output_width=settings.width,
                output_height=settings.height,
                hysteresis_ratio=crop_hysteresis_ratio,
                smoothing_alpha=crop_smoothing_alpha,
                max_crop_velocity_ratio_per_second=max_crop_velocity_ratio_per_second,
                transition_presets=self.transition_presets,
            )
            used_asset_ids = sorted({
                overlay.asset_id
                for segment in composition.segments
                for overlay in segment.overlays
                if overlay.kind == "image"
            })
            missing_assets = sorted(set(used_asset_ids) - set(asset_paths))
            if missing_assets:
                raise RenderError(
                    "AGENTIC_RENDER_ASSET_MISSING",
                    f"resolved image assets are missing: {', '.join(missing_assets)}",
                )
            asset_input_indexes = {
                asset_id: index
                for index, asset_id in enumerate(used_asset_ids, start=1)
            }
            asset_kinds = {
                request.id: request.kind
                for request in clip_plan.asset_requests
                if request.id in used_asset_ids
            }
            filtergraph, video_label, audio_label = build_reframe_filtergraph(
                composition.segments,
                output_width=settings.width,
                output_height=settings.height,
                subtitle_filename=(
                    subtitles.ass_path.name if subtitles.ass_path is not None else None
                ),
                has_audio=media.has_audio,
                asset_input_indexes=asset_input_indexes,
                asset_input_kinds=asset_kinds,
                color_filter=color_filter,
            )
            command = [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(Path(source).resolve()),
            ]
            for asset_id in used_asset_ids:
                if asset_kinds[asset_id] == "stock_video":
                    command.extend(["-stream_loop", "-1", "-i", str(asset_paths[asset_id])])
                else:
                    command.extend(["-loop", "1", "-i", str(asset_paths[asset_id])])
            command.extend([
                "-filter_complex", filtergraph,
                "-map", f"[{video_label}]", "-map", f"[{audio_label}]",
                "-r", str(profile["output_fps"]),
                "-c:v", "libx264", "-preset", profile["preset"], "-crf", str(profile["crf"]),
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", "-shortest", video_path.name,
            ])
            started = time.monotonic()
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
                raise RenderError("AGENTIC_VIDEO_RENDER_FAILED", str(exc)) from exc
            if result.returncode != 0 or not video_path.is_file():
                raise RenderError("AGENTIC_VIDEO_RENDER_FAILED", _reason(result.stderr))
            quality = _encode_quality_evidence(
                profile=profile,
                source=media,
                output=video_path,
                elapsed_seconds=time.monotonic() - started,
            )
            rendered.append(RenderedShort(
                video_path=video_path,
                subtitle_path=subtitles.srt_path,
                clip=selected_clip,
                subtitle_layout_path=layout_path,
                caption_footprint_path=footprint_path,
                render_quality=quality,
            ))
            executions.append({
                **composition.to_dict(),
                "video": video_path.name,
                "subtitles": subtitles.srt_path.name if subtitles.srt_path else None,
                "subtitle_layout": layout_path.name,
                "caption_footprint": footprint.to_dict(),
                "render_quality": quality,
                "encode_count": 1,
                "filtergraph": filtergraph,
                "filtergraph_length": len(filtergraph),
                "asset_ids": used_asset_ids,
                "asset_kinds": asset_kinds,
            })
            _notify_render_progress(progress_callback, "completed", index, total)

        return AgenticRenderResult(
            rendered=tuple(rendered),
            execution={
                "version": RENDER_EXECUTION_VERSION,
                "plan_version": edit_plan.version,
                "output": {
                    "width": settings.width,
                    "height": settings.height,
                    "fps": profile["output_fps"],
                    "video_codec": "h264",
                    "audio_codec": "aac",
                },
                "quality_profile": profile,
                "summary": {
                    "clips": len(executions),
                    "encodes": len(executions),
                    "fallbacks": sum(item["fallback_count"] for item in executions),
                },
                "clips": executions,
            },
        )
