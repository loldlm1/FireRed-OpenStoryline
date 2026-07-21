from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence
import asyncio
import json
import math
import os
import re
import subprocess

from pydantic import BaseModel, ConfigDict, Field, field_validator

from open_storyline.mvp.frame_sampling import sample_frames
from open_storyline.mvp.scene_boundaries import build_scene_boundaries
from open_storyline.mvp.structured_outputs import SEMANTIC_QA_SCHEMA


RENDER_QA_VERSION = "render_qa.v1"
RETENTION_RHYTHM_QA_VERSION = "retention_rhythm_qa.v1"
CREATIVE_CONFORMANCE_VERSION = "creative_conformance.v1"
SEMANTIC_QA_VERSION = "semantic_output_review.v1"
ASSET_VISIBILITY_VERSION = "asset_visibility.v1"
QA_NOTICE = (
    "These deterministic rhythm heuristics identify editing risks; they do not "
    "predict retention, engagement, or virality."
)

_MAX_QA_CLIPS = 8
_MAX_COMMAND_OUTPUT = 1_000_000
_MAX_FINDINGS = 128
_MAX_ASSET_VISIBILITY_OVERLAYS = 16
_SEVERITY_ORDER = {"blocker": 0, "warning": 1, "review": 2}
_ASSET_VISIBILITY_MIN_OPACITY = 0.75
_ASSET_VISIBILITY_MIN_SSIM = 0.6


class CreativeQAError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class QAInput:
    clip_index: int
    video_path: Path
    expected_duration_ms: int
    subtitle_path: Path | None = None


@dataclass(frozen=True)
class CreativeQAArtifacts:
    render_qa_path: Path
    rhythm_qa_path: Path
    conformance_path: Path
    render_qa: dict[str, Any]
    rhythm_qa: dict[str, Any]
    conformance: dict[str, Any]


class _SemanticObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clip_index: int = Field(ge=1, le=_MAX_QA_CLIPS)
    frame_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    planned_focus_visible: bool
    relevant: bool
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    note: str = Field(default="", max_length=240)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str) -> str:
        return _clean_text(value, limit=240)


class _SemanticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["pass", "review"]
    summary: str = Field(min_length=1, max_length=500)
    observations: tuple[_SemanticObservation, ...] = Field(max_length=8)

    @field_validator("summary")
    @classmethod
    def clean_summary(cls, value: str) -> str:
        return _clean_text(value, limit=500)


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+\-/=]+", "Bearer ***", text)
    return text[:limit]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    value = raw.strip().lower()
    if value not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise CreativeQAError("QA_CONFIG_INVALID", f"{name} must be true or false")
    return value in {"1", "true", "yes", "on"}


def creative_qa_enabled(config: Any) -> bool:
    return _env_bool(
        "OPENSTORYLINE_CREATIVE_QA_ENABLED",
        bool(getattr(config, "creative_qa_enabled", True)),
    )


def creative_qa_strict(config: Any) -> bool:
    return _env_bool(
        "OPENSTORYLINE_CREATIVE_QA_STRICT",
        bool(getattr(config, "creative_qa_strict", True)),
    )


def semantic_qa_enabled(config: Any) -> bool:
    return _env_bool(
        "OPENSTORYLINE_SEMANTIC_QA_ENABLED",
        bool(getattr(config, "semantic_qa_enabled", False)),
    )


def semantic_qa_frame_limit(config: Any) -> int:
    raw = os.getenv("OPENSTORYLINE_SEMANTIC_QA_MAX_FRAMES")
    try:
        value = int(
            raw
            if raw is not None
            else getattr(config, "semantic_qa_max_frames", 4)
        )
    except (TypeError, ValueError) as exc:
        raise CreativeQAError(
            "QA_CONFIG_INVALID",
            "OPENSTORYLINE_SEMANTIC_QA_MAX_FRAMES must be an integer",
        ) from exc
    if not 1 <= value <= 8:
        raise CreativeQAError(
            "QA_CONFIG_INVALID",
            "semantic QA frame count must be between 1 and 8",
        )
    return value


def _finding(
    code: str,
    severity: Literal["blocker", "warning", "review"],
    message: str,
    **details: Any,
) -> dict[str, Any]:
    value = {
        "code": code,
        "severity": severity,
        "message": _clean_text(message, limit=500),
    }
    if details:
        value["details"] = details
    return value


def _status(findings: Iterable[dict[str, Any]]) -> str:
    severities = {str(item.get("severity")) for item in findings}
    if "blocker" in severities:
        return "blocker"
    if "warning" in severities:
        return "warning"
    if "review" in severities:
        return "review"
    return "pass"


def _summary(findings: Sequence[dict[str, Any]]) -> dict[str, int]:
    return {
        "blockers": sum(item.get("severity") == "blocker" for item in findings),
        "warnings": sum(item.get("severity") == "warning" for item in findings),
        "review_notes": sum(item.get("severity") == "review" for item in findings),
    }


def _run(command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    if not 1 <= timeout <= 300:
        raise CreativeQAError("QA_TIMEOUT_INVALID", "QA timeout is outside the safe range")
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CreativeQAError("QA_MEDIA_TOOL_UNAVAILABLE", "FFmpeg or FFprobe is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise CreativeQAError("QA_MEDIA_TOOL_TIMEOUT", "bounded media analysis timed out") from exc
    if len(completed.stdout) + len(completed.stderr) > _MAX_COMMAND_OUTPUT:
        raise CreativeQAError("QA_MEDIA_OUTPUT_TOO_LARGE", "media analysis output exceeded its limit")
    return completed


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_segments(log: str, kind: str) -> list[dict[str, float]]:
    if kind == "black":
        pattern = re.compile(
            r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)"
            r"\s+black_duration:(?P<duration>[0-9.]+)"
        )
        return [
            {
                "start": round(float(match.group("start")), 3),
                "end": round(float(match.group("end")), 3),
                "duration": round(float(match.group("duration")), 3),
            }
            for match in pattern.finditer(log)
        ][:_MAX_FINDINGS]

    prefix = "freeze" if kind == "freeze" else "silence"
    starts: list[float] = []
    segments: list[dict[str, float]] = []
    for line in log.splitlines():
        start_match = re.search(rf"{prefix}_start:\s*([0-9.]+)", line)
        if start_match:
            starts.append(float(start_match.group(1)))
            continue
        end_match = re.search(
            rf"{prefix}_end:\s*([0-9.]+)\s*\|\s*{prefix}_duration:\s*([0-9.]+)",
            line,
        )
        if not end_match:
            continue
        end = float(end_match.group(1))
        duration = float(end_match.group(2))
        start = starts.pop(0) if starts else max(0.0, end - duration)
        segments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
        })
        if len(segments) >= _MAX_FINDINGS:
            break
    return segments


def _probe(path: Path, *, timeout: float) -> dict[str, Any]:
    completed = _run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,duration,avg_frame_rate,r_frame_rate:format=duration",
        "-of",
        "json",
        str(path),
    ], timeout=timeout)
    if completed.returncode != 0:
        raise CreativeQAError("QA_VIDEO_INVALID", "the rendered video is not decodable")
    try:
        payload = json.loads(completed.stdout)
        streams = list(payload.get("streams") or [])
        video = next(item for item in streams if item.get("codec_type") == "video")
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        duration = _finite_float((payload.get("format") or {}).get("duration"))
        if duration is None:
            duration = _finite_float(video.get("duration"))
        width = int(video.get("width"))
        height = int(video.get("height"))
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CreativeQAError("QA_VIDEO_INVALID", "FFprobe returned incomplete media data") from exc
    if duration is None or duration <= 0 or width <= 0 or height <= 0:
        raise CreativeQAError("QA_VIDEO_INVALID", "FFprobe returned invalid media values")
    return {
        "duration_ms": int(round(duration * 1000)),
        "width": width,
        "height": height,
        "video_codec": _clean_text(video.get("codec_name"), limit=40),
        "audio_codec": _clean_text(audio.get("codec_name"), limit=40) if audio else "",
        "has_audio": audio is not None,
    }


def _probe_visual_asset(path: Path, *, timeout: float) -> dict[str, Any]:
    completed = _run([
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height:format=duration",
        "-of",
        "json",
        str(path),
    ], timeout=timeout)
    if completed.returncode != 0:
        raise CreativeQAError("asset_visibility_asset_invalid", "asset is not decodable")
    try:
        payload = json.loads(completed.stdout)
        stream = (payload.get("streams") or [])[0]
        width = int(stream.get("width"))
        height = int(stream.get("height"))
        duration = _finite_float((payload.get("format") or {}).get("duration")) or 0.0
    except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CreativeQAError(
            "asset_visibility_asset_invalid",
            "asset media metadata is incomplete",
        ) from exc
    if width <= 0 or height <= 0:
        raise CreativeQAError("asset_visibility_asset_invalid", "asset dimensions are invalid")
    return {"width": width, "height": height, "duration_seconds": duration}


def _overlay_geometry(
    overlay: dict[str, Any],
    *,
    asset_width: int,
    asset_height: int,
    output_width: int,
    output_height: int,
) -> tuple[int, int, int, int]:
    width = max(2, int(round(output_width * float(overlay.get("width_ratio") or 0.35))))
    width -= width % 2
    height = max(2, int(round((asset_height * width / asset_width) / 2)) * 2)
    margin_ratio = float(overlay.get("margin_ratio") or 0.035)
    position = str(overlay.get("position") or "center")
    if position in {"top_left", "bottom_left"}:
        x = int(round(output_width * margin_ratio))
    elif position in {"top_right", "bottom_right"}:
        x = int(round(output_width - width - output_width * margin_ratio))
    else:
        x = int(round((output_width - width) / 2))
    if position in {"top_left", "top_right", "top"}:
        y = int(round(output_height * margin_ratio))
    elif position in {"bottom_left", "bottom_right", "bottom"}:
        y = int(round(output_height - height - output_height * margin_ratio))
    else:
        y = int(round((output_height - height) / 2))
    if x < 0 or y < 0 or x + width > output_width or y + height > output_height:
        raise CreativeQAError(
            "asset_visibility_geometry_invalid",
            "asset overlay extends outside the rendered frame",
        )
    return x, y, width, height


def _measure_asset_visibility(
    *,
    video_path: Path,
    asset_path: Path,
    midpoint_seconds: float,
    asset_offset_seconds: float,
    geometry: tuple[int, int, int, int],
    timeout: float,
) -> float:
    x, y, width, height = geometry
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-v",
        "info",
        "-ss",
        f"{midpoint_seconds:.3f}",
        "-i",
        str(video_path),
    ]
    if asset_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".avif"}:
        command.extend(["-loop", "1", "-i", str(asset_path)])
    else:
        command.extend([
            "-ss",
            f"{asset_offset_seconds:.3f}",
            "-i",
            str(asset_path),
        ])
    command.extend([
        "-filter_complex",
        (
            f"[0:v]crop={width}:{height}:{x}:{y},format=yuv420p[roi];"
            f"[1:v]scale={width}:{height},format=yuv420p[asset];"
            "[roi][asset]ssim"
        ),
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ])
    completed = _run(command, timeout=timeout)
    matches = re.findall(r"All:([0-9]+(?:\.[0-9]+)?)", completed.stderr or completed.stdout)
    if completed.returncode != 0 or not matches:
        raise CreativeQAError(
            "asset_visibility_analysis_unavailable",
            "rendered asset visibility could not be measured",
        )
    return round(float(matches[-1]), 6)


def build_asset_visibility_report(
    inputs: Sequence[QAInput],
    *,
    render_execution: dict[str, Any],
    resolved_assets: dict[str, Path],
    expected_width: int,
    expected_height: int,
    strict: bool = True,
    timeout: float = 90.0,
) -> dict[str, Any]:
    input_by_clip = {item.clip_index: item for item in inputs[:_MAX_QA_CLIPS]}
    asset_paths = {str(key): Path(value) for key, value in resolved_assets.items()}
    overlays: list[dict[str, Any]] = []
    for clip in render_execution.get("clips") or []:
        clip_index = int(clip.get("clip_index") or 0)
        for segment in clip.get("segments") or []:
            for overlay in segment.get("overlays") or []:
                asset_id = str(overlay.get("asset_id") or "")
                if asset_id:
                    overlays.append({**overlay, "clip_index": clip_index, "asset_id": asset_id})

    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for index, left in enumerate(overlays):
        left_window = left.get("timeline_window") or {}
        for right in overlays[index + 1:]:
            if left["clip_index"] != right["clip_index"] or left["asset_id"] != right["asset_id"]:
                continue
            right_window = right.get("timeline_window") or {}
            overlap = min(
                int(left_window.get("end_ms") or 0),
                int(right_window.get("end_ms") or 0),
            ) - max(
                int(left_window.get("start_ms") or 0),
                int(right_window.get("start_ms") or 0),
            )
            if overlap > 0:
                findings.append(_finding(
                    "asset_overlay_duplicated",
                    "blocker" if strict else "warning",
                    "The same resolved asset is executed in overlapping overlays.",
                    clip_index=left["clip_index"],
                    asset_id=left["asset_id"],
                    overlap_ms=overlap,
                ))
                break

    for overlay in overlays[:_MAX_ASSET_VISIBILITY_OVERLAYS]:
        clip_index = int(overlay["clip_index"])
        source = input_by_clip.get(clip_index)
        window = overlay.get("timeline_window") or {}
        start_ms = int(window.get("start_ms") or 0)
        end_ms = int(window.get("end_ms") or 0)
        opacity = float(overlay.get("opacity") if overlay.get("opacity") is not None else 1.0)
        observation = {
            "clip_index": clip_index,
            "asset_id": overlay["asset_id"],
            "overlay_kind": str(overlay.get("kind") or ""),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "opacity": round(opacity, 6),
            "status": "blocker",
            "ssim": None,
            "error_code": None,
        }
        if source is None or end_ms <= start_ms:
            observation["error_code"] = "asset_visibility_timing_invalid"
            findings.append(_finding(
                "asset_visibility_timing_invalid",
                "blocker" if strict else "warning",
                "Asset visibility timing could not be mapped to a rendered clip.",
                clip_index=clip_index,
                asset_id=overlay["asset_id"],
            ))
            observations.append(observation)
            continue
        if opacity < _ASSET_VISIBILITY_MIN_OPACITY:
            observation["error_code"] = "asset_overlay_opacity_too_low"
            findings.append(_finding(
                "asset_overlay_opacity_too_low",
                "blocker" if strict else "warning",
                "Asset overlay opacity is below the deterministic visibility threshold.",
                clip_index=clip_index,
                asset_id=overlay["asset_id"],
                opacity=round(opacity, 6),
            ))
            observations.append(observation)
            continue
        asset_path = asset_paths.get(overlay["asset_id"])
        if asset_path is None or not asset_path.is_file():
            observation["error_code"] = "asset_visibility_asset_unresolved"
            findings.append(_finding(
                "asset_visibility_asset_unresolved",
                "blocker" if strict else "warning",
                "A rendered asset reference has no resolved media available for visibility analysis.",
                clip_index=clip_index,
                asset_id=overlay["asset_id"],
            ))
            observations.append(observation)
            continue
        try:
            asset_media = _probe_visual_asset(asset_path, timeout=timeout)
            geometry = _overlay_geometry(
                overlay,
                asset_width=asset_media["width"],
                asset_height=asset_media["height"],
                output_width=expected_width,
                output_height=expected_height,
            )
            elapsed_seconds = (end_ms - start_ms) / 2000
            duration_seconds = float(asset_media["duration_seconds"] or 0)
            asset_offset = (
                elapsed_seconds % duration_seconds
                if duration_seconds > 0
                else 0.0
            )
            score = _measure_asset_visibility(
                video_path=source.video_path,
                asset_path=asset_path,
                midpoint_seconds=(start_ms + end_ms) / 2000,
                asset_offset_seconds=asset_offset,
                geometry=geometry,
                timeout=timeout,
            )
            observation["ssim"] = score
            observation["status"] = "pass" if score >= _ASSET_VISIBILITY_MIN_SSIM else "blocker"
            if score < _ASSET_VISIBILITY_MIN_SSIM:
                findings.append(_finding(
                    "asset_overlay_not_visible",
                    "blocker" if strict else "warning",
                    "Rendered pixels do not match the resolved asset inside its declared overlay window.",
                    clip_index=clip_index,
                    asset_id=overlay["asset_id"],
                    ssim=score,
                ))
        except CreativeQAError as exc:
            observation["error_code"] = exc.code
            finding_code = (
                exc.code
                if exc.code.startswith("asset_visibility_")
                else "asset_visibility_analysis_unavailable"
            )
            findings.append(_finding(
                finding_code,
                "blocker" if strict else "review",
                "Asset visibility analysis was unavailable for a declared overlay.",
                clip_index=clip_index,
                asset_id=overlay["asset_id"],
                error_code=exc.code,
            ))
        observations.append(observation)

    if len(overlays) > _MAX_ASSET_VISIBILITY_OVERLAYS:
        findings.append(_finding(
            "asset_visibility_limit_reached",
            "blocker" if strict else "review",
            "The rendered asset overlay count exceeds the bounded visibility audit limit.",
            overlay_count=len(overlays),
            analyzed=_MAX_ASSET_VISIBILITY_OVERLAYS,
        ))
    return {
        "version": ASSET_VISIBILITY_VERSION,
        "status": _status(findings),
        "strict_thresholds": strict,
        "thresholds": {
            "minimum_opacity": _ASSET_VISIBILITY_MIN_OPACITY,
            "minimum_ssim": _ASSET_VISIBILITY_MIN_SSIM,
            "max_overlays": _MAX_ASSET_VISIBILITY_OVERLAYS,
        },
        "summary": {
            "overlays_analyzed": len(observations),
            "visible": sum(item["status"] == "pass" for item in observations),
            **_summary(findings),
        },
        "observations": observations,
        "findings": findings[:_MAX_FINDINGS],
    }


def _detect(path: Path, *, kind: str, timeout: float) -> tuple[list[dict[str, float]], str | None]:
    if kind == "silence":
        command = [
            "ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(path),
            "-af", "silencedetect=n=-35dB:d=1.5", "-vn", "-f", "null", "-",
        ]
    else:
        filter_value = (
            "blackdetect=d=0.25:pix_th=0.10"
            if kind == "black"
            else "freezedetect=n=-60dB:d=1.0"
        )
        command = [
            "ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(path),
            "-vf", filter_value, "-an", "-f", "null", "-",
        ]
    completed = _run(command, timeout=timeout)
    if completed.returncode != 0:
        return [], "analysis_unavailable"
    return _parse_segments(completed.stderr or completed.stdout, kind), None


def _structural_clip_report(
    item: QAInput,
    *,
    expected_width: int,
    expected_height: int,
    strict: bool,
    timeout: float,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    try:
        media = _probe(item.video_path, timeout=timeout)
    except CreativeQAError as exc:
        findings.append(_finding(exc.code, "blocker", "Rendered media structure could not be validated."))
        return {
            "clip_index": item.clip_index,
            "video": item.video_path.name,
            "status": "blocker",
            "media": None,
            "analysis": {"black": [], "freeze": [], "silence": []},
            "findings": findings,
        }

    if (media["width"], media["height"]) != (expected_width, expected_height):
        findings.append(_finding(
            "output_dimensions_mismatch",
            "blocker",
            "Rendered dimensions do not match the configured portrait output.",
            expected={"width": expected_width, "height": expected_height},
            actual={"width": media["width"], "height": media["height"]},
        ))
    if media["video_codec"] not in {"h264", "libx264"}:
        findings.append(_finding(
            "video_codec_unexpected",
            "blocker",
            "Rendered video codec is outside the remote MVP contract.",
            codec=media["video_codec"],
        ))
    if not media["has_audio"]:
        findings.append(_finding("audio_missing", "blocker", "Rendered output has no audio stream."))
    elif media["audio_codec"] != "aac":
        findings.append(_finding(
            "audio_codec_unexpected",
            "warning",
            "Rendered audio codec differs from the expected AAC output.",
            codec=media["audio_codec"],
        ))

    duration_delta = abs(media["duration_ms"] - item.expected_duration_ms)
    duration_tolerance = max(500, int(item.expected_duration_ms * 0.04))
    if duration_delta > duration_tolerance:
        findings.append(_finding(
            "duration_mismatch",
            "blocker",
            "Rendered duration differs materially from the validated clip duration.",
            expected_ms=item.expected_duration_ms,
            actual_ms=media["duration_ms"],
            tolerance_ms=duration_tolerance,
        ))

    analysis: dict[str, list[dict[str, float]]] = {}
    for kind in ("black", "freeze", "silence"):
        segments, error = _detect(item.video_path, kind=kind, timeout=timeout)
        analysis[kind] = segments
        if error:
            findings.append(_finding(
                f"{kind}_analysis_unavailable",
                "review",
                f"{kind.title()} analysis was unavailable and needs operator review.",
            ))

    duration_seconds = media["duration_ms"] / 1000
    black_total = sum(item["duration"] for item in analysis["black"])
    freeze_total = sum(item["duration"] for item in analysis["freeze"])
    silence_total = sum(item["duration"] for item in analysis["silence"])
    if black_total > 0:
        severity = "blocker" if strict and black_total > 1.0 else "warning"
        findings.append(_finding(
            "black_frames_detected",
            severity,
            "Black-frame intervals were detected in the rendered output.",
            total_seconds=round(black_total, 3),
            segments=analysis["black"],
        ))
    if freeze_total > 0:
        hard_limit = max(4.0, duration_seconds * 0.25)
        severity = "blocker" if strict and freeze_total > hard_limit else "warning"
        findings.append(_finding(
            "frozen_video_detected",
            severity,
            "Frozen-video intervals were detected; static content may still be intentional.",
            total_seconds=round(freeze_total, 3),
            hard_limit_seconds=round(hard_limit, 3),
            segments=analysis["freeze"],
        ))
    if silence_total > 0:
        hard_limit = max(6.0, duration_seconds * 0.35)
        severity = "blocker" if strict and silence_total > hard_limit else "warning"
        findings.append(_finding(
            "long_silence_detected",
            severity,
            "Long silence was detected in an output expected to retain source audio.",
            total_seconds=round(silence_total, 3),
            hard_limit_seconds=round(hard_limit, 3),
            segments=analysis["silence"],
        ))
    return {
        "clip_index": item.clip_index,
        "video": item.video_path.name,
        "status": _status(findings),
        "media": media,
        "analysis": analysis,
        "findings": sorted(findings, key=lambda finding: _SEVERITY_ORDER[finding["severity"]]),
    }


def build_render_qa_report(
    inputs: Sequence[QAInput],
    *,
    expected_width: int,
    expected_height: int,
    strict: bool = True,
    timeout: float = 90.0,
) -> dict[str, Any]:
    selected = tuple(inputs[:_MAX_QA_CLIPS])
    clips = [
        _structural_clip_report(
            item,
            expected_width=expected_width,
            expected_height=expected_height,
            strict=strict,
            timeout=timeout,
        )
        for item in selected
    ]
    findings = [finding for clip in clips for finding in clip["findings"]]
    if len(inputs) > _MAX_QA_CLIPS:
        findings.append(_finding(
            "qa_clip_limit_reached",
            "review",
            "Only the bounded maximum number of output clips was analyzed.",
            analyzed=_MAX_QA_CLIPS,
        ))
    if not clips:
        findings.append(_finding("qa_output_missing", "blocker", "No rendered output was available for QA."))
    return {
        "version": RENDER_QA_VERSION,
        "status": _status(findings),
        "strict_thresholds": strict,
        "thresholds": {
            "duration_tolerance": "max(500ms, 4%)",
            "black_min_seconds": 0.25,
            "freeze_min_seconds": 1.0,
            "silence_min_seconds": 1.5,
            "command_timeout_seconds": timeout,
            "max_clips": _MAX_QA_CLIPS,
        },
        "summary": {"clips_analyzed": len(clips), **_summary(findings)},
        "clips": clips,
        "findings": findings[:_MAX_FINDINGS],
    }


def _parse_srt(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    timestamp = re.compile(
        r"(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})\s+-->\s+"
        r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})"
    )
    cues: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="replace")):
        match = timestamp.search(block)
        if not match:
            continue
        values = {key: int(value) for key, value in match.groupdict().items()}
        start = ((values["sh"] * 60 + values["sm"]) * 60 + values["ss"]) * 1000 + values["sms"]
        end = ((values["eh"] * 60 + values["em"]) * 60 + values["es"]) * 1000 + values["ems"]
        if end <= start:
            continue
        text = _clean_text(block[match.end():], limit=500)
        cues.append({"start_ms": start, "end_ms": end, "text": text})
        if len(cues) >= 512:
            break
    return cues


def _timeline_gaps(duration_ms: int, events: Iterable[int]) -> list[dict[str, int]]:
    points = [0, *sorted({value for value in events if 0 < value < duration_ms}), duration_ms]
    return [
        {"start_ms": start, "end_ms": end, "duration_ms": end - start}
        for start, end in zip(points, points[1:])
        if end > start
    ]


def _clip_rhythm(
    execution: dict[str, Any],
    *,
    duration_ms: int,
    cues: Sequence[dict[str, Any]],
    strict: bool,
) -> dict[str, Any]:
    segments = list(execution.get("segments") or [])
    visual_events: set[int] = set()
    overlay_events: set[int] = set()
    for index, segment in enumerate(segments):
        window = segment.get("timeline_window") or {}
        start = int(window.get("start_ms") or 0)
        end = int(window.get("end_ms") or 0)
        if index > 0 and 0 < start < duration_ms:
            visual_events.add(start)
        for overlay in segment.get("overlays") or []:
            overlay_window = overlay.get("timeline_window") or {}
            overlay_start = int(overlay_window.get("start_ms") or 0)
            overlay_end = int(overlay_window.get("end_ms") or 0)
            if 0 < overlay_start < duration_ms:
                overlay_events.add(overlay_start)
            if 0 < overlay_end < duration_ms:
                overlay_events.add(overlay_end)
        if end and end < start:
            continue
    subtitle_events = {int(cue["start_ms"]) for cue in cues if 0 <= int(cue["start_ms"]) < duration_ms}
    attention_events = visual_events | overlay_events | subtitle_events
    visual_gaps = _timeline_gaps(duration_ms, visual_events | overlay_events)
    attention_gaps = _timeline_gaps(duration_ms, attention_events)
    longest_hold = max((gap["duration_ms"] for gap in visual_gaps), default=duration_ms)
    longest_attention_gap = max((gap["duration_ms"] for gap in attention_gaps), default=duration_ms)
    hook_events = sorted(value for value in attention_events if value <= min(duration_ms, 3000))

    findings: list[dict[str, Any]] = []
    if not hook_events:
        findings.append(_finding(
            "inactive_hook",
            "warning",
            "No scene, overlay, or subtitle change was observed in the opening hook window.",
            start_ms=0,
            end_ms=min(duration_ms, 3000),
        ))
    if longest_hold > 6000:
        severity = "blocker" if strict and longest_hold > 10_000 else "warning"
        findings.append(_finding(
            "long_visual_hold",
            severity,
            "A long interval has no planned scene or overlay change.",
            longest_hold_ms=longest_hold,
            warning_limit_ms=6000,
            blocker_limit_ms=10_000,
        ))
    if longest_attention_gap > 6000:
        severity = "blocker" if strict and longest_attention_gap > 10_000 else "warning"
        findings.append(_finding(
            "attention_gap",
            severity,
            "A long interval has no scene, overlay, or subtitle attention event.",
            longest_gap_ms=longest_attention_gap,
            warning_limit_ms=6000,
            blocker_limit_ms=10_000,
        ))

    longest_subtitle = max(
        (int(cue["end_ms"]) - int(cue["start_ms"]) for cue in cues),
        default=0,
    )
    subtitle_gaps = [
        max(0, int(right["start_ms"]) - int(left["end_ms"]))
        for left, right in zip(cues, cues[1:])
    ]
    longest_subtitle_gap = max(subtitle_gaps, default=0)
    if not cues:
        findings.append(_finding(
            "subtitle_timeline_unavailable",
            "review",
            "No output-aligned subtitle cues were available for cadence review.",
        ))
    if longest_subtitle > 4500:
        findings.append(_finding(
            "long_subtitle_hold",
            "warning",
            "A subtitle cue remains on screen longer than the cadence threshold.",
            longest_hold_ms=longest_subtitle,
            limit_ms=4500,
        ))
    if longest_subtitle_gap > 1500:
        findings.append(_finding(
            "subtitle_gap_review",
            "review",
            "A long subtitle gap should be checked against speech and intentional pauses.",
            longest_gap_ms=longest_subtitle_gap,
            review_limit_ms=1500,
        ))
    return {
        "clip_index": int(execution.get("clip_index") or 0),
        "video": Path(str(execution.get("video") or "")).name,
        "status": _status(findings),
        "metrics": {
            "duration_ms": duration_ms,
            "scene_changes": len(visual_events),
            "overlay_changes": len(overlay_events),
            "hook_attention_events": len(hook_events),
            "longest_visual_hold_ms": longest_hold,
            "longest_attention_gap_ms": longest_attention_gap,
            "subtitle_cues": len(cues),
            "longest_subtitle_hold_ms": longest_subtitle,
            "longest_subtitle_gap_ms": longest_subtitle_gap,
        },
        "findings": findings,
    }


def build_retention_rhythm_report(
    inputs: Sequence[QAInput],
    *,
    render_execution: dict[str, Any],
    strict: bool = True,
) -> dict[str, Any]:
    by_index = {item.clip_index: item for item in inputs[:_MAX_QA_CLIPS]}
    clips: list[dict[str, Any]] = []
    for execution in list(render_execution.get("clips") or [])[:_MAX_QA_CLIPS]:
        clip_index = int(execution.get("clip_index") or 0)
        source = by_index.get(clip_index)
        if source is None:
            continue
        clips.append(_clip_rhythm(
            execution,
            duration_ms=source.expected_duration_ms,
            cues=_parse_srt(source.subtitle_path),
            strict=strict,
        ))
    findings = [finding for clip in clips for finding in clip["findings"]]
    if not clips:
        findings.append(_finding(
            "rhythm_evidence_unavailable",
            "review",
            "No matching render-execution clips were available for rhythm analysis.",
        ))
    return {
        "version": RETENTION_RHYTHM_QA_VERSION,
        "status": _status(findings),
        "strict_thresholds": strict,
        "notice": QA_NOTICE,
        "thresholds": {
            "hook_window_ms": 3000,
            "visual_hold_warning_ms": 6000,
            "visual_hold_blocker_ms": 10_000,
            "attention_gap_warning_ms": 6000,
            "attention_gap_blocker_ms": 10_000,
            "subtitle_hold_warning_ms": 4500,
            "subtitle_gap_review_ms": 1500,
        },
        "summary": {"clips_analyzed": len(clips), **_summary(findings)},
        "clips": clips,
        "findings": findings[:_MAX_FINDINGS],
    }


def _planned_operations(edit_plan: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    operations = {"subtitles"}
    conditional_operations: set[str] = set()
    assets: set[str] = set()
    for clip in edit_plan.get("clips") or []:
        for segment in clip.get("segments") or []:
            layout = segment.get("layout") or {}
            mode = str(layout.get("mode") or "")
            mapped_layout = {
                "crop": "crop",
                "fit": "fit",
                "letterbox": "letterbox",
                "source": "source_cutaway",
            }.get(mode)
            if mapped_layout:
                operations.add(mapped_layout)
            if mode == "crop" and float(layout.get("max_zoom") or 1) > 1:
                operations.add("focus_zoom")
                conditional_operations.add("focus_zoom")
            transition = str((segment.get("transition_in") or {}).get("kind") or "cut")
            operations.add({"cut": "hard_cut", "fade": "fade", "xfade": "xfade"}.get(transition, transition))
            for overlay in segment.get("overlays") or []:
                mapped_overlay = {
                    "image": "image_overlay",
                    "text": "text_emphasis",
                    "source": "source_cutaway",
                    "pip": "pip",
                }.get(str(overlay.get("kind") or ""))
                if mapped_overlay:
                    operations.add(mapped_overlay)
        for request in clip.get("asset_requests") or []:
            if request.get("id"):
                assets.add(str(request["id"]))
    return operations, conditional_operations, assets


def _executed_operations(render_execution: dict[str, Any]) -> tuple[set[str], set[str], int, int]:
    operations: set[str] = set()
    assets: set[str] = set()
    fallback_count = 0
    unexplained_fallbacks = 0
    for clip in render_execution.get("clips") or []:
        if clip.get("subtitles"):
            operations.add("subtitles")
        assets.update(str(value) for value in clip.get("asset_ids") or [])
        for segment in clip.get("segments") or []:
            operation = str(segment.get("operation") or "")
            if operation:
                operations.add(operation)
                if operation == "focus_zoom":
                    operations.add("crop")
            strategy = str(segment.get("strategy") or "")
            if operation == "source_cutaway":
                pass
            elif strategy in {"crop", "fit", "letterbox"}:
                operations.add(strategy)
            elif strategy == "source":
                operations.add("source_cutaway")
            transition = str(segment.get("transition_kind") or "cut")
            operations.add({"cut": "hard_cut", "fade": "fade", "xfade": "xfade"}.get(transition, transition))
            for overlay in segment.get("overlays") or []:
                kind = str(overlay.get("kind") or "")
                mapped = {
                    "image": "image_overlay",
                    "text": "text_emphasis",
                    "source": "source_cutaway",
                    "pip": "pip",
                }.get(kind)
                if mapped:
                    operations.add(mapped)
            if segment.get("fallback_used"):
                fallback_count += 1
                if not _clean_text(segment.get("reason"), limit=500):
                    unexplained_fallbacks += 1
    return operations, assets, fallback_count, unexplained_fallbacks


def build_creative_conformance_report(
    *,
    edit_plan: dict[str, Any],
    render_execution: dict[str, Any],
    strict: bool = True,
    semantic_review: dict[str, Any] | None = None,
    asset_visibility: dict[str, Any] | None = None,
) -> dict[str, Any]:
    planned_operations, conditional_operations, requested_assets = _planned_operations(edit_plan)
    executed_operations, used_assets, fallback_count, unexplained_fallbacks = _executed_operations(
        render_execution
    )
    missing_operations = sorted(
        planned_operations - conditional_operations - executed_operations
    )
    extra_operations = sorted(executed_operations - planned_operations)
    missing_assets = sorted(requested_assets - used_assets)
    unrequested_assets = sorted(used_assets - requested_assets)
    findings: list[dict[str, Any]] = []
    if missing_operations:
        findings.append(_finding(
            "planned_operations_missing",
            "blocker" if strict else "warning",
            "One or more planned operations are absent from render execution evidence.",
            operations=missing_operations,
        ))
    if extra_operations:
        findings.append(_finding(
            "unplanned_operations_executed",
            "review",
            "Render execution contains operations not declared by the validated plan.",
            operations=extra_operations,
        ))
    if missing_assets:
        findings.append(_finding(
            "requested_assets_missing",
            "blocker" if strict else "warning",
            "One or more requested assets were not used in the intended render.",
            asset_ids=missing_assets,
        ))
    if unrequested_assets:
        findings.append(_finding(
            "unrequested_assets_used",
            "blocker",
            "Render execution references assets absent from the validated plan.",
            asset_ids=unrequested_assets,
        ))
    if unexplained_fallbacks:
        findings.append(_finding(
            "unexplained_fallback",
            "blocker" if strict else "warning",
            "A renderer fallback lacks an explanatory execution reason.",
            count=unexplained_fallbacks,
        ))
    elif fallback_count:
        findings.append(_finding(
            "fallbacks_used",
            "warning",
            "The renderer used explicit composition fallbacks that need creative review.",
            count=fallback_count,
        ))
    semantic = semantic_review or {
        "version": SEMANTIC_QA_VERSION,
        "status": "disabled",
        "non_mutating": True,
        "provider_calls": 0,
        "attempts": [],
        "observations": [],
    }
    visibility = asset_visibility or {
        "version": ASSET_VISIBILITY_VERSION,
        "status": "disabled",
        "strict_thresholds": strict,
        "summary": {
            "overlays_analyzed": 0,
            "visible": 0,
            "blockers": 0,
            "warnings": 0,
            "review_notes": 0,
        },
        "observations": [],
        "findings": [],
    }
    findings.extend(list(visibility.get("findings") or [])[:_MAX_FINDINGS])
    if semantic.get("status") == "unavailable":
        findings.append(_finding(
            "semantic_review_unavailable",
            "review",
            "Optional semantic visibility review was unavailable; deterministic QA remains valid.",
        ))
    elif semantic.get("status") == "review":
        findings.append(_finding(
            "semantic_review_requested",
            "review",
            "Optional semantic visibility review identified frames for operator review.",
        ))
    return {
        "version": CREATIVE_CONFORMANCE_VERSION,
        "status": _status(findings),
        "strict_thresholds": strict,
        "notice": QA_NOTICE,
        "summary": {
            "planned_operations": len(planned_operations),
            "executed_operations": len(executed_operations),
            "requested_assets": len(requested_assets),
            "used_assets": len(used_assets),
            "fallbacks": fallback_count,
            "center_fallbacks": sum(
                1
                for clip in render_execution.get("clips") or []
                for segment in clip.get("segments") or []
                if segment.get("fallback_used") and segment.get("strategy") == "crop"
            ),
            **_summary(findings),
        },
        "operations": {
            "planned": sorted(planned_operations),
            "conditional": sorted(conditional_operations),
            "executed": sorted(executed_operations),
            "missing": missing_operations,
            "extra": extra_operations,
        },
        "assets": {
            "requested": sorted(requested_assets),
            "used": sorted(used_assets),
            "missing": missing_assets,
            "unrequested": unrequested_assets,
        },
        "asset_visibility": visibility,
        "semantic_review": semantic,
        "findings": findings[:_MAX_FINDINGS],
    }


def _semantic_prompt(frame_records: Sequence[dict[str, Any]]) -> str:
    return json.dumps({
        "task": (
            "Review visibility and relevance only. Do not propose executable edits, "
            "authorize actions, or change the plan."
        ),
        "frames_in_image_order": frame_records,
        "required_output": {
            "status": "pass|review",
            "summary": "short sanitized summary",
            "observations": [{
                "clip_index": 1,
                "frame_id": "clip-001-frame-001",
                "planned_focus_visible": True,
                "relevant": True,
                "confidence": 0.0,
                "note": "short evidence note",
            }],
        },
    }, ensure_ascii=True, separators=(",", ":"))


async def build_semantic_review(
    inputs: Sequence[QAInput],
    *,
    client: Any,
    max_frames: int,
    max_width: int = 384,
    max_height: int = 384,
    max_frame_bytes: int = 750_000,
) -> dict[str, Any]:
    selected = list(inputs[:max_frames])
    frame_records: list[dict[str, Any]] = []
    data_urls: list[str] = []
    try:
        for item in selected:
            media = await asyncio.to_thread(_probe, item.video_path, timeout=60.0)
            scene_report = build_scene_boundaries(
                [],
                source_duration_ms=int(media["duration_ms"]),
                threshold=0.35,
                min_scene_duration_ms=0,
                max_scenes=1,
            )
            manifest = await asyncio.to_thread(
                sample_frames,
                item.video_path,
                scene_report=scene_report,
                source_width=int(media["width"]),
                source_height=int(media["height"]),
                max_frames=1,
                max_width=max_width,
                max_height=max_height,
                max_frame_bytes=max_frame_bytes,
                timeout_per_frame=60.0,
            )
            sampled = manifest.frames[0]
            frame_id = f"clip-{item.clip_index:03d}-{sampled.id}"
            frame_records.append({
                "clip_index": item.clip_index,
                "frame_id": frame_id,
                "timestamp_ms": sampled.timestamp_ms,
                "purpose": "verify planned focus visibility and frame relevance",
            })
            data_urls.append(sampled.data_url)
        if not data_urls:
            raise CreativeQAError("SEMANTIC_QA_FRAMES_MISSING", "no review frames were available")
        raw = await client.complete_structured(
            schema_name=SEMANTIC_QA_SCHEMA,
            system_prompt=(
                "You are a bounded, non-mutating video QA reviewer. Evaluate only the "
                "ordered rendered frames against the supplied visibility task. Return one JSON object."
            ),
            user_prompt=_semantic_prompt(frame_records),
            image_data_urls=tuple(data_urls),
        )
        validated = _SemanticResponse.model_validate(raw)
        allowed = {(item["clip_index"], item["frame_id"]) for item in frame_records}
        if any((item.clip_index, item.frame_id) not in allowed for item in validated.observations):
            raise CreativeQAError("SEMANTIC_QA_RESPONSE_INVALID", "semantic review referenced an unknown frame")
        attempts = [
            {
                "number": int(getattr(item, "number", 0)),
                "status_code": getattr(item, "status_code", None),
                "reason": _clean_text(getattr(item, "reason", ""), limit=160),
            }
            for item in getattr(client, "last_attempts", ())
        ][:6]
        return {
            "version": SEMANTIC_QA_VERSION,
            "status": validated.status,
            "non_mutating": True,
            "provider_calls": 1,
            "frame_count": len(frame_records),
            "frames": frame_records,
            "summary": validated.summary,
            "observations": [item.model_dump(mode="json") for item in validated.observations],
            "attempts": attempts,
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        attempts = [
            {
                "number": int(getattr(item, "number", 0)),
                "status_code": getattr(item, "status_code", None),
                "reason": _clean_text(getattr(item, "reason", ""), limit=160),
            }
            for item in getattr(exc, "attempts", getattr(client, "last_attempts", ()))
        ][:6]
        return {
            "version": SEMANTIC_QA_VERSION,
            "status": "unavailable",
            "non_mutating": True,
            "provider_calls": 1 if data_urls else 0,
            "frame_count": len(frame_records),
            "frames": frame_records,
            "error_code": _clean_text(getattr(exc, "code", "SEMANTIC_QA_UNAVAILABLE"), limit=80),
            "observations": [],
            "attempts": attempts,
        }


def _unavailable_reports(code: str, *, strict: bool) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    finding = _finding(code, "review", "Post-render creative QA was unavailable; operator review is required.")
    render = {
        "version": RENDER_QA_VERSION,
        "status": "unavailable",
        "strict_thresholds": strict,
        "summary": {"clips_analyzed": 0, "blockers": 0, "warnings": 0, "review_notes": 1},
        "clips": [],
        "findings": [finding],
    }
    rhythm = {
        "version": RETENTION_RHYTHM_QA_VERSION,
        "status": "unavailable",
        "strict_thresholds": strict,
        "notice": QA_NOTICE,
        "summary": {"clips_analyzed": 0, "blockers": 0, "warnings": 0, "review_notes": 1},
        "clips": [],
        "findings": [finding],
    }
    conformance = {
        "version": CREATIVE_CONFORMANCE_VERSION,
        "status": "unavailable",
        "strict_thresholds": strict,
        "notice": QA_NOTICE,
        "summary": {"blockers": 0, "warnings": 0, "review_notes": 1},
        "operations": {
            "planned": [],
            "conditional": [],
            "executed": [],
            "missing": [],
            "extra": [],
        },
        "assets": {"requested": [], "used": [], "missing": [], "unrequested": []},
        "asset_visibility": {
            "version": ASSET_VISIBILITY_VERSION,
            "status": "unavailable",
            "strict_thresholds": strict,
            "summary": {
                "overlays_analyzed": 0,
                "visible": 0,
                "blockers": 0,
                "warnings": 0,
                "review_notes": 1,
            },
            "observations": [],
            "findings": [finding],
        },
        "semantic_review": {
            "version": SEMANTIC_QA_VERSION,
            "status": "unavailable",
            "non_mutating": True,
            "provider_calls": 0,
            "observations": [],
            "attempts": [],
        },
        "findings": [finding],
    }
    return render, rhythm, conformance


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


async def generate_creative_qa_artifacts(
    *,
    output_dir: str | Path,
    inputs: Sequence[QAInput],
    edit_plan: dict[str, Any],
    render_execution: dict[str, Any],
    resolved_assets: dict[str, Path] | None = None,
    expected_width: int,
    expected_height: int,
    strict: bool,
    semantic_enabled: bool,
    semantic_max_frames: int,
    semantic_client: Any,
) -> CreativeQAArtifacts:
    root = Path(output_dir).resolve()
    render_path = root / "render_qa.json"
    rhythm_path = root / "retention_rhythm_qa.json"
    conformance_path = root / "creative_conformance.json"
    try:
        render_qa, rhythm_qa, asset_visibility = await asyncio.gather(
            asyncio.to_thread(
                build_render_qa_report,
                inputs,
                expected_width=expected_width,
                expected_height=expected_height,
                strict=strict,
            ),
            asyncio.to_thread(
                build_retention_rhythm_report,
                inputs,
                render_execution=render_execution,
                strict=strict,
            ),
            asyncio.to_thread(
                build_asset_visibility_report,
                inputs,
                render_execution=render_execution,
                resolved_assets=resolved_assets or {},
                expected_width=expected_width,
                expected_height=expected_height,
                strict=strict,
            ),
        )
        semantic = (
            await build_semantic_review(
                inputs,
                client=semantic_client,
                max_frames=semantic_max_frames,
            )
            if semantic_enabled and semantic_client is not None
            else {
                "version": SEMANTIC_QA_VERSION,
                "status": "disabled" if not semantic_enabled else "unavailable",
                "non_mutating": True,
                "provider_calls": 0,
                "observations": [],
                "attempts": [],
            }
        )
        conformance = build_creative_conformance_report(
            edit_plan=edit_plan,
            render_execution=render_execution,
            strict=strict,
            semantic_review=semantic,
            asset_visibility=asset_visibility,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = _clean_text(getattr(exc, "code", "CREATIVE_QA_UNAVAILABLE"), limit=80)
        render_qa, rhythm_qa, conformance = _unavailable_reports(code, strict=strict)

    await asyncio.to_thread(_write_json, render_path, render_qa)
    await asyncio.to_thread(_write_json, rhythm_path, rhythm_qa)
    await asyncio.to_thread(_write_json, conformance_path, conformance)
    return CreativeQAArtifacts(
        render_qa_path=render_path,
        rhythm_qa_path=rhythm_path,
        conformance_path=conformance_path,
        render_qa=render_qa,
        rhythm_qa=rhythm_qa,
        conformance=conformance,
    )
