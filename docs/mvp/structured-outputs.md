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
- `render_critic.v1`
- `candidate_comparison.v1`
- `post_render_repair.v1` (read-only compatibility)
- `post_render_repair.v2`
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

Strict boundaries use the 9Router Responses-compatible endpoint with
`text.format.type=json_schema`, `strict=true`, and `store=false`. The existing
Chat Completions `json_object` transport remains the rollback path for
unallowlisted boundaries. This split is intentional: the production route did
not consistently enforce Chat Completions `response_format`, while the
Responses transport passed repeated acceptance, extra-field rejection, and
multimodal probes. Local Pydantic and semantic validation still run after every
provider success.

Registry-driven semantic repair is independently controlled by
`OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE`. `off` bypasses eligibility and makes no
repair call. `report` evaluates the same evidence and records only a redacted
would-call contract. `enforce` may make at most one visual-understanding repair
per affected clip (bounded to the eight-clip repair limit) and one primary plus
at most one contingency pre-render edit-plan batch. The
contingency is reserved for a genuinely new authoritative defect after primary
revalidation. Advisory-only,
post-render, provider, media, security, unknown, and FFMPEGA findings cannot
trigger these calls. Returning the setting to `off` is the repair rollback and
does not disable the defect registry or strict schema transport.

`OPENSTORYLINE_POST_RENDER_REVIEW_MODE=enforce` additionally enables the
strict `post_render_repair.v2` boundary after a rendered-evidence critic
finding. `post_render_repair.v1` remains readable for historical artifacts but
is not emitted for new requests. The model can return only typed replacement
plans for affected clips or a typed replacement for the allowlisted FFMPEGA
finishing plan. Deterministic code preserves source bounds and protected
intent, rerenders only affected clips, applies finishing only through the
typed sidecar contract, repeats technical QA, and promotes a candidate only
when the rendered critic shows improvement without a new authoritative blocker.
One primary request is allowed; one contingency request is allowed only for a
new objective defect. Sidecar failure records an omitted effect and does not
trigger another semantic request.

The critic receives bounded transient narrative context: timestamped transcript
segments, scene/cut counts, caption-event counts, and hold-length metrics. These
values ground pacing and coherence findings but are never persisted in the
critic, comparison, or outcome artifacts. `candidate_comparison.v1` is called
only when original and repaired candidates are materially different and both
survive deterministic gates; a single candidate, unchanged evidence, or a
technical blocker produces a no-call outcome.

The deploy wrapper runs Responses-based strict acceptance and extra-field
rejection probes whenever `json_schema` mode is selected. A failed probe blocks
deployment. Run the isolated probe directly with:

```bash
python scripts/qa_ninerouter.py \
  --strict-models --live-inference --strict-schema --skip-ssh
```

The production ordering, owners, validation signals, and rollback switches are
defined in [agentic-defect-repair-rollout.md](agentic-defect-repair-rollout.md).
`./bin/kamal-mvp rollout validate` checks the flag order without calling a
provider or changing deployment state.

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
