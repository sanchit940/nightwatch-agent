# Building in public — draft posts (#neatHack)

Drafts only. The builder posts them. Each post links the public repo.

## Post 1 — start

Building NightWatch for #neatHack: an AI on-call agent you can trust to run while you sleep.
It investigates incidents with evidence (USE method from Brendan Gregg's Systems Performance), and it changes infrastructure only after an approval bound to the exact change.
Traces in @neatlogs, provenance with @entireio.
https://github.com/sanchit940/nightwatch-agent

## Post 2 — safety model

How do you let an agent touch prod at 3am? NightWatch:
- no shell, only typed tools
- a deterministic policy gate checks every call
- approvals are HMAC-signed and bound to action + params + target state + expiry
- stale, modified, replayed or forged approvals are rejected (41 tests in total)
#neatHack

## Post 3 — first live run, and a cost bug

First live run: Redis down → root cause found, fix approved, verified (HTTP 200) in 101 s.
Our run stats showed 124k input tokens and 0 cache reads: the proxy dropped the cache flag. Explicit cache breakpoints → $0.69 → $0.33 per incident (−53%). #neatHack

## Post 4 — before/after numbers

12 real faults (Docker + kind), claude-haiku, ~$0.06 per incident:
v1 prompt: root cause 12/12, correct fix decision 8/12
v2 prompt (Gregg's method): 12/12 and 11/12
The method did not change cost. It stopped the agent from proposing changes that cannot fix the cause. #neatHack

## Post 5 — prompts move errors, rules remove them

v3 made the agent too careful (missed fixes). v4 made it too eager (restarts "for after the config is fixed").
So we stopped tuning the prompt and added a human-owned rule: a restart cannot fix a config fault. The policy gate rejects it.
v1 → v5: correct fix decisions 8/12 → 11/12, ineffective changes 4 → 1, same $0.06 per incident, 0 unauthorized changes in 60 runs. #neatHack

## Post 6 — recovery

A colleague fixed Redis by hand while NightWatch's approval was waiting. The approval was bound to the old state,
so the worker refused it (STALE_STATE). The agent re-tested, saw HTTP 200, and reported "resolved" without taking credit. #neatHack

## Post 7 — Slack approvals

NightWatch now asks in Slack: P0/P1/P2, nine short lines (problem, cause, plan, risk, rollback, check).
Approve, Reject, or "Suggest another fix": your instruction cancels the plan, the agent tests it, and a new plan needs approval again. #neatHack

## Post 8 — the agent blamed the wrong service, so we wrote a rule

In a live test our agent said a job restart on another server "caused" Redis to stop. Both happened at 10:20:44. Nothing else linked them.
Now every causal link needs proof from a tool result, checked against a human-owned dependency map. Time correlation is not causation. #neatHack

## Post 9 — a real AWS account, and what neatlogs showed

We pointed NightWatch at a real (read-only, masked) AWS account. Run 1: the trace showed 82% of the time in 20 model calls,
15 guessed CloudTrail lookups, and tools that cut their output without saying so. The agent missed the biggest outage.
Four runs later: 6 of 8 problems found, 105 s instead of 146 s, $0.05 instead of $0.20. #neatHack
