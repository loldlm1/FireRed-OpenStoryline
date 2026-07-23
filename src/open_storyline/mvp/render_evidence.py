from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import json
import os
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from open_storyline.mvp.frame_sampling import FrameRequest, SampledFrame, sample_frame_requests


RENDER_EVIDENCE_VERSION = "render_evidence.v1"
RENDER_EVIDENCE_SAMPLER_VERSION = "adaptive_render_sampler.v1"
EVIDENCE_ID_PATTERN = re.compile(r"^ev-[a-f0-9]{24}$")
SAFE_ARTIFACT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
REASONS = frozenset(
    {
        "opening_anchor",
        "ending_anchor",
        "midpoint_anchor",
        "scene_boundary",
        "caption_event",
        "overlay_boundary",
        "effect_boundary",
        "transition_boundary",
        "crop_focus_change",
        "defect_window",
        "uncertainty_window",
    }
)
HIGH_RISK_REASONS = frozenset(
    {
        "caption_event",
        "effect_boundary",
        "transition_boundary",
        "crop_focus_change",
        "defect_window",
        "uncertainty_window",
    }
)


class RenderEvidenceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class EvidenceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    max_frames_per_clip: int = Field(12, ge=3, le=32)
    max_frames_total: int = Field(64, ge=3, le=128)
    max_bursts_per_clip: int = Field(8, ge=0, le=16)
    max_frame_bytes: int = Field(1_500_000, ge=16_384, le=8 * 1024 * 1024)
    max_total_bytes: int = Field(12 * 1024 * 1024, ge=16_384, le=64 * 1024 * 1024)
    max_width: int = Field(512, ge=128, le=2048)
    max_height: int = Field(512, ge=128, le=2048)
    timeout_per_frame: float = Field(120.0, gt=0, le=300)


class EvidenceFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN.pattern)
    clip_index: int = Field(ge=1, le=50)
    timestamp_ms: int = Field(ge=0)
    purpose: tuple[str, ...] = Field(min_length=1, max_length=8)
    source_artifact: str = Field(min_length=1, max_length=120)
    width: int = Field(ge=2, le=4096)
    height: int = Field(ge=2, le=4096)
    encoded_bytes: int = Field(ge=1, le=8 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_fields(self) -> "EvidenceFrame":
        if not SAFE_ARTIFACT_PATTERN.fullmatch(self.source_artifact):
            raise ValueError("evidence source artifact is invalid")
        if any(reason not in REASONS for reason in self.purpose):
            raise ValueError("evidence purpose is not allowlisted")
        if len(set(self.purpose)) != len(self.purpose):
            raise ValueError("evidence purposes must be unique")
        return self


class EvidenceBurst(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    burst_id: str = Field(pattern=r"^burst-[a-f0-9]{16}$")
    clip_index: int = Field(ge=1, le=50)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=40)
    frame_ids: tuple[str, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_window(self) -> "EvidenceBurst":
        if self.end_ms <= self.start_ms:
            raise ValueError("evidence burst must have positive duration")
        if self.reason not in REASONS:
            raise ValueError("evidence burst reason is not allowlisted")
        if any(not EVIDENCE_ID_PATTERN.fullmatch(item) for item in self.frame_ids):
            raise ValueError("evidence burst references an invalid frame")
        if len(set(self.frame_ids)) != len(self.frame_ids):
            raise ValueError("evidence burst frame IDs must be unique")
        return self


class EvidenceClip(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    clip_index: int = Field(ge=1, le=50)
    source_artifact: str = Field(min_length=1, max_length=120)
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    duration_ms: int = Field(gt=0, le=86_400_000)
    frames: tuple[EvidenceFrame, ...] = Field(min_length=1, max_length=32)
    bursts: tuple[EvidenceBurst, ...] = Field(default=(), max_length=16)
    selected_reasons: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_clip(self) -> "EvidenceClip":
        if not SAFE_ARTIFACT_PATTERN.fullmatch(self.source_artifact):
            raise ValueError("evidence clip artifact is invalid")
        if len({frame.evidence_id for frame in self.frames}) != len(self.frames):
            raise ValueError("evidence frame IDs must be unique")
        frame_ids = {frame.evidence_id for frame in self.frames}
        for frame in self.frames:
            if frame.clip_index != self.clip_index:
                raise ValueError("evidence frame clip does not match its manifest")
            if frame.source_artifact != self.source_artifact:
                raise ValueError("evidence frame artifact does not match its clip")
            if frame.timestamp_ms >= self.duration_ms:
                raise ValueError("evidence timestamp is outside the clip")
        if len({burst.burst_id for burst in self.bursts}) != len(self.bursts):
            raise ValueError("evidence burst IDs must be unique")
        for burst in self.bursts:
            if burst.clip_index != self.clip_index:
                raise ValueError("evidence burst clip does not match its manifest")
            if burst.end_ms > self.duration_ms:
                raise ValueError("evidence burst is outside the clip")
            if not set(burst.frame_ids) <= frame_ids:
                raise ValueError("evidence burst references an unknown frame")
        if len(set(self.selected_reasons)) != len(self.selected_reasons):
            raise ValueError("selected evidence reasons must be unique")
        return self


class RenderEvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: str = Field(default=RENDER_EVIDENCE_VERSION, pattern=r"^render_evidence\.v1$")
    sampler_version: str = Field(default=RENDER_EVIDENCE_SAMPLER_VERSION, pattern=r"^[a-z0-9_.-]{1,64}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    render_execution_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    effects_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    call_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    limits: EvidenceLimits
    clips: tuple[EvidenceClip, ...] = Field(min_length=1, max_length=50)
    frame_count: int = Field(ge=1, le=128)
    burst_count: int = Field(ge=0, le=800)
    encoded_bytes: int = Field(ge=1, le=64 * 1024 * 1024)
    checkpoint_reused: bool = False
    warnings: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_manifest(self) -> "RenderEvidenceManifest":
        if len({clip.clip_index for clip in self.clips}) != len(self.clips):
            raise ValueError("evidence clip indexes must be unique")
        if self.frame_count != sum(len(clip.frames) for clip in self.clips):
            raise ValueError("evidence frame count is inconsistent")
        if self.burst_count != sum(len(clip.bursts) for clip in self.clips):
            raise ValueError("evidence burst count is inconsistent")
        if self.encoded_bytes != sum(frame.encoded_bytes for clip in self.clips for frame in clip.frames):
            raise ValueError("evidence byte count is inconsistent")
        if self.frame_count > self.limits.max_frames_total:
            raise ValueError("evidence frame count exceeds the job limit")
        if self.encoded_bytes > self.limits.max_total_bytes:
            raise ValueError("evidence bytes exceed the job limit")
        if len(set(self.warnings)) != len(self.warnings):
            raise ValueError("evidence warnings must be unique")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class EvidenceEvent:
    timestamp_ms: int
    reason: str
    priority: int = 50
    window_ms: int = 0

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0 or self.reason not in REASONS:
            raise RenderEvidenceError("EVIDENCE_EVENT_INVALID", "evidence event is invalid")
        if self.priority < 0 or self.priority > 100:
            raise RenderEvidenceError("EVIDENCE_EVENT_INVALID", "evidence event priority is invalid")
        if self.window_ms < 0 or self.window_ms > 5_000:
            raise RenderEvidenceError("EVIDENCE_EVENT_INVALID", "evidence event window is invalid")


@dataclass(frozen=True)
class RenderedCandidate:
    clip_index: int
    video_path: Path
    duration_ms: int
    source_artifact: str
    source_width: int
    source_height: int
    events: tuple[EvidenceEvent, ...] = ()


@dataclass(frozen=True)
class RenderEvidenceBundle:
    manifest: RenderEvidenceManifest
    image_data_urls: dict[str, str]


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return sha256(encoded).hexdigest()


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_fingerprint(
    candidates: Sequence[RenderedCandidate],
    *,
    source_sha256: str,
    render_execution: Mapping[str, Any],
    plan: Mapping[str, Any],
    effects: Mapping[str, Any],
    limits: EvidenceLimits,
) -> str:
    candidate_inputs = []
    for candidate in sorted(candidates, key=lambda item: item.clip_index):
        if not candidate.video_path.is_file():
            raise RenderEvidenceError("EVIDENCE_CANDIDATE_MISSING", "rendered candidate is unavailable")
        candidate_inputs.append({
            "clip_index": candidate.clip_index,
            "artifact": candidate.source_artifact,
            "output_sha256": _hash_file(candidate.video_path),
            "duration_ms": candidate.duration_ms,
            "events": [
                {
                    "timestamp_ms": event.timestamp_ms,
                    "reason": event.reason,
                    "priority": event.priority,
                    "window_ms": event.window_ms,
                }
                for event in candidate.events
            ],
        })
    return _hash_json({
        "source_sha256": source_sha256,
        "render_execution_sha256": _hash_json(render_execution),
        "plan_sha256": _hash_json(plan),
        "effects_sha256": _hash_json(effects),
        "candidates": candidate_inputs,
        "sampler_version": RENDER_EVIDENCE_SAMPLER_VERSION,
        "limits": limits.model_dump(mode="json"),
    })


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RenderEvidenceError("EVIDENCE_CONFIG_INVALID", f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RenderEvidenceError("EVIDENCE_CONFIG_INVALID", f"{name} is outside its bounds")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RenderEvidenceError("EVIDENCE_CONFIG_INVALID", f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise RenderEvidenceError("EVIDENCE_CONFIG_INVALID", f"{name} is outside its bounds")
    return value


def evidence_limits(config: Any | None = None) -> EvidenceLimits:
    source = config or object()
    return EvidenceLimits(
        max_frames_per_clip=_env_int(
            "OPENSTORYLINE_RENDER_EVIDENCE_MAX_FRAMES_PER_CLIP",
            int(getattr(source, "render_evidence_max_frames_per_clip", 12)),
            3,
            32,
        ),
        max_frames_total=_env_int(
            "OPENSTORYLINE_RENDER_EVIDENCE_MAX_FRAMES_TOTAL",
            int(getattr(source, "render_evidence_max_frames_total", 64)),
            3,
            128,
        ),
        max_bursts_per_clip=_env_int(
            "OPENSTORYLINE_RENDER_EVIDENCE_MAX_BURSTS_PER_CLIP",
            int(getattr(source, "render_evidence_max_bursts_per_clip", 8)),
            0,
            16,
        ),
        max_frame_bytes=_env_int(
            "OPENSTORYLINE_RENDER_EVIDENCE_MAX_FRAME_BYTES",
            int(getattr(source, "render_evidence_max_frame_bytes", 1_500_000)),
            16_384,
            8 * 1024 * 1024,
        ),
        max_total_bytes=_env_int(
            "OPENSTORYLINE_RENDER_EVIDENCE_MAX_TOTAL_BYTES",
            int(getattr(source, "render_evidence_max_total_bytes", 12 * 1024 * 1024)),
            16_384,
            64 * 1024 * 1024,
        ),
        max_width=_env_int("OPENSTORYLINE_RENDER_EVIDENCE_MAX_WIDTH", int(getattr(source, "render_evidence_max_width", 512)), 128, 2048),
        max_height=_env_int("OPENSTORYLINE_RENDER_EVIDENCE_MAX_HEIGHT", int(getattr(source, "render_evidence_max_height", 512)), 128, 2048),
        timeout_per_frame=_env_float(
            "OPENSTORYLINE_RENDER_EVIDENCE_TIMEOUT_SECONDS",
            float(getattr(source, "render_evidence_timeout_seconds", 120.0)),
            1.0,
            300.0,
        ),
    )


def _request_candidates(
    candidate: RenderedCandidate,
    limits: EvidenceLimits,
) -> tuple[list[FrameRequest], list[tuple[str, int, int, str]]]:
    duration = int(candidate.duration_ms)
    if duration <= 0:
        raise RenderEvidenceError("EVIDENCE_DURATION_INVALID", "rendered clip duration is invalid")
    safe_end = max(0, duration - 1)
    raw: list[tuple[int, str, int]] = [
        (min(safe_end, min(250, max(0, duration // 10))), "opening_anchor", 100),
        (min(safe_end, max(0, duration // 2)), "midpoint_anchor", 90),
        (max(0, safe_end - min(250, max(1, duration // 10))), "ending_anchor", 100),
    ]
    bursts: dict[str, tuple[str, int, int, str]] = {}
    for event in sorted(candidate.events, key=lambda item: (-item.priority, item.timestamp_ms, item.reason)):
        timestamp = min(safe_end, max(0, int(event.timestamp_ms)))
        raw.append((timestamp, event.reason, event.priority))
        if event.reason in HIGH_RISK_REASONS:
            radius = max(100, event.window_ms or 150)
            start = max(0, timestamp - radius)
            end = min(duration, timestamp + radius)
            if end > start:
                burst_id = "burst-" + sha256(
                    f"{candidate.clip_index}|{start}|{end}|{event.reason}".encode("utf-8")
                ).hexdigest()[:16]
                bursts[burst_id] = (burst_id, start, end, event.reason)
                raw.extend(
                    (
                        (max(0, timestamp - min(100, radius)), event.reason, event.priority),
                        (min(safe_end, timestamp + min(100, radius)), event.reason, event.priority),
                    )
                )
    merged: dict[int, tuple[set[str], int]] = {}
    for timestamp, reason, priority in raw:
        reasons, previous_priority = merged.setdefault(timestamp, (set(), 0))
        reasons.add(reason)
        merged[timestamp] = (reasons, max(previous_priority, priority))
    ordered = sorted(
        ((timestamp, reasons, priority) for timestamp, (reasons, priority) in merged.items()),
        key=lambda item: (-item[2], item[0]),
    )
    selected = ordered[: limits.max_frames_per_clip]
    selected_timestamps = {item[0] for item in selected}
    requests = [
        FrameRequest(
            timestamp_ms=timestamp,
            scene_id=f"clip-{candidate.clip_index}",
            reason="+".join(sorted(reasons)),
        )
        for timestamp, reasons, _priority in sorted(selected, key=lambda item: item[0])
    ]
    bounded_bursts = [
        burst
        for burst in sorted(bursts.values(), key=lambda item: (item[1], item[2], item[3]))[: limits.max_bursts_per_clip]
        if any(start <= timestamp < end for timestamp in selected_timestamps for _id, start, end, _reason in (burst,))
    ]
    return requests, bounded_bursts


def derive_evidence_events(
    *,
    clip_plan: Mapping[str, Any] | None,
    render_clip: Mapping[str, Any] | None,
    quality_clip: Mapping[str, Any] | None,
    duration_ms: int,
    has_subtitles: bool = False,
    effect_count: int = 0,
) -> tuple[EvidenceEvent, ...]:
    """Translate typed render/QA metadata into bounded sampling reasons."""
    events: list[EvidenceEvent] = []
    render_segments = list((render_clip or {}).get("segments") or [])
    plan_segments = list((clip_plan or {}).get("segments") or [])
    segments = render_segments or plan_segments
    for index, segment in enumerate(segments):
        window = segment.get("timeline_window") or {}
        start = int(window.get("start_ms") or 0)
        if index:
            transition = str(segment.get("transition_kind") or (segment.get("transition_in") or {}).get("kind") or "cut")
            events.append(EvidenceEvent(
                start,
                "transition_boundary" if transition != "cut" else "scene_boundary",
                82,
                240 if transition != "cut" else 0,
            ))
        strategy = str(segment.get("strategy") or (segment.get("layout") or {}).get("mode") or "")
        if strategy in {"crop", "focus_zoom"}:
            events.append(EvidenceEvent(start, "crop_focus_change", 78, 220))
        for overlay in segment.get("overlays") or []:
            overlay_window = overlay.get("timeline_window") or {}
            overlay_start = int(overlay_window.get("start_ms") or 0)
            kind = "caption_event" if overlay.get("kind") == "text" else "overlay_boundary"
            events.append(EvidenceEvent(overlay_start, kind, 75, 180))
            overlay_end = int(overlay_window.get("end_ms") or overlay_start)
            if overlay_end > overlay_start:
                events.append(EvidenceEvent(overlay_end - 1, kind, 72, 180))
    if has_subtitles:
        for fraction in (0.25, 0.5, 0.75):
            events.append(EvidenceEvent(int(duration_ms * fraction), "caption_event", 68, 150))
    if effect_count > 0:
        events.append(EvidenceEvent(max(0, duration_ms // 2), "effect_boundary", 70, 240))
    quality = quality_clip or {}
    findings = quality.get("findings") or []
    samples = ((quality.get("active_picture") or {}).get("samples") or [])
    if findings:
        candidate_samples = sorted(
            (item for item in samples if isinstance(item, Mapping)),
            key=lambda item: float(item.get("active_area_ratio") or 1),
        )
        timestamp = int((candidate_samples[0] if candidate_samples else {}).get("timestamp_ms") or duration_ms // 2)
        events.append(EvidenceEvent(timestamp, "defect_window", 100, 400))
    if str(quality.get("status") or "pass") not in {"pass", ""} and not findings:
        events.append(EvidenceEvent(max(0, duration_ms // 2), "uncertainty_window", 55, 300))
    return tuple(events)


def build_render_evidence(
    candidates: Sequence[RenderedCandidate],
    *,
    source_sha256: str,
    render_execution: Mapping[str, Any],
    plan: Mapping[str, Any],
    effects: Mapping[str, Any],
    limits: EvidenceLimits | None = None,
    checkpoint_reused: bool = False,
) -> RenderEvidenceBundle:
    limits = limits or EvidenceLimits()
    if not candidates:
        raise RenderEvidenceError("EVIDENCE_CANDIDATE_MISSING", "at least one rendered candidate is required")
    if not re.fullmatch(r"^[a-f0-9]{64}$", str(source_sha256 or "")):
        raise RenderEvidenceError("EVIDENCE_SOURCE_HASH_INVALID", "source hash is invalid")
    render_execution_sha256 = _hash_json(render_execution)
    plan_sha256 = _hash_json(plan)
    effects_sha256 = _hash_json(effects)
    candidate_inputs = []
    for candidate in sorted(candidates, key=lambda item: item.clip_index):
        if not candidate.video_path.is_file():
            raise RenderEvidenceError("EVIDENCE_CANDIDATE_MISSING", "rendered candidate is unavailable")
        if not 1 <= candidate.clip_index <= 50 or candidate.duration_ms <= 0:
            raise RenderEvidenceError("EVIDENCE_CANDIDATE_INVALID", "rendered candidate metadata is invalid")
        if not SAFE_ARTIFACT_PATTERN.fullmatch(candidate.source_artifact):
            raise RenderEvidenceError("EVIDENCE_ARTIFACT_INVALID", "rendered artifact name is invalid")
        candidate_inputs.append({
            "clip_index": candidate.clip_index,
            "artifact": candidate.source_artifact,
            "output_sha256": _hash_file(candidate.video_path),
            "duration_ms": candidate.duration_ms,
        })
    candidate_fingerprint = evidence_fingerprint(
        candidates,
        source_sha256=source_sha256,
        render_execution=render_execution,
        plan=plan,
        effects=effects,
        limits=limits,
    )
    image_data_urls: dict[str, str] = {}
    clips: list[EvidenceClip] = []
    total_bytes = 0
    total_requested_frames = 0
    for candidate, candidate_input in zip(sorted(candidates, key=lambda item: item.clip_index), candidate_inputs):
        requests, raw_bursts = _request_candidates(candidate, limits)
        total_requested_frames += len(requests)
        if total_requested_frames > limits.max_frames_total:
            raise RenderEvidenceError("EVIDENCE_FRAME_LIMIT_EXCEEDED", "evidence job frame limit exceeded")
        sampled = sample_frame_requests(
            candidate.video_path,
            requests,
            source_width=candidate.source_width,
            source_height=candidate.source_height,
            max_width=limits.max_width,
            max_height=limits.max_height,
            max_frame_bytes=limits.max_frame_bytes,
            timeout_per_frame=limits.timeout_per_frame,
            id_prefix=f"clip{candidate.clip_index}-",
        )
        frames: list[EvidenceFrame] = []
        timestamp_to_id: dict[int, str] = {}
        for sampled_frame in sampled:
            purpose = tuple(sorted(set(sampled_frame.extraction_reason.split("+"))))
            evidence_id = "ev-" + sha256(
                f"{candidate_fingerprint}|{candidate.clip_index}|{sampled_frame.timestamp_ms}|{','.join(purpose)}".encode("utf-8")
            ).hexdigest()[:24]
            raw_frame = b64decode(sampled_frame.data_url.split(",", 1)[1])
            frame = EvidenceFrame(
                evidence_id=evidence_id,
                clip_index=candidate.clip_index,
                timestamp_ms=sampled_frame.timestamp_ms,
                purpose=purpose,
                source_artifact=candidate.source_artifact,
                width=sampled_frame.width,
                height=sampled_frame.height,
                encoded_bytes=sampled_frame.encoded_bytes,
                sha256=sha256(raw_frame).hexdigest(),
            )
            frames.append(frame)
            image_data_urls[evidence_id] = sampled_frame.data_url
            timestamp_to_id[sampled_frame.timestamp_ms] = evidence_id
            total_bytes += frame.encoded_bytes
        if total_bytes > limits.max_total_bytes:
            raise RenderEvidenceError("EVIDENCE_BYTES_LIMIT_EXCEEDED", "evidence job byte limit exceeded")
        bursts: list[EvidenceBurst] = []
        for burst_id, start_ms, end_ms, reason in raw_bursts:
            frame_ids = tuple(
                timestamp_to_id[timestamp]
                for timestamp in sorted(timestamp_to_id)
                if start_ms <= timestamp < end_ms
            )
            if frame_ids:
                bursts.append(EvidenceBurst(
                    burst_id=burst_id,
                    clip_index=candidate.clip_index,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    reason=reason,
                    frame_ids=frame_ids[:8],
                ))
        clips.append(EvidenceClip(
            clip_index=candidate.clip_index,
            source_artifact=candidate.source_artifact,
            output_sha256=str(candidate_input["output_sha256"]),
            duration_ms=candidate.duration_ms,
            frames=tuple(frames),
            bursts=tuple(bursts),
            selected_reasons=tuple(sorted({reason for frame in frames for reason in frame.purpose})),
        ))
    manifest = RenderEvidenceManifest(
        source_sha256=source_sha256,
        render_execution_sha256=render_execution_sha256,
        plan_sha256=plan_sha256,
        effects_sha256=effects_sha256,
        candidate_fingerprint=candidate_fingerprint,
        call_fingerprint=candidate_fingerprint,
        limits=limits,
        clips=tuple(clips),
        frame_count=sum(len(clip.frames) for clip in clips),
        burst_count=sum(len(clip.bursts) for clip in clips),
        encoded_bytes=total_bytes,
        checkpoint_reused=checkpoint_reused,
    )
    return RenderEvidenceBundle(manifest=manifest, image_data_urls=image_data_urls)


def manifest_from_checkpoint(payload: Mapping[str, Any]) -> RenderEvidenceManifest:
    try:
        return RenderEvidenceManifest.model_validate(payload)
    except Exception as exc:
        raise RenderEvidenceError("EVIDENCE_CHECKPOINT_INVALID", "evidence checkpoint is invalid") from exc


__all__ = [
    "EvidenceBurst",
    "EvidenceClip",
    "EvidenceEvent",
    "EvidenceFrame",
    "EvidenceLimits",
    "RENDER_EVIDENCE_SAMPLER_VERSION",
    "RENDER_EVIDENCE_VERSION",
    "RenderEvidenceBundle",
    "RenderEvidenceError",
    "RenderEvidenceManifest",
    "RenderedCandidate",
    "build_render_evidence",
    "derive_evidence_events",
    "evidence_fingerprint",
    "evidence_limits",
    "manifest_from_checkpoint",
]
