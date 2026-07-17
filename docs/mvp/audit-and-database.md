# Remote MVP Database, Audit, And Recovery

The remote social-clips MVP uses PostgreSQL as its application database while
keeping video files on the existing persistent output volume. PostgreSQL runs
as the private `db` Kamal accessory on the same VPS and does not publish port
5432. The full local LangChain/MCP profile remains independent.

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

Editing sessions, video jobs, artifacts, and ordered job events are also
PostgreSQL-authoritative. Media remains under `outputs/mvp_jobs/<job_id>`, while
artifact rows retain traversal-safe relative paths, hashes, availability, and
retention timestamps. Each committed job transition writes a compatibility
`job.json` snapshot after the database transaction. Keep those snapshots while
Sprint 2 rollback remains possible, but never treat them as the live source of
truth.

Create or inspect the schema with:

```bash
./bin/kamal-mvp db migrate
./bin/kamal-mvp db current
```

Code rollback after an additive migration leaves the database directory and
tables in place. Do not use Alembic downgrade as a production rollback method.

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
dump, verifies the required tables and Alembic revision, then removes it.

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

Use bounded JSON or NDJSON output when another agent will inspect the result:

```bash
./bin/kamal-mvp audit list --since 24h --limit 50 --format json
./bin/kamal-mvp audit show JOB_ID --limit 200 --format json
./bin/kamal-mvp audit events JOB_ID --limit 200 --format json
./bin/kamal-mvp audit documents JOB_ID --limit 200 --format ndjson
./bin/kamal-mvp audit verify JOB_ID --format json
```

`audit list` also filters by editing session, state, stage, latest verdict,
error code, media availability, and audit hold. Follow its `next_cursor` for
the next bounded page. `audit verify` uses FFprobe plus manifest/subtitle checks
to assess decodability, stream metadata, duration/count agreement, and cue
ordering. Its verdict is structural evidence only; it does not claim creative,
semantic, or visual quality.

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

Source videos, rendered clips, and generated ZIP bundles remain on the output
volume for seven days after a job reaches a terminal state. Work files are
removed immediately after terminal processing. Deleting an editing session in
the browser soft-deletes its database rows and immediately attempts to remove
all of its video media. The browser hides the session at once; the audit CLI can
still inspect its prompts, plans, JSON/SRT evidence, events, QC results, and
reviews until the 30-day audit deadline.

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
`OPENSTORYLINE_AUDIT_RETENTION_DAYS=30`,
`OPENSTORYLINE_RETENTION_INTERVAL_SECONDS=86400`, and
`OPENSTORYLINE_RETENTION_BATCH_SIZE=100` are bounded operational controls; the
example production policy preserves seven and 30 days.

## Initial cutover and rollback gate

Use this order for the first PostgreSQL/audit release. Real server commands
require a separately authorized maintenance window.

1. Boot and verify the private `db` accessory.
2. Run `./bin/kamal-mvp db migrate` and `./bin/kamal-mvp db current`.
3. Run `./bin/kamal-mvp db backup` and `./bin/kamal-mvp db restore-check`.
4. Deploy the application with `OPENSTORYLINE_RETENTION_ENABLED=false`.
5. Run the legacy import dry-run, apply it, then repeat apply to prove
   idempotency.
6. Run audit backfill dry-run/apply and verify bounded job/document counts.
7. Run retention preview twice and review job counts and estimated bytes; the
   output intentionally excludes private payload text.
8. Enable retention only after explicit approval, then redeploy/restart.
9. Verify `/up`, `/health`, `kamal app details`, `kamal accessory details db`,
   recent redacted app logs, audit CLI output, and retention status.

The rollback point is the Sprint 4-compatible image/commit, a restore-checked
`openstoryline.latest.dump`, current compatibility snapshots, and retention
disabled. Stop future deletion first by setting
`OPENSTORYLINE_RETENTION_ENABLED=false`. Additive tables remain compatible with
a code rollback; do not downgrade them automatically. Restore the dump only
with writes stopped and an empty/isolated target check. Media already purged by
expiry or session deletion is irreversible and cannot be recovered from the
database dump.

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
