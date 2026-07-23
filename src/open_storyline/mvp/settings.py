from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.11+ uses tomllib.
    import tomli as tomllib

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


def _resolve_paths(value: Any, info: ValidationInfo) -> Any:
    config_dir = (info.context or {}).get("config_dir")
    if not config_dir:
        return value
    if isinstance(value, Path):
        expanded = value.expanduser()
        if expanded.is_absolute():
            return expanded
        return (Path(config_dir).expanduser() / expanded).resolve(strict=False)
    if isinstance(value, list):
        return [_resolve_paths(item, info) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve_paths(item, info) for item in value)
    if isinstance(value, set):
        return {_resolve_paths(item, info) for item in value}
    if isinstance(value, dict):
        return {key: _resolve_paths(item, info) for key, item in value.items()}
    return value


class ConfigBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def _resolve_path_fields(cls, value: Any, info: ValidationInfo) -> Any:
        return _resolve_paths(value, info)


class RemoteASRConfig(ConfigBaseModel):
    timeout: float = Field(default=180.0, gt=0)
    language: str = ""


class NineRouterConfig(ConfigBaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = "cx/gpt-5.6-sol"
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
    timeout: float = Field(default=180.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)


class RemoteImageConfig(ConfigBaseModel):
    base_url: str = ""
    api_key: str = ""
    models: list[str] = Field(default_factory=lambda: ["cx/gpt-5.5-image"])
    timeout: float = Field(default=180.0, gt=0)
    max_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    size: str = "1024x1024"


class MVPConfig(ConfigBaseModel):
    frame_count: int = Field(default=6, ge=0, le=24)
    render_width: int = Field(default=1080, ge=128, le=4320)
    render_height: int = Field(default=1920, ge=128, le=4320)
    render_quality_profile: Literal["legacy", "balanced", "high"] = "high"
    render_fps_cap: int = Field(default=60, ge=12, le=60)
    render_fps: int = Field(default=30, ge=12, le=60)
    render_preset: str = "veryfast"
    render_crf: int = Field(default=23, ge=0, le=51)


class AgenticEditingConfig(ConfigBaseModel):
    mode: Literal["off", "shadow", "render"] = "off"
    shadow_allow_blocked_plans: bool = True
    baseline_fallbacks_enabled: bool = False
    max_segments_per_clip: int = Field(default=24, ge=1, le=48)
    max_overlays_per_clip: int = Field(default=12, ge=0, le=16)
    max_assets_per_clip: int = Field(default=4, ge=0, le=8)
    generated_assets_enabled: bool = False
    max_generated_assets_per_clip: int = Field(default=2, ge=0, le=8)
    pexels_enabled: bool = False
    max_stock_assets_per_clip: int = Field(default=2, ge=0, le=8)
    pexels_license_reviewed_at: str = ""
    pexels_search_limit: int = Field(default=8, ge=1, le=15)
    pexels_timeout: float = Field(default=30.0, gt=0, le=120)
    pexels_max_retries: int = Field(default=2, ge=0, le=3)
    pexels_max_bytes: int = Field(default=80 * 1024 * 1024, ge=1_000_000)
    pexels_max_video_duration_seconds: int = Field(default=60, ge=1, le=300)
    creative_qa_enabled: bool = True
    creative_qa_strict: bool = True
    render_promotion_mode: Literal["off", "report", "enforce"] = "report"
    completion_policy: Literal["strict", "baseline_guaranteed"] = "strict"
    delivery_policy: Literal["qa_enforced", "technical_pass_guaranteed"] = "qa_enforced"
    semantic_qa_enabled: bool = False
    semantic_qa_max_frames: int = Field(default=4, ge=1, le=8)
    post_render_review_mode: Literal["off", "shadow", "report", "enforce"] = "off"
    render_evidence_max_frames_per_clip: int = Field(default=12, ge=3, le=32)
    render_evidence_max_frames_total: int = Field(default=64, ge=3, le=128)
    render_evidence_max_bursts_per_clip: int = Field(default=8, ge=0, le=16)
    render_evidence_max_frame_bytes: int = Field(default=1_500_000, ge=16_384, le=8_388_608)
    render_evidence_max_total_bytes: int = Field(default=12_582_912, ge=16_384, le=67_108_864)
    render_evidence_max_width: int = Field(default=512, ge=128, le=2048)
    render_evidence_max_height: int = Field(default=512, ge=128, le=2048)
    render_evidence_timeout_seconds: float = Field(default=120.0, gt=0, le=300)
    scene_threshold: float = Field(default=0.35, gt=0, lt=1)
    min_scene_duration_ms: int = Field(default=1000, ge=100, le=30_000)
    max_scenes: int = Field(default=64, ge=1, le=256)
    vision_frame_count: int = Field(default=12, ge=1, le=64)
    vision_clip_frame_count: int = Field(default=6, ge=5, le=16)
    vision_clip_repair_frame_count: int = Field(default=12, ge=5, le=32)
    vision_frame_max_width: int = Field(default=512, ge=128, le=2048)
    vision_frame_max_height: int = Field(default=512, ge=128, le=2048)
    vision_frame_max_bytes: int = Field(default=1_500_000, ge=16_384, le=8_388_608)
    crop_coverage_min_observations: int = Field(default=2, ge=1, le=16)
    crop_coverage_min_ratio: float = Field(default=0.5, ge=0, le=1)
    crop_coverage_max_gap_ms: int = Field(default=8_000, ge=250, le=60_000)
    crop_hysteresis_ratio: float = Field(default=0.03, ge=0, le=0.25)
    crop_smoothing_alpha: float = Field(default=0.65, ge=0, le=1)
    max_crop_velocity_ratio_per_second: float = Field(default=0.45, gt=0, le=2)


class FFMPEGAConfig(ConfigBaseModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8188"
    timeout: float = Field(default=1800.0, gt=0)
    poll_interval: float = Field(default=1.0, gt=0, le=30)
    quality_preset: Literal["draft", "standard", "high", "lossless"] = "high"
    shared_local_root: str = ""
    shared_remote_root: str = ""


class MVPProjectConfig(ConfigBaseModel):
    outputs_dir: Path = Field(default=Path("./outputs"))


class MVPSettings(ConfigBaseModel):
    project: MVPProjectConfig = Field(default_factory=MVPProjectConfig)
    remote_asr: RemoteASRConfig = Field(default_factory=RemoteASRConfig)
    ninerouter: NineRouterConfig = Field(default_factory=NineRouterConfig)
    remote_image: RemoteImageConfig = Field(default_factory=RemoteImageConfig)
    mvp: MVPConfig = Field(default_factory=MVPConfig)
    agentic_editing: AgenticEditingConfig = Field(default_factory=AgenticEditingConfig)
    ffmpega: FFMPEGAConfig = Field(default_factory=FFMPEGAConfig)


_MVP_SECTIONS = (
    "remote_asr",
    "ninerouter",
    "remote_image",
    "mvp",
    "agentic_editing",
    "ffmpega",
)


def load_mvp_settings(config_path: str | Path) -> MVPSettings:
    path = Path(config_path).expanduser().resolve()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    project = raw.get("project") if isinstance(raw.get("project"), dict) else {}
    data = {
        "project": {"outputs_dir": project.get("outputs_dir", "./outputs")},
        **{
            section: raw[section]
            for section in _MVP_SECTIONS
            if isinstance(raw.get(section), dict)
        },
    }
    return MVPSettings.model_validate(data, context={"config_dir": path.parent})


def default_mvp_config_path() -> str:
    return os.getenv("OPENSTORYLINE_CONFIG", "config.toml")
