#!/usr/bin/env bash
# Fault: a deploy sets CACHE_WARM_MB=400 on the api. The cache warm-up grows past the
# 256m memory limit, the kernel OOM-kills the process, and Docker restarts it in a loop.
set -euo pipefail
FAULT_ID=api-oom
source "$(dirname "$0")/lib.sh"

do_inject() {
  override_set CACHE_WARM_MB 400
  redeploy_api
  log "nw-api recreated with CACHE_WARM_MB=400"
}

do_reset() {
  override_unset CACHE_WARM_MB
  redeploy_api
  wait_healthy nw-api 90
  wait_http_ok http://localhost:8080/ 60
  log "reset done: nw-api recreated without CACHE_WARM_MB"
}

do_status() {
  local v; v="$(api_env CACHE_WARM_MB)"
  if [ -n "$v" ] && [ "$v" != "0" ]; then
    echo "injected (CACHE_WARM_MB=$v, OOMKilled=$(docker inspect -f '{{.State.OOMKilled}}' nw-api), restarts=$(docker inspect -f '{{.RestartCount}}' nw-api))"
  else
    echo "not injected"
  fi
}

fault_main "$@"
