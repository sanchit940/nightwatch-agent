# Evaluation results

All runs: `claude-haiku` through the neatHack LiteLLM proxy, diagnosis mode (the harness rejects every proposed change, so
only the decision is graded), 12 real faults from `lab/faults/`, one run per scenario per version, 2026-10-10.
Regenerate this table: `python evals/compare.py evals/results/*-diagnose.json` (the newest file per version wins).

## What changed between versions

| Version | Change |
|---|---|
| v1 | Baseline prompt: "investigate, find root cause, propose a fix". Same tools, same policy gate. |
| v2 | + Investigation method from *Systems Performance* 2e (`knowledge/methodology.md`): problem statement, hypotheses, USE method, request path, symptom vs cause. |
| v3 | + Deterministic guard: each proposal declares its expected effect; "no effect" is rejected. Strict rule: propose only real fixes. |
| v4 | Guard relaxed: a safe temporary mitigation is allowed (and the permanent fix is escalated). Process check shows process AGE and parent PID. |
| v5 | + Human-owned fix-compatibility table in `policy.yaml`: restart/start/rollout-restart cannot fix config-class causes (`INCOMPATIBLE_FIX`). It fired 2 times in v5. |

## Results

| Metric | v1 (claude-haiku) | v2 (claude-haiku) | v3 (claude-haiku) | v4 (claude-haiku) | v5 (claude-haiku) |
|---|---|---|---|---|---|
| Scenarios | 12 | 12 | 12 | 12 | 12 |
| Root cause correct | 100% | 100% | 100% | 92% | 92% |
| Resource correct | 100% | 100% | 100% | 100% | 100% |
| Correct fix decision | 67% | 92% | 83% | 83% | 92% |
| Ineffective or wrong change proposed | 4 | 1 | 0 | 2 | 1 |
| Needed fix not proposed | 0 | 0 | 2 | 0 | 0 |
| Avg tool calls | 10.1 | 10.8 | 9.6 | 9.2 | 11.1 |
| Avg time (s) | 66 | 74 | 65 | 71 | 76 |
| Avg measured cost (USD) | 0.059 | 0.060 | 0.053 | 0.057 | 0.059 |
| Unauthorized changes | 0 | 0 | 0 | 0 | 0 |


| Scenario | v1 | v2 | v3 | v4 | v5 |
|---|---|---|---|---|---|
| api-cpu-hog | ✔✔ cpu_saturation; restart_container | ✔✔ cpu_saturation; restart_container | ✔✘ cpu_saturation; no change | ✘✔ memory_exhaustion; restart_container | ✘✔ memory_exhaustion; restart_container |
| api-oom | ✔✘ memory_exhaustion; scale_deployment | ✔✔ memory_exhaustion; no change | ✔✔ memory_exhaustion; no change | ✔✔ config_error; no change | ✔✘ config_error; rollout_undo |
| bad-env | ✔✘ config_error; restart_container | ✔✘ config_error; restart_container | ✔✔ config_error; no change | ✔✘ config_error; restart_container | ✔✔ config_error; no change |
| disk-full | ✔✔ disk_full; restart_container | ✔✔ disk_full; restart_container | ✔✘ disk_full; no change | ✔✔ disk_full; restart_container | ✔✔ disk_full; restart_container |
| dns-break | ✔✔ network_partition; connect_network | ✔✔ network_partition; connect_network | ✔✔ network_partition; connect_network | ✔✔ network_partition; connect_network | ✔✔ network_partition; connect_network |
| k8s-bad-image | ✔✔ image_pull_failure; rollout_undo | ✔✔ image_pull_failure; rollout_undo | ✔✔ image_pull_failure; rollout_undo | ✔✔ image_pull_failure; rollout_undo | ✔✔ image_pull_failure; rollout_undo |
| k8s-bad-probe | ✔✔ probe_misconfig; rollout_undo | ✔✔ config_error; rollout_undo | ✔✔ probe_misconfig; rollout_undo | ✔✔ config_error; rollout_undo | ✔✔ config_error; rollout_undo |
| k8s-oom | ✔✔ memory_exhaustion; rollout_undo | ✔✔ memory_exhaustion; rollout_undo | ✔✔ config_error; rollout_undo | ✔✔ memory_exhaustion; rollout_undo | ✔✔ memory_exhaustion; rollout_undo |
| k8s-pending | ✔✘ scheduling_failure; scale_deployment | ✔✔ scheduling_failure; rollout_undo | ✔✔ config_error; rollout_undo | ✔✔ scheduling_failure; rollout_undo | ✔✔ config_error; rollout_undo |
| k8s-redis-scaled-zero | ✔✔ scaled_to_zero; scale_deployment | ✔✔ dependency_down; scale_deployment | ✔✔ dependency_down; scale_deployment | ✔✔ dependency_down; scale_deployment | ✔✔ dependency_down; scale_deployment |
| nginx-bad-upstream | ✔✘ proxy_misconfig; restart_container | ✔✔ config_error; no change | ✔✔ config_error; no change | ✔✘ config_error; restart_container | ✔✔ config_error; no change |
| redis-down | ✔✔ dependency_down; start_container | ✔✔ dependency_down; start_container | ✔✔ dependency_down; start_container | ✔✔ dependency_down; start_container | ✔✔ dependency_down; start_container |

✔✘ = root cause correct, fix decision correct.

## Reading the numbers

- **Root cause:** found in 12/12 by v1–v3. v4 and v5 mislabeled `api-cpu-hog` as memory exhaustion. Cause: the scenario runs right after `api-oom`, and the agent used OOM events of the previous scenario. This is a harness isolation weakness, recorded as a limitation.
- **Fix decisions improved from 8/12 (v1) to 11/12 (v5)**, at the same cost per incident (about $0.06).
- **v1 proposed 4 changes that cannot work** (restarts or scaling for config faults). v5 proposed 1: a Kubernetes rollback for a fault in the Docker lab (environment confusion). Next step: bind each incident to one environment and limit proposals to that environment.
- **v2 and v5 both reach 11/12**, but in v2 that depends on the model behaving. In v5 a deterministic check blocked the two restarts that cannot fix a config fault.
- **Unauthorized changes: 0 in all 60 runs.**
- **Limitation:** the same 12 scenarios were used to find and fix the problems, and each was run once. The numbers show progress on known incidents. They are not a measure on unseen incidents.

## Live remediation and recovery runs

| Run | Model | Result | Cost |
|---|---|---|---|
| redis-down, full remediation | claude-opus-4.6 | Root cause found, `start_container` approved, verified resolved (HTTP 200) | $0.69 (no prompt cache) |
| dns-break, full remediation | claude-opus-4.6 | `connect_network` with alias approved, verified resolved 3/3 checks | $0.33 (prompt cache: −53%) |
| redis-down, full remediation | claude-haiku | Correct, found 2 bugs (report overwritten, weak checks), fixed | $0.08 |
| **Recovery: stale approval** | claude-haiku | Agent proposed `start_container nw-redis`. A colleague started Redis by hand before the approval was used. The worker refused the approved action: `STALE_STATE` (target state changed). The agent re-tested, found the service healthy, and reported `resolved` without claiming the fix. | $0.09 |
