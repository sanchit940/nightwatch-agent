#!/usr/bin/env bash
# Fault: the Redis container stops. The api returns 503 and the web front end passes it on.
set -euo pipefail
FAULT_ID=redis-down
source "$(dirname "$0")/lib.sh"

do_inject() {
  require_container nw-redis
  if container_running nw-redis; then
    docker stop nw-redis >/dev/null
    log "nw-redis stopped"
  else
    log "nw-redis already stopped"
  fi
}

do_reset() {
  require_container nw-redis
  container_running nw-redis || docker start nw-redis >/dev/null
  wait_healthy nw-api 60 || true
  wait_http_ok http://localhost:8080/ 60
  log "reset done: nw-redis running, web returns 200"
}

do_status() {
  if container_running nw-redis; then echo "not injected"; else echo "injected (nw-redis is not running)"; fi
}

fault_main "$@"
