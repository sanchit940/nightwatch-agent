#!/bin/sh
# Send about 5 requests per second to the web front end, forever.
TARGET="${TARGET:-http://web/}"
WORKERS="${WORKERS:-1}"
echo "loadgen: target=$TARGET workers=$WORKERS"
worker() {
  while true; do
    wget -q -O /dev/null -T 3 "$TARGET" 2>/dev/null || true
    sleep 0.2
  done
}
i=0
while [ "$i" -lt "$WORKERS" ]; do
  worker &
  i=$((i + 1))
done
wait
