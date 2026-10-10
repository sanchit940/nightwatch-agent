#!/usr/bin/env bash
# Fault: deployment/redis scales to 0 replicas. The api health check fails, api pods
# become unready and the liveness probe restarts them.
set -euo pipefail
FAULT_ID=k8s-redis-scaled-zero
source "$(dirname "$0")/lib.sh"

do_inject() {
  require_cluster
  kc scale deployment/redis --replicas=0 >/dev/null
  log "deployment/redis scaled to 0"
}

do_reset() {
  require_cluster
  k8s_apply_baseline
  log "reset done: baseline manifests applied, redis and api rolled out"
}

do_status() {
  local r; r="$(kc get deployment redis -o jsonpath='{.spec.replicas}')"
  if [ "$r" = "0" ]; then echo "injected (redis replicas 0)"; else echo "not injected (redis replicas $r)"; fi
}

fault_main "$@"
