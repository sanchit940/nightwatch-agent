#!/usr/bin/env bash
# Fault: the readiness probe of deployment/api checks the wrong port (8001).
# Pods run but never become Ready, so Service api has no endpoints.
set -euo pipefail
FAULT_ID=k8s-bad-probe
source "$(dirname "$0")/lib.sh"

do_inject() {
  require_cluster
  kc patch deployment api --type=json \
    -p '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/port","value":8001}]' >/dev/null
  k8s_record_change api "release 1.4.1-hotfix (pipeline 4813)"
  log "deployment/api readiness probe port set to 8001"
}

do_reset() {
  require_cluster
  k8s_apply_baseline
  log "reset done: baseline manifests applied, api rolled out"
}

do_status() {
  local p; p="$(kc get deployment api -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}')"
  if [ "$p" != "8000" ]; then echo "injected (readiness port $p)"; else echo "not injected"; fi
}

fault_main "$@"
