#!/usr/bin/env bash
# Fault: nw-redis is disconnected from the nw-back network. The name "redis" no longer
# resolves for nw-api, so every Redis call fails.
set -euo pipefail
FAULT_ID=dns-break
source "$(dirname "$0")/lib.sh"

on_back() { docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' nw-redis 2>/dev/null | grep -qw nw-back; }
# True when nw-redis is on nw-back AND answers to the service name "redis".
resolvable() { docker inspect -f '{{with index .NetworkSettings.Networks "nw-back"}}{{range .Aliases}}{{.}} {{end}}{{end}}' nw-redis 2>/dev/null | grep -qw redis; }

do_inject() {
  require_container nw-redis
  if on_back; then
    docker network disconnect nw-back nw-redis
    log "nw-redis disconnected from nw-back"
  else
    log "nw-redis already disconnected"
  fi
}

do_reset() {
  require_container nw-redis
  # The compose service name "redis" is a network alias; it must be restored too.
  if ! resolvable; then
    on_back && docker network disconnect nw-back nw-redis
    docker network connect --alias redis nw-back nw-redis
  fi
  wait_healthy nw-api 60 || true
  wait_http_ok http://localhost:8080/ 60
  log "reset done: nw-redis connected to nw-back with alias redis"
}

do_status() {
  if resolvable; then echo "not injected"
  elif on_back; then echo "injected (nw-redis on nw-back without alias redis)"
  else echo "injected (nw-redis not on nw-back)"; fi
}

fault_main "$@"
