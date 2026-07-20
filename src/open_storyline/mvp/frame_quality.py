from __future__ import annotations

from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any, Sequence
import json
import math
import re
import subprocess

from open_storyline.mvp.creative_qa import QAInput


FRAME_QUALITY_VERSION = "frame_quality_qa.v1"
MAX_CLIPS = 8
MAX_CROP_SAMPLES = 64
MAX_REFERENCE_SAMPLES = 8
MAX_RAW_FRAMES = 32
MAX_COMMAND_OUTPUT = 4 * 1024 * 1024


class FrameQualityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _run(
    command: Sequence[str],
    *,
    timeout: float,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    if not 1 <= timeout <= 300:
        raise FrameQualityError("FRAME_QUALITY_TIMEOUT_INVALID", "timeout is outside bounds")
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=text,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FrameQualityError(
            "FRAME_QUALITY_TOOL_UNAVAILABLE",
            "FFmpeg or FFprobe is unavailable",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FrameQualityError(
            "FRAME_QUALITY_TIMEOUT",
            "bounded frame-quality analysis timed out",
        ) from exc
    output_size = len(completed.stdout or b"") + len(completed.stderr or b"")
    if output_size > MAX_COMMAND_OUTPUT:
        raise FrameQualityError(
            "FRAME_QUALITY_OUTPUT_TOO_LARGE",
            "frame-quality command output exceeded its limit",
        )
    return completed


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _ratio(value: Any) -> float:
    text = str(value or "0/0")
    try:
        numerator, denominator = text.split("/", 1)
        parsed = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return parsed if 0 < parsed <= 240 else 0.0


def _probe_frames(path: Path, *, timeout: float) -> dict[str, Any]:
    completed = _run([
        "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate,nb_frames,nb_read_frames,duration",
        "-show_entries", "format=duration,bit_rate,size", "-of", "json", str(path),
    ], timeout=timeout)
    if completed.returncode != 0:
        raise FrameQualityError("FRAME_QUALITY_PROBE_FAILED", "video frame probe failed")
    try:
        payload = json.loads(completed.stdout)
        stream = (payload.get("streams") or [])[0]
        container = payload.get("format") or {}
        duration = float(container.get("duration") or stream.get("duration") or 0)
        decoded_frames = int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0)
        frame_rate = _ratio(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
        return {
            "duration_ms": int(round(duration * 1000)),
            "frame_rate": round(frame_rate, 3),
            "decoded_frames": decoded_frames,
            "expected_frames": int(round(duration * frame_rate)) if frame_rate > 0 else 0,
            "bit_rate": int(container.get("bit_rate") or 0),
            "bytes": int(container.get("size") or path.stat().st_size),
        }
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise FrameQualityError(
            "FRAME_QUALITY_PROBE_INVALID",
            "video frame probe returned incomplete metadata",
        ) from exc


_CROP_PATTERN = re.compile(
    r"(?:pts_time|t):(?P<time>[0-9.]+).*?crop=(?P<width>\d+):(?P<height>\d+):"
    r"(?P<x>\d+):(?P<y>\d+)"
)


def _execution_segment(execution: dict[str, Any], timestamp_ms: int) -> dict[str, Any] | None:
    for segment in execution.get("segments") or []:
        window = segment.get("timeline_window") or {}
        if int(window.get("start_ms") or 0) <= timestamp_ms < int(window.get("end_ms") or 0):
            return segment
    return None


def _near_transition(segment: dict[str, Any] | None, timestamp_ms: int) -> bool:
    if not segment:
        return False
    window = segment.get("timeline_window") or {}
    start = int(window.get("start_ms") or 0)
    transition = int(segment.get("transition_duration_ms") or 0)
    return str(segment.get("transition_kind") or "cut") != "cut" and timestamp_ms < start + transition + 250


def _crop_analysis(
    path: Path,
    *,
    execution: dict[str, Any],
    width: int,
    height: int,
    timeout: float,
) -> dict[str, Any]:
    completed = _run([
        "ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(path),
        "-vf", "fps=2,cropdetect=limit=24:round=2:reset=1",
        "-frames:v", str(MAX_CROP_SAMPLES), "-an", "-f", "null", "-",
    ], timeout=timeout)
    if completed.returncode != 0:
        raise FrameQualityError("FRAME_QUALITY_CROPDETECT_FAILED", "active-picture analysis failed")
    samples: list[dict[str, Any]] = []
    for match in _CROP_PATTERN.finditer(completed.stderr or ""):
        timestamp_ms = int(round(float(match.group("time")) * 1000))
        segment = _execution_segment(execution, timestamp_ms)
        if _near_transition(segment, timestamp_ms):
            continue
        active_width = int(match.group("width"))
        active_height = int(match.group("height"))
        expected_ratio = float((segment or {}).get("expected_active_area_ratio") or 1.0)
        strategy = str((segment or {}).get("strategy") or "unknown")
        samples.append({
            "timestamp_ms": timestamp_ms,
            "strategy": strategy,
            "expected_active_area_ratio": round(expected_ratio, 6),
            "active_width_ratio": round(active_width / width, 6),
            "active_height_ratio": round(active_height / height, 6),
            "active_area_ratio": round((active_width * active_height) / (width * height), 6),
            "crop": {
                "width": active_width,
                "height": active_height,
                "x": int(match.group("x")),
                "y": int(match.group("y")),
            },
        })
        if len(samples) >= MAX_CROP_SAMPLES:
            break
    fill_samples = [
        item for item in samples
        if item["strategy"] == "crop" and item["expected_active_area_ratio"] >= 0.9
    ]
    ratios = [item["active_area_ratio"] for item in fill_samples]
    heights = [item["active_height_ratio"] for item in fill_samples]
    return {
        "status": "available" if samples else "unavailable",
        "sample_rate_fps": 2,
        "samples": samples,
        "summary": {
            "samples": len(samples),
            "fill_samples": len(fill_samples),
            "median_active_area_ratio": round(median(ratios), 6) if ratios else None,
            "minimum_active_area_ratio": round(min(ratios), 6) if ratios else None,
            "median_active_height_ratio": round(median(heights), 6) if heights else None,
            "minimum_active_height_ratio": round(min(heights), 6) if heights else None,
        },
    }


def _raw_defect_metrics(
    path: Path,
    *,
    timeout: float,
    sample_width: int = 180,
    sample_height: int = 320,
) -> dict[str, Any]:
    completed = _run([
        "ffmpeg", "-v", "error", "-i", str(path),
        "-vf", f"fps=1,scale={sample_width}:{sample_height},format=gray",
        "-frames:v", str(MAX_RAW_FRAMES), "-f", "rawvideo", "pipe:1",
    ], timeout=timeout, text=False)
    if completed.returncode != 0:
        raise FrameQualityError("FRAME_QUALITY_SIGNAL_FAILED", "frame signal analysis failed")
    frame_size = sample_width * sample_height
    raw = bytes(completed.stdout or b"")
    frame_count = min(MAX_RAW_FRAMES, len(raw) // frame_size)
    laplacian_variances: list[float] = []
    blockiness_ratios: list[float] = []
    luma_ranges: list[int] = []
    for frame_index in range(frame_count):
        frame = raw[frame_index * frame_size:(frame_index + 1) * frame_size]
        if not frame:
            continue
        luma_ranges.append(max(frame) - min(frame))
        laplacian: list[int] = []
        boundary_diffs: list[int] = []
        interior_diffs: list[int] = []
        for y in range(1, sample_height - 1, 2):
            row = y * sample_width
            for x in range(1, sample_width - 1, 2):
                index = row + x
                center = frame[index]
                laplacian.append(
                    frame[index - 1] + frame[index + 1]
                    + frame[index - sample_width] + frame[index + sample_width]
                    - 4 * center
                )
        for y in range(sample_height):
            row = y * sample_width
            for x in range(1, sample_width):
                difference = abs(frame[row + x] - frame[row + x - 1])
                (boundary_diffs if x % 8 == 0 else interior_diffs).append(difference)
        for y in range(1, sample_height):
            row = y * sample_width
            previous = row - sample_width
            for x in range(sample_width):
                difference = abs(frame[row + x] - frame[previous + x])
                (boundary_diffs if y % 8 == 0 else interior_diffs).append(difference)
        if laplacian:
            average = sum(laplacian) / len(laplacian)
            variance = sum((value - average) ** 2 for value in laplacian) / len(laplacian)
            laplacian_variances.append(variance)
        boundary = sum(boundary_diffs) / max(1, len(boundary_diffs))
        interior = sum(interior_diffs) / max(1, len(interior_diffs))
        blockiness_ratios.append(boundary / max(0.25, interior))
    return {
        "status": "available" if frame_count else "unavailable",
        "sample_rate_fps": 1,
        "sample_dimensions": {"width": sample_width, "height": sample_height},
        "frames": frame_count,
        "median_laplacian_variance": (
            round(median(laplacian_variances), 3) if laplacian_variances else None
        ),
        "minimum_laplacian_variance": (
            round(min(laplacian_variances), 3) if laplacian_variances else None
        ),
        "median_blockiness_ratio": (
            round(median(blockiness_ratios), 3) if blockiness_ratios else None
        ),
        "maximum_blockiness_ratio": (
            round(max(blockiness_ratios), 3) if blockiness_ratios else None
        ),
        "median_luma_range": round(median(luma_ranges), 3) if luma_ranges else None,
        "minimum_luma_range": min(luma_ranges) if luma_ranges else None,
    }


def _overlay_active(segment: dict[str, Any], timestamp_ms: int) -> bool:
    for overlay in segment.get("overlays") or []:
        window = overlay.get("timeline_window") or {}
        if int(window.get("start_ms") or 0) <= timestamp_ms < int(window.get("end_ms") or 0):
            return True
    return False


def _reference_candidates(execution: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for segment in execution.get("segments") or []:
        if str(segment.get("transition_kind") or "cut") == "xfade":
            continue
        window = segment.get("timeline_window") or {}
        start = int(window.get("start_ms") or 0)
        end = int(window.get("end_ms") or 0)
        transition = int(segment.get("transition_duration_ms") or 0)
        safe_start = start + transition + 250
        safe_end = end - 250
        if safe_end <= safe_start:
            continue
        for ratio in (0.25, 0.5, 0.75):
            timestamp = int(round(safe_start + (safe_end - safe_start) * ratio))
            if not _overlay_active(segment, timestamp):
                candidates.append((timestamp, segment))
    candidates.sort(key=lambda item: item[0])
    if len(candidates) <= MAX_REFERENCE_SAMPLES:
        return candidates
    return [
        candidates[round(index * (len(candidates) - 1) / (MAX_REFERENCE_SAMPLES - 1))]
        for index in range(MAX_REFERENCE_SAMPLES)
    ]


def _composition_filter(segment: dict[str, Any], *, width: int, height: int) -> str:
    strategy = str(segment.get("strategy") or "")
    if strategy == "crop":
        crop = segment.get("crop") or {}
        values = [int(crop.get(name) or 0) for name in ("width", "height", "x", "y")]
        if min(values[:2]) <= 0 or min(values[2:]) < 0:
            raise FrameQualityError("FRAME_QUALITY_REFERENCE_INVALID", "crop evidence is invalid")
        return f"crop={values[0]}:{values[1]}:{values[2]}:{values[3]},scale={width}:{height}"
    if strategy in {"fit", "letterbox", "source"}:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    raise FrameQualityError("FRAME_QUALITY_REFERENCE_INVALID", "composition strategy is unsupported")


def _metric(log: str, pattern: str) -> float | None:
    matches = re.findall(pattern, log)
    return _finite(matches[-1]) if matches else None


def _reference_metrics(
    *,
    source: Path,
    delivery: Path,
    execution: dict[str, Any],
    width: int,
    height: int,
    timeout: float,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    mask_height = max(2, int(height * 0.70) // 2 * 2)
    with TemporaryDirectory(prefix="openstoryline-frame-quality-") as directory:
        root = Path(directory)
        for index, (timestamp_ms, segment) in enumerate(_reference_candidates(execution), start=1):
            timeline = segment.get("timeline_window") or {}
            source_window = segment.get("source_window") or {}
            source_ms = int(source_window.get("start_ms") or 0) + (
                timestamp_ms - int(timeline.get("start_ms") or 0)
            )
            reference = root / f"reference-{index:02d}.png"
            distorted = root / f"delivery-{index:02d}.png"
            reference_filter = (
                f"{_composition_filter(segment, width=width, height=height)},"
                f"setsar=1,crop={width}:{mask_height}:0:0"
            )
            delivery_filter = f"crop={width}:{mask_height}:0:0"
            for path, seek_ms, filtergraph, target in (
                (source, source_ms, reference_filter, reference),
                (delivery, timestamp_ms, delivery_filter, distorted),
            ):
                completed = _run([
                    "ffmpeg", "-y", "-v", "error", "-ss", f"{seek_ms / 1000:.3f}",
                    "-i", str(path), "-frames:v", "1", "-vf", filtergraph, str(target),
                ], timeout=timeout)
                if completed.returncode != 0 or not target.is_file():
                    raise FrameQualityError(
                        "FRAME_QUALITY_REFERENCE_FAILED",
                        "aligned reference frame reconstruction failed",
                    )
            ssim = _run([
                "ffmpeg", "-hide_banner", "-v", "info", "-i", str(distorted),
                "-i", str(reference), "-lavfi", "ssim", "-f", "null", "-",
            ], timeout=timeout)
            psnr = _run([
                "ffmpeg", "-hide_banner", "-v", "info", "-i", str(distorted),
                "-i", str(reference), "-lavfi", "psnr", "-f", "null", "-",
            ], timeout=timeout)
            if ssim.returncode != 0 or psnr.returncode != 0:
                raise FrameQualityError(
                    "FRAME_QUALITY_REFERENCE_FAILED",
                    "aligned reference metric calculation failed",
                )
            samples.append({
                "timestamp_ms": timestamp_ms,
                "source_timestamp_ms": source_ms,
                "segment_id": str(segment.get("id") or "")[:80],
                "operation": str(segment.get("operation") or "")[:80],
                "strategy": str(segment.get("strategy") or "")[:40],
                "caption_region_masked": True,
                "ssim": _metric(ssim.stderr or "", r"All:([0-9.]+)"),
                "psnr": _metric(psnr.stderr or "", r"average:([0-9.]+)"),
            })
    ssim_values = [item["ssim"] for item in samples if item["ssim"] is not None]
    psnr_values = [item["psnr"] for item in samples if item["psnr"] is not None]
    return {
        "status": "available" if samples else "not_applicable",
        "samples": samples,
        "summary": {
            "samples": len(samples),
            "median_ssim": round(median(ssim_values), 6) if ssim_values else None,
            "minimum_ssim": round(min(ssim_values), 6) if ssim_values else None,
            "median_psnr": round(median(psnr_values), 6) if psnr_values else None,
            "minimum_psnr": round(min(psnr_values), 6) if psnr_values else None,
            "xpsnr": "unavailable_in_ffmpeg_build",
        },
    }


def _finding(code: str, severity: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "details": details}


def _clip_report(
    item: QAInput,
    *,
    source: Path,
    execution: dict[str, Any],
    width: int,
    height: int,
    strict: bool,
    timeout: float,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    probe = _probe_frames(item.video_path, timeout=timeout)
    crop = _crop_analysis(
        item.video_path,
        execution=execution,
        width=width,
        height=height,
        timeout=timeout,
    )
    defects = _raw_defect_metrics(item.video_path, timeout=timeout)
    reference = _reference_metrics(
        source=source,
        delivery=item.video_path,
        execution=execution,
        width=width,
        height=height,
        timeout=timeout,
    )
    crop_summary = crop["summary"]
    if crop["status"] == "unavailable":
        findings.append(_finding("ACTIVE_PICTURE_UNAVAILABLE", "blocker" if strict else "warning"))
    elif crop_summary["fill_samples"]:
        if (
            float(crop_summary["median_active_area_ratio"] or 0) < 0.75
            or float(crop_summary["median_active_height_ratio"] or 0) < 0.80
        ):
            findings.append(_finding(
                "ACTIVE_PICTURE_TOO_SMALL",
                "blocker",
                median_active_area_ratio=crop_summary["median_active_area_ratio"],
                median_active_height_ratio=crop_summary["median_active_height_ratio"],
            ))
    if defects["status"] == "unavailable":
        findings.append(_finding("FRAME_SIGNAL_UNAVAILABLE", "blocker" if strict else "warning"))
    elif float(defects.get("median_luma_range") or 0) < 8:
        findings.append(_finding("FRAME_SIGNAL_COLLAPSED", "blocker"))
    else:
        if float(defects.get("median_laplacian_variance") or 0) < 8:
            findings.append(_finding("SEVERE_BLUR_REVIEW", "warning"))
        if (
            float(defects.get("median_blockiness_ratio") or 0) > 3.0
            and float(defects.get("maximum_blockiness_ratio") or 0) > 4.0
        ):
            findings.append(_finding("SEVERE_BLOCKINESS_REVIEW", "warning"))
    reference_summary = reference["summary"]
    if reference["status"] == "available" and (
        float(reference_summary.get("minimum_ssim") or 1) < 0.65
        or float(reference_summary.get("minimum_psnr") or 100) < 18
    ):
        findings.append(_finding(
            "REFERENCE_QUALITY_CATASTROPHIC",
            "blocker",
            minimum_ssim=reference_summary.get("minimum_ssim"),
            minimum_psnr=reference_summary.get("minimum_psnr"),
        ))
    status = (
        "blocker" if any(item["severity"] == "blocker" for item in findings)
        else "warning" if findings else "pass"
    )
    return {
        "clip_index": item.clip_index,
        "video": item.video_path.name,
        "status": status,
        "frame_rate": probe,
        "active_picture": crop,
        "defects": defects,
        "reference_metrics": reference,
        "findings": findings,
    }


def build_frame_quality_report(
    inputs: Sequence[QAInput],
    *,
    source: str | Path,
    render_execution: dict[str, Any],
    expected_width: int,
    expected_height: int,
    strict: bool = True,
    timeout: float = 90.0,
) -> dict[str, Any]:
    source_path = Path(source).resolve()
    executions = {
        int(item.get("clip_index") or 0): item
        for item in render_execution.get("clips") or []
    }
    clips: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for item in inputs[:MAX_CLIPS]:
        execution = executions.get(item.clip_index)
        if execution is None:
            finding = _finding("FRAME_EXECUTION_MISSING", "blocker")
            findings.append(finding)
            continue
        try:
            report = _clip_report(
                item,
                source=source_path,
                execution=execution,
                width=expected_width,
                height=expected_height,
                strict=strict,
                timeout=timeout,
            )
        except FrameQualityError as exc:
            report = {
                "clip_index": item.clip_index,
                "video": item.video_path.name,
                "status": "blocker" if strict else "warning",
                "findings": [_finding(exc.code, "blocker" if strict else "warning")],
            }
        clips.append(report)
        findings.extend(report["findings"])
    if not clips:
        findings.append(_finding("FRAME_QUALITY_OUTPUT_MISSING", "blocker"))
    status = (
        "blocker" if any(item["severity"] == "blocker" for item in findings)
        else "warning" if findings else "pass"
    )
    return {
        "version": FRAME_QUALITY_VERSION,
        "status": status,
        "strict_thresholds": strict,
        "limits": {
            "clips": MAX_CLIPS,
            "crop_samples_per_clip": MAX_CROP_SAMPLES,
            "reference_samples_per_clip": MAX_REFERENCE_SAMPLES,
            "raw_frames_per_clip": MAX_RAW_FRAMES,
            "command_timeout_seconds": timeout,
        },
        "thresholds": {
            "fill_median_active_area_ratio": 0.75,
            "fill_median_active_height_ratio": 0.80,
            "collapsed_median_luma_range": 8,
            "catastrophic_minimum_ssim": 0.65,
            "catastrophic_minimum_psnr": 18,
        },
        "summary": {
            "clips_analyzed": len(clips),
            "blockers": sum(item["severity"] == "blocker" for item in findings),
            "warnings": sum(item["severity"] == "warning" for item in findings),
        },
        "clips": clips,
        "findings": findings[:128],
    }
