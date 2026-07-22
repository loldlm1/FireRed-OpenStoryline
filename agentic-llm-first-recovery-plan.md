# Plan: Agentic LLM-First Recovery Hardening

**Generated**: 2026-07-22
**Status**: Planning only; implementation not started
**Estimated Complexity**: High

## Overview

Harden the remote social-clips MVP so every repairable editorial, visual,
planning, or render-composition defect is presented to an LLM at least once
before a deterministic quality fallback is allowed. The first regression is
`COMPOSITION_CROP_TARGET_TOO_WIDE`, which is currently detected inside the
compositor after the existing `edit_plan_repair.v1` opportunity has passed.

The implementation will establish an explicit, bounded recovery state machine:

```text
deterministic full-plan detection
  -> primary strict-schema LLM repair batch
  -> deterministic full-plan revalidation
  -> accept a defect-free candidate or discard it
  -> optional contingency LLM batch for newly discovered authoritative defects
  -> deterministic segment-local fallback for attempted defects
  -> shared compositor dry-run
  -> FFmpeg execution
```

The normal budget is one batched plan-repair call. A second plan-repair call is
permitted only when a genuinely new repairable defect affects the authoritative
pre-render plan after the primary batch. Candidate-only defects do not consume
the contingency budget because the candidate is rejected. No third
plan-repair batch is permitted. Provider timeout, refusal, transport failure,
or invalid structured output counts as an attempted LLM repair when the
outbound attempt is recorded; the affected defect may then use its approved
deterministic fallback.

Security, source-integrity, authentication, database, unsafe-path, corrupt
media, and other non-repairable infrastructure failures continue to fail
closed. The implementation must not merge the full local agent and remote MVP
profiles, change providers or models, expose production evidence, or mutate
production during development.

## Scope

- **In scope**:
  - A registry-wide invariant for repairable creative, planning, evidence, and
    composition defects.
  - Early deterministic crop-feasibility detection using the same calculations
    as the compositor.
  - One primary and at most one contingency `edit_plan_repair.v1` batch.
  - Strict field-level repair constraints, deterministic candidate validation,
    and new-defect rejection.
  - Engine-authorized, segment-local `fit` or `letterbox` fallback after an LLM
    attempt, even when the original plan did not grant full-frame fallback.
  - Final compositor dry-run before any FFmpeg execution.
  - Accurate repair/checkpoint/fallback attribution in successful and failed
    outcomes, audit documents, and rollout metrics.
  - Plain retained-session rerun independent of quality-feedback repair.
  - Production rollout validation requiring enforced repair for agentic render
    mode, plus synthetic regression coverage and operator rollback guidance.
- **Out of scope**:
  - Changing the configured `cx/gpt-5.6-sol` route, 9Router transport, Mistral
    STT, FFMPEGA service, or structured-output provider API.
  - Automatically creating unbounded recursive jobs after post-render QA.
    Post-render recovery remains a new retained-session attempt.
  - Asking an LLM to repair corrupt/missing media, authorization failures,
    unsafe paths, database failures, invalid executable output, or secrets.
  - Rewriting `.storyline/skills/`, bilingual model prompts unrelated to the
    repair contract, or the tracked opaque `agent_fastapi.py` artifact.
  - Deploying, rerunning a production session, calling live/paid providers, or
    copying private production evidence into tests, documents, logs, or Git.
- **Fixed decisions**:
  - The LLM-first invariant applies to all defects classified as repairable,
    not only crop geometry.
  - All currently known plan-repair findings are grouped into one primary
    strict-schema batch rather than one call per defect.
  - A recorded outbound provider attempt satisfies the LLM-attempt gate even
    when no valid response is returned.
  - The plan-repair budget is two: one primary batch and one contingency batch
    only for new authoritative pre-render defects.
  - Candidate-only defects cause candidate rejection and do not trigger the
    contingency batch.
  - A deterministic fallback may alter only affected segment layout/focal
    fields and must preserve timing, source bounds, output count, source media,
    assets, subtitles, and unrelated operations.
  - Agentic production render mode requires repair mode `enforce`; `off` and
    `report` remain shadow/development or emergency rollback configurations.
  - Every retained failed session exposes a plain rerun when its immutable
    prompt and source remain available, regardless of quality-feedback support.
- **Assumptions**:
  - The existing `edit_plan_repair.v1` response schema remains the provider
    schema; stronger behavior is enforced through bounded request context,
    allowed-mutation metadata, merge rules, and deterministic validation.
  - The existing visual observations contain enough normalized geometry for an
    early feasibility assessment; no new frame bytes or provider call is needed.
  - `fit` is the default content-preserving geometry fallback. `letterbox` is
    retained only when selected by existing style/configuration policy.
  - No PostgreSQL migration is required. Repair rounds, attempts, and fallback
    evidence remain versioned JSON artifacts/checkpoints and bounded outcome
    fields. If implementation proves a migration unavoidable, stop before
    creating it and revise this plan.
  - Existing repair cost and latency rollout thresholds are not silently
    relaxed. Canary evidence must show the effect of the contingency call before
    any threshold change receives separate approval.

## Recovery Invariant

For each authoritative repairable defect instance, identified by canonical
code, clip index, segment/operation identifier, and authoritative plan
fingerprint, deterministic fallback is allowed only when at least one of these
conditions is true:

1. The defect instance was included in a recorded primary LLM repair request.
2. The defect instance was newly discovered after the primary batch and was
   included in a recorded contingency LLM repair request.

The following do not satisfy the invariant:

- A model call earlier in the job that did not include the defect instance.
- A repair call for another clip, segment, or defect code.
- A report-only disposition without an outbound call in production enforce
  mode.
- A deterministic fallback applied before repair-attempt evidence is persisted.

Candidate-only defects are evaluated but are not authoritative defect
instances when the candidate is rejected. If a candidate is accepted, the full
validator suite must report no unresolved or newly introduced objective defect.
If a third plan-repair batch would be required, the engine must use the already
attempted defect's deterministic safe baseline or fail closed with an explicit
technical recovery-invariant error when no safe executable baseline exists.

## Named Resources

- **Project instructions**:
  - `AGENTS.md`
  - `docs/agent-engineering.md`
  - `docs/mvp/architecture.md`
  - `docs/mvp/defect-repair.md`
  - `docs/mvp/agentic-defect-repair-rollout.md`
- **Core implementation files**:
  - `src/open_storyline/mvp/compositor.py`
  - `src/open_storyline/mvp/preflight.py`
  - `src/open_storyline/mvp/defects.py`
  - `src/open_storyline/mvp/repair.py`
  - `src/open_storyline/mvp/pipeline.py`
  - `src/open_storyline/mvp/fallbacks.py`
  - `src/open_storyline/mvp/render.py`
  - `src/open_storyline/mvp/checkpoints.py`
  - `src/open_storyline/mvp/outcomes.py`
  - `src/open_storyline/mvp/observability.py`
  - `src/open_storyline/mvp/audit.py`
  - `src/open_storyline/mvp/jobs.py`
  - `src/open_storyline/mvp/prompt_versions.py`
  - `src/open_storyline/mvp/api.py`
  - `web/static/mvp/app.js`
  - `web/static/mvp/views.js`
- **Configuration and release resources**:
  - `config/deploy.yml`
  - `.env.mvp.example`
  - `.env.kamal.example`
  - `bin/kamal-mvp`
  - `docs/mvp/agentic-defect-repair-rollout.md`
  - `docs/mvp/architecture.md`
  - `docs/mvp/guia-es.md`
- **Tests and fixtures**:
  - `tests/test_mvp_compositor.py`
  - `tests/test_mvp_preflight.py`
  - `tests/test_mvp_defects.py`
  - `tests/test_mvp_repair.py`
  - `tests/test_mvp_repair_evals.py`
  - `tests/test_mvp_pipeline.py`
  - `tests/test_mvp_fallbacks.py`
  - `tests/test_mvp_outcomes.py`
  - `tests/test_mvp_observability.py`
  - `tests/test_mvp_audit.py`
  - `tests/test_mvp_prompt_versions.py`
  - `tests/test_mvp_app.py`
  - `tests/test_kamal_config.py`
  - `tests/fixtures/mvp_agentic/`
  - `.qa/web/tests/mvp-workspace.spec.ts`
  - `.qa/web/tests/mvp-auth-sessions.spec.ts`
- **Provider and schema contracts**:
  - `src/open_storyline/mvp/structured_outputs.py`
  - `src/open_storyline/mvp/ninerouter.py`
  - Existing `edit_plan_repair.v1` strict structured-output boundary.
  - No external provider/API documentation change is required because this
    plan does not change the configured provider, model, endpoint, or response
    API. The repository's strict-schema capability probe remains the release
    authority.
- **Operational evidence**:
  - Sanitized `repair_report.json`, `outcome_report.json`,
    `fallback_ledger.json`, `edit_preflight.json`, and FFmpeg preflight artifact.
  - `./bin/kamal-mvp rollout validate` and sanitized audit outcome/defect/SLO
    summaries.
  - Private production media, prompts, transcripts, frames, raw provider
    responses, and temporary incident files are explicitly excluded.

## Prerequisites

- Use Python 3.11+ and the repository `.venv`; set `PYTHONPATH=src` for tests.
- Confirm the working tree and preserve unrelated user changes before each
  sprint. Do not modify or remove private/generated `outputs/` or `resource/`
  data.
- Confirm FFmpeg/FFprobe availability for compositor/render regressions; report
  their documented skips when unavailable.
- Use only synthetic geometry and plan fixtures. The production incident may be
  used later as an operator-only canary after explicit authorization, never as
  a committed fixture.
- Before Sprint 1 implementation, read
  `/home/loldlm/.codex/skills/planner/references/execution-state.md` and
  initialize the required active-plan execution state.
- Record the baseline focused tests and current rollout-validator result before
  changing behavior. Planning does not claim these validations pass.

## Sprint 1: Shared Repairable-Defect Detection

**Goal**: Detect crop-feasibility and other repairable composition findings
before the repair batch, using one shared geometry evaluator that cannot drift
from compositor execution.

**Dependencies**: Prerequisites only.

**Tracked scope**: `src/open_storyline/mvp/compositor.py`,
`src/open_storyline/mvp/preflight.py`, `src/open_storyline/mvp/defects.py`,
`src/open_storyline/mvp/repair.py`, focused tests and one synthetic fixture.

**Safe parallel work**: Registry tests and synthetic fixture construction may
proceed in parallel only after the shared geometry-assessment contract is
defined. All changes remain in the single Sprint 1 commit.

**Commit**: `fix(mvp): detect repairable composition defects before rendering`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_compositor.py tests/test_mvp_preflight.py tests/test_mvp_defects.py tests/test_mvp_repair.py -v`
- Demonstrate with a private-free synthetic fixture that a protected target
  wider than the `1.12` threshold is reported before any provider or FFmpeg
  call and carries clip/segment attribution plus bounded measurements.

**Rollback point**: The repository state immediately before the Sprint 1
commit. Reverting this sprint restores late compositor-only detection and does
not require data rollback.

### Task 1.1: Extract A Shared Crop-Geometry Assessment

- **Location**: `src/open_storyline/mvp/compositor.py`
- **Description**:
  - Extract the crop feasibility calculations currently embedded in
    `_resolve_segment` into a pure, bounded assessment used by both preflight
    and final composition resolution.
  - Include source/crop/target dimensions, width and height overflow ratios,
    threshold, target region IDs, selected fallback, fallback permission, and
    allowed repair operations. Do not include frame bytes or raw provider data.
  - Keep `CROP_TARGET_MAX_OVERFLOW_RATIO` authoritative in one place.
  - Preserve existing crop smoothing and accepted geometry behavior.
- **Dependencies**: None.
- **Acceptance criteria**:
  - The assessment and compositor make identical feasibility decisions for all
    existing tests.
  - Non-finite, zero, negative, or malformed geometry fails deterministically.
  - A synthetic equivalent of the incident's approximately `1.162x` width
    overflow is classified as `COMPOSITION_CROP_TARGET_TOO_WIDE` against `1.12`.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_compositor.py -v`
- **Rollback**: Revert the extraction while retaining the original compositor
  calculations; no persisted state changes.

### Task 1.2: Add Early Composition Findings To Full Preflight

- **Location**: `src/open_storyline/mvp/preflight.py`,
  `src/open_storyline/mvp/pipeline.py`, `src/open_storyline/mvp/repair.py`
- **Description**:
  - Supply the preflight stage with the existing visual observations, source
    dimensions, and configured output dimensions needed by the shared geometry
    assessment.
  - Emit a blocking repairable finding before plan repair with canonical code,
    clip index, segment ID, measured overflow, threshold, fallback permission,
    and an allowlist of permitted mutations.
  - Extend preflight detail serialization only additively and keep all fields
    bounded and audit-safe. Preserve existing keys and readers.
  - Make `repair_findings_from_preflight` retain bounded structured details
    rather than reducing geometry evidence to severity and source strings.
- **Dependencies**: Task 1.1.
- **Acceptance criteria**:
  - The geometry finding is present in the primary repair-batch input.
  - Detection occurs before asset acquisition, renderer preflight, or FFmpeg.
  - Existing non-geometry preflight findings remain unchanged.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_preflight.py tests/test_mvp_repair.py -v`
- **Rollback**: Remove the additive preflight details and composition context;
  the shared compositor evaluator may remain internally without changing output.

### Task 1.3: Audit And Enforce Repairable Registry Classification

- **Location**: `src/open_storyline/mvp/defects.py`,
  `docs/mvp/defect-repair.md`, `tests/test_mvp_defects.py`
- **Description**:
  - Reclassify `COMPOSITION_CROP_TARGET_TOO_WIDE` as
    `LLM_PLAN_REPAIR`/pre-render with `VISUAL_REFRAME_FALLBACK`.
  - Review other composition/render codes and explicitly classify repairable
    editorial/layout cases versus non-repairable configuration, executable,
    source, security, and infrastructure cases.
  - Add a registry invariant test preventing a defect with an approved creative
    fallback from silently defaulting to terminal without an explicit policy
    exemption.
  - Keep unknown codes fail-closed.
- **Dependencies**: Task 1.2.
- **Acceptance criteria**:
  - Every repairable registered defect has a repair phase, required evidence,
    safe fallback, and retry action.
  - `COMPOSITION_CONFIG_INVALID`, unsafe paths, corrupt media, auth, and database
    failures remain non-LLM technical/terminal cases.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_defects.py -v`
- **Rollback**: Restore prior registry mappings. No artifact or database cleanup
  is required.

### Sprint 1 Gate

- [ ] All Sprint 1 tasks complete.
- [ ] Sprint 1 focused validation passes and evidence is recorded.
- [ ] The synthetic fixture contains no production identifiers or private data.
- [ ] Residual registry-classification questions are documented.
- [ ] Exactly one Sprint 1 commit is created with the proposed sprint message.
- [ ] The Sprint 1 rollback commit/reference is recorded.
- [ ] Sprint 2 has not started before this gate completes.

## Sprint 2: Bounded Two-Batch Recovery State Machine

**Goal**: Guarantee one LLM attempt per authoritative repairable defect before
fallback, permit one contingency batch for newly discovered authoritative
defects, and prevent FFmpeg execution until deterministic validation succeeds.

**Dependencies**: Sprint 1 gate.

**Tracked scope**: `src/open_storyline/mvp/repair.py`,
`src/open_storyline/mvp/pipeline.py`, `src/open_storyline/mvp/fallbacks.py`,
`src/open_storyline/mvp/render.py`, `src/open_storyline/mvp/compositor.py`, and
focused repair/pipeline/fallback tests.

**Safe parallel work**: Mutation-allowlist tests and fallback compiler tests may
run in parallel after the repair-round state contract is fixed. Pipeline
orchestration and render-order work are sequential because they share the same
state transitions.

**Commit**: `feat(mvp): enforce bounded two-batch agentic recovery`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair.py tests/test_mvp_repair_evals.py tests/test_mvp_fallbacks.py tests/test_mvp_pipeline.py tests/test_mvp_compositor.py -v`
- Demonstrate primary success, primary rejection with fallback, provider failure
  with fallback, candidate-only new-defect rejection, contingency repair, and
  hard rejection of a third batch.

**Rollback point**: The Sprint 1 commit. Reverting Sprint 2 restores the prior
single plan-repair orchestration; new additive checkpoints must be ignored by
their version/fingerprint rather than deleted.

### Task 2.1: Model Repair Rounds And Per-Defect Attempt Gates

- **Location**: `src/open_storyline/mvp/repair.py`
- **Description**:
  - Add explicit `primary` and `contingency` plan-repair rounds with a hard
    maximum of two plan calls. Preserve the existing independent visual-repair
    budget.
  - Identify defect instances by canonical code, clip, segment/operation, and
    authoritative plan fingerprint.
  - Record whether each defect was included in an outbound call, the call
    result, schema validity, semantic validity, and fallback eligibility.
  - Treat timeout, refusal, transport error, incomplete response, schema
    mismatch, and invalid semantic output as attempts only when the outbound
    provider attempt is evidenced.
  - Make fallback eligibility a validated function of attempt evidence rather
    than an incidental control-flow branch.
- **Dependencies**: Sprint 1 registry and finding contracts.
- **Acceptance criteria**:
  - A fallback request without matching attempt evidence is rejected.
  - Report-only mode never masquerades as an attempt.
  - No path can allocate a third plan-repair batch.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair.py -v`
- **Rollback**: Restore the one-call `RepairBudget` and remove round metadata;
  do not delete persisted additive evidence.

### Task 2.2: Build One Comprehensive Primary Repair Batch

- **Location**: `src/open_storyline/mvp/pipeline.py`,
  `src/open_storyline/mvp/repair.py`
- **Description**:
  - Run the complete deterministic finding suite, including geometry,
    capability, coverage, catalog, creative-intent, timing, overlay, subtitle,
    and predictive objective checks, before the primary batch.
  - Batch all bounded eligible findings into one `edit_plan_repair.v1` request.
  - Include immutable constraints and defect-specific permitted mutation paths.
    Geometry repairs may change only affected layout mode, focal target,
    fallback choice/permission, safe margin, and bounded zoom fields.
  - Preserve clip timing, source windows, output count, assets, subtitle
    requirements, catalog constraints, and unaffected operations.
- **Dependencies**: Task 2.1.
- **Acceptance criteria**:
  - Every known eligible finding appears in the primary request or is explicitly
    reported as bounded overflow with no silent fallback.
  - One provider call covers multiple defects without weakening per-defect
    attribution.
  - Private prompts/transcripts remain bounded and hashed in observability.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair.py tests/test_mvp_pipeline.py -v`
- **Rollback**: Restore the existing batch construction and one-stage request
  context.

### Task 2.3: Reject Unsafe Candidates And Distinguish New Defects

- **Location**: `src/open_storyline/mvp/repair.py`,
  `src/open_storyline/mvp/pipeline.py`
- **Description**:
  - Re-run the full deterministic finding suite and shared compositor
    assessment against the candidate.
  - Accept only candidates that resolve eligible objective findings, introduce
    no objective defect, obey the field mutation allowlist, and pass the
    existing repair quality floor.
  - Discard candidate-only defects with the candidate; do not spend the
    contingency call on a plan that will not become authoritative.
  - Compute newly discovered authoritative defects only against the last
    accepted/base plan and its final pre-render validation.
- **Dependencies**: Task 2.2.
- **Acceptance criteria**:
  - Candidate-introduced source-window, output-count, subtitle, asset, catalog,
    or unrelated-operation changes are rejected.
  - Candidate-only defects do not trigger a second call.
  - An accepted candidate has zero unresolved/new objective findings.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair_evals.py tests/test_mvp_pipeline.py -v`
- **Rollback**: Restore the prior quality-floor behavior and remove the
  authoritative-versus-candidate distinction.

### Task 2.4: Add Contingency Repair, Segment-Local Fallback, And Render Gate

- **Location**: `src/open_storyline/mvp/pipeline.py`,
  `src/open_storyline/mvp/fallbacks.py`, `src/open_storyline/mvp/render.py`,
  `src/open_storyline/mvp/compositor.py`
- **Description**:
  - Invoke the contingency batch only when a new repairable defect affects the
    authoritative plan before FFmpeg and lacks primary attempt evidence.
  - After an unsuccessful attempt, compile fallback only for affected segments.
    For unsafe crop geometry, authorize content-preserving `fit`/`letterbox`
    under engine recovery policy and emit `VISUAL_REFRAME_FALLBACK`.
  - Re-run edit preflight and the shared compositor dry-run after every accepted
    repair or fallback mutation.
  - Move or invoke renderer preflight early enough that `CompositionError`
    cannot first appear after FFmpeg work begins. Normalize composition/render
    exceptions defensively, but never bypass the per-defect attempt gate.
  - Delete or release optional assets only when the final authoritative fallback
    plan no longer references them, preserving required-asset failures.
  - If the safe minimal plan cannot pass deterministic execution validation,
    fail closed as a technical engine/source problem with explicit evidence.
- **Dependencies**: Task 2.3.
- **Acceptance criteria**:
  - Primary-known unresolved defects go directly to fallback because their LLM
    attempt is already recorded.
  - A new authoritative defect receives exactly one contingency attempt before
    fallback.
  - Provider failure still yields a valid playable fallback for repairable
    geometry.
  - FFmpeg execution is not entered before final dry-run success.
  - The fallback changes only affected segment layout and produces an auditable
    `VISUAL_REFRAME_FALLBACK` entry.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_pipeline.py tests/test_mvp_fallbacks.py tests/test_mvp_compositor.py -v`
- **Rollback**: Revert the contingency state and early render gate. Existing
  fallback artifacts remain harmless additive evidence.

### Sprint 2 Gate

- [ ] All Sprint 2 tasks complete.
- [ ] All primary/contingency/fallback branches have focused regression tests.
- [ ] Tests prove no third plan-repair call and no FFmpeg-before-dry-run path.
- [ ] Cost/latency implications are recorded without changing thresholds.
- [ ] Exactly one Sprint 2 commit is created with the proposed sprint message.
- [ ] The Sprint 2 rollback commit/reference is recorded.
- [ ] Sprint 3 has not started before this gate completes.

## Sprint 3: Durable Repair Evidence And Failed-Outcome Recovery

**Goal**: Preserve truthful repair rounds, provider attempts, validation,
checkpoints, and fallbacks in both completed and failed outcomes without leaking
private content.

**Dependencies**: Sprint 2 gate.

**Tracked scope**: `src/open_storyline/mvp/repair.py`,
`src/open_storyline/mvp/checkpoints.py`, `src/open_storyline/mvp/pipeline.py`,
`src/open_storyline/mvp/outcomes.py`, `src/open_storyline/mvp/observability.py`,
`src/open_storyline/mvp/audit.py`, `src/open_storyline/mvp/jobs.py`, and focused
audit/outcome/observability tests.

**Safe parallel work**: Outcome-reader compatibility tests and audit
sanitization tests may proceed in parallel after the repair-report v2 schema is
fixed. Pipeline failure propagation must follow that schema.

**Commit**: `fix(mvp): preserve repair evidence across failed outcomes`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_outcomes.py tests/test_mvp_observability.py tests/test_mvp_audit.py tests/test_mvp_checkpoints.py tests/test_mvp_pipeline.py -v`
- Demonstrate a synthetic failed job whose outcome truthfully reports the
  attempted primary/contingency repair, checkpoint use, fallback decision, and
  remaining non-repairable failure instead of hardcoding repair mode `off`.

**Rollback point**: The Sprint 2 commit. Reverting Sprint 3 restores prior
outcome generation; retain additive JSON documents and ensure older readers
ignore unsupported fields safely.

### Task 3.1: Version Repair Reports And Round Checkpoints

- **Location**: `src/open_storyline/mvp/repair.py`,
  `src/open_storyline/mvp/checkpoints.py`, `src/open_storyline/mvp/pipeline.py`,
  `src/open_storyline/mvp/audit.py`
- **Description**:
  - Introduce a backward-readable `repair_report.v2` carrying primary and
    contingency round identity, authoritative plan fingerprint, defect-instance
    IDs, outbound-attempt evidence, provider outcome, validation result,
    candidate disposition, and fallback authorization.
  - Continue accepting historical `repair_report.v1` in audit and quality
    feedback readers.
  - Bump the plan-repair checkpoint contract/fingerprint so v1 one-round
    checkpoints cannot be mistaken for v2 recovery evidence.
  - Reuse a checkpoint only when source, prompt, schema, registry, model
    attribution, plan fingerprint, repair round, and exact finding set match.
- **Dependencies**: Sprint 2 state machine.
- **Acceptance criteria**:
  - Historical reports remain readable and sanitized.
  - A primary checkpoint cannot satisfy a contingency defect set.
  - A failed or rejected call retains bounded attempt metadata without raw
    prompts, transcripts, provider bodies, or secrets.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair.py tests/test_mvp_checkpoints.py tests/test_mvp_audit.py -v`
- **Rollback**: Restore v1 writing while retaining v2 reader tolerance during
  the rollback window.

### Task 3.2: Preserve Actual Evidence In Failed Outcomes

- **Location**: `src/open_storyline/mvp/outcomes.py`,
  `src/open_storyline/mvp/pipeline.py`, `src/open_storyline/mvp/jobs.py`
- **Description**:
  - Pass the latest persisted repair report, rollout attribution, checkpoint
    summary, and fallback ledger into `build_failed_outcome_report`.
  - Stop hardcoding repair mode `off`, empty stages, no attempts, and all codes
    as newly introduced/not repairable.
  - Preserve actual resolved, remaining, introduced, attempted, and fallback
    dispositions for terminal and retryable failures.
  - Keep `outcome_report.v2` public readers backward compatible; add only
    bounded optional repair details unless compatibility analysis requires a
    separately reviewed version bump.
- **Dependencies**: Task 3.1.
- **Acceptance criteria**:
  - Failed outcomes attribute the actual model, prompt/schema hashes, repair
    mode, rounds, attempts, and checkpoint reuse when available.
  - A failure after successful fallback does not erase the fallback evidence.
  - Non-repairable errors remain clearly classified and fail closed.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_outcomes.py tests/test_mvp_pipeline.py -v`
- **Rollback**: Restore the previous compact failed outcome builder; do not
  delete registered evidence artifacts.

### Task 3.3: Add Invariant And Contingency Rollout Metrics

- **Location**: `src/open_storyline/mvp/observability.py`,
  `src/open_storyline/mvp/outcomes.py`, `src/open_storyline/mvp/audit.py`,
  `tests/test_mvp_observability.py`, `tests/test_mvp_outcomes.py`
- **Description**:
  - Add bounded metrics for primary calls, contingency calls, defects presented,
    fallback-after-attempt count, provider failures, candidate rejections, late
    authoritative findings, repair invariant violations, and jobs reaching the
    two-call cap.
  - Retain token, cost, latency, schema-validity, semantic-validity, checkpoint,
    new-defect, and playable-output attribution per model/prompt/schema version.
  - Store summaries only; never expose repair prompts, raw provider payloads,
    transcript text, paths, credentials, or frame data.
- **Dependencies**: Tasks 3.1 and 3.2.
- **Acceptance criteria**:
  - SLO summaries distinguish primary and contingency behavior.
  - `repair_invariant_violation_count` is zero in passing synthetic regressions.
  - Metrics remain bounded under malformed or oversized stored documents.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_observability.py tests/test_mvp_outcomes.py tests/test_mvp_audit.py -v`
- **Rollback**: Stop emitting new metrics while keeping tolerant readers for
  already persisted summaries.

### Sprint 3 Gate

- [ ] All Sprint 3 tasks complete.
- [ ] Historical v1 repair/outcome fixtures remain readable.
- [ ] Failed outcomes preserve real repair evidence and contain no private data.
- [ ] Exactly one Sprint 3 commit is created with the proposed sprint message.
- [ ] The Sprint 3 rollback commit/reference is recorded.
- [ ] Sprint 4 has not started before this gate completes.

## Sprint 4: Retained-Session Rerun API And UX

**Goal**: Let users rerun every retained completed/failed attempt whose source
and immutable prompt remain available, while keeping quality-feedback retry as
a separate evidence-backed option.

**Dependencies**: Sprint 3 gate.

**Tracked scope**: `src/open_storyline/mvp/outcomes.py`,
`src/open_storyline/mvp/prompt_versions.py`, `src/open_storyline/mvp/api.py`,
`web/static/mvp/app.js`, `web/static/mvp/views.js`, Python API/workspace tests,
and focused Playwright workspace tests.

**Safe parallel work**: Backend rerun-capability tests and accessible UI copy
may proceed in parallel after the outcome capability semantics are defined.

**Commit**: `feat(mvp): expose retained-session rerun recovery`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_outcomes.py tests/test_mvp_prompt_versions.py tests/test_mvp_app.py -v`
- `cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:desktop`
- Demonstrate distinct plain rerun and defect-feedback retry behavior without
  browser console errors.

**Rollback point**: The Sprint 3 commit. Disable
`OPENSTORYLINE_RETRY_UX_ENABLED` to hide the UI immediately, then revert Sprint
4 if backend behavior also needs removal.

### Task 4.1: Separate Rerun Capability From Quality Feedback

- **Location**: `src/open_storyline/mvp/outcomes.py`,
  `src/open_storyline/mvp/prompt_versions.py`, `src/open_storyline/mvp/api.py`
- **Description**:
  - Make `retry.supported` represent whether a retained source/prompt can create
    another run, independent of defect-feedback eligibility.
  - Keep `retry.quality_feedback_supported` evidence-based and pass
    `prior_attempt_id` only when quality feedback is explicitly selected.
  - Reuse the existing authenticated, CSRF-protected prompt-version rerun route;
    do not add unscoped job creation or weaken session ownership checks.
  - Return a safe unavailable reason when source retention, deletion, expiry, or
    immutable prompt state prevents rerun.
- **Dependencies**: Sprint 3 outcome evidence.
- **Acceptance criteria**:
  - A retained non-retryable failed attempt supports plain rerun.
  - Expired/deleted/missing source remains unavailable and fails closed.
  - Plain rerun does not fabricate prior quality feedback.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_outcomes.py tests/test_mvp_prompt_versions.py tests/test_mvp_app.py -v`
- **Rollback**: Restore current retry-support calculation and route payload
  behavior.

### Task 4.2: Present Clear, Accessible Recovery Actions

- **Location**: `web/static/mvp/app.js`, `web/static/mvp/views.js`,
  `.qa/web/tests/mvp-workspace.spec.ts`,
  `.qa/web/tests/mvp-auth-sessions.spec.ts`
- **Description**:
  - Show a plain rerun action for every retained eligible attempt.
  - Show the defect-feedback action only when
    `quality_feedback_supported=true` and clearly distinguish its behavior.
  - Preserve existing DOM hooks where possible, keyboard access, focus order,
    loading/disabled states, Spanish interface language, and mobile layout.
  - Display sanitized repair/fallback/checkpoint context without exposing
    internal payloads.
- **Dependencies**: Task 4.1.
- **Acceptance criteria**:
  - Failed attempts no longer lose all recovery actions because quality
    feedback is unsupported.
  - Buttons cannot submit duplicate reruns and report safe API errors.
  - Desktop and mobile layouts remain usable with no console errors.
- **Validation**:
  - `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:desktop`
  - Run the mobile auth test instead of desktop when the implementation changes
    mobile-specific layout behavior.
- **Rollback**: Set `OPENSTORYLINE_RETRY_UX_ENABLED=false`; revert UI changes if
  necessary while retaining backend evidence.

### Sprint 4 Gate

- [ ] All Sprint 4 tasks complete.
- [ ] Plain rerun and quality-feedback retry are independently tested.
- [ ] Authentication, CSRF, source retention, and session ownership remain
  enforced.
- [ ] Focused browser validation passes with no console errors.
- [ ] Exactly one Sprint 4 commit is created with the proposed sprint message.
- [ ] The Sprint 4 rollback commit/reference is recorded.
- [ ] Sprint 5 has not started before this gate completes.

## Sprint 5: Regression Evals, Production Gates, And Runbooks

**Goal**: Prove the invariant with private-free regressions, enforce compatible
production flags, document rollout/rollback, and prepare an operator-authorized
canary without performing it during implementation by default.

**Dependencies**: Sprint 4 gate.

**Tracked scope**: `tests/fixtures/mvp_agentic/`,
`tests/test_mvp_repair_evals.py`, `tests/test_mvp_pipeline.py`,
`tests/test_kamal_config.py`, `bin/kamal-mvp`, `config/deploy.yml`, env examples,
and MVP architecture/rollout/operator documents.

**Safe parallel work**: Documentation and release-validator tests may proceed
in parallel after final flag/invariant behavior is fixed. Full validation runs
after both are merged into the single sprint worktree.

**Commit**: `chore(mvp): gate llm-first recovery rollout`

**Demo/Validation**:

- `./bin/kamal-mvp rollout validate`
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
- `bash -n bin/kamal-mvp`
- A release reviewer can identify the primary/contingency call counts, fallback
  evidence, invariant violations, playable result, and rollback signal from
  sanitized reports alone.

**Rollback point**: The Sprint 4 commit plus the last known-compatible deployed
image/config reference. Code rollback retains additive audit evidence and does
not downgrade PostgreSQL.

### Task 5.1: Add Incident-Shaped Synthetic Evals

- **Location**: `tests/fixtures/mvp_agentic/`,
  `tests/test_mvp_repair_evals.py`, `tests/test_mvp_pipeline.py`
- **Description**:
  - Add a private-free multi-speaker/portrait geometry fixture reproducing the
    protected-target overflow class without production media, transcript,
    prompt, IDs, or provider output.
  - Cover primary repair success, primary provider failure, primary invalid
    candidate, candidate-only defect rejection, new-authoritative contingency
    repair, contingency failure with local fallback, and no-safe-baseline
    technical failure.
  - Assert exact LLM call bounds, per-defect attempt attribution, unchanged
    timing/source/output/assets/subtitles, final dry-run order, and playable
    fallback metadata.
- **Dependencies**: Sprints 1-4 behavior.
- **Acceptance criteria**:
  - The original geometry failure class cannot become product-terminal while a
    valid source-preserving fit is executable.
  - Every fallback entry maps to primary or contingency attempt evidence.
  - No test contains production-private material.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair_evals.py tests/test_mvp_pipeline.py -v`
- **Rollback**: Remove only the synthetic fixture/tests if they are invalid;
  do not weaken implementation validation to make failures pass.

### Task 5.2: Enforce Production-Compatible Flags

- **Location**: `bin/kamal-mvp`, `config/deploy.yml`,
  `.env.mvp.example`, `.env.kamal.example`, `tests/test_kamal_config.py`
- **Description**:
  - Make rollout validation reject production agentic `render` mode unless
    repair mode is `enforce`, strict `edit_plan_repair.v1` capability is
    verified and enabled, baseline fallbacks are enabled, and retry UX is
    enabled for the production profile.
  - Keep `off`/`report` valid for `off`/`shadow` development or rollback
    profiles. Emergency rollback should leave render mode rather than silently
    running non-agentic repair behavior under the production render label.
  - Validate the hard two-plan-call limit and report/reporting compatibility
    without adding a user-tunable value that could exceed it.
  - Keep committed env examples secret-free and conservative for local use;
    document the production combination rather than putting credentials or live
    values in Git.
- **Dependencies**: Task 5.1.
- **Acceptance criteria**:
  - Incompatible render/repair/fallback/schema/retry combinations fail offline
    rollout validation before deploy.
  - Shadow and rollback configurations remain explicit and testable.
  - No release check makes an unrequested live provider or deployment call.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_kamal_config.py -v`
  - `./bin/kamal-mvp rollout validate`
  - `bash -n bin/kamal-mvp`
- **Rollback**: Restore the previous rollout matrix and set agentic mode to
  `shadow`/`off` before deploying a prior image.

### Task 5.3: Update Architecture, Operations, And Rollback Guidance

- **Location**: `docs/mvp/architecture.md`,
  `docs/mvp/defect-repair.md`,
  `docs/mvp/agentic-defect-repair-rollout.md`, `docs/mvp/guia-es.md`
- **Description**:
  - Document the two-batch state machine, attempt definition, candidate-only
    behavior, final dry-run gate, segment-local fallback authority, and
    non-repairable boundaries.
  - Replace the existing "at most one plan call" rollout statement with one
    primary plus at most one contingency call and add separate metrics.
  - Preserve the existing cost/latency/playable-output thresholds until canary
    evidence supports a separately approved change.
  - Document plain rerun versus defect-feedback retry and source-retention
    limitations in English architecture/runbook and the Spanish operator guide.
  - Define rollback signals: any invariant violation, third-call attempt,
    technical blocker publication, private evidence leakage, new-defect
    regression, checkpoint mismatch, or unacceptable cost/latency/playable rate.
- **Dependencies**: Task 5.2.
- **Acceptance criteria**:
  - An operator can stage, validate, canary, disable, and roll back the feature
    without deleting evidence or downgrading the database.
  - Documentation does not claim a 99% or zero-failure result without the
    existing statistical gate.
- **Validation**:
  - Review every renamed/changed flag reference with `rg`.
  - Run `./bin/kamal-mvp rollout validate` against documented render, shadow,
    and rollback matrices.
- **Rollback**: Restore prior runbook wording only together with the compatible
  validator behavior; never document a configuration the validator rejects.

### Task 5.4: Run Final Local And Authorized Release Gates

- **Location**: Repository-wide tests and operator runbook; no product file is
  changed solely to force a passing result.
- **Description**:
  - Run focused tests first, then the full project-native Python suite.
  - Run at most smoke plus one affected authenticated browser flow by default.
  - Review Git status/diff, profile boundaries, secrets/private-data absence,
    artifact compatibility, exact skips, and rollout rollback points.
  - Only after separate production authorization: run backup/restore readiness,
    live provider gates, deploy a canary, verify exact image/database health,
    and rerun an authorized retained session without exporting its private
    evidence.
- **Dependencies**: Tasks 5.1-5.3.
- **Acceptance criteria**:
  - Local focused and full checks pass or every exact limitation/skip is
    reported.
  - The synthetic regression shows zero repair-invariant violations.
  - Production canary remains a separately authorized external action.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
  - `bash -n run.sh build_env.sh download.sh bin/kamal-mvp scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy .kamal/hooks/post-deploy`
  - `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
  - Operator-authorized only: `./bin/kamal-mvp db backup`,
    `./bin/kamal-mvp db restore-check`, provider readiness checks, deploy, and
    sanitized canary review.
- **Rollback**: If local gates fail, do not release. If an authorized canary
  fails, leave render mode, disable retry UX if necessary, deploy the last
  compatible image/config, retain additive evidence, and verify health/readiness.

### Sprint 5 Gate

- [ ] All Sprint 5 tasks complete.
- [ ] Focused and full local validation evidence is recorded with exact skips.
- [ ] Release-validator matrices and rollback procedure are verified offline.
- [ ] No live provider, production rerun, deploy, or database mutation occurred
  without separate explicit authorization.
- [ ] Exactly one Sprint 5 commit is created with the proposed sprint message.
- [ ] The Sprint 5 rollback commit/reference is recorded.
- [ ] The active-plan execution state is marked complete only after all gates
  and documentation are current.

## Testing Strategy

- **Unit**:
  - Shared geometry calculations, threshold boundaries, malformed/non-finite
    data, registry classification, repair budgets, defect-instance identity,
    mutation allowlists, candidate disposition, fallback authorization, report
    parsing, and retry capability.
- **Integration**:
  - Pipeline ordering from detection through primary/contingency repair,
    fallback compilation, compositor dry-run, artifact registration, failed
    outcome generation, checkpoint reuse, and audit ingestion.
  - Provider clients remain mocked for deterministic tests; assert call count,
    schema name, request contents, and failure attribution.
- **Regression evals**:
  - Private-free geometry overflow plus existing cooking, interview,
    multi-speaker, product, tutorial, trading, and sparse-visual fixtures.
  - Ensure repair improvements do not introduce source-window, subtitle, asset,
    catalog, or output-count regressions.
- **End-to-end/manual**:
  - Local authenticated workspace flow for failed-attempt plain rerun and
    quality-feedback retry.
  - Operator-authorized production canary only after backup, restore, provider,
    exact-image, database, health, and rollout gates.
- **Security/privacy**:
  - Session ownership, CSRF, traversal-safe artifacts, bounded report fields,
    prompt/transcript hashing, provider-body exclusion, secret scanning, and
    fail-closed unknown/non-repairable codes.
- **Performance/cost**:
  - Confirm shared geometry detection adds no provider call and negligible CPU
    overhead.
  - Measure primary and contingency latency/token/cost separately, contingency
    rate, total time to playable output, and checkpoint reuse.
  - Do not relax the existing p95 latency, cost-per-trigger, playable-output, or
    confidence thresholds without separate approval.
- **Accessibility/browser**:
  - Keyboard/focus behavior, button labels and disabled states, desktop/mobile
    layout, no console errors, and clear Spanish distinction between plain rerun
    and quality-feedback repair.
- **Database/migration**:
  - No migration is planned. Run database-backed tests with a disposable
    `openstoryline_test*` database when available and report skips when
    `TEST_DATABASE_URL` is unset.
- **Operational**:
  - Rollout-validator matrices, shell syntax, health/readiness, artifact reader
    compatibility, backup/restore readiness, canary call-count bounds, and
    explicit rollback references.

## Risks And Gotchas

| Risk | Impact | Mitigation | Validation signal |
| --- | --- | --- | --- |
| Preflight and compositor geometry drift | A repairable crop can still fail after the LLM opportunity | Use one pure shared assessment in both paths | Same boundary fixtures produce identical preflight/compositor verdicts |
| Two LLM batches become an implicit loop | Cost, latency, or stuck jobs | Hard-code primary plus one contingency; reject a third allocation | Call-count tests and `jobs_at_two_call_cap` metric |
| Candidate-only defects unnecessarily trigger another call | Wasted budget and latency | Reject candidate and compare new defects only on the authoritative plan | Candidate-only regression records one plan call |
| A fallback runs without presenting the defect to the LLM | Violates the core product invariant | Require matching defect-instance attempt evidence before fallback | Zero `repair_invariant_violation_count` |
| Model changes unrelated fields | Source, timing, subtitles, assets, or output count regress | Field mutation allowlist plus existing quality floor and full revalidation | Candidate rejected with explicit violation code |
| Engine-authorized fit weakens creative intent | Playable but visually limited output | Segment-local scope, content-preserving default, limitation ledger, optional rerun | `VISUAL_REFRAME_FALLBACK` with clip/segment and preserved contracts |
| Provider outage makes recovery terminal | Loss of availability | Count recorded outbound failure as attempt and continue to safe fallback | Provider-failure fixture yields playable fallback |
| Stale one-round checkpoint bypasses new logic | False repair evidence or skipped call | Version/fingerprint checkpoints by round, plan, schema, registry, and finding set | v1 checkpoint is ignored/recomputed |
| Failed outcome overwrites real repair history | Incident diagnosis and user recovery remain misleading | Build failure outcome from persisted report/checkpoint/fallback evidence | Synthetic failure shows actual rounds and attribution |
| Repair evidence leaks private content | Privacy/security incident | Store codes, hashes, bounded measurements, and metrics only | Audit/report tests reject raw prompt/transcript/provider data |
| FFmpeg starts before final recovery gate | Late non-recoverable failure and wasted work | Final compositor dry-run is a hard execution prerequisite | Mock asserts no render call before dry-run success |
| Plain rerun bypasses auth or retention | Cross-session access or use of deleted source | Reuse authenticated CSRF route and authoritative session/source checks | API tests for ownership, expiry, deletion, and missing source |
| Two calls breach existing rollout thresholds | Release regression despite higher recovery | Track primary/contingency separately; keep existing thresholds until approved | Canary fails closed on threshold breach |
| Older image cannot read new evidence | Rollback becomes unsafe | Additive DB-free evidence, backward readers, versioned artifacts/checkpoints | Historical v1 and new v2 fixtures both parse |

## Rollback Plan

1. **Sprint 1 rollback**: Revert the shared early-detection commit. No persisted
   state or database rollback is needed.
2. **Sprint 2 rollback**: Revert the two-batch orchestrator and restore the
   prior repair budget. Ignore new checkpoint versions by fingerprint; do not
   delete them.
3. **Sprint 3 rollback**: Stop writing v2 repair details while retaining tolerant
   readers for already stored reports. Keep audit artifacts and database rows.
4. **Sprint 4 rollback**: Set `OPENSTORYLINE_RETRY_UX_ENABLED=false` immediately,
   then revert UI/API capability changes if required.
5. **Sprint 5/runtime rollback**:
   - Leave production agentic render mode by setting the documented shadow/off
     rollback combination; do not run render mode with repair disabled.
   - Restore `OPENSTORYLINE_DELIVERY_POLICY=qa_enforced` when needed.
   - Deploy the last known-compatible image/config only after compatibility
     review and health/readiness validation.
   - Retain additive repair/outcome/checkpoint/audit evidence; do not downgrade
     PostgreSQL or delete session media as part of rollback.
6. **Rollback triggers**: Any repair-invariant violation, attempted third plan
   call, technical blocker publication, private evidence leakage, unsafe plan
   mutation, checkpoint mismatch, new-defect regression, unacceptable
   latency/cost, or lower playable-output rate.

## Execution Order

1. Read the planner execution-state reference and initialize active-plan state.
2. Implement Sprint 1 only.
3. Run and record every Sprint 1 validation command.
4. Create exactly one Sprint 1 commit and record its rollback point.
5. Start Sprint 2 only after the Sprint 1 gate passes.
6. Repeat the complete/validate/single-commit/rollback-point gate for Sprints
   2-5 in order.
7. Do not squash sprint commits during execution; any later integration/squash
   decision is a separate user-authorized Git operation.
8. Do not run production canaries, deploy, call live providers, or mutate a
   production database without separate explicit authorization.

## Completion Checklist

- [ ] Every repairable authoritative defect receives primary or contingency LLM
  attempt evidence before deterministic fallback.
- [ ] Known defects are comprehensively detected before the primary batch.
- [ ] Candidate-only defects are rejected without wasting the contingency call.
- [ ] New authoritative pre-render defects receive the one allowed contingency
  batch.
- [ ] No job can make a third plan-repair call.
- [ ] No FFmpeg execution starts before full validation and compositor dry-run.
- [ ] Geometry overflow resolves through valid LLM repair or segment-local
  content-preserving fallback instead of terminal product failure.
- [ ] Failed outcomes preserve actual repair, checkpoint, attribution, and
  fallback evidence.
- [ ] Retained failed sessions expose plain rerun independently of quality
  feedback.
- [ ] Non-repairable security, source-integrity, executable, auth, database, and
  infrastructure failures continue to fail closed.
- [ ] Synthetic fixtures contain no private production evidence.
- [ ] Every sprint passes its exact validation gate.
- [ ] Every sprint has exactly one sprint-specific commit and recorded rollback
  point.
- [ ] Final local integration, browser, security/privacy, and rollout-validator
  checks pass with exact skips documented.
- [ ] Production rollout remains separately authorized and has a practical
  rollback reference before canary execution.
