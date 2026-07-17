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
  local command_text
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

  exec kamal "_${KAMAL_VERSION:-2.12.0}_" accessory exec db \
    --primary --reuse --raw -- sh -lc "$command_text"
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
