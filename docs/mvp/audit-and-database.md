# Remote MVP Database, Audit, And Recovery

The remote social-clips MVP uses PostgreSQL as its application database while
keeping video files on the existing persistent output volume. PostgreSQL runs
as the private `db` Kamal accessory on the same VPS and does not publish port
5432. It is the only supported application database/runtime profile.

## Database topology

- PostgreSQL 17 is pinned by image digest in `config/deploy.yml`.
- The `postgres` bootstrap role remains an administrative role inside the
  accessory. The application connects as the non-superuser `openstoryline`
  role created during first initialization.
- `DATABASE_URL`, `POSTGRES_PASSWORD`, and
  `OPENSTORYLINE_DATABASE_PASSWORD` are private Kamal secrets. Commit only the
  variable names and example placeholders.
- Alembic migrations are explicit. Application startup never creates or
  changes tables from ORM metadata.
- `/health` remains a public liveness/profile response. `/up` also checks that
  PostgreSQL is reachable and at the expected Alembic revision, but returns
  only a generic readiness code.

PostgreSQL also stores password-login state. `auth_sessions` contains only
keyed digests and bounded session metadata; `login_attempt_buckets` contains
keyed client/global counters for failed password submissions. Raw passwords,
password hashes, session cookies, CSRF values, addresses, and user-agent text
must not enter application logs or audit documents. Authenticated API and job
activity has no RPM/RPD quota.

Editing sessions, session input videos, immutable prompt versions, video-job
attempts, artifacts, favorites, and ordered events are PostgreSQL-authoritative.
Workflow-version-2 source media remains under
`outputs/mvp_sessions/<session_id>/input/`; job work and outputs remain under
`outputs/mvp_jobs/<job_id>`. Artifact and source rows retain traversal-safe
relative paths, hashes, availability, and retention timestamps. Each committed
job transition writes a compatibility `job.json` snapshot after the database
transaction. Keep those snapshots during the rollback window, but never treat
them as the live source of truth.

The additive `20260719_0002` revision introduces workflow versions,
`session_input_videos`, `prompt_versions`, run attempts, favorites, and public
activity without removing legacy job columns. The additive `20260721_0003`
revision adds session-analysis and job-stage checkpoints without removing or
rewriting existing rows. The additive `20260723_0004` revision changes the
server default for new sessions to workflow version 2 without rewriting any
historical row. `/up` accepts all four known revisions and fails closed on
unknown revisions.

Create or inspect the schema with:

```bash
./bin/kamal-mvp db migrate
./bin/kamal-mvp db current
./bin/kamal-mvp db readiness
```

In Kamal mode these commands start a disposable container from the exact
delivered application image on the private `kamal` network. They do not reuse
the web container or publish its HTTP port. The tracked `pre-deploy` hook
repeats `db migrate` for the candidate image before stopping the current
direct-port container; rollback skips this forward-migration step.

Code rollback after an additive migration leaves the database directory and
tables in place. `./bin/kamal-mvp rollback VERSION` requires an explicit target
image and runs that image's database readiness contract before Kamal selects
it. Do not use Alembic downgrade as a production rollback method.

Inventory historical ownership before migration or deployment. The report is
aggregate-only and never includes prompt text, media paths, or provider data:

```bash
./bin/kamal-mvp workspace inventory
```

If historical audit records require immutable compatibility prompt versions,
link them in bounded, advisory-locked batches. This does not change workflow
version or move media:

```bash
./bin/kamal-mvp workspace backfill-prompts --dry-run --limit 1000 --batch-size 100
./bin/kamal-mvp workspace backfill-prompts --apply --limit 1000 --batch-size 100
```

Repeat apply until no eligible unlinked jobs remain. Existing workflow-version-1
sessions remain non-executable administrative audit history.

## One-file migration backup

The project intentionally keeps one replaceable custom-format dump at:

```text
/var/lib/openstoryline/backups/openstoryline.latest.dump
```

Create it and prove that it restores into a separate temporary database:

```bash
./bin/kamal-mvp db backup
./bin/kamal-mvp db restore-check
```

Backup creation writes a temporary file, validates its archive, and atomically
replaces the previous dump only after success. The restore check never writes
to the application database; it creates an isolated database, restores the
dump, verifies the required tables and Alembic revision, then removes it. Both
commands stream a short maintenance script over SSH into the running private
PostgreSQL accessory, so database credentials do not enter command arguments.

The dump contains private prompts, transcripts, and audit text once those
features are enabled. Protect it like production data. A dump stored only on
the same VPS is convenient for a planned server move, but it does not protect
against loss of that VPS or disk.

## Moving to a new VPS

1. Stop application writes on the old VPS.
2. Run `db backup` and `db restore-check`.
3. Copy `openstoryline.latest.dump` off the old VPS through SSH/SCP.
4. Boot an empty PostgreSQL accessory on the new VPS.
5. Copy the dump into the new accessory backup directory.
6. Restore only into an empty target database while the application is stopped.
7. Run `db current`, then `db migrate` only if the restored revision is behind.
8. Start the application and verify `/up` and `/health`.

A real restore is deliberately not exposed as an automatic wrapper command.
It must be performed during a maintenance window with an explicit empty-target
check and operator confirmation. Never overwrite a running database implicitly.

Before the PostgreSQL job cutover, create and verify the single dump and retain
the current filesystem job tree. Import legacy snapshots in two explicit steps:

```bash
PYTHONPATH=src python -m open_storyline.mvp.admin import-legacy-jobs \
  --root outputs/mvp_jobs --dry-run
PYTHONPATH=src python -m open_storyline.mvp.admin import-legacy-jobs \
  --root outputs/mvp_jobs --apply
```

The command reports counts only. Re-running `--apply` is idempotent, does not
move or rewrite media, and groups jobs under one `Imported legacy jobs` session.
Corrupt job JSON, invalid IDs, traversal-like artifact names, and missing files
are skipped or recorded without trusting their content. Backfill the imported
job snapshots and registered JSON/SRT evidence after the import:

```bash
./bin/kamal-mvp audit backfill --dry-run --limit 100 --format json
./bin/kamal-mvp audit backfill --apply --limit 100 --format json
```

The backfill is bounded and idempotent. Missing or invalid evidence is recorded
as an audit outcome instead of aborting the batch, and it never reprocesses
media or contacts an external provider.

## Persistent video audit

PostgreSQL is the audit history source. Each job keeps sanitized ordered events,
versioned `job.json` snapshots, every registered JSON/SRT document up to
`OPENSTORYLINE_AUDIT_MAX_DOCUMENT_BYTES`, artifact hashes and availability,
deterministic structural reviews, and optional agent/human reviews. Video,
audio, frame, thumbnail, and ZIP bytes never enter PostgreSQL.

Agentic render candidates also produce bounded `frame_quality_qa.json`,
`render_promotion.json`, `repair_report.json`, and `outcome_report.json`
documents. Frame quality records cropdetect active-area
ratios, decoded frame counts, bounded blur/blockiness signal summaries, and
caption-masked aligned SSIM/PSNR samples without retaining sampled frames or
private paths. `OPENSTORYLINE_RENDER_PROMOTION_MODE=report` preserves completion
while exposing blocker codes. In `enforce`, the default `strict` completion
policy removes a candidate for any blocker. `baseline_guaranteed` may publish
creative-only limitations only when
`OPENSTORYLINE_LIMITED_OUTPUT_PROMOTION_ENABLED=true`; missing or invalid core
media/QA evidence still blocks. The explicit
`OPENSTORYLINE_DELIVERY_POLICY=technical_pass_guaranteed` target supersedes that
compatibility pair and publishes creative-only strict failures with limitations
without rewriting the strict verdict. `qa_enforced` remains the default and
rollback value. Every report preserves strict, technical, and delivery
decisions for comparison. `off` is a rollback-only mode.

Operators and review agents can query the bounded projection with
`./bin/kamal-mvp audit defects --since 24h --format json`, optionally filtering
by `--code`, `--strategy`, `--disposition`, or `--stage`. The query reads only
sanitized PostgreSQL evidence and returns no prompts, transcripts, provider
bodies, media, frames, credentials, or filesystem paths. This projection stays
document-backed for now; normalize or backfill it only after measured query
volume proves a dedicated table is necessary.
Optional offline VMAF/XPSNR analysis remains a read-only operator action in the
separate [quality sidecar](quality-sidecar.md). Its metrics do not change job
state or promotion decisions. A later run can explicitly reuse only the
allowlisted deterministic findings from one prior attempt; the new request and
manifest record that attribution without exposing raw private audit documents
to the planner.

Reusable runs also retain the session source hash, prompt version and attempt
number, settings snapshot, and whether the user selected the completed run as
the favorite. Favorite selection is human creative preference, not an audit or
QA verdict. Public activity is a separate allowlisted projection: it may expose
safe message keys, stage categories, monotonic percentage, bounded counts, and
sanitized retry/failure metadata, but never prompt/transcript text, provider
bodies, internal tool arguments, filesystem paths, cookies, or credentials.
Authenticated clients replay it with `GET /api/mvp/jobs/<job_id>/events` or
stream it with `GET /api/mvp/jobs/<job_id>/events/stream`; SSE resumes from the
last sequence and the browser falls back to bounded polling.

Use bounded JSON or NDJSON output when another agent will inspect the result:

```bash
./bin/kamal-mvp audit list --since 24h --limit 50 --format json
./bin/kamal-mvp audit outcomes --since 24h --limit 5000 --format json
./bin/kamal-mvp audit show JOB_ID --limit 200 --format json
./bin/kamal-mvp audit events JOB_ID --limit 200 --format json
./bin/kamal-mvp audit documents JOB_ID --limit 200 --format ndjson
./bin/kamal-mvp audit verify JOB_ID --format json
```

`audit list` also filters by editing session, state, stage, latest verdict,
error code, media availability, and audit hold. Follow its `next_cursor` for
the next bounded page. `audit outcomes` reports classified sample size, playable
output rate, a 95% Wilson interval, outcome counts, top limitation codes, retry
success, checkpoint reuse, and time to playable output. It sets `claim_ready`
only when the lower confidence bound reaches the 99% target with at least 100
classified attempts; unclassified historical attempts remain explicit.
`audit verify` uses FFprobe plus manifest/subtitle checks
to assess decodability, stream metadata, duration/count agreement, cue ordering,
and the recorded promotion/frame-quality documents. Its verdict confirms
evidence integrity and structure; it does not claim engagement, semantic
relevance, or creative taste.

Record a private agent or human review through stdin or a file so notes do not
enter shell history:

```bash
./bin/kamal-mvp audit review JOB_ID --input review.json --format json
printf '%s' "$REVIEW_JSON" | \
  ./bin/kamal-mvp audit review JOB_ID --input - --format json
```

The JSON object must provide `verdict` (`approved`, `rejected`, or
`needs_review`), `source` (`agent` or `human`), optional descriptive
`reviewer_label`/`notes`, and structured `findings`. The label is audit metadata,
not authenticated personal identity, because the application still uses one
shared password.

Application stdout emits compact correlation-only JSON with request/job/session
IDs, stage, duration, outcome, and sanitized error codes. Use
`kamal app logs` for recent diagnosis, never as audit history: Docker rotates
those logs, while the PostgreSQL events and documents remain authoritative.
Prompts, transcripts, subtitle text, provider bodies, cookies, CSRF values, and
secrets are deliberately absent from stdout.

## Media and audit retention

Workflow-version-1 source videos, rendered clips, and generated ZIP bundles
remain on the output volume for seven days after their job reaches a terminal
state. Workflow-version-2 rendered clips and ZIP bundles follow the same job
deadline, while the one session-owned source expires seven days after the latest
run creation or terminal completion. Reuse therefore renews the source deadline
without copying the media. Work files are removed immediately after terminal
processing.

Incomplete session-source uploads expire after
`OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS` (24 by default). Retention removes their
`.part` data idempotently and records a failed/expired source state. Deleting an
editing session soft-deletes its database rows and immediately attempts to
remove its session source and all job media. The browser hides the session at
once; the audit CLI can still inspect prompt versions, attempts, plans, JSON/SRT
evidence, events, QA results, favorites, and reviews until the 30-day audit
deadline.

Retention uses database timestamps, bounded batches, validated job-root paths,
and one PostgreSQL advisory lock. It never relies on file modification times.
Preview and status commands are read-only:

```bash
./bin/kamal-mvp retention status --format json
./bin/kamal-mvp retention preview --limit 100 --format json
```

`retention run` also previews unless the explicit mutation flag is present:

```bash
./bin/kamal-mvp retention run --limit 100 --format json
./bin/kamal-mvp retention run --apply --limit 100 --format json
```

Audit holds are CLI-only and retain database audit evidence after day 30. They
do not retain video files. Supply the private reason through a JSON file or
stdin so it does not enter shell history:

```bash
printf '%s' '{"reason":"manual quality investigation"}' | \
  ./bin/kamal-mvp audit hold SESSION_ID --set --input - --format json
./bin/kamal-mvp audit hold SESSION_ID --clear --format json
```

The committed production default is
`OPENSTORYLINE_RETENTION_ENABLED=false`. Keep it disabled on the initial
deployment, run and review the preview, then enable the daily scheduler only
with explicit operator approval. `OPENSTORYLINE_MEDIA_RETENTION_DAYS=7`,
`OPENSTORYLINE_INCOMPLETE_UPLOAD_HOURS=24`,
`OPENSTORYLINE_AUDIT_RETENTION_DAYS=30`,
`OPENSTORYLINE_RETENTION_INTERVAL_SECONDS=86400`, and
`OPENSTORYLINE_RETENTION_BATCH_SIZE=100` are bounded operational controls; the
example production policy preserves seven and 30 days.

## Agentic-only workspace rollout and rollback gate

Real server commands require an authorized maintenance window.

1. Run `workspace inventory`; stop if workflow-version-1 has active jobs.
2. Create a backup and complete the isolated restore check.
3. Apply migration `20260723_0004` from the exact candidate image and verify
   `db current`; never hand-edit the default or historical rows.
4. Deploy with `OPENSTORYLINE_RETENTION_ENABLED=false`, then verify `/up`,
   `/health`, authentication, workflow-v2 session creation, immutable upload,
   prompt-version runs, SSE, artifacts, and worker recovery.
5. Run `workspace inventory` again and verify workflow-v1 counts and artifact
   availability are unchanged while no v1 job is executable.
6. Run retention preview twice before separately approving automatic retention.

The normal emergency action is to deploy the previous compatible Agentic image,
not to restore a legacy workspace or renderer. Keep the additive schema,
including `20260723_0004`; do not downgrade after workflow-v2 data exists.
Disable retention before investigating inconsistent state, preserve the output
volume and restore-checked `openstoryline.latest.dump`, and repair bounded
records idempotently. Restore only with writes stopped and an empty/isolated
target.
Media already purged by expiry or session deletion is irreversible and cannot
be recovered from the database dump.

## Password and session operations

Generate an Argon2id password hash locally before loading deploy or provider
secrets:

```bash
./bin/kamal-mvp auth hash-password
```

Store only the resulting hash in the ignored deploy environment. Production
also requires a separate random `OPENSTORYLINE_SECURITY_PEPPER` and an exact
HTTPS `OPENSTORYLINE_PUBLIC_ORIGIN`. Changing the password hash is treated as a
global session rotation: deploy/restart after replacing the hash, then verify
that an old browser session is rejected and a new login succeeds. Do not place
the password in a URL, header, JavaScript storage, shell argument, or database
backup note.
