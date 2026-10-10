# Entire evidence

NightWatch was built with Claude Code as the coding agent. Entire recorded the agent sessions and linked them to the commits.

## How Entire was used

1. `entire enable -y --agent claude-code` installed 9 Claude Code hooks in this repository (`.claude/settings.json`, `.entire/settings.json`).
2. Each agent-assisted commit became a **checkpoint**. The checkpoint pairs the code change with the prompt, reasoning and tool calls behind it.
3. Each commit message on `main` carries an `Entire-Checkpoint: <id>` trailer. A judge can match every commit in this repository to its checkpoint:
   ```bash
   git log --format='%h %s%n  %(trailers:key=Entire-Checkpoint,valueonly)' main
   ```
4. We used checkpoints during the build to review **why** a change was made. Example: checkpoint `01M4JQYCY60V88239G5A9FFR9R` holds the session in which the first live run showed 0 prompt-cache reads, and the change that fixed it (cost per incident $0.69 → $0.33).

## What is published, and what is not

- **Published:** checkpoint IDs, times, token counts, linked commits and the first line of the prompt (below).
- **Not published:** the full session transcripts. They contain the builder's personal and work email addresses and other private context. On 2026-10-10 Entire's push hook pushed them to GitHub by default; we deleted those refs within minutes and set `push_sessions: false`. See the build log in `hackathon.md`.
- The transcripts remain available locally (`entire checkpoint explain <id>`).

## Key checkpoints

Times are UTC.

```
● Checkpoint 01M4JKFNN0P3KJYXDZMS5NWFJE
  session  b9e7beed-68d7-42f2-a0a2-d99022ce6f54
  created  2026-10-10 09:49:41
  tokens   8216.4k
  commits  8a684e7 Add agent core: connectors, policy gate, audit chain, orchestrator, watch mode
────────────────────────────────────────────────────────────

● Checkpoint 01M4JQYCY60V88239G5A9FFR9R
  session  b9e7beed-68d7-42f2-a0a2-d99022ce6f54
  created  2026-10-10 11:07:36
  tokens   19837.1k
  commits  ff28b05 Live run 2 (dns-break) resolved; caching cut cost 53%; accept equivalent root-cause labels; build log
────────────────────────────────────────────────────────────

● Checkpoint 01M4JRJACVVS00GRX6FR2CR0J0
  session  b9e7beed-68d7-42f2-a0a2-d99022ce6f54
  created  2026-10-10 11:18:29
  tokens   23972.8k
  commits  b0c5f3a Validate final reports, report best verification round, add regex checks
────────────────────────────────────────────────────────────

```

## All checkpoints on `main` (`entire checkpoint list`)

```
  branch       main
  checkpoints  13

● b9e7beed-68d  [temporary]  "added the neatlogs api key and anhtropic key i dont have ..."
  10-10 17:10 (5684aaf) <task-notification> <task-id>belrwymeu</task-id> <tool-use-id>toolu_014d
  10-10 16:50 (035196b) carry forward: uncommitted session files

● 01M4JRNCHTZAA4BFFX4FWY1AGB  "added the neatlogs api key and anhtropic key i dont have ..."
  10-10 16:50 (bdf1695) Build log: Haiku findings, public repo, checkpoint privacy fix

● 01M4JRMVV4GFZV6SGPWMDY57Q1  "added the neatlogs api key and anhtropic key i dont have ..."
  10-10 16:49 (a76441c) Entire: do not push session logs automatically (transcripts contain personal ...

● 01M4JRJACVVS00GRX6FR2CR0J0  "added the neatlogs api key and anhtropic key i dont have ..."
  10-10 16:48 (b0c5f3a) Validate final reports, report best verification round, add regex checks

● 01M4JRFW1XSCCY24A71MHY5ZRZ  "added the neatlogs api key and anhtropic key i dont have ..."
  10-10 16:47 (387b566) Add claude-haiku price estimate (builder switched the agent model to claude-h...

● 01M4JQYCY60V88239G5A9FFR9R  "added the neatlogs api key and anhtropic key i dont have ..."
  10-10 16:37 (ff28b05) Live run 2 (dns-break) resolved; caching cut cost 53%; accept equivalent root...

● 01M4JQS8CKGFGT0GZR7ZEXZHBQ  "added the neatlogs api key and anhtropic key i dont have ..."
  10-10 16:34 (b9544ba) Route LLM through neatHack LiteLLM proxy; explicit prompt-cache breakpoints; ...

● 01M4JQD17PPWHC9AM25KGHWMF8  "added the neatlogs api key and anhtropic key i dont have ..."
  10-10 16:28 (0bc92b4) Add 'nightwatch check': one watch round without model calls

● 01M4JMN2J41XT3ACA4Y2NWKKY2  Update build log: lab and watch results
  10-10 15:40 (f1162eb) Update build log: lab and watch results

● 01M4JMMRZ8CZB14K90GWY9K8WC  Fix watch pod-table parsing (header was read as a pod)
  10-10 15:39 (14beea1) Fix watch pod-table parsing (header was read as a pod)

● 01M4JMM34MDBF847ZY7P68V8EK  "<agent-message from=\"a56467c16d7648dc9\">\n[Subagent hand-b..."
  10-10 15:39 (5128fd6) Add fault lab (Docker Compose + kind, 12 real faults) and eval scenarios

● 01M4JKJ7A2FX4ZQJKYHRXJA5JC  "<agent-message from=\"a56467c16d7648dc9\">\n[Subagent hand-b..."
  10-10 15:21 (d5ab979) Add evaluation harness; fix event timestamps and restarting-target errors; up...

● 01M4JKFNN0P3KJYXDZMS5NWFJE  "<agent-message from=\"a56467c16d7648dc9\">\n[Subagent hand-b..."
  10-10 15:19 (8a684e7) Add agent core: connectors, policy gate, audit chain, orchestrator, watch mode

```
