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
| Post-render critic report | AI application owner | `off` | After `render_critic.v1` is in the strict prefix, set `OPENSTORYLINE_POST_RENDER_REVIEW_MODE=shadow` or `report` | Set it to `off`; deterministic QA and promotion remain authoritative | Findings reference supplied evidence only, calls are fingerprinted, and no plan/output mutation occurs | Provider/schema failures, private evidence, duplicate calls, or creative findings treated as technical blockers |
| Post-render repair enforce | AI application owner | `off` | After `post_render_repair.v2`, strict deterministic QA, and enforced pre-render repair are active, set post-render review to `enforce` for a private canary | Return post-render review to `report` or `off`; the original validated candidate remains eligible | One primary repair at most, one new-objective-defect contingency at most, localized rerendering, typed effect execution, and demonstrated critic improvement with no new deterministic blocker | Third semantic request, invalid typed patch, unsupported effect, sidecar failure without truthful omission, candidate regression, private evidence, or missing rollback candidate |
| Candidate comparison | AI application owner | `off` | Add `candidate_comparison.v1` and enable post-render enforce only after both candidates pass deterministic gates | Set review to `report` or `off`; deterministic promotion remains authoritative | One evidence-grounded preference call only for two materially different eligible candidates; tie/uncertainty is recorded | Comparison for one candidate, technical override, unknown evidence, duplicate fingerprint, or provider failure treated as a repair retry |
| Repair report | AI application owner | `off` | After all strict boundaries, set agentic mode to `shadow` and repair mode to `report` | Set repair mode to `off` | Eligible dispositions match enforce mode while semantic repair calls remain zero | Unexpected eligibility, private evidence, or unbounded request/report |
| Repair enforce | AI application owner | `off` | Set agentic mode to `render`, repair mode to `enforce`, baseline fallbacks to `true`, and retry UX to `true` | Leave render mode first, then set repair mode to `off` or use an explicit shadow/off profile | At most one visual repair call per affected clip (maximum eight clips) and at most two plan batches (`primary` plus new-defect `contingency`); no FFMPEGA repair calls; no invariant violations | Repair failures, third-call attempt, new defects, checkpoint mismatch, latency, cost, or playable-rate threshold |
| Technical-pass delivery | QA/release owner | `qa_enforced` | Keep creative QA strict, set render promotion to `enforce`, then set delivery to `technical_pass_guaranteed` | Restore `qa_enforced` | Creative-only blockers publish truthfully; technical and mixed blockers remain withheld | Any technical blocker becomes downloadable or strict evidence is rewritten |
| Retry/details UI | Product QA owner | `false` | Set `OPENSTORYLINE_RETRY_UX_ENABLED=true` last | Set it to `false` | Focused desktop/mobile comparison flow passes without console errors | Retry action, comparison, accessibility, or activity regression |

## Strict boundary order

Each deployed boundary set must be a complete prefix. The two edit-plan schemas
and the two FFMPEGA schemas move together.

1. `shorts_selection.v1`
2. Add `visual_understanding.v1`
3. Add `edit_plan.v1,edit_plan_repair.v1`
4. Add `semantic_qa.v1`
5. Add `render_critic.v1`; then set post-render review to `shadow` or `report`.
6. Add `post_render_repair.v2` (keep `post_render_repair.v1` readable).
7. Add `candidate_comparison.v1`; private canaries may then set post-render
   review to `enforce`.
8. Add `ffmpega_agentic_finishing.v1,ffmpega_deterministic_effects.v1` and
   require a healthy pinned sidecar before enabling finishing.
9. Set repair to `report` while agentic editing remains `shadow`.
10. Switch the production profile atomically: agentic `render`, repair
   `enforce`, baseline fallbacks `true`, promotion `enforce`, delivery
   `technical_pass_guaranteed`, and retry/details UI `true`.

The production cutover is one validated configuration edit because partial
render combinations are intentionally invalid. `off` and `shadow` remain the
only profiles for staged observation and rollback.

Example fully staged canary values:

```bash
OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_schema
OPENSTORYLINE_STRUCTURED_OUTPUT_CAPABILITY_VERIFIED=true
OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES=shorts_selection.v1,visual_understanding.v1,edit_plan.v1,edit_plan_repair.v1,semantic_qa.v1,render_critic.v1,post_render_repair.v1,post_render_repair.v2,candidate_comparison.v1,ffmpega_agentic_finishing.v1,ffmpega_deterministic_effects.v1
OPENSTORYLINE_POST_RENDER_REVIEW_MODE=report
OPENSTORYLINE_AGENTIC_EDITING_MODE=render
OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=enforce
OPENSTORYLINE_BASELINE_FALLBACKS_ENABLED=true
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

The enforce canary also verifies the one-or-many output contract. A single input
session may produce one or several bounded output clips; deterministic artifact
and promotion checks operate per clip and never assume exactly one output. A
canary that exposes several short speech pauses must keep those pauses visible
as a pacing warning, while only a sustained silent interval may be a technical
promotion blocker. This prevents cumulative natural pauses from being
misclassified as an unplayable backend failure without weakening the technical
gate for genuinely silent output.

The review scorecard must also report candidate-comparison calls, completed
preferences, ties, skipped no-call decisions, checkpoint reuse, and invariant
violations. A comparison provider failure is reportable and falls back to the
deterministic candidate gate; it never authorizes another repair request.
Promotion gates require zero third repair calls, zero promoted new technical
defects, no raw evidence persistence, stable artifact lineage, and an
owner-approved human preference aggregate from the rubric in
[agentic-video-review-eval.md](agentic-video-review-eval.md).

Queue the newest immutable prompt when it is the intended canary, or select an
older authoritative version explicitly when a targeted QA variant is newer:

```bash
./bin/kamal-mvp workspace rerun-latest SESSION_ID --wait
./bin/kamal-mvp workspace rerun-version SESSION_ID PROMPT_VERSION_ID --wait
```

Both commands keep prompt text private and reject prompt versions outside the
specified reusable session.

Review thresholds are emitted in `outcome_slo_summary.v1`:

- repair provider latency p95 at or below 180 seconds;
- repair cost per trigger at or below USD 0.25;
- primary and contingency call counts remain separately attributable, with no
  third plan-repair attempt;
- zero new-defect rate;
- playable output rate at or above 99%.

The 99% statement additionally requires the Wilson confidence gate. A passing
sample still requires explicit operator approval and does not mutate rollout
configuration.

## Completed final-image evidence

The 2026-07-22 same-session production proof uses exact application image
`1e15456b76e84d1f83d7b4d6b318ec348f9d7b02`, PostgreSQL revision
`20260721_0003`, the configured `cx/gpt-5.6-sol` route, all seven strict
boundaries, semantic QA, enforced bounded repair, technical-pass delivery, and
the pinned FFMPEGA service.

| Gate | Result | Evidence boundary |
| --- | --- | --- |
| Start-of-video asset visibility | PASS | Shared FFmpeg timebase/frame-rate normalization fixes the healthy-path unavailable result; focused regression and the prior real artifact pass. |
| Full Python validation | PASS | 455 local tests with 78 expected database skips and 455 disposable connected-PostgreSQL tests. |
| Release dependencies | PASS | 9Router strict/text/vision/image, direct Mistral STT, FFMPEGA readiness, `/up`, `/health`, database head, exact image, backup, and restore checks. |
| Authoritative repeatability | PASS | Two consecutive immutable same-source/same-prompt outputs pass deterministic, semantic, asset-visibility, playback, subtitle, promotion, and direct-inspection gates. |
| Typed FFMPEGA execution | PASS | The existing targeted prompt selects typed `sharpen(amount=0.4)` and registers a playable effects output with zero omission or introduced defects. |
| Healthy-path unavailable analysis | PASS | Zero semantic- or asset-analysis-unavailable codes in the final consecutive pair and final-image effect canary. |
| Portrait active-picture composition | REQUIRED | A landscape source rendered with `fit` must show the full foreground over a dimmed blurred full-canvas background; explicit `letterbox` must retain solid padding and trigger repair when predictive active-picture thresholds fail. Inspect opening, midpoint/transition, subtitle or overlay moments, and ending frames directly. |
| Broad 99% reliability | OPEN | `claim_ready=false`; 7 classified attempts yield `0.857143` playable rate and Wilson `[0.486872, 0.974320]`. |
| Quantified effect quality lift | OPEN | Execution and non-regression are proven, but the subtle sharpen result lacks a retained native A/B artifact showing strong lift. |
| Authenticated production browser | OPEN | Artifact registration and direct decode pass; the operator-held plaintext password is unavailable to automation and authentication must not be bypassed. |

The production claim is therefore scoped to the tested uploaded source, the
authoritative prompt, the one targeted effect prompt, and the exact final image.
It is not a cross-source, cross-niche, or 99%-reliability statement.

This evidence closed the earlier defect-repair rollout. The adaptive rendered
review upgrade below supersedes its final-image implementation evidence without
turning the older result into a broad quality claim.

## Adaptive rendered-review rollout evidence

The 2026-07-23 closeout uses exact application image
`675d1b5a71acff248e829edf4623982c687535f9`, PostgreSQL revision
`20260723_0004`, the full strict-schema prefix, enforced Agentic editing,
adaptive rendered evidence, technical-pass delivery, and the pinned FFMPEGA
boundary. Sanitized evidence only is recorded here.

| Gate | Result | Evidence boundary |
| --- | --- | --- |
| Full Python validation | PASS | 541 tests pass with 85 expected PostgreSQL/provider skips. |
| Release dependencies | PASS | The release wrapper passes mandatory 9Router text/schema/vision/image, direct Mistral STT, FFMPEGA readiness, database head, build/deploy, exact-image, `/up`, and `/health` gates. |
| Historical data safety | PASS | Three workflow-version-1 sessions, six terminal jobs, and 49 artifacts remain non-executable audit history with zero active legacy jobs. No destructive migration or media deletion ran. |
| One-or-many output contract | PASS | The immutable private input produced three bounded, registered, downloadable outputs; artifact and promotion checks did not assume one output. |
| Critic evidence alignment | PASS with provider variability | One canary critic call returned 14 repairable findings and all 14 windows were normalized to authoritative clip/evidence timestamps without another LLM call. A later call returned a status/findings mismatch and was rejected locally. |
| Repair call attribution | PASS with unavailable repair | The bounded primary repair call was counted as one after the provider response, including local semantic validation failure. No contingency, third repair, or comparison call followed. |
| Technical promotion | PASS | Deterministic and semantic checks passed. Invalid critic or repair evidence produced `publish_with_limitations`, never a false enhanced label or legacy fallback. |
| Deployed browser smoke | PASS | Chromium smoke passes with console-error enforcement on the exact final image. |
| Authenticated production browser | OPEN | The operator-held plaintext password was unavailable to automation; authentication was not bypassed. |
| Broad human preference and 99% reliability | OPEN | One private source cannot establish cross-source creative lift, professional-quality equivalence, or the Wilson reliability gate. |

The canary proves bounded calls, deterministic evidence normalization, truthful
failure attribution, one-to-many output handling, and safe promotion behavior.
It does not prove that every provider response will produce an accepted creative
repair, nor does it establish broad human-preference improvement.

## Rollback

1. In one configuration edit, leave production render mode by setting
   `OPENSTORYLINE_AGENTIC_EDITING_MODE=off` (or the documented shadow profile)
   and `OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off`; the validator rejects either
   half-applied combination.
2. Restore `OPENSTORYLINE_BASELINE_FALLBACKS_ENABLED=false` and
   `OPENSTORYLINE_DELIVERY_POLICY=qa_enforced`.
3. Remove FFMPEGA, semantic QA, edit-plan, visual, and shorts strict boundaries
   in reverse order, then restore `OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_object`.
4. Set `OPENSTORYLINE_RETRY_UX_ENABLED=false`.
5. Validate flags, deploy the prior compatible image if required, and recheck
   `/up`, `/health`, database readiness, playback, downloads, and audit output.

Additive repair/outcome evidence remains in PostgreSQL and job artifacts. Do not
delete it during rollback. Review schema compatibility before selecting an older
image; use the existing explicit-version Kamal rollback command.
