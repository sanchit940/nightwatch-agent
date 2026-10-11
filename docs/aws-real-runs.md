# Real AWS account: four runs, measured with neatlogs

The builder gave NightWatch a **read-only** IAM user for a real company AWS account (one region, staging).
Identifiers were masked (account IDs, public IPs, users, e-mails). This page is **anonymized**: it names no
resources, customers or account details. The symptom given to the agent, in all runs:
"Some of our staging web services on AWS do not respond. Find what is broken or degraded now, and why."
Every change proposal was denied (read-only investigation). Model: `claude-haiku`.

## Ground truth (built without the model)

The new `aws_health_summary` tool and direct API checks found 8 problems:

| # | Problem |
|---|---|
| A1 | 5 instances were stopped by a user, but they are still the only targets of 22 target groups on the staging ALB |
| A2 | 1 running instance fails all its health checks (one check calls path `/jj` on port 80 for a service on port 8200) |
| A3 | 3 running services return the wrong HTTP code to the health check |
| A4 | 1 target (a secret store) times out |
| B | An internal ALB target fails its health check (check on port 8080, target on port 8443), so the public NLB in front of it fails too |
| C | 255 target groups have no targets |
| D | 31 EBS volumes (1,010 GiB) are not attached (cost) |
| E | One `CreateLoadBalancer` call failed with a duplicate name |

**First lesson:** our own first snapshot found only part of this list. `aws_elb_health` read only the first 20 load
balancers and 10 target groups each, and `aws_ebs_volumes` the first 50 volumes, **without saying so**. The agent
had the same blind spot. Now every AWS tool reads all pages or states "showing N of M".

## What the neatlogs trace showed in run 1

| Span type | Count | Time |
|---|---|---|
| Model calls | 20 | 116.9 s (82% of 143 s) |
| Tools | 33 | 35.5 s |
| Policy checks | 33 | about 0 s |

- 15 of 33 tool calls were CloudTrail lookups with guessed event names, one at a time.
- 4 calls went to the local lab (wrong environment).
- 15 s went to network tests from the laptop to an internet endpoint.
- The 3 longest model calls (54 s) were report writing, including 2 reports that the causal guard rejected.
- The main cause in the report was a guess ("a controller is stuck"), confidence low. The biggest outage (A1) was missed.

## Fixes, each from a trace finding

| Commit | Entire checkpoint | Fix |
|---|---|---|
| `e7147cf` | `01M4NQZY0QQ05YVVDRD9NAXJ74` | `aws_health_summary` (one call, problems first, grouped by cause, stopped targets resolved to their instance); all AWS tools paginate or say when output is cut; CloudTrail takes up to 10 event names in one call; incidents are bound to one environment (an AWS incident cannot call lab tools; a lab incident cannot call AWS tools); reports list other proven problems; prompt v6 |
| `794dff2` | `01M4NR5YXXFR5XKH44F5BND99F` | No network tools for AWS incidents (run 2: 7 timeouts to private VPC addresses, about 35 s) |
| `93a43e9` | `01M4NRD5A3DT1N10SBGNT8JG08` | A missing list field no longer rejects a report; unproven causal links are downgraded after one rejection (run 3: 2 rejections cost about 40 s) |

## Results

| Run | Problems found (of 8) | Main cause | Tool calls | Model calls | Rejected reports | Time | Cost |
|---|---|---|---|---|---|---|---|
| 1 (v5, old tools) | 3 | guess, low | 33 | 20 | 2 | 146 s | $0.20 |
| 2 (v6, new tools) | 6 | A1, high | 25 | 20 | 0 | 141 s | $0.13 |
| 3 (+ no network tools) | 6 | A1 + A2, low (downgraded) | 6 | 8 | 2 | 152 s | $0.09 |
| 4 (+ cheaper rejections) | 6 | A1 + A2, high | 11 | 11 | 0 | **105 s** | **$0.05** |

Run 1 → run 4: problems found 3 → 6 of 8, time −28%, cost −75%, tool calls −67%, model calls −45%.
Still missed: D (unattached volumes, a cost item) and E (one failed API call). The agent ranks outages first.

## Remaining risks

- Masking covers identifiers, not resource names. Names can reveal customers or projects. See `docs/connectors.md`.
- One run per version. Results vary between runs (run 3 vs run 4).
