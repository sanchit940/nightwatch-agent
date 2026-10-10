#!/usr/bin/env bash
# Restore a healthy baseline for both labs. Safe to run at any time.
set -euo pipefail
source "$(dirname "$0")/faults/lib.sh"
FAULT_ID=reset-all

# ---------- Docker Compose lab ----------
log "compose: restoring baseline"
rm -f "$OVERRIDE_FILE"
perl -pi -e 's#http://api:\d+;#http://api:8000;#' "$COMPOSE_DIR/nginx/conf.d/default.conf"
if docker inspect nw-redis >/dev/null 2>&1; then
  "$FAULTS_DIR/dns-break.sh" status | grep -q '^not injected' || "$FAULTS_DIR/dns-break.sh" reset || true
fi
compose up -d --remove-orphans >/dev/null 2>&1 || compose up -d --remove-orphans
if container_running nw-api; then
  "$FAULTS_DIR/api-cpu-hog.sh" reset >/dev/null 2>&1 || true
  docker exec nw-api rm -f /data/orders-export.ndjson
fi
docker exec nw-web nginx -s reload >/dev/null 2>&1 || true
wait_healthy nw-api 90 || true
if wait_http_ok http://localhost:8080/ 60; then log "compose: OK (http://localhost:8080 returns 200)"; else log "compose: NOT healthy"; fi

# ---------- kind lab ----------
if kubectl --context "$KUBE_CONTEXT" get ns "$K8S_NS" >/dev/null 2>&1; then
  log "k8s: applying baseline manifests"
  k8s_apply_baseline
  kc get deploy,pods
  log "k8s: OK"
else
  log "k8s: cluster 'nightwatch' not found; skipped (start it with lab/k8s/up.sh)"
fi
