from copy import deepcopy
import unittest

from pydantic import ValidationError

from open_storyline.mvp.ffmpega_contracts import (
    AGENTIC_FINISHING_SKILLS,
    DETERMINISTIC_SKILLS,
    EFFECT_PARAMETER_INVENTORY,
    FFMPEGAAgenticFinishingResponse,
    FFMPEGADeterministicEffectsResponse,
    FFMPEGA_SOURCE_COMMIT,
    validate_typed_effects,
)


def valid_effect(skill: str) -> dict:
    params = {}
    for name, parameter in EFFECT_PARAMETER_INVENTORY[skill].items():
        if parameter.default is not None:
            params[name] = None
        elif parameter.choices:
            params[name] = parameter.choices[0]
        elif parameter.kind == "int":
            params[name] = int(parameter.minimum or 1)
        elif parameter.kind in {"float", "time"}:
            params[name] = float(parameter.minimum or 1)
        elif parameter.kind == "bool":
            params[name] = True
        else:
            params[name] = "value"
    return {"skill": skill, "params": params}


class FFMPEGAContractTests(unittest.TestCase):
    def test_inventory_is_complete_and_pinned(self):
        self.assertEqual(len(DETERMINISTIC_SKILLS), 26)
        self.assertEqual(len(AGENTIC_FINISHING_SKILLS), 21)
        self.assertEqual(
            FFMPEGA_SOURCE_COMMIT,
            "0cfe2db05df104f95c98cc45e11f129fa5ef5193",
        )

    def test_every_effect_has_typed_success_and_rejection_cases(self):
        for skill, parameters in EFFECT_PARAMETER_INVENTORY.items():
            payload = {"effects": [valid_effect(skill)]}
            with self.subTest(skill=skill, case="valid"):
                self.assertEqual(
                    FFMPEGADeterministicEffectsResponse.model_validate(payload)
                    .model_dump(mode="json")["effects"][0]["skill"],
                    skill,
                )

            unknown = deepcopy(payload)
            unknown["effects"][0]["params"]["unknown"] = 1
            with self.subTest(skill=skill, case="unknown"), self.assertRaises(ValidationError):
                FFMPEGADeterministicEffectsResponse.model_validate(unknown)

            if parameters:
                name, parameter = next(iter(parameters.items()))
                missing = deepcopy(payload)
                missing["effects"][0]["params"].pop(name)
                with self.subTest(skill=skill, case="missing"), self.assertRaises(ValidationError):
                    FFMPEGADeterministicEffectsResponse.model_validate(missing)

                wrong_type = deepcopy(payload)
                wrong_type["effects"][0]["params"][name] = []
                with self.subTest(skill=skill, case="type"), self.assertRaises(ValidationError):
                    FFMPEGADeterministicEffectsResponse.model_validate(wrong_type)

                if parameter.maximum is not None:
                    out_of_bounds = deepcopy(payload)
                    out_of_bounds["effects"][0]["params"][name] = parameter.maximum + 1
                    with self.subTest(skill=skill, case="bound"), self.assertRaises(ValidationError):
                        FFMPEGADeterministicEffectsResponse.model_validate(out_of_bounds)

    def test_agentic_schema_excludes_structural_effects(self):
        for skill in sorted(DETERMINISTIC_SKILLS - AGENTIC_FINISHING_SKILLS):
            with self.subTest(skill=skill), self.assertRaises(ValidationError):
                FFMPEGAAgenticFinishingResponse.model_validate({
                    "effects": [valid_effect(skill)],
                })

    def test_null_defaults_are_removed_and_duplicates_are_rejected(self):
        self.assertEqual(
            validate_typed_effects({"effects": [valid_effect("vignette")]}),
            [{"skill": "vignette", "params": {}}],
        )
        duplicate = valid_effect("vignette")
        with self.assertRaises(ValueError):
            validate_typed_effects({"effects": [duplicate, duplicate]})


if __name__ == "__main__":
    unittest.main()
