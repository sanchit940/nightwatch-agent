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

**Planned**
- Distill the *Systems Performance* book into short runbooks for the agent prompt.
- Build the core: policy service, audit store, connectors, orchestrator, verification engine.
- Build the Docker Compose fault lab and the kind cluster.
- Build the evaluation suite with known incidents (before/after numbers for neatlogs).

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
