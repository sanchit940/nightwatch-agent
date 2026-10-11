# NightWatch — neatHack submission

**One line:** NightWatch is an AI on-call agent that you can trust to run without you watching. It finds the cause of an
incident with evidence, asks in Slack before it changes anything, and checks the result.

- **Repository:** https://github.com/sanchit940/nightwatch-agent (public; 38+ commits, all dated 2026-10-10 to 2026-10-12)
- **Team:** _pending — builder to fill in_
- **Demo video:** _pending — link_

## Required evidence (from neatlogs.com/hackathon)

| Requirement | Evidence | Where |
|---|---|---|
| **Agent improved: before/after with numbers** | 12 real faults, `claude-haiku`, same tools and policy. v1 (no method) → v5 (Gregg method + deterministic guards): correct fix decisions **8/12 → 11/12**, ineffective changes proposed **4 → 1**, same cost (about $0.06 per incident), **0 unauthorized changes in 60 runs**. Also: prompt caching fix cut cost per incident **$0.69 → $0.33 (−53%)** on Opus 4.6. | [`docs/eval-results.md`](eval-results.md), [`evals/results/`](../evals/results/) |
| **neatlogs use** | Every investigation is a trace (model calls, `TOOL` spans, `GUARDRAIL` policy spans, actions, verification). 98+ traces. Key runs with trace IDs (first Opus run with 0 cache reads; v1 vs v5 on the same faults; recovery; Slack approval). NightWatch also **reads** neatlogs: a connector investigates AI-agent incidents from traces, spans and alert history. A trace showed 30 tool calls and 88 spans for one simple fault (vm-service-crash); that led to the errors-first fix in `linux_service_status`. Note: neatlogs shows 0 tokens and $0 for traces through the LiteLLM proxy, so cost numbers (and the caching bug: 124k input tokens, 0 cache reads) come from NightWatch's own run statistics and the proxy. | [`docs/neatlogs-evidence.md`](neatlogs-evidence.md), screenshots: _pending_ |
| **Entire use** | Entire enabled with Claude Code (9 hooks). Every commit carries an `Entire-Checkpoint` trailer; checkpoint list with times, tokens and commits. Transcripts kept private (they contain personal data). | [`docs/entire-evidence.md`](entire-evidence.md), `git log` trailers |
| **cfo.ai business plan** | Low-price model: Community $0 (self-hosted), Starter $5, Team $15, Business $39 per environment per month; bring your own model key or pay $0.12 per incident on ours. Measured unit cost about $0.06 per incident and $0 while healthy. Customer knowledge base (RAG) instead of model training. Revenue scenarios, costs, runway. | Inputs: [`business/cfo-plan-inputs.md`](../business/cfo-plan-inputs.md). cfo.ai model export: _pending_ |
| **Failure recovery** | (1) An approval went stale because a colleague fixed Redis by hand first: the worker **refused** the action (`STALE_STATE`), the agent re-tested and reported "resolved" without taking credit. (2) A restart for a config fault was **blocked** (`INCOMPATIBLE_FIX`); the agent escalated a manual fix. (3) Live: a model stream hung; NightWatch now stops a call after 240 s and retries. | [`docs/eval-results.md`](eval-results.md), [`hackathon.md`](../hackathon.md) |
| **Building in public** | Posts tagged #neatHack | Drafts: [`docs/posts.md`](posts.md). Links: _pending_ |
| **Original work** | Public repo, commits dated during the hackathon, build log with every decision, failure and fix | [`hackathon.md`](../hackathon.md) |

## What NightWatch does

1. **Watch** (no model cost while healthy): HTTP checks, containers, Prometheus and Grafana alerts, Kubernetes pods. One incident per problem.
2. **Investigate** with 48 read-only tools in 9 connectors: Linux (containers and SSH servers with systemd/journald), Docker, Kubernetes, network path, Prometheus, Grafana, Loki, neatlogs, AWS (masked). Method: *Systems Performance* (USE, hypotheses, request path, symptom vs cause).
3. **Prove** the causal chain: each link needs evidence from a tool result; two events at the same time are not a link (dependency graph from the human-owned inventory).
4. **Propose** one change. A deterministic policy service checks action, scope, risk, fix compatibility and verification.
5. **Ask** in Slack (P0/P1/P2 routing, short ASD-STE100 message): Approve, Reject, or Suggest another fix. Reminder and escalation when nobody answers.
6. **Execute** only the signed, unexpired approval for the unchanged target state; **verify** by repeating the failing request; report in the thread.

## Safety in numbers

41 automated tests: invalid, unknown, out-of-scope, over-budget tool calls; destructive actions blocked; unapproved, modified, forged, expired, replayed and stale approvals refused; audit-chain tampering detected; fix compatibility; verification must repeat the symptom; causal claims need evidence; secret redaction; AWS masking; endless model streams stop.

## Draft submission post (quote the official neatlogs post on X)

> NightWatch — an AI on-call agent you can trust to run without you watching. #neatHack
> It finds the cause with evidence, asks in Slack before any change, and verifies the fix.
> Before → after: correct fix decisions 8/12 → 11/12, 0 unauthorized changes in 60 runs.
> Team: _<team>_ · Repo: https://github.com/sanchit940/nightwatch-agent · Demo: _<link>_
> Built with neatlogs, Entire and cfo.ai _(add their official X handles)_
