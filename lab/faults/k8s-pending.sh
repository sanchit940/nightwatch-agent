#!/usr/bin/env bash
# Fault: deployment/api scales to 3 replicas with a CPU request of 4 cores. No node
# has that much allocatable CPU, so the pods stay Pending (FailedScheduling).
set -euo pipefail
FAULT_ID=k8s-pending
source "$(dirname "$0")/lib.sh"

do_inject() {
  require_cluster
  kc set resources deployment/api -c api --requests=cpu=4 --limits=cpu=4 >/dev/null
  kc scale deployment/api --replicas=3 >/dev/null
  k8s_record_change api "scale api for peak traffic (pipeline 4815)"
  log "deployment/api set to 3 replicas with cpu request 4"
}

do_reset() {
  require_cluster
  k8s_apply_baseline
  log "reset done: baseline manifests applied, api rolled out"
}

do_status() {
  local c; c="$(kc get deployment api -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu}')"
  if [ "$c" = "4" ]; then echo "injected (cpu request $c)"; else echo "not injected (cpu request $c)"; fi
}

fault_main "$@"
