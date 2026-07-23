# Plan: Agentic-Only Render Review and Repair Upgrade

**Generated**: 2026-07-22
**Status**: In progress; Sprints 1-5 complete
**Estimated Complexity**: High

## Overview

The remote MVP already has an Agentic pre-render planner, bounded source-frame
understanding, deterministic FFmpeg rendering, pre-render plan repair, and
post-render deterministic QA. Its current semantic frame review is deliberately
non-mutating, so it cannot use the rendered result to improve the creative plan.

This plan makes Agentic editing the only product workflow and adds a bounded
rendered-video feedback loop:

```text
Agentic plan
  -> deterministic render and optional typed effects
  -> adaptive rendered evidence
  -> LLM creative critic
  -> typed localized repair decision
  -> affected-clip rerender
  -> deterministic technical gates + LLM comparative verification
  -> promote the best eligible candidate
```

The LLM owns editorial and creative judgments: narrative, pacing, framing,
caption treatment, transitions, effects, emphasis, and repair strategy.
Deterministic code owns observation, evidence selection, technical defect
detection, capability enforcement, typed execution, candidate isolation, and
final verification. The workflow never sends every frame to the LLM and never
allows model output to issue raw FFmpeg or filesystem commands.

The plan also removes the unused full local application after proving that the
remote MVP has no runtime dependency on it. This is separate from removing the
MVP's legacy workspace/editing paths, which requires a data and rollout gate.

## Scope

- **In scope**:
  - Remove the unused full local CLI/MCP application and its local-only build,
    dependency, documentation, and test surface.
  - Make the remote MVP Agentic-only: no user-selectable legacy editing mode,
    no legacy workspace UI, and no legacy execution branch.
  - Add typed rendered-evidence manifests with adaptive anchors, event-focused
    frames, defect-window densification, and short temporal snippets.
  - Add an LLM-led post-render critic for composition, framing, captions,
    pacing, narrative coherence, transitions, effects, and visual emphasis.
  - Add bounded post-render repair, localized rerendering, candidate comparison,
    defect fingerprints, checkpoint reuse, and no-op call suppression.
  - Add effect-aware review and repair through the existing typed FFMPEGA
    capability boundary, followed by narrative/pacing review once objective
    repair behavior is measurable.
  - Add privacy-safe artifacts, audit/outcome attribution, call ledgers, eval
    fixtures, human preference review, staged rollout, and final cleanup.

- **Out of scope**:
  - Literal frame-by-frame LLM review of the entire video.
  - A new model provider, model migration, speech-to-text provider, image
    provider, database product, or authentication policy.
  - Raw FFmpeg/model/shell access from the LLM.
  - Automated claims about virality, retention, or taste without human-eval
    evidence.
  - Production deployment, live provider calls, media upload, or database
    mutation during this planning turn.

- **Fixed decisions**:
  - Agentic is the only user-facing editing workflow.
  - The LLM makes creative decisions; deterministic code supplies evidence and
    enforces technical and security constraints.
  - Review is adaptive and evidence-grounded, not exhaustive frame-by-frame.
  - One normal post-render repair round is allowed. One contingency round is
    allowed only when the first repair introduces a genuinely new authoritative
    objective defect. No third repair batch is possible.
  - Every LLM call must add new evidence or make a materially new decision.
  - Frame bytes, thumbnails, transcripts, and provider bodies remain transient;
    persisted artifacts contain bounded metadata, evidence IDs, timestamps,
    metrics, hashes, sanitized findings, and call attribution only.
  - Rollout is shadow -> report -> private enforce canary -> broader enforce.
    Temporary rollout states are operational controls, not alternative editor
    modes, and are removed or narrowed after the canary proves the Agentic path.
  - Rollback never resurrects the legacy editor. It restores the prior known-good
    Agentic image or pauses promotion with an explicit retryable/limited outcome.

- **Assumptions**:
  - The remote MVP deployed through `Dockerfile.remote` and `config/deploy.yml`
    is the active product; no supported production user depends on the full
    local application.
  - A pre-removal inventory can prove whether workflow-version-1 sessions or
    legacy jobs still exist. If active legacy data is found, destructive legacy
    cleanup pauses and a bounded archive/import decision is required.
  - Existing 9Router strict-schema capability probes, Mistral STT boundaries,
    FFmpeg validation, PostgreSQL authority, and FFMPEGA allowlists remain the
    authoritative integration contracts.
  - The final reviewer may compare the original and repaired rendered evidence,
    but it may not override deterministic technical blockers.
  - Quality thresholds that require subjective judgment will be approved from
    the private cross-niche eval set before enforce rollout; they will not be
    invented from a single production artifact.

## Named Resources

- **Project instructions**: `AGENTS.md`, `.agents/`, `.claude/`, and the
  repository-specific verification commands.
- **Architecture and rollout**: `docs/agent-engineering.md`,
  `docs/mvp/architecture.md`, `docs/mvp/agentic-defect-repair-rollout.md`,
  `docs/mvp/audit-and-database.md`, and
  `docs/mvp/implementation-history.md`.
- **Pipeline and contracts**: `src/open_storyline/mvp/pipeline.py`,
  `edit_plan.py`, `repair.py`, `defects.py`, `creative_qa.py`,
  `frame_sampling.py`, `frame_quality.py`, `visual_understanding.py`,
  `ffmpega.py`, `structured_outputs.py`, `prompts.py`, `checkpoints.py`,
  `promotion.py`, `outcomes.py`, `audit.py`, `observability.py`, and
  `edit_plan.py`'s `AgenticArtifactNames`.
- **API and UI**: `mvp_fastapi.py`, `src/open_storyline/mvp/api.py`,
  `jobs.py`, `prompt_versions.py`, `web/mvp.html`, and `web/static/mvp/`.
- **Configuration and deployment**: `src/open_storyline/config.py`,
  `config.toml`, `.env.mvp.example`, `.env.kamal.example`, `config/deploy.yml`,
  `bin/kamal-mvp`, `Dockerfile.remote`, and `requirements-remote.txt`.
- **Database**: `src/open_storyline/mvp/models.py`, `database.py`, `admin.py`,
  `migrations/versions/`, `scripts/mvp-postgres-backup.sh`, and
  `scripts/mvp-postgres-restore-check.sh`.
- **Tests and fixtures**: existing `tests/test_mvp_*.py`,
  `tests/test_remote_profile.py`, `tests/test_kamal_config.py`,
  `tests/fixtures/mvp_agentic/`, `tests/fixtures/quality/`, and new focused
  tests named in the sprint tasks below.
- **External documentation**: no new provider/API integration is planned.
  If command semantics change, consult the official FFmpeg references
  (`https://ffmpeg.org/ffmpeg-filters.html` and
  `https://ffmpeg.org/ffprobe.html`) and PostgreSQL JSON/migration guidance
  (`https://www.postgresql.org/docs/current/datatype-json.html`). The local
  9Router adapter and its strict capability probe remain authoritative for this
  project.
- **Operational resources**: sanitized private canary media, a disposable
  PostgreSQL database named with the `openstoryline_test` prefix, provider
  capability evidence, backup/restore evidence, rollout summaries, and a
  human-review rubric. Never commit or paste those private artifacts.

## Prerequisites

- Clean working tree and a recorded baseline from the fast local test suite.
- Authorized owner confirmation that the full local CLI/MCP application has no
  supported users or deployment target.
- Read-only inventory of production workflow-version-1 sessions, legacy jobs,
  and legacy artifacts before any destructive migration is implemented.
- Disposable PostgreSQL database for connected migration/concurrency checks.
- Authorized private media and prompts spanning interviews, tutorials,
  talking-head clips, screen recordings, sparse visuals, and effect-heavy
  requests. Do not store those media or raw provider responses in Git.
- Existing provider strict-schema capability probe and FFMPEGA readiness checks
  available to the operator; do not run paid/live checks without authorization.
- A rollback image or commit reference for every sprint that changes runtime,
  schema, configuration, or deployment behavior.

## Sprint 1: Isolate The Active Remote Runtime

**Goal**: Prove and enforce that the remote MVP can build and run without the
full local application, while preserving all MVP-owned shared behavior.
**Dependencies**: Baseline repository and production-use confirmation.
**Tracked scope**: `src/open_storyline/config.py`, new MVP settings/provider
modules as needed, `Dockerfile.remote`, `requirements-remote.txt`,
`tests/test_remote_profile.py`, and a new import-boundary test.
**Commit**: `refactor(mvp): isolate the remote agentic runtime`
**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_remote_profile.py tests/test_kamal_config.py -v`
- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_app.py tests/test_mvp_pipeline.py -v`
- `PYTHONPATH=src .venv/bin/python -c "import mvp_fastapi; import open_storyline.mvp.pipeline; print('mvp_imports_ok')"`
- `docker build -f Dockerfile.remote .` in an authorized build environment.

Expected result: the MVP import/build path does not import LangChain, MCP,
local nodes, local storage, local skills, or the opaque local FastAPI artifact.

**Rollback point**: Revert the sprint commit and restore the previous remote
image/configuration. Do not delete any local runtime files in this sprint.

### Task 1.1: Produce The Dependency Removal Manifest

- **Location**: `tests/test_remote_profile.py`, new
  `tests/test_mvp_import_boundary.py`, `AGENTS.md`, and the plan's named
  resource list.
- **Description**: Add a static import/build assertion and a maintainer-facing
  manifest classifying local-only files, MVP-only files, and shared files.
  The manifest must explicitly retain `config.py`, MVP modules, and the remote
  image/STT/generated-media utilities until their ownership is moved or proven.
- **Dependencies**: None.
- **Acceptance criteria**:
  - MVP imports are tested without importing `open_storyline.agent`, MCP,
    nodes, skills, storage, or `agent_fastapi`.
  - The manifest names every root entrypoint and shared module proposed for
    deletion or retention.
- **Validation**:
  - Run the new import-boundary test with the full local dependency set hidden
    or blocked where the test runner permits.
  - Review all `rg` inbound references before any deletion.
- **Rollback**: Remove only the new assertions if the ownership classification
  is wrong; no runtime behavior changes are allowed yet.

### Task 1.2: Extract MVP Configuration And Provider Ownership

- **Location**: `src/open_storyline/config.py`, new
  `src/open_storyline/mvp/settings.py` or equivalent, and the shared remote
  utilities currently under `src/open_storyline/utils/`.
- **Description**: Ensure MVP code can load its remote settings without
  requiring local-only model, MCP, skill, node, or resource sections. Move or
  re-export remote STT, remote image, and generated-media helpers under an MVP
  ownership boundary if that is the smallest safe change. Preserve environment
  names, provider routing, timeouts, secret redaction, and public error codes.
- **Dependencies**: Task 1.1.
- **Acceptance criteria**:
  - `mvp_fastapi.py` and all `src/open_storyline/mvp/` modules use the MVP
    settings/provider boundary.
  - Existing `config.toml` and `.env` names used by the MVP remain valid.
  - No local-only dependency becomes required by `requirements-remote.txt`.
- **Validation**:
  - Run focused settings/provider tests: `tests/test_ninerouter.py`,
    `tests/test_remote_stt.py`, `tests/test_remote_image.py`,
    `tests/test_generated_media.py`, and `tests/test_mvp_app.py`.
  - Run `PYTHONPATH=src .venv/bin/python -c "import mvp_fastapi; print('settings_ok')"`.
- **Rollback**: Restore the previous settings/provider import paths and keep
  the local application intact until a later sprint.

### Task 1.3: Narrow The Remote Image Build Context

- **Location**: `Dockerfile.remote`, `.dockerignore`, and
  `tests/test_remote_profile.py`.
- **Description**: Copy only the MVP package, required shared modules, catalog,
  migrations, remote UI, and remote scripts needed by the image. Do not rely on
  the remote image receiving the full local source tree as an incidental
  dependency.
- **Dependencies**: Task 1.2.
- **Acceptance criteria**:
  - The image does not contain local entrypoints or local model/resource
    installers unless a specific MVP contract requires them.
  - The image still starts `mvp_fastapi:app` and keeps its health check.
- **Validation**:
  - Run `tests/test_remote_profile.py` and `tests/test_kamal_config.py`.
  - Build the image and inspect its startup command and copied paths without
    running a provider or production deployment.
- **Rollback**: Revert the Dockerfile/context change and use the prior remote
  image while preserving the import-boundary tests.

### Sprint 1 Gate

- [x] All Sprint 1 tasks complete.
- [x] Sprint 1 validation passes and evidence is recorded.
- [x] The local-runtime removal manifest and production-use confirmation are
  reviewed by the maintainer.
- [x] Residual risks are documented.
- [x] Exactly one Sprint 1 commit is created with the proposed message.
- [x] The rollback point is recorded.
- [x] Sprint 2 has not started before this gate completes.

## Sprint 2: Remove The Unused Full Local Application

**Goal**: Delete the local CLI/MCP application without changing the remote MVP
execution path.
**Dependencies**: Sprint 1 gate and explicit confirmation that no supported
local deployment remains.
**Tracked scope**: Local-only root entrypoints/build files, local-only
`src/open_storyline/` packages, local skills/prompts/resources, local web
assets, local dependencies, and documentation/tests that describe them.
**Commit**: `refactor: remove unused full local application`
**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
- `PYTHONPATH=src .venv/bin/python -c "import mvp_fastapi; import open_storyline.mvp.pipeline; print('mvp_only_ok')"`
- `bash -n bin/kamal-mvp scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy .kamal/hooks/post-deploy`
- `docker build -f Dockerfile.remote .`
- `rg -n "open_storyline\\.(agent|mcp|nodes|storage|skills)|agent_fastapi|python cli\\.py|python -m open_storyline\\.mcp"` must return no active product/deployment references; historical records must be explicitly labeled.

**Rollback point**: Revert this single deletion/cleanup commit and restore the
previous full-local dependency lock/image if an authorized local consumer is
discovered. Reinstalling local dependencies may require a separate environment
rebuild; that is why the remote image is validated before deletion.

### Task 2.1: Delete Local Entrypoints And Packaging

- **Location**: `Dockerfile`, `run.sh`, `cli.py`, `agent_fastapi.py`,
  `requirements.txt`, `download.sh`, `build_env.sh`, and any local-only launch
  or model-download scripts confirmed by the manifest.
- **Description**: Delete the full-local launch/build surface. Do not decode,
  format, regenerate, or hand-edit `agent_fastapi.py`; remove it only as an
  authorized file deletion. Preserve `Dockerfile.remote`, `Dockerfile.ffmpega`,
  `Dockerfile.quality`, and their scripts.
- **Dependencies**: Sprint 1 gate.
- **Acceptance criteria**:
  - No supported command or deployment references the deleted entrypoints.
  - Remote deployment configuration still points exclusively to
    `Dockerfile.remote`.
- **Validation**:
  - Run `tests/test_remote_profile.py`, `tests/test_kamal_config.py`, and the
    repository-wide reference search from the sprint demo.
- **Rollback**: Revert the deletion commit; never recreate the opaque artifact
  from a generated substitute.

### Task 2.2: Delete Local-Only Python Packages And Assets

- **Location**: `src/open_storyline/agent.py`, `src/open_storyline/mcp/`,
  `src/open_storyline/nodes/`, `src/open_storyline/storage/`,
  `src/open_storyline/skills/`, local-only `src/open_storyline/utils/` files,
  `.storyline/`, root local `prompts/`, and non-MVP web/resources identified by
  the manifest.
- **Description**: Remove local-only code and assets after verifying that no
  `src/open_storyline/mvp/` or remote build path imports them. Keep shared
  config/provider helpers until the MVP boundary from Sprint 1 owns them.
- **Dependencies**: Task 2.1.
- **Acceptance criteria**:
  - The MVP package imports and executes its deterministic paths without local
    packages installed or present.
  - No local skill, node, MCP, or artifact-store code remains in the active
    product tree.
- **Validation**:
  - Run the full non-browser MVP suite and import-boundary test.
  - Run `rg --files` plus the manifest review to confirm no required MVP file was
    deleted.
- **Rollback**: Revert the sprint commit if any MVP import or build path needs a
  retained shared helper; split that helper into an MVP-owned module before
  retrying deletion.

### Task 2.3: Rewrite Repository Guidance And Test Selection

- **Location**: `AGENTS.md`, `README.md`, `README_zh.md`,
  `docs/agent-engineering.md`, `docs/source/`, local-only tests, and any
  operator skill discovery links that describe the deleted application.
- **Description**: Remove obsolete local installation/run instructions and
  clarify that the repository contains the remote Agentic MVP plus its
  deterministic sidecars. Keep historical implementation records labeled as
  history, not current runtime instructions.
- **Dependencies**: Tasks 2.1 and 2.2.
- **Acceptance criteria**:
  - English and Chinese current instructions agree on the MVP entrypoint and
    deployment boundary.
  - No test command attempts to validate deleted local behavior.
- **Validation**:
  - Run documentation/reference searches and the full MVP test discovery.
  - Run `bash -n` on every retained shell script named by `AGENTS.md`.
- **Rollback**: Restore documentation only if the reference audit identifies a
  still-supported operational path; do not restore deleted runtime code without
  a new product decision.

### Sprint 2 Gate

- [x] All local-only files are deleted only after the dependency manifest passes.
- [x] Remote imports, tests, image build, and deployment configuration pass.
- [x] No secret, media, provider body, or generated artifact entered the commit.
- [x] Residual rollback/reinstallation risk is documented.
- [x] Exactly one Sprint 2 commit is created with the proposed message.
- [x] The rollback point is recorded.
- [x] Sprint 3 has not started before this gate completes.

## Sprint 3: Make The Remote MVP Agentic-Only

**Goal**: Remove legacy editing/workspace choices from the active MVP while
protecting historical data and preserving the reusable Agentic workspace.
**Dependencies**: Sprint 2 gate and a read-only database/filesystem inventory.
**Tracked scope**: `mvp_fastapi.py`, `src/open_storyline/mvp/api.py`, `jobs.py`,
`prompt_versions.py`, `edit_plan.py`, `pipeline.py`, `models.py`, UI modules,
`web/mvp.html`, `web/mvp-legacy.html`, env/config/deploy files, tests, and a
new migration/admin inventory path if needed.
**Commit**: `refactor(mvp): make agentic editing the only workflow`
**Demo/Validation**:

- A disposable database inventory proves the count and ownership of
  workflow-version-1 sessions, legacy jobs, and legacy artifacts before any
  destructive migration.
- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_app.py tests/test_mvp_jobs.py tests/test_mvp_prompt_versions.py tests/test_mvp_session_media.py tests/test_mvp_sessions.py -v`
- `cd .qa/web && npm run test:smoke`

**Rollback point**: Deploy the previous compatible image and leave additive
schema data intact. Do not drop legacy columns or delete old media in this
sprint unless the inventory is empty and the backup/restore gate passes.

### Task 3.1: Remove Legacy Edit-Mode Inputs And Branches

- **Location**: `src/open_storyline/mvp/api.py`, `jobs.py`,
  `prompt_versions.py`, `edit_plan.py`, `pipeline.py`, `web/mvp.html`,
  `web/static/mvp/app.js`, `views.js`, and relevant tests.
- **Description**: Make Agentic plan generation and typed execution implicit.
  Remove the `EditMode` legacy/agentic union, legacy defaults, legacy form
  selector, and non-Agentic rendering branch. Preserve asset policy as a
  capability policy, not as an editing mode. Historical request data may be
  read for audit, but new writes must not accept or persist a selectable
  `edit_mode`.
- **Dependencies**: Inventory complete; no active legacy jobs may be running.
- **Acceptance criteria**:
  - Every new job and prompt version follows the Agentic path.
  - A legacy `edit_mode` request fails closed with a documented compatibility
    error or is accepted only by a bounded migration adapter, never by a
    legacy renderer.
  - The UI contains no editing-mode choice and sends no legacy field.
- **Validation**:
  - Update and run `tests/test_mvp_edit_plan.py`, `tests/test_mvp_jobs.py`,
    `tests/test_mvp_prompt_versions.py`, and `tests/test_mvp_pipeline.py`.
  - Add API tests for omitted, invalid, and legacy `edit_mode` inputs.
- **Rollback**: Restore the previous API/schema adapter while keeping all new
  Agentic artifacts additive.

### Task 3.2: Retire The Legacy Workspace Entry Point

- **Location**: `mvp_fastapi.py`, `web/mvp-legacy.html`,
  `web/static/mvp/`, `tests/test_mvp_app.py`, `tests/test_mvp_session_media.py`,
  `tests/test_remote_profile.py`, and env/deploy examples.
- **Description**: Serve only `web/mvp.html`, create workflow-version-2
  sessions, remove `OPENSTORYLINE_SESSION_WORKSPACE_MODE=legacy|enabled`, and
  delete legacy workspace-only UI branches after the data gate. Keep historical
  records outside the active workflow; do not silently reinterpret old media.
- **Dependencies**: Task 3.1 and zero active legacy sessions, or an explicit
  archive/import decision recorded by the owner.
- **Acceptance criteria**:
  - New sessions always use the reusable immutable source/prompt contract.
  - No route or static asset exposes `mvp-legacy.html`.
  - Existing historical records cannot be executed through the removed path.
- **Validation**:
  - Run `tests/test_mvp_app.py`, `tests/test_mvp_session_media.py`,
    `tests/test_mvp_sessions.py`, and `tests/test_remote_profile.py`.
  - Run desktop and mobile auth smoke tests with an authorized local password.
- **Rollback**: Restore the prior UI/config profile and redeploy the previous
  image. Retain additive schema/media until the final cleanup sprint.

### Task 3.3: Apply The Data Compatibility Migration

- **Location**: `migrations/versions/<next_revision>_agentic_only_workflow.py`,
  `src/open_storyline/mvp/admin.py`, `models.py`, audit docs, and migration
  tests.
- **Description**: Choose one explicit path based on the inventory: migrate
  empty/compatible data to the Agentic-only schema, or archive legacy records
  and retain them as non-executable audit history. Normalize historical JSON
  settings only in bounded, resumable batches. Do not delete customer media or
  rewrite authoritative PostgreSQL state without backup and restore evidence.
- **Dependencies**: Tasks 3.1 and 3.2; PostgreSQL backup and restore-check.
- **Acceptance criteria**:
  - The migration is idempotent, bounded, and fails closed on unknown rows.
  - No workflow-version-1 record can enter the active Agentic worker.
  - Rollback compatibility is documented before dropping any constraint/column.
- **Validation**:
  - Run migration tests against a disposable `openstoryline_test` database.
  - Run `scripts/mvp-postgres-restore-check.sh` through the documented wrapper.
  - Verify job/session/artifact counts and hashes before and after migration.
- **Rollback**: Restore the database backup or deploy the additive compatibility
  image. If legacy data exists, postpone destructive schema cleanup.

### Sprint 3 Gate

- [x] No active user-facing legacy editor/workspace path remains.
- [x] Inventory, migration, backup, and restore evidence are recorded.
- [x] Authenticated desktop/mobile smoke checks pass without console errors.
- [x] Historical data handling and residual compatibility risks are documented.
- [x] Exactly one Sprint 3 commit is created with the proposed message.
- [x] The rollback point is recorded.
- [x] Sprint 4 has not started before this gate completes.

## Sprint 4: Define Adaptive Rendered Evidence

**Goal**: Produce a deterministic, privacy-safe evidence manifest from the final
rendered candidate without making an LLM call or sampling every frame.
**Dependencies**: Agentic-only MVP contract and existing frame/scene/QA helpers.
**Tracked scope**: New `src/open_storyline/mvp/render_evidence.py`,
`frame_sampling.py`, `creative_qa.py`, `frame_quality.py`, `edit_plan.py`,
`checkpoints.py`, `observability.py`, artifact registration, and focused tests.
**Commit**: `feat(mvp): add adaptive rendered evidence manifests`
**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_frame_quality.py tests/test_mvp_creative_qa.py tests/test_mvp_visual_coverage.py tests/test_mvp_render_evidence.py -v`
- Synthetic rendered fixtures produce stable evidence IDs and deterministic
  fingerprints across repeated runs.
- A privacy assertion proves frame bytes/provider bodies are not persisted.

**Rollback point**: Keep the existing non-mutating QA path and ignore the new
evidence artifact if the manifest or sampler fails; no output promotion depends
on it until Sprint 5 report mode.

### Task 4.1: Add The Evidence Contract

- **Location**: new `src/open_storyline/mvp/render_evidence.py`,
  `src/open_storyline/mvp/structured_outputs.py`, `prompts.py`,
  `edit_plan.py` (`AgenticArtifactNames`), `audit.py`, and tests.
- **Description**: Define versioned typed models for rendered candidates,
  clips, evidence frames, temporal bursts, event reasons, source/output hashes,
  evidence IDs, and call fingerprints. Persist only the manifest metadata and
  sanitized summaries. Keep image data URLs in memory for the provider request.
- **Dependencies**: None beyond Sprint 3 contracts.
- **Acceptance criteria**:
  - Every evidence item identifies clip, timestamp, purpose, source artifact,
    and bounded dimensions/size without embedding bytes.
  - Evidence IDs are stable for the same candidate, sampler configuration, and
    render execution; changed inputs produce a new fingerprint.
  - The schema rejects unknown fields, out-of-range timestamps, duplicate IDs,
    and oversized evidence.
- **Validation**:
  - Add schema/serialization tests and property-style boundary cases using
    synthetic metadata.
  - Run secret-redaction and no-frame-persistence assertions.
- **Rollback**: Remove the new artifact registration while retaining the typed
  models for test isolation.

### Task 4.2: Implement Event-Focused Adaptive Sampling

- **Location**: `render_evidence.py`, `frame_sampling.py`,
  `creative_qa.py`, `frame_quality.py`, and new tests/fixtures.
- **Description**: Select opening/ending anchors, scene openings/closings,
  subtitle/caption events, overlay/effect/transition boundaries, crop/focus
  changes, deterministic defect windows, and uncertainty windows. Add short
  ordered temporal bursts around high-risk events. Densify only when a detector
  or prior observation justifies it; otherwise reuse the existing bounded
  scene/uniform coverage. Keep per-clip and per-job limits configurable and
  enforce timeouts/byte caps.
- **Dependencies**: Task 4.1.
- **Acceptance criteria**:
  - A simple clip receives a small anchor set rather than a full frame dump.
  - A caption/effect/transition/defect window receives focused evidence.
  - Repeated identical evidence requests hit the checkpoint rather than calling
    FFmpeg or the LLM again.
- **Validation**:
  - Test short, long, sparse, fast-cut, caption-heavy, portrait, and effect
    fixtures with exact expected reasons/count limits.
  - Run FFprobe/FFmpeg timeout and malformed-input tests.
- **Rollback**: Fall back to the existing `sample_frames` manifest and disable
  temporal bursts without changing rendered media.

### Task 4.3: Add Evidence Checkpoints And Privacy Ledger

- **Location**: `checkpoints.py`, `observability.py`, `pipeline.py`,
  `audit.py`, and new evidence tests.
- **Description**: Cache manifests by candidate hash, render execution hash,
  plan/effects hash, sampler configuration, and prompt/schema version. Record
  why each evidence batch was selected, whether it was reused, and its bounded
  byte/frame counts. Never cache raw frame bytes in PostgreSQL or job JSON.
- **Dependencies**: Tasks 4.1 and 4.2.
- **Acceptance criteria**:
  - Restarting a job reuses valid evidence checkpoints.
  - A changed rendered candidate or repair fingerprint invalidates only the
    affected evidence.
  - Audit output contains metadata, not provider bodies or media bytes.
- **Validation**:
  - Run `tests/test_mvp_checkpoints.py`, `tests/test_mvp_audit.py`,
    `tests/test_mvp_observability.py`, and restart/reuse integration cases.
- **Rollback**: Ignore evidence checkpoints and recompute deterministic evidence
  while preserving the existing job checkpoint contract.

### Sprint 4 Gate

- [x] Evidence manifests are versioned, bounded, deterministic, and private.
- [x] Adaptive sampling covers required events and does not become exhaustive
  frame-by-frame review.
- [x] Checkpoint reuse and invalidation are tested.
- [x] Exactly one Sprint 4 commit is created with the proposed message.
- [x] The rollback point is recorded at the Sprint 4 commit.
- [x] Sprint 5 has not started before this gate completes.

## Sprint 5: Add The LLM Render Critic In Shadow/Report

**Goal**: Let the LLM inspect rendered evidence and produce typed creative
findings without mutating the plan or output.
**Dependencies**: Sprint 4 evidence contract and existing 9Router client.
**Tracked scope**: New `src/open_storyline/mvp/render_critic.py`,
`structured_outputs.py`, `prompts.py`, `defects.py`, `creative_qa.py`,
`pipeline.py`, `outcomes.py`, `observability.py`, config/env examples, and
focused tests/fixtures.
**Commit**: `feat(mvp): add bounded rendered-video creative critic`
**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_ninerouter.py tests/test_mvp_structured_outputs.py tests/test_mvp_creative_qa.py tests/test_mvp_render_critic.py tests/test_mvp_defects.py tests/test_mvp_observability.py -v`
- Mocked critic calls produce a sanitized, schema-valid report with no plan
  mutation in shadow/report mode.
- Existing rollout validation rejects incomplete strict-schema capability sets.

**Rollback point**: Set the temporary post-render review control to report/off
and deploy the previous Agentic image. Existing deterministic QA and pre-render
repair remain authoritative.

### Task 5.1: Define The Creative Critic Schema And Prompt

- **Location**: new `render_critic.py`, `structured_outputs.py`, `prompts.py`,
  `defects.py`, and `tests/test_mvp_render_critic.py`.
- **Description**: Add a strict `render_critic.v1` response with findings for
  composition/framing, captions, pacing/rhythm, narrative/coherence,
  transitions, effects, visual hierarchy, and relevance. Each finding must
  include severity, confidence, clip/time window, evidence IDs, explanation,
  repair objective, and whether it is creative, objective, technical, or
  advisory. The prompt must explicitly forbid executable commands, unsupported
  evidence, provider-body echoing, and claims beyond supplied evidence.
- **Dependencies**: Sprint 4 schema and evidence IDs.
- **Acceptance criteria**:
  - Unknown fields, missing evidence IDs, out-of-bounds times, duplicate
    findings, and unsupported capabilities are rejected locally.
  - The contract distinguishes a creative recommendation from a deterministic
    technical blocker.
  - English/Spanish prompt routing remains aligned with existing user language
    handling.
- **Validation**:
  - Run schema tests with valid, malformed, adversarial, and prompt-injection
    fixture responses.
  - Verify provider attempts are attributed without retaining raw bodies.
- **Rollback**: Remove the critic schema/version and return to the existing
  semantic QA schema.

### Task 5.2: Orchestrate Evidence-Grounded Critic Calls

- **Location**: `pipeline.py`, new `render_critic.py`, `creative_qa.py`,
  `observability.py`, and `config.py`/env examples.
- **Description**: Invoke the critic only after the final candidate (including
  typed effects) has deterministic evidence. Batch clips when the evidence fits
  the bounded provider payload; partition only when required by limits. Reuse
  the evidence/call fingerprint and skip a call when no new evidence or
  decision is available. Add temporary rollout control
  `OPENSTORYLINE_POST_RENDER_REVIEW_MODE=off|shadow|report|enforce` with strict
  validation; it is not a user-facing editing mode.
- **Dependencies**: Task 5.1.
- **Acceptance criteria**:
  - Shadow/report calls never change the edit plan, effects plan, rendered
    candidate, or promotion decision.
  - Findings reference only supplied evidence and are persisted as sanitized
    metadata.
  - Technical failures bypass creative repair and remain deterministic.
- **Validation**:
  - Add mocked call-count/fingerprint tests for one clip, multiple clips,
    repeated evidence, provider failure, schema failure, and timeout.
  - Run the existing structured-output rollout validator in offline mode.
- **Rollback**: Set review mode to report/off and remove the pipeline hook; keep
  artifacts readable by older versions.

### Task 5.3: Connect Critic Findings To Defect Lifecycle Reporting

- **Location**: `defects.py`, `repair.py`, `outcomes.py`, `audit.py`,
  `observability.py`, `jobs.py`, and tests.
- **Description**: Give each finding a stable defect fingerprint based on code,
  clip/window, evidence hash, and relevant plan/effects hash. Track observed,
  eligible, repaired, rejected, unresolved, and introduced states without
  treating a subjective review note as a technical blocker.
- **Dependencies**: Task 5.2.
- **Acceptance criteria**:
  - Same finding/evidence does not create a duplicate repair trigger.
  - New evidence or a materially different candidate creates a new instance.
  - Outcome/audit summaries expose creative limitations truthfully.
- **Validation**:
  - Run `tests/test_mvp_defects.py`, `tests/test_mvp_repair.py`,
    `tests/test_mvp_outcomes.py`, `tests/test_mvp_audit.py`, and new fingerprint
    regression cases.
- **Rollback**: Keep the report as an advisory artifact and disable its effect
  on repair/promotion while retaining backward-readable outcome documents.

### Sprint 5 Gate

- [x] Critic schema and prompt are strict, bounded, evidence-grounded, and
  non-executable.
- [x] Shadow/report calls are non-mutating and call fingerprints suppress
  redundant requests.
- [x] Defect lifecycle and privacy evidence are recorded.
- [x] Exactly one Sprint 5 commit is created with the proposed message.
- [x] The rollback point is recorded.
- [x] Sprint 6 has not started before this gate completes.

## Sprint 6: Add Core Post-Render Creative Repair

**Goal**: Repair composition, framing, caption treatment, emphasis, and other
typed visual defects by letting the LLM choose a safe plan patch, then rerender
only affected clips.
**Dependencies**: Sprint 5 report-only critic and existing pre-render repair
contracts/checkpoints.
**Tracked scope**: New `src/open_storyline/mvp/post_render_repair.py`,
`pipeline.py`, `edit_plan.py`, `repair.py`, `preflight.py`, `render.py`,
`compositor.py`, `checkpoints.py`, `outcomes.py`, and tests.
**Commit**: `feat(mvp): repair rendered creative defects with bounded replans`
**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_pipeline.py tests/test_mvp_edit_plan.py tests/test_mvp_preflight.py tests/test_mvp_render.py tests/test_mvp_repair.py tests/test_mvp_post_render_repair.py tests/test_mvp_checkpoints.py -v`
- Synthetic defects show one primary repair, localized rerendering, deterministic
  revalidation, and candidate promotion only when the repaired result is safe.
- A repeated fingerprint produces zero additional repair calls.

**Rollback point**: Keep the original rendered candidate and set post-render
repair to report-only. If repair code is reverted, previously stored candidates
and reports remain inspectable.

### Task 6.1: Define A Typed Post-Render Repair Patch

- **Location**: `post_render_repair.py`, `structured_outputs.py`, `repair.py`,
  `edit_plan.py`, and tests.
- **Description**: Extend the existing edit-plan repair contract or add a
  versioned post-render patch contract. The patch may change only supported
  timeline/layout/caption/transition parameters and declared creative intent;
  it must identify affected clips and map every change to one or more critic
  finding IDs. It must not invent source bounds, raw filters, paths, commands,
  or unapproved capabilities.
- **Dependencies**: Sprint 5 finding contract.
- **Acceptance criteria**:
  - Patches validate against the current plan, source bounds, renderer
    capabilities, asset permissions, and creative intent.
  - A patch with no material change is classified as a no-op and does not cause
    a rerender.
  - Technical/security defects cannot enter this creative patch path.
- **Validation**:
  - Add valid/invalid patch tests, capability-denial tests, and idempotency
    tests using existing `tests/test_mvp_edit_plan.py` patterns.
- **Rollback**: Reject all post-render patches and retain the original candidate.

### Task 6.2: Implement The One-Round Localized Repair Loop

- **Location**: `pipeline.py`, `post_render_repair.py`, `render.py`,
  `compositor.py`, `checkpoints.py`, and activity stages.
- **Description**: After deterministic QA and the critic, send the complete
  eligible creative finding set to one primary repair request. Reuse source,
  transcript, assets, and unaffected clip outputs. Rerender only affected clips
  and rebuild downstream evidence for those clips. Preserve the original and
  repaired candidates until verification completes.
- **Dependencies**: Task 6.1.
- **Acceptance criteria**:
  - One primary post-render creative repair round is possible per job.
  - Unaffected clips are not rerendered or re-reviewed without new evidence.
  - Provider failure, schema failure, or invalid patch becomes a bounded
    retryable/limited result, never a silent legacy fallback.
  - Checkpoints can resume after process interruption.
- **Validation**:
  - Add pipeline tests for affected-clip selection, checkpoint reuse,
    provider failure, invalid patch, no-op patch, and partial rerender.
  - Assert exact provider call categories and maximum counts.
- **Rollback**: Keep the pre-repair candidate as the only eligible output and
  mark the repair attempt failed/limited with sanitized evidence.

### Task 6.3: Verify Improvement And Prevent New Defects

- **Location**: `creative_qa.py`, `frame_quality.py`, `promotion.py`,
  `post_render_repair.py`, `outcomes.py`, and tests.
- **Description**: Re-run deterministic technical checks and adaptive evidence
  after repair. Reject repaired candidates that introduce technical defects,
  violate duration/audio/subtitle/caption/asset constraints, or fail the typed
  quality floor. If both candidates are technically eligible but creative
  improvement is not demonstrated, retain the original and record why.
- **Dependencies**: Task 6.2.
- **Acceptance criteria**:
  - A repair can resolve a creative finding without weakening deterministic
    promotion gates.
  - A new authoritative objective defect is the only condition that can unlock
    the one contingency repair round.
  - No third plan/repair call is possible, including after restart/checkpoint
    reuse.
- **Validation**:
  - Add regression fixtures for resolved defects, unchanged output, introduced
    defects, and technical blockers.
  - Run the full pipeline and promotion test groups.
- **Rollback**: Choose the original candidate if valid; otherwise withhold both
  candidates and return a truthful retryable/terminal outcome.

### Sprint 6 Gate

- [ ] Typed creative patches are validated and cannot issue raw execution.
- [ ] Localized rerender and checkpoint reuse work.
- [ ] The one-primary plus new-defect-contingency cap is enforced.
- [ ] Candidate verification prevents technical regressions and silent fallback.
- [ ] Exactly one Sprint 6 commit is created with the proposed message.
- [ ] The rollback point is recorded.
- [ ] Sprint 7 has not started before this gate completes.

## Sprint 7: Add Effect-Aware Render Review And Repair

**Goal**: Let the LLM evaluate rendered effects and finishing choices while the
typed FFMPEGA/deterministic allowlists remain the only execution boundary.
**Dependencies**: Sprint 6 core repair loop and a healthy FFMPEGA sidecar when
effects are enabled.
**Tracked scope**: `ffmpega.py`, `ffmpega_contracts.py`, `pipeline.py`,
`render_critic.py`, `post_render_repair.py`, `creative_qa.py`,
`structured_outputs.py`, `prompts.py`, rollout docs, and tests.
**Commit**: `feat(mvp): add effect-aware rendered repair`
**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_ffmpega.py tests/test_ffmpega_service.py tests/test_mvp_ffmpega_contracts.py tests/test_mvp_pipeline.py tests/test_mvp_repair.py -v`
- Effect-enabled synthetic fixtures prove that the critic receives final
  effect evidence and that any repair remains inside the typed allowlist.
- `./bin/kamal-mvp rollout validate` rejects an enabled effect path without a
  healthy pinned sidecar/strict boundary.

**Rollback point**: Disable optional FFMPEGA finishing and retain the native
Agentic render; record `EFFECT_OMITTED` or equivalent limitation. Do not create
an additional semantic repair call when the sidecar fails.

### Task 7.1: Review Final Effect Evidence

- **Location**: `pipeline.py`, `render_evidence.py`, `creative_qa.py`,
  `ffmpega.py`, and artifact names/tests.
- **Description**: Build evidence from the actual post-effect video, not merely
  the pre-effect native render or effect plan. Include effect boundaries,
  transitions, overlays, subtitle moments, and before/after hashes. Preserve
  effect omission/fallback attribution.
- **Dependencies**: Sprint 6 evidence/review loop.
- **Acceptance criteria**:
  - The critic can distinguish planned, executed, omitted, and visibly defective
    effects from supplied evidence.
  - Native fallback remains deterministic and explicitly labeled.
- **Validation**:
  - Run effect execution, render evidence, and asset visibility tests with a
    mocked sidecar and a sidecar-failure fixture.
- **Rollback**: Review the native render only and record the effect limitation.

### Task 7.2: Add Typed Effect Repair Decisions

- **Location**: `ffmpega_contracts.py`, `ffmpega.py`, `post_render_repair.py`,
  `structured_outputs.py`, and tests.
- **Description**: Permit the LLM to request a bounded effect-plan change such
  as removing, reducing, retiming, or replacing an allowlisted effect. The
  server validates skill, timing, input, output, and resource limits before
  calling FFMPEGA. The LLM never supplies arbitrary filters or commands.
- **Dependencies**: Task 7.1.
- **Acceptance criteria**:
  - Unsupported effect requests are rejected before the sidecar call.
  - Effect repair consumes the existing post-render repair budget and cannot
    trigger a separate unbounded loop.
  - Failed effect execution uses native deterministic fallback or fails safely;
    it does not create a redundant LLM attempt.
- **Validation**:
  - Run `tests/test_ffmpega.py`, `tests/test_ffmpega_service.py`, and new
    effect-repair call-cap/invariant tests.
- **Rollback**: Disable effect repair and use the previously validated effect
  plan/native render path.

### Task 7.3: Preserve Effect And Creative Conformance Evidence

- **Location**: `creative_qa.py`, `audit.py`, `outcomes.py`,
  `observability.py`, and UI outcome views.
- **Description**: Expose whether a requested effect was executed, omitted,
  repaired, or remained limited. Keep creative findings advisory unless they
  violate a declared intent contract or a technical promotion gate.
- **Dependencies**: Task 7.2.
- **Acceptance criteria**:
  - Downloads and UI outcome summaries never claim an omitted effect was applied.
  - Audits can attribute effect decisions to the critic evidence and typed
    execution without storing private media/provider bodies.
- **Validation**:
  - Run outcome/audit tests and focused desktop/mobile rendering checks.
- **Rollback**: Preserve existing conformance artifacts and hide the new effect
  repair detail while keeping truthful limitations.

### Sprint 7 Gate

- [ ] Effect review uses final rendered evidence.
- [ ] Effect repair is typed, allowlisted, bounded, and attributable.
- [ ] Sidecar failure does not add a redundant semantic repair call.
- [ ] Creative conformance and limitation reporting remain truthful.
- [ ] Exactly one Sprint 7 commit is created with the proposed message.
- [ ] The rollback point is recorded.
- [ ] Sprint 8 has not started before this gate completes.

## Sprint 8: Add Narrative, Pacing, And Candidate Comparison

**Goal**: Add the higher-level human-like creative pass only after objective
visual repair is measurable, using the same evidence and bounded-call rules.
**Dependencies**: Sprint 6 core loop, Sprint 7 effect evidence, and initial
cross-niche evaluation fixtures.
**Tracked scope**: `render_critic.py`, `render_evidence.py`,
`post_render_repair.py`, `pipeline.py`, `outcomes.py`, `observability.py`, UI
comparison views, prompts/schemas, and eval fixtures.
**Commit**: `feat(mvp): add narrative pacing and candidate comparison`
**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_pipeline.py tests/test_mvp_outcomes.py tests/test_mvp_prompt_versions.py tests/test_mvp_repair_evals.py -v`
- Authorized private A/B review compares baseline and repaired candidates using
  a fixed rubric; raw media and prompts remain outside the repository.

**Rollback point**: Disable the narrative/comparison pass and retain the
objective composition/effect repair loop and deterministic promotion.

### Task 8.1: Add Narrative And Rhythm Critic Dimensions

- **Location**: `render_critic.py`, `render_evidence.py`, `creative_qa.py`,
  `prompts.py`, and schemas/tests.
- **Description**: Supply transcript timing, scene changes, subtitle cadence,
  visual holds, hook-window evidence, and cross-clip ordering as bounded context.
  Ask the LLM to judge pacing, emphasis, coherence, and audience-oriented
  effectiveness without claiming retention or virality. Use deterministic rhythm
  metrics as evidence, not as creative decisions.
- **Dependencies**: Sprint 6 verified repair telemetry.
- **Acceptance criteria**:
  - Narrative/pacing findings are traceable to timestamps, transcript segments,
    and evidence IDs.
  - The critic can recommend a typed timing/cut/emphasis change but cannot
    directly execute it.
- **Validation**:
  - Add fixture tests for slow openings, abrupt cuts, subtitle overload,
    repeated visual holds, and coherent clips that should not be changed.
- **Rollback**: Omit narrative dimensions from critic requests and preserve the
  last accepted plan.

### Task 8.2: Add Evidence-Grounded Candidate Comparison

- **Location**: `post_render_repair.py`, `pipeline.py`, `outcomes.py`,
  `observability.py`, `web/static/mvp/views.js`, and tests.
- **Description**: When a repair occurred, compare original and repaired
  evidence only when both candidates survive deterministic technical gates or
  when the original has an objective blocker that the repair may resolve. The
  LLM may select the more effective creative candidate or declare a tie; it may
  not override a technical block. Reuse existing evidence and avoid a comparison
  call when there is only one eligible candidate.
- **Dependencies**: Task 8.1 and Sprint 6 candidate retention.
- **Acceptance criteria**:
  - A creative comparison call is never made for a single candidate or unchanged
    evidence.
  - The selected candidate, decision rationale, evidence IDs, and tie/uncertainty
    are persisted in sanitized outcome metadata.
  - The UI clearly separates deterministic QA from the LLM creative preference
    and keeps the human favorite action independent.
- **Validation**:
  - Add call-count tests for one candidate, two candidates, tie, technical block,
    and comparison provider failure.
  - Run the affected desktop/mobile browser tests with console failure enabled.
- **Rollback**: Prefer the deterministic promotion decision and keep the
  original candidate when comparison is unavailable.

### Task 8.3: Calibrate The Human Quality Rubric

- **Location**: new `docs/mvp/agentic-video-review-eval.md`,
  `tests/fixtures/mvp_agentic/`, and private evaluation tooling/artifacts.
- **Description**: Define a reviewer rubric for visual clarity, framing,
  caption readability, pacing, narrative coherence, effect appropriateness,
  instruction fidelity, and overall preference. Record ties and uncertainty.
  Do not use a single model's score as ground truth.
- **Dependencies**: Tasks 8.1 and 8.2.
- **Acceptance criteria**:
  - The rubric can compare baseline, repaired, and unchanged candidates across
    the agreed cross-niche fixture set.
  - The plan records owner-approved promotion thresholds before enforce rollout.
- **Validation**:
  - Complete the authorized private review sample and store only aggregate,
    sanitized results.
- **Rollback**: Keep the rubric as report-only evidence and do not enable
  comparison-based promotion.

### Sprint 8 Gate

- [ ] Narrative/pacing review is evidence-grounded and advisory where subjective.
- [ ] Candidate comparison is conditional, bounded, and technically subordinate.
- [ ] Human-eval rubric and aggregate results are recorded.
- [ ] Exactly one Sprint 8 commit is created with the proposed message.
- [ ] The rollback point is recorded.
- [ ] Sprint 9 has not started before this gate completes.

## Sprint 9: Instrument Efficiency, Evals, And Operational Evidence

**Goal**: Make every review/repair call explainable, measurable, privacy-safe,
and regression-tested before production enforcement.
**Dependencies**: Sprints 4-8 complete in report mode.
**Tracked scope**: `observability.py`, `audit.py`, `outcomes.py`, `jobs.py`,
`repair.py`, `checkpoints.py`, eval fixtures/tests, rollout docs, and UI
activity/outcome views.
**Commit**: `test(mvp): add agentic review evals and call telemetry`
**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_repair_evals.py tests/test_mvp_observability.py tests/test_mvp_audit.py tests/test_mvp_outcomes.py -v`
- `./bin/kamal-mvp rollout validate`
- Authorized private canary rehearsal records aggregate call counts, repair
  outcomes, new-defect rate, playable rate, and human preference without raw
  media/provider data.

**Rollback point**: Leave the reviewer in report mode and use the prior
Agentic-only image if telemetry or eval ingestion is incomplete.

### Task 9.1: Add A Complete Call And Decision Ledger

- **Location**: `observability.py`, `audit.py`, `outcomes.py`, `repair.py`,
  `jobs.py`, and new telemetry tests.
- **Description**: Attribute each evidence, critic, repair, comparison, and
  contingency call with stage, reason, input/evidence fingerprint, prompt/schema
  version, checkpoint reuse, attempt number, latency, token/cost fields when
  supplied, disposition, and changed decision. Persist bounded metadata only.
- **Dependencies**: All prior review stages.
- **Acceptance criteria**:
  - A repeated fingerprint is visibly a checkpoint reuse/no-call event.
  - Primary and contingency calls are separately attributable.
  - A third repair attempt is reported as an invariant violation and cannot be
    sent to the provider.
- **Validation**:
  - Run ledger schema, sanitization, restart, provider failure, and call-cap
    tests.
- **Rollback**: Stop persisting new fields while retaining backward-readable
  repair/outcome artifacts.

### Task 9.2: Define Quality And Efficiency Gates

- **Location**: `docs/mvp/agentic-defect-repair-rollout.md`, new eval docs,
  `bin/kamal-mvp`, `tests/test_kamal_config.py`, and eval tests.
- **Description**: Add explicit gates for zero third repair calls, zero promoted
  new technical defects, no raw evidence persistence, stable artifact lineage,
  checkpoint reuse, and owner-approved human preference improvement. Latency and
  tokens are not hard optimization targets, but redundant calls, duplicate
  evidence, and no-op repairs are failures.
- **Dependencies**: Task 9.1 and Sprint 8 rubric.
- **Acceptance criteria**:
  - The validator rejects incomplete or contradictory review/repair rollout
    settings.
  - The canary scorecard separates useful additional calls from redundant calls.
  - Thresholds are recorded before production activation and do not claim broad
    reliability from a small sample.
- **Validation**:
  - Run rollout validator tests and a dry-run canary scorecard against synthetic
    reports.
- **Rollback**: Restore prior rollout validator/profile and keep report-only
  evidence.

### Task 9.3: Complete Security, Privacy, And Failure-Mode Review

- **Location**: `AGENTS.md`, architecture/rollout/audit docs, privacy/security
  tests, `mvp/security.py`, provider adapters, and artifact registration.
- **Description**: Verify prompt injection cannot expand capability, evidence
  IDs are scoped to the current job, secrets/provider bodies are redacted,
  frame bytes are transient, artifact paths are traversal-safe, and provider or
  sidecar failure produces explicit retryable/limited/terminal outcomes.
- **Dependencies**: Task 9.1.
- **Acceptance criteria**:
  - No creative critic output can authorize raw shell/FFmpeg/filesystem/network
    actions.
  - No cross-job evidence or artifact access is possible.
  - Failure states do not silently fall back to the deleted legacy editor.
- **Validation**:
  - Run focused security, artifact traversal, redaction, and failure tests plus
    the full MVP suite.
- **Rollback**: Disable enforcement and return to report-only until every failed
  gate is understood.

### Sprint 9 Gate

- [ ] Call ledger, eval scorecard, privacy review, and failure handling are
  complete.
- [ ] No redundant-call or third-call invariant is left untested.
- [ ] Rollout validator and full MVP test baseline pass.
- [ ] Exactly one Sprint 9 commit is created with the proposed message.
- [ ] The rollback point is recorded.
- [ ] Sprint 10 has not started before this gate completes.

## Sprint 10: Staged Production Rollout

**Goal**: Activate the rendered review/repair loop safely without reintroducing
legacy editing behavior.
**Dependencies**: Sprint 9 gate, strict provider capability evidence, database
backup/restore readiness, and owner-approved human-eval thresholds.
**Tracked scope**: `.env.mvp.example`, `.env.kamal.example`, `config/deploy.yml`,
`bin/kamal-mvp`, `docs/mvp/agentic-defect-repair-rollout.md`, and operational
release evidence. No product code changes should be bundled with a canary flag
edit unless required by the validator.
**Commit**: `ops(mvp): stage rendered review repair rollout`
**Demo/Validation**:

1. `./bin/kamal-mvp db backup`
2. `./bin/kamal-mvp db restore-check`
3. `./bin/kamal-mvp rollout validate`
4. Existing strict 9Router/schema/provider gates, FFMPEGA readiness when
   enabled, `/up`, `/health`, image/version, and database-head checks.
5. Private shadow/report rehearsal followed by the approved private enforce
   canary. Never copy prompt, transcript, media, frames, provider bodies,
   credentials, or raw reports into Git or chat.

**Rollback point**: In one validated configuration change, leave enforce mode,
set review/repair to report or off, restore the previous Agentic image if needed,
and recheck `/up`, `/health`, database readiness, artifact registration,
playback, downloads, and audit summaries. Do not switch back to legacy mode.

### Task 10.1: Stage Shadow And Report

- **Location**: rollout env examples, Kamal config, validator, runbook, and
  release tests.
- **Description**: Deploy the critic and repair path in shadow/report so it
  records eligible decisions and call counts without mutating completion. Verify
  strict schemas in the required prefix order and confirm report evidence is
  private and bounded.
- **Dependencies**: Sprint 9 gate.
- **Acceptance criteria**:
  - Shadow/report output matches enforce eligibility without semantic repair
    mutations.
  - No unexpected provider call, raw evidence, or legacy branch occurs.
- **Validation**:
  - Run `./bin/kamal-mvp rollout validate` and the private canary rehearsal.
- **Rollback**: Set the temporary review/repair controls to off/report and keep
  the last known-good Agentic image.

### Task 10.2: Enforce A Private Canary

- **Location**: same rollout resources plus operational scorecard artifacts.
- **Description**: Enable enforce only for an authorized private cohort. Require
  one primary and at most one new-defect contingency repair, truthful technical
  promotion, no new defect rate regressions, and the owner-approved human-eval
  threshold before broadening.
- **Dependencies**: Task 10.1 and all release gates.
- **Acceptance criteria**:
  - The canary produces playable, registered artifacts only after deterministic
    gates pass.
  - Creative-only limitations remain visible and technical blockers remain
    withheld.
  - Repeated checkpoints and immutable prompt/source reruns are repeatable.
- **Validation**:
  - Run authorized private same-source/same-prompt reruns and aggregate the
    scorecard; do not claim broad reliability from the canary alone.
- **Rollback**: Leave enforce first, disable repair/review, then restore the
  prior image/configuration as a single validated change.

### Task 10.3: Broaden Enforce And Record The Release Evidence

- **Location**: rollout runbook, `docs/mvp/architecture.md`,
  `docs/mvp/implementation-history.md`, and release evidence outside Git when
  private.
- **Description**: Broaden only after the canary gate passes. Record exact image,
  schema/prompt versions, provider capability result, call caps, playable rate,
  new-defect rate, human preference aggregate, rollback point, and open gaps.
- **Dependencies**: Task 10.2.
- **Acceptance criteria**:
  - Current docs describe one Agentic workflow and the adaptive review loop.
  - Open evidence gaps remain explicitly open; no marketing-quality claim is
    inferred from a narrow private sample.
- **Validation**:
  - Run the documented release checks and read the resulting audit/outcome
    summaries back without exposing private payloads.
- **Rollback**: Revert the release configuration/image and retain additive
  evidence for incident review.

### Sprint 10 Gate

- [ ] Shadow, report, private enforce, and broadened enforce gates are complete.
- [ ] Rollback has been rehearsed without resurrecting legacy editing.
- [ ] Release evidence and residual gaps are recorded.
- [ ] Exactly one Sprint 10 commit is created with the proposed message.
- [ ] The rollback point is recorded.
- [ ] Sprint 11 has not started before this gate completes.

## Sprint 11: Final Compatibility And Temporary-Control Cleanup

**Goal**: Remove obsolete legacy compatibility and temporary rollout controls
after production evidence proves the Agentic path, leaving one clean workflow and
deployment rollback rather than multiple editor modes.
**Dependencies**: Sprint 10 broadened-enforce gate, zero active legacy data, a
  final backup/restore check, and owner approval for irreversible cleanup.
**Tracked scope**: legacy workspace/edit-mode branches, temporary review-mode
flags, migrations/models/admin compatibility, env examples, release validator,
docs, tests, and any remaining local-retirement references.
**Commit**: `chore(mvp): remove legacy workflow and rollout compatibility`
**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
- `./bin/kamal-mvp db backup`
- `./bin/kamal-mvp db restore-check`
- `./bin/kamal-mvp rollout validate`
- `bash -n bin/kamal-mvp scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy .kamal/hooks/post-deploy`
- `cd .qa/web && npm run test:smoke` plus one affected desktop/mobile auth/layout test.
- Final `rg` audit confirms no active legacy editor, full-local runtime, or
  temporary report/off/shadow fallback branch remains. A deployment rollback to
  a prior Agentic image remains documented.

**Rollback point**: Use the final pre-cleanup backup and Sprint 10 image/config.
Because dropping schema columns or deleting compatibility code may be
irreversible, no cleanup begins without explicit backup and owner approval.

### Task 11.1: Remove Legacy Data-Path Compatibility

- **Location**: final migration under `migrations/versions/`, `models.py`,
  `jobs.py`, `prompt_versions.py`, `admin.py`, `audit.py`, and migration tests.
- **Description**: After the zero-row/archival gate, remove workflow-version-1
  constraints, legacy job creation/import execution paths, and selectable
  `edit_mode` persistence. Keep only the minimum read-only audit metadata needed
  for retention/legal policy; do not delete media outside the approved retention
  workflow.
- **Dependencies**: Sprint 10 gate and final inventory.
- **Acceptance criteria**:
  - There is one active session/job contract and one Agentic worker path.
  - Unknown historical records fail closed and cannot be rendered or executed.
  - The migration is documented as irreversible after backup, with a tested
    compatibility image for rollback.
- **Validation**:
  - Run connected migration/concurrency tests and verify row/artifact counts.
  - Run `tests/test_mvp_sessions.py`, `tests/test_mvp_jobs.py`,
    `tests/test_mvp_prompt_versions.py`, and `tests/test_mvp_audit.py`.
- **Rollback**: Restore the database backup and deploy the prior compatibility
  image; do not attempt an ad hoc down migration for dropped data.

### Task 11.2: Remove Temporary Review/Repair Modes

- **Location**: `config.py`, env examples, `config/deploy.yml`,
  `bin/kamal-mvp`, `promotion.py`, `repair.py`, `pipeline.py`, rollout docs,
  and tests.
- **Description**: Remove user-visible and temporary shadow/report/off editor
  branches. The production Agentic path becomes mandatory; provider or critic
  unavailability yields a bounded retryable/limited/terminal outcome rather
  than a legacy or silent deterministic editor fallback. Use deployment/image
  rollback as the emergency control.
- **Dependencies**: Task 11.1 and owner approval that enforce evidence is
  sufficient.
- **Acceptance criteria**:
  - No runtime config can select legacy, non-Agentic, or report-only editing.
  - The release validator accepts only the final Agentic profile and rejects
    partial capabilities.
  - Deterministic technical repair/validation remains available as infrastructure
    and is not presented as a creative editor mode.
- **Validation**:
  - Run `tests/test_kamal_config.py`, `tests/test_mvp_repair.py`,
    `tests/test_mvp_pipeline.py`, and the complete suite.
  - Run the final rollout validator and reference audit.
- **Rollback**: Restore the pre-cleanup Agentic image/configuration from Sprint
  10. Do not restore a legacy renderer.

### Task 11.3: Final Documentation, UI, And Artifact Contract Review

- **Location**: `AGENTS.md`, `README.md`, `README_zh.md`,
  `docs/mvp/architecture.md`, `docs/mvp/agentic-defect-repair-rollout.md`,
  `docs/mvp/implementation-history.md`, `web/mvp.html`, `web/static/mvp/`,
  artifact validators, and tests.
- **Description**: Remove stale mode names and explain the final flow, the
  LLM/deterministic ownership boundary, adaptive evidence, repair cap, privacy
  policy, quality limitations, and deployment rollback. Keep English/Chinese
  contract parity and stable public artifact/error names unless an intentional
  migration has completed.
- **Dependencies**: Tasks 11.1 and 11.2.
- **Acceptance criteria**:
  - A maintainer can understand the current product without reading historical
    legacy plans or local-agent instructions.
  - UI/outcome language separates LLM creative preference from deterministic QA
    and human favorite selection.
- **Validation**:
  - Run documentation/reference searches, browser smoke, accessibility checks
    in the affected UI path, and the final MVP suite.
- **Rollback**: Restore documentation/UI from the pre-cleanup commit while
  keeping the final runtime only if the text change is the defect.

### Sprint 11 Gate

- [ ] Legacy data-path compatibility is removed only after backup and inventory.
- [ ] Temporary rollout modes are gone or reduced to deployment rollback, not
  editor alternatives.
- [ ] Final docs, UI, artifacts, tests, and bilingual references agree.
- [ ] Exactly one Sprint 11 commit is created with the proposed message.
- [ ] The rollback point is recorded.
- [ ] The implementation is complete only after the completion checklist below.

## Testing Strategy

- **Unit and schema**:
  - Evidence IDs, fingerprints, temporal-window bounds, sampler limits,
    strict critic/repair/comparison schemas, capability validation, defect
    lifecycle, call budgets, and sanitized artifact serialization.
  - Existing focused suites: `test_mvp_visual_understanding.py`,
    `test_mvp_creative_qa.py`, `test_mvp_frame_quality.py`,
    `test_mvp_repair.py`, `test_mvp_edit_plan.py`,
    `test_mvp_structured_outputs.py`, `test_mvp_defects.py`, and new focused
    render-evidence/critic/post-render-repair tests.

- **Integration**:
  - Mocked 9Router/Mistral/FFMPEGA calls, strict-schema failure, timeout,
    provider refusal, checkpoint restart, immutable prompt/source rerun,
    affected-clip rerender, candidate comparison, asset fallback, subtitle
    validation, and deterministic FFprobe/FFmpeg technical gates.
  - Full pipeline tests through `tests/test_mvp_pipeline.py` with synthetic
    fixtures; `tests/test_mvp_render.py` when FFmpeg is available.

- **Database and migration**:
  - Disposable PostgreSQL database whose name starts with `openstoryline_test`.
  - Inventory, idempotent backfill/archive, advisory locking, row/artifact
    counts, source hashes, restart recovery, backup, restore-check, and final
    zero-legacy-row gates.
  - Report expected database skips when `TEST_DATABASE_URL` is unset; never
    claim connected evidence from the offline suite.

- **Browser and accessibility**:
  - `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`.
  - One affected auth/layout test on desktop and mobile after UI changes.
  - Verify no legacy UI, no edit-mode selector, truthful repair/outcome labels,
    keyboard access, comparison focus handling, and registered-artifact download
    behavior.

- **Security and privacy**:
  - Prompt-injection fixtures, cross-job evidence IDs, artifact traversal,
    provider-key redaction, raw-body exclusion, transient frame cleanup, and
    bounded command output/timeouts.
  - Confirm no media, frame bytes, transcripts, private prompts, credentials,
    or raw provider responses enter Git, PostgreSQL audit JSON, screenshots,
    traces, or chat.

- **Efficiency and quality**:
  - Count useful critic/repair/comparison calls separately from retries,
    checkpoint reuse, no-op repairs, and redundant requests.
  - Require zero duplicate calls for identical evidence/plan/schema fingerprints
    and zero third repair attempts.
  - Use the authorized cross-niche human rubric for creative lift; retain
    deterministic defect escape/new-defect/playable metrics separately.

- **Operational and release**:
  - `./bin/kamal-mvp rollout validate`, provider capability checks when
    authorized, FFMPEGA readiness when enabled, `bash -n` retained scripts,
    `Dockerfile.remote` build, `/up`, `/health`, database head, backup/restore,
    exact image/version, and sanitized audit/outcome summaries.

## Risks And Gotchas

| Risk | Impact | Mitigation | Validation signal |
| --- | --- | --- | --- |
| An undiscovered local consumer exists | Deletion breaks an external workflow | Require owner confirmation, import-boundary tests, and a removal manifest before Sprint 2 | Remote build/import passes; no supported local references remain |
| Legacy sessions or jobs still exist | Data loss or incompatible reads | Inventory first; archive/import explicitly; defer irreversible migration on unknown rows | Zero active legacy rows or approved archive evidence |
| LLM proposes unsafe or impossible edits | Corrupt render, traversal, or unsupported capability | Strict schemas, typed patches, source-bound validation, allowlists, no raw commands | Invalid proposal rejected before execution |
| Adaptive evidence misses a meaningful defect | Creative quality does not improve | Combine anchors, scene/events, deterministic defect windows, uncertainty densification, and human eval | Defect escape rate and reviewer findings across fixtures |
| Repair makes output worse | Visual regressions or lost intent | Preserve original, rerender locally, run deterministic gates, compare candidates, reject no-improvement patches | No promoted new technical defects; candidate lineage is complete |
| Duplicate/redundant calls increase without value | Wasted spend and noisy decisions | Fingerprints, checkpoint reuse, no-op detection, conditional comparison, hard repair cap | Ledger shows reason and zero identical-fingerprint duplicate calls |
| Effects sidecar fails | Missing or inconsistent finishing | Keep native deterministic render, typed omission/fallback, no extra semantic repair call | `EFFECT_OMITTED`/equivalent truthful limitation and playable output |
| Provider or vision review is unavailable | Unclear quality or blocked delivery | Explicit retryable/limited/terminal outcomes; never silently use legacy editor | Outcome/promotion state reflects unavailable evidence |
| Private frames/provider bodies leak | Privacy/security incident | Transient in-memory evidence, bounded metadata, redaction, job-scoped artifacts, audit tests | No bytes/bodies/secrets in persisted evidence or logs |
| Rollout thresholds are overfit to a tiny sample | False professional-quality claim | Cross-niche fixtures, human rubric, Wilson/aggregate reporting, explicit open gaps | Release record distinguishes canary evidence from broad claims |
| Destructive schema cleanup prevents rollback | Recovery requires emergency restore | Backup/restore before final migration; additive compatibility until Sprint 11 | Restore-check and documented pre-cleanup image |

## Rollback Plan

1. **Before Sprint 2**: revert the isolation commit if a hidden local
   dependency is discovered; keep the local application available until the
   owner decides.
2. **After full-local removal**: revert Sprint 2 if an authorized local consumer
   appears; rebuild the local environment from the recorded pre-removal commit.
3. **During Agentic-only MVP cleanup**: deploy the previous compatible image,
   preserve additive PostgreSQL data, and keep legacy media untouched until the
   inventory/migration gate is resolved.
4. **During rendered review development**: set the temporary review/repair
   control to report/off. Continue deterministic QA and the existing Agentic
   pre-render path; never restore a legacy renderer as an untracked fallback.
5. **During canary**: leave enforce mode first, restore report/off, then deploy
   the previous known-good Agentic image. Recheck `/up`, `/health`, DB readiness,
   playback, registered downloads, and sanitized audit/outcome summaries.
6. **After final cleanup**: restore the Sprint 10 database backup and image if a
   dropped compatibility field or removed branch is required for recovery. Do
   not use an ad hoc down migration for deleted data.

## Execution Order

1. Implement Sprint 1 only.
2. Run and record all Sprint 1 validation, including remote build/import evidence.
3. Create exactly one Sprint 1 commit with the proposed message and record its
   rollback point.
4. Start Sprint 2 only after the Sprint 1 gate passes; repeat this gate for every
   sprint through Sprint 11.
5. Do not apply the irreversible Sprint 3/11 data cleanup until inventory,
   backup, restore-check, and owner approval are recorded.
6. Do not enable enforce mode until the human-eval rubric, strict provider
   capability, call ledger, privacy checks, and canary thresholds pass.
7. Do not remove temporary rollout controls until broadened enforce evidence is
   accepted and deployment rollback has been rehearsed.

## Completion Checklist

- [ ] Full local application is removed and remote MVP ownership boundaries are
  explicit.
- [ ] Legacy MVP editing/workspace paths are removed or formally archived with
  no executable compatibility branch.
- [ ] Adaptive rendered evidence is versioned, bounded, checkpointed, and private.
- [ ] LLM creative critic covers visual, caption, pacing, narrative, transition,
  effect, and emphasis decisions without direct execution authority.
- [ ] One primary plus one new-defect contingency repair cap is enforced with no
  redundant or third calls.
- [ ] Localized rerender, candidate comparison, deterministic verification, and
  truthful promotion/outcome reporting pass.
- [ ] Effect-aware and narrative/pacing stages have eval evidence.
- [ ] All sprint-specific validation gates and exactly one commit per sprint are
  recorded.
- [ ] Final security/privacy, migration, browser, release, backup/restore, and
  rollback checks are complete.
- [ ] Residual quality gaps and evidence limits are documented; no unsupported
  professional-quality claim is made.
