# Plan: Centralized Agentic Defect Registry And Bounded Repair Loop

**Generated**: 2026-07-21
**Status**: Sprints 1-6 and Sprint 7 production execution complete; core
same-session agentic proof passes on the final image, while the trustworthy-MVP
release claim remains operator-gated by the explicit residuals below; pull
request pending
**Estimated Complexity**: High

## Overview

The remote MVP already validates model JSON with Pydantic and performs bounded
repair in a few places, but it does not currently use provider-enforced strict
JSON Schema. `NineRouterClient.complete_json()` sends
`response_format={"type":"json_object"}`, extracts the first JSON object from
free-form content, and leaves schema and business-rule validation to downstream
code. The edit planner may then make one additional repair call for each invalid
clip, while clip-local visual coverage has a separate higher-density evidence
and replan path.

Defect codes are also distributed across planning, preflight, visual coverage,
fallback compilation, captions, creative QA, frame QA, promotion, activity, and
outcomes. That makes it difficult to answer consistently whether a code is
LLM-repairable, deterministically repairable, transient, advisory, or terminal.

This plan establishes one version-controlled defect registry, upgrades every
remote-MVP JSON-producing 9Router boundary, including the runtime-optional FFMPEGA
effects planner, to a proven strict JSON Schema contract, and introduces two
causally separate bounded repair stages before final render: at most one visual
evidence repair and at most one job-level edit-plan repair. The backend, not the
model, decides whether defects were resolved. If repair does not validate, the
original real defect codes remain auditable and the smallest deterministic
fallback is applied. A technically valid video is always published with
truthful limitations even when strict creative QA remains blocked.

The quality guarantee is intentionally precise: a fallback may omit or simplify
the defective operation, but it must preserve the quality of unaffected clips,
source windows, audio, captions, successful effects, selected catalog style,
and already validated assets. No plan can honestly promise that a fallback
preserves an unavailable enhancement itself.

Sprint 7 extends the completed rollout by production-enabling semantic video QA
and typed FFMPEGA finishing, then debugging, fixing, and improving every affected
remote-MVP stage required to make the current uploaded session video succeed
reliably with the latest prompt. Latency, token usage, and provider cost remain
observable capacity signals rather than quality rejection thresholds while
calls stay bounded, jobs complete inside operational timeouts, and the measured
quality improvement is positive. The trustworthy-MVP gate combines deterministic
media evidence, model semantic evidence, direct output inspection, and repeatable
same-session behavior; the model is never the sole judge of its own work.

## Current Verified Context

- The active branch is `feat/agentic-defect-repair-registry`, created directly
  from the merged fork `main` at
  `dae7366461316c08344601def9a3d22bb6e3b97b`.
- GitHub authentication was verified as `loldlm1`.
- Pull request #11 was merged into `loldlm1/FireRed-OpenStoryline:main` on
  2026-07-21 with normal merge commit
  `dae7366461316c08344601def9a3d22bb6e3b97b`. Its final head was
  `ef28df6912f9d6bac2db8047a05e275bbe5365d5`.
- PR #11 had zero GitHub check runs and zero formal reviews. The user's explicit
  approval was the merge authorization; the PR body preserves the completed
  local, connected-PostgreSQL, browser, deployment, and production canary
  evidence.
- The predecessor plan was removed in
  `ef28df6 docs: archive agentic reliability plan` before the merge.
- The completed reliability rollout is preserved in
  `docs/mvp/implementation-history.md` and `docs/mvp/creative-catalog.md`.
- The current approved 9Router text/vision route remains
  `cx/gpt-5.6-sol`. This plan does not change the model or provider.
- The approved plan bootstrap and all six sprint commits are present on the
  feature branch. Two narrow post-Sprint-6 fixes preserve the release gate while
  routing allowlisted strict schemas through the provider-compatible Responses
  transport.

No provider call, deployment, production database mutation, or production media
mutation was performed while closing PR #11 and preparing this branch. The user
later authorized the Sprint 6 deployment and private production canaries
recorded below.

## Scope

- **In scope**:
  - Build on the merged PR #11 reliability, checkpoint, fallback, catalog, and
    retry contracts without reopening or relabeling its historical evidence.
  - Create a central registry for every code that can enter a job outcome,
    promotion decision, fallback ledger, retry decision, repair decision, audit
    summary, public activity failure, or workspace presentation. Unrelated
    admin/config/API validation codes remain explicitly outside Registry v1.
  - Record category, severity, repair strategy, repair stage, evidence
    requirements, safe fallback, retryability, promotion behavior, and Spanish
    and English user-safe presentation for each registered code.
  - Prove whether the deployed 9Router route supports Chat Completions
    `response_format.type=json_schema` with `strict=true` before enabling it.
  - Use stable Pydantic-backed wire schemas at every remote-MVP JSON-producing
    9Router boundary: short selection, visual understanding, agentic edit
    planning, repair planning, optional semantic QA, and FFMPEGA effect planning.
  - Replace FFMPEGA's open-ended `params` object with stable effect-specific
    contracts derived from an authoritative, pinned parameter inventory.
  - Keep application-side Pydantic and deterministic semantic validation
    authoritative after provider schema validation.
  - Replace multiple uncoordinated semantic repairs with at most one visual
    evidence repair plus at most one job-level pre-render edit-plan repair. The
    plan repair batches all currently known eligible defects and affected clips
    into one bounded request; FFMPEGA receives no independent repair attempt.
  - Let the edit-plan repair model make context-aware corrections for eligible
    numeric, timing, geometry, capability, budget, catalog, reference, and
    creative-intent defects before deterministic fallback is applied.
  - Add objective pre-render predictive QA and bounded heuristic creative-risk
    signals. Objective defects may trigger repair; advisory-only signals may
    accompany an already-triggered repair but never trigger a provider call by
    themselves or become technical blockers.
  - Preserve the exact original defect codes if repair fails; add repair metadata
    without replacing the real cause with only a generic exhaustion code.
  - Apply the smallest safe deterministic fallback to remaining creative defects
    and keep all unaffected creative work.
  - Keep technical media defects, provider failures, source failures, security
    failures, and infrastructure failures outside LLM repair.
  - Add audit artifacts, metrics, API/UI presentation, tests, evals, feature
    flags, canary gates, and rollback controls.
  - Add a bounded agent-friendly `audit defects` command with code, strategy,
    disposition, stage, and time filters without introducing a migration.
  - Production-enable strict `semantic_qa.v1` and both strict FFMPEGA schemas,
    validate native fallback, and prove the enabled path with authorized,
    sanitized production evidence.
  - Separate analysis-unavailable limitations from genuine source constraints
    and creative shortcomings so healthy production dependencies cannot hide
    behind a generic technical-pass limitation.
  - Compare baseline and complete agentic outputs for the current uploaded
    session video, using the latest prompt and at most two targeted prompt
    variants only when additional residual-risk evidence is required.
- **Out of scope**:
  - The full local LangChain/MCP profile and `.storyline/skills/` behavior.
  - Multi-agent repair, open-ended autonomous loops, more than one semantic
    repair call per eligible repair stage, or any same-job post-render repair and
    rerender loop.
  - Asking the LLM to repair encoded pixels, audio streams, codecs, FFmpeg tool
    availability, source corruption, authentication, authorization, or storage.
  - Paid assets, runtime catalog downloads, new languages beyond Spanish and
    English, or new model/provider selection.
  - Unbounded agent loops, unlimited provider calls, removal of job/provider
    timeouts, or treating higher latency/token usage as proof of higher quality.
  - A creative-quality claim based only on semantic QA from the same model that
    generated or repaired the plan.
  - Rewriting historical attempts or silently changing their original labels.
  - Runtime database CRUD for defect definitions. The registry remains reviewed,
    version-controlled application policy.
  - Automatic production deployment or live/paid provider calls during ordinary
    implementation validation.
- **Fixed decisions**:
  - Strict JSON Schema constrains wire shape; it does not replace business-rule,
    evidence, safety, or render validation.
  - All fields in provider wire schemas are required; optional values use an
    explicit nullable type. Every object forbids additional properties.
  - Schemas are stable and contain no prompt text, transcript text, media data,
    frame bytes, credentials, user identifiers, or per-job private data.
    Repair requests may transiently include the already-authorized immutable
    editing prompt and bounded clip-local transcript excerpts sent to the same
    configured 9Router route; those values are never stored in repair reports,
    checkpoints, audit summaries, logs, or schema definitions.
  - Dynamic catalog IDs and evidence IDs remain prompt/context data validated by
    the backend, not dynamically injected into per-job JSON schemas.
  - Visual understanding may receive one semantic repair while the referenced
    images and evidence are available. Edit planning may separately receive one
    job-level semantic repair containing multiple registered codes and affected
    clips. HTTP transport retries remain separately bounded and do not count as
    semantic repair rounds.
  - FFMPEGA uses strict effect-specific schemas and deterministic validation but
    receives no semantic repair call. Invalid or failed optional finishing work
    publishes the native FFmpeg render with `EFFECT_OMITTED`.
  - The model returns only a corrected candidate plan. It does not return a
    trusted `resolved=true` decision or authorize fallback/promotion.
  - Deterministic revalidation computes resolved, remaining, and newly introduced
    codes after repair.
  - For registry-driven repair, `off` bypasses eligibility and makes no repair
    call, `report` evaluates and records the bounded call that would have been
    made without calling the model, and `enforce` makes the allowed stage-bounded
    call when an objective repairable defect is eligible. Advisory-only
    predictive findings never trigger a model call; they are secondary
    suggestions only when an objective defect has already triggered the
    plan-repair batch.
  - Post-render creative defects do not trigger another same-job LLM/render loop
    in the baseline path. A technically valid candidate is published with typed
    limitations and can be improved through the existing retry/version workflow.
  - Technical blockers continue to fail closed and cannot be reclassified as
    creative limitations by the model. Strict creative QA remains an immutable
    evaluation verdict, but delivery is independently technical-pass guaranteed:
    every candidate without a technical blocker is registered and downloadable
    with truthful limitations.
  - Historical `outcome_report.v1` and `quality_feedback.v1` remain readable.
  - No database migration is expected: the repair report can use existing
    registered JSON artifacts, audit ingestion, outcome JSON, and flexible
    checkpoint stage names. The bounded `audit defects` command is the initial
    agentic query surface. A migration requires separate justification only if
    measured query volume or correctness cannot be served by these contracts.
  - Feature flags default to the current behavior until schema capability,
    regression, and canary gates pass.
- **Assumptions**:
  - 9Router is OpenAI-compatible at the endpoint level, but strict JSON Schema
    forwarding is unknown until the repository's safe capability probe passes.
  - The current provider and model limits can accept a stable per-job repair
    schema covering no more than the existing eight output clips.
  - Authoritative FFMPEGA parameter definitions can be located, pinned, and
    represented within the provider-supported strict JSON Schema subset. If not,
    Sprint 2 stops before inventing parameter contracts.
  - The existing immutable prompt-version, checkpoint, audit, promotion, and
    retry contracts remain authoritative.
  - The user authorized production deployment and same-session canary validation
    before PR creation on 2026-07-21; manual approval remains reserved for the
    resulting PR.

## Recommended Repairability Classification

The registry must encode a strategy, not merely an `llm_repairable` boolean.
Eligibility is conditional on authoritative evidence and available capabilities.

| Strategy | Initial code families | Required behavior |
| --- | --- | --- |
| `llm_visual_repair` | Schema-valid visual-understanding responses with invalid frame/region/track/scene references, timing, role consistency, or evidence relationships | Make at most one complete replacement call while the original bounded images are available; revalidate locally; fail or use an already-proven deterministic evidence fallback if still invalid. |
| `llm_plan_repair` | `EDIT_PLAN_INVALID`, intent mismatch, plan capability/budget violations, unknown or mismatched catalog selections, invalid clip/evidence/region/track references, exact timing/geometry conflicts, unsupported optional transitions/effects, caption safe-zone problems, duplicate/low-opacity/out-of-zone overlays, equivalent preflight findings, and clip-local crop coverage defects after evidence refresh | Include all affected codes in one pre-render repair batch so the model may choose context-aware valid values or substitutions; revalidate deterministically; apply the smallest registered fallback only for remaining codes. |
| `deterministic_fallback` | Any eligible creative defect that remains after the single plan-repair round, lacks required evidence, cannot be achieved with installed capabilities, or arises from optional FFMPEGA planning/execution | Preserve unaffected work and apply the smallest exact safe correction, omission, or baseline operation; record requested and executed values without another model call. |
| `conditional_llm_or_fallback` | `CREATIVE_INTENT_UNMET`, `ACTIVE_PICTURE_TOO_SMALL`, `PLANNED_OPERATIONS_MISSING`, `REQUESTED_ASSETS_MISSING`, `UNREQUESTED_ASSETS_USED`, and `UNEXPLAINED_FALLBACK` | Use LLM repair only when the missing intent is achievable with installed capabilities and evidence before render; otherwise use a typed fallback. Post-render occurrence remains a limitation. |
| `provider_retry` | 9Router/Mistral/Pexels/image request failures, rate limits, timeouts, and unavailable dependencies | Use existing bounded transport/provider retry and checkpoints; never ask an LLM to explain or repair another provider's failure. |
| `renderer_retry_or_fail` | Missing audio, bad codec/dimensions/duration, black/frozen/collapsed frames, catastrophic reference quality, FFmpeg/FFprobe failures, and unavailable technical QA | Use deterministic re-render or minimal baseline only where already proven safe; otherwise fail closed. No LLM repair. |
| `advisory` | Blur/blockiness review, inactive hook, long visual hold, attention gap, subtitle cadence review, semantic review unavailable/requested, and bounded-analysis review notes | Preserve as audit evidence. A deterministic pre-render predictor may attach an advisory to an already-triggered plan-repair request, but an advisory alone never triggers a call or blocks technical-pass delivery. |
| `terminal` | Invalid/changed/expired source, path/integrity/security violations, invalid deployment configuration, and unrecoverable authoritative-state corruption | Fail closed with the exact registered code and no model call. |

### Initial LLM-Repairable Code Set

The first registry version should classify the following current codes as
LLM-repairable when their required evidence is available:

- Schema and intent: `EDIT_PLAN_INVALID`, `EDIT_PLAN_INTENT_MISMATCH`.
- Capability and budget:
  `EDIT_PLAN_CAPABILITY_UNAVAILABLE`,
  `EDIT_PLAN_CAPABILITY_UNDECLARED`,
  `EDIT_PLAN_ASSET_BUDGET_EXCEEDED`,
  `EDIT_PLAN_GENERATED_ASSET_BUDGET_EXCEEDED`,
  `EDIT_PLAN_STOCK_ASSET_BUDGET_EXCEEDED`,
  `EDIT_PLAN_SEGMENT_BUDGET_EXCEEDED`, and
  `EDIT_PLAN_OVERLAY_BUDGET_EXCEEDED`.
- Catalog selection:
  `EDIT_PLAN_CATALOG_ID_UNKNOWN`,
  `EDIT_PLAN_CATALOG_KIND_INVALID`,
  `EDIT_PLAN_CATALOG_STYLE_MISMATCH`, and
  `EDIT_PLAN_CATALOG_TRANSITION_MISMATCH`.
- Context references:
  `EDIT_PLAN_CLIP_BOUNDS_INVALID`,
  `EDIT_PLAN_CLIP_MISMATCH`,
  `EDIT_PLAN_EVIDENCE_UNKNOWN`,
  `EDIT_PLAN_REGION_UNKNOWN`,
  `EDIT_PLAN_REGION_OUTSIDE_CLIP`,
  `EDIT_PLAN_TRACK_UNKNOWN`, and
  `EDIT_PLAN_TRACK_OUTSIDE_CLIP`.
- Equivalent preflight findings when caused by model output:
  `CAPABILITY_UNAVAILABLE`,
  `CAPABILITY_UNDECLARED`,
  `SEGMENT_BUDGET_EXCEEDED`,
  `OVERLAY_BUDGET_EXCEEDED`,
  `ASSET_BUDGET_EXCEEDED`,
  `FULL_FRAME_FALLBACK_UNAPPROVED`,
  `REGION_REFERENCE_UNKNOWN`,
  `REGION_REFERENCE_OUTSIDE_CLIP`,
  `TRACK_REFERENCE_UNKNOWN`,
  `TRACK_REFERENCE_OUTSIDE_CLIP`,
  `EVIDENCE_REFERENCE_UNKNOWN`,
  `TRANSITION_TOO_LONG`,
  `OVERLAY_TRANSITION_TOO_LONG`, and
  `SUBTITLE_SAFE_ZONE_CONFLICT`.
- Crop evidence after the existing bounded higher-density analysis:
  `CROP_VISUAL_OBSERVATION_MISSING`,
  `CROP_VISUAL_OBSERVATIONS_INSUFFICIENT`,
  `CROP_VISUAL_TEMPORAL_COVERAGE_LOW`, and
  `CROP_VISUAL_GAP_TOO_LARGE`.
- Conditional creative conformance:
  `CREATIVE_INTENT_UNMET`,
  `PLANNED_OPERATIONS_MISSING`,
  `REQUESTED_ASSETS_MISSING`,
  `UNREQUESTED_ASSETS_USED`, and
  `UNEXPLAINED_FALLBACK` only when detected before final render and when the
  requested behavior is possible with installed capabilities.

The following are specifically not LLM-repairable: catalog unavailable/version
or configuration failures, provider/media unavailable codes, source/security
errors, caption measurement tool errors, asset byte/visibility failures,
technical promotion blockers, and any unknown code.

## Named Resources

- **Project instructions**:
  - `AGENTS.md`
  - `docs/agent-engineering.md`
  - `docs/mvp/architecture.md`
- **Completed predecessor records**:
  - PR #11 merge commit `dae7366461316c08344601def9a3d22bb6e3b97b`.
  - `docs/mvp/implementation-history.md` - preserve as historical evidence.
  - `docs/mvp/creative-catalog.md` - preserve as the catalog operations guide.
- **New implementation files**:
  - `src/open_storyline/mvp/defects.py` - registry, enums, definitions,
    compatibility normalization, and presentation metadata.
  - `src/open_storyline/mvp/structured_outputs.py` - stable wire schemas,
    schema names/versions/hashes, and provider-safe schema validation.
  - `src/open_storyline/mvp/ffmpega_contracts.py` - pinned effect parameter
    inventory, effect-specific Pydantic models, agentic/full allowlists, and
    shared local/provider validation.
  - `src/open_storyline/mvp/repair.py` - eligibility, evidence compaction,
    stage-bounded repair budgets, batching, deterministic resolution comparison,
    and repair report.
  - `tests/test_mvp_defects.py`
  - `tests/test_mvp_structured_outputs.py`
  - `tests/test_mvp_ffmpega_contracts.py`
  - `tests/test_mvp_repair.py`
  - `docs/mvp/defect-repair.md`
- **Existing implementation files expected to change**:
  - `src/open_storyline/config.py`
  - `src/open_storyline/mvp/ninerouter.py`
  - `src/open_storyline/mvp/shorts.py`
  - `src/open_storyline/mvp/visual_understanding.py`
  - `src/open_storyline/mvp/edit_plan.py`
  - `src/open_storyline/mvp/preflight.py`
  - `src/open_storyline/mvp/visual_coverage.py`
  - `src/open_storyline/mvp/fallbacks.py`
  - `src/open_storyline/mvp/subtitles.py`
  - `src/open_storyline/mvp/creative_qa.py`
  - `src/open_storyline/mvp/frame_quality.py`
  - `src/open_storyline/mvp/promotion.py`
  - `src/open_storyline/mvp/outcomes.py`
  - `src/open_storyline/mvp/observability.py`
  - `src/open_storyline/mvp/activity.py`
  - `src/open_storyline/mvp/checkpoints.py`
  - `src/open_storyline/mvp/pipeline.py`
  - `src/open_storyline/mvp/jobs.py`
  - `src/open_storyline/mvp/prompt_versions.py`
  - `src/open_storyline/mvp/prompts.py`
  - `src/open_storyline/mvp/admin.py`
  - `src/open_storyline/mvp/ffmpega.py`
  - `scripts/qa_ninerouter.py`
  - `bin/kamal-mvp`
  - `web/static/mvp/messages.js`
  - `web/static/mvp/views.js`
  - `config.toml`
  - `.env.mvp.example`
  - `.env.kamal.example`
  - `config/deploy.yml`
  - `docs/mvp/architecture.md`
  - `docs/agent-engineering.md`
  - `docs/mvp/implementation-history.md`
- **Existing tests expected to change**:
  - `tests/test_ninerouter.py`
  - `tests/test_qa_ninerouter.py`
  - `tests/test_shorts.py`
  - `tests/test_mvp_visual_understanding.py`
  - `tests/test_mvp_edit_plan.py`
  - `tests/test_mvp_pipeline.py`
  - `tests/test_mvp_fallbacks.py`
  - `tests/test_mvp_creative_qa.py`
  - `tests/test_mvp_frame_quality.py`
  - `tests/test_mvp_outcomes.py`
  - `tests/test_mvp_observability.py`
  - `tests/test_mvp_prompt_versions.py`
  - `tests/test_mvp_activity.py`
  - `tests/test_ffmpega.py`
  - `tests/test_mvp_audit.py`
  - `tests/test_kamal_config.py`
  - `.qa/web/tests/mvp-workspace.spec.ts`
- **External documentation**:
  - OpenAI Structured Outputs:
    `https://developers.openai.com/api/docs/guides/structured-outputs`
  - OpenAI function strict-mode schema requirements:
    `https://developers.openai.com/api/docs/guides/function-calling#strict-mode`
  - Pydantic JSON Schema generation:
    `https://docs.pydantic.dev/latest/concepts/json_schema/`
  - The repository contains no pinned public 9Router strict-schema contract.
    `scripts/qa_ninerouter.py` and an isolated non-private capability probe are
    authoritative for this deployment. OpenRouter is comparison context only
    and is not an implementation dependency.
- **Operational resources**:
  - Existing `job_stage_checkpoints` table; a `plan_repair` stage can be added
    without schema changes because checkpoint stages are bounded strings.
  - Existing JSON artifact registration and audit ingestion.
  - Proposed flags:
    `OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_object|json_schema`,
    `OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES=<allowlisted names>`,
    `OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off|report|enforce`, and
    `OPENSTORYLINE_DELIVERY_POLICY=qa_enforced|technical_pass_guaranteed`.
  - Existing kill switches for checkpoints, baseline fallbacks, catalog
    planning, completion policy, limited promotion, and retry UX.
  - Existing bounded audit CLI extended with `audit defects`; no new database
    table or migration in the initial implementation.

## Completed Prerequisites And PR #11 Closure

The following prerequisites were completed before Sprint 1:

1. Verified GitHub authentication as `loldlm1`, fork destination
   `loldlm1/FireRed-OpenStoryline`, base `main`, and the expected PR #11 head.
2. Verified inbound references, removed only the completed predecessor plan, and
   preserved implementation history, catalog guidance, and architecture docs.
3. Committed the cleanup as `ef28df6 docs: archive agentic reliability plan`
   and pushed the exact head.
4. Re-read PR #11 at the pushed head, confirmed `mergeable_state=clean`, and
   merged it normally under the user's explicit approval.
5. Re-read PR #11 with `merged=true` and verified merge SHA
   `dae7366461316c08344601def9a3d22bb6e3b97b` on remote `main`.
6. Fast-forwarded local `main` and created
   `feat/agentic-defect-repair-registry` from that merge commit.
7. Confirmed this planning file is the only intended branch change and no Sprint
   implementation has started.

## Fresh Session Bootstrap

A new Codex session executing this plan should read, in order:

1. `AGENTS.md`.
2. `agentic-defect-registry-repair-loop-plan.md` in full.
3. `docs/agent-engineering.md` and `docs/mvp/architecture.md`.
4. `src/open_storyline/mvp/ninerouter.py`, `edit_plan.py`, `preflight.py`,
   `visual_understanding.py`, `visual_coverage.py`, `fallbacks.py`, `ffmpega.py`,
   `promotion.py`, `outcomes.py`, `observability.py`, and `pipeline.py`.
5. The focused tests named under each sprint.
6. Present the complete ordered sprint-title list from the pre-execution gate to
   the user and obtain explicit approval before initializing execution state or
   editing implementation files.
7. `/home/loldlm/.codex/skills/planner/references/execution-state.md`, when the
   `$planner` skill is exposed in that session, then initialize active-plan
   execution state before Sprint 1.
8. Current Git status, branch tracking, merge-base, and remote routing; verify
   the branch still descends from `dae7366` before making edits.

## Pre-Execution User Review Gate

Before any sprint execution, implementation edit, execution-state initialization,
provider probe, or sprint commit, the implementer must present this exact ordered
title list to the user:

1. Sprint 1: Centralize Outcome-Facing Defect Policy And Presentation.
2. Sprint 2: Enforce Progressive Strict Structured Outputs And Typed Effects.
3. Sprint 3: Define Stage-Bounded Evidence-Driven Repair Policy And Contracts.
4. Sprint 4: Execute Visual And Plan Repair With Predictive QA And Safe Fallback.
5. Sprint 5: Audit Repair Outcomes And Publish Technical-Pass Candidates.
6. Sprint 6: Run Evals, Operational Gates, And Staged Rollout.

The user must explicitly approve the ordered titles before Sprint 1 starts. If a
sprint title, goal, dependency boundary, or commit scope changes later, stop and
present the complete revised list again before continuing. This gate is separate
from production/provider authorization and does not authorize deployment.

## Sprint 1: Centralize Outcome-Facing Defect Policy And Presentation

**Goal**: Every remote-MVP code that can affect a user-visible attempt or repair
decision resolves through one typed, versioned registry with no change to
current runtime outcomes.

**Dependencies**: PR #11 closure complete; new branch created from merged main.

**Tracked scope**: `agentic-defect-registry-repair-loop-plan.md`,
`src/open_storyline/mvp/defects.py`, current code emitters, outcome/activity
presentation, focused tests, and `docs/mvp/defect-repair.md`.

**Commit**: `feat: centralize agentic defect definitions`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_defects.py tests/test_mvp_outcomes.py tests/test_mvp_activity.py -v`
- Run a registry inventory check that reports every outcome/promotion/fallback/
  retry/repair/audit/activity/workspace code as registered and lists intentionally
  excluded admin/config/API validation codes with a stable exclusion reason.
- Confirm historical `outcome_report.v1` fixtures still summarize identically.

**Rollback point**: Revert the Sprint 1 commit. No database or provider behavior
changes are introduced in this sprint.

### Task 1.1: Define The Registry Contract

- **Location**: `src/open_storyline/mvp/defects.py`
- **Description**:
  - Add `DEFECT_REGISTRY_VERSION = "defect_registry.v1"`.
  - Define bounded enums for domain, severity, visibility, repair strategy,
    repair phase, retry action, and promotion class.
  - Define an immutable `DefectDefinition` containing at minimum: code, domain,
    default severity, public visibility, repair strategy, repair phase,
    evidence requirements, safe fallback code, retryability, promotion class,
    English/Spanish title and description, and legacy aliases.
  - Add normalization for current lowercase QA codes and historical codes without
    mutating stored history.
  - Add an explicit unknown-code definition that fails closed and never becomes
    LLM-repairable.
- **Dependencies**: Current code inventory.
- **Acceptance criteria**:
  - Every current code that can affect a job outcome, promotion, fallback,
    retry, repair, audit summary, public activity failure, or workspace state has
    a registered definition.
  - Excluded administrative, configuration, and request-validation codes are
    inventoried but do not require bilingual presentation or repair metadata.
  - Definitions contain no secrets, prompts, provider bodies, or private data.
  - Duplicate codes and aliases fail at import/test time.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_defects.py -v`
- **Rollback**: Remove the new registry module and restore literal-code imports.

This planning artifact is committed as branch bootstrap before Sprint 1. The
Sprint 1 commit must contain only Sprint 1 implementation and supporting tests.

### Task 1.2: Route Existing Outcome Decisions Through The Registry

- **Location**: `outcomes.py`, `promotion.py`, `fallbacks.py`, `activity.py`,
  `observability.py`, and code-emitting QA/preflight modules.
- **Description**:
  - Preserve exact public code strings while replacing duplicated category,
    retryability, and presentation decisions with registry lookups.
  - Keep technical promotion allowlists fail-closed; registry metadata must not
    allow a creative code to override an explicitly technical detector.
  - Preserve existing `retryable_error()` suffix compatibility for codes outside
    Registry v1 and historical records until a later explicitly scoped expansion.
- **Dependencies**: Task 1.1.
- **Acceptance criteria**:
  - Existing completed, limited, retryable, and terminal fixtures keep their
    grades and exact codes.
  - Unknown codes are terminal/non-LLM-repairable by default.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_outcomes.py tests/test_mvp_frame_quality.py tests/test_mvp_fallbacks.py -v`
- **Rollback**: Restore local allowlists while leaving stored data unchanged.

### Task 1.3: Centralize Bilingual Defect Presentation

- **Location**: `defects.py`, `outcomes.py`, `web/static/mvp/messages.js`,
  `web/static/mvp/views.js`, `docs/mvp/defect-repair.md`.
- **Description**:
  - Embed bounded registry presentation metadata in outcome summaries so the UI
    does not invent human labels by lowercasing raw codes.
  - Keep raw code visible for audit and support.
  - Document categories, repair semantics, and the rule that historical outcomes
    are not reclassified in storage.
- **Dependencies**: Tasks 1.1-1.2.
- **Acceptance criteria**:
  - Spanish and English labels exist for every Registry v1 user-visible code.
  - Missing presentation metadata fails focused tests.
  - Browser output remains safe when encountering an unknown historical code.
- **Validation**:
  - `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
- **Rollback**: UI falls back to raw safe code labels; backend registry remains.

### Sprint 1 Gate

- [x] All Sprint 1 tasks complete.
- [x] Sprint 1 validation passes and evidence is recorded.
- [x] Residual risks are documented.
- [x] Exactly one Sprint 1 commit is created with the proposed sprint message.
- [x] The rollback point is recorded.
- [x] Sprint 2 has not started before this gate completes.

**Recorded evidence**: 37 focused tests passed with five expected database
skips; 29 connected PostgreSQL tests passed; JavaScript syntax checks and the
single-worker Chromium smoke passed. Optional FFMPEGA failures remain a typed
`EFFECT_OMITTED` fallback, while measured caption and asset findings remain
post-render limitations. Roll back by reverting `0c812f2`; no migration or
provider-mode change was introduced.

## Sprint 2: Enforce Progressive Strict Structured Outputs And Typed Effects

**Goal**: All remote-MVP 9Router JSON boundaries can use stable provider-enforced
strict schemas, with deterministic local validation, progressive per-boundary
activation, typed FFMPEGA effects, and a deploy-time capability gate.

**Dependencies**: Sprint 1 gate complete.

**Tracked scope**: `structured_outputs.py`, `ffmpega_contracts.py`, `ninerouter.py`,
all remote JSON model callers, provider QA script, configuration, documentation,
and tests.

**Commit**: `feat: enforce strict agentic and effect schemas`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_structured_outputs.py tests/test_mvp_ffmpega_contracts.py tests/test_ninerouter.py tests/test_qa_ninerouter.py tests/test_shorts.py tests/test_mvp_visual_understanding.py tests/test_mvp_edit_plan.py tests/test_ffmpega.py -v`
- Run the safe local/mock strict-schema capability cases for success, refusal,
  incomplete output, unsupported schema, additional properties, and invalid
  business semantics.
- Do not run live inference unless separately authorized.

**Rollback point**: Set
`OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_object` and revert the Sprint 2 commit
if necessary. Domain validators remain unchanged.

### Task 2.1: Create Stable Provider Wire Schemas

- **Location**: `src/open_storyline/mvp/structured_outputs.py` and existing
  Pydantic domain models.
- **Description**:
  - Define dedicated provider-response wire models for shorts, visual
    understanding, per-clip edit planning, edit-plan repair, semantic QA,
    agentic FFMPEGA finishing, and the full deterministic FFMPEGA effects path.
  - Keep wire schemas separate from domain models where defaults, custom
    validators, or cross-field rules cannot be represented by the provider's
    supported JSON Schema subset.
  - Require every property and use explicit `null` unions for optional values.
  - Set `additionalProperties=false` recursively and reject unsupported schema
    constructs in a startup/test validator.
  - Assign stable schema names, versions, and SHA-256 fingerprints.
  - Validate the configured boundary allowlist against registered stable names;
    unknown names fail startup and never silently enable permissive parsing.
- **Dependencies**: Sprint 1 registry categories.
- **Acceptance criteria**:
  - Schema generation is deterministic across runs.
  - No schema contains private or per-job values.
  - A snapshot change requires an intentional schema-version change.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_structured_outputs.py -v`
- **Rollback**: Remove wire schema use and retain domain validation.

### Task 2.2: Add A Strict NineRouter Client Boundary

- **Location**: `src/open_storyline/mvp/ninerouter.py`.
- **Description**:
  - Add a narrow `complete_structured()` API accepting only registered schema
    names/models, not arbitrary caller-provided schemas.
  - Send strict boundaries through the Responses-compatible
    `text.format.type=json_schema` transport with schema name, `strict=true`,
    `store=false`, and the stable schema. Keep Chat Completions `json_object` as
    the explicit rollback transport for unallowlisted boundaries.
  - In strict mode, stop accepting fenced JSON or unrelated surrounding prose.
  - Distinguish transport retries from semantic repair attempts.
  - Handle refusal, empty content, non-success finish reasons, truncation,
    provider schema rejection, malformed response envelopes, and schema mismatch
    with registered safe error codes.
  - Always run local Pydantic validation after provider success.
- **Dependencies**: Task 2.1.
- **Acceptance criteria**:
  - `json_object` and `json_schema` modes are explicit and testable.
  - `OPENSTORYLINE_STRUCTURED_OUTPUT_BOUNDARIES` activates only registered
    boundaries and supports the staged order defined by Sprint 6.
  - Strict mode never silently downgrades to permissive parsing.
  - Error details remain secret-safe and provider bodies are not persisted.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_ninerouter.py -v`
- **Rollback**: Disable strict mode with the configuration flag.

### Task 2.3: Convert Core Remote MVP JSON Callers

- **Location**: `shorts.py`, `visual_understanding.py`, `edit_plan.py`,
  and `creative_qa.py`.
- **Description**:
  - Move each caller to its registered wire schema.
  - Preserve current prompt meaning, model, reasoning effort, provider route,
    local domain validation, and private-data boundaries.
  - Bump prompt/schema versions only where output contracts actually change.
- **Dependencies**: Tasks 2.1-2.2.
- **Acceptance criteria**:
  - Every core 9Router JSON call names a strict schema when its boundary is
    allowlisted in strict mode.
  - No caller assumes provider schema adherence is sufficient for business rules.
- **Validation**:
  - Focused caller tests listed in Sprint 2 validation.
- **Rollback**: Remove the affected boundary from the allowlist or restore global
  `json_object` mode; never perform an implicit runtime downgrade.

### Task 2.4: Pin And Type FFMPEGA Effect Contracts

- **Location**: `src/open_storyline/mvp/ffmpega_contracts.py`,
  `src/open_storyline/mvp/ffmpega.py`, `tests/test_mvp_ffmpega_contracts.py`, and
  `tests/test_ffmpega.py`.
- **Description**:
  - Locate and record the authoritative parameter names, types, required values,
    ranges, enums, and incompatible combinations for all 26 currently allowed
    deterministic skills. Stop rather than inventing contracts if the installed
    FFMPEGA boundary cannot provide authoritative definitions.
  - Define two stable schemas: `ffmpega_agentic_finishing.v1` for the 21 current
    `AGENTIC_FINISHING_SKILLS`, and `ffmpega_deterministic_effects.v1` for the
    complete `DETERMINISTIC_SKILLS` set.
  - Replace arbitrary `params: dict[str, Any]` model output with effect-specific
    typed variants or an equivalent provider-supported strict representation.
    Forbid unknown parameters and keep the existing maximum of five effects.
  - Use the same Pydantic contracts for provider schema generation and local
    runtime validation so schema/runtime drift fails tests.
  - Preserve the agentic subset boundary. Strict schema must not authorize
    `deshake`, `fade`, `letterbox`, `mirror`, or `rotate` in agentic finishing.
- **Dependencies**: Tasks 2.1-2.2 and authoritative FFMPEGA parameter evidence.
- **Acceptance criteria**:
  - Every allowed effect has success, unknown-field, missing-field, type, bound,
    and allowlist tests appropriate to its contract.
  - Agentic and full schemas are deterministic, private-free, versioned, and do
    not expose raw FFmpeg, paths, URLs, model names, devices, or commands.
  - Schema-valid but incompatible effect combinations are rejected locally.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_ffmpega_contracts.py tests/test_ffmpega.py -v`
- **Rollback**: Remove `ffmpega` from the strict boundary allowlist and retain the
  existing local validator and native-render fallback behavior.

### Task 2.5: Add The 9Router Strict-Schema Capability Gate

- **Location**: `scripts/qa_ninerouter.py`, `tests/test_qa_ninerouter.py`,
  `bin/kamal-mvp`, env/config/deploy documentation.
- **Description**:
  - Add an isolated, private-free schema probe using a tiny stable object.
  - Verify strict rejection of an impossible extra field and acceptance of the
    valid schema response.
  - Make `json_schema` production enablement fail closed when the probe has not
    passed for the configured route/model.
  - Keep OpenRouter-specific behavior out of the implementation.
- **Dependencies**: Tasks 2.2-2.4.
- **Acceptance criteria**:
  - Mock tests cover supported and unsupported providers.
  - Live probe is opt-in and never includes production prompts/media.
  - Sprint 2 may complete with production mode still `json_object`; live proof is
    a Sprint 6 production-enablement gate and requires separate authorization.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_qa_ninerouter.py tests/test_kamal_config.py -v`
- **Rollback**: Set mode to `json_object`; deploy wrapper no longer requires the
  strict capability gate.

### Sprint 2 Gate

- [x] All Sprint 2 tasks complete.
- [x] Sprint 2 validation passes and evidence is recorded.
- [x] Residual risks are documented.
- [x] Exactly one Sprint 2 commit is created with the proposed sprint message.
- [x] The rollback point is recorded.
- [x] Sprint 3 has not started before this gate completes.

**Recorded evidence**: 100 focused Sprint 2 tests passed, the full local suite
passed 403 tests with 74 expected database skips, and the same 403 tests passed
against the isolated PostgreSQL database. Python compilation, shell syntax,
schema snapshots, configuration rendering, diff checks, and private-free mock
capability cases passed. The live strict-schema probe intentionally remains a
Sprint 6 authorized rollout gate; production defaults to `json_object`. Roll
back by setting `OPENSTORYLINE_STRUCTURED_OUTPUT_MODE=json_object` and reverting
the Sprint 2 commit; no migration was added.

## Sprint 3: Define Stage-Bounded Evidence-Driven Repair Policy And Contracts

**Goal**: Every registered defect has an explicit repair disposition, and the
visual and plan models receive bounded machine-generated repair tasks only for
eligible codes under their separate stage budgets.

**Dependencies**: Sprint 2 gate complete.

**Tracked scope**: `repair.py`, registry metadata, prompt/schema versions,
observability compaction, fixtures, and focused tests.

**Commit**: `feat: define stage-bounded defect repair policy`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair.py tests/test_mvp_observability.py tests/test_mvp_prompt_versions.py tests/test_mvp_edit_plan.py -v`
- Generate synthetic bounded visual and plan-repair requests for every
  LLM-repairable registry entry, prove non-repairable codes cannot enter them,
  and prove transient prompt/transcript context never enters persisted evidence.

**Rollback point**: Keep the registry and strict schemas, set
`OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off`, and revert Sprint 3 policy wiring.

### Task 3.1: Implement Conditional Repair Eligibility

- **Location**: `src/open_storyline/mvp/repair.py` and `defects.py`.
- **Description**:
  - Evaluate repair eligibility using code, stage, available capabilities,
    evidence completeness, whether rendering has started, and whether the
    finding is objective or advisory.
  - Enforce mode semantics explicitly: `off` bypasses registry-driven repair,
    `report` computes and records a redacted would-repair disposition without a
    provider call, and only `enforce` may consume a semantic repair budget.
  - Permit at most one visual-understanding repair and separately group all
    eligible edit-plan codes for the same job into one plan-repair round.
  - Include exact numeric, timing, geometry, capability, budget, and subtitle
    safe-zone defects in plan repair so the model may choose a better bounded
    correction before deterministic fallback.
  - Reject unknown, technical, transient-provider, source, security, and
    post-render-only codes. Reject FFMPEGA defects from semantic repair.
  - Enforce hard bounds for clips, codes, evidence records, prompt bytes, one
    visual repair, and one plan repair.
- **Dependencies**: Sprint 1 registry and Sprint 2 schema.
- **Acceptance criteria**:
  - Eligibility decisions are deterministic and independently testable.
  - A code cannot become LLM-repairable through model output.
  - Advisory-only findings never trigger a semantic call.
  - A missing required evidence type forces deterministic fallback or failure.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair.py -v`
- **Rollback**: Return `eligible=false` for all LLM strategies.

### Task 3.2: Create The Repair Request And Response Contract

- **Location**: `repair.py`, `structured_outputs.py`, `prompts.py`, and
  `prompt_versions.py`.
- **Description**:
  - Create a stable `repair_batch_request.v1` application contract containing
    affected clip IDs, original candidate subtrees, registered code descriptions,
    bounded objective evidence, available capabilities, catalog context,
    immutable constraints, the already-authorized editing prompt, and only the
    affected clip-local transcript excerpts required for editorial context.
  - Use the stable `edit_plan_repair.v1` provider-response schema registered in
    Sprint 2; the response contains corrected candidate clip plans only.
  - The system prompt explicitly requires preserving usable editorial decisions,
    source windows, unaffected operations, and catalog consistency.
  - Exclude free-form error prose, raw provider bodies, credentials, paths,
    unrelated transcript segments, and unnecessary full-job context.
- **Dependencies**: Task 3.1.
- **Acceptance criteria**:
  - The request is fully JSON-serializable, bounded, and private-data reviewed.
  - The response cannot claim that a code was resolved or authorize promotion.
  - Prompt/schema versions and hashes are recorded without storing prompt or
    transcript text in checkpoints, reports, events, audit summaries, or logs.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair.py tests/test_mvp_prompt_versions.py -v`
- **Rollback**: Remove the repair prompt/schema while retaining the registry.

### Task 3.3: Define Deterministic Resolution And Quality Floors

- **Location**: `repair.py`, `edit_plan.py`, `preflight.py`, `fallbacks.py`.
- **Description**:
  - Re-run the same authoritative validators after repair and compute resolved,
    remaining, and new codes from deterministic results.
  - Add quality-floor checks: unchanged selected source windows and output count,
    preserved audio/subtitle requirements, no loss of valid catalog style, no
    deletion of unaffected operations/assets, and no new unsupported capability.
  - If the repaired candidate collapses unnecessarily, reject it and apply
    defect-specific fallback to the original validated candidate.
- **Dependencies**: Task 3.2.
- **Acceptance criteria**:
  - A schema-valid but semantically worse plan is rejected.
  - Failed repair preserves original real codes and records any new codes.
- **Validation**:
  - Add adversarial fixtures for valid-schema invalid-timing, invented evidence,
    removed clips, removed captions, and unsupported catalog IDs.
- **Rollback**: Disable repair; existing validation/fallback remains authoritative.

### Task 3.4: Define Objective And Advisory Predictive QA Policy

- **Location**: `defects.py`, `repair.py`, `preflight.py`, `creative_qa.py`, and
  focused fixtures.
- **Description**:
  - Define objective pre-render findings for mechanically provable plan defects,
    including duplicate overlays, invalid opacity, impossible timing/geometry,
    unsafe subtitle-zone occupancy, unsupported combinations, and bounded static
    active-picture risks when the plan alone provides sufficient evidence.
  - Define heuristic findings for inactive hooks, attention gaps, long visual
    holds, rhythm risks, and similar creative predictions.
  - Give predictive pre-render findings distinct codes from measured post-render
    QA findings so a forecast cannot overwrite or impersonate observed evidence.
  - Allow objective repairable findings to trigger the plan-repair batch. Allow
    heuristic findings only as secondary suggestions when an objective finding
    has already triggered that batch.
- **Dependencies**: Tasks 3.1-3.3.
- **Acceptance criteria**:
  - Every predictive code declares detector, evidence, confidence/threshold,
    phase, trigger eligibility, fallback, and promotion behavior.
  - Advisory-only fixtures make zero semantic repair calls and never become
    technical blockers.
  - Post-render measurements retain their existing authoritative codes.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair.py tests/test_mvp_creative_qa.py tests/test_mvp_frame_quality.py -v`
- **Rollback**: Disable predictive findings while retaining the core repair
  contracts and existing post-render QA.

### Sprint 3 Gate

- [x] All Sprint 3 tasks complete.
- [x] Sprint 3 validation passes and evidence is recorded.
- [x] Residual risks are documented.
- [x] Exactly one Sprint 3 commit is created with the proposed sprint message.
- [x] The rollback point is recorded.
- [x] Sprint 4 has not started before this gate completes.

**Recorded evidence**: 100 focused policy, privacy, prompt, planning, predictive
QA, registry, observability, and deployment-config tests passed. The full local
suite passed 418 tests with 74 expected database skips, and the same 418 tests
passed against the isolated PostgreSQL database. Python compilation, shell
syntax, configuration rendering, diff checks, bounded every-code request
generation, and private-context redaction checks passed. The policy remains
dormant until Sprint 4 wires stage execution; no live provider call, migration,
or production state change occurred. Roll back by setting
`OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off` and reverting the Sprint 3 commit;
the registry and strict transport remain available.

## Sprint 4: Execute Visual And Plan Repair With Predictive QA And Safe Fallback

**Goal**: The pipeline may perform one strict-schema visual-evidence repair and
one later job-level strict-schema plan repair, deterministically validates both,
uses predictive QA under the registry policy, and falls back only where repair
remains invalid.

**Dependencies**: Sprint 3 gate complete.

**Tracked scope**: `edit_plan.py`, `visual_understanding.py`,
`visual_coverage.py`, `preflight.py`, `pipeline.py`, `checkpoints.py`,
`fallbacks.py`, `creative_qa.py`, render preparation, and integration tests.

**Commit**: `feat: run stage-bounded agentic repair`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_edit_plan.py tests/test_mvp_visual_understanding.py tests/test_mvp_pipeline.py tests/test_mvp_fallbacks.py -v`
- Demonstrate: invalid visual relationships -> at most one visual repair ->
  validated evidence or fail-closed evidence handling.
- Demonstrate: initial plan defects -> evidence refresh -> one batched plan repair
  call -> corrected preflight -> render-ready plan.
- Demonstrate: failed repair -> exact remaining labels -> localized fallback ->
  technically valid render-ready plan.

**Rollback point**: Set `OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off`; retain
strict schemas and restore the existing deterministic baseline behavior.

### Task 4.1: Refactor The Current Repair Sequence

- **Location**: `edit_plan.py`, `visual_understanding.py`, and `pipeline.py`.
- **Description**:
  - In `enforce`, perform at most one bounded visual-understanding replacement
    call for invalid visual relationships and remove independent immediate repair
    calls for each invalid edit-plan clip in favor of one job-level plan repair.
  - In `report`, build the same bounded eligibility/request summary and persist
    only redacted metadata without calling the model; in `off`, bypass the new
    registry-driven repair path entirely.
  - Preserve enough bounded invalid-candidate evidence to assemble a job-level
    repair batch.
  - Keep current behavior behind the kill switch until the new path is proven.
  - Separate transport attempts, visual repair, visual evidence refresh, plan
    repair, and deterministic fallback in attempt accounting.
- **Dependencies**: Sprint 3 contracts.
- **Acceptance criteria**:
  - At most one visual repair and one plan repair are made per job.
  - `off` and `report` make zero registry-driven semantic repair calls; `report`
    records the same bounded eligibility disposition that `enforce` would use.
  - Multiple clips/codes are represented in the same bounded repair round.
  - Cancellation and worker recovery do not duplicate either completed repair
    stage.
- **Validation**:
  - Integration tests assert provider call counts and attempt categories.
- **Rollback**: Disable the new repair path and use current per-clip behavior.

### Task 4.2: Refresh Objective Evidence Before Repair

- **Location**: `visual_coverage.py`, `frame_sampling.py`, `preflight.py`,
  `creative_qa.py`, and `pipeline.py`.
- **Description**:
  - Keep the existing one bounded clip-local higher-density visual analysis.
  - Add objective pre-render static plan checks for issues that can be proven
    from the plan and evidence, such as duplicate overlays, invalid opacity,
    impossible timing/geometry, unsupported combinations, and unsafe
    subtitle-zone occupancy.
  - Add bounded heuristic signals for attention gaps, inactive hooks, long visual
    holds, and rhythm risks. These signals may accompany an objective repair
    request but never trigger one alone.
  - Include only sanitized evidence identifiers, bounded measurements, affected
    prompt context, and affected clip-local transcript excerpts.
- **Dependencies**: Task 4.1.
- **Acceptance criteria**:
  - Crop repair never uses global/out-of-window evidence.
  - Objective predictors and advisory heuristics use distinct registered codes.
  - Advisory-only jobs make no repair call and remain non-blocking.
- **Validation**:
  - Focused visual coverage and creative QA tests with cross-niche fixtures.
- **Rollback**: Remove new static checks; retain existing visual repair evidence.

### Task 4.3: Apply Localized Fallback After Failed Repair

- **Location**: `fallbacks.py`, `preflight.py`, `pipeline.py`, and renderer inputs.
- **Description**:
  - Map every remaining creative defect to the smallest registered fallback.
  - Apply fallback only after the model has had the single bounded opportunity to
    choose context-aware valid timing, geometry, capability substitution, budget,
    catalog, reference, or safe-zone values.
  - Preserve valid segments and operations instead of replacing an entire clip
    with the minimal baseline unless FFmpeg preflight proves that necessary.
  - Never remove a technically valid candidate solely for a creative limitation.
  - Keep technical blockers fail-closed.
- **Dependencies**: Tasks 4.1-4.2.
- **Acceptance criteria**:
  - Unaffected operations survive byte-for-byte in plan serialization where
    possible.
  - Every fallback emits a registered limitation with requested/executed values.
  - Final preflight is ready before rendering starts.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_fallbacks.py tests/test_mvp_pipeline.py -v`
- **Rollback**: Restore whole-plan baseline fallback behavior under the feature
  flag.

### Task 4.4: Checkpoint Repair Results Idempotently

- **Location**: `checkpoints.py`, `pipeline.py`, and checkpoint tests.
- **Description**:
  - Store validated repair decisions and fingerprints under `visual_repair` and
    `plan_repair` checkpoint stages without raw private prompts, transcript text,
    frame bytes, or provider bodies.
  - Fingerprint source, prompt version, initial plan, defect registry, schema,
    catalog, renderer capabilities, and objective evidence.
  - Reuse only when every fingerprint matches; corruption fails closed and is
    recomputed within existing bounds.
- **Dependencies**: Tasks 4.1-4.3.
- **Acceptance criteria**:
  - Worker recovery does not spend another semantic call for either stage after
    its valid repair checkpoint exists.
  - Schema/registry/catalog changes invalidate repair checkpoints.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_checkpoints.py tests/test_mvp_pipeline.py -v`
- **Rollback**: Disable repair checkpoint reads while retaining evidence.

### Sprint 4 Gate

- [x] All Sprint 4 tasks complete.
- [x] Sprint 4 validation passes and evidence is recorded.
- [x] Residual risks are documented.
- [x] Exactly one Sprint 4 commit is created with the proposed sprint message.
- [x] The rollback point is recorded.
- [x] Sprint 5 has not started before this gate completes.

**Recorded evidence**: 83 focused edit-plan, visual-understanding, pipeline,
fallback, checkpoint, and repair-policy tests passed with 3 expected database
skips. The full local suite passed 421 tests with 74 expected database skips.
The integration fixtures prove that two objective plan codes are batched into
one strict-schema repair call, successful checkpoints prevent a second call,
invalid cached repair payloads recompute within the same bound, visual repair
attempts remain separately categorized, and localized fallbacks preserve
unaffected operations. Python compilation and diff checks passed. No live
provider call or connected-database run occurred because ordinary validation
does not authorize paid providers and `TEST_DATABASE_URL` is unset. Roll back
with `OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off`; strict schemas and the existing
deterministic baseline remain available.

## Sprint 5: Audit Repair Outcomes And Publish Technical-Pass Candidates

**Goal**: Attempts expose exactly what repair tried, what deterministic checks
resolved, what remained, and which fallback was used, while preserving safe
playable outputs, publishing every technical-pass candidate with truthful
limitations, and providing clear retry UX and agentic audit queries.

**Dependencies**: Sprint 4 gate complete.

**Tracked scope**: outcome/audit/reporting, promotion, delivery policy, FFMPEGA
fallback evidence, job persistence, admin CLI, UI, browser tests, and
compatibility readers.

**Commit**: `feat: publish and audit technical-pass outcomes`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_outcomes.py tests/test_mvp_observability.py tests/test_mvp_audit.py tests/test_mvp_jobs.py tests/test_mvp_frame_quality.py tests/test_ffmpega.py -v`
- `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
- Run the focused retry/comparison Chromium scenario with one worker.

**Rollback point**: Disable repair UX and repair execution; compatibility readers
continue to display `outcome_report.v1` and ignore additive repair artifacts.

### Task 5.1: Add `repair_report.v1`

- **Location**: `repair.py`, `pipeline.py`, artifact registration, audit ingestion,
  and `observability.py`.
- **Description**:
  - Record registry/schema/prompt versions and hashes, trigger codes, eligible and
    ineligible dispositions, evidence identifiers, visual-repair, plan-repair,
    semantic/transport attempts, tokens/cost/latency, deterministic resolution
    results, predictive findings, fallback actions, and checkpoint reuse.
  - Store no prompt text, transcript text, frame bytes, media bytes, provider
    body, credentials, or unsafe paths.
- **Dependencies**: Sprint 4 execution evidence.
- **Acceptance criteria**:
  - Audit ingestion verifies the artifact hash and bounded schema.
  - Repair failure retains original defect codes as remaining.
- **Validation**:
  - Focused observability/audit tests and secret/private-data assertions.
- **Rollback**: Stop registering the additive repair artifact.

### Task 5.2: Version Outcome And Feedback Compatibility

- **Location**: `outcomes.py`, `observability.py`, `jobs.py`, API serializers.
- **Description**:
  - Introduce `outcome_report.v2` and, if needed, `quality_feedback.v2` with
    registry metadata and repair lifecycle fields.
  - Preserve readers for v1 data and never rewrite historical attempts.
  - Track `resolved`, `remaining`, `new`, `fallback_applied`, and
    `not_repairable` dispositions per code.
  - Keep strict creative QA verdict, technical status, delivery policy, delivery
    decision, and download availability as separate fields so publication never
    rewrites strict evidence.
- **Dependencies**: Task 5.1.
- **Acceptance criteria**:
  - Historical and new attempts can be compared safely.
  - Unknown historical codes display safely and remain non-repairable.
- **Validation**:
  - Outcome and prompt-version compatibility tests.
- **Rollback**: Continue writing v1 while retaining v2 reader code if required.

### Task 5.3: Separate Strict QA From Technical-Pass Delivery

- **Location**: `promotion.py`, `pipeline.py`, artifact registration.
- **Description**:
  - Add `OPENSTORYLINE_DELIVERY_POLICY=qa_enforced|technical_pass_guaranteed`,
    defaulting to current `qa_enforced` behavior until the staged rollout gate.
  - Under the approved target `technical_pass_guaranteed` policy, never delete or
    withhold a candidate because a creative repair or strict creative QA failed.
    Register and publish it with truthful limitations whenever technical status
    passes.
  - Preserve `strict_decision=block` when strict creative checks fail. Record a
    separate `delivery_decision=publish_with_limitations`; do not relabel strict
    evidence as pass.
  - Delete or withhold only candidates with technical blockers.
  - If optional FFMPEGA planning, strict validation, service execution, or effect
    execution fails, publish the native FFmpeg candidate with `EFFECT_OMITTED`
    and no additional semantic repair call.
  - If future optional enhanced re-rendering is added, keep the first valid
    candidate until the replacement independently passes promotion; this plan
    does not enable such a loop.
- **Dependencies**: Task 5.2.
- **Acceptance criteria**:
  - Every creative-only blocked candidate remains downloadable with truthful
    limitations under `technical_pass_guaranteed`, including when strict QA is
    blocked.
  - Strict decision evidence remains available and is never rewritten as pass.
  - Technical-only and mixed technical/creative blockers remain unpublished.
  - FFMPEGA failures preserve the native render and emit registered requested/
    executed fallback evidence.
- **Validation**:
  - Promotion tests cover creative-only, technical-only, and mixed blockers.
- **Rollback**: Restore current promotion behavior under existing policy flags.

### Task 5.4: Expose Repair Lifecycle In The Workspace

- **Location**: `messages.js`, `views.js`, styles only if required, and focused
  Playwright coverage.
- **Description**:
  - Show Spanish registry title, raw code, repair attempted/not eligible,
    resolved/remaining/new state, executed fallback, reused stages, and next
    action.
  - Display strict QA verdict separately from the delivery result so a published
    limited output is not presented as strict-approved.
  - Preserve the existing same-version retry and improved-version distinction.
  - Do not imply that a repaired label guarantees subjective quality or virality.
- **Dependencies**: Tasks 5.1-5.3.
- **Acceptance criteria**:
  - Keyboard, mobile, and screen-reader status presentation remains usable.
  - UI never renders raw provider text or private evidence.
- **Validation**:
  - Smoke plus the one affected retry/comparison Playwright test.
- **Rollback**: Hide additive repair details and show current outcome chips.

### Task 5.5: Add Bounded Agentic Defect Audit Queries

- **Location**: `src/open_storyline/mvp/audit.py`,
  `src/open_storyline/mvp/admin.py`, `bin/kamal-mvp`, and
  `tests/test_mvp_audit.py`.
- **Description**:
  - Add `audit defects` with bounded `--since`, `--code`, `--strategy`,
    `--disposition`, `--stage`, `--limit`, and `--format table|json` filters.
  - Read sanitized `outcome_report.v1/v2`, `repair_report.v1`, fallback, and
    promotion evidence through existing PostgreSQL/audit contracts without raw
    prompts, transcripts, provider bodies, media, frames, credentials, or paths.
  - Return stable records suitable for agent review: job/attempt identifiers,
    registry version, code, stage, strategy, disposition, repair/fallback state,
    technical status, delivery decision, and timestamps.
  - Keep queries bounded and add an explicit future normalization/backfill note;
    do not add a table or migration unless measured evidence proves this surface
    insufficient.
- **Dependencies**: Tasks 5.1-5.3.
- **Acceptance criteria**:
  - Agents can answer recent defect, repair-success, fallback, and delivery
    questions without loading raw audit documents or scanning filesystem media.
  - Limits, invalid filters, v1 compatibility, unknown codes, and secret/private
    data absence have deterministic tests.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_audit.py tests/test_mvp_outcomes.py -v`
- **Rollback**: Remove the additive CLI subcommand; retained artifacts and
  outcomes remain readable through existing audit commands.

### Sprint 5 Gate

- [x] All Sprint 5 tasks complete.
- [x] Sprint 5 validation passes and evidence is recorded.
- [x] Residual risks are documented.
- [x] Exactly one Sprint 5 commit is created with the proposed sprint message.
- [x] The rollback point is recorded.
- [x] Sprint 6 has not started before this gate completes.

**Recorded evidence**: 92 focused repair, outcome, observability, audit, job,
promotion, deployment-config, and FFMPEGA tests passed with 28 expected
PostgreSQL skips. The full local suite passed 434 tests with 76 expected
environment-dependent skips. Configuration loading, repository shell syntax,
and diff checks passed. Console-strict Chromium smoke and the focused
retry/comparison lifecycle scenario each passed with one worker; compact
results are stored under `.qa/web/artifacts/`. Outcome v2 remains compatible
with v1 readers, strict QA and delivery decisions remain separate, creative-only
technical-pass candidates remain downloadable only under the explicit target
policy, technical and mixed blockers remain withheld, optional FFMPEGA failure
preserves the native candidate with registered fallback evidence, and repair
artifacts contain bounded hashes/identifiers rather than prompts, transcripts,
frames, provider bodies, credentials, or paths. The defect audit projection is
bounded to at most 5,000 recent jobs and remains document-backed; normalization
or backfill is deferred until measured query volume justifies it. Connected
PostgreSQL query evidence remains a Sprint 6 operational gate because
`TEST_DATABASE_URL` is unset. No live provider call or deployment occurred.
Roll back by setting `OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=off`, restoring
`OPENSTORYLINE_DELIVERY_POLICY=qa_enforced`, and hiding repair/retry UX; v1
outcome readers and existing deterministic rendering remain available.

## Sprint 6: Run Evals, Operational Gates, And Staged Rollout

**Goal**: Prove strict-schema and repair behavior across synthetic niches and
failure modes, then provide a reversible production rollout without claiming
success from an insufficient sample.

**Dependencies**: Sprint 5 gate complete.

**Tracked scope**: fixtures, full tests, config/deploy docs, release QA, metrics,
implementation history, and rollback runbook.

**Commit**: `chore: gate agentic defect repair rollout`

**Demo/Validation**:

- Full local suite:
  `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
- Connected disposable PostgreSQL suite using a database named with the required
  `openstoryline_test` prefix.
- Catalog reproducibility/runtime validation.
- Shell/config/Kamal tests and remote Docker build.
- Smoke plus one affected Playwright retry/comparison test.
- Strict 9Router live probe and private production canary only after explicit
  authorization.

**Rollback point**: Set repair mode to `off`, restore delivery policy to
`qa_enforced`, remove strict boundaries in reverse activation order, restore
global `json_object`, disable repair UI, and roll back the image only after
schema compatibility review. No additive evidence is deleted.

**Pre-deploy evidence (2026-07-21)**:

- The final full local suite passed 444 tests with 76 expected environment
  skips; the disposable connected PostgreSQL suite passed 443 tests with no
  skips.
- The 49-test focused release/catalog suite, catalog manifest/runtime check,
  the final 36-test client/schema/provider-preflight suite, configuration load,
  Python compilation, shell syntax, and diff checks passed.
- The remote image built as `openstoryline-mvp:sprint6-local`; console-strict
  Chromium smoke and the focused retry/comparison flow passed with one worker.
- Authorized redacted 9Router text, vision, and image probes passed. The release
  gate then exposed intermittent Chat Completions extra-field enforcement; the
  Responses-compatible strict transport passed five repeated adversarial calls
  plus a multimodal call and became the fail-closed strict boundary.
- The production PostgreSQL backup and isolated restore check passed at schema
  revision `20260721_0003`, and the private rollout flags passed the offline
  monotonic validator.

**Post-deploy and canary evidence (2026-07-21 local / 2026-07-22 UTC)**:

- Commit `002b4cdde9aa9a5392da2ea40fe945e9bda4f8da` deployed as the exact healthy
  production image. `/up` and `/health` returned HTTP 200, PostgreSQL returned
  `DATABASE_READY`, and recent logs contained no `ERROR` or `Traceback` lines.
- Two runs reused the authorized private session, immutable prompt version, and
  source lineage without recording those private values in Git. Both produced
  one registered 1080x1920 H.264/AAC video at 60 fps with a 22.5-second duration,
  ordered subtitles, and five passing deterministic structural checks.
- The technical-pass candidate published with the truthful creative limitation
  `ASSET_VISIBILITY_ANALYSIS_UNAVAILABLE`; strict QA remained blocked while
  technical status passed. A separate run published as enhanced with no
  limitations. Authenticated range requests returned HTTP 206 for both inline
  preview and attachment download.
- The same-session repair-only canary injected a deterministic, schema-valid
  crop-coverage defect into a private copy of the current valid plan. Enforced
  repair made exactly one semantic call and one transport attempt, used strict
  `edit_plan_repair.v1`, returned HTTP 200 in 25,664 ms, consumed 5,193 input and
  1,235 output tokens (6,428 total), resolved all three objective crop-coverage
  codes through a valid `fit` layout, passed the quality floor, and introduced
  zero defects. The provider did not report a cost value.
- An exploratory replay against a historical pre-catalog artifact returned a
  strict response but failed closed on catalog-version mismatch before quality
  acceptance; it did not alter a job, register an output, or weaken validation.
- The bounded outcome summary reports two playable outputs, a 100% observed
  playable/publication rate, one enhanced and one limited outcome, six reused
  and two recomputed stages, and `claim_ready=false`. The 95% Wilson lower bound
  is 0.34238, far below the 99% claim gate.
- Production FFMPEGA and semantic-QA execution flags remain disabled. Their
  strict schemas and deterministic fallbacks are covered by local tests plus
  the live strict-capability gate, but no production execution canary is claimed
  for disabled features.

### Task 6.1: Build The Repair Eval Matrix

- **Location**: `tests/fixtures/mvp_agentic/`, focused unit/integration tests, and
  optional private operator-only canary procedure.
- **Description**:
  - Cover interview, product presentation, tutorial, cooking, trading/screen,
    multi-speaker, sparse-visual, and no-asset cases.
  - Inject every LLM-repairable code family, deterministic-fallback family,
    objective predictive finding, advisory-only finding, provider retry,
    technical blocker, unknown code, refusal, incomplete output, invalid strict
    schema, schema-valid semantic invalidity, FFMPEGA plan/execution failure, and
    repair regression.
  - Compare baseline current behavior, strict schema only, and strict schema plus
    repair in `off`, `report`, and `enforce` using identical synthetic fixtures.
- **Dependencies**: Complete implementation.
- **Acceptance criteria**:
  - Every registered LLM-repairable code has at least one success and one failed
    repair/fallback test.
  - Every non-LLM code has a test proving no semantic repair call occurs.
  - Advisory-only cases make zero semantic repair calls; advisory signals
    accompany an objectively triggered request without changing call count.
  - Call-count tests prove at most one visual repair, one plan repair, and zero
    FFMPEGA repair calls per job.
  - `off` and `report` cases make zero registry-driven semantic repair calls;
    report/enforce eligibility dispositions match before provider execution.
  - Creative-only strict blockers publish under technical-pass delivery while
    technical and mixed blockers remain unpublished.
  - Fixtures contain no private production data.
- **Validation**:
  - Focused eval suite plus full local suite.
- **Rollback**: Tests remain useful even if rollout flags stay disabled.

### Task 6.2: Add Metrics And Claim Gates

- **Location**: outcome SLO summaries, audit CLI/reporting, and docs.
- **Description**:
  - Measure strict-schema validity, semantic-validity rate, repair trigger rate,
    visual- and plan-repair success by original code, predictive advisory
    attachment rate, fallback rate, FFMPEGA omission rate, new-defect rate,
    provider calls, semantic versus transport attempts, tokens, cost, latency,
    checkpoint reuse, technical-pass publication rate, playable output rate, and
    enhanced/limited outcome rates.
  - Keep the existing confidence/sample gate for a 99% claim.
  - Add alerts/review thresholds for repair increasing latency/cost, introducing
    new defects, or reducing playable output.
- **Dependencies**: Task 6.1.
- **Acceptance criteria**:
  - Metrics are attributable to model, reasoning effort, prompt/schema/registry
    versions, catalog, renderer, and flags without private payloads.
  - `claim_ready` remains evidence-only and cannot enable rollout.
- **Validation**:
  - Outcome/audit metric tests with small-sample and zero-failure cases.
- **Rollback**: Stop emitting additive repair metrics; retain outcome history.

### Task 6.3: Stage Flags And Release Gates

- **Location**: `config.toml`, env examples, `config/deploy.yml`,
  `bin/kamal-mvp`, `scripts/qa_ninerouter.py`, Kamal tests, architecture docs.
- **Description**:
  - Roll out in this order: registry/read-only presentation, strict schema probe,
    strict boundaries progressively (`shorts`, `visual_understanding`,
    `edit_plan`, `semantic_qa`, then `ffmpega`), repair `report`, repair
    `enforce`, technical-pass delivery, then UI details.
  - Keep repair mode, strict mode, strict boundary allowlist, delivery policy,
    and UI independently reversible.
  - Require healthy `/up` and `/health`, database compatibility, backup/restore
    readiness, provider probe, and canary evidence before production enablement.
- **Dependencies**: Tasks 6.1-6.2.
- **Acceptance criteria**:
  - Each flag has a documented owner, default, enable command, disable command,
    validation signal, and rollback signal.
  - Release wrappers make no unapproved live provider calls during ordinary
    local validation.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_kamal_config.py tests/test_qa_ninerouter.py tests/test_remote_profile.py -v`
  - `bash -n run.sh build_env.sh download.sh bin/kamal-mvp scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy .kamal/hooks/post-deploy`
- **Rollback**: Follow the flag order in the Sprint 6 rollback point.

### Task 6.4: Run An Authorized Production Canary

- **Location**: Operator procedure and private production environment only.
- **Description**:
  - After explicit authorization, use the same private immutable session/version
    pattern without exposing prompt, transcript, media, frames, provider bodies,
    or credentials.
  - Run one progressively enabled strict-schema canary, one repair-trigger canary
    if a safely reproducible objective defect exists, and one technical-pass
    delivery canary with a truthful creative limitation.
  - When production FFMPEGA is enabled and separately approved for the canary,
    verify one typed effect plan and one native-render fallback without adding a
    semantic repair call.
  - Verify output playback, promotion, frame evidence, subtitle structure,
    fallback truthfulness, repair checkpoint reuse, tokens/cost/latency, and
    rollback switches.
- **Dependencies**: All prior tasks and production authorization.
- **Acceptance criteria**:
  - Technically valid outputs are registered and downloadable even when strict
    creative QA remains blocked.
  - Real remaining defect codes are visible and auditable.
  - No 99% claim is made until the configured statistical gate passes.
- **Validation**:
  - Private audit CLI and artifact checks; report only sanitized summaries.
- **Rollback**: Disable repair, restore `qa_enforced` delivery, remove strict
  boundaries in reverse order, restore JSON object mode, and retain evidence.

### Sprint 6 Gate

- [x] All Sprint 6 tasks complete.
- [x] Sprint 6 validation passes and evidence is recorded.
- [x] Residual risks are documented.
- [x] Exactly one Sprint 6 commit is created with the proposed sprint message.
  Two later fix commits address provider-gate defects discovered during the
  authorized deployment without rewriting the sprint commit.
- [x] The rollback point is recorded.
- [x] No production rollout occurs without separate authorization.

## Sprint 7: Prove Quality-First Production Semantic QA And Agentic Finishing

**Goal**: Production-enable strict semantic video QA and typed FFMPEGA effect
execution, then debug, fix, and improve the complete remote-MVP path until the
current uploaded session video produces correct, robust agentic output with the
latest prompt and no known in-scope regression.

**Dependencies**: Sprint 6 gate complete, the current production image healthy,
the semantic and FFMPEGA strict-capability probes passing for the exact deployed
route/model, FFMPEGA service/storage readiness verified, and explicit approval
of this Sprint 7 title before implementation or production execution.

**Tracked scope**: the affected remote-MVP upload/session, job, provider,
planning, repair, rendering, semantic-QA, FFMPEGA, outcome, audit,
preview/download, browser, deployment, observability, and rollback paths. Sprint
7 is authorized to debug and fix defects discovered anywhere in those paths,
with focused regression tests and preserved public contracts.

**Commit**: `feat: prove quality-first agentic video editing`

**Demo/Validation**:

- Reuse the current authorized private session, uploaded source, immutable
  lineage, and latest prompt without recording private values in Git or PR text.
- Run the latest prompt first. Add only one or two targeted prompt variants when
  a measured residual risk cannot be proven or disproven with the latest prompt.
- Verify strict semantic responses, frame/evidence references, typed effect
  plans, actual effect execution, native-render fallback, output registration,
  playback/download, subtitles, technical media properties, and defect/audit
  attribution.
- Inspect and compare the rendered outputs directly against prompt intent,
  deterministic evidence, semantic-QA findings, and the previously published
  baseline. Semantic QA may explain or prioritize evidence, but it cannot be the
  sole quality authority.
- After the final code/config/prompt fix, require repeatable clean execution of
  the latest prompt. The existing broad 99% Wilson gate remains reported but is
  not a Sprint 7 blocker because this sprint makes a scoped same-session MVP
  readiness claim rather than a cross-population reliability claim.

**Quality-first cost policy**: Provider latency, tokens, and cost are recorded
and attributed but do not fail Sprint 7 merely for exceeding the Sprint 6 review
thresholds when the agentic variant demonstrates a verified output-quality
improvement. Hard call-count limits, payload bounds, provider/job
timeouts, checkpoint idempotency, queue capacity, and kill switches remain
mandatory. Additional spend without measured quality improvement fails the
quality-value gate.

**Rollback point**: Disable `OPENSTORYLINE_FFMPEGA_ENABLED` first and retain the
native renderer, then disable `OPENSTORYLINE_SEMANTIC_QA_ENABLED`. Remove the two
FFMPEGA strict boundaries and then `semantic_qa.v1` only if their provider
compatibility is implicated. If agentic quality regresses, set repair to
`report` or `off` and restore `qa_enforced`. Roll back the image only after
schema, database, checkpoint, and artifact compatibility review. Retain all
sanitized outcome, repair, semantic-QA, effect, and audit evidence.

### Task 7.1: Debug And Harden The Complete Same-Session MVP Path

- **Location**: all affected remote-MVP backend, provider, pipeline, renderer,
  persistence, API, browser, deployment, tests, and documentation paths.
- **Description**:
  - Trace the current session from stored upload/source lineage through planning,
    repair, render, semantic QA, FFMPEGA, promotion, registration, preview,
    download, retry, and audit evidence.
  - Reproduce every discovered failure with the smallest safe local fixture,
    sanitized production diagnostic, or focused test before changing behavior.
  - Fix in-scope implementation, prompt, schema, configuration, deployment,
    observability, and browser defects discovered during Sprint 7. Preserve auth,
    CSRF, job scoping, artifact safety, API/status shapes, and profile boundaries.
  - Add focused regression coverage for each fixed defect and re-run adjacent
    workflow tests. Do not defer a correctness defect merely because an output
    remains technically playable.
  - Classify every published limitation as dependency/analysis unavailable,
    genuine source constraint, unresolved creative-quality gap, or technical
    blocker. A technical blocker remains unpublished.
  - Treat `SEMANTIC_QA_UNAVAILABLE`, `SEMANTIC_QA_FRAMES_MISSING`, and
    `ASSET_VISIBILITY_ANALYSIS_UNAVAILABLE` as Sprint 7 failures when their
    configured production dependencies are healthy; do not relabel them as
    acceptable creative limitations.
  - Preserve genuine creative limitations when the source lacks sufficient
    visual evidence, an eligible repair fails its quality floor, or the requested
    enhancement cannot be produced safely. Publication remains truthful rather
    than pretending strict creative approval.
  - Use a direct comparison rubric for intent fidelity, composition and subject
    visibility, pacing/story continuity, caption readability, effect
    appropriateness, artifact absence, and overall correctness.
  - Attribute each result to source characteristics, immutable prompt, model/reasoning,
    schema/prompt/registry/catalog versions, renderer, flags, and review rubric
    without persisting private content.
- **Acceptance criteria**:
  - Every Sprint 7 defect has a reproducible diagnosis, implemented fix or
    explicit external blocker, regression coverage, and sanitized evidence.
  - The same session remains authoritative; no replacement upload or unrelated
    niche dataset is required to pass the sprint.
  - API, audit, and browser presentation distinguish technical status, semantic
    verdict, delivery decision, and limitation class without color-only cues.
  - Healthy-path dependency/analysis-unavailable limitation count is zero.
  - Technical-pass is never presented as synonymous with strict creative QA.
  - The quality claim requires deterministic checks plus direct rendered-output
    inspection; semantic self-evaluation alone cannot pass the gate.
- **Validation**:
  - Focused regression tests for every fix, registry/outcome/audit/UI tests,
    sanitized limitation fixtures, and same-session end-to-end verification.
- **Rollback**: Keep raw codes and prior outcome readers; hide additive Sprint 7
  presentation if necessary without reclassifying historical evidence.

### Task 7.2: Production-Enable Strict Semantic Video QA

- **Location**: `creative_qa.py`, pipeline orchestration, strict boundary config,
  env/deploy examples, release wrapper, metrics, tests, and rollout docs.
- **Description**:
  - Require the strict `semantic_qa.v1` boundary and a passing live capability
    probe before setting `OPENSTORYLINE_SEMANTIC_QA_ENABLED=true`.
  - Select a bounded but representative frame set across important scenes and
    subject/layout changes. Calibrate the frame cap against missed-defect and
    false-positive evidence rather than assuming the current default is ideal.
  - Validate schema, frame references, evidence ownership, code classification,
    and semantic consistency in application code. The model may report a
    verdict but cannot authorize publication, promotion, repair success, or
    technical status.
  - Persist only redacted summaries and stable evidence references. Never store
    prompt text, transcript excerpts, image bytes, or provider response bodies.
  - Keep semantic-provider failure fail-soft for a playable output but classify
    it explicitly as dependency/analysis unavailable and fail the healthy-path
    Sprint 7 gate.
- **Acceptance criteria**:
  - Authorized production semantic-QA calls return strict, semantically valid,
    frame-grounded verdicts for every healthy-path canary.
  - Injected blur, blockiness, crop/visibility, caption, pacing, and mismatch
    cases are detected or correctly rejected according to detector ownership.
  - False semantic findings cannot introduce new defects, suppress a valid
    technical output, or bypass deterministic validators.
  - Semantic QA call counts, latency, tokens, cost, frames reviewed, and verdict
    attribution are visible in sanitized audit evidence.
- **Validation**:
  - Focused semantic-QA unit/integration suite, live strict probe, private
    canary, and direct rendered-output comparison against deterministic evidence.
- **Rollback**: Set `OPENSTORYLINE_SEMANTIC_QA_ENABLED=false`; retain strict
  schema code, redacted evidence, deterministic QA, and playable delivery.

### Task 7.3: Production-Enable Typed FFMPEGA Agentic Finishing

- **Location**: FFMPEGA client/contracts, pipeline, ComfyUI service readiness,
  shared output paths, strict boundary config, env/deploy examples, release
  wrapper, metrics, tests, and rollback docs.
- **Description**:
  - Verify the pinned FFMPEGA contract source, service health, queue/history API,
    shared input/output path mapping, timeouts, and persistent storage before
    setting `OPENSTORYLINE_FFMPEGA_ENABLED=true`.
  - Require both `ffmpega_agentic_finishing.v1` and
    `ffmpega_deterministic_effects.v1`; continue rejecting arbitrary skills,
    parameters, paths, effect counts, and incompatible combinations.
  - Prove that a requested effect is not merely schema-valid: record typed plan
    evidence, FFMPEGA completion evidence, output registration, media probing,
    and a deterministic or visual comparison signal appropriate to the effect.
  - Preserve the first playable native render and fall back to it when FFMPEGA
    planning, service execution, output discovery, validation, or registration
    fails. FFMPEGA never receives an independent LLM repair loop.
  - Include no-op and over-decoration controls so agentic finishing is accepted
    only when it is relevant to the prompt and improves or preserves the direct
    quality score.
- **Acceptance criteria**:
  - Every healthy-path requested effect has a valid typed plan and verified
    execution; otherwise the registered native fallback remains playable and
    the exact limitation is auditable.
  - FFMPEGA cannot read or write outside the authorized shared job paths and no
    provider/service payload containing private media data is persisted.
  - Effect execution introduces zero technical blockers and zero new accepted
    defect codes compared with the native baseline.
  - Agentic finishing passes the direct quality-value comparison; extra effects
    that do not improve the current output are omitted rather than rewarded for
    complexity.
- **Validation**:
  - Every-effect contract/integration tests, service readiness and failure
    canaries, media probes, path-security checks, native-fallback comparison,
    and private production effect canaries.
- **Rollback**: Set `OPENSTORYLINE_FFMPEGA_ENABLED=false`; retain the native
  output, typed contracts, omission evidence, and semantic QA independently.

### Task 7.4: Run Iterative Same-Session Production QA

- **Location**: private operator procedure, outcome/audit summaries, eval
  fixtures, browser QA, and sanitized Sprint 7 evidence record.
- **Description**:
  - Produce immutable same-session variants with matched uploaded source lineage:
    the latest prompt first, then semantic-QA/FFMPEGA/repaired reruns required by
    discovered fixes. Do not overwrite or mutate prior registered outputs.
  - After the last implementation, configuration, or prompt fix, run the latest
    prompt until two consecutive outputs pass the complete deterministic,
    semantic, effect, playback, audit, and direct-inspection gate.
  - Use at most one or two additional prompt versions only when a specific
    residual risk needs targeted evidence, such as asset selection, pacing,
    subject framing, captions, or effect appropriateness. Record why each variant
    was necessary and stop when the risk is resolved or reproduced.
  - Report the actual attempts, failures, limitation distribution, retry and
    fallback rates, semantic validity, FFMPEGA execution/omission, new defects,
    latency, tokens, and available provider cost. Continue to report the broad
    Wilson gate honestly without requiring an unrelated large cohort.
  - Verify authenticated preview/download, mobile/desktop outcome details,
    Spanish/English limitation text, logs, health, queue behavior, PostgreSQL,
    backup/restore readiness, and rollback switches during the QA attempts.
- **Acceptance criteria**:
  - Two consecutive latest-prompt outputs pass after the final fix, with no
    intervening code, config, schema, model, source, or prompt change.
  - Any targeted prompt variants pass their named residual-risk checks without
    technical, privacy, authorization, or adjacent-workflow regression.
  - Accepted repairs and finishing introduce zero new defects; all technically
    valid candidates remain registered and playable.
  - Healthy-path semantic/asset-analysis unavailable limitations are zero;
    remaining creative limitations are genuine, specific, and reviewable.
  - Latency/token/cost increases are accepted only with verified quality lift,
    bounded execution, stable queue health, and explicit operator approval.
- **Validation**:
  - Sanitized outcome/defect audit reports, deterministic artifact verification,
    direct comparison results, browser QA, production health/log observation, and
    database backup/restore evidence.
- **Rollback**: Stop production QA, disable FFMPEGA, disable semantic QA, and keep
  prior playable outputs plus all redacted evidence for diagnosis.

### Task 7.5: Close The Trustworthy-MVP Release Gate

- **Location**: rollout runbook, architecture/audit/user docs, implementation
  history, PR evidence, and operator handoff.
- **Description**:
  - Publish a sanitized claim table separating deterministic technical proof,
    same-session repeatability, semantic-QA validity, direct creative-quality
    comparison, limitations, latency/tokens/cost, and untested boundaries.
  - Keep `claim_ready` evidence-only. Explicit operator approval remains required
    to retain production flags after the canary and to make any public claim.
  - Document incident thresholds for semantic invalidity, FFMPEGA service/path
    failures, new defects, playable-rate regression, misleading limitations,
    queue instability, and privacy/redaction failures.
  - Update the PR only after the complete Sprint 7 gate passes; do not describe
    planned or partial evidence as completed production proof.
- **Acceptance criteria**:
  - The release gate has explicit PASS/FAIL evidence for behavior, tools and
    authorization, state/privacy, evals, observability, rollout, and rollback.
  - Every production flag has a verified enable, disable, health, evidence, and
    rollback command; no secrets or private media identifiers enter Git/PR text.
  - A failed quality or reliability gate leaves the MVP operational on the
    previously proven native/semantic-disabled path without data loss.
- **Validation**:
  - Final full local and connected-PostgreSQL suites; provider/service probes;
    remote image build; Kamal/config/shell checks; browser QA; production health,
    logs, backup/restore, canary, audit, privacy, and diff review.
- **Rollback**: Follow the Sprint 7 rollback point and preserve evidence.

### Sprint 7 Gate

- [x] The Sprint 7 title is presented and explicitly approved before execution.
- [ ] All Sprint 7 release gates complete. Core execution is complete; the
  authenticated production browser and quantified effect-lift gates remain open.
- [x] Production semantic QA and typed FFMPEGA execution pass authorized
  canaries for the exact deployed route, model, service, schema, and image.
- [x] Healthy-path analysis-unavailable limitations are zero.
- [x] The latest prompt produces two consecutive fully passing same-session
  outputs after the final fix.
- [x] No more than two additional prompt versions are used, and only when a named
  residual risk requires them.
- [x] The broad `claim_ready` value and Wilson interval remain reported; no 99%
  cross-population reliability statement is made unless that separate gate passes.
- [ ] Latency/token/cost increases have measured quality lift and remain bounded.
- [ ] Full integration, security/privacy, browser, database, release, health,
  backup/restore, observability, and rollback validation passes. All listed
  checks pass except authenticated production browser preview/download, which
  requires an operator-held plaintext password and must not be bypassed.
- [x] Residual risks and claim language are current and evidence-backed.
- [x] Exactly one Sprint 7 implementation commit is created with the proposed
  sprint message; any post-gate production fix remains separate and explicit.
- [x] The rollback point and sanitized production evidence are recorded.

### Sprint 7 Production Evidence

- **Final image and defect repair**: deployed
  `1e15456b76e84d1f83d7b4d6b318ec348f9d7b02`. A real 60-fps render versus
  30-fps stock-video comparison previously returned no FFmpeg SSIM summary and
  was misclassified as `ASSET_VISIBILITY_ANALYSIS_UNAVAILABLE`. The comparison
  now normalizes both streams to a shared frame rate, timebase, and zero-based
  timestamps. The prior production artifact measures `0.977345` after the fix,
  and a mismatched-timebase zero-start regression is checked in.
- **Validation**: 61 focused tests pass; the full local suite passes 455 tests
  with 78 expected PostgreSQL skips; the disposable connected-PostgreSQL suite
  passes all 455 tests. The guarded 9Router strict-schema/text/vision/image,
  direct Mistral STT, and pinned FFMPEGA readiness gates pass.
- **Consecutive authoritative proof**: two post-fix immutable reruns use the
  same uploaded source, prompt version, model route, schemas, configuration, and
  final image with no intervening mutation. Both publish one distinct playable
  22.5-second 1080x1920 H.264/AAC output at 60 fps; strict promotion, frame
  quality, semantic QA, subtitle structure, and full decode pass. Both external
  assets are visible in each run with deterministic SSIM values above `0.93`,
  and neither run introduces a defect or healthy-path analysis-unavailable code.
- **Direct inspection**: both outputs satisfy the requested source-first edit,
  visible external-support assets, legible captions, coherent ending, and
  artifact-free playback. `VISUAL_REFRAME_FALLBACK` remains truthful for dense
  landscape screen content where content-preserving fit is safer than cropping;
  the second run reduces the fallback count from six to two but does not remove
  the source/aspect-ratio compromise.
- **Typed finishing proof**: the one existing targeted prompt version is rerun
  on the final image. Strict planning selects typed `sharpen(amount=0.4)`, the
  pinned FFMPEGA service produces and registers `short-01-effects.mp4`, semantic
  and deterministic QA pass, full decode passes, and FFMPEGA omission and new
  defect counts remain zero. Direct inspection proves execution without proving
  a strong native-versus-effect quality lift; that remains an explicit gate.
- **Operations**: `/up`, `/health`, PostgreSQL readiness and head
  `20260721_0003`, exact-image verification, queue completion, post-canary logs,
  FFMPEGA readiness, and fresh backup/restore checks pass. No migration is added.
- **Claim boundary**: the current two-hour aggregate remains
  `claim_ready=false` with 7 classified attempts, a playable-output rate of
  `0.857143`, and a 95% Wilson interval of `[0.486872, 0.974320]`. That window
  intentionally includes pre-fix failures and limitations. It cannot support a
  99% population claim; the passing statement is limited to the tested source,
  authoritative prompt, targeted effect prompt, and exact final image.
- **Open operator gates**: production preview/download is structurally
  registered and independently decoded, but authenticated browser verification
  requires the operator's plaintext login password. Provider token/cost fields
  remain redacted or unavailable, and the subtle sharpen canary does not provide
  a strong retained-native A/B quality measurement.

## Testing Strategy

- **Unit**:
  - Registry uniqueness, aliases, bilingual presentation, unknown fail-closed
    behavior, and repairability predicates.
  - Strict schema snapshots, schema subset validation, stable hashes, nullable
    optional fields, recursive `additionalProperties=false`, refusal/incomplete
    responses, and secret-safe errors.
  - Effect-specific FFMPEGA fields, bounds, enums, agentic/full allowlists,
    incompatible combinations, schema/runtime drift, and native-render fallback.
  - Visual- and plan-repair request bounds, eligibility, objective/advisory
    policy, quality floors, resolution comparison, and fallback mapping.
- **Integration**:
  - Strict 9Router mock transport through shorts, vision, edit planning, repair,
    semantic QA, and FFMPEGA effect planning.
  - Progressive strict-boundary activation and explicit no-downgrade behavior.
  - Pipeline call counts for at most one visual repair, one plan repair, and zero
    FFMPEGA repairs; zero registry-driven calls in `off` and `report`; evidence
    refresh, both repair checkpoints, fallback
    compilation, strict QA versus delivery decisions, outcome v1/v2
    compatibility, audit ingestion, `audit defects`, retention, and retry
    feedback.
  - Disposable PostgreSQL coverage for outcome/audit/checkpoint persistence.
- **End-to-end/manual**:
  - Browser outcome/repair comparison, Spanish labels, raw code visibility,
    strict-verdict versus delivery status, retry actions, mobile layout, keyboard
    navigation, and console cleanliness.
  - Optional authorized live 9Router strict-schema probe using no private data.
  - Optional authorized private production canary with sanitized reporting.
  - Sprint 7 requires iterative same-session output inspection using the latest
    prompt plus at most two evidence-driven variants, with semantic-QA and
    FFMPEGA production canaries.
- **Security/privacy**:
  - Stable schemas contain no job-private values. Repair reports, checkpoints,
    events, logs, screenshots, audit summaries, and persisted fixtures expose no
    credentials, raw prompts, transcript text, media/frame bytes, provider
    bodies, or unsafe paths.
  - Transient repair input contains only the already-authorized prompt and
    affected bounded clip-local transcript excerpts sent to the same provider;
    tests prove persistence and logging redact them.
  - Unknown and technical codes cannot be promoted to LLM repair through input.
  - Provider response cannot authorize fallback, promotion, or code resolution.
- **Performance/cost**:
  - At most one visual repair and one plan repair per job; FFMPEGA adds no repair
    call.
  - Bounded visual/plan repair payload and response sizes.
  - Separate token/cost/latency metrics for initial generation, transport retry,
    visual repair, evidence refresh, and plan repair.
  - Stage-specific checkpoints prevent duplicate spending after worker recovery.
  - Sprint 7 treats latency/token/cost thresholds as review signals rather than
    quality blockers when same-session evidence proves improvement, but it never
    removes bounded calls, timeouts, idempotency, queue controls, or kill switches.
- **Accessibility**:
  - Outcome and repair status are not color-only; raw code and explanatory text
    remain available to assistive technology.
- **Migration/compatibility**:
  - No migration expected. `audit defects` must remain bounded against existing
    outcome/audit JSON. If measured evidence requires a table, stop and add a
    separately justified connected-database, backfill, backup/restore,
    compatibility-set, and rollback plan before proceeding.
  - Historical v1 outcome/feedback documents remain readable and immutable.
- **Release**:
  - Project-native Python tests precede browser QA.
  - Docker/Kamal/config/health/rollback checks are required for release changes.
  - Production/provider calls remain opt-in.
  - Semantic QA and FFMPEGA enable independently, in order, only after their
    exact strict boundaries, service dependencies, fallback, and rollback pass.
  - Trustworthy-MVP claim language is scoped to the current uploaded source and
    tested prompt versions unless the broader playable-output confidence gate
    separately passes.

## Risks And Gotchas

| Risk | Impact | Mitigation | Validation signal |
| --- | --- | --- | --- |
| 9Router does not forward strict JSON Schema | Runtime 4xx or invalid compatibility assumption | Safe capability probe, explicit mode flag, fail closed before enablement | Probe passes for exact route/model; unsupported mock fails safely |
| Strict schema is mistaken for semantic correctness | Valid JSON still violates timing, evidence, catalog, or quality rules | Keep Pydantic/domain/preflight/QA validation authoritative | Schema-valid semantic-invalid fixtures are rejected |
| Dynamic schemas leak data or increase latency | Privacy and first-request schema-processing cost | Stable private-free schemas; keep job/catalog evidence in bounded prompt data | Schema snapshots contain no job data and remain stable |
| Transient repair context leaks into persistence | Prompt or transcript privacy regression | Send only bounded affected context to the same provider; redact reports/checkpoints/logs; secret/private fixtures | Persistence tests find no prompt/transcript text or provider bodies |
| Stage-bounded repair payload is too broad | Large payload or model removes good edits | Per-clip bounded subtrees, quality-floor diff, hard clip/code/byte caps | Adversarial collapse fixtures fail and localized fallback runs |
| Current per-clip repair behavior regresses | More fallbacks or lower quality | Feature flag, A/B fixture matrix, preserve current path during rollout | Baseline vs new repair success/output comparison |
| Repair creates new defects | Worse plan despite fixing original code | Re-run every validator and reject on new blocker/quality-floor failure | `new_codes` empty for accepted repairs |
| Predictive QA overreaches | False positives spend tokens or weaken edits | Objective/advisory code separation; advisory-only findings never trigger or block | Advisory-only fixtures make zero calls and preserve delivery |
| Post-render defect triggers expensive loop | Higher latency/tokens and possible loss of first playable output | No baseline same-job post-render LLM rerender; publish limited output | Provider/render call-count tests |
| Registry scope accidentally expands to hundreds of unrelated errors | Sprint 1 becomes risky and bilingual metadata becomes low-value | Inventory only outcome/promotion/fallback/retry/repair/audit/activity/workspace codes; record stable exclusions | Registry coverage reports zero in-scope omissions and explicit exclusions |
| Registry centralization changes historical meaning | Audit inconsistency | Preserve raw codes/versioned reports; no stored reclassification | v1 fixture compatibility tests |
| Creative limitation is mislabeled technical or vice versa | A technical defect could be published or a valid output withheld | Detector-owned technical classification plus registry review | Creative-only publishes; technical and mixed blockers remain withheld |
| Strict QA and delivery are conflated | UI or operators interpret published limited output as strict-approved | Store and present strict verdict and delivery decision independently | API/browser tests show strict block plus published limitation truthfully |
| FFMPEGA parameter contracts drift | Strict output passes but runtime rejects, or valid effects are blocked | Pin authoritative contracts; share Pydantic models between schema and runtime; stop if authority is missing | Contract drift and every-effect focused tests pass |
| FFMPEGA strict schema exceeds provider subset or size | Provider rejects the effect schema | Two stable schemas, provider-subset validator, progressive boundary activation | Mock rejection is fail-closed; live gate passes before enablement |
| Fallback reduces unaffected quality | User receives unnecessarily generic output | Operation-local fallback and quality-floor comparison | Plan diff tests preserve unaffected operations |
| Repair duplicates after worker interruption | Additional cost and latency | Fingerprinted `visual_repair` and `plan_repair` checkpoints and idempotent resume | Recovery tests show no repeated stage call |
| JSON-backed agentic audit becomes too slow | Agents cannot query defect trends within bounded latency | Bounded indexed job selection, compact outcome/repair documents, measured thresholds, future normalized backfill gate | `audit defects` limit/latency tests remain within documented bounds |
| PR #11 merged without formal GitHub checks | Baseline confidence depends on recorded manual evidence | Preserve the PR validation record and rerun focused regressions in every sprint | New sprint gates pass from merge SHA `dae7366` |
| Semantic QA grades work produced by the same model family | Self-evaluation bias can overstate creative quality | Keep deterministic validation authoritative and directly inspect rendered output against prompt intent | Same-session output gate passes independently of semantic verdict |
| Healthy dependency outage is mislabeled as an acceptable creative limitation | The MVP appears successful while creative analysis never ran | Separate dependency-unavailable, source-constraint, creative-gap, and technical classes | Healthy-path analysis-unavailable count is zero |
| FFMPEGA service, queue, or shared-path failure loses the playable video | Agentic finishing reduces reliability | Preserve/register native render first, constrain paths, and fail back without semantic repair | Failure canaries retain playable native output and exact audit code |
| More tokens/effects are mistaken for better editing | Cost and visual complexity rise without user value | Require direct quality lift and omit no-op or over-decoration effects | Latest-prompt output improves while call counts remain bounded |
| Same-session evidence is presented as universal proof | Reliability/quality claim does not generalize to unseen sources | Scope Sprint 7 claims to the uploaded video and tested prompts; continue reporting the separate Wilson gate | PR and docs name the tested source/prompt scope and untested boundaries |

## Rollback Plan

1. **Before Sprint 7 implementation**: Present the Sprint 7 title and obtain
   explicit user approval. If the branch no longer descends from verified merge
   SHA `dae7366`, stop and reconcile the base without rewriting history.
2. **Sprint 1**: Revert registry routing; stored outcomes remain unchanged.
3. **Sprint 2**: Remove strict boundaries in reverse order or set global mode to
   `json_object`; typed schemas and tests can remain dormant.
4. **Sprint 3**: Set repair mode to `off`; registry and strict transport remain.
5. **Sprint 4**: Disable predictive QA, repair orchestration, and both repair
   checkpoint reads; retain evidence for diagnosis.
6. **Sprint 5**: Restore delivery policy to `qa_enforced`, hide repair UI, remove
   the additive audit command, and stop writing repair reports; keep v1/v2
   readers and historical evidence.
7. **Sprint 6/production**: Disable repair first, restore `qa_enforced` delivery,
   remove strict boundaries in reverse order, restore JSON object mode, disable
   UI, and roll back the image only after confirming database and artifact
   compatibility.
8. **Sprint 7/quality proof**: Disable FFMPEGA first and retain the native
   renderer, then disable semantic QA. Remove their strict boundaries only when
   provider compatibility is implicated. Restore repair/delivery policy only if
   quality regresses, and roll back the image after compatibility review.
9. Never delete checkpoint, registry-version, outcome, repair, semantic-QA,
   effect, or audit evidence
   as part of normal rollback.

## Execution Order

1. Preserve the completed and committed Sprint 1-6 evidence without rewriting
   history.
2. Present the Sprint 7 title and obtain explicit approval before implementation
   or production execution.
3. Reconfirm the production image, database revision, provider route/model,
   strict boundaries, FFMPEGA service, private-session authorization, backup,
   health, and rollback baseline.
4. Trace and debug the complete current-session path, implementing Task 7.1 and
   the offline/test portions of Tasks 7.2-7.3 before changing production flags.
5. Run focused, full local, and connected-PostgreSQL validation; commit no
   production/private evidence.
6. Enable semantic QA first, run and record its authorized sanitized canary, and
   roll back immediately if its gate fails.
7. Enable FFMPEGA second, run service/failure/native-fallback canaries, and roll
   back immediately if its gate fails.
8. Run iterative current-session production QA with the latest prompt. Fix and
   retry defects, require two consecutive passing outputs after the final fix,
   and use at most two targeted prompt variants when residual-risk evidence
   requires them.
9. Complete release, privacy, browser, database, health, backup/restore,
   observability, and rollback review.
10. Create exactly one Sprint 7 implementation commit; keep any post-gate
    production fix separate and explicit.
11. Push the branch and update the existing fork PR with sanitized evidence only
    after the Sprint 7 gate passes.
12. Scope the trustworthy-MVP result to the current uploaded source and tested
    prompts; never describe a partial canary, model self-score, or planned result
    as universal proof.

## Completion Checklist

- [x] PR #11 is cleaned, pushed, merged, and verified against the expected refs.
- [x] The new implementation branch starts from merged fork `main`.
- [x] Every Registry v1 outcome/promotion/fallback/retry/repair/audit/activity/
  workspace code is registered and bilingual; exclusions are inventoried.
- [x] Unknown codes fail closed and cannot trigger LLM repair.
- [x] Strict schema support is proven for the exact 9Router route/model.
- [x] Every core and FFMPEGA 9Router JSON boundary uses a stable strict schema
  when progressively enabled.
- [x] FFMPEGA has authoritative effect-specific schemas for both agentic and full
  deterministic allowlists, with no arbitrary model-generated parameter keys.
- [x] Application semantic validation remains authoritative.
- [x] At most one visual repair and one plan repair occur per job; FFMPEGA adds no
  repair call.
- [x] Advisory-only predictive findings make zero semantic repair calls.
- [x] Every LLM-repairable code has success and failure/fallback coverage.
- [x] Every non-LLM code has coverage proving no semantic repair call occurs.
- [x] Failed repair preserves original real defect codes.
- [x] Fallback preserves unaffected quality and a technically valid output.
- [x] Every technical-pass candidate is published with truthful limitations even
  when strict creative QA remains blocked.
- [x] Strict QA verdict and delivery decision remain separate and auditable.
- [x] `audit defects` provides bounded agent-friendly queries without a migration.
- [x] Historical v1 outcomes remain readable and immutable.
- [ ] Every sprint has passed its validation gate; Sprint 7 retains the
  authenticated-browser and quantified-effect-lift gates documented above.
- [x] Every sprint has exactly one sprint-specific commit; post-gate release fixes
  remain separate and explicit.
- [ ] Final integration, security, browser, operational, and rollback checks
  pass; production browser authentication remains operator-gated.
- [x] Residual risks and statistical claim limits are current.
- [x] Sprint 7 production semantic QA passes strict and semantic validation.
- [x] Sprint 7 typed FFMPEGA execution and native fallback pass production
  canaries without losing a playable output.
- [x] Sprint 7 latest-prompt same-session output passes twice consecutively after
  the final fix; targeted prompt variants are used only when required.
- [x] The trustworthy-MVP claim remains scoped to the tested source/prompts until
  the separate 99% playable-output confidence gate passes.
