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

- Script: `docs/demo-script.md` (watch mode → investigation → Slack approval with an instruction → verification → neatlogs, Entire, cfo.ai).
- Reproduce: `bash lab/compose/up.sh`, `bash lab/k8s/up.sh`, `nightwatch watch --approval slack`, then `bash lab/faults/fault.sh redis-down inject`.
- Video link: pending.

## Known limitations and next steps

- Evaluated on 12 (+2 VM) known lab faults, one run each; the same scenarios were used to find and fix problems. Next: held-out scenarios and repeated runs.
- One incident at a time (the watch loop waits for the incident). Next: an incident queue with workers.
- The causal guard still rejects about 2 reports per incident (the model paraphrases evidence); costs about $0.01–0.02 extra.
- An incident is not yet bound to one environment (one v5 run proposed a Kubernetes rollback for a Docker fault).
- AWS, neatlogs-read and SSH connectors are read-only; there are no cloud change actions yet. Azure, GCP, CI/CD, PagerDuty: not built.
- The approval secret is in the same process as the orchestrator. Next: a separate policy service process.
- neatlogs shows 0 tokens and $0 for traces sent through the LiteLLM proxy; measured costs come from the proxy.
- Slack app-level tokens rotate and expire; the operator must refresh them.
- Next product steps (planned, not built): RAG over past incidents and runbooks; control plane + customer-side agent; SSO; web UI; billing.

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

**Outstanding items** (status 2026-10-11 evening; checklist with links: `docs/SUBMISSION.md`)

- [ ] Team names or handles (builder).
- [x] neatlogs account, ingest key and read token.
- [x] LLM access: neatHack LiteLLM key (no direct Anthropic key).
- [x] cfo.ai sign-up; subscription cancelled (builder). [x] cfo.ai forecast built and exported (`business/nightwatch-forecast-cfo-ai.xlsx`, summary `business/cfo-forecast-summary.md`).
- [x] Entire login. Mirror: not used (transcripts private).
- [x] Public GitHub repository: https://github.com/sanchit940/nightwatch-agent
- [ ] neatlogs screenshots of the key traces in `docs/neatlogs-evidence.md` (builder).
- [ ] Demo video (length and format not announced).
- [ ] Building-in-public posts with #neatHack (drafts in `docs/posts.md`).
- [ ] Final cutoff time: announced on Discord (check on Monday). Submissions: "opening soon" (checked 2026-10-11).
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
- AWS identity check (local, no LLM): a dedicated read-only IAM user, account matches `.env`. IAM simulation: terminate, stop, security-group ingress, create access key, delete object → `implicitDeny`. Note: `s3:GetObject` is allowed (broad `ReadOnlyAccess` policy); NightWatch does not call S3; the least-privilege policy is recommended.
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
- **Live runs 2–3 of the causal guard, and fixes:**
  - Run 2: 8 report rejections, because correct quotes spanned multi-line JSON with escaped quotes. Then the agent hung in round 2: the proxy kept the stream open with ping events, so the 180 s read timeout never fired. Fixes: loose quote matching; **total time limit of 240 s per model call** (2 attempts, then the round ends and `llm_unavailable` is logged); `kill -USR1` prints a stack dump.
  - Run 3 (watch mode + Slack, same instruction as the builder's): plan 2 "why" had **no false causal claim**; resolved. The guard still rejected 5 reports: the agent writes summaries, combines two sources in one evidence text, and put fix steps in the chain.
  - Fixes: evidence is matched per record (one log/event line, or one whole JSON result, tagged with tool name and arguments); a piece of evidence passes if its text or all its specific values (numbers, names, IDs) are in one record; the reported symptom counts as a source; the chain has max 4 links (root cause → symptom); the strict "dependency or one record names both" rule applies only when cause and effect name two different components; after 2 rejections the report is accepted with unproven links moved to "Not proven" and confidence "low".
  - Offline replay of the rejected chains of the last eval run: the honest chains now pass; the one wrong claim ("the restart caused Redis to lose its network", in dns-break) is still rejected. Tests: 41 passed.
- Live confirmation (v5, `claude-haiku`, redis-down + dns-break, diagnosis mode, $0.17): both causal chains honest. dns-break chain now starts at "nw-redis disconnected from nw-back" (the false "restart" link is gone); one unprovable link was moved to "Not proven" with confidence low. No model hang. Remaining: 2 report rejections per incident (the agent still paraphrases some evidence; about $0.01–0.02 extra each); dns-break labeled `dependency_down` (resource correct).

### 2026-10-11 (night) — Business plan with cfo.ai

- **Builder decision:** prices must be very low ("$200 for a wrapper is not a good deal"). Customers bring their own model key or use ours; customers add their own data through a knowledge base (RAG).
- New plan inputs (v2, `business/cfo-plan-inputs.md`): Community $0 (self-hosted), Starter $5, Team $15, Business $39 per environment per month; $0.12 per extra incident on our key (measured cost $0.06). No model training on customer data; RAG and learning from approvals instead.
- **cfo.ai forecast** (builder ran the prompt in Ari and exported `business/nightwatch-forecast-cfo-ai.xlsx`): base case break-even in month 7 (Apr 2027), gross margin 71%, lowest cash $3,117, cash $43,562 at month 18. Team at $19: break-even month 6. 50% bring-your-own-key: margin 77%. Slowdown to 30 new customers per month: cash below zero for 4 months. The base numbers match our own quick check exactly.
- Checked before publishing: the workbook contains only NightWatch plan data (no e-mail, no bank or payment integration data).
- Gap: the Downside case (15 new customers per month) is not in the export.

### 2026-10-11 (night) — Real AWS account, measured with neatlogs

- **Builder request:** investigate the real company AWS account (read-only user, masking on) and use neatlogs and Entire to find where the agent wastes time or claims things it cannot prove.
- Ground truth first, without the model: 8 problems (stopped instances still behind the staging ALB, failing health checks, wrong HTTP codes, a timeout, a port mismatch, 255 empty target groups, 31 unattached volumes, one failed API call). Details, anonymized: `docs/aws-real-runs.md`.
- **Finding: our tools cut output silently** (first 20 load balancers, 10 target groups each, first 50 volumes). Fixed: pagination or an explicit "showing N of M".
- **neatlogs trace of run 1:** 20 model calls = 82% of 143 s; 15 guessed CloudTrail lookups; 4 calls to the lab; 2 rejected reports in the 3 longest model calls. Report: a guessed cause, confidence low; the biggest outage missed.
- Fixes (Entire checkpoints `01M4NQZY0QQ05YVVDRD9NAXJ74`, `01M4NR5YXXFR5XKH44F5BND99F`, `01M4NRD5A3DT1N10SBGNT8JG08`): `aws_health_summary`; batched CloudTrail; environment binding (lab incidents cannot call AWS tools, AWS incidents cannot call lab tools); `other_findings`; prompt v6; no network tools for AWS; cheaper report rejections.
- **Result run 1 → run 4:** problems found 3 → 6 of 8, time 146 s → 105 s, cost $0.20 → $0.05, tool calls 33 → 11, model calls 20 → 11, rejected reports 2 → 0. Tests: 42 passed (3 consecutive runs; one earlier flaky failure, probably the 3-second escalation test).
- **Correction:** neatlogs records tokens and duration per model span; only the trace totals show 0 through the proxy. Docs updated.
- **Privacy:** resource names are not masked. Some names identify customers of the company. They reached the LLM proxy and the private neatlogs project; they are not in the public repo. Open decision for the builder: mask resource names too.

### 2026-10-11 (night) — CI/CD connector: Jenkins, GitHub Actions, GitLab CI

- **Builder request:** a versatile CI connector so that teams can use Jenkins, GitHub Actions or GitLab.
- **Completed** (`nightwatch/connectors/ci.py`): one interface, three providers. Tools `ci_runs` (failing since / last good), `ci_failure_logs` (errors first, redacted), `ci_compare_runs` (commits, files, Jenkins parameters; same-commit and trigger hints); action `ci_rerun_failed` (approval; blocked by `fix_compatibility` for test, build and config failures; only for projects in `policy.yaml`). New environment `ci`; new causes `build_failure`, `test_failure`, `flaky_failure`, `ci_infrastructure`. 52 tools.
- **Tests:** 6 offline provider tests (48 passed, 1 skipped). Live: GitLab.com public project (runs OK; job logs need a token: clear message); GitHub Actions on this repo (new `tests` workflow, passes on push; two manual runs failed in Test and Install; logs read with a read-only token); local Jenkins (`lab/jenkins/`, kind paused for memory).
- **Agent runs** (v6, `claude-haiku`, environment `ci`, changes denied): first runs found the cause, but the lab names gave it away (`FAULT`, `inject_failure`, `test_ci_lab_fault.py`, "lab fault" in the inventory). Renamed to neutral names and ran again: Jenkins → "registry credential REGISTRY_TOKEN not set for the publish-only profile" (correct); GitHub → the exact line `tax_rate * 0` and its fix (correct). 3 tool calls, 5–7 model calls, 35–52 s, $0.03–0.05 each. No re-run proposed (correct).
- **Fix from the GitHub run:** the agent called the test "non-deterministic" because the same commit passed on push. `ci_compare_runs` now says when the trigger differs (manual runs can use other inputs).
- Lab note: the stopped Jenkins container was renamed to `jenkins-lab`, so watch mode (prefix `nw-`) does not report it.

### 2026-10-11 (late night) — Company Jenkins with Okta login

- **Builder update:** added a Jenkins API token for the company Jenkins (people log in through Okta). Not yet set: `JENKINS_URL`, `JENKINS_USER`. No call to the company Jenkins yet.
- **Found during review:** the HTTP helper followed redirects with the token. A login proxy that redirects to Okta would receive the token. Fixed: redirects keep the token only on the same host; to another host the request stops with `SSO_REDIRECT` (or `SSO_LOGIN_PAGE` for an HTML login page). Log downloads still follow without the token.
- `JENKINS_API_TOKEN` is accepted as an alias of `JENKINS_TOKEN`; a token without `JENKINS_USER` gives a clear error. Tests: 50 passed, 1 skipped.
