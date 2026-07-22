# Agentic defect-repair rollout

This runbook stages strict structured outputs, bounded LLM repair, technical-pass
delivery, and retry details without weakening deterministic validation. Every
production change requires a private-free provider probe, healthy application
and database checks, backup/restore readiness, sanitized canary evidence, and
operator approval. Metrics and `claim_ready` are evidence only; neither can
enable a flag.

Run this offline validator after every flag edit and before any Kamal command:

```bash
./bin/kamal-mvp rollout validate
```

The validator makes no provider or deployment call. `setup`, `deploy`, and
`redeploy` additionally run the existing live 9Router and Mistral gates.

## Ownership and controls

| Control | Owner | Default | Enable | Disable | Validation signal | Rollback signal |
| --- | --- | --- | --- | --- | --- | --- |
| Defect registry and read-only presentation | Application reliability | Always on | Deploy compatible readers | Roll back code only after outcome compatibility review | Registry/hash tests and bounded audit output | Unknown or historical codes stop rendering safely in API/UI |
| Strict-schema capability probe | Provider operations | Unverified | Run `scripts/qa_ninerouter.py --strict-models --live-inference --strict-schema --skip-ssh`, then set `OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED=true` | Set the verification flag to `false` before returning to permissive mode | Responses-based acceptance and extra-field rejection both pass for the configured model | Schema unsupported, mismatch, refusal, incomplete response, or provider regression |
| Strict boundaries | AI application owner | `json_object`, empty list | Set `json_schema` and add the next complete prefix described below | Remove boundaries in reverse order, then restore `json_object` | Strict validity and local semantic validity remain healthy | Higher schema failures, latency, cost, or lower playable output |
| Repair report | AI application owner | `off` | After all strict boundaries, set agentic mode to `shadow` or `render` and repair mode to `report` | Set repair mode to `off` | Eligible dispositions match enforce mode while semantic repair calls remain zero | Unexpected eligibility, private evidence, or unbounded request/report |
| Repair enforce | AI application owner | `off` | Set agentic mode to `render` and repair mode to `enforce` | Set repair mode to `report` or `off` | At most one visual and one plan call; no FFMPEGA repair calls; no new defects | Repair failures, new defects, checkpoint mismatch, latency, or cost threshold |
| Technical-pass delivery | QA/release owner | `qa_enforced` | Keep creative QA strict, set render promotion to `enforce`, then set delivery to `technical_pass_guaranteed` | Restore `qa_enforced` | Creative-only blockers publish truthfully; technical and mixed blockers remain withheld | Any technical blocker becomes downloadable or strict evidence is rewritten |
| Retry/details UI | Product QA owner | `false` | Set `OPENSTORYLINE_RETRY_UX_ENABLED=true` last | Set it to `false` | Focused desktop/mobile comparison flow passes without console errors | Retry action, comparison, accessibility, or activity regression |

## Strict boundary order

Each deployed boundary set must be a complete prefix. The two edit-plan schemas
and the two FFMPEGA schemas move together.

1. `shorts_selection.v1`
2. Add `visual_understanding.v1`
3. Add `edit_plan.v1,edit_plan_repair.v1`
4. Add `semantic_qa.v1`
5. Add `ffmpega_agentic_finishing.v1,ffmpega_deterministic_effects.v1`
6. Set repair to `report`, then separately to `enforce`
7. Set delivery to `technical_pass_guaranteed`
8. Enable retry/details UI

Example fully staged canary values:

```bash
OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_schema
OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED=true
OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES=shorts_selection.v1,visual_understanding.v1,edit_plan.v1,edit_plan_repair.v1,semantic_qa.v1,ffmpega_agentic_finishing.v1,ffmpega_deterministic_effects.v1
OPENSTORYLINE_AGENTIC_EDITING_MODE=render
OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=enforce
OPENSTORYLINE_RENDER_PROMOTION_MODE=enforce
OPENSTORYLINE_DELIVERY_POLICY=technical_pass_guaranteed
OPENSTORYLINE_RETRY_UX_ENABLED=true
```

`OPENSTORYLINE_SEMANTIC_QA_ENABLED` and `OPENSTORYLINE_FFMPEGA_ENABLED` remain
independent feature flags. They may become `true` only after their strict
boundaries are present. FFMPEGA failure always uses native deterministic render
fallback or fails safely; it never adds a semantic repair call.

Before enabling FFMPEGA, deploy and verify the separate pinned, model-free
service. It is private to the Kamal network and shares only the outputs root:

```bash
./bin/kamal-mvp ffmpega deploy
./bin/kamal-mvp ffmpega readiness
```

The application release wrapper refuses an enabled FFMPEGA deployment unless
the service is healthy, reports the pinned upstream commit, uses
`http://openstoryline-mvp-ffmpega:8188`, and maps the exact
`KAMAL_OUTPUTS_DIR`. Disable the application flag before stopping or rolling
back the sidecar.

## Release and canary gate

Before production enablement:

```bash
./bin/kamal-mvp db backup
./bin/kamal-mvp db restore-check
./bin/kamal-mvp rollout validate
./bin/kamal-mvp deploy
```

After deployment, verify `/up`, `/health`, database readiness, the exact image
version, and sanitized `audit outcomes`/`audit defects` summaries. Use only an
authorized private session without copying its prompt, transcript, media,
frames, provider bodies, credentials, or raw reports into Git or chat. Verify
playback and download registration, truthful creative limitations, technical
withholding, repair checkpoint reuse, call counts, tokens, cost, latency, and
the absence of new defects.

Review thresholds are emitted in `outcome_slo_summary.v1`:

- repair provider latency p95 at or below 180 seconds;
- repair cost per trigger at or below USD 0.25;
- zero new-defect rate;
- playable output rate at or above 99%.

The 99% statement additionally requires the Wilson confidence gate. A passing
sample still requires explicit operator approval and does not mutate rollout
configuration.

## Rollback

1. Set `OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off`.
2. Restore `OPENSTORYLINE_DELIVERY_POLICY=qa_enforced`.
3. Remove FFMPEGA, semantic QA, edit-plan, visual, and shorts strict boundaries
   in reverse order, then restore `OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_object`.
4. Set `OPENSTORYLINE_RETRY_UX_ENABLED=false`.
5. Validate flags, deploy the prior compatible image if required, and recheck
   `/up`, `/health`, database readiness, playback, downloads, and audit output.

Additive repair/outcome evidence remains in PostgreSQL and job artifacts. Do not
delete it during rollback. Review schema compatibility before selecting an older
image; use the existing explicit-version Kamal rollback command.
