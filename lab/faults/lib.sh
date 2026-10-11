# Shared helpers for the NightWatch fault scripts. Source this file; do not run it.
# shellcheck shell=bash

FAULTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$FAULTS_DIR/.." && pwd)"
COMPOSE_DIR="$LAB_DIR/compose"
K8S_DIR="$LAB_DIR/k8s"
OVERRIDE_FILE="$COMPOSE_DIR/docker-compose.override.yml"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-nightwatch}"
K8S_NS="shop"

log() { printf '[%s] %s\n' "${FAULT_ID:-lab}" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ---------- Docker Compose helpers ----------

# Run docker compose in the lab directory. Compose loads docker-compose.override.yml
# automatically when it exists, the same way a local deploy does.
compose() { (cd "$COMPOSE_DIR" && docker compose "$@"); }

container_running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" = "true" ]
}

container_health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null || echo missing; }

require_container() { docker inspect "$1" >/dev/null 2>&1 || die "container $1 not found; start the lab with lab/compose/up.sh"; }

# wait_healthy <container> [timeout_seconds]
wait_healthy() {
  local c="$1" t="${2:-90}" i=0
  while [ "$i" -lt "$t" ]; do
    [ "$(container_health "$c")" = "healthy" ] && return 0
    sleep 1; i=$((i + 1))
  done
  log "WARN: $c not healthy after ${t}s (state: $(container_health "$c"))"
  return 1
}

# http_code <url>: print the HTTP status code (000 on connection error).
http_code() { curl -s -o /dev/null -m 5 -w '%{http_code}' "$1" || true; }

# wait_http_ok <url> [timeout_seconds]
wait_http_ok() {
  local url="$1" t="${2:-60}" i=0
  while [ "$i" -lt "$t" ]; do
    [ "$(http_code "$url")" = "200" ] && return 0
    sleep 1; i=$((i + 1))
  done
  log "WARN: $url did not return 200 after ${t}s"
  return 1
}

# ---------- compose override (api environment) ----------
# The override file holds api environment settings that differ from the base file.
# Format (fixed, written only by these helpers):
#   services:
#     api:
#       environment:
#         KEY: "VALUE"

_override_keys() {
  [ -f "$OVERRIDE_FILE" ] || return 0
  sed -n 's/^      \([A-Z0-9_]*\): .*/\1/p' "$OVERRIDE_FILE"
}
_override_get() { sed -n "s/^      $1: \"\(.*\)\"$/\1/p" "$OVERRIDE_FILE" 2>/dev/null; }

_override_write() { # args: KEY=VALUE ...
  if [ "$#" -eq 0 ]; then rm -f "$OVERRIDE_FILE"; return 0; fi
  {
    echo "services:"
    echo "  api:"
    echo "    environment:"
    local kv
    for kv in "$@"; do echo "      ${kv%%=*}: \"${kv#*=}\""; done
  } > "$OVERRIDE_FILE"
}

override_set() { # KEY VALUE
  local pairs=() k
  for k in $(_override_keys); do [ "$k" = "$1" ] || pairs+=("$k=$(_override_get "$k")"); done
  pairs+=("$1=$2")
  _override_write "${pairs[@]}"
}

override_unset() { # KEY
  local pairs=() k
  for k in $(_override_keys); do [ "$k" = "$1" ] || pairs+=("$k=$(_override_get "$k")"); done
  _override_write ${pairs[@]+"${pairs[@]}"}
}

# api_env <KEY>: print the value of an env var in the running nw-api container.
api_env() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' nw-api 2>/dev/null | sed -n "s/^$1=//p"
}

# Recreate nw-api from the compose files (base + override) without touching other services.
redeploy_api() { compose up -d --no-deps api >/dev/null 2>&1 || compose up -d --no-deps api; }

# ---------- nw-vm helpers (systemd host) ----------
# The fault scripts act as root through docker exec. The agent uses SSH as user nightwatch.

VM=nw-vm

require_vm() {
  require_container "$VM"
  container_running "$VM" || die "$VM is not running; start it with lab/vm/up.sh"
}

vm() { docker exec "$VM" "$@"; }

# vm_unit_state <unit>: print the ActiveState of a unit (active, failed, activating, ...).
vm_unit_state() { docker exec "$VM" systemctl show -p ActiveState --value "$1" 2>/dev/null || echo unknown; }

# wait_vm_unit_state <unit> <state> [timeout_seconds]
wait_vm_unit_state() {
  local u="$1" want="$2" t="${3:-60}" i=0
  while [ "$i" -lt "$t" ]; do
    [ "$(vm_unit_state "$u")" = "$want" ] && return 0
    sleep 1; i=$((i + 1))
  done
  log "WARN: $u not $want after ${t}s (state: $(vm_unit_state "$u"))"
  return 1
}

# ---------- Kubernetes helpers ----------

kc() { kubectl --context "$KUBE_CONTEXT" -n "$K8S_NS" "$@"; }

require_cluster() {
  kubectl --context "$KUBE_CONTEXT" get ns "$K8S_NS" >/dev/null 2>&1 \
    || die "kind cluster 'nightwatch' or namespace '$K8S_NS' not found; run lab/k8s/up.sh"
}

# Re-apply the known-good manifests and wait for the workloads.
k8s_apply_baseline() {
  kubectl --context "$KUBE_CONTEXT" apply -f "$K8S_DIR/manifests/" >/dev/null
  kc rollout status deployment/redis --timeout=180s >/dev/null
  kc rollout status deployment/api --timeout=180s >/dev/null
  wait_http_ok http://localhost:8081/ 60
}

# k8s_record_change <deployment> <cause>: set the change-cause shown by rollout history.
k8s_record_change() {
  kc annotate "deployment/$1" "kubernetes.io/change-cause=$2" --overwrite >/dev/null
}

# ---------- dispatcher ----------

# fault_main "$@": call inject|reset|status functions defined by the fault script.
fault_main() {
  local action="${1:-}"
  case "$action" in
    inject) do_inject ;;
    reset) do_reset ;;
    status) do_status ;;
    *) echo "usage: $(basename "$0") inject|reset|status" >&2; exit 2 ;;
  esac
}
