# Plan: Remote MVP Reusable Session Workspace And Streaming UX

**Generated**: 2026-07-19
**Status**: Execution in progress on `feat/reusable-session-workspace`
**Estimated Complexity**: High

## Overview

Redesign the remote-only social-clips MVP as a durable creative workspace where one
editing session owns one immutable input video, multiple immutable prompt versions,
multiple run attempts, and the resulting output videos. Users upload the source once,
iterate on prompts without duplicating media, compare results, select a favorite run,
and follow sanitized real-time activity from upload through rendering and QA.

The current system already has the right production foundation: password-authenticated
FastAPI routes, PostgreSQL editing sessions/jobs/artifacts/events, job-scoped FFmpeg
work directories, ordered audit evidence, retention, restart recovery, and a shared
Kamal output volume. The implementation will extend those contracts additively rather
than merge the remote MVP with the full local agent profile.

The frontend will remain a zero-build HTML/CSS/JavaScript application. The current
warm editorial visual language will be retained and refined into a cinematic production
workspace with stronger hierarchy, persistent session context, an accessible activity
timeline, source and output preview, and prompt-version comparison. The monolithic
`web/mvp.html` implementation will be split into focused ES modules and tokenized CSS.

## Current-System Findings

| Concern | Current behavior | Required change |
| --- | --- | --- |
| Frontend | Spanish single-file page with inline CSS/JS | Modular zero-build workspace with complete async, responsive, and accessibility states |
| Session model | `editing_sessions` groups multiple jobs | Add explicit workflow version and one session-owned source video |
| Input video | Every job owns and uploads `input/source.*` | Upload once to `outputs/mvp_sessions/<session_id>/input/` and reference it read-only from runs |
| Prompt history | Prompt text is embedded directly in `video_jobs` | Add immutable, numbered `prompt_versions` with separate run attempts |
| Outputs | Registered artifacts belong to a job | Keep artifact ownership; expose outputs grouped by prompt version and attempt |
| Progress | Browser polls the current job every two seconds | Add sanitized ordered activity events, native FastAPI SSE, reconnect, and polling fallback |
| Rendering feedback | One coarse `rendering` stage | Add per-clip start/completion activity and elapsed-time visibility |
| Upload feedback | Remote page uses a single `fetch` multipart upload | Add bounded sequential chunks, server offset recovery, XHR percentage, and atomic completion |
| Retention | Job media expires seven days after terminal state | Keep job output retention; retain source until seven days after session last activity or session deletion |
| Deployment | Only `web/mvp.html` is packaged; proxy buffers responses | Package scoped static modules and disable response buffering for SSE-compatible deployments |
| Readiness | `/up` requires one exact Alembic revision | Introduce a compatibility bridge before the additive schema migration |

## Product Decisions

- **Target profile**: remote MVP only (`mvp_fastapi.py`, `src/open_storyline/mvp/`, `web/mvp.html`).
- **Frontend stack**: semantic HTML, tokenized CSS, and browser-native ES modules; no React, bundler, or new UI library.
- **Source lifecycle**: one immutable source per reusable session; expiry is seven days after the session's last run activity and is renewed on run creation and terminal completion.
- **Prompt model**: one immutable prompt version can have multiple immutable run attempts.
- **Best-result model**: one user-selected favorite run per session; deterministic QA remains supporting evidence and never auto-selects a creative winner.
- **Live progress**: native FastAPI SSE with ordered replay and polling fallback.
- **Transparency boundary**: show user-facing stages, providers/tools in plain language, attempt counts, elapsed time, clip progress, warnings, and safe failures; never expose chain-of-thought, raw transcripts, provider bodies, credentials, or private traces.
- **Language**: Spanish remains the initial UI language; all new copy and server activity use stable message keys so English can be added without changing API/event contracts.
- **Legacy behavior**: existing sessions/jobs remain readable and downloadable. Existing sessions use workflow version 1 and do not silently acquire a reusable source. New workflow-version-2 sessions use the new contract.
- **Rollout**: ship behind `OPENSTORYLINE_SESSION_WORKSPACE_MODE=legacy|enabled`, defaulting to `legacy` until an authorized canary activates the new workspace.

## Success Measures

- A workflow-version-2 session accepts one source upload and rejects replacement after successful completion.
- Creating or rerunning prompts performs no second source upload and creates no second source-media copy.
- Interrupted uploads resume from the authoritative server offset; after a page reload the user can reselect the same file and continue.
- Prompt versions are monotonically numbered per session, immutable, auditable, and independently rerunnable.
- Every run records its prompt version, attempt number, source hash, settings snapshot, ordered activity, artifacts, timing, and terminal state.
- The browser receives activity within two seconds locally under normal conditions and reconnects using `Last-Event-ID` without duplicating timeline entries.
- Long rendering stages display the current clip, total clip count, stage start time, and elapsed time even when the underlying FFmpeg process has no fine-grained percentage.
- A user can compare two runs, preview registered videos, inspect settings/QA evidence, and select or clear one favorite run.
- Source, output, and audit retention remain bounded and observable; session deletion purges session source and job media without losing authorized audit metadata before audit expiry.
- Desktop and 390px mobile flows have no horizontal overflow, maintain keyboard order, visible focus, usable touch targets, and polite status announcements.
- Provider keys, raw transcripts, prompt text, provider bodies, cookies, and CSRF values never appear in public activity events, logs, screenshots, or browser fixtures.

## Scope

### In Scope

- Additive PostgreSQL schema, migration, constraints, indexes, compatibility fields, and bounded legacy prompt backfill.
- Session-owned resumable source-video upload, validation, preview, expiry renewal, purge, and path safety.
- Immutable prompt versions, rerun attempts, favorite-run selection, and paginated history APIs.
- Job creation from an existing session source without copying or uploading media again.
- Public activity-event schema, replay API, SSE stream, polling fallback, heartbeats, reconnect, and sanitization.
- Pipeline stage instrumentation and per-clip renderer callbacks without changing model/provider policy.
- Complete remote MVP page redesign in modular HTML/CSS/JavaScript.
- Source and output video preview through authenticated, registered, range-capable routes.
- Upload, empty, loading, queued, running, reconnecting, stale, failed, expired, deleted, success, and recovery UI states.
- Accessibility, responsive behavior, reduced motion, security headers, CSP, static packaging, Kamal buffering, docs, tests, rollout, and rollback.

### Out Of Scope

- Changes to the full local agent UI, MCP server, LangChain loop, local models, public MCP tool schemas, or `agent_fastapi.py`.
- React, TypeScript, Tailwind, shadcn/ui, a bundler, a frontend package manager, or a new runtime UI dependency.
- Replacing or editing a source video inside an existing workflow-version-2 session.
- Cross-session content-addressed deduplication or shared-media references.
- Automatic creative-quality or virality ranking.
- Model upgrades, prompt rewrites, provider routing changes, or new provider fallbacks.
- Multi-user accounts, roles, collaboration, comments, or external sharing.
- Production deployment, live provider calls, private-media upload, or production-database mutation during implementation validation.
- Dropping the legacy job prompt/input fields during this plan; they remain rollback snapshots and compatibility data.

## Assumptions

- The existing `OPENSTORYLINE_MAX_UPLOAD_BYTES` limit remains authoritative; this feature changes transfer/reuse behavior, not the maximum accepted source size.
- One sequential active upload per workflow-version-2 session is sufficient. Parallel chunk uploads are intentionally excluded to simplify cross-container consistency.
- All overlapping remote web containers share the existing persistent `/app/outputs` volume and PostgreSQL database.
- The current single-password browser authentication model remains the authorization boundary; this plan does not introduce per-user ownership.
- Supported browsers provide native ES modules, `EventSource`, `XMLHttpRequest` upload progress, dialog, and HTML5 video/range playback capabilities covered by the Chromium QA baseline.
- A user who reloads during upload can reselect the same local file name/size to resume; browsers will not persist or regain filesystem access silently.
- Session activity and retention timestamps use UTC server time and are rendered in the browser's locale.
- Spanish is the only shipped message catalog in this plan, but message keys and interpolation remain language-neutral.

## Design Direction And UX Contract

### Visual Direction

- Preserve the current warm paper, ink, coral signal, mint, and dark production-console palette.
- Evolve the page from a marketing-style stacked form into a focused creative workspace.
- Use expressive editorial headings, precise mono labels for timing/status, and quiet sans-serif body copy.
- Keep the source video and active prompt as the visual center; session navigation and activity are supporting rails.
- Use motion only for upload continuity, stage transitions, and result reveal; honor `prefers-reduced-motion`.
- Avoid generic KPI dashboards, arbitrary gradients, decorative charts, excessive card grids, and hidden critical controls.

### Internal Pattern Evidence

- **Primary**: `cruip://classic/mosaic/dashboard-projects` for authenticated project hierarchy, action placement, and version/run scanning.
- **Support**: `cruip://classic/mosaic/dashboard-monitoring` for compact operational status cards and visible system state.
- Reimplement only the semantic patterns in the repository's plain HTML/CSS/JS stack. Do not carry Alpine, Chart.js, Flatpickr, Moment, Tailwind configuration, demo data, or template assets into the product.

### Desktop Information Architecture

1. Application header: product identity, current session title/source state, logout.
2. Session rail: create session, paginated/recent sessions, source expiry/status, legacy marker.
3. Main workbench: source preview, immutable-source metadata, prompt composer, progressive edit settings, primary run action.
4. Activity panel: upload or run progress, current stage, elapsed time, ordered timeline, reconnect/fallback state, failure recovery.
5. Results/history: prompt-version list, attempts, output previews, settings, QA evidence, favorite, compare action.

### Mobile Reading Order

1. Session selector and source state.
2. Source preview.
3. Prompt composer and run action.
4. Current upload/run activity.
5. Latest outputs.
6. Prompt-version history and compare controls.

### Required UI States

| Surface | Required states |
| --- | --- |
| Authentication | checking, locked, invalid password, rate limited, unavailable, authenticated |
| Session list | loading, empty, selected, legacy, source pending, uploading, ready, expired, delete blocked, deleted |
| Upload | validating, starting, percentage, paused/retrying, offset mismatch recovery, user cancel, server rejection, complete |
| Prompt composer | disabled until source ready, empty, invalid, submitting, queue full, success, retryable failure |
| Activity | connecting, live, reconnecting, polling fallback, stale, terminal success, terminal failure, cancelled |
| History | loading, empty, paginated, long prompt, multiple attempts, output expired/missing, favorite, compare selection |
| Preview | metadata loading, playable, unavailable, purged, unsupported media, download fallback |
| Session deletion | confirmation, active-job block, purge partial failure, success, focus return |

### Accessibility Contract

- Preserve semantic headings, navigation, forms, buttons, lists, progress elements, and dialogs.
- Announce stage transitions and terminal outcomes politely; do not announce every percentage tick.
- Give upload and job progress visible text plus programmatic values, not color/spinner-only state.
- Keep streaming content from stealing focus.
- Return focus after dialogs and destructive confirmations.
- Support keyboard-only session selection, prompt submission, favorite selection, comparison, preview, download, and deletion.
- Keep touch targets at least 44px where practical and avoid overlapping fixed controls with mobile keyboards.
- Maintain legibility at 200% zoom and avoid horizontal page scrolling at 390px.
- Use text/icon/status-shape combinations so state does not rely on color alone.

## Data And Filesystem Contract

### PostgreSQL Additions

| Object | Purpose | Key invariants |
| --- | --- | --- |
| `editing_sessions.workflow_version` | Distinguish legacy per-job uploads from reusable session sources | `1` or `2`; existing rows backfill to `1` |
| `session_input_videos` | One authoritative source-video record per reusable session | Unique session FK; bounded state machine; received bytes never exceed expected; ready rows require safe path, size, hash, and completion time |
| `prompt_versions` | Immutable numbered prompt and settings snapshot | Unique `(editing_session_id, version_number)`; versions are ordered history without speculative branching metadata |
| `video_jobs.prompt_version_id` | Associate every run with a prompt version | Nullable only for rolling compatibility; implementation-created jobs always set it |
| `video_jobs.attempt_number` | Order reruns of the same prompt version | Positive; unique with `prompt_version_id` when non-null |
| `video_jobs.is_favorite` | Select one best run per session | Partial unique index on non-deleted favorite rows permits at most one favorite per session |
| `job_events.audience` | Separate internal audit events from user-visible activity | `internal` or `user`; legacy rows default to `internal` |

### Source State Machine

`pending -> uploading -> validating -> ready -> expired|deleted`

- `failed` is allowed before `ready` and can restart only after its partial file is safely removed.
- Once `ready`, the source filename/path/content cannot be replaced or modified.
- `expired` and `deleted` are terminal for media availability; a new video requires a new session.
- Active jobs block source purge.

### Filesystem Layout

```text
outputs/
  mvp_sessions/
    <session_id>/
      input/
        source.part
        source.<validated_suffix>
  mvp_jobs/
    <job_id>/
      output/
      work/
      job.json
```

- Workflow-version-2 jobs do not copy the source into their job directory.
- `VideoJob.input_data` keeps a sanitized immutable reference snapshot: input-video id, source hash, safe relative path, original filename, and size.
- All resolved paths must remain beneath the expected session or job root.
- Session source bytes remain filesystem-owned; PostgreSQL stores metadata and validated relative paths only.
- `job.json` remains a derived rollback snapshot, never authoritative state.

### Retention Contract

- Ready session source expiry is `last_session_activity + OPENSTORYLINE_MEDIA_RETENTION_DAYS`.
- Run creation and terminal completion renew source expiry.
- Job outputs keep their existing job-level seven-day retention behavior.
- Incomplete uploads expire after `OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS` (recommended default: 24).
- Session deletion immediately attempts to purge source plus all session job media, while database/audit evidence remains until the existing audit expiry.
- Expired source makes the session history read-only; it does not permit replacement.
- Audit holds delay audit hard deletion only and do not extend source/output media.

## API Contract

### Session Source

| Method | Route | Behavior |
| --- | --- | --- |
| `POST` | `/api/mvp/sessions` | Creates workflow version according to the server rollout mode; version 2 starts without a source |
| `POST` | `/api/mvp/sessions/{session_id}/input-video/uploads` | Initializes or resumes the one incomplete upload from validated metadata |
| `GET` | `/api/mvp/sessions/{session_id}/input-video` | Returns bounded source/upload metadata and authoritative offset |
| `PATCH` | `/api/mvp/sessions/{session_id}/input-video/uploads/{upload_id}` | Appends one bounded chunk at the required `Upload-Offset` |
| `POST` | `/api/mvp/sessions/{session_id}/input-video/uploads/{upload_id}/complete` | Verifies size/media, computes SHA-256, atomically renames, and marks ready |
| `DELETE` | `/api/mvp/sessions/{session_id}/input-video/uploads/{upload_id}` | Cancels an incomplete upload and removes partial bytes |
| `GET` | `/api/mvp/sessions/{session_id}/input-video/content` | Serves only a ready registered source inline with range support |

### Prompt Versions And Runs

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/api/mvp/sessions/{session_id}/prompt-versions` | Bounded cursor pagination with bounded recent attempt summaries |
| `POST` | `/api/mvp/sessions/{session_id}/prompt-versions` | Atomically creates an immutable prompt version and first queued run |
| `GET` | `/api/mvp/prompt-versions/{prompt_version_id}` | Returns one version with attempts and artifact summaries |
| `POST` | `/api/mvp/prompt-versions/{prompt_version_id}/runs` | Creates a new attempt using the exact stored prompt/settings/source |
| `PUT` | `/api/mvp/sessions/{session_id}/favorite-run` | Transactionally selects a completed run from the same session |
| `DELETE` | `/api/mvp/sessions/{session_id}/favorite-run` | Clears the favorite without deleting any run |

### Activity And Preview

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/api/mvp/jobs/{job_id}/events` | Bounded user-visible replay after an optional sequence cursor |
| `GET` | `/api/mvp/jobs/{job_id}/events/stream` | Authenticated SSE using event sequence as SSE id and `Last-Event-ID` for replay |
| `GET` | `/api/mvp/jobs/{job_id}/artifacts/{artifact_name}/preview` | Inline preview for registered available video/image/audio artifacts only |

### Compatibility

- Keep existing job, artifact-download, bundle, auth, health, and session routes.
- Preserve the retired unscoped `POST /api/mvp/jobs` response.
- Keep legacy `POST /api/mvp/sessions/{session_id}/jobs` available only for workflow-version-1 sessions during the rollback window.
- Return stable error codes for immutable source, missing/expired source, offset mismatch, upload conflict, invalid source, legacy read-only session, invalid prompt version, invalid rerun, and favorite ownership failures.
- Continue same-origin cookie auth and CSRF enforcement on every state-changing route.

## Public Activity Event Contract

User-visible SSE/REST events use a bounded structured payload such as:

```json
{
  "schema_version": 1,
  "sequence": 17,
  "category": "render",
  "status": "progress",
  "message_key": "activity.rendering_clip",
  "stage": "rendering",
  "progress": 0.76,
  "current": 3,
  "total": 8,
  "elapsed_ms": 48120,
  "retryable": false,
  "occurred_at": "2026-07-19T12:00:00Z"
}
```

Allowed categories: `queue`, `analysis`, `provider`, `planning`, `asset`, `render`,
`qa`, and `system`.

Allowed public information includes stage, safe provider/tool label, model category,
attempt ordinal, selected clip count, sampled frame count, asset count, render clip
index, elapsed time, normalized failure code, and retryability. Prompt text, transcript
text, frame bytes, provider response bodies, request headers, credentials, cookies,
CSRF values, and hidden reasoning are forbidden.

## Named Resources

### Project Instructions And Documentation

- `AGENTS.md`
- `docs/agent-engineering.md`
- `docs/mvp/architecture.md`
- `docs/mvp/audit-and-database.md`
- `docs/mvp/implementation-history.md`
- `docs/mvp/agentic-production-rollout.md`
- `README.md`
- `README_zh.md`

### Backend And Data

- `mvp_fastapi.py`
- `src/open_storyline/mvp/api.py`
- `src/open_storyline/mvp/models.py`
- `src/open_storyline/mvp/database.py`
- `src/open_storyline/mvp/jobs.py`
- `src/open_storyline/mvp/session_media.py` (new)
- `src/open_storyline/mvp/prompt_versions.py` (new)
- `src/open_storyline/mvp/activity.py` (new)
- `src/open_storyline/mvp/pipeline.py`
- `src/open_storyline/mvp/render.py`
- `src/open_storyline/mvp/audit.py`
- `src/open_storyline/mvp/admin.py`
- `src/open_storyline/mvp/retention.py`
- `src/open_storyline/mvp/observability.py`
- `src/open_storyline/mvp/security.py`
- `migrations/versions/20260719_0002_add_reusable_session_workspace.py` (new)

### Frontend

- `web/mvp.html`
- `web/mvp-legacy.html` (new temporary rollback surface copied from the pre-redesign page)
- `web/static/mvp/styles.css` (new)
- `web/static/mvp/app.js` (new)
- `web/static/mvp/api.js` (new)
- `web/static/mvp/upload.js` (new)
- `web/static/mvp/activity.js` (new)
- `web/static/mvp/views.js` (new)
- `web/static/mvp/messages.js` (new)

### Tests And QA

- `tests/test_mvp_database.py`
- `tests/test_mvp_sessions.py`
- `tests/test_mvp_session_media.py` (new)
- `tests/test_mvp_prompt_versions.py` (new)
- `tests/test_mvp_activity.py` (new)
- `tests/test_mvp_jobs.py`
- `tests/test_mvp_pipeline.py`
- `tests/test_mvp_render.py`
- `tests/test_mvp_retention.py`
- `tests/test_mvp_audit.py`
- `tests/test_mvp_app.py`
- `tests/test_remote_profile.py`
- `tests/test_kamal_config.py`
- `.qa/web/tests/mvp-auth-sessions.spec.ts`
- `.qa/web/tests/mvp-workspace.spec.ts` (new)
- `.qa/web/package.json`

### Packaging And Operations

- `Dockerfile.remote`
- `.dockerignore`
- `config/deploy.yml`
- `.env.mvp.example`
- `.env.kamal.example`
- `.kamal/secrets.example`
- `.kamal/hooks/pre-deploy`
- `bin/kamal-mvp`
- `scripts/mvp-postgres-backup.sh`
- `scripts/mvp-postgres-restore-check.sh`

### Current Official Documentation

- FastAPI 0.128 SSE and `EventSourceResponse`: `https://fastapi.tiangolo.com/tutorial/server-sent-events/`
- FastAPI streaming responses: `https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse`
- MDN server-sent events and reconnection: `https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events`
- MDN upload progress events: `https://developer.mozilla.org/en-US/docs/Web/API/XMLHttpRequestUpload/progress_event`
- Alembic migration cookbook: `https://alembic.sqlalchemy.org/en/latest/cookbook.html`
- Alembic operations reference: `https://alembic.sqlalchemy.org/en/latest/ops.html`
- PostgreSQL 17 constraints: `https://www.postgresql.org/docs/17/ddl-constraints.html`
- Kamal proxy buffering: `https://kamal-deploy.org/docs/configuration/proxy/#buffering`

## Prerequisites

- A clean, restorable PostgreSQL backup before any production migration; implementation uses only a disposable database named with the `openstoryline_test` prefix.
- Current `.venv` dependencies installed and `.qa/web` Playwright dependencies available.
- `ffmpeg` and `ffprobe` installed for media validation/render tests; their absence must be reported as skips where tests already support skipping.
- No live providers, private videos, production database, or deployment is needed for deterministic implementation validation.
- Confirm the production output volume has enough headroom for both legacy job inputs and new session sources during the compatibility window.
- Preserve the current legacy page before replacing `web/mvp.html` so the feature flag has a real rollback surface.

## Dependency And Parallelization Rules

- Sprint 1 must be releasable before Sprint 2 migration is applied in production; it is the schema-compatible rollback bridge.
- Sprint 2 schema must land before session-media, prompt-version, or public-event code writes new columns/tables.
- Sprint 3 source upload must complete before Sprint 4 creates jobs from session sources.
- Sprint 4 run/version APIs must complete before the redesigned composer/history UI.
- Sprint 5 event streaming may be developed after Sprint 2 and in parallel with late Sprint 4 API work, but it must merge before Sprint 6 UI integration.
- Within Sprint 6, CSS/layout and ES-module API orchestration can proceed in parallel after DOM hooks and message keys are agreed.
- Sprint 7 comparison UI depends on artifact preview and favorite APIs from Sprint 4.
- Sprint 8 is the only sprint that may declare release readiness; it still must not deploy without separate authorization.

## Sprint 1: Schema-Compatible Rollout Bridge

**Goal**: Make the current application safely readable against both the existing schema and the upcoming additive schema, and establish an explicit legacy/enabled workspace kill switch without changing user behavior.
**Dependencies**: Current main branch and existing PostgreSQL migration `20260717_0001`.
**Tracked scope**: `src/open_storyline/mvp/database.py`, `mvp_fastapi.py`, `.env.mvp.example`, `.env.kamal.example`, `config/deploy.yml`, `tests/test_mvp_database.py`, `tests/test_mvp_app.py`, `tests/test_kamal_config.py`, `docs/mvp/architecture.md`
**Commit**: `chore(mvp): prepare reusable workspace rollout`
**Demo/Validation**:

- Run the app against schema `20260717_0001`; `/up` remains healthy and the current page/flows remain unchanged in `legacy` mode.
- Unit tests reject unknown/older schema revisions but accept the current and planned additive revision.
- Configuration tests accept only `legacy` or `enabled`, with `legacy` as the safe default.

**Rollback point**: The pre-Sprint-1 commit. No schema or data has changed, so code-only rollback is safe.

### Task 1.1: Replace Exact Revision Readiness With An Explicit Compatibility Set

- **Location**: `src/open_storyline/mvp/database.py`, `tests/test_mvp_database.py`, `tests/test_mvp_app.py`
- **Description**: Replace the single exact-revision equality check with a named allowlist containing `20260717_0001` and the planned `20260719_0002`. Keep unknown, missing, and obsolete revisions fail-closed. Do not infer compatibility from lexical revision ordering.
- **Acceptance criteria**:
  - Current schema remains ready.
  - Planned additive schema is ready for the bridge release.
  - Any unrecognized revision returns `DATABASE_SCHEMA_OUTDATED` without database details.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_database.py tests/test_mvp_app.py -v`
- **Rollback**: Restore the exact-revision check before any newer migration exists.

### Task 1.2: Add The Workspace Rollout Setting Without Activating New Behavior

- **Location**: `mvp_fastapi.py`, `.env.mvp.example`, `.env.kamal.example`, `config/deploy.yml`, `tests/test_kamal_config.py`, `docs/mvp/architecture.md`
- **Description**: Add bounded parsing for `OPENSTORYLINE_SESSION_WORKSPACE_MODE=legacy|enabled`, default `legacy`. In this sprint both modes continue serving the current application; later sprints bind `enabled` to workflow-version-2 session creation and the redesigned page.
- **Acceptance criteria**:
  - Invalid values fail startup with a sanitized configuration error.
  - No new secrets are introduced.
  - `legacy` is explicit in environment examples and Kamal configuration.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_kamal_config.py tests/test_mvp_app.py -v`
  - `PYTHONPATH=src .venv/bin/python -c "from open_storyline.config import load_settings; load_settings('config.toml'); print('config_ok')"`
- **Rollback**: Remove the setting while the application still uses only legacy behavior.

### Sprint 1 Gate

- [ ] All Sprint 1 tasks complete.
- [ ] Sprint 1 validation passes and evidence is recorded.
- [ ] No product behavior, schema, media, or provider contract changed.
- [ ] Residual risks are documented.
- [ ] Exactly one Sprint 1 commit is created with the proposed sprint message.
- [ ] The rollback commit SHA is recorded.
- [ ] Sprint 2 has not started before this gate completes.

## Sprint 2: Additive Session, Prompt, Run, And Event Schema

**Goal**: Introduce durable schema and typed model contracts while keeping legacy sessions and jobs operational.
**Dependencies**: Sprint 1 compatibility bridge.
**Tracked scope**: `migrations/versions/20260719_0002_add_reusable_session_workspace.py`, `src/open_storyline/mvp/models.py`, `src/open_storyline/mvp/database.py`, `src/open_storyline/mvp/jobs.py`, `src/open_storyline/mvp/admin.py`, `bin/kamal-mvp`, `tests/test_mvp_database.py`, `tests/test_mvp_sessions.py`, `tests/test_mvp_jobs.py`, `tests/test_kamal_config.py`
**Commit**: `feat(mvp): add reusable session workspace schema`
**Demo/Validation**:

- Upgrade a disposable `openstoryline_test` database from `20260717_0001` to `20260719_0002`.
- Existing sessions are workflow version 1; a bounded dry-run/apply command creates deterministic prompt-version/attempt references without moving media.
- Legacy store/API tests continue to pass.

**Rollback point**: Sprint 1 commit. Code rollback to Sprint 1 is compatible with schema `20260719_0002`; do not downgrade after workflow-version-2 data exists.

### Task 2.1: Create Additive Tables, Columns, Constraints, And Indexes

- **Location**: `migrations/versions/20260719_0002_add_reusable_session_workspace.py`, `src/open_storyline/mvp/models.py`
- **Description**: Add the objects defined in the Data Contract. Use named checks and foreign keys, bounded strings/JSON, positive counters, a unique source per session, unique prompt version numbers, unique attempt numbers, and a partial unique favorite index. Keep new `video_jobs` foreign keys nullable for rolling compatibility even though new application writes must populate them.
- **Acceptance criteria**:
  - Existing rows receive `workflow_version=1` and `job_events.audience='internal'`.
  - Source metadata cannot represent negative/over-received sizes or a ready source without required completion metadata.
  - Database constraints enforce one source and at most one favorite run per session.
  - No existing table or column is dropped or renamed.
- **Validation**:
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_database.py tests/test_mvp_sessions.py tests/test_mvp_jobs.py -v`
- **Rollback**: Alembic downgrade is permitted only before new workflow data is created; otherwise keep the additive schema and use the Sprint 1 application bridge.

### Task 2.2: Add A Bounded, Resumable Legacy Prompt Backfill

- **Location**: `src/open_storyline/mvp/admin.py`, `bin/kamal-mvp`, `tests/test_mvp_database.py`, `tests/test_mvp_sessions.py`, `tests/test_kamal_config.py`
- **Description**: Keep the Alembic revision schema-only apart from safe column defaults. Add a PostgreSQL advisory-locked `workspace backfill-prompts` command with `--dry-run`, `--apply`, and bounded batch/limit controls. For each unlinked existing job, create one prompt version ordered by `(created_at, id)`, copy the sanitized prompt/request settings, set attempt 1, and link the job in a short transaction. Existing media paths and job snapshots remain untouched. The command must be resumable and idempotent.
- **Acceptance criteria**:
  - Schema upgrade does not scan or rewrite the full `video_jobs` table.
  - Every eligible existing job has a prompt-version link after the apply command completes.
  - Version numbers are gap-free within each legacy session when the backfill completes.
  - Duplicate prompts still remain separate historical versions because they were separate user submissions.
  - Repeated apply runs create no duplicate prompt versions or links.
  - Prompt text is not written to migration/backfill logs or command output.
- **Validation**:
  - Add connected fixtures with multiple sessions, identical prompts, timestamp ties, partial prior backfill, and no jobs.
  - `DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m open_storyline.mvp.admin workspace backfill-prompts --dry-run --limit 10`
  - `DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m open_storyline.mvp.admin workspace backfill-prompts --apply --limit 10`
  - Repeat bounded apply and run final verification against the disposable database to prove idempotency/resume.
  - Run `alembic downgrade 20260717_0001` and `alembic upgrade head` only against a disposable empty/fixture database before any workflow-version-2 data is created.
- **Rollback**: Stop the backfill and retain completed links. Use the pre-migration database backup only if destructive rollback is explicitly required; normal code rollback retains the additive data.

### Task 2.3: Extend Serialization Without Changing Existing Response Keys

- **Location**: `src/open_storyline/mvp/jobs.py`, `tests/test_mvp_jobs.py`, `tests/test_mvp_sessions.py`
- **Description**: Add workflow version, optional source summary, prompt-version id, attempt number, and favorite state to existing serialized objects. Preserve all existing keys and `job.json` compatibility.
- **Acceptance criteria**:
  - Legacy clients can ignore the additive fields.
  - Snapshots contain only sanitized metadata, not source bytes or new private logs.
  - Imported legacy jobs remain importable.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_jobs.py tests/test_mvp_sessions.py -v`
- **Rollback**: Remove additive serialization while retaining schema columns.

### Sprint 2 Gate

- [ ] All Sprint 2 tasks complete.
- [ ] Upgrade, compatibility, bounded backfill, and fixture validation passes and evidence is recorded.
- [ ] Migration lock/backfill behavior and downgrade limits are documented.
- [ ] No source files were moved or copied during backfill.
- [ ] Exactly one Sprint 2 commit is created with the proposed sprint message.
- [ ] The Sprint 1 commit is recorded as the code rollback point.
- [ ] Sprint 3 has not started before this gate completes.

## Sprint 3: Immutable Resumable Session Source Video

**Goal**: Upload, resume, validate, preview, retain, and purge one source video per workflow-version-2 session.
**Dependencies**: Sprint 2 schema.
**Tracked scope**: `src/open_storyline/mvp/session_media.py`, `src/open_storyline/mvp/api.py`, `src/open_storyline/mvp/jobs.py`, `src/open_storyline/mvp/retention.py`, `mvp_fastapi.py`, `.env.mvp.example`, `.env.kamal.example`, `config/deploy.yml`, `tests/test_mvp_session_media.py`, `tests/test_mvp_sessions.py`, `tests/test_mvp_retention.py`, `tests/test_mvp_app.py`
**Commit**: `feat(mvp): persist immutable session source videos`
**Demo/Validation**:

- Initialize an upload, send multiple chunks, interrupt, query the authoritative offset, resume, complete, and preview a small synthetic video.
- Attempting to replace a ready source returns a stable immutable-source conflict.
- Retention preview identifies expired sources without deleting media unless apply mode is explicitly used in a disposable test.

**Rollback point**: Sprint 2 commit plus retained session-source files. Code rollback does not delete new files; keep the database/schema for later reactivation.

### Task 3.1: Implement The Session Media Store And Path Contract

- **Location**: `src/open_storyline/mvp/session_media.py`, `src/open_storyline/mvp/jobs.py`, `mvp_fastapi.py`
- **Description**: Implement source metadata transactions, safe path resolution under `outputs/mvp_sessions`, sequential chunk append, PostgreSQL/advisory coordination across overlapping containers, filesystem-size reconciliation after interrupted commits, atomic `.part` rename, SHA-256 calculation, and bounded FFprobe validation. Stream chunks directly to disk without loading the whole file into memory.
- **Dependencies**: Existing security sanitizers and media probing helpers may be reused; no local inference is introduced.
- **Acceptance criteria**:
  - Each session has at most one source row and one ready source file.
  - Chunk offsets are exact; stale, overlapping, out-of-order, oversized, and cross-session chunks fail closed.
  - A crash after file append but before database commit is reconciled safely from file size on the next authorized request.
  - Ready source metadata includes safe relative path, size, SHA-256, media type, and timestamps.
  - Invalid media never reaches `ready`.
- **Validation**:
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_session_media.py -v`
- **Rollback**: Disable workspace mode, retain source rows/files, and revert the service code; do not delete files as part of code rollback.

### Task 3.2: Add Authenticated Upload, Status, Cancel, Complete, And Preview Routes

- **Location**: `src/open_storyline/mvp/api.py`, `mvp_fastapi.py`, `tests/test_mvp_session_media.py`, `tests/test_mvp_app.py`
- **Description**: Add the session-source API contract. State-changing routes require current CSRF/same-origin protection. Preview uses registered ready metadata, validated path resolution, inline `FileResponse`, correct media type, and range support. Return stable bounded error shapes.
- **Acceptance criteria**:
  - Unauthorized requests fail without revealing source existence.
  - Workflow-version-1 sessions reject the new upload contract with an actionable legacy code.
  - Ready sources reject upload initialization and cancellation.
  - Preview is unavailable for pending, failed, expired, deleted, missing, or unsafe paths.
  - Existing auth/session/error contracts remain intact.
- **Validation**:
  - Add HTTP tests for auth, CSRF, MIME/suffix rejection, size limits, offset mismatch, cancel, complete, range preview, and traversal.
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_session_media.py tests/test_mvp_app.py -v`
- **Rollback**: Remove the new routes while leaving the additive source metadata untouched.

### Task 3.3: Extend Retention For Incomplete And Ready Session Sources

- **Location**: `src/open_storyline/mvp/retention.py`, `src/open_storyline/mvp/session_media.py`, `.env.mvp.example`, `.env.kamal.example`, `config/deploy.yml`, `tests/test_mvp_retention.py`, `tests/test_kamal_config.py`
- **Description**: Add `OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS`, source expiry preview/status/apply accounting, active-job guards, idempotent partial/ready source purge, and session deletion integration. Keep source metadata for audit while media is unavailable.
- **Acceptance criteria**:
  - Incomplete uploads expire independently from ready media.
  - Ready sources expire from last session activity, not original upload time.
  - Active jobs prevent source purge.
  - Purge is idempotent and records only safe metadata/events.
  - Existing job media and audit hold semantics remain unchanged.
- **Validation**:
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_retention.py tests/test_mvp_session_media.py tests/test_kamal_config.py -v`
- **Rollback**: Disable automatic retention, revert source-retention code, and leave metadata/files in place for manual review.

### Sprint 3 Gate

- [ ] All Sprint 3 tasks complete.
- [ ] Upload, resume, validation, preview, concurrency, path-safety, and retention tests pass.
- [ ] No source replacement path exists after `ready`.
- [ ] No private media or credentials were added to fixtures or logs.
- [ ] Exactly one Sprint 3 commit is created with the proposed sprint message.
- [ ] The Sprint 2 commit and source-file preservation note are recorded as rollback points.
- [ ] Sprint 4 has not started before this gate completes.

## Sprint 4: Immutable Prompt Versions, Run Attempts, Source Reuse, And Favorites

**Goal**: Create and rerun auditable prompt versions against the same session source, group outputs by attempt, and let the user select one favorite completed run.
**Dependencies**: Sprint 3 ready source contract.
**Tracked scope**: `src/open_storyline/mvp/prompt_versions.py`, `src/open_storyline/mvp/api.py`, `src/open_storyline/mvp/jobs.py`, `src/open_storyline/mvp/pipeline.py`, `src/open_storyline/mvp/audit.py`, `src/open_storyline/mvp/admin.py`, `tests/test_mvp_prompt_versions.py`, `tests/test_mvp_jobs.py`, `tests/test_mvp_pipeline.py`, `tests/test_mvp_audit.py`, `tests/test_mvp_sessions.py`
**Commit**: `feat(mvp): version prompts and reuse session media`
**Demo/Validation**:

- Upload one synthetic source, create prompt version 1, create prompt version 2, rerun version 1, and confirm all three jobs resolve the same source path/hash without copied input files.
- Mark one completed run favorite, switch the favorite transactionally, and clear it.
- Legacy sessions remain readable and use only their old route during the rollback window.

**Rollback point**: Sprint 3 commit with workspace mode set to `legacy`. Keep prompt/run rows and session source files.

### Task 4.1: Implement Prompt-Version And Attempt Transactions

- **Location**: `src/open_storyline/mvp/prompt_versions.py`, `src/open_storyline/mvp/jobs.py`, `tests/test_mvp_prompt_versions.py`
- **Description**: Under a locked active workflow-version-2 session, allocate the next prompt version and first attempt atomically. Reruns allocate the next attempt without changing prompt text/settings. Enforce queue capacity, source readiness, source expiry renewal, prompt length, settings validation, and same-session ownership.
- **Acceptance criteria**:
  - Concurrent submissions cannot duplicate version or attempt numbers.
  - Prompt versions and their settings are immutable after creation.
  - A rerun uses the exact stored prompt/settings and current immutable source identity.
  - Failed job creation leaves no orphan prompt version unless the version was intentionally persisted and reported as having no run; prefer one atomic transaction.
  - Queue capacity remains global active-job capacity, not a user quota.
- **Validation**:
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_prompt_versions.py tests/test_mvp_jobs.py -v`
- **Rollback**: Disable new version creation and retain rows for audit/read-only access.

### Task 4.2: Create Jobs From Session Source References Without Copies

- **Location**: `src/open_storyline/mvp/jobs.py`, `src/open_storyline/mvp/pipeline.py`, `tests/test_mvp_jobs.py`, `tests/test_mvp_pipeline.py`
- **Description**: Add a workflow-version-2 job creation path that starts queued, stores a source-reference snapshot in `input_data`, resolves the session source safely, and keeps job output/work directories unchanged. Do not create or populate `mvp_jobs/<job_id>/input`. Preserve legacy job creation for workflow-version-1 sessions only.
- **Acceptance criteria**:
  - Multiple attempts read the same immutable source path and SHA-256.
  - Missing, expired, purged, changed-hash, or unsafe sources fail closed before provider calls.
  - Worker restart/recovery continues to process a queued job exactly once.
  - `job.json` remains compatible and identifies prompt version/attempt/source metadata.
- **Validation**:
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_jobs.py tests/test_mvp_pipeline.py -v`
- **Rollback**: Set workspace mode to `legacy`; queued/running v2 jobs must drain or be cancelled before reverting processor code.

### Task 4.3: Add History, Rerun, Favorite, And Preview APIs

- **Location**: `src/open_storyline/mvp/api.py`, `src/open_storyline/mvp/prompt_versions.py`, `tests/test_mvp_prompt_versions.py`, `tests/test_mvp_app.py`
- **Description**: Add bounded prompt-version list/detail, rerun, favorite select/clear, source preview, and registered artifact preview routes. Favorite selection accepts only a completed, non-deleted run from the same session. Artifact preview remains job-scoped, registered, available, and path-safe.
- **Acceptance criteria**:
  - Pagination remains bounded and stable.
  - Prompt history exposes prompt text only to the authorized browser session and never to logs.
  - Favorite changes are atomic and leave at most one selected run.
  - Preview supports media playback/seek without changing the download endpoint.
- **Validation**:
  - Add API cases for cross-session access, incomplete/failed favorite, expired artifact, traversal, and long prompt pagination.
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_prompt_versions.py tests/test_mvp_app.py -v`
- **Rollback**: Remove mutation routes first; keep read-only history/detail access if schema remains.

### Task 4.4: Align Audit Evidence With Prompt Versions And Attempts

- **Location**: `src/open_storyline/mvp/audit.py`, `src/open_storyline/mvp/admin.py`, `tests/test_mvp_audit.py`
- **Description**: Include prompt-version id/number, attempt number, source hash, settings-version metadata, and favorite state in authorized audit output and job snapshots. Preserve redacted bounded CLI/log behavior. Do not ingest binary source media.
- **Acceptance criteria**:
  - Audit comparisons are attributable to an immutable version and attempt.
  - Favorite is labeled as a human selection, not a QA verdict.
  - Prompt/source details never enter observability logs or public activity.
- **Validation**:
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_audit.py -v`
- **Rollback**: Revert additive audit fields while retaining database records.

### Sprint 4 Gate

- [ ] All Sprint 4 tasks complete.
- [ ] Version numbering, attempt concurrency, source reuse, recovery, favorite, preview, audit, and compatibility tests pass.
- [ ] Filesystem inspection confirms no v2 job input copies.
- [ ] Legacy and workflow-version-2 behavior remain explicitly separated.
- [ ] Exactly one Sprint 4 commit is created with the proposed sprint message.
- [ ] Workspace-mode rollback and active-job drain requirements are recorded.
- [ ] Sprint 5 has not started before this gate completes.

## Sprint 5: Sanitized Activity Events And SSE Streaming

**Goal**: Expose trustworthy, reconnectable processing activity from queue through terminal state without leaking private reasoning or provider data.
**Dependencies**: Sprint 2 event audience schema; Sprint 4 run model.
**Tracked scope**: `src/open_storyline/mvp/activity.py`, `src/open_storyline/mvp/api.py`, `src/open_storyline/mvp/jobs.py`, `src/open_storyline/mvp/pipeline.py`, `src/open_storyline/mvp/render.py`, `src/open_storyline/mvp/assets.py`, `src/open_storyline/mvp/stock.py`, `src/open_storyline/mvp/observability.py`, `tests/test_mvp_activity.py`, `tests/test_mvp_pipeline.py`, `tests/test_mvp_render.py`, `tests/test_mvp_jobs.py`, `tests/test_mvp_audit.py`
**Commit**: `feat(mvp): stream sanitized editing activity`
**Demo/Validation**:

- Connect to a job event stream, receive ordered replay and live stage events, disconnect, reconnect with the last id, and receive only missing events.
- Run a mocked pipeline and observe queue, transcription, analysis, planning, rendering per clip, QA, packaging, and terminal activity.
- Confirm public serialization contains no prompt, transcript, provider body, key, cookie, or raw exception text.

**Rollback point**: Sprint 4 commit. The browser can continue using existing job polling; internal events remain authoritative.

### Task 5.1: Define The Versioned Public Activity Schema And Projection

- **Location**: `src/open_storyline/mvp/activity.py`, `src/open_storyline/mvp/jobs.py`, `tests/test_mvp_activity.py`
- **Description**: Define bounded activity categories/status/message keys, field limits, monotonic progress validation, terminal semantics, and a projection that exposes only `audience='user'` events. Internal job/audit events remain inaccessible from browser routes.
- **Acceptance criteria**:
  - Unknown fields and oversized payloads are rejected or sanitized before persistence.
  - Every event has stable sequence, schema version, timestamp, category, status, and message key.
  - Progress never decreases in the public job summary.
  - Failure activity contains stable code, safe stage, and retryability only.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_activity.py tests/test_mvp_jobs.py tests/test_mvp_audit.py -v`
- **Rollback**: Stop writing user-audience events; existing rows remain harmless and unread by legacy code.

### Task 5.2: Add Replay And Native FastAPI SSE Endpoints

- **Location**: `src/open_storyline/mvp/api.py`, `src/open_storyline/mvp/activity.py`, `tests/test_mvp_activity.py`, `tests/test_mvp_app.py`
- **Description**: Add bounded replay after sequence and `EventSourceResponse` streaming with SSE ids, event names, retry hint, heartbeat comments, terminal close, `Last-Event-ID`, disconnect cancellation checkpoints, and bounded database polling. Use existing same-origin session-cookie auth; GET streams require no CSRF token.
- **Acceptance criteria**:
  - Reconnect does not duplicate or skip committed public events.
  - Cross-session/unauthorized access fails closed.
  - Heartbeats contain no data and keep idle proxies/connections observable.
  - Client disconnect releases the generator/database resources promptly.
  - A REST polling fallback can reconstruct the same public timeline.
- **Validation**:
  - Add async tests for initial replay, live append, reconnect, terminal close, disconnect, auth expiry, database failure, and bounded limits.
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_activity.py tests/test_mvp_app.py -v`
- **Rollback**: Remove the stream route and keep the replay/job polling endpoints.

### Task 5.3: Instrument Pipeline Stages And Safe Provider/Tool Activity

- **Location**: `src/open_storyline/mvp/pipeline.py`, `src/open_storyline/mvp/assets.py`, `src/open_storyline/mvp/stock.py`, `src/open_storyline/mvp/observability.py`, `tests/test_mvp_pipeline.py`
- **Description**: Replace ad hoc progress constants with a centralized monotonic stage map and emit plain-language activity metadata for audio extraction, Mistral transcription, scene detection, frame sampling, visual understanding, clip planning, edit planning, generated/Pexels asset resolution, effects, QA, and packaging. Provider retries expose only safe ordinal/category/timing metadata already permitted by provider boundaries.
- **Acceptance criteria**:
  - Legacy and agentic modes produce valid ordered stage sequences.
  - Disabled optional providers appear as skipped/disabled, not as mysterious stalls.
  - Long provider calls show stage start and client-calculated elapsed time.
  - No model reasoning, raw request, transcript, frame, or provider response is emitted.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_pipeline.py tests/test_mvp_assets.py tests/test_mvp_stock.py -v`
- **Rollback**: Revert to coarse job stages; SSE still streams terminal/state events.

### Task 5.4: Add Per-Clip Renderer Progress Callbacks

- **Location**: `src/open_storyline/mvp/render.py`, `src/open_storyline/mvp/pipeline.py`, `tests/test_mvp_render.py`, `tests/test_mvp_activity.py`
- **Description**: Add optional callback hooks before and after each legacy/agentic clip render. Keep rendering synchronous/CPU-bound and call through a safe loop adapter so database activity writes do not corrupt thread boundaries. Do not rewrite FFmpeg execution merely to create artificial percentages.
- **Acceptance criteria**:
  - Activity identifies current clip and total clips.
  - Callback failure cannot fail or corrupt rendering; it records a sanitized observability warning.
  - Renderer behavior and output bytes remain unchanged when no callback is supplied.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_render.py tests/test_mvp_activity.py -v`
- **Rollback**: Remove callbacks and retain coarse rendering stage events.

### Sprint 5 Gate

- [ ] All Sprint 5 tasks complete.
- [ ] Event schema, privacy, ordering, reconnect, disconnect, fallback, pipeline, and render callback tests pass.
- [ ] A redaction review confirms no private reasoning or sensitive payload reaches public events.
- [ ] Existing polling remains functional as fallback.
- [ ] Exactly one Sprint 5 commit is created with the proposed sprint message.
- [ ] The Sprint 4 polling behavior is recorded as rollback.
- [ ] Sprint 6 has not started before this gate completes.

## Sprint 6: Premium Creative Workspace, Upload UX, And Live Activity UI

**Goal**: Replace the remote page with the modular production workspace covering session creation, one-time upload, source preview, prompt creation, and live processing.
**Dependencies**: Sprints 3-5 APIs.
**Tracked scope**: `web/mvp.html`, `web/mvp-legacy.html`, `web/static/mvp/styles.css`, `web/static/mvp/app.js`, `web/static/mvp/api.js`, `web/static/mvp/upload.js`, `web/static/mvp/activity.js`, `web/static/mvp/views.js`, `web/static/mvp/messages.js`, `mvp_fastapi.py`, `Dockerfile.remote`, `.dockerignore`, `tests/test_remote_profile.py`, `tests/test_mvp_app.py`, `.qa/web/tests/mvp-auth-sessions.spec.ts`, `.qa/web/tests/mvp-workspace.spec.ts`, `.qa/web/package.json`
**Commit**: `feat(web): redesign remote editing workspace`
**Demo/Validation**:

- Login, create a session, upload a synthetic video with visible progress, restore an interrupted offset, preview the immutable source, submit a prompt, and watch live activity through completion/failure.
- Toggle workspace mode to `legacy` and confirm the preserved page remains usable.
- Exercise desktop and 390px mobile without horizontal overflow or console errors.

**Rollback point**: Set `OPENSTORYLINE_SESSION_WORKSPACE_MODE=legacy` without deleting workflow-version-2 data, or roll code back to Sprint 5.

### Task 6.1: Preserve The Legacy Page And Establish Scoped Static Delivery

- **Location**: `web/mvp-legacy.html`, `mvp_fastapi.py`, `Dockerfile.remote`, `.dockerignore`, `tests/test_remote_profile.py`, `tests/test_mvp_app.py`
- **Description**: Preserve the exact pre-redesign page as a temporary rollback surface. Serve the new page only in `enabled` mode. Mount/package only the required `web/static/mvp/` assets in the remote image. Return no-cache HTML/static headers that avoid mixed-version module loads during deploys.
- **Acceptance criteria**:
  - `legacy` and `enabled` root pages are deterministic.
  - Remote image contains the new modules but no full local UI assets or model resources.
  - Static paths cannot traverse outside the scoped directory.
  - Existing `/health`, `/up`, and `/api/mvp/**` routes are unchanged.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_remote_profile.py tests/test_mvp_app.py -v`
- **Rollback**: Set mode to `legacy`; no database action required.

### Task 6.2: Build The Tokenized Responsive Workspace Shell

- **Location**: `web/mvp.html`, `web/static/mvp/styles.css`, `web/static/mvp/messages.js`, `web/static/mvp/views.js`
- **Description**: Implement the visual/UX contract with semantic landmarks, session rail, main workbench, source card, composer, activity panel, result/history region, dialogs, and mobile reading order. Preserve the existing palette while expanding semantic CSS variables for surface, text, border, focus, success, warning, danger, spacing, radius, and elevation.
- **Acceptance criteria**:
  - Primary action is obvious within two seconds.
  - Current session and immutable source state remain visible while composing or viewing results.
  - Long Spanish prompts, filenames, error messages, and session titles wrap without breaking layout.
  - Focus states, contrast, reduced motion, touch targets, and non-color status cues meet the accessibility contract.
  - No frontend dependency or global style leakage is introduced.
- **Validation**:
  - Manual keyboard/200%-zoom review at desktop and mobile widths.
  - `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
- **Rollback**: Serve `web/mvp-legacy.html` through the mode flag.

### Task 6.3: Implement Resumable Upload With Accessible Percentage

- **Location**: `web/static/mvp/upload.js`, `web/static/mvp/api.js`, `web/static/mvp/app.js`, `web/static/mvp/views.js`, `.qa/web/tests/mvp-workspace.spec.ts`
- **Description**: Use XHR upload progress for each server-bounded chunk and compute overall progress from server offset plus current chunk bytes. Automatically retry bounded transient failures; on reload require the user to reselect the same name/size file before resuming. Keep no token, source path, file handle, session data, or upload metadata in `localStorage`/`sessionStorage`.
- **Acceptance criteria**:
  - Visible and programmatic percentage is monotonic.
  - Stage text distinguishes browser upload, server validation, and ready state.
  - Pause/retry/cancel and offset mismatch have actionable messages.
  - Prompt submission remains disabled until source is ready.
  - Ready source controls no longer offer replace/change.
- **Validation**:
  - Browser test with delayed chunks, interruption, resume, cancel, invalid source, and completed source.
  - Verify browser storage remains empty.
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:desktop`
- **Rollback**: Legacy mode restores the single-upload page; partial uploads expire through Sprint 3 retention.

### Task 6.4: Integrate Prompt Submission And Live Activity With Polling Fallback

- **Location**: `web/static/mvp/activity.js`, `web/static/mvp/api.js`, `web/static/mvp/app.js`, `web/static/mvp/views.js`, `web/static/mvp/messages.js`, `.qa/web/tests/mvp-workspace.spec.ts`
- **Description**: Create prompt versions, select the active run, render SSE activity by message key, update elapsed time locally, reconnect after transient loss, and fall back to bounded event/job polling. Announce stage changes but not every tick. Translate internal stages into user language.
- **Acceptance criteria**:
  - Live, reconnecting, fallback, stale, terminal, and auth-expired states are visibly distinct.
  - Duplicate SSE/replay events are ignored by sequence.
  - The UI can resume an active run after page reload from the session URL.
  - Queue full and failed-provider states keep the prompt/source available for a later rerun.
  - Raw JSON is not used as the primary status UI.
- **Validation**:
  - Browser cases for live events, forced disconnect/reconnect, polling fallback, terminal failure, terminal success, and focus stability.
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:desktop`
- **Rollback**: Keep job polling and switch page mode to `legacy` if the new activity UI fails.

### Sprint 6 Gate

- [ ] All Sprint 6 tasks complete.
- [ ] Static packaging, legacy toggle, upload, live activity, responsive, keyboard, storage, and focused browser tests pass.
- [ ] The new UI uses project-owned content and no copied template dependencies/assets.
- [ ] No screenshots or fixtures contain private media/prompts/provider data.
- [ ] Exactly one Sprint 6 commit is created with the proposed sprint message.
- [ ] The `legacy` mode switch is recorded as the immediate rollback point.
- [ ] Sprint 7 has not started before this gate completes.

## Sprint 7: Prompt History, Output Comparison, Favorites, And State Completeness

**Goal**: Make iterations comparable and auditable while completing responsive, accessibility, performance, expiry, and recovery states.
**Dependencies**: Sprint 4 APIs and Sprint 6 workspace.
**Tracked scope**: `web/mvp.html`, `web/static/mvp/styles.css`, `web/static/mvp/app.js`, `web/static/mvp/api.js`, `web/static/mvp/views.js`, `web/static/mvp/messages.js`, `tests/test_mvp_prompt_versions.py`, `.qa/web/tests/mvp-workspace.spec.ts`, `.qa/web/package.json`
**Commit**: `feat(web): compare prompt versions and outputs`
**Demo/Validation**:

- View numbered prompt versions and attempts, preview outputs, compare two runs, inspect settings/QA evidence, select a favorite, reload, and see the favorite restored.
- Display useful states when source/output media is expired, missing, purged, or a run failed.
- Complete the workflow on desktop and mobile using keyboard/touch controls.

**Rollback point**: Sprint 6 commit; core create/upload/run/activity remains usable without comparison UI.

### Task 7.1: Build Paginated Version And Attempt History

- **Location**: `web/static/mvp/api.js`, `web/static/mvp/app.js`, `web/static/mvp/views.js`, `web/static/mvp/messages.js`, `web/static/mvp/styles.css`
- **Description**: Render prompt versions in newest-first order with version number, prompt excerpt/full disclosure, settings chips, attempt state, timing, output count, QA availability, source identity, and favorite marker. Support bounded load-more pagination and legacy read-only session history.
- **Acceptance criteria**:
  - History remains scannable with long prompts and many attempts.
  - Status does not rely on raw internal stage names.
  - Legacy sessions clearly instruct the user to create a new reusable session for another video/prompt workflow.
  - Empty and expired histories remain informative.
- **Validation**:
  - Browser fixtures for zero, one, twenty, and paginated versions; long text; mixed terminal states; legacy sessions.
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:desktop`
- **Rollback**: Hide the history region while retaining the active run/result UI.

### Task 7.2: Add Lazy Output Preview And Two-Run Comparison

- **Location**: `web/mvp.html`, `web/static/mvp/views.js`, `web/static/mvp/app.js`, `web/static/mvp/styles.css`, `.qa/web/tests/mvp-workspace.spec.ts`
- **Description**: Add on-demand `preload="metadata"` video cards and an accessible comparison dialog/sheet for exactly two selected runs. Compare prompt, settings, timing, clips, structural QA/conformance evidence, and output media without presenting a composite quality score.
- **Acceptance criteria**:
  - Offscreen/history videos do not eagerly download full media.
  - Missing/purged media degrades to metadata plus download/audit guidance.
  - The comparison control is keyboard reachable, traps/returns focus correctly when modal, and becomes a vertical stack on mobile.
  - Different output counts are handled without false alignment.
- **Validation**:
  - Browser tests for media preview, range requests, missing media, comparison selection limits, focus return, and mobile layout.
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:desktop`
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:mobile`
- **Rollback**: Remove comparison UI; keep normal output preview/download.

### Task 7.3: Implement Transactional Favorite UX

- **Location**: `web/static/mvp/api.js`, `web/static/mvp/app.js`, `web/static/mvp/views.js`, `web/static/mvp/messages.js`, `.qa/web/tests/mvp-workspace.spec.ts`, `tests/test_mvp_prompt_versions.py`
- **Description**: Allow selecting, switching, and clearing one completed favorite run. Label the action as the user's choice and keep deterministic QA badges separate.
- **Acceptance criteria**:
  - Optimistic UI rolls back on API failure.
  - Failed/running/cross-session runs cannot be selected.
  - Favorite persists across reload and appears in session summary/history.
  - No automatic winner or misleading quality claim is shown.
- **Validation**:
  - Connected concurrency test for two favorite requests.
  - Browser tests for select, switch, clear, failure rollback, and reload.
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_prompt_versions.py -v`
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:desktop`
- **Rollback**: Hide favorite controls; the database field remains additive.

### Task 7.4: Complete Accessibility, Offline/Stale, And Performance Review

- **Location**: `web/mvp.html`, `web/static/mvp/styles.css`, `web/static/mvp/app.js`, `web/static/mvp/activity.js`, `.qa/web/tests/mvp-workspace.spec.ts`
- **Description**: Audit semantic order, visible focus, announcements, reduced motion, zoom, touch, long text, reconnect/offline/stale behavior, video loading, event-list bounds, and DOM cleanup when switching sessions/runs.
- **Acceptance criteria**:
  - Activity list is bounded or virtualized/paginated enough to avoid unbounded DOM growth.
  - EventSource, timers, object URLs, and video playback are torn down on session/run switches and logout.
  - Important stage changes are announced once; progress updates do not flood screen readers.
  - Mobile keyboard does not hide the primary run action.
- **Validation**:
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:desktop`
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:mobile`
  - Manual reduced-motion, keyboard, and 200%-zoom pass.
- **Rollback**: Revert to Sprint 6 UI while retaining backend history/favorite data.

### Sprint 7 Gate

- [ ] All Sprint 7 tasks complete.
- [ ] History, comparison, preview, favorite, expiry, cleanup, desktop, mobile, keyboard, and reduced-motion checks pass.
- [ ] Manual favorite and deterministic QA remain clearly separate.
- [ ] Browser/network behavior is bounded for long sessions.
- [ ] Exactly one Sprint 7 commit is created with the proposed sprint message.
- [ ] The Sprint 6 workspace is recorded as rollback.
- [ ] Sprint 8 has not started before this gate completes.

## Sprint 8: Security Headers, Proxy Streaming, Documentation, And Release Readiness

**Goal**: Make the completed workspace safe to package and ready for an explicitly authorized staged rollout with tested rollback and recovery.
**Dependencies**: Sprints 1-7.
**Tracked scope**: `mvp_fastapi.py`, `Dockerfile.remote`, `.dockerignore`, `config/deploy.yml`, `.env.mvp.example`, `.env.kamal.example`, `.kamal/secrets.example`, `tests/test_remote_profile.py`, `tests/test_kamal_config.py`, `tests/test_mvp_app.py`, `docs/mvp/architecture.md`, `docs/mvp/audit-and-database.md`, `docs/mvp/implementation-history.md`, `README.md`, `README_zh.md`
**Commit**: `chore(mvp): harden reusable workspace rollout`
**Demo/Validation**:

- Build the remote image, render Kamal config, run the complete deterministic suite, run connected PostgreSQL tests on a disposable database, and run focused Chromium QA.
- Confirm SSE is not proxy-buffered, HTML/ES modules load under CSP, `/up` accepts only known schema revisions, and `legacy` mode remains a data-preserving kill switch.
- Produce an operator rollout/rollback checklist without deploying.

**Rollback point**: `OPENSTORYLINE_SESSION_WORKSPACE_MODE=legacy`, then code rollback to Sprint 1 compatibility bridge if required. Keep schema `20260719_0002` unless a separately authorized restore is performed.

### Task 8.1: Add Scoped Security And Cache Headers

- **Location**: `mvp_fastapi.py`, `tests/test_mvp_app.py`
- **Description**: Because inline CSS/JS has been removed, add a restrictive CSP and related response headers for the remote page/static assets: self-only scripts/styles/connect/media, required `blob:` media/object URLs only where used, no objects, no framing, self base/form action, MIME sniffing disabled, and suitable referrer/cache policy. Do not break API downloads or SSE.
- **Acceptance criteria**:
  - New page loads with no CSP console violations.
  - SSE, source/output previews, blob downloads, login, and CSRF requests remain functional.
  - Health/API JSON responses do not expose backend details.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_app.py -v`
  - `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
- **Rollback**: Revert headers independently; keep modular assets.

### Task 8.2: Make Kamal And Remote Packaging SSE-Compatible

- **Location**: `config/deploy.yml`, `Dockerfile.remote`, `.dockerignore`, `tests/test_kamal_config.py`, `tests/test_remote_profile.py`
- **Description**: Package all scoped frontend modules, keep the build-context allowlist narrow, and set Kamal proxy response buffering to false while preserving request size limits and the long response timeout. Keep direct-port mode behavior and health checks unchanged.
- **Acceptance criteria**:
  - Domain/HTTPS proxy config streams responses rather than buffering SSE.
  - Large uploads remain bounded by existing maximum bytes and chunk-level API validation.
  - ZIP/video downloads remain streamable.
  - Remote image still excludes the full local UI/runtime resources and all secrets/private data.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_kamal_config.py tests/test_remote_profile.py -v`
  - `docker build -f Dockerfile.remote .`
- **Rollback**: Re-enable response buffering only if SSE is disabled and polling fallback is active; otherwise use legacy mode/image rollback.

### Task 8.3: Update Architecture, Retention, Audit, User, And Operator Documentation

- **Location**: `docs/mvp/architecture.md`, `docs/mvp/audit-and-database.md`, `docs/mvp/implementation-history.md`, `README.md`, `README_zh.md`, `.env.mvp.example`, `.env.kamal.example`, `.kamal/secrets.example`
- **Description**: Document workflow versions, one-source rule, prompt versions/attempts, favorite semantics, public activity privacy, source/output/audit retention, new paths/routes/env variables, SSE fallback, source expiry, migration compatibility, and rollout/rollback. Keep shared README links aligned. Add no secret values.
- **Acceptance criteria**:
  - User documentation explains that a new video requires a new session.
  - Operator documentation explains source expiry renewal, incomplete upload cleanup, proxy buffering, kill switch, database backup, schema compatibility, and orphan-file handling after rollback.
  - Implementation history is updated only after each implemented sprint has actually passed its gate.
- **Validation**:
  - `rg -n "OPENSTORYLINE_SESSION_WORKSPACE_MODE|OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS|mvp_sessions|prompt_versions|events/stream" README.md README_zh.md docs .env.mvp.example .env.kamal.example config/deploy.yml`
  - Review all examples for placeholders only and bilingual navigation parity.
- **Rollback**: Revert documentation/config references with the corresponding code mode; do not remove historical implementation records after release.

### Task 8.4: Execute Full Local Release Gates And Record Residual Risk

- **Location**: Entire affected scope; no production systems.
- **Description**: Run focused checks first, then the complete deterministic suite, connected database suite when available, FFmpeg tests, browser smoke/workspace tests, shell syntax, Docker build, and Kamal config tests. Review status/diff, source/media privacy, profile separation, env names, schema readiness, and rollback.
- **Acceptance criteria**:
  - All required checks pass or exact skips/limitations are recorded.
  - No live provider, production database, deployment, private media, or VPS mutation occurs.
  - Review gates below are completed with evidence.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
  - `TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_render.py -v`
  - `bash -n run.sh build_env.sh download.sh bin/kamal-mvp scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy`
  - `cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:desktop`
  - `cd .qa/web && QA_PASSWORD='local test password' npm run test:workspace:mobile`
  - `docker build -f Dockerfile.remote .`
- **Rollback**: No live state is changed by these checks. Revert Sprint 8 commit if packaging/config/docs are incorrect.

### Sprint 8 Gate

- [ ] All Sprint 8 tasks complete.
- [ ] Full local, connected PostgreSQL, FFmpeg, browser, shell, Docker, and Kamal validation evidence is recorded with skips clearly identified.
- [ ] Security/privacy, accessibility, database, AI activity, and release review gates pass.
- [ ] Rollout and rollback steps are current and do not require destructive schema downgrade for normal recovery.
- [ ] Exactly one Sprint 8 commit is created with the proposed sprint message.
- [ ] The workspace remains in `legacy` mode until a separately authorized canary activation.
- [ ] The final rollback point and completed plan state are recorded.

## Testing Strategy

### Unit And Domain

- Source state transitions, safe filenames/paths, offsets, size bounds, SHA-256 metadata, and media validation.
- Prompt-version numbering, attempt numbering, immutable settings, queue limits, favorite uniqueness, and source-expiry renewal.
- Public activity schema, message keys, monotonic progress, redaction, terminal semantics, and stage maps.
- Renderer callbacks preserve output behavior and tolerate reporting failures.
- Frontend formatters/message interpolation remain pure and do not render untrusted HTML.

### PostgreSQL Integration

- Migration upgrade from the current revision, fixture backfill, constraints, indexes, and limited downgrade before new data.
- Concurrent upload offsets, version creation, rerun attempt allocation, favorite switching, job recovery, retention locks, and session deletion.
- Cursor pagination for sessions, prompt versions, runs, and events.
- Old/new application compatibility at the Sprint 1/Sprint 2 boundary.
- Use only disposable database names beginning with `openstoryline_test`.

### API And Security

- Auth/CSRF/same-origin enforcement for every new mutation.
- EventSource GET auth, session expiry, cross-session isolation, and no internal-event exposure.
- Upload MIME/suffix/size/offset/traversal/concurrency/replay/cancel/complete failures.
- Source and artifact preview registration, availability, media type, range support, and traversal prevention.
- Stable bounded error shapes and no credentials/provider bodies/raw prompt/transcript leakage.
- CSP, security headers, cookie behavior, no browser token storage, and logout cleanup.

### Pipeline And Rendering

- Legacy and agentic stage ordering under mocked providers.
- Disabled optional capabilities, provider retry summaries, timeout/failure mapping, and redaction.
- Multiple runs reuse the same source without duplicate files.
- FFmpeg legacy/agentic per-clip callbacks, source expiry/missing failures, and artifact registration.
- Existing creative QA and conformance behavior remains advisory/non-ranking.

### Browser And Accessibility

- Login, create session, upload progress, interruption/resume, invalid upload, immutable ready source, source preview.
- Submit prompt, queue state, SSE activity, reconnect, polling fallback, reload active session/run, terminal failure/success.
- Version history, rerun, preview, comparison, favorite, expired media, deletion, and legacy read-only sessions.
- Desktop/mobile hierarchy, no overflow, keyboard order, visible focus, dialog focus return, touch targets, reduced motion, long content, and 200% zoom.
- No console/page errors and no private screenshots/traces/videos retained on success.

### Performance And Reliability

- Chunk memory remains bounded; no whole-video buffering in app memory.
- Session/prompt/event queries are paginated and indexed.
- SSE generator disconnects cleanly and uses bounded polling/heartbeat intervals.
- Activity DOM is bounded; videos use metadata/on-demand loading and object URLs are revoked.
- Job queue/recovery and Kamal overlapping-container execution fencing remain unchanged.
- Source retention preview reports estimated bytes and does not delete without apply mode.

### Operational

- Remote image includes only allowed code/assets and no local inference resources.
- Kamal response buffering is disabled for streaming; upload bounds and health checks remain.
- Schema bridge release precedes migration in production.
- Backup and isolated restore verification precede any authorized migration rollout.
- Feature flag supports immediate data-preserving UI/API fallback.

## Review Gates

### UI Quality Gate

- **UX clarity**: source, prompt, current activity, and next action are immediately clear.
- **Visual hierarchy**: editorial workspace remains distinctive without decorative clutter.
- **Accessibility**: semantic, keyboard, focus, announcements, contrast, motion, touch, and zoom pass.
- **Responsiveness**: desktop and mobile reading orders work with long real data.
- **State completeness**: all async, empty, error, stale, expired, and recovery states are implemented.
- **Maintainability**: modules have clear ownership and CSS uses semantic tokens.

### AI/Activity Gate

- **Behavior**: no provider/model/prompt policy changes are bundled.
- **Transparency**: public stages explain work without claiming unavailable certainty.
- **Privacy**: no chain-of-thought, transcript, provider body, secret, or private trace is exposed.
- **Observability**: internal audit/log evidence remains sufficient when public activity is intentionally narrow.
- **Rollback**: polling remains available and activity instrumentation can be disabled independently.

### PostgreSQL Gate

- **Schema**: constraints, foreign keys, indexes, nullability, defaults, and ownership invariants match the model.
- **Migration**: compatibility bridge, additive upgrade, backfill, lock scope, and downgrade limits are explicit.
- **Queries**: session/version/event pagination and favorite/source lookups match indexes.
- **Data safety**: no destructive cleanup occurs during normal code rollback.
- **Operations**: backup/restore, retention, orphan-file review, and audit expiry are documented.

### Release Gate

- **Docker**: scoped assets, no secrets/private data/local models, health check intact.
- **Kamal**: response streaming, request bounds, persistent output volume, migration hook, and rollback coherent.
- **Health**: `/health` and `/up` remain public/sanitized and schema-aware.
- **Rollback**: `legacy` mode and Sprint 1 bridge work with schema `20260719_0002`.
- **No deployment**: release readiness does not imply a production deploy was performed.

## Risks And Gotchas

| Risk | Impact | Mitigation | Validation signal |
| --- | --- | --- | --- |
| Exact-revision readiness blocks old-image rollback | `/up` fails after migration | Deploy Sprint 1 compatibility bridge before migration; retain additive schema on rollback | Both known revisions pass readiness tests |
| Crash between chunk write and DB commit | Offset/file size diverge | Single sequential writer, advisory/row lock, fsync, reconcile actual file size on next request | Fault-injection resume tests pass |
| Two containers accept concurrent chunks | Source corruption | Cross-container PostgreSQL lock plus exact offset check | Concurrent PATCH test permits one writer only |
| Ready source is replaced accidentally | Audit/source identity breaks | Unique source row and terminal immutability at DB/service/API/UI layers | Replacement returns stable conflict |
| Source expires during a long job | Run fails or reads deleted bytes | Active-job purge guard and expiry renewal at create/terminal transitions | Retention test keeps active source |
| Legacy sessions are silently migrated to wrong source | History becomes misleading | Backfill prompts only; keep workflow version 1 and original media paths | Legacy fixtures remain unchanged/readable |
| Prompt version and job creation partially commit | Orphan version or missing attempt | One transaction and queue/source locks | Failure injection leaves consistent rows |
| Favorite points across sessions | Incorrect best-result display | Transactional same-session validation and partial unique index | Cross-session favorite test fails closed |
| Public events leak prompts/transcripts/provider bodies | Privacy/security incident | Separate audience, strict schema/projection, redaction tests | Forbidden markers absent from events/logs |
| SSE is buffered by Kamal proxy | UI appears stalled | Disable response buffering, heartbeat, browser fallback polling | Domain-mode config and stream timing tests |
| EventSource reconnect duplicates events | Confusing timeline/unbounded DOM | Sequence ids, `Last-Event-ID`, client dedupe, bounded history | Reconnect test receives only missing ids |
| Screen reader is flooded by progress | Unusable live region | Announce stage changes/terminal only; visual updates remain silent | Accessibility review hears one announcement per stage |
| Many video previews consume bandwidth/memory | Slow page/mobile instability | `preload=metadata`, on-demand playback, teardown, bounded history | Network/browser resource checks remain bounded |
| Response buffering disabled affects downloads | Proxy/runtime behavior changes | Validate ZIP/video range and streaming behavior; preserve long timeout | Download/browser smoke succeeds |
| CSP blocks required preview or SSE behavior | Broken UI after hardening | Self/blob directives limited to actual needs; console-fail QA | No CSP console errors |
| New source directory becomes orphaned on rollback | Disk usage without active cleanup | Preserve metadata/schema, document preview/cleanup, re-enable new code for purge | Retention status accounts for source rows |
| Source retention conflicts with user expectation | Unexpected media loss | Visible expiry date, renewal on activity, explicit delete/expired copy | UI and retention tests show exact date/state |
| Automatic QA is mistaken for creative ranking | Misleading product claim | Manual favorite only; QA labeled structural/advisory | Copy/tests contain no winner/virality claim |

## Rollback Plan

### Sprint-Level Rollback

1. **Sprint 1**: Code-only revert; no data/schema impact.
2. **Sprint 2**: Prefer code rollback to Sprint 1 while keeping additive schema. Alembic downgrade is allowed only before workflow-version-2 data exists.
3. **Sprint 3**: Set mode `legacy`; keep source rows/files. Do not delete new media during rollback.
4. **Sprint 4**: Stop new v2 run creation, drain/cancel active v2 jobs, then roll back code while retaining prompt/run data.
5. **Sprint 5**: Disable SSE usage in the UI and use job/event polling; retained public events are additive.
6. **Sprint 6**: Switch root page to `web/mvp-legacy.html`; preserve all v2 data and partial-upload retention.
7. **Sprint 7**: Revert comparison/history UI while keeping core workspace and backend records.
8. **Sprint 8**: Revert packaging/proxy/security/docs changes only if compatible; keep polling active if response buffering is restored.

### Production Rollout Sequence Requiring Separate Authorization

1. Deploy Sprint 1 compatibility bridge while PostgreSQL is still at `20260717_0001`.
2. Verify `/up`, `/health`, login, current sessions/jobs, backup, and isolated restore.
3. Apply additive migration `20260719_0002` and deploy the completed image with workspace mode still `legacy`.
4. Run the legacy prompt backfill in dry-run mode, review counts, then apply bounded batches until verification reports no eligible unlinked jobs.
5. Verify old UI/API, worker recovery, audit, retention preview, static assets, and SSE endpoint authentication.
6. Enable the workspace for one controlled synthetic/private canary only after explicit authorization.
7. Verify one upload, two prompt versions, one rerun, output playback, favorite, audit evidence, retention dates, logs, and SSE timing.
8. Expand activation only after canary evidence is accepted.

### Emergency Recovery

- First action: set `OPENSTORYLINE_SESSION_WORKSPACE_MODE=legacy` and redeploy/restart through the normal Kamal path.
- If code rollback is needed: roll back to the Sprint 1 compatibility image, not the pre-Sprint-1 image.
- Do not downgrade the schema after v2 data exists unless the user explicitly accepts loss and a verified backup/restore plan is executed.
- If persistent data is inconsistent: stop new workspace creation, preserve the output volume and PostgreSQL backup, inspect bounded audit/retention state, and repair idempotently.
- Never remove the output volume or production database as a rollback shortcut.

## Execution Order

1. Implement Sprint 1 only.
2. Run and record all Sprint 1 validation.
3. Create exactly one Sprint 1 commit and record its rollback SHA.
4. Start Sprint 2 only after the Sprint 1 gate passes.
5. Repeat implementation, validation, one commit, and rollback recording for every sprint in order.
6. Do not combine migrations, source upload, prompt versions, SSE, frontend, and release activation into one commit.
7. Do not begin production rollout without a separate explicit authorization after Sprint 8 completes.

## Completion Checklist

- [ ] Every sprint passed its validation gate.
- [ ] Every sprint has exactly one sprint-specific commit.
- [ ] Sprint 1 compatibility bridge is identified as the post-migration code rollback floor.
- [ ] Source video uploads once, remains immutable, and is reused without copies.
- [ ] Prompt versions, attempts, outputs, favorite, audit, and source identity are attributable.
- [ ] SSE activity is ordered, reconnectable, sanitized, and backed by polling.
- [ ] Upload percentage/resume and all required async/error/expiry states are usable.
- [ ] Desktop/mobile accessibility and performance checks pass.
- [ ] Existing remote auth, CSRF, queue, worker, artifact, audit, retention, provider, and render contracts remain valid.
- [ ] Full local, connected database, FFmpeg, browser, Docker, shell, and Kamal checks are recorded accurately.
- [ ] Documentation, env names, profile boundaries, privacy, rollout, and rollback are current.
- [ ] No implementation validation called live providers, uploaded private media, touched production, or deployed.
