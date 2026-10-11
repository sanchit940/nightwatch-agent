#!/usr/bin/env bash
# Get everything ready to record the demo. No model calls. Safe to run at any time.
#   bash lab/demo_ready.sh
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate

step() { printf '\n== %s\n' "$1"; }

step "Docker"
docker info >/dev/null 2>&1 || { echo "Docker Desktop is not running. Start it, then run this script again."; exit 1; }
echo "OK"

step "Reset the lab (about 1-2 minutes)"
bash lab/reset_all.sh 2>&1 | grep -E "OK|NOT" || true

step "Wait until watch mode says healthy 2 times in a row (old alerts must clear)"
ok=0
for _ in $(seq 1 20); do
  if nightwatch check >/dev/null 2>&1; then ok=$((ok + 1)); echo "healthy ($ok/2)"; else ok=0; echo "not healthy yet"; fi
  [ "$ok" -ge 2 ] && break
  sleep 30
done
[ "$ok" -ge 2 ] || { echo "The lab is not healthy. Run: nightwatch check"; exit 1; }

step "Slack tokens"
python - <<'PY'
import os
from nightwatch.config import Settings
s = Settings.from_env()
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
try:
    r = WebClient(token=s.slack_bot_token).auth_test()
    print(f"bot token OK (app user {r['user']} in {r['team']})")
except SlackApiError as e:
    print(f"bot token NOT OK: {e.response['error']}  -> Slack app > OAuth & Permissions > copy the Bot User OAuth Token to SLACK_BOT_TOKEN")
try:
    WebClient().apps_connections_open(app_token=s.slack_app_token)
    print("app token OK (buttons will work)")
except SlackApiError as e:
    print(f"app token NOT OK: {e.response['error']}  -> Slack app > Basic Information > App-Level Tokens > new token (connections:write) to SLACK_APP_TOKEN")
PY

step "Ready to record"
cat <<'TXT'
Terminal 1:  nightwatch watch --approval slack --interval 15
Terminal 2:  bash lab/faults/fault.sh redis-down inject      (when the recording runs)
After:       bash lab/reset_all.sh
TXT
