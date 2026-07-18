from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
import math
import re
import subprocess


SCENE_BOUNDARIES_VERSION = "scene_boundaries.v1"


class SceneBoundaryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SceneInterval:
    id: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["duration_ms"] = self.duration_ms
        return value


@dataclass(frozen=True)
class SceneBoundaryReport:
    source_duration_ms: int
    threshold: float
    min_scene_duration_ms: int
    raw_boundary_count: int
    boundaries_ms: tuple[int, ...]
    scenes: tuple[SceneInterval, ...]
    warnings: tuple[dict[str, Any], ...] = ()
    version: str = SCENE_BOUNDARIES_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_duration_ms": self.source_duration_ms,
            "method": "ffmpeg_select_scene_showinfo",
            "threshold": self.threshold,
            "min_scene_duration_ms": self.min_scene_duration_ms,
            "summary": {
                "raw_boundaries": self.raw_boundary_count,
                "boundaries": len(self.boundaries_ms),
                "scenes": len(self.scenes),
            },
            "boundaries_ms": list(self.boundaries_ms),
            "scenes": [scene.to_dict() for scene in self.scenes],
            "warnings": list(self.warnings),
        }


def parse_scene_times_ms(log_text: str) -> list[int]:
    times: set[int] = set()
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", str(log_text or "")):
        try:
            seconds = float(match.group(1))
        except ValueError:
            continue
        if math.isfinite(seconds) and seconds >= 0:
            times.add(int(round(seconds * 1000)))
    return sorted(times)


def _evenly_select(values: Sequence[int], count: int) -> list[int]:
    if count <= 0:
        return []
    if len(values) <= count:
        return list(values)
    if count == 1:
        return [values[len(values) // 2]]
    indexes = {
        int(round(index * (len(values) - 1) / (count - 1)))
        for index in range(count)
    }
    return [values[index] for index in sorted(indexes)]


def build_scene_boundaries(
    scene_times_ms: Sequence[int],
    *,
    source_duration_ms: int,
    threshold: float,
    min_scene_duration_ms: int = 1000,
    max_scenes: int = 64,
) -> SceneBoundaryReport:
    if source_duration_ms <= 0:
        raise SceneBoundaryError("SCENE_DURATION_INVALID", "source duration must be positive")
    if not math.isfinite(float(threshold)) or not 0 < float(threshold) < 1:
        raise SceneBoundaryError("SCENE_THRESHOLD_INVALID", "scene threshold must be between 0 and 1")
    if not 0 <= min_scene_duration_ms <= source_duration_ms:
        raise SceneBoundaryError(
            "SCENE_GAP_INVALID",
            "minimum scene duration must stay within the source duration",
        )
    if not 1 <= max_scenes <= 256:
        raise SceneBoundaryError("SCENE_LIMIT_INVALID", "max_scenes must be between 1 and 256")

    raw_values: set[int] = set()
    for value in scene_times_ms:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise SceneBoundaryError(
                "SCENE_BOUNDARY_INVALID",
                "scene boundaries must be finite numbers",
            ) from exc
        if not math.isfinite(parsed):
            raise SceneBoundaryError(
                "SCENE_BOUNDARY_INVALID",
                "scene boundaries must be finite numbers",
            )
        boundary = int(round(parsed))
        if 0 < boundary < source_duration_ms:
            raw_values.add(boundary)
    raw = sorted(raw_values)
    deduped: list[int] = []
    previous = 0
    for boundary in raw:
        if boundary - previous < min_scene_duration_ms:
            continue
        if source_duration_ms - boundary < min_scene_duration_ms:
            continue
        deduped.append(boundary)
        previous = boundary

    warnings: list[dict[str, Any]] = []
    maximum_boundaries = max_scenes - 1
    if len(deduped) > maximum_boundaries:
        original_count = len(deduped)
        deduped = _evenly_select(deduped, maximum_boundaries)
        warnings.append({
            "code": "SCENE_BOUNDARIES_CAPPED",
            "message": "Dense scene output was capped deterministically.",
            "detected": original_count,
            "retained": len(deduped),
        })

    points = [0, *deduped, source_duration_ms]
    scenes = tuple(
        SceneInterval(id=f"scene-{index:03d}", start_ms=start, end_ms=end)
        for index, (start, end) in enumerate(zip(points, points[1:]), start=1)
        if end > start
    )
    if not scenes:
        raise SceneBoundaryError("SCENE_OUTPUT_INVALID", "scene detection produced no intervals")
    return SceneBoundaryReport(
        source_duration_ms=source_duration_ms,
        threshold=round(float(threshold), 4),
        min_scene_duration_ms=min_scene_duration_ms,
        raw_boundary_count=len(raw),
        boundaries_ms=tuple(deduped),
        scenes=scenes,
        warnings=tuple(warnings),
    )


def detect_scene_boundaries(
    source: str | Path,
    *,
    source_duration_ms: int,
    threshold: float = 0.35,
    min_scene_duration_ms: int = 1000,
    max_scenes: int = 64,
    timeout: float = 300.0,
) -> SceneBoundaryReport:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-v",
        "info",
        "-i",
        str(Path(source).resolve()),
        "-vf",
        f"select='gt(scene,{float(threshold):.4f})',showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SceneBoundaryError("SCENE_FFMPEG_UNAVAILABLE", "FFmpeg is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise SceneBoundaryError("SCENE_DETECTION_TIMEOUT", "scene detection timed out") from exc
    if result.returncode != 0:
        reason = re.sub(r"\s+", " ", result.stderr or result.stdout or "").strip()[-1200:]
        raise SceneBoundaryError(
            "SCENE_DETECTION_FAILED",
            reason or "FFmpeg scene detection failed",
        )
    return build_scene_boundaries(
        parse_scene_times_ms(f"{result.stderr}\n{result.stdout}"),
        source_duration_ms=source_duration_ms,
        threshold=threshold,
        min_scene_duration_ms=min_scene_duration_ms,
        max_scenes=max_scenes,
    )
