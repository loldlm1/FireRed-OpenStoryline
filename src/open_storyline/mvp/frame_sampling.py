from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
import base64
import math
import re
import subprocess

from open_storyline.mvp.scene_boundaries import SceneBoundaryReport, SceneInterval


FRAME_MANIFEST_VERSION = "frame_manifest.v1"


class FrameSamplingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class FrameRequest:
    timestamp_ms: int
    scene_id: str
    reason: str


@dataclass(frozen=True)
class SampledFrame:
    id: str
    timestamp_ms: int
    scene_id: str
    width: int
    height: int
    extraction_reason: str
    encoded_bytes: int
    data_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp_ms": self.timestamp_ms,
            "scene_id": self.scene_id,
            "width": self.width,
            "height": self.height,
            "extraction_reason": self.extraction_reason,
            "encoded_bytes": self.encoded_bytes,
        }


@dataclass(frozen=True)
class FrameManifest:
    source_duration_ms: int
    source_width: int
    source_height: int
    frames: tuple[SampledFrame, ...]
    warnings: tuple[dict[str, Any], ...] = ()
    version: str = FRAME_MANIFEST_VERSION

    @property
    def image_data_urls(self) -> tuple[str, ...]:
        return tuple(frame.data_url for frame in self.frames)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_duration_ms": self.source_duration_ms,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "frame_count": len(self.frames),
            "frames": [frame.to_dict() for frame in self.frames],
            "warnings": list(self.warnings),
        }


def _scene_for_time(scenes: Sequence[SceneInterval], timestamp_ms: int) -> SceneInterval:
    for scene in scenes:
        if scene.start_ms <= timestamp_ms < scene.end_ms:
            return scene
    return scenes[-1]


def _evenly_select(values: Sequence[FrameRequest], count: int) -> list[FrameRequest]:
    if len(values) <= count:
        return list(values)
    if count == 1:
        return [values[len(values) // 2]]
    indexes = {
        int(round(index * (len(values) - 1) / (count - 1)))
        for index in range(count)
    }
    return [values[index] for index in sorted(indexes)]


def build_frame_requests(
    scenes: Sequence[SceneInterval],
    *,
    source_duration_ms: int,
    max_frames: int,
) -> tuple[FrameRequest, ...]:
    if source_duration_ms <= 0 or not scenes:
        raise FrameSamplingError("FRAME_SOURCE_INVALID", "source duration and scenes are required")
    if not 1 <= max_frames <= 64:
        raise FrameSamplingError("FRAME_LIMIT_INVALID", "max_frames must be between 1 and 64")

    candidates: list[FrameRequest] = []
    for scene in scenes:
        span = scene.duration_ms
        opening = min(scene.end_ms - 1, scene.start_ms + min(250, max(1, span // 4)))
        midpoint = min(scene.end_ms - 1, scene.start_ms + max(1, span // 2))
        candidates.append(FrameRequest(opening, scene.id, "scene_opening"))
        candidates.append(FrameRequest(midpoint, scene.id, "scene_midpoint"))

    uniform_count = max(2, min(max_frames, 8))
    for index in range(uniform_count):
        timestamp = int(round((index + 1) * source_duration_ms / (uniform_count + 1)))
        timestamp = min(source_duration_ms - 1, max(0, timestamp))
        scene = _scene_for_time(scenes, timestamp)
        candidates.append(FrameRequest(timestamp, scene.id, "uniform_coverage"))

    ordered = sorted(candidates, key=lambda item: (item.timestamp_ms, item.reason, item.scene_id))
    deduped: list[FrameRequest] = []
    for candidate in ordered:
        if deduped and candidate.timestamp_ms - deduped[-1].timestamp_ms < 100:
            previous = deduped[-1]
            reasons = "+".join(sorted(set(previous.reason.split("+") + candidate.reason.split("+"))))
            deduped[-1] = FrameRequest(previous.timestamp_ms, previous.scene_id, reasons)
        else:
            deduped.append(candidate)
    return tuple(_evenly_select(deduped, max_frames))


def build_clip_frame_requests(
    scenes: Sequence[SceneInterval],
    *,
    source_duration_ms: int,
    clip_start_ms: int,
    clip_end_ms: int,
    max_frames: int,
    focus_windows: Sequence[tuple[int, int]] = (),
) -> tuple[FrameRequest, ...]:
    if (
        source_duration_ms <= 0
        or not scenes
        or clip_start_ms < 0
        or clip_end_ms > source_duration_ms
        or clip_end_ms <= clip_start_ms
    ):
        raise FrameSamplingError(
            "FRAME_CLIP_INVALID",
            "clip bounds must stay inside the source duration",
        )
    if not 5 <= max_frames <= 32:
        raise FrameSamplingError(
            "FRAME_CLIP_LIMIT_INVALID",
            "clip-local max_frames must be between 5 and 32",
        )

    try:
        normalized_focus_windows = tuple(sorted({
            (int(window[0]), int(window[1]))
            for window in focus_windows
            if isinstance(window, (list, tuple)) and len(window) == 2
        }))
    except (TypeError, ValueError, OverflowError):
        normalized_focus_windows = ()
    if len(normalized_focus_windows) != len(focus_windows):
        raise FrameSamplingError(
            "FRAME_FOCUS_INVALID",
            "focus windows must contain integer start and end timestamps",
        )
    if any(
        start_ms < clip_start_ms
        or end_ms > clip_end_ms
        or end_ms <= start_ms
        for start_ms, end_ms in normalized_focus_windows
    ):
        raise FrameSamplingError(
            "FRAME_FOCUS_INVALID",
            "focus windows must stay inside the selected clip",
        )

    focused: list[FrameRequest] = []
    for start_ms, end_ms in normalized_focus_windows:
        duration_ms = end_ms - start_ms
        safe_offset = min(250, max(1, duration_ms // 20))
        for timestamp_ms, reason in (
            (min(end_ms - 1, start_ms + safe_offset), "repair_window_start"),
            (max(start_ms, end_ms - safe_offset - 1), "repair_window_end"),
        ):
            focused.append(FrameRequest(
                timestamp_ms,
                _scene_for_time(scenes, timestamp_ms).id,
                reason,
            ))
    focused_timestamps = {item.timestamp_ms for item in focused}
    if len(focused_timestamps) > max_frames:
        raise FrameSamplingError(
            "FRAME_FOCUS_LIMIT_INVALID",
            "focused repair windows exceed the clip-local frame budget",
        )

    duration_ms = clip_end_ms - clip_start_ms
    safe_offset = min(250, max(1, duration_ms // 20))
    required = [
        FrameRequest(
            min(clip_end_ms - 1, clip_start_ms + safe_offset),
            _scene_for_time(scenes, min(clip_end_ms - 1, clip_start_ms + safe_offset)).id,
            "clip_safe_start",
        ),
        FrameRequest(
            clip_start_ms + duration_ms // 4,
            _scene_for_time(scenes, clip_start_ms + duration_ms // 4).id,
            "clip_first_quartile",
        ),
        FrameRequest(
            clip_start_ms + duration_ms // 2,
            _scene_for_time(scenes, clip_start_ms + duration_ms // 2).id,
            "clip_midpoint",
        ),
        FrameRequest(
            clip_start_ms + (duration_ms * 3) // 4,
            _scene_for_time(scenes, clip_start_ms + (duration_ms * 3) // 4).id,
            "clip_third_quartile",
        ),
        FrameRequest(
            max(clip_start_ms, clip_end_ms - safe_offset - 1),
            _scene_for_time(scenes, max(clip_start_ms, clip_end_ms - safe_offset - 1)).id,
            "clip_safe_end",
        ),
    ]
    scene_candidates: list[FrameRequest] = []
    for scene in scenes:
        start_ms = max(scene.start_ms, clip_start_ms)
        end_ms = min(scene.end_ms, clip_end_ms)
        if end_ms <= start_ms:
            continue
        scene_candidates.append(FrameRequest(start_ms, scene.id, "clip_scene_opening"))
        scene_candidates.append(FrameRequest(end_ms - 1, scene.id, "clip_scene_closing"))

    uniform_candidates: list[FrameRequest] = []
    for index in range(max_frames):
        timestamp_ms = clip_start_ms + int(
            round((index + 1) * duration_ms / (max_frames + 1))
        )
        timestamp_ms = min(clip_end_ms - 1, max(clip_start_ms, timestamp_ms))
        uniform_candidates.append(FrameRequest(
            timestamp_ms,
            _scene_for_time(scenes, timestamp_ms).id,
            "clip_uniform_coverage",
        ))

    selected: dict[int, FrameRequest] = {}

    def add(candidate: FrameRequest) -> None:
        previous = selected.get(candidate.timestamp_ms)
        if previous is None:
            selected[candidate.timestamp_ms] = candidate
            return
        reasons = "+".join(sorted(set(previous.reason.split("+") + candidate.reason.split("+"))))
        selected[candidate.timestamp_ms] = FrameRequest(
            candidate.timestamp_ms,
            candidate.scene_id,
            reasons,
        )

    for candidate in focused:
        add(candidate)
    remaining = max_frames - len(selected)
    for candidate in _evenly_select(required, max(0, remaining)):
        add(candidate)
    remaining = max_frames - len(selected)
    for candidate in _evenly_select(scene_candidates, max(0, remaining)):
        add(candidate)
    remaining = max_frames - len(selected)
    for candidate in _evenly_select(uniform_candidates, max(0, remaining)):
        add(candidate)
    if len(selected) < max_frames:
        for candidate in uniform_candidates:
            add(candidate)
            if len(selected) == max_frames:
                break
    return tuple(sorted(selected.values(), key=lambda item: item.timestamp_ms))


def _scaled_dimensions(source_width: int, source_height: int, max_width: int, max_height: int) -> tuple[int, int]:
    if min(source_width, source_height, max_width, max_height) <= 0:
        raise FrameSamplingError("FRAME_DIMENSIONS_INVALID", "frame dimensions must be positive")
    ratio = min(1.0, max_width / source_width, max_height / source_height)
    width = max(2, int(math.floor(source_width * ratio)))
    height = max(2, int(math.floor(source_height * ratio)))
    width -= width % 2
    height -= height % 2
    return width, height


def sample_frames(
    source: str | Path,
    *,
    scene_report: SceneBoundaryReport,
    source_width: int,
    source_height: int,
    max_frames: int = 12,
    max_width: int = 512,
    max_height: int = 512,
    max_frame_bytes: int = 1_500_000,
    timeout_per_frame: float = 120.0,
    clip_start_ms: int | None = None,
    clip_end_ms: int | None = None,
    id_prefix: str = "",
    focus_windows: Sequence[tuple[int, int]] = (),
) -> FrameManifest:
    if not 16_384 <= max_frame_bytes <= 8 * 1024 * 1024:
        raise FrameSamplingError(
            "FRAME_BYTES_LIMIT_INVALID",
            "max_frame_bytes must be between 16384 and 8388608",
        )
    if (clip_start_ms is None) != (clip_end_ms is None):
        raise FrameSamplingError(
            "FRAME_CLIP_INVALID",
            "clip_start_ms and clip_end_ms must be supplied together",
        )
    if focus_windows and clip_start_ms is None:
        raise FrameSamplingError(
            "FRAME_FOCUS_INVALID",
            "focus windows require selected clip bounds",
        )
    requests = (
        build_frame_requests(
            scene_report.scenes,
            source_duration_ms=scene_report.source_duration_ms,
            max_frames=max_frames,
        )
        if clip_start_ms is None
        else build_clip_frame_requests(
            scene_report.scenes,
            source_duration_ms=scene_report.source_duration_ms,
            clip_start_ms=int(clip_start_ms),
            clip_end_ms=int(clip_end_ms),
            max_frames=max_frames,
            focus_windows=focus_windows,
        )
    )
    clean_prefix = str(id_prefix or "")
    if clean_prefix and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*-", clean_prefix):
        raise FrameSamplingError("FRAME_ID_PREFIX_INVALID", "frame ID prefix is invalid")
    width, height = _scaled_dimensions(source_width, source_height, max_width, max_height)
    sampled: list[SampledFrame] = []
    source_path = str(Path(source).resolve())
    for index, request in enumerate(requests, start=1):
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{request.timestamp_ms / 1000:.3f}",
            "-i",
            source_path,
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:{height}",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=timeout_per_frame,
            )
        except FileNotFoundError as exc:
            raise FrameSamplingError("FRAME_FFMPEG_UNAVAILABLE", "FFmpeg is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise FrameSamplingError("FRAME_EXTRACTION_TIMEOUT", "frame extraction timed out") from exc
        if result.returncode != 0 or not result.stdout:
            reason = re.sub(
                r"\s+",
                " ",
                result.stderr.decode("utf-8", "ignore") if result.stderr else "",
            ).strip()[-1200:]
            raise FrameSamplingError(
                "FRAME_EXTRACTION_FAILED",
                reason or "FFmpeg returned no frame data",
            )
        if len(result.stdout) > max_frame_bytes:
            raise FrameSamplingError(
                "FRAME_TOO_LARGE",
                f"sampled frame exceeds the {max_frame_bytes}-byte limit",
            )
        sampled.append(SampledFrame(
            id=f"{clean_prefix}frame-{index:03d}",
            timestamp_ms=request.timestamp_ms,
            scene_id=request.scene_id,
            width=width,
            height=height,
            extraction_reason=request.reason,
            encoded_bytes=len(result.stdout),
            data_url="data:image/jpeg;base64," + base64.b64encode(result.stdout).decode("ascii"),
        ))
    return FrameManifest(
        source_duration_ms=scene_report.source_duration_ms,
        source_width=source_width,
        source_height=source_height,
        frames=tuple(sampled),
        warnings=scene_report.warnings,
    )
