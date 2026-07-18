# Plan: General-Purpose Agentic Video Editing For The Remote MVP

**Generated**: 2026-07-18
**Status**: Execution in progress; sprint gates are recorded in Git history
**Estimated Complexity**: High

## Overview

Upgrade the remote-only social-clips MVP from clip selection plus fixed center
crop into a general-purpose, capability-aware video editor. The upgraded flow
will preserve the strong transcript and clip-selection behavior while adding
timestamped visual evidence, intelligent portrait composition, timeline-level
creative primitives, conditional generated assets, render preflight, and
creative conformance evidence.

The implementation will adapt selected algorithms and artifact patterns from
`/home/loldlm/python_projects/video-editing-skill`, but it will not import that
repository as a runtime service or copy its CLI/file-state architecture. The
remote MVP will continue to use PostgreSQL as authoritative state, job-scoped
media storage, direct Mistral STT, approved 9Router text/vision/image routes,
and deterministic CPU FFmpeg rendering.

The target flow is:

```text
source video + user prompt
    -> transcript + deterministic scene boundaries
    -> timestamped remote visual understanding
    -> bounded clip selection
    -> capability-aware per-clip edit plan
    -> plan preflight and conditional asset acquisition
    -> deterministic timeline compositor
    -> structural, rhythm, and creative conformance reports
    -> registered outputs and PostgreSQL-backed audit evidence
```

Every sprint below must leave a runnable, independently verifiable increment.
The legacy renderer remains available behind a kill switch until the final
rollout gate completes.

## Scope

- **In scope**:
  - The remote-only social-clips profile under `src/open_storyline/mvp/`,
    `mvp_fastapi.py`, `web/mvp.html`, `Dockerfile.remote`, and its tests/docs.
  - Versioned visual-understanding, edit-plan, asset, render-execution, and QA
    artifacts.
  - General editing primitives: scene-aware cuts, tracked crop, fit/letterbox,
    focus zoom, source cutaways, image overlays, PiP, text emphasis, hard cut,
    fade, and bounded crossfade.
  - Conditional 9Router image generation when the validated edit plan identifies
    a specific visual gap and the job policy permits generated assets.
  - A later, disabled-by-default Pexels adapter for stock photos/videos.
  - Shadow planning, canary rendering, creative eval fixtures, observability,
    security checks, rollout, and rollback.
- **Out of scope**:
  - Refactoring or merging the full local LangChain/MCP agent profile.
  - Local YOLO, local ASR, local embeddings, local scene models, or any other
    local inference in the remote image.
  - Generated video providers, voice cloning, advanced color grading, film/MV
    editing, frame-by-frame keyframing, or replacing a professional NLE.
  - Multi-user accounts, billing, per-user quotas, or a human approval pause
    state inside the job queue.
  - Replacing the approved text/vision model, image model, or direct Mistral STT
    route as part of this roadmap.
  - Deploying to production, calling paid/live providers, or uploading private
    production media without separate implementation-time authorization.
- **Fixed decisions**:
  - The work targets the remote MVP only. Shared pure utilities may be reused,
    but full-agent public nodes, prompts, containers, and runtime behavior stay
    unchanged.
  - The engine remains niche-neutral. Trading, interviews, tutorials, cooking,
    product demos, and other domains enter through the user prompt and visual
    evidence, not through niche-specific Python branches.
  - PostgreSQL remains authoritative for jobs, request/result state, artifacts,
    ordered events, audit evidence, reviews, holds, and retention.
  - Media stays under `outputs/mvp_jobs/<job_id>`; JSON/SRT plans and reports are
    registered and ingested through the existing audit path.
  - Core editing is deterministic CPU FFmpeg. FFMPEGA may remain an optional
    finishing pass, but it cannot be required for framing, shot composition,
    asset insertion, or core plan execution.
  - Generated assets are conditional, not automatic for every job. No image
    provider call is allowed unless the validated edit plan contains a bounded
    asset request with a timeline purpose and the job's asset policy permits it.
  - If no generated asset is requested, the job must make zero image-generation
    calls and use the source video only.
  - 9Router-generated images are implemented before Pexels. Pexels remains
    disabled by default and is never a silent fallback for image-generation
    failure.
  - Selected provider failure remains fail-closed with sanitized attempt
    metadata. The planner may choose a source-only composition before execution,
    but the runtime must not silently switch providers after a request starts.
  - Existing HTTP routes, authentication/CSRF behavior, job states, status/error
    shapes, artifact path safety, and retention policy remain compatible.
  - The user has authorized adapting the open-source `video-editing-skill`
    repository. Record the source paths and preserve appropriate attribution;
    do not copy unrelated platform/provider-specific code wholesale.
- **Assumptions**:
  - Existing 18-25 second short duration constraints and maximum clip limits
    remain unchanged during this roadmap.
  - The approved `cx/gpt-5.6-sol` route continues to accept ordered image inputs
    and JSON-object responses through the current 9Router adapter.
  - The approved `cx/gpt-5.5-image` route is available only when generated assets
    are enabled and the provider release gate passes.
  - The deployed FFmpeg build exposes the documented CPU filters required by the
    selected capability set.
  - Additive job controls can be stored in existing `request_data`/`result_data`
    JSONB and versioned artifacts; no database migration is expected unless
    implementation discovery proves a relational query or constraint is needed.
  - Private production media, transcripts, prompts, and contact sheets will not
    be committed as fixtures. Production comparison is an operator-only gate.

## Success Measures

- The planner never emits unsupported renderer operations.
- Every input frame supplied to vision has a stable frame ID and source timestamp.
- Every agentic output has an `edit_plan.json`, `edit_preflight.json`, and
  `render_execution.json` that explain what was planned and executed.
- Center crop is no longer the implicit default. Every scene records an explicit
  composition strategy and any fallback.
- Crop changes are bounded and smoothed; no unexplained jump between adjacent
  tracking segments is permitted.
- A requested and successfully acquired asset appears in the intended timeline
  window and in provenance/conformance evidence.
- Jobs whose plan does not request an asset perform no generated-image or stock
  calls.
- Structural QA catches black/frozen/silent/invalid outputs; creative QA reports
  hook activity, visual holds, plan execution, and fallback usage without
  rewriting a completed render into a failed job.
- The legacy renderer remains selectable until canary evidence supports making
  agentic mode the MVP UI default.

## Named Resources

- **Project instructions**:
  - `AGENTS.md`
  - `docs/agent-engineering.md`
  - `docs/mvp/architecture.md`
  - `docs/mvp/audit-and-database.md`
  - `docs/mvp/implementation-history.md`
- **Current implementation files**:
  - `src/open_storyline/mvp/pipeline.py`
  - `src/open_storyline/mvp/shorts.py`
  - `src/open_storyline/mvp/render.py`
  - `src/open_storyline/mvp/ffmpega.py`
  - `src/open_storyline/mvp/ninerouter.py`
  - `src/open_storyline/mvp/jobs.py`
  - `src/open_storyline/mvp/models.py`
  - `src/open_storyline/mvp/audit.py`
  - `src/open_storyline/mvp/api.py`
  - `src/open_storyline/config.py`
  - `src/open_storyline/utils/remote_image.py`
  - `src/open_storyline/utils/generated_media.py`
  - `mvp_fastapi.py`
  - `web/mvp.html`
  - `config.toml`
  - `.env.mvp.example`
  - `.env.kamal.example`
  - `.kamal/secrets.example`
  - `config/deploy.yml`
  - `Dockerfile.remote`
  - `requirements-remote.txt`
- **Planned implementation files**:
  - `src/open_storyline/mvp/edit_plan.py`
  - `src/open_storyline/mvp/preflight.py`
  - `src/open_storyline/mvp/scene_boundaries.py`
  - `src/open_storyline/mvp/frame_sampling.py`
  - `src/open_storyline/mvp/visual_understanding.py`
  - `src/open_storyline/mvp/prompts.py`
  - `src/open_storyline/mvp/ffmpeg_filters.py`
  - `src/open_storyline/mvp/compositor.py`
  - `src/open_storyline/mvp/assets.py`
  - `src/open_storyline/mvp/creative_qa.py`
  - `src/open_storyline/mvp/stock.py`
  - Exact file consolidation is allowed during implementation when it reduces
    coupling, but the named responsibilities and public artifacts must remain.
- **Reference implementation candidates**:
  - `/home/loldlm/python_projects/video-editing-skill/scripts/scene_boundaries.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/video_understanding.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/smart_reframe.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/edit_preflight.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/render_qa.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/retention_rhythm_qa.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/pipeline_manifest.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/storyboard_assets.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/asset_provenance.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/pip_overlay.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/screen_focus.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/transition_bridge.py`
  - `/home/loldlm/python_projects/video-editing-skill/scripts/stock_material_plan.py`
- **Tests and validation**:
  - Existing: `tests/test_ninerouter.py`, `tests/test_shorts.py`,
    `tests/test_mvp_render.py`, `tests/test_mvp_jobs.py`,
    `tests/test_mvp_audit.py`, `tests/test_mvp_app.py`,
    `tests/test_mvp_sessions.py`, `tests/test_remote_image.py`,
    `tests/test_generated_media.py`, `tests/test_remote_profile.py`,
    `tests/test_kamal_config.py`, and `.qa/web/tests/`.
  - Planned: `tests/test_mvp_edit_plan.py`,
    `tests/test_mvp_preflight.py`, `tests/test_mvp_scene_boundaries.py`,
    `tests/test_mvp_visual_understanding.py`,
    `tests/test_mvp_compositor.py`, `tests/test_mvp_assets.py`,
    `tests/test_mvp_creative_qa.py`, and `tests/test_mvp_stock.py`.
  - Planned synthetic helpers/fixtures: `tests/fixtures/mvp_agentic/` or test-time
    FFmpeg generators that contain no private production media.
- **External documentation**:
  - FFmpeg filter documentation: `https://ffmpeg.org/ffmpeg-filters.html`
  - FFmpeg filtergraph syntax: `https://ffmpeg.org/ffmpeg-filters.html#Filtergraph-syntax-1`
  - Pexels API documentation: `https://www.pexels.com/api/documentation/`
  - Pexels license: `https://www.pexels.com/license/`
  - Pexels official pages were Cloudflare-gated during planning; the implementer
    must re-verify current authentication, rate-limit, download, attribution,
    and license requirements before Sprint 8 code is finalized.
- **Operational resources**:
  - Existing 9Router catalog/live modality checks in `scripts/qa_ninerouter.py`
  - Existing direct Mistral checks in `scripts/qa_mistral_stt.py`
  - `bin/kamal-mvp`, `.kamal/hooks/pre-deploy`, `/health`, and `/up`
  - PostgreSQL backup/restore helpers under `scripts/mvp-postgres-*.sh`
  - Feature flags and provider secrets named in the sprint tasks below
  - Operator-only audit CLI and private production session evidence

## Versioned Artifact Contract

The following job-scoped JSON artifacts form the durable handoff between model
reasoning and deterministic execution:

| Artifact | Purpose | Authoritative source |
| --- | --- | --- |
| `transcript.json` | Existing timestamped speech evidence | Direct Mistral result |
| `scene_boundaries.json` | Deterministic scene intervals | FFmpeg scene scores |
| `visual_understanding.json` | Timestamped regions, tracks, semantic roles, confidence | Validated 9Router vision output |
| `shorts_plan.json` | Bounded clip selection independent of rendering | Validated shorts planner |
| `edit_plan.json` | Per-clip executable composition and optional asset requests | Validated agentic planner |
| `edit_preflight.json` | Capability, timing, reference, asset, and safety readiness | Deterministic validator |
| `asset_manifest.json` | Requested/resolved assets, provenance, hashes, and rights notice | Provider adapters plus validator |
| `render_execution.json` | Exact operations, fallbacks, FFmpeg graph metadata, and outputs | Deterministic compositor |
| `render_qa.json` | Structural encoded-media checks | FFprobe/FFmpeg analysis |
| `retention_rhythm_qa.json` | Hook activity, visual holds, attention gaps, subtitle cadence | Deterministic heuristics |
| `creative_conformance.json` | Plan-versus-execution and optional semantic review | Deterministic checks plus bounded 9Router review |
| `manifest.json` | Backward-compatible aggregate and output index | Pipeline aggregation |

All schemas must include a version, bounded values, finite numbers, and no
absolute secret-bearing paths. Model output is never executable until the
corresponding validator and preflight pass.

## Prerequisites

- Start implementation from a clean or understood worktree and record the base
  commit SHA as the Sprint 1 rollback point.
- Confirm that adapting the named `video-editing-skill` source files remains
  authorized and record attribution/provenance in a small repository document or
  source comments where copied logic is material.
- Verify `.venv/bin/python`, FFmpeg, FFprobe, and the current remote test
  dependencies are available.
- Record a deterministic legacy-render benchmark on the same machine that will
  run compositor comparisons; do not compare timings across unlike hosts.
- Keep all provider tests mocked by default. Live 9Router image/vision and Pexels
  tests require separate authorization, non-production media, and a cost/quota
  review.
- Use a disposable PostgreSQL database whose name starts with
  `openstoryline_test` for connected database evidence.
- Before any production canary, create and verify the documented PostgreSQL
  backup and record the currently deployed application version and rollback
  command.

## Dependency And Parallelization Rules

- Sprint gates are strictly sequential; no task from a later sprint may merge or
  deploy before the current sprint has one validated commit and a recorded
  rollback point.
- Within Sprint 1, schema work is authoritative. Config/API wiring and test
  scaffolding may proceed in parallel only after field names and versions freeze.
- Within Sprint 2, deterministic scene detection and timestamped-frame test
  fixtures may proceed in parallel; remote visual understanding waits for the
  final frame-manifest contract.
- Within Sprint 3, cross-niche prompt fixtures may be prepared in parallel with
  planner implementation, but preflight integration waits for the final plan
  schema.
- Within Sprint 4, safe FFmpeg filter generation and synthetic compositor
  fixtures may proceed in parallel; pipeline activation waits for both.
- Within Sprint 5, individual primitive render tests may proceed in parallel
  after the capability schema freezes; FFMPEGA boundary changes wait for the core
  compositor to render a complete clip independently.
- Within Sprint 6, UI copy/accessibility tests and the mocked generated-asset
  adapter may proceed in parallel after job policy fields freeze; timeline
  insertion waits for adapter/provenance validation.
- Within Sprint 7, deterministic structural/rhythm QA and synthetic fixture
  authoring may proceed in parallel; semantic QA waits for the conformance schema.
- Within Sprint 8, documentation, env/Kamal tests, and browser test updates may
  proceed in parallel after provider/config names freeze; live rollout remains
  the final serialized task and requires separate authorization.

## Sprint 1: Versioned Agentic Contracts And Shadow Controls

**Goal**: Add validated, niche-neutral plan contracts and feature controls
without changing rendered output.

**Dependencies**: Current remote MVP baseline and recorded base commit.

**Tracked scope**: `src/open_storyline/mvp/edit_plan.py`,
`src/open_storyline/mvp/preflight.py`, `src/open_storyline/mvp/prompts.py`,
`src/open_storyline/config.py`, `config.toml`, `src/open_storyline/mvp/jobs.py`,
`src/open_storyline/mvp/api.py`, `src/open_storyline/mvp/pipeline.py`,
`tests/test_mvp_edit_plan.py`, `tests/test_mvp_preflight.py`, and affected
config/API tests.

**Commit**: `feat(mvp): define agentic edit contracts`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_edit_plan.py tests/test_mvp_preflight.py tests/test_mvp_jobs.py tests/test_mvp_app.py -v`
- Create a local job in `shadow` mode and verify legacy video output remains
  byte/structure-equivalent in behavior while versioned placeholder plan and
  preflight artifacts are registered.

**Rollback point**: The pre-sprint commit SHA. Keep
`OPENSTORYLINE_AGENTIC_EDITING_MODE=off` so reverting this sprint has no media
or database-format dependency.

### Task 1.1: Define Renderer Capabilities And Edit-Plan Schemas

- **Location**: `src/open_storyline/mvp/edit_plan.py`
- **Description**: Define versioned, typed contracts for clip-local timeline
  segments, source references, semantic focal targets, layouts, transitions,
  overlays, optional asset requests, and explicit fallbacks. Define a renderer
  capability registry so the planner can request only executable primitives.
- **Dependencies**: Existing `ShortCandidate` bounds and artifact conventions.
- **Acceptance criteria**:
  - Schemas reject non-finite values, out-of-bounds windows, overlaps that violate
    the declared layer model, unsupported operations, excessive event counts,
    invalid region/track references, unknown providers, and unsafe text/path data.
  - The schema contains no trading, platform-specific narrative, or provider
    routing heuristics.
  - Every asset request requires purpose, timeline window, requested kind,
    rationale, and explicit provider/source policy.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_edit_plan.py -v`
- **Rollback**: Remove the new module and its callers; no persisted schema is
  authoritative while the feature mode is off.

### Task 1.2: Add Deterministic Edit Preflight

- **Location**: `src/open_storyline/mvp/preflight.py`, adapted from
  `/home/loldlm/python_projects/video-editing-skill/scripts/edit_preflight.py`
- **Description**: Validate timing, source bounds, capability availability,
  referenced regions/assets, subtitle-safe zones, event budgets, transition
  compatibility, fallback completeness, and output target before rendering.
- **Dependencies**: Task 1.1 schemas.
- **Acceptance criteria**:
  - Produces `edit_preflight.v1` with `ready`, `warn`, or `blocked`, bounded
    findings, stable error codes, and next actions.
  - Blocking findings prevent agentic execution; warnings are persisted but do
    not silently rewrite the plan.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_preflight.py -v`
- **Rollback**: Disable the preflight caller and remove the artifact; legacy
  rendering remains available.

### Task 1.3: Add Additive Job Controls And Feature Modes

- **Location**: `src/open_storyline/config.py`, `config.toml`,
  `src/open_storyline/mvp/api.py`, `src/open_storyline/mvp/jobs.py`,
  `src/open_storyline/mvp/pipeline.py`
- **Description**: Add server mode `off|shadow|render` and additive job request
  fields such as `edit_mode=legacy|agentic` and `asset_policy=off|auto`. Store
  controls in existing `request_data`; do not add a migration unless required by
  a proven query/constraint need.
- **Dependencies**: Tasks 1.1-1.2.
- **Acceptance criteria**:
  - Existing clients that omit new fields continue to create valid jobs.
  - `off` rejects or keeps agentic behavior unavailable explicitly; `shadow`
    plans but renders legacy output; `render` permits validated agentic execution.
  - No provider calls or output changes occur in Sprint 1.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_jobs.py tests/test_mvp_app.py tests/test_update_config.py -v`
  - `PYTHONPATH=src .venv/bin/python -c "from open_storyline.config import load_settings; load_settings('config.toml'); print('config_ok')"`
- **Rollback**: Set the server feature mode to `off`, then revert the sprint
  commit. Existing JSONB rows remain readable because fields are additive.

### Sprint 1 Gate

- [ ] All Sprint 1 tasks complete.
- [ ] Sprint 1 focused validation passes and evidence is recorded.
- [ ] Legacy output behavior is unchanged in `off` and `shadow` modes.
- [ ] Residual schema and compatibility risks are documented.
- [ ] Exactly one Sprint 1 commit is created with the proposed sprint message.
- [ ] The pre-sprint commit SHA is recorded as the rollback point.
- [ ] Sprint 2 has not started before this gate completes.

## Sprint 2: Scene-Aligned Timestamped Visual Evidence

**Goal**: Produce reviewable visual evidence that maps every analyzed frame and
semantic region to source time, while continuing to render legacy output.

**Dependencies**: Sprint 1 gate.

**Tracked scope**: `src/open_storyline/mvp/scene_boundaries.py`,
`src/open_storyline/mvp/frame_sampling.py`,
`src/open_storyline/mvp/visual_understanding.py`,
`src/open_storyline/mvp/prompts.py`, `src/open_storyline/mvp/ninerouter.py`,
`src/open_storyline/mvp/pipeline.py`, `src/open_storyline/config.py`, and
corresponding tests.

**Commit**: `feat(mvp): add timestamped visual evidence`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_scene_boundaries.py tests/test_mvp_visual_understanding.py tests/test_ninerouter.py -v`
- Run a synthetic wide video in shadow mode and inspect
  `scene_boundaries.json` and `visual_understanding.json`; each observation must
  point to a stable frame ID and timestamp.

**Rollback point**: Sprint 1 commit SHA. Set agentic mode to `off` to stop all
new vision work before reverting.

### Task 2.1: Port Deterministic Scene Boundary Detection

- **Location**: `src/open_storyline/mvp/scene_boundaries.py`, adapted from
  `/home/loldlm/python_projects/video-editing-skill/scripts/scene_boundaries.py`
- **Description**: Use FFmpeg scene scores to produce bounded source intervals,
  with minimum-gap deduplication, timeout/error handling, and no local model.
- **Dependencies**: Existing FFmpeg/FFprobe helpers.
- **Acceptance criteria**:
  - Output is deterministic for the same source and parameters.
  - Boundaries remain within media duration, are ordered, and cannot create zero
    or negative scene durations.
  - Excessively dense scene output is capped with an explicit warning.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_scene_boundaries.py -v`
- **Rollback**: Remove scene-boundary generation; frame sampling falls back to
  the existing evenly spaced behavior only in legacy mode.

### Task 2.2: Build Timestamped Frame Sampling

- **Location**: `src/open_storyline/mvp/frame_sampling.py`,
  `src/open_storyline/mvp/render.py`
- **Description**: Replace anonymous frame data URLs in the agentic path with a
  bounded ordered manifest containing frame IDs, exact source timestamps,
  scene IDs, dimensions, extraction reason, and in-memory image bytes/data URLs.
  Sample scene openings/midpoints plus bounded uniform coverage.
- **Dependencies**: Task 2.1.
- **Acceptance criteria**:
  - The textual vision payload lists frame IDs/timestamps in the exact order of
    attached image inputs.
  - Temporary frame files remain under job work storage or memory and are
    removed by existing terminal cleanup.
  - Frame count, dimensions, and encoded bytes are bounded by configuration.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_visual_understanding.py tests/test_mvp_render.py -v`
- **Rollback**: Disable agentic mode and restore the old `extract_frame_data_urls`
  caller for legacy planning.

### Task 2.3: Add Remote Visual-Understanding Planner

- **Location**: `src/open_storyline/mvp/visual_understanding.py`,
  `src/open_storyline/mvp/prompts.py`, `src/open_storyline/mvp/pipeline.py`
- **Description**: Ask the approved 9Router model for bounded regions, tracklets,
  semantic roles, scene summaries, salience, visibility, and confidence. Adapt
  the artifact structure from `video_understanding.py`, but exclude local YOLO
  and model-specific class IDs.
- **Dependencies**: Task 2.2 and current `NineRouterClient`.
- **Acceptance criteria**:
  - Server validation rejects observations that reference unknown frames, invalid
    normalized boxes, unbounded text, invalid confidence, or impossible timing.
  - Region roles remain general (`speaker`, `screen`, `text`, `object`,
    `demonstration_target`, `background`) and permit prompt-supplied context.
  - Sanitized JSON evidence is registered and ingested; raw frame bytes and
    provider bodies are not persisted in audit/log state.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_visual_understanding.py tests/test_ninerouter.py tests/test_mvp_audit.py -v`
- **Rollback**: Stop the visual-understanding stage with feature mode `off` and
  revert to transcript plus existing anonymous frames for legacy planning.

### Sprint 2 Gate

- [ ] All Sprint 2 tasks complete.
- [ ] Scene/frame/vision schema and failure-path tests pass.
- [ ] Shadow-mode artifacts contain timestamped evidence and no private frame
  bytes in PostgreSQL/logs.
- [ ] Vision call count and payload bounds are recorded.
- [ ] Exactly one Sprint 2 commit is created with the proposed sprint message.
- [ ] The Sprint 1 commit SHA is recorded as the rollback point.
- [ ] Sprint 3 has not started before this gate completes.

## Sprint 3: Capability-Aware Per-Clip Edit Planning

**Goal**: Produce a validated executable shot plan for every selected short,
without changing rendered output outside an explicit local experiment.

**Dependencies**: Sprint 2 gate.

**Tracked scope**: `src/open_storyline/mvp/shorts.py`,
`src/open_storyline/mvp/edit_plan.py`, `src/open_storyline/mvp/preflight.py`,
`src/open_storyline/mvp/prompts.py`, `src/open_storyline/mvp/pipeline.py`,
`tests/test_shorts.py`, `tests/test_mvp_edit_plan.py`, and
`tests/test_mvp_preflight.py`.

**Commit**: `feat(mvp): plan capability-aware video shots`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_shorts.py tests/test_mvp_edit_plan.py tests/test_mvp_preflight.py -v`
- For synthetic trading, interview, tutorial, and product-demo prompts, inspect
  plans and verify that the same schema expresses different visual priorities
  without niche-specific code.

**Rollback point**: Sprint 2 commit SHA. Shadow mode remains the default.

### Task 3.1: Preserve Clip Selection As A Separate Decision

- **Location**: `src/open_storyline/mvp/shorts.py`,
  `src/open_storyline/mvp/pipeline.py`
- **Description**: Keep bounded clip selection independent from shot composition
  and register `shorts_plan.json` before detailed editing. Add source timestamps
  and evidence IDs needed by the next planner without weakening existing duration,
  overlap, finite-score, or output-count validation.
- **Dependencies**: Sprint 2 evidence artifacts.
- **Acceptance criteria**:
  - Existing valid clip behavior remains compatible.
  - Detailed editing cannot expand a clip outside its validated source bounds.
  - `shorts_plan.json` is independently auditable.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_shorts.py tests/test_mvp_audit.py -v`
- **Rollback**: Keep the existing manifest-embedded plan and remove the separate
  detailed-planning handoff.

### Task 3.2: Implement The General-Purpose Edit Planner

- **Location**: `src/open_storyline/mvp/edit_plan.py`,
  `src/open_storyline/mvp/prompts.py`
- **Description**: Produce per-clip scene cards using transcript windows, user
  instructions, scene boundaries, visual regions/tracks, subtitle constraints,
  and renderer capabilities. Each card must declare visual intent, source/layout,
  focus target, transition, overlays, optional asset request, and fallback.
- **Dependencies**: Task 3.1 and Sprint 1 contracts.
- **Acceptance criteria**:
  - Prompt context can prioritize faces, screens, demonstrated objects, charts,
    motion, or source composition without hardcoded domain branches.
  - Planner output includes evidence references and a reason for every nontrivial
    composition decision.
  - Unsupported or malformed output fails before rendering with sanitized errors.
  - Asset requests are absent when source evidence can satisfy the requested
    visual intent; no generic rule forces B-roll or images into every clip.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_edit_plan.py -v`
- **Rollback**: Disable detailed planning and continue legacy clip rendering.

### Task 3.3: Run Preflight And Persist Shadow Evidence

- **Location**: `src/open_storyline/mvp/preflight.py`,
  `src/open_storyline/mvp/pipeline.py`, `src/open_storyline/mvp/jobs.py`
- **Description**: Write/register `edit_plan.json` and `edit_preflight.json` in
  shadow mode, record planner/prompt schema versions and sanitized attempts, and
  expose artifact links through existing job responses.
- **Dependencies**: Tasks 3.1-3.2.
- **Acceptance criteria**:
  - Shadow planning never changes video output or calls asset providers.
  - Blocked plans remain visible as audit evidence while legacy rendering can
    complete only when shadow policy explicitly allows comparison.
  - Artifact names remain job-scoped and bundle-compatible.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_edit_plan.py tests/test_mvp_preflight.py tests/test_mvp_jobs.py tests/test_mvp_audit.py -v`
- **Rollback**: Set mode `off`; additive artifacts expire through existing audit
  retention and do not affect live state reconstruction.

### Sprint 3 Gate

- [ ] All Sprint 3 tasks complete.
- [ ] Cross-niche schema fixtures pass without niche-specific Python logic.
- [ ] Shadow-mode output remains legacy-rendered and provider-free.
- [ ] Plan/preflight artifact privacy and audit ingestion are verified.
- [ ] Exactly one Sprint 3 commit is created with the proposed sprint message.
- [ ] The Sprint 2 commit SHA is recorded as the rollback point.
- [ ] Sprint 4 has not started before this gate completes.

## Sprint 4: Intelligent Portrait Reframing

**Goal**: Replace implicit center crop in agentic render mode with explicit,
smooth, scene-aware composition while preserving audio/subtitle sync.

**Dependencies**: Sprint 3 gate and accepted shadow plans.

**Tracked scope**: `src/open_storyline/mvp/ffmpeg_filters.py`,
`src/open_storyline/mvp/compositor.py`, `src/open_storyline/mvp/render.py`,
`src/open_storyline/mvp/pipeline.py`, `tests/test_mvp_compositor.py`,
`tests/test_mvp_render.py`, and synthetic video helpers.

**Commit**: `feat(mvp): render intelligent portrait reframes`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_compositor.py tests/test_mvp_render.py -v`
- Render a wide synthetic source containing separated colored subjects and verify
  crop, fit/letterbox, target visibility, output dimensions, duration, and audio
  sync using FFprobe and pixel/region assertions.

**Rollback point**: Sprint 3 commit SHA plus
`OPENSTORYLINE_AGENTIC_EDITING_MODE=shadow`.

### Task 4.1: Build Safe FFmpeg Filter Primitives

- **Location**: `src/open_storyline/mvp/ffmpeg_filters.py`
- **Description**: Generate filtergraphs from typed plans for trim, setpts,
  crop, scale, pad, concat, subtitle placement, and bounded interpolation. Never
  accept raw FFmpeg fragments from the model.
- **Dependencies**: Sprint 1 capability registry.
- **Acceptance criteria**:
  - All paths, text, dimensions, expressions, and labels are generated or escaped
    server-side.
  - Filtergraphs are bounded in input count, segment count, and command length.
  - Unit tests cover hostile strings, invalid dimensions, unsupported filters,
    and missing source/audio streams.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_compositor.py -v`
- **Rollback**: Remove agentic filtergraph generation and keep legacy `-vf` path.

### Task 4.2: Adapt Smart Reframe Into A Smoothed Composer

- **Location**: `src/open_storyline/mvp/compositor.py`, adapted conceptually from
  `/home/loldlm/python_projects/video-editing-skill/scripts/smart_reframe.py`
- **Description**: Resolve semantic targets into crop/fit/letterbox segments with
  temporal smoothing, hysteresis, maximum crop velocity, safe margins, multi-box
  union, and explicit fallback reporting.
- **Dependencies**: Task 4.1 and visual tracks from Sprint 2.
- **Acceptance criteria**:
  - Adjacent target movement does not cause unbounded crop jumps.
  - Wide groups/screens can select fit/letterbox rather than destructive crop.
  - Center fallback is explicit in `render_execution.json` with reason and count.
  - Subtitle-safe areas and requested protected regions remain visible.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_compositor.py -v`
- **Rollback**: Switch server mode to `shadow`; legacy center crop remains intact.

### Task 4.3: Execute Agentic Reframe Plans In One Encode

- **Location**: `src/open_storyline/mvp/render.py`,
  `src/open_storyline/mvp/pipeline.py`
- **Description**: Add an agentic render path that consumes only preflight-ready
  plans and produces `render_execution.json`. Preserve existing subtitle timing,
  audio mapping, output codecs, output names, and artifact registration.
- **Dependencies**: Tasks 4.1-4.2.
- **Acceptance criteria**:
  - Agentic render mode produces valid 1080x1920 H.264/AAC outputs with expected
    duration and subtitles.
  - Rendering uses one final video encode per output before optional FFMPEGA.
  - On the same benchmark host, agentic reframe wall time is no worse than
    `max(1.5 * legacy_time, legacy_time + 30 seconds)` for the representative
    25-second synthetic input; any exception requires explicit risk acceptance.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_render.py tests/test_mvp_compositor.py -v`
  - Record legacy/agentic benchmark commands, source hash, FFmpeg version, and
    timings in implementation evidence, not committed private artifacts.
- **Rollback**: Set mode to `shadow` or `off`; revert the sprint commit if no
  agentic jobs are active. Existing completed videos remain downloadable.

### Sprint 4 Gate

- [ ] All Sprint 4 tasks complete.
- [ ] Synthetic subject-visibility, smoothness, duration, and subtitle tests pass.
- [ ] CPU benchmark stays within the defined bound or has explicit acceptance.
- [ ] The legacy kill switch is exercised successfully.
- [ ] Exactly one Sprint 4 commit is created with the proposed sprint message.
- [ ] The Sprint 3 commit SHA is recorded as the rollback point.
- [ ] Sprint 5 has not started before this gate completes.

## Sprint 5: Timeline-Level Creative Primitives

**Goal**: Make hooks, cutaways, zooms, PiP, emphasis, and transitions executable
per scene instead of applying one global finishing effect.

**Dependencies**: Sprint 4 gate.

**Tracked scope**: `src/open_storyline/mvp/edit_plan.py`,
`src/open_storyline/mvp/preflight.py`, `src/open_storyline/mvp/ffmpeg_filters.py`,
`src/open_storyline/mvp/compositor.py`, `src/open_storyline/mvp/render.py`,
`src/open_storyline/mvp/ffmpega.py`, `src/open_storyline/mvp/pipeline.py`, and
compositor/preflight tests.

**Commit**: `feat(mvp): add timeline creative primitives`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_edit_plan.py tests/test_mvp_preflight.py tests/test_mvp_compositor.py tests/test_ffmpega.py -v`
- Render a synthetic plan containing a source cutaway, focus zoom, PiP, text
  emphasis, hard cut, fade, and bounded crossfade; verify timing and layer order.

**Rollback point**: Sprint 4 commit SHA; capability registry can disable every
new primitive independently while retaining smart reframe.

### Task 5.1: Extend The Capability Registry And Plan Validator

- **Location**: `src/open_storyline/mvp/edit_plan.py`,
  `src/open_storyline/mvp/preflight.py`
- **Description**: Add typed source cutaway, focus zoom, PiP, still/image overlay,
  text emphasis, hard cut, fade, and xfade operations. Adapt useful schemas from
  `pip_overlay.py`, `screen_focus.py`, and `transition_bridge.py` without their
  platform/provider assumptions.
- **Dependencies**: Sprint 4 plan/compositor contracts.
- **Acceptance criteria**:
  - Layer order, time windows, opacity/scale/position, transition duration, and
    protected subtitle zones are bounded.
  - Unsupported combinations produce preflight blockers rather than partial
    execution.
  - Hook effects occur only when requested or justified by the plan; the engine
    does not force a generic effect cadence.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_edit_plan.py tests/test_mvp_preflight.py -v`
- **Rollback**: Remove capabilities from the registry so old plans cannot select
  them; versioned artifacts remain readable.

### Task 5.2: Implement Deterministic Layered Composition

- **Location**: `src/open_storyline/mvp/ffmpeg_filters.py`,
  `src/open_storyline/mvp/compositor.py`, `src/open_storyline/mvp/render.py`
- **Description**: Execute the new primitives in a bounded filtergraph with
  deterministic z-order and timeline windows. Use generated expressions only;
  do not permit arbitrary filter strings or shell fragments.
- **Dependencies**: Task 5.1.
- **Acceptance criteria**:
  - Output has correct dimensions, duration, audio continuity, subtitle sync,
    and no black transition gaps outside the plan.
  - PiP/focus transitions respect reduced motion in UI previews; final video
    motion is controlled by the user's plan rather than browser preferences.
  - `render_execution.json` records every operation and fallback.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_compositor.py tests/test_mvp_render.py -v`
- **Rollback**: Disable individual capabilities or return to shadow/legacy mode.

### Task 5.3: Bound FFMPEGA To Optional Finishing

- **Location**: `src/open_storyline/mvp/ffmpega.py`,
  `src/open_storyline/mvp/pipeline.py`, `tests/test_ffmpega.py`
- **Description**: Preserve FFMPEGA as an optional post-render finishing layer,
  but prevent it from duplicating or overriding timeline composition. Record its
  plan separately and keep deterministic allowlists/path controls.
- **Dependencies**: Task 5.2.
- **Acceptance criteria**:
  - Agentic timeline output is complete when FFMPEGA is disabled.
  - FFMPEGA receives only the finished clip and allowed finishing effects.
  - Failure behavior, shared-path validation, and secret sanitization remain
    covered.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_ffmpega.py -v`
- **Rollback**: Set `OPENSTORYLINE_FFMPEGA_ENABLED=false`; no core feature is lost.

### Sprint 5 Gate

- [ ] All Sprint 5 tasks complete.
- [ ] Every new primitive has schema, invalid-input, render, and fallback tests.
- [ ] Timeline execution is complete with FFMPEGA disabled.
- [ ] Filtergraph complexity/performance evidence is recorded.
- [ ] Exactly one Sprint 5 commit is created with the proposed sprint message.
- [ ] The Sprint 4 commit SHA is recorded as the rollback point.
- [ ] Sprint 6 has not started before this gate completes.

## Sprint 6: Conditional 9Router Generated Assets

**Goal**: Generate and insert still-image assets only when the validated edit
plan identifies a visual gap and the job policy allows it.

**Dependencies**: Sprint 5 gate and passing 9Router image release gate in the
target environment before live use.

**Tracked scope**: `src/open_storyline/mvp/assets.py`,
`src/open_storyline/mvp/edit_plan.py`, `src/open_storyline/mvp/preflight.py`,
`src/open_storyline/mvp/pipeline.py`, `src/open_storyline/mvp/compositor.py`,
`src/open_storyline/utils/remote_image.py`,
`src/open_storyline/utils/generated_media.py`, `src/open_storyline/config.py`,
`web/mvp.html`, env/deploy files, and asset/API/browser tests.

**Commit**: `feat(mvp): insert conditional generated assets`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_assets.py tests/test_remote_image.py tests/test_generated_media.py tests/test_mvp_edit_plan.py tests/test_mvp_preflight.py tests/test_mvp_app.py -v`
- With a mocked provider, run one plan with no asset request and prove zero image
  calls; run one justified request and verify generation, registration,
  provenance, timeline insertion, and conformance metadata.

**Rollback point**: Sprint 5 commit SHA plus
`OPENSTORYLINE_GENERATED_ASSETS_ENABLED=false`.

### Task 6.1: Define Conditional Asset Policy And Limits

- **Location**: `src/open_storyline/mvp/edit_plan.py`,
  `src/open_storyline/mvp/preflight.py`, `src/open_storyline/config.py`,
  `config.toml`
- **Description**: Add server-side generated-asset enablement and maximum counts,
  plus job `asset_policy=off|auto`. Require each generated request to explain the
  source visual gap, intended timeline purpose, prompt, orientation, and fallback
  before execution.
- **Dependencies**: Sprint 5 still/image overlay capability.
- **Acceptance criteria**:
  - No asset request or `asset_policy=off` results in zero provider calls.
  - Requested count is bounded by both job and server caps; server caps win.
  - Prompts reject excessive length and receive the existing originality/rights
    suffix.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_assets.py tests/test_mvp_edit_plan.py tests/test_mvp_preflight.py -v`
- **Rollback**: Disable generated assets globally; source-only plans continue.

### Task 6.2: Add A Job-Scoped Generated-Asset Adapter

- **Location**: `src/open_storyline/mvp/assets.py`,
  `src/open_storyline/utils/remote_image.py`,
  `src/open_storyline/utils/generated_media.py`,
  `src/open_storyline/mvp/pipeline.py`
- **Description**: Reuse the existing approved 9Router image client through a
  remote-MVP adapter that writes only under the current job, validates binary
  type/size, registers each asset and provenance JSON, records sanitized attempts,
  and cleans partial batches transactionally.
- **Dependencies**: Task 6.1.
- **Acceptance criteria**:
  - Only `cx/gpt-5.5-image` is accepted and catalog availability is checked.
  - PNG/JPEG/WebP bytes, size limits, hashes, prompt hashes, model IDs, and rights
    notice are retained; credentials and raw provider responses are not.
  - Partial generation failure deletes partial media and fails closed without a
    Pexels/local fallback.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_assets.py tests/test_remote_image.py tests/test_generated_media.py tests/test_mvp_jobs.py tests/test_mvp_audit.py -v`
- **Rollback**: Disable generated assets and revert the adapter. Registered JSON
  evidence remains compatible with retention; generated media follows existing
  media expiry.

### Task 6.3: Insert Generated Assets And Expose User Policy

- **Location**: `src/open_storyline/mvp/compositor.py`,
  `src/open_storyline/mvp/pipeline.py`, `src/open_storyline/mvp/api.py`,
  `web/mvp.html`
- **Description**: Resolve planned asset IDs into full-screen cutaways or bounded
  overlays with deterministic duration/motion. Add an accessible job control and
  explanation that automatic generation occurs only when the planner identifies
  a visual need; include a pre-publish rights-review notice.
- **Dependencies**: Task 6.2.
- **Acceptance criteria**:
  - Requested assets appear in their declared timeline windows and are listed in
    `asset_manifest.json` and `render_execution.json`.
  - Jobs with no requested asset render exactly through source-only composition.
  - New controls are keyboard accessible, labelled, responsive, and do not expose
    provider keys to the browser.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_assets.py tests/test_mvp_compositor.py tests/test_mvp_app.py -v`
  - `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:mobile`
- **Rollback**: Disable generated assets, keep agentic source-only rendering, and
  hide/disable the UI control without changing existing job rows.

### Sprint 6 Gate

- [ ] All Sprint 6 tasks complete.
- [ ] Zero-call behavior is proven for plans without asset requests.
- [ ] Generation failure, partial cleanup, provenance, and secret-redaction tests pass.
- [ ] UI policy and rights notice pass focused desktop/mobile accessibility review.
- [ ] Exactly one Sprint 6 commit is created with the proposed sprint message.
- [ ] The Sprint 5 commit SHA is recorded as the rollback point.
- [ ] Sprint 7 has not started before this gate completes.

## Sprint 7: Structural, Rhythm, And Creative Conformance

**Goal**: Make creative completeness measurable and build cross-niche regression
evidence before changing the default renderer.

**Dependencies**: Sprint 6 gate.

**Tracked scope**: `src/open_storyline/mvp/creative_qa.py`,
`src/open_storyline/mvp/audit.py`, `src/open_storyline/mvp/pipeline.py`,
`src/open_storyline/mvp/compositor.py`, `tests/test_mvp_creative_qa.py`,
`tests/test_mvp_audit.py`, synthetic fixtures/helpers, and docs.

**Commit**: `test(mvp): add creative conformance gates`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_creative_qa.py tests/test_mvp_audit.py tests/test_mvp_render.py tests/test_mvp_compositor.py -v`
- Generate a review bundle containing all QA artifacts for at least five
  synthetic cross-niche scenarios and inspect deterministic findings.

**Rollback point**: Sprint 6 commit SHA. Semantic QA and strict QA remain
independently disableable; completed job state is never rewritten.

### Task 7.1: Port Structural Render QA

- **Location**: `src/open_storyline/mvp/creative_qa.py`, adapted from
  `/home/loldlm/python_projects/video-editing-skill/scripts/render_qa.py`
- **Description**: Add bounded FFprobe/FFmpeg checks for dimensions, codec/audio,
  duration, black frames, freezes, long silence, and invalid output structure.
- **Dependencies**: Existing audit structural QC and rendered outputs.
- **Acceptance criteria**:
  - Produces `render_qa.v1` with stable findings and thresholds.
  - QA failure is recorded separately and never changes an already completed
    render to failed, preserving the remote MVP invariant.
  - Commands have timeouts and bounded output parsing.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_creative_qa.py tests/test_mvp_audit.py -v`
- **Rollback**: Disable the new QA stage; existing structural audit remains.

### Task 7.2: Add Rhythm And Plan-Execution Checks

- **Location**: `src/open_storyline/mvp/creative_qa.py`, adapted from
  `retention_rhythm_qa.py` and `pipeline_manifest.py`
- **Description**: Measure hook-window activity, scene/overlay changes, longest
  visual hold, attention gaps, subtitle cadence, center-fallback count, planned
  versus executed operations, and requested versus used assets.
- **Dependencies**: Task 7.1 and `render_execution.json`.
- **Acceptance criteria**:
  - Report states explicitly that rhythm heuristics do not predict virality.
  - Findings distinguish blockers, warnings, and review notes.
  - A missing requested asset, unexecuted operation, or unexplained fallback is
    visible in `creative_conformance.json`.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_creative_qa.py -v`
- **Rollback**: Disable rhythm/conformance reporting independently of rendering.

### Task 7.3: Add Optional Bounded Semantic Output Review

- **Location**: `src/open_storyline/mvp/creative_qa.py`,
  `src/open_storyline/mvp/frame_sampling.py`,
  `src/open_storyline/mvp/ninerouter.py`, `src/open_storyline/config.py`
- **Description**: Behind a disabled-by-default flag, sample a small number of
  rendered frames and ask the approved vision model whether the planned subject,
  screen, or asset is visible and relevant. Validate the report server-side and
  store only sanitized JSON evidence.
- **Dependencies**: Tasks 7.1-7.2.
- **Acceptance criteria**:
  - Semantic QA cannot authorize actions or mutate the edit plan.
  - Frame count/cost is bounded and raw provider bodies are not persisted.
  - Provider failure yields an unavailable/review result, not a failed render.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_creative_qa.py tests/test_ninerouter.py -v`
- **Rollback**: Set semantic QA disabled; deterministic QA remains.

### Task 7.4: Establish Cross-Niche Eval Fixtures And Metrics

- **Location**: `tests/fixtures/mvp_agentic/`, test-time FFmpeg helpers,
  `tests/test_mvp_creative_qa.py`, `docs/mvp/architecture.md`
- **Description**: Add synthetic or redistributable scenarios for trading/screen
  content, single-speaker tutorial, two-speaker interview, cooking/demo motion,
  and product/presentation content. Record expected capabilities and schema-level
  outcomes rather than exact creative prose.
- **Dependencies**: Tasks 7.1-7.3.
- **Acceptance criteria**:
  - Fixtures contain no production/private media, secrets, or real provider
    responses.
  - Metrics cover schema validity, source bounds, target visibility, center
    fallback, asset-call behavior, plan execution, QA status, latency, and cost
    counters where available.
  - The private `Sesion prueba 1` trading video is documented only as an
    operator-only regression gate, never committed.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_edit_plan.py tests/test_mvp_compositor.py tests/test_mvp_assets.py tests/test_mvp_creative_qa.py -v`
- **Rollback**: Remove or disable the new eval cases; production artifacts are
  unaffected.

### Sprint 7 Gate

- [ ] All Sprint 7 tasks complete.
- [ ] Structural/rhythm/conformance reports are generated and audited.
- [ ] Cross-niche fixtures pass without exact-prose assertions.
- [ ] Semantic QA failure is proven non-blocking for completed renders.
- [ ] Exactly one Sprint 7 commit is created with the proposed sprint message.
- [ ] The Sprint 6 commit SHA is recorded as the rollback point.
- [ ] Sprint 8 has not started before this gate completes.

## Sprint 8: Guarded Pexels Sourcing And Production Rollout

**Goal**: Add optional stock sourcing behind strict provider controls, then
complete shadow/canary rollout and operational documentation.

**Dependencies**: Sprint 7 gate, current official Pexels contract/license review,
configured secret handling, and explicit authorization for any live canary.

**Tracked scope**: `src/open_storyline/mvp/stock.py`,
`src/open_storyline/mvp/assets.py`, `src/open_storyline/mvp/edit_plan.py`,
`src/open_storyline/mvp/preflight.py`, `src/open_storyline/mvp/pipeline.py`,
`src/open_storyline/config.py`, `web/mvp.html`, `.env.mvp.example`,
`.env.kamal.example`, `.kamal/secrets.example`, `config/deploy.yml`,
`Dockerfile.remote`, `docs/mvp/architecture.md`, `docs/mvp/api-keys.md`,
`docs/mvp/guia-es.md`, `docs/mvp/implementation-history.md`, and release tests.

**Commit**: `feat(mvp): add guarded pexels sourcing and rollout`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_stock.py tests/test_mvp_assets.py tests/test_mvp_app.py tests/test_remote_profile.py tests/test_kamal_config.py -v`
- Run the complete deterministic suite, remote-image build, shell checks, and
  focused browser smoke before any authorized production canary.

**Rollback point**: Sprint 7 commit SHA, verified database backup, currently
deployed application version, and feature flags set to legacy/source-only.

### Task 8.1: Implement A Disabled-By-Default Pexels Adapter

- **Location**: `src/open_storyline/mvp/stock.py`,
  `src/open_storyline/mvp/assets.py`, `src/open_storyline/config.py`
- **Description**: Build an async `httpx` provider boundary for bounded Pexels
  search and download. Do not reuse the full-agent synchronous `requests` node
  directly. Verify current official endpoint, auth, limits, attribution, and
  license rules before implementation.
- **Dependencies**: Sprint 6 asset manifest/provenance contracts.
- **Acceptance criteria**:
  - `OPENSTORYLINE_PEXELS_ENABLED=false` is the default and `PEXELS_API_KEY` is a
    deploy secret only.
  - Search/result counts, response bytes, redirects, MIME types, dimensions,
    duration, timeouts, and retries are bounded.
  - Runtime accepts only HTTPS URLs returned by the provider response and
    validated against the provider/CDN policy; arbitrary model/user URLs are
    rejected.
  - Provenance records Pexels asset ID, creator, source URL, selected file
    metadata, retrieval time, license reference, and SHA-256.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_stock.py tests/test_mvp_assets.py -v`
- **Rollback**: Disable Pexels, remove the adapter, and retain generated/source
  asset paths. No provider fallback occurs.

### Task 8.2: Add Provider-Capability Planning And UI Controls

- **Location**: `src/open_storyline/mvp/edit_plan.py`,
  `src/open_storyline/mvp/preflight.py`, `src/open_storyline/mvp/api.py`,
  `web/mvp.html`
- **Description**: Expose whether generated images and/or Pexels stock are
  permitted for the job. The planner chooses only among enabled capabilities;
  missing provider capability must be resolved during planning/preflight, not by
  a runtime fallback.
- **Dependencies**: Task 8.1.
- **Acceptance criteria**:
  - Pexels is opt-in and visibly distinct from generated images.
  - Users can disable all external assets and still receive agentic source-only
    edits.
  - UI is responsive, keyboard accessible, and explains provenance/rights review.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_stock.py tests/test_mvp_app.py -v`
  - `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:desktop`
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:mobile`
- **Rollback**: Disable/hide Pexels controls and retain generated/source-only
  policies.

### Task 8.3: Update Remote Packaging, Secrets, And Documentation

- **Location**: `.env.mvp.example`, `.env.kamal.example`,
  `.kamal/secrets.example`, `config/deploy.yml`, `Dockerfile.remote`,
  `requirements-remote.txt`, `docs/mvp/architecture.md`,
  `docs/mvp/api-keys.md`, `docs/mvp/guia-es.md`,
  `docs/mvp/implementation-history.md`, `README.md`, `README_zh.md` when shared
  navigation changes.
- **Description**: Document new flags, provider boundaries, artifact schemas,
  costs/limits, rights review, kill switches, canary steps, and rollback. Keep
  the remote image free of local inference packages and large resources.
- **Dependencies**: Tasks 8.1-8.2 and final config names.
- **Acceptance criteria**:
  - Env names match code, Kamal, examples, tests, and runbooks exactly.
  - No secret value, media, provider body, or private audit evidence enters Git or
    the Docker build context.
  - Historical completion is added to implementation history only after Sprint 8
    implementation/validation, not copied from this active plan prematurely.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_remote_profile.py tests/test_kamal_config.py tests/test_update_config.py -v`
  - `bash -n run.sh build_env.sh download.sh bin/kamal-mvp scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy`
  - `docker build -f Dockerfile.remote .`
- **Rollback**: Revert code/config/docs together and restore the prior image; do
  not leave deploy env names pointing at removed code.

### Task 8.4: Execute Shadow, Canary, And Default Rollout Gates

- **Location**: Operational environment, `bin/kamal-mvp`, audit CLI, `/health`,
  `/up`, and the documented feature flags.
- **Description**: After separate authorization, deploy first with agentic mode
  `shadow`, generated assets disabled, Pexels disabled, and semantic QA disabled.
  Compare plans/evidence, then enable agentic rendering for selected test jobs,
  then generated assets, and only later Pexels. Make the MVP UI default agentic
  only after acceptance criteria pass.
- **Dependencies**: Tasks 8.1-8.3 and verified backup/rollback resources.
- **Acceptance criteria**:
  - Health checks, queue recovery, artifacts, audit ingestion, retention, and
    legacy kill switch are verified after each flag change.
  - The private production regression set includes `Sesion prueba 1` plus at
    least two non-trading sources; no private payload is copied into Git/evidence.
  - Roll back if plan validity, render success, target visibility, output sync,
    provider error rate, or latency exceeds thresholds accepted from Sprint 7
    canary evidence.
  - Final default change is additive and reversible by feature flag without a
    database restore.
- **Validation**:
  - Deterministic release commands in Task 8.3.
  - Authorized provider checks:
    `PYTHONPATH=src .venv/bin/python scripts/qa_ninerouter.py --strict-models --live-inference`
    and
    `PYTHONPATH=src .venv/bin/python scripts/qa_mistral_stt.py --audio "$MISTRAL_QA_STT_AUDIO" --each-key`.
  - Authorized runtime checks: `/health`, `/up`, one synthetic source-only job,
    one generated-asset job, optional Pexels job, restart recovery, artifact
    download, audit verification, retention preview, and operator review of
    private canary outputs.
- **Rollback**: Set UI/request default to legacy, set agentic mode `off`, disable
  generated/Pexels/semantic QA, deploy the recorded prior application version,
  and use database restore only if a separately introduced migration proves
  incompatible.

### Sprint 8 Gate

- [ ] All Sprint 8 tasks complete.
- [ ] Current Pexels API/license behavior is verified and documented.
- [ ] Complete deterministic, connected-database, render, container, shell, and
  focused browser checks pass with exact evidence recorded.
- [ ] Authorized shadow/canary gates pass or rollout remains disabled.
- [ ] Kill switches and prior-image rollback are exercised or dry-run verified.
- [ ] Exactly one Sprint 8 commit is created with the proposed sprint message.
- [ ] The Sprint 7 commit SHA and production rollback version are recorded.
- [ ] No further sprint or production-default change starts before this gate completes.

## Testing Strategy

- **Unit**:
  - Schema boundaries, finite numbers, timing/source bounds, capability allowlists,
    crop smoothing, safe zones, layer order, transition compatibility, asset-call
    conditions, provider response validation, provenance, QA thresholds, and
    sanitized errors.
  - Test model output shapes and required evidence, not exact creative prose.
- **Integration**:
  - Mocked 9Router text/vision/image calls, mocked Pexels search/download,
    job-scoped filesystem paths, artifact registration, audit ingestion, bundle
    creation, retention, and restart recovery.
  - Connected disposable PostgreSQL tests for additive request/result fields,
    events, artifact/audit documents, terminal cleanup, and no state migration
    regression.
- **FFmpeg/rendering**:
  - Test-time synthetic videos for wide separated subjects, moving focus targets,
    group/screens requiring fit, PiP, overlays, transitions, subtitles, silence,
    black/freeze defects, and generated still insertion.
  - Verify duration, dimensions, codecs, audio presence, subtitle timing, target
    visibility, black gaps, and one-encode behavior where applicable.
- **End-to-end/manual**:
  - Local authenticated upload/job/poll/download flow in legacy, shadow, and
    agentic modes.
  - Source-only plan, justified generated-image plan, provider failure, restart
    recovery, artifact bundle, session deletion, and retention preview.
  - Private production canary only after separate authorization.
- **Security/privacy**:
  - CSRF/auth compatibility, traversal rejection, generated FFmpeg expressions,
    no raw model commands, SSRF/redirect/host controls, media size/type limits,
    secret redaction, private payload absence from logs/audit/provider attempts,
    and remote-profile dependency inspection.
- **Performance/reliability**:
  - Same-host legacy versus agentic benchmark, bounded model/image/Pexels calls,
    timeout/retry tests, queue capacity, worker restart recovery, filtergraph/event
    limits, output disk growth, and cleanup behavior.
- **Accessibility/browser**:
  - Labels, keyboard navigation, focus visibility, error/live regions, responsive
    desktop/mobile layout, disabled/loading states, rights notices, and no console
    errors in focused Chromium flows.
- **Release/operations**:
  - Config parse, complete deterministic suite, connected PostgreSQL suite,
    shell syntax, Docker build, Kamal render/config tests, `/health`, `/up`, backup,
    restore-check, feature flags, audit CLI, and rollback documentation.

### Baseline Commands During Implementation

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

```bash
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' \
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

```bash
PYTHONPATH=src .venv/bin/python -c "from open_storyline.config import load_settings; load_settings('config.toml'); print('config_ok')"
```

```bash
bash -n run.sh build_env.sh download.sh bin/kamal-mvp \
  scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh \
  scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy
```

```bash
docker build -f Dockerfile.remote .
```

The implementation may use `rtk test` for compact output, but the first hidden
failure must be rerun with the smallest raw command needed for diagnosis.

## Risks And Gotchas

| Risk | Impact | Mitigation | Validation signal |
| --- | --- | --- | --- |
| Sparse or incorrectly mapped frames | Planner focuses the wrong subject/time | Stable frame IDs, timestamps, scene-aware sampling, strict reference validation | Every observation and plan target resolves to a known frame/region/track |
| Niche-specific heuristics leak into code | Trading works while other domains regress | General semantic roles and capability schemas; context stays in prompt/evidence | Cross-niche fixtures pass without domain branches |
| Invalid model plan reaches FFmpeg | Render failure or command-injection risk | Typed server validation, capability registry, generated filtergraphs only | Hostile/unsupported plan tests fail before render |
| Crop jitter or subject loss | Output feels automated and visually wrong | Smoothing, hysteresis, max velocity, safe margins, fit/PiP alternatives | Pixel/target visibility and adjacent-crop delta tests |
| Complex filtergraphs overload CPU | Queue latency and timeouts rise | Event/filtergraph caps, one encode, benchmarks, feature flags | Same-host benchmark remains within accepted bound |
| Asset generation happens unnecessarily | Cost, latency, and irrelevant visuals | Structured visual-gap request plus job/server policy and zero-call tests | No-request tests observe zero image transport calls |
| Generated image failure silently changes source | Untraceable behavior and policy breach | Fail closed after provider selection; no Pexels/local fallback | Failure tests show sanitized error and partial cleanup |
| Generated/stock rights are misunderstood | Publication/legal risk | Provenance, source/license references, rights notice, human publish review | Asset manifest and UI notice are present |
| Pexels URL/download abuse | SSRF, oversized files, hostile content | Provider-only response URLs, HTTPS/host policy, MIME/size/duration limits | Redirect/host/type/size security tests |
| Semantic QA is treated as truth | False failures or false confidence | Advisory result, bounded evidence, no job-state rewrite | Provider failure leaves completed render intact |
| New JSON artifacts break rollback | Older code cannot inspect jobs/bundles | Versioned additive artifacts, legacy manifest fields retained | Prior-version rollback can load/list/download jobs |
| JSONB becomes an unbounded state dump | Database growth and privacy exposure | Bounded sanitized summaries; binary/media remain filesystem-owned | Size/redaction tests and audit retention preview |
| FFMPEGA duplicates timeline decisions | Conflicting effects and nondeterminism | Keep it optional and post-render with finishing-only allowlist | Agentic output completes identically with FFMPEGA off |
| Source repository licensing/provenance is unclear | Reuse/attribution dispute | Record user authorization, source path, and material adaptation attribution | Review shows no unexplained wholesale copy |
| Pexels docs change after planning | Incorrect auth/limits/license behavior | Re-verify official docs immediately before Sprint 8 | Documentation review is recorded in Sprint 8 evidence |

## Rollback Plan

- Maintain `OPENSTORYLINE_AGENTIC_EDITING_MODE=off|shadow|render` as the primary
  kill switch throughout implementation.
- Maintain separate generated-asset, Pexels, and semantic-QA flags so provider
  or QA failures do not require disabling intelligent source-only reframing.
- Record the exact pre-sprint commit SHA before every sprint and create exactly
  one sprint commit. Revert only the latest completed sprint when possible.
- Preserve the legacy renderer and additive API defaults until the final canary
  accepts agentic mode. Do not delete the legacy path in this plan.
- Keep artifact schemas versioned and additive. Older code may ignore new JSON
  files but must continue to reconstruct jobs from PostgreSQL and serve existing
  registered videos/subtitles/manifests.
- Because no migration is planned, normal rollback should require only flags and
  application image rollback. If implementation introduces a migration, it must
  add its own forward/backward compatibility, backup, restore-check, and sprint
  rollback instructions before merging.
- For production rollback:
  1. Stop selecting agentic mode for new jobs.
  2. Set agentic mode `off`; disable generated assets, Pexels, and semantic QA.
  3. Let the active execution fence drain or cancel only through supported job
     controls; do not overlap the same job on two workers.
  4. Deploy the recorded prior application version.
  5. Verify `/up`, `/health`, login, job listing, legacy render, artifact download,
     audit CLI, and retention preview.
  6. Restore PostgreSQL only if a separately reviewed incompatible migration or
     data corruption requires it.

## Execution Order

1. Read `/home/loldlm/.codex/skills/planner/references/execution-state.md` when
   the user separately authorizes implementation.
2. Initialize active-plan execution state before Sprint 1.
3. Implement Sprint 1 only.
4. Run and record every Sprint 1 validation item.
5. Create exactly one Sprint 1 commit and record its rollback SHA.
6. Start Sprint 2 only after the Sprint 1 gate passes.
7. Repeat the validation, one-commit, rollback-record, and sprint-advance gate for
   every remaining sprint.
8. Do not deploy, run live providers, or inspect private production media unless
   the user separately authorizes that action during implementation.

## Completion Checklist

- [ ] Every sprint passes its focused and required regression validation.
- [ ] Every sprint has exactly one sprint-specific commit.
- [ ] Every sprint records its rollback point and residual risks.
- [ ] Legacy, shadow, and agentic modes behave according to their contracts.
- [ ] Source-only jobs make no asset-provider calls unless the plan requests one.
- [ ] Generated assets and Pexels remain independently disableable and auditable.
- [ ] Cross-niche evals and private operator canary evidence support the final
  default decision.
- [ ] Complete deterministic, connected-database, render, browser, security,
  container, deployment-config, health, backup, and rollback checks are recorded.
- [ ] Documentation, env names, Kamal config, artifact schemas, and operational
  runbooks match the delivered implementation.
- [ ] No private media, credentials, provider bodies, or sensitive transcripts
  enter Git, logs, fixtures, screenshots, or handoff evidence.
