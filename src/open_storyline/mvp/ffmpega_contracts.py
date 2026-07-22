from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from operator import or_
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model


FFMPEGA_SOURCE_REPOSITORY = "AEmotionStudio/ComfyUI-FFMPEGA"
FFMPEGA_SOURCE_COMMIT = "0cfe2db05df104f95c98cc45e11f129fa5ef5193"


@dataclass(frozen=True)
class EffectParameter:
    kind: str
    default: Any
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[str, ...] = ()


# Pinned from the upstream SkillRegistry at FFMPEGA_SOURCE_COMMIT.
EFFECT_PARAMETER_INVENTORY: dict[str, dict[str, EffectParameter]] = {
    "black_and_white": {
        "style": EffectParameter(
            "choice",
            "standard",
            choices=("standard", "high_contrast", "soft", "sepia"),
        ),
    },
    "blur": {"radius": EffectParameter("int", 5, 1, 50)},
    "brightness": {"value": EffectParameter("float", 0.1, -1.0, 1.0)},
    "chromatic_aberration": {"amount": EffectParameter("int", 4, 1, 20)},
    "color_grade": {
        "style": EffectParameter(
            "choice",
            None,
            choices=("teal_orange", "warm", "cool", "desaturated", "high_contrast"),
        ),
    },
    "contrast": {"value": EffectParameter("float", 1.2, 0.0, 3.0)},
    "deband": {
        "threshold": EffectParameter("float", 0.08, 0.003, 0.5),
        "range": EffectParameter("int", 16, 8, 64),
        "blur": EffectParameter("bool", True),
    },
    "denoise": {
        "strength": EffectParameter(
            "choice",
            "medium",
            choices=("light", "medium", "strong"),
        ),
    },
    "deshake": {
        "rx": EffectParameter("int", 16, 1, 64),
        "ry": EffectParameter("int", 16, 1, 64),
        "edge": EffectParameter(
            "choice",
            "mirror",
            choices=("blank", "original", "clamp", "mirror"),
        ),
    },
    "fade": {
        "type": EffectParameter("choice", "in", choices=("in", "out", "both")),
        "start": EffectParameter("time", 0),
        "duration": EffectParameter("time", 1),
    },
    "film_grain": {
        "intensity": EffectParameter(
            "choice",
            "medium",
            choices=("light", "medium", "heavy"),
        ),
    },
    "gamma": {"value": EffectParameter("float", 1.2, 0.1, 4.0)},
    "glow": {
        "radius": EffectParameter("float", 30, 5, 60),
        "strength": EffectParameter("float", 0.4, 0.1, 0.8),
    },
    "hue": {"value": EffectParameter("float", 15, -180, 180)},
    "letterbox": {
        "ratio": EffectParameter("string", "2.35:1"),
        "color": EffectParameter("string", "black"),
    },
    "mirror": {
        "mode": EffectParameter(
            "choice",
            "horizontal",
            choices=("horizontal", "vertical", "quad"),
        ),
    },
    "noise_reduction": {
        "floor": EffectParameter("float", -30, -80, -10),
        "amount": EffectParameter("float", 12, 1, 50),
    },
    "normalize": {},
    "pixelate": {"factor": EffectParameter("int", 10, 2, 50)},
    "quality": {
        "crf": EffectParameter("int", 23, 0, 51),
        "preset": EffectParameter(
            "choice",
            "medium",
            choices=(
                "ultrafast",
                "superfast",
                "veryfast",
                "faster",
                "fast",
                "medium",
                "slow",
                "slower",
                "veryslow",
            ),
        ),
    },
    "rotate": {"angle": EffectParameter("int", 90)},
    "saturation": {"value": EffectParameter("float", 1.3, 0.0, 3.0)},
    "sharpen": {"amount": EffectParameter("float", 1.0, 0.1, 3.0)},
    "vignette": {"intensity": EffectParameter("float", 0.3, 0.0, 1.0)},
    "vintage": {
        "era": EffectParameter(
            "choice",
            "70s",
            choices=("50s", "60s", "70s", "80s", "90s"),
        ),
    },
    "volume": {"level": EffectParameter("float", 1.5, 0.0, 10.0)},
}

DETERMINISTIC_SKILLS = frozenset(EFFECT_PARAMETER_INVENTORY)
AGENTIC_FINISHING_SKILLS = DETERMINISTIC_SKILLS - {
    "deshake",
    "fade",
    "letterbox",
    "mirror",
    "rotate",
}


class FFMPEGAWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _parameter_annotation(parameter: EffectParameter) -> Any:
    if parameter.kind == "choice":
        value_type = Literal.__getitem__(parameter.choices)
    elif parameter.kind == "int":
        value_type = int
    elif parameter.kind in {"float", "time"}:
        value_type = float
    elif parameter.kind == "bool":
        value_type = bool
    else:
        value_type = str
    if parameter.default is not None:
        value_type = value_type | None
    constraints: dict[str, Any] = {}
    if parameter.minimum is not None:
        constraints["ge"] = parameter.minimum
    if parameter.maximum is not None:
        constraints["le"] = parameter.maximum
    return Annotated[value_type, Field(**constraints)] if constraints else value_type


def _model_name(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))


EFFECT_PARAM_MODELS: dict[str, type[BaseModel]] = {}
EFFECT_STEP_MODELS: dict[str, type[BaseModel]] = {}
for _skill, _parameters in EFFECT_PARAMETER_INVENTORY.items():
    _params_model = create_model(
        f"{_model_name(_skill)}Params",
        __base__=FFMPEGAWireModel,
        **{
            name: (_parameter_annotation(parameter), ...)
            for name, parameter in _parameters.items()
        },
    )
    _step_model = create_model(
        f"{_model_name(_skill)}Effect",
        __base__=FFMPEGAWireModel,
        skill=(Literal[_skill], ...),
        params=(_params_model, ...),
    )
    EFFECT_PARAM_MODELS[_skill] = _params_model
    EFFECT_STEP_MODELS[_skill] = _step_model


def _effects_model(name: str, allowed_skills: frozenset[str]) -> type[BaseModel]:
    variants = tuple(EFFECT_STEP_MODELS[skill] for skill in sorted(allowed_skills))
    effect_union = reduce(or_, variants)
    return create_model(
        name,
        __base__=FFMPEGAWireModel,
        effects=(list[effect_union], Field(max_length=5)),
    )


FFMPEGAAgenticFinishingResponse = _effects_model(
    "FFMPEGAAgenticFinishingResponse",
    AGENTIC_FINISHING_SKILLS,
)
FFMPEGADeterministicEffectsResponse = _effects_model(
    "FFMPEGADeterministicEffectsResponse",
    DETERMINISTIC_SKILLS,
)


def validate_typed_effects(
    value: Any,
    *,
    allowed_skills: frozenset[str] = DETERMINISTIC_SKILLS,
) -> list[dict[str, Any]]:
    model = (
        FFMPEGAAgenticFinishingResponse
        if allowed_skills == AGENTIC_FINISHING_SKILLS
        else FFMPEGADeterministicEffectsResponse
    )
    validated = model.model_validate(value)
    effects = validated.model_dump(mode="json")["effects"]
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for effect in effects:
        skill = effect["skill"]
        if skill not in allowed_skills:
            raise ValueError(f"effect is not allowed in this boundary: {skill}")
        if skill in seen:
            raise ValueError(f"duplicate effect is not allowed: {skill}")
        seen.add(skill)
        result.append({
            "skill": skill,
            "params": {
                key: item
                for key, item in effect["params"].items()
                if item is not None
            },
        })
    return result


__all__ = [
    "AGENTIC_FINISHING_SKILLS",
    "DETERMINISTIC_SKILLS",
    "EFFECT_PARAMETER_INVENTORY",
    "EFFECT_PARAM_MODELS",
    "FFMPEGAAgenticFinishingResponse",
    "FFMPEGADeterministicEffectsResponse",
    "FFMPEGA_SOURCE_COMMIT",
    "FFMPEGA_SOURCE_REPOSITORY",
    "validate_typed_effects",
]
