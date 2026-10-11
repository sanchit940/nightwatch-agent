#!/usr/bin/env bash
# Fault: a config change on nw-vm sets INTERVAL_SECONDS=5s in /etc/shop-worker/worker.conf.
# shop-worker exits with a config error at start. systemd restarts it 3 times and then
# stops (start-limit-hit). The unit stays in state "failed".
set -euo pipefail
FAULT_ID=vm-service-crash
source "$(dirname "$0")/lib.sh"
CONF=/etc/shop-worker/worker.conf
UNIT=shop-worker.service

conf_interval() { vm sed -n 's/^INTERVAL_SECONDS=//p' "$CONF" 2>/dev/null | tail -1; }

do_inject() {
  require_vm
  if [ "$(conf_interval)" = "5s" ] && [ "$(vm_unit_state "$UNIT")" = failed ]; then
    log "already injected ($UNIT failed)"
    return 0
  fi
  vm sed -i 's/^INTERVAL_SECONDS=.*/INTERVAL_SECONDS=5s/' "$CONF"
  log "INTERVAL_SECONDS set to 5s in $CONF"
  vm systemctl reset-failed "$UNIT" 2>/dev/null || true
  vm systemctl restart "$UNIT" 2>/dev/null || true
  wait_vm_unit_state "$UNIT" failed 60 || true
  log "$UNIT state: $(vm_unit_state "$UNIT")"
}

do_reset() {
  require_vm
  vm sed -i 's/^INTERVAL_SECONDS=.*/INTERVAL_SECONDS=5/' "$CONF"
  vm systemctl reset-failed "$UNIT" 2>/dev/null || true
  vm systemctl restart "$UNIT"
  wait_vm_unit_state "$UNIT" active 30
  log "reset done: $UNIT $(vm_unit_state "$UNIT"), INTERVAL_SECONDS=$(conf_interval)"
}

do_status() {
  local v; v="$(conf_interval)"
  if [ -z "$v" ]; then echo "unknown (cannot read $CONF in $VM)"; return 0; fi
  if [[ "$v" =~ ^[1-9][0-9]*$ ]]; then
    echo "not injected"
  else
    echo "injected (INTERVAL_SECONDS=$v, $UNIT $(vm_unit_state "$UNIT"))"
  fi
}

fault_main "$@"
