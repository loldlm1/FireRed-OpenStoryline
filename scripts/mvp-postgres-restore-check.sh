#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${OPENSTORYLINE_POSTGRES_ADMIN_MODE:-kamal}"
BACKUP_NAME="openstoryline.latest.dump"
EXPECTED_REVISION="${OPENSTORYLINE_EXPECTED_SCHEMA_REVISION:-20260723_0004}"
REQUIRED_TABLE_COUNT=12

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

local_restore_check() {
  local backup_dir="${OPENSTORYLINE_POSTGRES_BACKUP_DIR:-$ROOT_DIR/tmp/postgres-backups}"
  local dump="$backup_dir/$BACKUP_NAME"
  local database="openstoryline_restore_check_${RANDOM}_$$"
  local revision
  local table_count

  for command_name in createdb dropdb pg_restore psql; do
    command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
  done
  [[ -f "$dump" ]] || fail "backup file is missing"
  [[ -n "${PGDATABASE:-}" ]] || fail "PGDATABASE is required in local mode"

  pg_restore --list "$dump" >/dev/null
  createdb --maintenance-db "$PGDATABASE" "$database"
  trap 'dropdb --if-exists --force --maintenance-db "${PGDATABASE}" "$database" >/dev/null 2>&1 || true' EXIT
  pg_restore --exit-on-error --no-owner --no-privileges --dbname "$database" "$dump"
  revision="$(psql --dbname "$database" --no-align --tuples-only --command \
    'SELECT version_num FROM alembic_version LIMIT 1')"
  table_count="$(psql --dbname "$database" --no-align --tuples-only --command \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('auth_sessions', 'login_attempt_buckets', 'editing_sessions', 'session_input_videos', 'prompt_versions', 'video_jobs', 'job_events', 'artifacts', 'audit_documents', 'audit_reviews', 'session_analysis_cache', 'job_stage_checkpoints')")"
  [[ "$revision" == "$EXPECTED_REVISION" ]] || fail "restored schema revision is not current"
  [[ "$table_count" == "$REQUIRED_TABLE_COUNT" ]] || fail "restored database is missing required tables"
  dropdb --if-exists --force --maintenance-db "$PGDATABASE" "$database"
  trap - EXIT
  printf 'PostgreSQL restore check passed for schema revision %s\n' "$EXPECTED_REVISION"
}

remote_restore_check() {
  local container="${OPENSTORYLINE_POSTGRES_CONTAINER:-openstoryline-mvp-db}"
  local command_text
  local target="${KAMAL_SSH_USER:-root}@${KAMAL_HOST:-}"
  local -a ssh_args=(
    -o BatchMode=yes
    -o ConnectTimeout=15
    -p "${KAMAL_SSH_PORT:-22}"
  )

  [[ -n "${KAMAL_HOST:-}" ]] || fail "KAMAL_HOST is required"
  [[ "${KAMAL_SSH_PORT:-22}" =~ ^[0-9]+$ ]] || fail "KAMAL_SSH_PORT must be numeric"
  [[ "$container" =~ ^[A-Za-z0-9_.-]+$ ]] \
    || fail "OPENSTORYLINE_POSTGRES_CONTAINER contains unsupported characters"

  command_text="set -eu
dump=/backups/openstoryline.latest.dump
database=openstoryline_restore_check_\$\$_\$(date +%s)
test -f \"\$dump\" || { printf 'Error: backup file is missing\\n' >&2; exit 1; }
pg_restore --list \"\$dump\" >/dev/null
createdb --username \"\$POSTGRES_USER\" --maintenance-db \"\$POSTGRES_DB\" \"\$database\"
trap 'dropdb --username \"\$POSTGRES_USER\" --if-exists --force --maintenance-db \"\$POSTGRES_DB\" \"\$database\" >/dev/null 2>&1 || true' EXIT
pg_restore --username \"\$POSTGRES_USER\" --exit-on-error --no-owner --no-privileges --dbname \"\$database\" \"\$dump\"
revision=\$(psql --username \"\$POSTGRES_USER\" --dbname \"\$database\" --no-align --tuples-only --command 'SELECT version_num FROM alembic_version LIMIT 1')
table_count=\$(psql --username \"\$POSTGRES_USER\" --dbname \"\$database\" --no-align --tuples-only --command \"SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('auth_sessions', 'login_attempt_buckets', 'editing_sessions', 'session_input_videos', 'prompt_versions', 'video_jobs', 'job_events', 'artifacts', 'audit_documents', 'audit_reviews', 'session_analysis_cache', 'job_stage_checkpoints')\")
test \"\$revision\" = '$EXPECTED_REVISION' || { printf 'Error: restored schema revision is not current\\n' >&2; exit 1; }
test \"\$table_count\" = '$REQUIRED_TABLE_COUNT' || { printf 'Error: restored database is missing required tables\\n' >&2; exit 1; }
dropdb --username \"\$POSTGRES_USER\" --if-exists --force --maintenance-db \"\$POSTGRES_DB\" \"\$database\"
trap - EXIT
printf 'PostgreSQL restore check passed for schema revision %s\\n' '$EXPECTED_REVISION'"

  ssh "${ssh_args[@]}" "$target" "docker exec -i '$container' sh" <<< "$command_text"
}

case "$MODE" in
  local)
    local_restore_check
    ;;
  kamal)
    remote_restore_check
    ;;
  *)
    fail "OPENSTORYLINE_POSTGRES_ADMIN_MODE must be local or kamal"
    ;;
esac
