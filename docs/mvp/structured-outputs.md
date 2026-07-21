# Remote Structured Outputs

The remote MVP keeps provider wire schemas in
`src/open_storyline/mvp/structured_outputs.py`. They are stable, versioned,
private-free Pydantic contracts. Every object rejects additional properties,
every property is required, and optional values use an explicit nullable type.
Application Pydantic models and deterministic semantic validators remain
authoritative after provider validation.

Registered boundaries are:

- `shorts_selection.v1`
- `visual_understanding.v1`
- `edit_plan.v1`
- `edit_plan_repair.v1`
- `semantic_qa.v1`
- `ffmpega_agentic_finishing.v1`
- `ffmpega_deterministic_effects.v1`

`OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_object` is the rollback-compatible
default. To activate strict schemas progressively, set the mode to
`json_schema`, set `OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES` to a
comma-separated subset of the registered names, and set
`OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED=true` only after the
private-free probe passes for the configured 9Router route and model. Unknown
boundary names fail startup. Strict boundaries never downgrade silently to
permissive parsing.

The deploy wrapper runs the strict acceptance and extra-field rejection probes
whenever `json_schema` mode is selected. A failed probe blocks deployment. Run
the isolated probe directly with:

```bash
python scripts/qa_ninerouter.py \
  --strict-models --live-inference --strict-schema --skip-ssh
```

The FFMPEGA contracts are pinned to
`AEmotionStudio/ComfyUI-FFMPEGA` commit
`0cfe2db05df104f95c98cc45e11f129fa5ef5193`. All 26 deterministic effects use
effect-specific parameter types, bounds, and enums from that registry. The
21-effect agentic finishing schema continues to exclude `deshake`, `fade`,
`letterbox`, `mirror`, and `rotate`. Parameters with authoritative upstream
defaults use required nullable fields; local validation removes `null` before
execution so the pinned upstream default applies.

No provider response body, private prompt, transcript, media data, credential,
user identifier, or per-job value is stored in a schema or capability result.
