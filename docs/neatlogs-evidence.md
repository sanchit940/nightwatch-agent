# neatlogs evidence

All NightWatch runs are traced in neatlogs (project "Sanchit's project"). Each investigation is one trace (one per round);
the neatlogs session ID is the NightWatch incident ID. Spans: each model call, each tool call (`TOOL`), each policy
decision (`GUARDRAIL`), each action and verification. The list below was read back through the neatlogs Public API
with NightWatch's own `neatlogs_traces` connector.

Traces since 2026-10-10 by workflow: `nightwatch-eval-v1` 12, `nightwatch-eval-v2` 27, `nightwatch-eval-v3` 12, `nightwatch-eval-v4` 12, `nightwatch-eval-v5` 15, `nightwatch-investigate` 17, `nightwatch-recovery-stale` 3.

| Run | Incident (session) | Trace IDs | Tags | What it shows |
|---|---|---|---|---|
| First live run (Opus 4.6) | `inc-20261010-163115-b70d` | `6169a87aae1f3001b3a0cb86bd99c96c`, `80c4bb46da27c97768865d57fa78e66c` | model:claude-opus-4.6,prompt:v2 | first live run (its token numbers come from NightWatch's run stats: 124k input, 0 cache reads) |
| After the caching fix (Opus 4.6) | `inc-20261010-163504-e688` | `ce77aab548090e1a7a10aa036cd3b0e3`, `58535eb04e9e0d83465cf25f8895bba4` | model:claude-opus-4.6,prompt:v2 | after the caching fix (run stats: 121k tokens from cache, cost −53%) |
| Before: v1, nginx-bad-upstream | `inc-20261010-165140-bb3b` | `fc57e282a704b4e87be0f3c675c818ef` | model:claude-haiku,prompt:v1 | v1 proposed a restart for a config fault |
| After: v5, nginx-bad-upstream | `inc-20261010-181912-ce9e` | `641a7d2670bf309b303d5d4c31d51417` | model:claude-haiku,prompt:v5 | v5: restart blocked (INCOMPATIBLE_FIX), escalated to a human |
| Before: v1, k8s-pending | `inc-20261010-171652-7b19` | `ab068bbe221d43f6d0e5869e52b3fdc0` | model:claude-haiku,prompt:v1 | v1 proposed scaling (cannot fix) |
| After: v5, k8s-pending | `inc-20261010-183558-ae1c` | `fd2b7555b0685f0c72d93eced80ca6d6` | model:claude-haiku,prompt:v5 | v5 proposed a rollback of the bad deploy |
| Recovery: stale approval | `inc-20261010-184058-8977` | `2991d1eacab59f586f926477e9a3e3d2`, `bb5c95391fa3cae526a36a31e5e9caf0` | model:claude-haiku,prompt:v5 | approved action refused (STALE_STATE); agent re-tested and reported |
| Slack approval (live) | `inc-20261011-113552-f3e8` | `63aa6e6e100ab6fc3b54376310504618`, `1088deaccaa555ab81b744dcdedbf74d` | model:claude-haiku,prompt:v5 | plan in Slack → human approved → executed → verified resolved |

Note: neatlogs shows 0 tokens and $0 for these traces: token usage is not reported through the LiteLLM proxy.
Measured cost per incident is in `evals/results/*.json` (`measured_cost_usd`, from the proxy).
