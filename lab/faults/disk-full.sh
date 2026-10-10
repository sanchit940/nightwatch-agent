#!/usr/bin/env bash
# Fault: a large export file fills the 64m /data tmpfs of nw-api. Access-log writes
# fail with ENOSPC (No space left on device) and GET / returns 500.
set -euo pipefail
FAULT_ID=disk-full
source "$(dirname "$0")/lib.sh"
FILL=/data/orders-export.ndjson

do_inject() {
  require_container nw-api
  container_running nw-api || die "nw-api is not running"
  docker exec nw-api sh -c "dd if=/dev/zero of=$FILL bs=1M >/dev/null 2>&1 || true"
  log "/data usage: $(docker exec nw-api df -h /data | awk 'NR==2{print $5}')"
}

do_reset() {
  require_container nw-api
  if container_running nw-api; then docker exec nw-api rm -f "$FILL"; fi
  wait_http_ok http://localhost:8080/ 30
  log "reset done: /data usage $(docker exec nw-api df -h /data | awk 'NR==2{print $5}')"
}

do_status() {
  local use; use="$(docker exec nw-api df /data 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')"
  if [ "${use:-0}" -ge 95 ]; then echo "injected (/data ${use}% used)"; else echo "not injected (/data ${use:-?}% used)"; fi
}

fault_main "$@"
