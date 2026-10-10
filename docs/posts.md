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
- stale, modified, replayed or forged approvals are rejected (17 tests)
#neatHack

## Post 3 — first live run, and a bug neatlogs showed

First live run: Redis down → root cause found, fix approved, verified (HTTP 200) in 101 s.
The trace showed 124k input tokens and 0 cache reads: our proxy dropped the cache flag. Explicit cache breakpoints → $0.69 → $0.33 per incident (−53%). #neatHack

## Post 4 — before/after numbers

12 real faults (Docker + kind), claude-haiku, ~$0.06 per incident:
v1 prompt: root cause 12/12, correct fix decision 8/12
v2 prompt (Gregg's method): 12/12 and 11/12
The method did not change cost. It stopped the agent from proposing changes that cannot fix the cause. #neatHack

## Post 5 — v3 result

(fill in after the v3 run)
