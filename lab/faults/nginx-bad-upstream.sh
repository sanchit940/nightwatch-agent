#!/usr/bin/env bash
# Fault: the nginx site config proxies to the wrong api port (8001). nginx returns 502.
set -euo pipefail
FAULT_ID=nginx-bad-upstream
source "$(dirname "$0")/lib.sh"
CONF="$COMPOSE_DIR/nginx/conf.d/default.conf"

reload_nginx() {
  docker exec nw-web nginx -t >/dev/null 2>&1 || die "nginx config test failed"
  docker exec nw-web nginx -s reload >/dev/null 2>&1
}

do_inject() {
  require_container nw-web
  perl -pi -e 's#http://api:8000;#http://api:8001;#' "$CONF"
  reload_nginx
  log "nginx upstream changed and reloaded"
}

do_reset() {
  require_container nw-web
  perl -pi -e 's#http://api:8001;#http://api:8000;#' "$CONF"
  reload_nginx
  wait_http_ok http://localhost:8080/ 30
  log "reset done: nginx proxies to api:8000"
}

do_status() {
  if grep -q 'http://api:8001;' "$CONF"; then echo "injected (upstream api:8001)"; else echo "not injected"; fi
}

fault_main "$@"
