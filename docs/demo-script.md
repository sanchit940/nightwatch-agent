# Demo video script (target 4 minutes)

Length and format are not announced. Keep it short. Use a large terminal font. Show Slack and the terminal side by side.

## Before you record

1. Generate a fresh Slack app-level token (Basic Information → App-Level Tokens) and put it in `.env` (rotating tokens expire).
2. `bash lab/reset_all.sh`, then wait until `nightwatch check` says "healthy" twice, about 2 minutes apart (old alerts must clear).
3. Open: terminal 1 (watch mode), terminal 2 (fault commands), Slack `#nightwatch`, Grafana http://localhost:3000, the neatlogs dashboard, the cfo.ai model.
4. Budget: one demo incident costs about $0.10 with `claude-haiku`.

## 0:00 — The problem (20 s)

Say: "At 3 a.m. an alert fires. Somebody wakes up, collects data, finds the cause, fixes it, checks it.
NightWatch does that work. It never changes anything without an approval for the exact change."

Show: the README diagram.

## 0:20 — It watches by itself (40 s)

Terminal 1: `nightwatch watch --approval slack --interval 15` → "healthy". "No model calls while all is healthy. It costs nothing."
Terminal 2: `bash lab/faults/fault.sh redis-down inject`. "This is a real fault. Redis stops."
Show: two rounds later, ONE incident opens for three symptoms.

## 1:00 — Investigation (45 s)

Terminal 1: tool calls (Docker, logs, Loki, Grafana alerts), the hypotheses table.
Say: "It follows Brendan Gregg's method from Systems Performance: one hypothesis, one test. It has no shell.
48 read-only tools. A policy service checks every call."

## 1:45 — Slack approval (60 s)

Show the Slack message: problem, cause, plan, risk, rollback, check. "Nine short lines, in Simplified Technical English."
Open the Details reply: the causal chain. Say: "Every link needs proof from a tool result. Two events at the same
time are not proof. We added that rule after the agent blamed the wrong service in a live test."
Click **Suggest another fix** and type: "Read the Redis logs first. If it stopped cleanly, start it."
Show: plan 1 cancelled (not executed), new plan in the thread. Click **Approve**.
Show: "Done. Check: 3 of 3 passed." and "Resolved".

## 2:45 — Safety (20 s)

Say: "The approval is signed and bound to the action, the target state and an expiry. A stale, changed, forged or replayed
approval is refused. A restart cannot be proposed for a config fault. 41 tests prove these rules."
Optional: show the recovery run in `docs/eval-results.md`: an approval went stale and NightWatch refused it.

## 3:05 — neatlogs (25 s)

Show the neatlogs trace of the incident: model calls, tool spans, policy (GUARDRAIL) spans.
Say: "neatlogs showed us real bugs: zero cache reads (cost −53% after the fix), and a model stream that never ended."
Show the before/after table: correct fix decisions 8/12 → 11/12, ineffective changes 4 → 1, same cost, 0 unauthorized changes.

## 3:30 — Entire (10 s)

Show `entire checkpoint list` and one commit with its `Entire-Checkpoint` trailer on GitHub.
Say: "Every commit links to the agent session that wrote it. We publish summaries, not transcripts."

## 3:40 — cfo.ai (15 s)

Show the cfo.ai model: pricing, cost per incident (measured: about $0.06 with Haiku), runway, break-even.

## 3:55 — Close (5 s)

Say: "NightWatch: an on-call agent you can trust to run without you watching." Show the repo URL.

## Optional extras (only if time allows)

- Grafana "Shop overview" dashboard during the fault.
- AWS masking: `docs/connectors.md`. Do NOT show real company AWS output on screen.
