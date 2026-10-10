# NightWatch — inputs for the cfo.ai business plan

Status: draft. **Measured** values come from NightWatch runs (see `evals/results/`).
**Assumption** values are planning inputs. The builder must confirm or change them in cfo.ai.

## 1. Product and customer

- Product: NightWatch, an AI on-call agent for Docker and Kubernetes. It investigates incidents with evidence and changes infrastructure only after a bound approval.
- Customer (assumption): engineering teams of 5–50 people who run Docker/Kubernetes and have no 24/7 SRE team.
- Delivery (assumption): the agent and connectors run in the customer's environment (their credentials never leave). A hosted control plane stores audit logs, approvals and reports.

## 2. Unit costs

| Cost item | Value | Source |
|---|---|---|
| LLM cost per incident, `claude-haiku` | $0.03–$0.10, average about $0.05 | **Measured**: 12 diagnosis runs, LiteLLM `/key/info` spend |
| LLM cost per incident, `claude-opus-4.6` with prompt caching | about $0.33 | **Measured**: dns-break run |
| LLM cost per incident, Opus 4.6 without caching | about $0.69 | **Measured**: redis-down run (caching cut 53%) |
| LLM cost while all systems are healthy | $0 | **Measured**: watch mode uses deterministic checks, no model calls |
| Investigation time | 35–120 s per incident | **Measured** |
| Incidents per customer per month | 60 | Assumption |
| LLM cost per customer per month | $3 (Haiku) to $20 (Opus) | 60 × measured cost |
| Hosting per customer per month (control plane, audit storage) | $8 | Assumption |
| Observability (neatlogs) per customer per month | $5 | Assumption |
| Support and onboarding per customer per month | $20 | Assumption |

## 3. Pricing (assumption)

| Plan | Price | Includes |
|---|---|---|
| Free | $0 | 1 environment, read-only investigations, 20 incidents/month |
| Team | $199 / month | 3 environments, approvals and remediation, 200 incidents/month, Slack approvals |
| Business | $799 / month | Unlimited environments, standing approval rules (night mode), SSO, audit export, 1 000 incidents/month |
| Overage | $0.50 / incident | Above the plan quota |

Gross margin check (Team plan, Opus model): cost ≈ $20 LLM + $8 hosting + $5 observability + $20 support = $53 → margin ≈ 73%. With Haiku: ≈ $36 → margin ≈ 82%.

## 4. Revenue scenarios (assumption, months 1–18)

| Scenario | New paying customers per month | Mix Team / Business | Monthly churn |
|---|---|---|---|
| Downside | 2 | 90% / 10% | 5% |
| Base | 5 | 80% / 20% | 3% |
| Upside | 10 | 70% / 30% | 2% |

Free-to-paid conversion (assumption): 5% of free workspaces per month.

## 5. Company costs (assumption)

| Item | Monthly |
|---|---|
| Founders (2), reduced salary | $6 000 |
| Cloud and tools (GitHub, Entire, neatlogs, CI, domains) | $400 |
| LLM budget for development and evals | $300 |
| Marketing and community | $500 |
| Total fixed | about $7 200 |

## 6. Cash and runway (assumption)

- Starting cash: builder to enter (for example savings or pre-seed).
- Ask cfo.ai: "With these inputs, what is the runway in the base and downside cases, and in which month does the company reach break-even?"

## 7. Questions to ask Ari (cfo.ai)

1. Build a 18-month model with the three scenarios above.
2. What is the break-even customer count for the Team plan only?
3. What happens to gross margin if every incident uses Opus instead of Haiku?
4. What is the runway with $50 000 starting cash in the downside case?
