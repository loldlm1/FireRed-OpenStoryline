from __future__ import annotations

import os
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.11+ uses tomllib.
    import tomli as tomllib

from pydantic import Field

from open_storyline.config import (
    AgenticEditingConfig,
    ConfigBaseModel,
    FFMPEGAConfig,
    MVPConfig,
    NineRouterConfig,
    RemoteASRConfig,
    RemoteImageConfig,
)


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

