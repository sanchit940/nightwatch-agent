# NightWatch — neatHack build log

## Project

- **Name:** NightWatch
- **Purpose:** NightWatch is an AI DevOps agent that monitors infrastructure, finds the cause of incidents with evidence, and changes infrastructure only after approval.
- **Problem:** On-call engineers lose time on alerts at night. Most of the work is the same: collect data, compare with a healthy state, find the cause, apply a safe fix, verify. Unsupervised automation is not safe because it can change the wrong thing. NightWatch does the investigation by itself and uses a deterministic policy gate for every change.

## Team

- Pending. The builder must supply the names or handles to publish.

## Stack

| Part | Service / tool | Status |
|---|---|---|
| Agent model | Claude Opus 4.8 (`claude-opus-4-8`) through the Anthropic Python SDK 1.13, adaptive thinking | Planned |
| Agent tracing | neatlogs Python SDK 1.4.26 (`neatlogs.init`, `neatlogs.wrap(Anthropic())`, `@neatlogs.span`) | Installed |
| Build provenance | Entire CLI 0.11.4, Claude Code agent hooks, `git-refs` checkpoints | Enabled |
| Business plan | cfo.ai (pricing, costs, revenue, runway) | Pending — builder signs up at cfo.ai/neathacks |
| Demo lab | Docker 29.2.1 (Docker Compose), kind (local Kubernetes) | Planned |
| Method source | Brendan Gregg, *Systems Performance*, 2nd ed. — USE method, 60-second checklist, per-resource runbooks (`knowledge/`) | In progress |
| Language | Python 3.13 | Installed |

## Build log

### 2026-10-10

**Completed**
- Read the published challenge, rules and submission requirements (neatlogs.com/hackathon, luma.com/s5blr882).
- Selected the idea: an AI DevOps investigation agent. Scope source: the builder's specification (10 components, 4 risk levels, 10-step investigation engine).
- Builder decisions: name NightWatch; lab = Docker Compose + kind; model = Claude Opus 4.8.
- Created the Git repository and a Python 3.13 virtual environment. Reason: neatlogs requires Python >= 3.10 and < 3.14; the default `python3` on this machine is 3.14.
- Installed `neatlogs[anthropic]` 1.4.26 and `anthropic` 1.13.0.
- Installed Entire CLI 0.11.4 and GitHub CLI 2.102.0 with Homebrew.
- Ran `entire enable -y --agent claude-code`. Entire installed 9 Claude Code hooks and created `.entire/settings.json`.

- Distilled *Systems Performance* 2e into 4 runbooks in `knowledge/` (methodology, USE checklist for Linux, resource runbooks, containers/cloud). All text is paraphrased. Each rule cites a book section (for example "SP §2.5.9"). Lines that are general ops practice, not from the book, have the tag `[ops]`.
- Built the agent core (commit `091cd35`):
  - 25 typed tools in 5 connectors: Linux (USE checks inside a container or node, cgroup throttling and OOM counters), Docker, Kubernetes, network (DNS → TCP → TLS → HTTP path trace from a chosen source), Prometheus, runbooks.
  - 8 actions: 6 allowed (restart/start container, connect network, rollout undo/restart, scale), 2 destructive actions registered only to prove that risk level 3 is blocked.
  - Policy service (`nightwatch/policy.py`, `policy.yaml`): schema validation, risk levels, target scope, tool-call and time budgets. Approvals are HMAC-signed and bound to action, parameters, target state fingerprint and expiry.
  - Restricted action worker and verification engine (`nightwatch/worker.py`).
  - Hash-chained SQLite audit log (`nightwatch/audit.py`).
  - Orchestrator (`nightwatch/agent.py`): manual tool loop on `claude-opus-4-8`, adaptive thinking, prompt caching, parallel read-only tool calls, structured report tool.
  - Watch mode (`nightwatch/watch.py`): deterministic checks, no model calls while healthy, incident deduplication.
  - neatlogs instrumentation: `neatlogs.wrap(Anthropic())`, spans for each tool (`TOOL`), each policy decision (`GUARDRAIL`), the investigation (`AGENT`) and verification.
- Tests that ran: `pytest tests` → 15 passed. They cover: invalid and unknown tools rejected, out-of-scope targets rejected, budget limit, risk 3 blocked, unapproved action never executes, replay rejected, modified approval rejected, forged token rejected, expired approval rejected, stale target state rejected, audit-chain tampering detected, standing auto-approval rule bounded by target and rate, secret redaction.
- Smoke test of the read-only connectors against the running Docker lab: path trace, DNS from a container (busybox fallback), cgroup check, Prometheus query and Docker events returned real data.
- Entire: checkpoint `01M4JKFNN0P3KJYXDZMS5NWFJE` is linked to commit `091cd35` (`entire checkpoint list`).

**Fixes**
- `linux_check` reported `ok=True` when the target container was restarting. Fix: treat any Docker daemon error as `TARGET_UNAVAILABLE`.
- `docker_events` printed Unix seconds. Fix: print ISO-8601 UTC, so the agent can correlate events with metrics and logs.

- Fault lab done (commit `087d332`, see `lab/README.md`). Docker Compose lab: nginx → API → Redis, toolbox, Prometheus with 3 alert rules, load generator. kind cluster `nightwatch`, namespace `shop`. 12 real faults (no fake log lines, no hints in names or labels). Each fault was tested: inject → symptom seen → reset → HTTP 200.
- Lab finding: after `dns-break`, a plain `docker network connect` does not repair name resolution. Fix: `connect_network` now adds the compose service name as the DNS alias by default.
- Watch mode test: healthy lab → no findings. `redis-down` injected → 3 findings (web 503, api health 503, nw-redis exited), grouped into one incident.

**Fixes**
- Watch mode read the `kubectl get pods` header line as a pod. Fixed (commit `630214f`).

**Model access (decision)**
- The builder has no direct Anthropic key. The team uses the neatHack LiteLLM proxy: $20 per key for 30 days, shared by the team.
- The proxy accepts the Anthropic Messages format. Tested: streaming, tool use, adaptive thinking, effort. The agent code did not change. Only the base URL changed (`NIGHTWATCH_LLM_BASE_URL`).
- Claude Opus 4.8 (the builder's choice) is not on the proxy. Decision: use `claude-opus-4.6`, the nearest model.
- The proxy reports spend per key (`/key/info`). The CLI and the eval harness now record the **measured** cost of each incident.

**First live runs** (prompt v2, `claude-opus-4.6`, harness as approver)

| Run | Scenario | Root cause found | Fix | Result | Tool calls | Time | Measured cost |
|---|---|---|---|---|---|---|---|
| 1 | `redis-down` | dependency_down, nw-redis ✔ | start_container nw-redis | verified resolved | 10 | 101 s | $0.69 |
| 2 | `dns-break` | network_partition, nw-redis (see note) | connect_network nw-back, alias redis | verified resolved (3/3 checks) | 10 | 99 s | $0.33 |

- Both runs: 0 unauthorized changes, audit chain intact.
- Run 1 report named the 503 errors, the Prometheus alert and the failed health check as **symptoms**, not causes. It listed "who stopped Redis" as a known unknown.
- **Failure found in run 1: prompt caching did not work through the proxy** (124k input tokens, 0 cache reads). The proxy drops the top-level `cache_control` field. Fix: explicit cache breakpoints on the system prompt and on the newest message. Result in run 2: 121k tokens read from cache, 12 uncached input tokens. Cost per incident fell from $0.69 to $0.33 (−53%).
- **Grader issue in run 2:** the agent named the cause `network_partition` (Redis was detached from its network). The scenario expected `dns_failure`. Both are correct descriptions; the DNS failure is a symptom of the detach. Decision: scenarios now list accepted alternative categories (`also_acceptable_categories`) where two labels describe the same cause. This rule is set before the v1/v2 comparison and applies to both.
- Total LLM spend after these tests: $1.36 of $20.

**Model switch: `claude-haiku`** (builder decision, to save budget)
- The proxy accepts adaptive thinking and effort for `claude-haiku`. No code change.
- Run 3 (`redis-down`, Haiku): cost $0.08 (8× cheaper than Opus 4.6). The first report was correct (dependency_down, nw-redis, high confidence). The fix was approved, executed, and the site returned 200.
- **Failures found in run 3:**
  1. In round 2, Haiku sent a report with only a `summary` string. It replaced the correct report. Fix: the orchestrator validates every report against the schema and rejects an incomplete report with the list of missing fields; the model must send it again.
  2. Verification showed 2/4. Haiku wrote a regex in a literal `contains` check, and a check on a startup log line aged out of its 1-minute window while the engine kept polling. Fix: the verification engine reports the best round; checks have a `regex` option; the tool description says that `contains` is literal and that short log windows age out.
- Tests that ran: `pytest tests` → 17 passed (2 new: report validation, best-round verification).

**Public repository**
- Created https://github.com/sanchit940/nightwatch-agent (public), pushed `main`.
- Builder decision: no Claude co-author lines. They were removed from all commit messages before the first push. The `Entire-Checkpoint` lines were kept, so Entire still links each checkpoint to its commit (`entire checkpoint list` shows the new commit IDs).
- History scan before the push: no `.env` file, no API key, no approval secret in any commit.
- **Privacy incident and fix:** Entire's push hook also pushed 10 checkpoint refs (`refs/entire/checkpoints/*`) to GitHub. These contain the session transcripts, which include the builder's personal and work email addresses. No API keys were in them (scanned). Within minutes the 10 refs were deleted from GitHub, and `push_sessions` was set to `false` in `.entire/settings.json`. The checkpoints remain local.
- Entire evidence decision (builder delegated it): publish checkpoint summaries only, in `docs/entire-evidence.md` (IDs, times, token counts, linked commits). No transcripts; the file was scanned for email addresses. No Entire mirror: with transcripts private it would only copy the code.
- cfo.ai: the builder signed up and cancelled the subscription. Inputs and a ready-to-paste prompt for Ari: `business/cfo-plan-inputs.md` (measured unit costs; prices, customers and cash are assumptions for the builder to confirm).

**Evaluation: prompt v1 → v2 → v3** (`claude-haiku`, diagnosis mode, 12 real faults, harness denies all changes)

| Metric | v1 (no method) | v2 (+ Gregg method) | v3 (+ "propose only real fixes" guard) |
|---|---|---|---|
| Root cause correct | 12/12 | 12/12 | 12/12 |
| Correct fix decision | 8/12 | 11/12 | 10/12 |
| Ineffective change proposed | 4 | 1 | 0 |
| Needed change not proposed | 0 | 0 | 2 |
| Avg tool calls | 10.1 | 10.8 | 9.6 |
| Avg measured cost | $0.059 | $0.060 | $0.053 |
| Unauthorized changes | 0 | 0 | 0 |

- v1 proposed restarts or scaling for config faults (nginx upstream, bad env, OOM setting, CPU request). These changes cannot remove the cause.
- v2 (method from *Systems Performance*) removed 3 of these 4. The remaining one (`bad-env`): the agent proposed a restart and wrote that it would not fix the cause, "to highlight" the problem.
- v3 added a deterministic guard: each proposal must declare its expected effect, and the orchestrator rejects an action that the agent itself expects not to work. Result: 0 ineffective proposals, but 2 needed mitigations were missed:
  - `api-cpu-hog`: the agent took 16 injected CPU-burning processes for normal app workers. Cause: the process check showed no start time. Fix: the check now shows AGE and parent PID.
  - `disk-full`: the agent correctly said a restart only mitigates (tmpfs fills again), and the strict rule blocked it. Fix: the guard now accepts `removes_root_cause` or `restores_service_temporarily`, and rejects only `no_effect_on_symptom`. Prompt v4 says that restoring service at night is valuable, and that an action that deletes data needs a recovery plan.
- Grading rule (set before each run and applied to all versions with `evals/compare.py`): where two labels describe the same cause, both count (for example `config_error` for a deploy that set a 4-CPU request, `network_partition` for a detached Redis).
- Limitation: the same 12 scenarios were used to find and fix these problems. The numbers show improvement on known incidents, not on unseen ones. Next step: held-out scenarios.
- Total LLM spend so far: about $4 of $20.

**Evaluation: v4 and v5, and the recovery run**
- v4 (relaxed guard, process AGE): the two missed mitigations came back. But the agent proposed restarts "for after the config is corrected" for nginx-bad-upstream and bad-env, and mislabeled api-cpu-hog as memory exhaustion: it used OOM events from the previous scenario (api-oom).
- Decision: stop adding prompt rules. Prompt rules only moved the errors around. Add a **deterministic, human-owned fix-compatibility table** to `policy.yaml`: restart, start and rollout-restart cannot fix config-class causes. The agent must name the root-cause category with each proposal, and the policy service rejects incompatible pairs (`INCOMPATIBLE_FIX`). Test added (19 tests pass).
- v5: correct fix decisions 11/12, ineffective changes 1, root cause 11/12, $0.059 per incident. `INCOMPATIBLE_FIX` fired 2 times. The one wrong proposal: a Kubernetes rollback for a fault in the Docker lab (environment confusion). Next step: bind each incident to one environment.
- Full table and per-scenario results: `docs/eval-results.md`. Summary v1 → v5: correct fix decisions 8/12 → 11/12, ineffective changes 4 → 1, same cost, 0 unauthorized changes in 60 runs.
- **Recovery run** (`evals/recovery_demo.py --case stale`, audit `runs/recovery-stale-*.db`): the agent proposed `start_container nw-redis`. A simulated colleague started Redis by hand before the approval was used. The worker re-checked the target fingerprint and refused to execute (`STALE_STATE`). The incident went to round 2: the agent re-tested, found HTTP 200, and reported `resolved` without claiming the fix. Cost $0.09. After this run, the round-2 message now states explicitly that NightWatch did not execute a refused action.
- Total LLM spend: $5.95 of $20.

**Planned**
- First live runs when the API keys are in `.env`.
- Evaluation: `evals/run_evals.py` with prompt v1 (baseline) and v2 (with the book method). Compare the numbers and the neatlogs traces.

## Demo

- Not available yet.

## Known limitations and next steps

- Not available yet.

## Submission

**Confirmed requirements** (source: neatlogs.com/hackathon, read 2026-10-10)

- One track: build an agent that completes a substantial task from start to finish.
- The project must use neatlogs, Entire and cfo.ai.
- Evidence to supply:
  - Agent improvement: before-and-after runs with compared numbers (for example, two neatlogs traces).
  - neatlogs: trace links or screenshots.
  - Entire: checkpoints or `entire` graph output from the repository.
  - cfo.ai: a business plan with pricing, costs, revenue and runway.
  - Recovery: a run where an action failed, and the next step of the agent.
  - Building in public: links tagged #neatHack.
  - Code: a public repository with commits dated during the hackathon.
- Judging: agent 30%, use of neatlogs/Entire/cfo.ai 25%, demo video 20%, building in public 15%, usefulness and originality 10%.
- Submit on X: quote the official neatlogs post with team name, project name, public repository and demo.
- Submission window: Monday 2026-10-12, 19:00–23:55 IST.

**Outstanding items**

- [ ] Team names or handles.
- [ ] neatlogs account and `NEATLOGS_API_KEY` (builder).
- [ ] Anthropic API key in `.env` (builder).
- [ ] cfo.ai sign-up at cfo.ai/neathacks; cancel the subscription in Settings → Billing (builder).
- [ ] Entire login and repository mirror (builder).
- [ ] Public GitHub repository (builder approves).
- [ ] Demo video. Length and format: not announced.
- [ ] Building-in-public posts with #neatHack.
- [ ] Rules on pre-hackathon work, team size, conduct: not visible on the page — check with the organizers.

### 2026-10-10 (evening) — Slack approvals

**Builder request:** connect NightWatch to Slack. Route approvals by P0/P1/P2. Post a plan in ASD-STE100 short sentences. Let the approver approve, reject or give another instruction. Check and post the result.

**Completed**
- `policy.yaml` (human-owned): severity definitions, approval time limit per severity (P0 15 min, P1 30 min, P2 60 min), Slack channel, mention and approver list per severity.
- The agent assigns a severity in every report and every proposal (schema-validated).
- `nightwatch/notify/slack.py`: one Slack thread per incident in the channel of its severity. The plan message has: what I checked (built from the audit log, not from model text), what I found, cause, symptoms not causes, plan (action, effect, why, risk, rollback), verification, not known. Buttons: Approve (with a confirm dialog), Reject, Suggest another fix. "Give instructions" when no allowed action can fix the problem.
- `nightwatch/notify/slack_app.py`: Socket Mode app (no public URL). Each click is checked against the approver list of that severity.
- An approver instruction replaces the pending plan (never executed). The agent receives it as `APPROVER INSTRUCTION`, tests it, and proposes a new plan that needs approval again, or verifies a manual fix.
- After execution: verification result and final status (RESOLVED / PARTLY RESOLVED / NOT RESOLVED) in the thread.
- Dry run without Slack tokens (`runs/slack-outbox.jsonl`); `nightwatch guide <incident> "text"` and `nightwatch slack-test`.
- Audit store made safe for several processes (WAL, `BEGIN IMMEDIATE` for the hash chain).
- Tests that ran: 23 passed, including an offline end-to-end Slack flow (P0 routing, one thread, replaced plan never runs, approved plan runs, RESOLVED posted).

**Live dry-run test** (redis-down, `claude-haiku`): plan → instruction ("read the Redis logs first, then start it") → new plan → approve → execute → verified → RESOLVED, all in one P0 thread. Cost $0.13.

**Failures found and fixed in the live test**
- Haiku again wrote a regex in a literal `contains` check. The verification engine now accepts regex-like text as a regex.
- The Slack message lost one of three check results (text parsing). Verification now returns structured check results.
- Thread replies repeated all steps. They now show only new steps.
- Decisions from the CLI did not appear in Slack. The runner now announces every decision, whatever its source.
- The agent verified a fix only with "container list contains nw-redis". New deterministic rule: if the symptom names a URL, the verification must repeat that request (`VERIFICATION_MISSES_SYMPTOM`).

**Outstanding**
- Builder: create the Slack app from `slack/manifest.yaml`, add tokens, create channels, set approver IDs.

### 2026-10-11 — Slack decisions and escalation

**Builder decisions:** approver lists per severity (IDs pending); one channel for all levels (`#nightwatch`); one approver is enough (no two-person rule); escalate when an approval expires. Severity floor rule: not decided.

**Completed**
- `policy.yaml`: single channel; `slack.escalation` (reminder at 50% of the time limit, max 2 levels, contacts per severity).
- Reminder in the thread at half of the time limit, with the approver mentions.
- At expiry: NightWatch re-reads the target state. Same state → it re-issues the plan as a new approval (new expiry) and mentions the escalation contacts, who may then decide too. Changed state → no re-issue; the agent investigates again. After the last level → "No change was made. A human must act."
- Tests that ran: 24 passed. New: expiry → reminder → escalation → escalation contact approves → executed.
- Failure found while testing: with a 1.5 s time limit, the escalated approval expired between the click and the runner's next poll (2 s), and the worker refused it (`STALE_APPROVAL`). This is correct behavior. The test now uses 3 s; real limits are 15–60 min.

**Planned (not implemented): RAG and SaaS** — retrieval over past incidents, runbooks, change history and the service map (no fine-tuning); split into a hosted control plane and a customer-side agent; tenant isolation, SSO, web UI, incident queue, billing, shadow-mode onboarding.

### 2026-10-11 — First live Slack approval

- Slack app installed (bot `nightwatch2`, Socket Mode). Channel and member IDs are in the git-ignored `policy.local.yaml`, not in the public repo.
- Live run (`redis-down`, `claude-haiku`, prompt v5): plan posted to the channel at 11:36:47 (P0, @channel). The approver clicked **Approve** in Slack at 11:42:35. NightWatch executed `start_container nw-redis` at 11:42:36, verified 3/3 checks at 11:42:42, and reported `resolved`. Cost $0.11. Audit chain intact. Approver recorded as `slack:<member ID>`.
- Known limit: the app uses rotating app-level tokens (`xoxe.xapp-…`). They expire. Before the demo, generate a fresh app-level token.
- Note: commit `797b569` contains the approver member ID in the public history. The current files do not.

### 2026-10-11 — More connectors

**Builder decision:** build Grafana + Loki, AWS read-only, neatlogs read, and Linux servers over SSH (systemd, journald). AWS: the builder's company account, with a read-only IAM user that the builder creates.

**Completed**
- Grafana connector: alert rules with state/health/errors, dashboard search, panel queries, annotations.
- Loki connector: LogQL log search across all services and log-count metrics; label discovery.
- neatlogs connector (Public API): traces, spans, keyword search, alert history. Needs a read credential; the SDK ingest key returns 401 (tested).
- AWS connector, 12 read-only tools. Named profile only (never the default profile), optional account pin, least-privilege policy in `docs/aws-readonly-policy.json`. Tested offline with botocore Stubber.
- Linux over SSH: all 19 checks on `host:<name>`, plus `journal` and `linux_service_status`. Key only, `BatchMode=yes`, `StrictHostKeyChecking=yes`, fixed scripts, unit names passed as arguments.
- Unconfigured connectors are hidden from the model.
- Lab: Grafana (dashboard "Shop overview", alert rules ShopHigh5xx, ShopHighLatency, ShopErrorLogs), Loki + Promtail (all containers and the VM journal), VM `nw-vm` (systemd, journald, SSH key login, `shop-worker.service`), faults `vm-service-crash` and `vm-disk-full`. Memory about 1.2 GB in total.
- Tests that ran: 31 passed (7 new). Live: Grafana alerts and panels, Loki labels and VM journal lines, SSH load/service status/journal on `host:nw-vm` all returned real data.
- Lab finding: `ShopHighLatency` did not fire under `api-cpu-hog` (p95 levels at about 0.475 s, threshold 0.5 s). Not changed yet.
- Data note for AWS: tool output goes to the LLM proxy and to neatlogs. The builder must confirm that the company allows this.

**Outstanding (builder):** AWS read-only user and profile; neatlogs read token and project ID.
- Live eval with the new connectors (prompt v5, `claude-haiku`, diagnosis mode): `redis-down`, `vm-service-crash`, `vm-disk-full` → root cause, resource and fix decision correct in 3/3. On the VM faults the agent used SSH checks, `linux_service_status`, Loki and Grafana by itself. Cost $0.36 in total.
- Failure found: `vm-service-crash` needed 30 tool calls (187 s, $0.22). The decisive journal error line was in the middle of 50 lines of `linux_service_status`, and one LogQL query used an invalid `| tail 20`. Fix: `linux_service_status` now shows recent error and warning lines first; the `loki_query` description says that LogQL has no tail/head. Not re-measured yet.

### 2026-10-11 — AWS (masked) and neatlogs read, live

**Builder decisions:** AWS on for the agent, **with masking**. The builder added a read-only IAM user (default profile) and a neatlogs service-account token.

**Completed**
- AWS identity check (local, no LLM): user `NightWatch-agent`, account matches `.env`. IAM simulation: terminate, stop, security-group ingress, create access key, delete object → `implicitDeny`. Note: `s3:GetObject` is allowed (broad `ReadOnlyAccess` policy); NightWatch does not call S3; the least-privilege policy is recommended.
- Live AWS connector (counts only): 32 EC2 instances (25 running, 0 impaired), 12 load balancers, 0 alarms in ALARM, CloudTrail events OK.
- Masking (`nightwatch/masking.py`): account IDs (also in ARNs), public IPs/CIDRs, IAM user names, role session names and e-mails become stable placeholders; placeholders in tool inputs are restored before the AWS call. Verified on live output: no account ID, public IP or e-mail. Tests: 33 passed.
- neatlogs read: `400 VALIDATION_ERROR` until `NEATLOGS_PROJECT_ID` was the project **UUID** (found with `GET /projects`); page size max 50. NightWatch now reads its own 98 traces. Evidence page with trace IDs for the key runs: `docs/neatlogs-evidence.md`.
- Finding: neatlogs records 0 tokens and $0 for traces sent through the LiteLLM proxy. Measured costs come from the proxy.

### 2026-10-11 — Live watch mode with Slack, and shorter Slack messages

- Live run (watch mode, `--approval slack`, `claude-haiku`, v5): `redis-down` injected with no hint. Watch mode detected it in 2 rounds (about 30 s) and opened ONE incident for 3 symptoms. P0 plan "Start container nw-redis" posted. The builder used **Suggest another fix**: "find out the cause and make the policy to restart always and for now restart the container". Plan 1 was cancelled (not executed); a new plan was posted; the builder approved; executed; verification 3/3; resolved.
- **Builder feedback:** the Slack messages were too long and not ASD-STE100 (first message: 43 lines, 409 words).
- **Fix:** main plan message about 9 short lines (problem, cause, plan, effect, risk, rollback, check, buttons), about 80 words. Evidence moved to one "Details" thread reply (counts and max 4 findings). No thread post for Approve/Reject (the button area shows it). One final message instead of a full report plus a status line. Every model sentence trimmed to 1 sentence and max 18 words, no semicolons. Word limits added to the report and proposal fields. Tests: 32 passed (new test: main message ≤ 12 lines and ≤ 140 words).
- **Reasoning error found:** after the instruction "find out the cause", the new plan claimed that a shop-worker restart on the VM "triggered container cleanup". There is no such link: a lab script stopped Redis at about the same time. This breaks the rule "time correlation is not causation". Open item.
- **Fix for the causation error** (`nightwatch/causality.py`): a dependency graph built only from the human-owned `inventory.yaml`. The final report must contain a `causal_chain`; each link needs an exact quote from a tool result of this incident. A link passes only when the two parts are connected in the graph, or the quoted line names both. A plan whose reason links two components with no dependency is rejected (`UNSUPPORTED_CAUSAL_CLAIM`). Tests: 36 passed, including a replay of the live mistake (shop-worker on nw-vm "caused" the Redis stop) → rejected; the true chain (Redis → API → web) passes; an invented quote is rejected.
