#!/usr/bin/env bash
# Fault: the memory limit of deployment/api drops to 20Mi. The container is
# OOMKilled at start and the pods go into CrashLoopBackOff.
set -euo pipefail
FAULT_ID=k8s-oom
source "$(dirname "$0")/lib.sh"

do_inject() {
  require_cluster
  kc set resources deployment/api -c api --requests=memory=20Mi --limits=memory=20Mi >/dev/null
  k8s_record_change api "right-size api resources (pipeline 4814)"
  log "deployment/api memory limit set to 20Mi"
}

do_reset() {
  require_cluster
  k8s_apply_baseline
  log "reset done: baseline manifests applied, api rolled out"
}

do_status() {
  local m; m="$(kc get deployment api -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')"
  if [ "$m" = "20Mi" ]; then echo "injected (memory limit $m)"; else echo "not injected (memory limit $m)"; fi
}

fault_main "$@"
