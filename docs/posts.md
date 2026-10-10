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

## Post 5 — prompts move errors, rules remove them

v3 made the agent too careful (missed fixes). v4 made it too eager (restarts "for after the config is fixed").
So we stopped tuning the prompt and added a human-owned rule: a restart cannot fix a config fault. The policy gate rejects it.
v1 → v5: correct fix decisions 8/12 → 11/12, ineffective changes 4 → 1, same $0.06 per incident, 0 unauthorized changes in 60 runs. #neatHack

## Post 6 — recovery

A colleague fixed Redis by hand while NightWatch's approval was waiting. The approval was bound to the old state,
so the worker refused it (STALE_STATE). The agent re-tested, saw HTTP 200, and reported "resolved" without taking credit. #neatHack
