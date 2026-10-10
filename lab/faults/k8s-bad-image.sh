#!/usr/bin/env bash
# Fault: a release points deployment/api at an image tag that does not exist.
# New pods stay in ErrImagePull/ImagePullBackOff and the rollout does not finish.
set -euo pipefail
FAULT_ID=k8s-bad-image
source "$(dirname "$0")/lib.sh"
BAD_IMAGE=nightwatch-api:1.4.2

do_inject() {
  require_cluster
  kc set image deployment/api "api=$BAD_IMAGE" >/dev/null
  k8s_record_change api "release 1.4.2 (pipeline 4812)"
  log "deployment/api image set to $BAD_IMAGE"
}

do_reset() {
  require_cluster
  k8s_apply_baseline
  log "reset done: baseline manifests applied, api rolled out"
}

do_status() {
  local img; img="$(kc get deployment api -o jsonpath='{.spec.template.spec.containers[0].image}')"
  if [ "$img" = "$BAD_IMAGE" ]; then echo "injected (image $img)"; else echo "not injected (image $img)"; fi
}

fault_main "$@"
