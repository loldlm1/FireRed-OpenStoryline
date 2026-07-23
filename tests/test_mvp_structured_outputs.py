import unittest

from pydantic import ValidationError

from open_storyline.mvp.structured_outputs import (
    SHORTS_SELECTION_SCHEMA,
    STRUCTURED_OUTPUTS,
    StructuredOutputError,
    parse_structured_output_boundaries,
    structured_output,
    validate_provider_schema,
)


EXPECTED_FINGERPRINTS = {
    "shorts_selection.v1": "f6c77a08235d09402843649b3d06d0396da4e81fdfada570454b3f894c80ca1c",
    "visual_understanding.v1": "701fe40251a17deb1a1ee10e48cf91346ccd035d628e6711757ab9a4db83cd92",
    "edit_plan.v1": "d24a6164feaf3ac90e7df3b4606235122b5056ffa7c2ebca3967032525c96868",
    "edit_plan_repair.v1": "d24a6164feaf3ac90e7df3b4606235122b5056ffa7c2ebca3967032525c96868",
    "semantic_qa.v1": "7384f780cec8500caed70bd2bebe5f3b672866a019ce42670927cdf0d12f77bf",
    "render_critic.v1": "c0fa3c631f386dde07c0e50d2ea82341b84575d52b7d0245d949aeda7dc32f0c",
    "ffmpega_agentic_finishing.v1": "9d7b956f9e9e00b9f243eaf518af6faabd1972e4a4315591ef36402ff22a5496",
    "ffmpega_deterministic_effects.v1": "fd8cde2fdc36ad66e4fb23eb958415afbda1944f07abbfeced9c02dfc1b57e0f",
}


class StructuredOutputRegistryTests(unittest.TestCase):
    def test_registry_is_versioned_deterministic_and_private_free(self):
        self.assertEqual(
            {name: item.fingerprint for name, item in STRUCTURED_OUTPUTS.items()},
            EXPECTED_FINGERPRINTS,
        )
        serialized = str({name: item.schema for name, item in STRUCTURED_OUTPUTS.items()})
        for private_value in (
            "Bearer ",
            "data:image",
            "NINEROUTER_KEY",
            "MISTRAL_API_KEYS",
            "editing_prompt",
            "transcript_text",
        ):
            self.assertNotIn(private_value, serialized)

    def test_all_objects_forbid_extras_and_require_every_property(self):
        for definition in STRUCTURED_OUTPUTS.values():
            with self.subTest(schema=definition.name):
                validate_provider_schema(definition.schema)

    def test_unknown_boundaries_fail_closed(self):
        self.assertEqual(
            parse_structured_output_boundaries(SHORTS_SELECTION_SCHEMA),
            frozenset({SHORTS_SELECTION_SCHEMA}),
        )
        with self.assertRaises(StructuredOutputError):
            parse_structured_output_boundaries("unknown.v1")
        with self.assertRaises(StructuredOutputError):
            structured_output("unknown.v1")

    def test_schema_validator_rejects_defaults_and_open_objects(self):
        with self.assertRaises(StructuredOutputError):
            validate_provider_schema({
                "type": "object",
                "properties": {"ok": {"type": "boolean", "default": True}},
                "required": ["ok"],
                "additionalProperties": False,
            })
        with self.assertRaises(StructuredOutputError):
            validate_provider_schema({
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            })

    def test_wire_validation_is_strict_and_forbids_extras(self):
        definition = structured_output(SHORTS_SELECTION_SCHEMA)
        valid = {
            "clips": [{
                "start_ms": 0,
                "end_ms": 20_000,
                "title": "Title",
                "hook": "Hook",
                "reason": "Reason",
                "score": 0.9,
            }],
        }
        self.assertEqual(definition.validate(valid), valid)
        with self.assertRaises(ValidationError):
            definition.validate({**valid, "private": "not allowed"})
        invalid_type = {"clips": [{**valid["clips"][0], "start_ms": "0"}]}
        with self.assertRaises(ValidationError):
            definition.validate(invalid_type)


if __name__ == "__main__":
    unittest.main()
