# Remote-only video MVP

This fork adds an opt-in MVP for turning one source video into several social
clips. The MVP is deliberately CPU-first and does not run local AI models.

## Non-negotiable runtime policy

- Text planning and frame understanding use `cx/gpt-5.6-sol` through Codex
  OAuth in 9Router.
- Speech-to-text uses only direct Mistral `voxtral-mini-2602`. FireRed sends
  compressed audio to the fixed official transcription endpoint and requires
  finite, non-empty segment `start`/`end` values.
- Generated images use only `cx/gpt-5.5-image` through Codex OAuth in 9Router.
- Optional stock photos and videos use only the fixed Pexels API boundary. Pexels
  is independently disabled by default and never acts as a fallback for 9Router,
  source media, or another provider.
- There are no provider, model, or local-inference fallbacks in these layers.
- FFmpeg is allowed because it performs deterministic media processing rather
  than model inference.
- If a selected provider fails, the job fails closed and persists only
  sanitized attempt metadata.
- Direct Mistral attempts serialize inside each application process. Key
  ordinals, categories, latency, cooldown, and request-sent state are retained;
  credentials, transcripts, audio, and provider response bodies are not logged.

The original OpenStoryline workflow remains available. The new MVP path is
isolated so upstream behavior can continue to be merged into this fork.

## Data flow

1. The browser authenticates with the single project password. The server
   returns an opaque, revocable session cookie and a separate CSRF cookie;
   neither the password nor a reusable API token is stored by JavaScript.
2. With workspace mode enabled, the browser creates a workflow-version-2
   editing session and uploads one source video in resumable, offset-checked
   chunks. The server validates the completed file with FFprobe, records its
   size and SHA-256, and makes it immutable under
   `outputs/mvp_sessions/<session_id>/input/`.
3. The browser creates an immutable prompt version. PostgreSQL atomically
   allocates its first run attempt and references the session source; no media
   is copied into the job directory. Rerunning the same version allocates a new
   attempt with the exact stored prompt, settings, and source identity.
4. The worker writes a rollback-compatible `job.json` snapshot and extracts
   compressed mono audio with FFmpeg from the session source.
5. Direct Mistral Voxtral transcribes the audio with segment timestamps.
6. `cx/gpt-5.6-sol` receives the transcript and bounded global scene samples
   through 9Router and returns a structured clip plan.
7. The server validates duration, bounds, overlap, and output count, then
   samples every selected source window independently for crop evidence.
8. In agentic render mode, the server validates an executable edit plan and
   same-window crop coverage, performs at most one bounded visual re-analysis
   and replan when coverage is insufficient,
   resolves only the generated-image and/or Pexels capabilities explicitly
   permitted for that job, and FFmpeg renders the typed timeline operations and
   subtitles on CPU.
9. Agentic renders remain unregistered candidates while deterministic QA writes
   `render_qa.json`, `frame_quality_qa.json`, `creative_conformance.json`,
   caption-footprint evidence, and `render_promotion.json`. Report mode records
   objective blockers without changing completion behavior. Enforce mode uses
   the configured completion policy: `strict` blocks any objective blocker,
   while the independently enabled `baseline_guaranteed` policy publishes a
   technically valid output with typed creative limitations and still deletes
   candidates with technical blockers. Rhythm and semantic findings remain
   advisory and do not predict retention or virality.
10. PostgreSQL ingests sanitized JSON/SRT evidence, promotion decisions, public
   activity events, and deterministic FFprobe/subtitle checks without storing
   media or frame bytes.
11. Native FastAPI SSE replays ordered, sanitized processing events and sends
   heartbeats. The browser reconnects from the last sequence and falls back to
   bounded event/job polling when the stream is unavailable.
12. The browser compares prompt versions and attempts, previews or downloads
   registered outputs, shows enhanced/limited/retryable/terminal outcomes, and may
   mark one completed run as the human favorite. When the independent retry UX
   flag is enabled, it can rerun the immutable version with typed prior evidence
   or prefill a user-editable improved version. Selecting a favorite never
   changes deterministic QA verdicts.

## Reusable workspace contract and rollout bridge

- `OPENSTORYLINE_SESSION_WORKSPACE_MODE` accepts only `legacy` or `enabled` and
  defaults to `legacy`. `legacy` serves `web/mvp-legacy.html` and creates
  workflow-version-1 sessions. `enabled` serves the modular reusable workspace
  and creates workflow-version-2 sessions.
- A workflow-version-2 session owns exactly one source video. Once validation
  succeeds it cannot be replaced or modified. A different video always requires
  a new session. Existing workflow-version-1 sessions remain readable and keep
  their original job-owned media paths; they are never silently converted into
  reusable sessions.
- Prompt text and run settings are immutable per version. Each version can have
  multiple attempts, all attributable to the same source hash. At most one
  completed run in the session may be the human-selected favorite.
- Database readiness uses an explicit compatibility set rather than revision
  ordering. The legacy `20260717_0001`, reusable-workspace `20260719_0002`,
  and checkpoint `20260721_0003` schemas are accepted; missing, obsolete, and
  unknown revisions fail closed with `DATABASE_SCHEMA_OUTDATED`.
- Deploy the compatibility bridge before applying the additive migration, and
  keep mode `legacy` until a separately authorized canary. A normal rollback
  returns to the bridge application while retaining the additive schema, source
  metadata, prompt versions, activity, and media; it does not infer downgrade
  safety.

## Agentic creative QA

- Deterministic creative QA is enabled independently of agentic rendering with
  `OPENSTORYLINE_CREATIVE_QA_ENABLED`; strict blocker thresholds can be relaxed
  with `OPENSTORYLINE_CREATIVE_QA_STRICT` without changing render completion.
- Structural analysis uses bounded FFprobe/FFmpeg commands and timeouts for
  dimensions, codecs, audio, duration, black frames, freezes, and silence.
- Rhythm evidence measures hook-window activity, scene and overlay changes,
  visual holds, attention gaps, and output-aligned subtitle cadence.
- Conformance evidence compares validated planned operations and requested
  assets with executed operations, used assets, and explained fallbacks.
- `OPENSTORYLINE_COMPLETION_POLICY` defaults to `strict`.
  `baseline_guaranteed` takes effect only when
  `OPENSTORYLINE_LIMITED_OUTPUT_PROMOTION_ENABLED=true`; otherwise strict
  remains authoritative. `render_promotion.json` records both decisions for
  canary comparison and rollback.
- `OPENSTORYLINE_RETRY_UX_ENABLED` independently controls the browser actions;
  disabling it does not remove outcome, lineage, or audit evidence.
- Optional semantic frame review uses the approved 9Router vision route only
  when `OPENSTORYLINE_SEMANTIC_QA_ENABLED=true`. It samples at most
  `OPENSTORYLINE_SEMANTIC_QA_MAX_FRAMES`, stores no frame bytes or raw provider
  body, cannot authorize actions or modify the edit plan, and degrades to an
  unavailable review note on provider failure.
- Cross-niche regression fixtures under `tests/fixtures/mvp_agentic/` contain
  only synthetic schema expectations. The private production session
  `Sesion prueba 1` is an operator-only regression gate and its media,
  transcript, prompts, frames, and reports must never be committed.

## Clip-local crop evidence

- Global samples guide clip selection but never authorize a crop outside their
  timestamps. Every selected clip receives stable start/end, midpoint,
  quartile, scene, and uniform samples bounded by `vision_clip_frame_count`.
- Clip-local frame, region, and track IDs are namespaced by clip before they
  enter the edit planner. `clip_visual_coverage.json` records timestamps,
  observation counts, temporal coverage, maximum gaps, and one repair result;
  it stores no frame bytes.
- Crop targets without sufficient same-window coverage receive one bounded
  higher-density re-analysis and replan. Remaining blockers fail before asset
  acquisition or rendering.
- Center crop is the safe deterministic fallback. Fit or letterbox from an
  automatic crop requires `allow_full_frame_fallback=true`; an oversized target
  without that permission fails instead of silently producing a small picture.

## External asset controls

- `asset_policy` controls 9Router-generated images; `stock_policy` independently
  controls Pexels photos/videos. `auto` is an optional maximum budget,
  `required` is an exact per-clip count, and `off` disables the capability.
  Existing settings without `required` retain their prior optional behavior.
- Explicit required asset language in an immutable prompt is converted into a
  versioned `creative_intent.json` ledger even for older settings snapshots. The
  ledger stores a prompt hash and sanitized contract metadata, never prompt text.
- Every required intent must map to an exact-count typed request, an executed
  timeline overlay, and an explicit planner decision. Missing, unused, dangling,
  or narratively claimed-only assets fail planning before provider resolution.
- The planner receives only effective server/job capabilities and per-clip
  budgets. A provider disabled by configuration is excluded before planning;
  runtime failures fail the complete asset batch and never select another source.
- Pexels search uses fixed `https://api.pexels.com/v1/search` and
  `https://api.pexels.com/videos/search` endpoints with the API key in the
  `Authorization` header. Search count, JSON bytes, redirects, CDN hosts, MIME,
  magic bytes, dimensions, duration, media bytes, timeout, and retries are bounded.
- `asset_manifest.json` stores request hashes, provider-separated call counts,
  SHA-256, creator/source/license provenance, selected-file metadata, and rights
  notices. It never stores Pexels keys or unredacted provider response bodies.
- `OPENSTORYLINE_PEXELS_ENABLED=false` is the default. Enabling it also requires
  `PEXELS_API_KEY` and an `OPENSTORYLINE_PEXELS_LICENSE_REVIEWED_AT=YYYY-MM-DD`
  value no older than 180 days. The release wrapper validates this configuration
  without making a live Pexels request.

## Default remote services

| Purpose | Model | Fallbacks |
| --- | --- | --- |
| Planning and vision | `cx/gpt-5.6-sol` | none |
| Speech-to-text | `voxtral-mini-2602` | key-only, same model |
| Full-agent generated images | `cx/gpt-5.5-image` | none |
| Optional stock photos/videos | Pexels API | none |
| Rendering | FFmpeg on CPU | none |

9Router is intentionally not part of the STT path. Its existing user, port
`20128`, launch command, database, and manual process remain unchanged while it
serves Codex text, vision, and image inference.

Before deployment, `scripts/qa_ninerouter.py --strict-models` validates health,
endpoint-key behavior, the exact Codex catalogs, SSH, and Docker. With
`--live-inference`, it validates structured text, vision input, and decodable
image bytes without persisting provider output. Direct-Mistral validation is a
separate release gate so credentials never cross provider boundaries.

`bin/kamal-mvp` enforces the live 9Router/Mistral provider gates before `setup`,
`deploy`, or `redeploy`. When Pexels is enabled, it additionally requires the
secret and a current recorded license review; no Pexels media is fetched by the
release wrapper. The Docker build context is allowlisted by
`.dockerignore`, so local env files, Kamal secrets, outputs, model resources,
and the development venv never enter the remote-image build context.

## Persistence and security

- PostgreSQL 17 runs as a private Kamal accessory on the same VPS. It is
  authoritative for browser sessions, editing sessions, job state, progress,
  errors, artifacts, and ordered job events.
- `OPENSTORYLINE_WEB_PASSWORD_HASH` contains an Argon2id hash generated with
  `./bin/kamal-mvp auth hash-password`. The raw password is never configured,
  persisted, logged, or passed in a command argument.
- Browser sessions have a 12-hour idle limit and a seven-day absolute limit.
  Only keyed digests of the session token, CSRF token, client address, and user
  agent are stored. Logging in rotates an existing browser session; logout and
  server-side expiry revoke it.
- State-changing `/api/mvp/**` requests require the session cookie, a matching
  CSRF header/cookie, and the configured same origin. Bearer and `X-API-Key`
  authentication are intentionally unsupported.
- PostgreSQL rate-limit buckets protect failed password submissions only, with
  per-client and global minute/day bounds. Successful logins and authenticated
  API/job requests do not consume application quotas.
- Every job belongs to exactly one editing session. Workflow-version-2 sessions
  own one row in `session_input_videos`, immutable rows in `prompt_versions`,
  and versioned attempts in `video_jobs`. Session, prompt-version, job, and event
  lists use bounded cursor pagination; soft-deleted or expired sessions fail
  closed.
- Workflow-version-2 source media lives under
  `outputs/mvp_sessions/<session_id>/input/`. Jobs keep work and generated media
  under `outputs/mvp_jobs/<job_id>` but do not receive a duplicate source.
  Workflow-version-1 jobs retain their original job-owned input. PostgreSQL
  stores only validated relative paths and reconstructs current responses
  without reading `job.json`.
- Every committed transition updates the PostgreSQL job row and appends an
  internal ordered event in the same transaction. A separate allowlisted public
  activity payload exposes only safe message keys, category, status, monotonic
  progress, bounded counts/labels, and sanitized retry/failure metadata through
  replay and SSE. Prompts, transcripts, provider bodies, paths, secrets, and
  internal tool payloads are excluded. A derived atomic `job.json` snapshot
  remains during the rollback window; snapshot failure is recorded as an event
  and never makes the file authoritative again.
- Registered JSON/SRT artifacts and terminal `job.json` snapshots are ingested
  as versioned, hashed audit documents with bounded size and sanitized raw text.
  Binary media is excluded. Audit/QC failure is recorded separately and never
  rewrites an already completed render as failed.
- Deterministic QC checks output count, FFprobe structure/duration, and subtitle
  ordering against the validated manifest. Its system verdict is explicitly
  structural and does not represent creative or semantic quality.
- Terminal work files are removed immediately. Job output media and ZIP bundles
  expire seven days after terminal completion. A ready workflow-version-2 source
  expires seven days after the latest run creation or terminal completion, so
  continued work renews its lifetime. Incomplete uploads expire after
  `OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS` (24 by default). Session deletion
  immediately attempts to purge both session source and job media. JSON/SRT
  evidence and database audit rows remain for 30 days.
- Editing-session deletion is a soft delete followed by an idempotent media
  purge. Deleted sessions disappear from the normal UI but remain available to
  the audit CLI until audit expiry.
- Audit holds are explicit CLI-only operator actions. They delay audit hard
  deletion but never extend media retention.
- Automatic retention is bounded, advisory-lock protected, and disabled by
  default until a production preview is explicitly reviewed.
- Sources and outputs are served only through validated session/job-scoped
  paths. The workspace HTML and modules use a self-scoped Content Security
  Policy, no-store caching, frame denial, MIME-sniff protection, and restrictive
  browser permissions. Kamal disables response buffering so SSE and streamed
  downloads remain incremental while request buffering and upload size bounds
  stay enabled.
- The 9Router endpoint key, direct `MISTRAL_API_KEYS` key ring, and optional
  `PEXELS_API_KEY` are delivered through Kamal secrets and never written to job
  state, logs, manifests, or Git.
- The remote web image runs as fixed UID/GID `65532`. The pre-deploy hook
  idempotently prepares only `KAMAL_OUTPUTS_DIR`; it never changes PostgreSQL
  data or backup ownership. The container health check and Kamal readiness use
  `/up`, while `/health` remains shallow public profile evidence.
- Optional VMAF/XPSNR research runs in the separate, network-disabled
  [`Dockerfile.quality`](../../Dockerfile.quality) image described in
  [quality-sidecar.md](quality-sidecar.md). The remote web image does not ship
  VMAF, research models, or quality-provider dependencies.
- Error bodies are truncated and sanitized before persistence.
- One in-process worker holds PostgreSQL coordinator and execution advisory
  locks. Overlapping Kamal web containers may serve requests, but only the lock
  holder polls and processes queued jobs. If coordinator leadership is lost
  during an active attempt, the execution fence remains held until that attempt
  drains; a standby cannot intentionally overlap or recover the same job.
- Interrupted queued or running jobs are recovered in bounded batches with a
  recovery count and event. `OPENSTORYLINE_MAX_ACTIVE_JOBS` is a queue-capacity
  bound, not a user or RPM/RPD quota.
- Failed jobs expose a sanitized `failure.json` with the stage and selected
  model attempt; artifacts can also be downloaded as one ZIP bundle.
- Kamal deploys the remote-only image, proxy, and persistent output volume.
- Production password login requires the domain/HTTPS path. Direct HTTP mode
  requires `OPENSTORYLINE_ALLOW_INSECURE_HTTP=true` and is limited to a private
  network, VPN, or controlled local test because the password is otherwise
  exposed in transit.
- In IP/custom-port mode the web container publishes `KAMAL_HTTP_PORT`
  directly and does not mutate a pre-existing shared `kamal-proxy` on the VPS.
  Domain/HTTPS mode keeps the normal Kamal proxy path and requires a separate
  maintenance review on a host already serving other Kamal applications.
- Direct-port deploys use a tracked `pre-deploy` hook to migrate the exact
  delivered image on the private `kamal` network, then stop only the current
  FireRed web container. Rollbacks skip forward migration but retain the
  stop-first port handoff. This creates a short application-only maintenance
  window because two containers cannot bind the same host port; 9Router
  remains untouched.

Password rotation is an application-wide sign-out: generate a new Argon2id
hash, update the ignored deploy environment, deploy/restart the application,
and verify that old browser sessions are rejected. When rolling back to a
pre-password-session release, revert application and deployment configuration
together and use the privately retained legacy web token only with that
matching older release.

Existing filesystem jobs can be inspected or imported idempotently without
moving media:

```bash
PYTHONPATH=src python -m open_storyline.mvp.admin import-legacy-jobs \
  --root outputs/mvp_jobs --dry-run
PYTHONPATH=src python -m open_storyline.mvp.admin import-legacy-jobs \
  --root outputs/mvp_jobs --apply
```

The importer uses one `Imported legacy jobs` session by default, re-sanitizes
state, validates every job/artifact path, hashes existing artifacts, records
missing evidence, and skips corrupt or unsafe snapshots. It never imports the
retired SQLite limiter.

Agents and operators inspect persistent evidence through bounded commands such
as `./bin/kamal-mvp audit list --since 24h --format json`,
`audit outcomes --since 24h --format json`, `audit show`, `audit events`,
`audit documents`, and `audit verify`. Reviews enter through a
JSON file or stdin, not command arguments. Rotating `kamal app logs` contain
only recent correlation/lifecycle summaries and are not the audit source.
Retention is inspected with `./bin/kamal-mvp retention status` and
`retention preview`; `retention run` mutates only when `--apply` is supplied.
