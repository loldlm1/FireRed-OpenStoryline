#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${OPENSTORYLINE_POSTGRES_ADMIN_MODE:-kamal}"
BACKUP_NAME="openstoryline.latest.dump"

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

local_backup() {
  local backup_dir="${OPENSTORYLINE_POSTGRES_BACKUP_DIR:-$ROOT_DIR/tmp/postgres-backups}"
  local target="$backup_dir/$BACKUP_NAME"
  local temporary

  command -v pg_dump >/dev/null 2>&1 || fail "pg_dump is required"
  command -v pg_restore >/dev/null 2>&1 || fail "pg_restore is required"
  [[ -n "${PGDATABASE:-}" ]] || fail "PGDATABASE is required in local mode"

  install -d -m 700 "$backup_dir"
  temporary="$(mktemp "$backup_dir/.${BACKUP_NAME}.XXXXXX")"
  trap 'rm -f "$temporary"' EXIT
  umask 077
  pg_dump --format=custom --no-owner --no-privileges --file "$temporary"
  pg_restore --list "$temporary" >/dev/null
  chmod 600 "$temporary"
  mv -f "$temporary" "$target"
  trap - EXIT
  printf 'PostgreSQL backup replaced atomically: %s\n' "$target"
}

remote_backup() {
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

  command_text='set -eu
umask 077
target=/backups/openstoryline.latest.dump
temporary="$(mktemp /backups/.openstoryline.latest.dump.XXXXXX)"
trap '\''rm -f "$temporary"'\'' EXIT
pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom --no-owner --no-privileges --file "$temporary"
pg_restore --list "$temporary" >/dev/null
chmod 600 "$temporary"
mv -f "$temporary" "$target"
trap - EXIT
printf "PostgreSQL backup replaced atomically: %s\n" "$target"'

  ssh "${ssh_args[@]}" "$target" "docker exec -i '$container' sh" <<< "$command_text"
}

case "$MODE" in
  local)
    local_backup
    ;;
  kamal)
    remote_backup
    ;;
  *)
    fail "OPENSTORYLINE_POSTGRES_ADMIN_MODE must be local or kamal"
    ;;
esac
