#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${KAMAL_ENV_FILE:-$ROOT_DIR/.env.kamal}"
SERVICE_NAME="openstoryline-mvp-ffmpega"
NETWORK_NAME="kamal"
SOURCE_COMMIT="0cfe2db05df104f95c98cc45e11f129fa5ef5193"

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

[[ -f "$ENV_FILE" ]] || fail "Kamal environment file not found: $ENV_FILE"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

command_name="${1:-}"
case "$command_name" in
  build|deploy|readiness|status|rollback|stop) ;;
  *) fail "usage: scripts/mvp-ffmpega-service.sh {build|deploy|readiness|status|rollback|stop} [VERSION]" ;;
esac

[[ -n "${KAMAL_HOST:-}" ]] || fail "KAMAL_HOST is required"
[[ "${KAMAL_SSH_PORT:-22}" =~ ^[0-9]+$ ]] || fail "KAMAL_SSH_PORT must be numeric"
outputs_dir="${KAMAL_OUTPUTS_DIR:-/var/lib/openstoryline/outputs}"
[[ "$outputs_dir" =~ ^/[A-Za-z0-9._/-]+$ && "$outputs_dir" != "/" && "$outputs_dir" != */ ]] \
  || fail "KAMAL_OUTPUTS_DIR must be a dedicated absolute path"

target="${KAMAL_SSH_USER:-root}@$KAMAL_HOST"
ssh_args=(-o BatchMode=yes -o ConnectTimeout=15 -p "${KAMAL_SSH_PORT:-22}")
version="${2:-$(git -C "$ROOT_DIR" rev-parse HEAD)}"
[[ "$version" =~ ^[A-Za-z0-9._-]+$ ]] || fail "VERSION contains unsupported characters"
image="openstoryline-ffmpega:$version"

remote() {
  ssh "${ssh_args[@]}" "$target" "$@"
}

wait_for_health() {
  local name="$1"
  local attempt
  for attempt in {1..30}; do
    status="$(remote "docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' '$name' 2>/dev/null || true")"
    [[ "$status" == "healthy" ]] && return 0
    [[ "$status" == "unhealthy" || "$status" == "exited" || "$status" == "dead" ]] && break
    sleep 2
  done
  return 1
}

build_image() {
  command -v docker >/dev/null 2>&1 || fail "Docker is required"
  docker build \
    --file "$ROOT_DIR/Dockerfile.ffmpega" \
    --build-arg "FFMPEGA_SOURCE_COMMIT=$SOURCE_COMMIT" \
    --tag "$image" \
    "$ROOT_DIR"
}

if [[ "$command_name" == "build" ]]; then
  build_image
  exit 0
fi

if [[ "$command_name" == "status" || "$command_name" == "readiness" ]]; then
  state="$(remote "docker inspect --format='{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.Config.Image}}' '$SERVICE_NAME' 2>/dev/null || true")"
  if [[ "$command_name" == "status" ]]; then
    [[ -n "$state" ]] && printf '%s\n' "$state" || printf 'absent\n'
    exit 0
  fi
  [[ "$state" == running\ healthy\ * ]] || fail "FFMPEGA service is not healthy"
  remote "docker exec '$SERVICE_NAME' python -c \"import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8188/health', timeout=3)); assert data['source_commit'] == '$SOURCE_COMMIT' and data['worker_alive']\""
  exit 0
fi

if [[ "$command_name" == "stop" ]]; then
  remote "docker stop -t 30 '$SERVICE_NAME' >/dev/null 2>&1 || true"
  exit 0
fi

if [[ "$command_name" == "rollback" ]]; then
  remote "set -eu; current='$SERVICE_NAME'; previous='$SERVICE_NAME-rollback'; failed='$SERVICE_NAME-failed'; docker inspect \"\$previous\" >/dev/null 2>&1; docker rm -f \"\$failed\" >/dev/null 2>&1 || true; docker stop -t 30 \"\$current\" >/dev/null 2>&1 || true; docker rename \"\$current\" \"\$failed\"; docker rename \"\$previous\" \"\$current\"; docker start \"\$current\" >/dev/null"
  wait_for_health "$SERVICE_NAME" || fail "rolled back FFMPEGA service did not become healthy"
  exit 0
fi

build_image
printf 'Delivering the pinned deterministic FFMPEGA image...\n'
docker save "$image" | gzip -1 | remote "gzip -d | docker load >/dev/null"

candidate="$SERVICE_NAME-candidate"
remote "set -eu; docker network inspect '$NETWORK_NAME' >/dev/null; test -d '$outputs_dir'; docker rm -f '$candidate' >/dev/null 2>&1 || true; docker run -d --name '$candidate' --network '$NETWORK_NAME' --restart unless-stopped --cpus 2 --memory 1536m --pids-limit 256 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=256m --security-opt no-new-privileges --cap-drop ALL --user 65532:65532 --env 'FFMPEGA_SHARED_ROOT=$outputs_dir' --volume '$outputs_dir:$outputs_dir:rw' '$image' >/dev/null"
if ! wait_for_health "$candidate"; then
  remote "docker logs --tail 20 '$candidate' 2>&1 | sed -E 's#/[^ ]+/mvp_(jobs|sessions)/[^ ]+#<redacted-path>#g'" >&2 || true
  fail "candidate FFMPEGA service did not become healthy"
fi

remote "set -eu; current='$SERVICE_NAME'; rollback='$SERVICE_NAME-rollback'; candidate='$candidate'; docker rm -f \"\$rollback\" >/dev/null 2>&1 || true; if docker inspect \"\$current\" >/dev/null 2>&1; then docker stop -t 30 \"\$current\" >/dev/null; docker rename \"\$current\" \"\$rollback\"; fi; docker stop -t 30 \"\$candidate\" >/dev/null; docker network disconnect '$NETWORK_NAME' \"\$candidate\"; docker network connect --alias '$SERVICE_NAME' '$NETWORK_NAME' \"\$candidate\"; docker rename \"\$candidate\" \"\$current\"; docker start \"\$current\" >/dev/null"
wait_for_health "$SERVICE_NAME" || fail "FFMPEGA service did not become healthy after cutover"
printf 'FFMPEGA service is healthy at source commit %s.\n' "$SOURCE_COMMIT"
