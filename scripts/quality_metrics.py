#!/usr/bin/env python3
from __future__ import annotations

from argparse import ArgumentParser
from csv import DictWriter
from hashlib import sha256
from importlib.metadata import version
from io import StringIO
from pathlib import Path
from statistics import mean, median
from tempfile import TemporaryDirectory
from typing import Any
import json
import math
import re
import subprocess
import sys

REPORT_VERSION = "reference_quality.v1"
MODEL_PATH = Path("/usr/local/share/model/vmaf_v0.6.1.json")
MAX_FRAMES_LIMIT = 18_000
MAX_WORST_FRAMES = 20
XPSNR_FRAME = re.compile(
    r"n:\s*(?P<n>\d+)(?P<values>(?:\s+XPSNR\s+[A-Za-z]:\s+(?:inf|[0-9.]+))+)",
    re.IGNORECASE,
)
XPSNR_VALUE = re.compile(
    r"XPSNR\s+(?P<plane>[A-Za-z]):\s+(?P<value>inf|[0-9.]+)",
    re.IGNORECASE,
)
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def _arguments() -> Any:
    parser = ArgumentParser(description="Run bounded plan-aligned reference video metrics.")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--execution")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--max-frames", type=int, default=3600)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--worst-frames", type=int, default=10)
    return parser.parse_args()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or "quality command failed").strip().splitlines()[-1]
        raise RuntimeError(message[:300])
    return completed


def _ratio(value: str) -> float:
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _frame_rate(path: Path) -> float:
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate", "-of", "default=nw=1:nk=1",
        str(path),
    ])
    rate = _ratio(result.stdout.strip())
    if not 0 < rate <= 240:
        raise RuntimeError("video frame rate is unavailable")
    return rate


def _safe_execution(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments: list[dict[str, Any]] = []
    clips = payload.get("clips") if isinstance(payload, dict) else None
    for clip in clips[:8] if isinstance(clips, list) else []:
        if not isinstance(clip, dict):
            continue
        try:
            clip_index = min(max(0, int(clip.get("clip_index") or 0)), 8)
        except (TypeError, ValueError, OverflowError):
            clip_index = 0
        raw_segments = clip.get("segments")
        for segment in raw_segments[:64] if isinstance(raw_segments, list) else []:
            if not isinstance(segment, dict):
                continue
            window = segment.get("timeline_window") or {}
            if not isinstance(window, dict):
                window = {}
            try:
                start_ms = min(max(0, int(window.get("start_ms") or 0)), 86_400_000)
                end_ms = min(max(start_ms, int(window.get("end_ms") or 0)), 86_400_000)
            except (TypeError, ValueError, OverflowError):
                start_ms = 0
                end_ms = 0
            segment_id = str(segment.get("id") or "")
            operation = str(segment.get("operation") or "")
            strategy = str(segment.get("strategy") or "")
            segments.append({
                "clip_index": clip_index,
                "segment_id": segment_id if SAFE_TOKEN.fullmatch(segment_id) else "",
                "operation": operation if SAFE_TOKEN.fullmatch(operation) else "",
                "strategy": strategy if SAFE_TOKEN.fullmatch(strategy) else "",
                "start_ms": start_ms,
                "end_ms": end_ms,
            })
    return segments


def _planned_operation(segments: list[dict[str, Any]], timestamp_ms: int) -> dict[str, Any]:
    for segment in segments:
        if segment["start_ms"] <= timestamp_ms < segment["end_ms"]:
            return dict(segment)
    return {}


def _xpsnr(
    reference: Path,
    candidate: Path,
    *,
    max_frames: int,
    threads: int,
    directory: Path,
) -> list[dict[str, Any]]:
    stats = directory / "xpsnr.log"
    _run([
        "ffmpeg", "-nostdin", "-nostats", "-v", "error", "-threads", str(threads),
        "-i", str(reference), "-i", str(candidate),
        "-lavfi", f"xpsnr=stats_file={stats}",
        "-frames:v", str(max_frames), "-an", "-f", "null", "-",
    ])
    frames: list[dict[str, Any]] = []
    for match in XPSNR_FRAME.finditer(stats.read_text(encoding="utf-8")):
        values = {
            item.group("plane").lower(): (
                math.inf if item.group("value").lower() == "inf" else float(item.group("value"))
            )
            for item in XPSNR_VALUE.finditer(match.group("values"))
        }
        finite = [value for value in values.values() if math.isfinite(value)]
        frames.append({
            "n": int(match.group("n")),
            "xpsnr_y": values.get("y"),
            "xpsnr_u": values.get("u"),
            "xpsnr_v": values.get("v"),
            "xpsnr_min": min(finite) if finite else None,
        })
    return frames


def _merge_frames(
    data: dict[str, list[dict[str, Any]]],
    xpsnr: list[dict[str, Any]],
    *,
    frame_rate: float,
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    metric_groups = list(data.items()) + [("xpsnr", xpsnr)]
    for metric_name, metric_frames in metric_groups:
        for item in metric_frames:
            frame_number = int(item.get("n") or 0)
            # ffmpeg-quality-metrics emits VIF frames zero-based while its
            # VMAF, SSIM, and PSNR parsers and FFmpeg XPSNR are one-based.
            if metric_name == "vif":
                frame_number += 1
            row = merged.setdefault(frame_number, {"frame": frame_number})
            for key, value in item.items():
                if key != "n" and isinstance(value, (int, float)):
                    output_key = f"vif_{key}" if metric_name == "vif" else key
                    row[output_key] = value if math.isfinite(float(value)) else None
    for frame_number, row in merged.items():
        timestamp_ms = int(round(max(0, frame_number - 1) * 1000 / frame_rate))
        row["timestamp_ms"] = timestamp_ms
        row.update(_planned_operation(segments, timestamp_ms))
    return [merged[key] for key in sorted(merged)]


def _stats(values: list[float]) -> dict[str, float | None]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "mean": round(mean(finite), 6) if finite else None,
        "median": round(median(finite), 6) if finite else None,
        "minimum": round(min(finite), 6) if finite else None,
        "maximum": round(max(finite), 6) if finite else None,
    }


def _summary(frames: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("vmaf", "ssim_avg", "psnr_avg", "vif_scale_0", "xpsnr_min")
    return {
        key: _stats([
            float(item[key]) for item in frames if isinstance(item.get(key), (int, float))
        ])
        for key in keys
    }


def _worst_frames(frames: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ranked = [item for item in frames if isinstance(item.get("vmaf"), (int, float))]
    ranked.sort(key=lambda item: (float(item["vmaf"]), int(item["frame"])))
    allow = {
        "frame", "timestamp_ms", "vmaf", "ssim_avg", "psnr_avg", "xpsnr_min",
        "clip_index", "segment_id", "operation", "strategy", "start_ms", "end_ms",
    }
    return [{key: item[key] for key in allow if key in item} for item in ranked[:limit]]


def _tool_version(command: list[str]) -> str:
    return _run(command).stdout.splitlines()[0][:160]


def build_report(arguments: Any) -> dict[str, Any]:
    from ffmpeg_quality_metrics import FfmpegQualityMetrics

    reference = Path(arguments.reference).resolve(strict=True)
    candidate = Path(arguments.candidate).resolve(strict=True)
    execution_path = Path(arguments.execution).resolve(strict=True) if arguments.execution else None
    if not 1 <= arguments.max_frames <= MAX_FRAMES_LIMIT:
        raise ValueError(f"max-frames must be between 1 and {MAX_FRAMES_LIMIT}")
    if not 1 <= arguments.threads <= 8:
        raise ValueError("threads must be between 1 and 8")
    if not 1 <= arguments.worst_frames <= MAX_WORST_FRAMES:
        raise ValueError(f"worst-frames must be between 1 and {MAX_WORST_FRAMES}")
    frame_rate = _frame_rate(reference)
    segments = _safe_execution(execution_path)
    with TemporaryDirectory(prefix="openstoryline-quality-") as directory:
        metrics = FfmpegQualityMetrics(
            str(reference),
            str(candidate),
            threads=arguments.threads,
            num_frames=arguments.max_frames,
            tmp_dir=directory,
        )
        metrics.calculate(
            ["vmaf", "ssim", "psnr", "vif"],
            vmaf_options={"model_path": str(MODEL_PATH), "n_threads": arguments.threads},
        )
        xpsnr = _xpsnr(
            reference,
            candidate,
            max_frames=arguments.max_frames,
            threads=arguments.threads,
            directory=Path(directory),
        )
        frames = _merge_frames(
            metrics.data,
            xpsnr,
            frame_rate=frame_rate,
            segments=segments,
        )
    return {
        "version": REPORT_VERSION,
        "status": "complete",
        "inputs": {
            "execution_supplied": execution_path is not None,
            "frame_rate": round(frame_rate, 6),
        },
        "limits": {
            "max_frames": arguments.max_frames,
            "threads": arguments.threads,
            "worst_frames": arguments.worst_frames,
        },
        "provenance": {
            "ffmpeg": _tool_version(["ffmpeg", "-version"]),
            "ffmpeg_quality_metrics": version("ffmpeg-quality-metrics"),
            "vmaf_model": MODEL_PATH.name,
            "vmaf_model_sha256": sha256(MODEL_PATH.read_bytes()).hexdigest(),
        },
        "summary": _summary(frames),
        "worst_frames": _worst_frames(frames, arguments.worst_frames),
        "frames": frames,
    }


def _csv(report: dict[str, Any]) -> str:
    frames = report["frames"]
    columns = sorted({key for item in frames for key in item})
    output = StringIO()
    writer = DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(frames)
    return output.getvalue()


def main() -> int:
    try:
        arguments = _arguments()
        report = build_report(arguments)
        if arguments.format == "csv":
            sys.stdout.write(_csv(report))
        else:
            json.dump(report, sys.stdout, sort_keys=True, separators=(",", ":"))
            sys.stdout.write("\n")
        return 0
    except Exception as exc:
        json.dump(
            {"version": REPORT_VERSION, "status": "failed", "error": type(exc).__name__},
            sys.stderr,
            separators=(",", ":"),
        )
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
