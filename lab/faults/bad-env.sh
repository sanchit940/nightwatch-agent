#!/usr/bin/env bash
# Fault: a config deploy sets REDIS_HOST=redis-primary on the api (a host that does not
# exist). The api is recreated with the bad value and cannot reach Redis.
set -euo pipefail
FAULT_ID=bad-env
source "$(dirname "$0")/lib.sh"

do_inject() {
  override_set REDIS_HOST redis-primary
  redeploy_api
  log "nw-api recreated with REDIS_HOST=redis-primary"
}

do_reset() {
  override_unset REDIS_HOST
  redeploy_api
  wait_healthy nw-api 90
  wait_http_ok http://localhost:8080/ 60
  log "reset done: nw-api recreated with REDIS_HOST=redis"
}

do_status() {
  local v; v="$(api_env REDIS_HOST)"
  if [ "$v" != "redis" ]; then echo "injected (REDIS_HOST=$v)"; else echo "not injected"; fi
}

fault_main "$@"
