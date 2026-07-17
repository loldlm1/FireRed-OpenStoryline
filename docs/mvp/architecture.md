# Remote-only video MVP

This fork adds an opt-in MVP for turning one source video into several social
clips. The MVP is deliberately CPU-first and does not run local AI models.

## Non-negotiable runtime policy

- Text planning and frame understanding use `cx/gpt-5.6-sol` through Codex
  OAuth in 9Router.
- Speech-to-text uses only direct Mistral `voxtral-mini-2602`. FireRed sends
  compressed audio to the fixed official transcription endpoint and requires
  finite, non-empty segment `start`/`end` values.
- Generated images in the full-agent `SearchMedia` path use only
  `cx/gpt-5.5-image` through Codex OAuth in 9Router.
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
2. The authenticated browser uploads a source video and an editing prompt.
3. The server creates the job inside a resumable editing session in PostgreSQL,
   writes a rollback-compatible `job.json` snapshot, and extracts compressed
   mono audio with FFmpeg.
4. Direct Mistral Voxtral transcribes the audio with segment timestamps.
5. `cx/gpt-5.6-sol` receives the transcript and sampled frames through
   9Router and returns a structured clip plan.
6. The server validates duration, bounds, overlap, and output count.
7. FFmpeg renders vertical clips and subtitles on CPU.
8. PostgreSQL ingests the sanitized JSON/SRT evidence and records deterministic
   FFprobe/subtitle structural checks without storing media bytes.
9. The browser downloads individual clips, the manifest, or a ZIP bundle.

## Default remote services

| Purpose | Model | Fallbacks |
| --- | --- | --- |
| Planning and vision | `cx/gpt-5.6-sol` | none |
| Speech-to-text | `voxtral-mini-2602` | key-only, same model |
| Full-agent generated images | `cx/gpt-5.5-image` | none |
| Rendering | FFmpeg on CPU | none |

9Router is intentionally not part of the STT path. Its existing user, port
`20128`, launch command, database, and manual process remain unchanged while it
serves Codex text, vision, and image inference.

Before deployment, `scripts/qa_ninerouter.py --strict-models` validates health,
endpoint-key behavior, the exact Codex catalogs, SSH, and Docker. With
`--live-inference`, it validates structured text, vision input, and decodable
image bytes without persisting provider output. Direct-Mistral validation is a
separate release gate so credentials never cross provider boundaries.

`bin/kamal-mvp` enforces the live provider gates before `setup`, `deploy`, or
`redeploy`. The Docker build context is allowlisted by
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
- Every job belongs to exactly one lightweight editing session. Session and job
  lists use bounded cursor pagination; soft-deleted or expired sessions fail
  closed.
- Every job still owns a directory under `outputs/mvp_jobs/<job_id>` for input,
  work files, and generated media. PostgreSQL stores only validated relative
  artifact paths. It reconstructs current job responses without reading
  `job.json`.
- Every committed transition updates the PostgreSQL job row and appends a
  sanitized ordered event in the same transaction. A derived atomic `job.json`
  snapshot remains during the rollback window; snapshot failure is recorded as
  an event and never makes the file authoritative again.
- Registered JSON/SRT artifacts and terminal `job.json` snapshots are ingested
  as versioned, hashed audit documents with bounded size and sanitized raw text.
  Binary media is excluded. Audit/QC failure is recorded separately and never
  rewrites an already completed render as failed.
- Deterministic QC checks output count, FFprobe structure/duration, and subtitle
  ordering against the validated manifest. Its system verdict is explicitly
  structural and does not represent creative or semantic quality.
- Terminal work files are removed immediately. Source/generated video and ZIP
  media expire after seven days, or immediately when an editing session is
  deleted. JSON/SRT evidence and database audit rows remain for 30 days.
- Editing-session deletion is a soft delete followed by an idempotent media
  purge. Deleted sessions disappear from the normal UI but remain available to
  the audit CLI until audit expiry.
- Audit holds are explicit CLI-only operator actions. They delay audit hard
  deletion but never extend media retention.
- Automatic retention is bounded, advisory-lock protected, and disabled by
  default until a production preview is explicitly reviewed.
- Inputs and outputs are served only through validated job-scoped paths.
- The 9Router endpoint key and direct `MISTRAL_API_KEYS` key ring are delivered
  through Kamal secrets and never written to job state, logs, manifests, or Git.
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
- Direct-port deploys and rollbacks use a tracked `pre-deploy` hook to stop
  only the current FireRed web container after the candidate image is ready.
  This creates a short application-only maintenance window because two
  containers cannot bind the same host port; 9Router remains untouched.

Password rotation is an application-wide sign-out: generate a new Argon2id
hash, update the ignored deploy environment, deploy/restart the application,
and verify that old browser sessions are rejected. For a Sprint 2 rollback,
revert application and deployment configuration together and use the privately
retained legacy web token only with the matching older release.

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
as `./bin/kamal-mvp audit list --since 24h --format json`, `audit show`,
`audit events`, `audit documents`, and `audit verify`. Reviews enter through a
JSON file or stdin, not command arguments. Rotating `kamal app logs` contain
only recent correlation/lifecycle summaries and are not the audit source.
Retention is inspected with `./bin/kamal-mvp retention status` and
`retention preview`; `retention run` mutates only when `--apply` is supplied.
