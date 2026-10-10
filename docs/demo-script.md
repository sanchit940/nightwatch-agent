# Demo video script (target 3–4 minutes)

Length and format are not announced. Keep it short. Record the terminal at a large font size.
Before recording: `bash lab/reset_all.sh`, `nightwatch check` → healthy. Use two terminals side by side.

## 0:00 — Problem (20 s)

Say: "At 3 a.m. an alert fires. Somebody wakes up, collects data, finds the cause, restarts something, checks it.
NightWatch does that work, but it never changes infrastructure without an approval that is bound to the exact change."

Show: the README diagram.

## 0:20 — Watch mode, break something real (40 s)

Terminal 1: `nightwatch watch --interval 10`  → "healthy".
Terminal 2: `bash lab/faults/fault.sh redis-down inject`.
Say: "This is a real fault. Redis stops. Watch mode uses cheap deterministic checks: no LLM cost while all is healthy."
Show: after 2 rounds, ONE incident opens with 3 grouped symptoms.

## 1:00 — Investigation (60 s)

Show terminal 1: tool calls, hypotheses table, "symptoms not causes" in the report.
Say: "It follows Brendan Gregg's method from *Systems Performance*: problem statement, hypotheses, one test at a time,
USE checks, the request path. Every claim points to a tool result."
Show: the proposal panel — action, reason, rollback, verification, "PENDING_APPROVAL apr-…".

## 2:00 — Approval and verification (30 s)

Terminal 2: `nightwatch approvals`, then `nightwatch approve apr-…`.
Show: execution panel → verification "resolved 3/3", site returns 200.
Say: "The approval is HMAC-signed and bound to the action, the parameters, the target's state and an expiry.
A stale, modified, replayed or forged approval is rejected. 18 tests prove it."
Optional: `nightwatch audit --verify` → "audit chain intact".

## 2:30 — neatlogs (30 s)

Show the neatlogs dashboard: the investigation trace with model calls, each tool span, each policy (GUARDRAIL) span.
Say: "neatlogs showed us real problems. In the first live run the trace showed 124 thousand input tokens and zero
cache reads. We fixed caching and the cost per incident fell by 53 percent."
Show the before/after table from `docs/eval-results.md` (v1 → v4).

## 3:00 — Entire (20 s)

Show `entire checkpoint list` and one commit with its `Entire-Checkpoint` trailer on GitHub.
Say: "Every commit links to the agent session that produced it, so we can see why a change was made.
We publish checkpoint summaries, not transcripts, because transcripts contain personal data."

## 3:20 — cfo.ai (20 s)

Show the cfo.ai model: pricing, unit costs (measured $0.05–$0.33 per incident), runway and break-even.
Say: "Watch mode costs nothing while healthy, and an incident costs cents, so gross margin stays high."

## 3:40 — Recovery and close (20 s)

Show the recovery run: an action fails or goes stale, and the agent's next step.
Say: "NightWatch: an on-call agent you can trust to run without you watching." Show the repo URL.
