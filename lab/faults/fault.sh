#!/usr/bin/env bash
# Dispatcher: lab/faults/fault.sh <fault-id> inject|reset|status
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -ne 2 ]; then
  echo "usage: $(basename "$0") <fault-id> inject|reset|status" >&2
  echo "fault ids: run $DIR/list.sh" >&2
  exit 2
fi
id="$1"; action="$2"
case "$id" in
  */*|.*|lib|fault|list) echo "unknown fault: $id" >&2; exit 2 ;;
esac
script="$DIR/$id.sh"
[ -x "$script" ] || { echo "unknown fault: $id (see $DIR/list.sh)" >&2; exit 2; }
exec "$script" "$action"
