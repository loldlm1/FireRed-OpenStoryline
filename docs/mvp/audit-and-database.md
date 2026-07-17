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
