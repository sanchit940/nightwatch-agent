# NightWatch — business plan inputs for cfo.ai (v2, low price)

Status: draft for the builder, 2026-10-11. **Measured** values come from NightWatch runs. **Assumption** values are
planning inputs: the builder confirms or changes them in cfo.ai. Section 9 is the prompt to paste into Ari.

## 1. Business model in one paragraph

NightWatch is an AI on-call agent for small engineering teams that run Docker, Kubernetes, Linux servers or AWS.
The agent runs in the customer's environment, so their credentials stay with them. The customer adds their own
knowledge (runbooks, postmortems, past incidents, service map); NightWatch retrieves it during an investigation (RAG).
The customer brings their own model key (BYOK), or uses our key and pays per incident. Prices stay very low because
an incident costs us about $0.06 in model use, and watch mode costs nothing while systems are healthy.

## 2. Why a low price works (measured)

| Fact | Value | Source |
|---|---|---|
| Model cost per incident, `claude-haiku` | about $0.06 (range $0.03–0.13) | Measured: 70+ runs, LiteLLM `/key/info` spend |
| Model cost per incident, Opus 4.6 with caching | about $0.33 | Measured |
| Model cost while all systems are healthy | $0 | Measured: watch mode uses no model |
| Model cost with BYOK | $0 for us | The customer pays their provider |
| Investigation time | 35–120 s | Measured |

## 3. Plans (assumption)

Price per **environment** (one cluster, one Docker host group, or one cloud account), not per person. Teams do not pay
more when more people join on-call.

| Plan | Price | For | Includes |
|---|---|---|---|
| **Community** | $0, self-hosted | Students, open source, one-person projects | 1 environment, BYOK only, investigations, Slack approvals, community support |
| **Starter** | $5 per environment / month | Small teams | BYOK only, approved fixes, knowledge base up to 100 MB, 30-day audit log, e-mail support |
| **Team** | $15 per environment / month | Growing teams | BYOK **or** our key with 50 incidents per environment included, knowledge base 2 GB, escalation, 1-year audit log |
| **Business** | $39 per environment / month | Companies with compliance needs | 100 incidents per environment included, SSO, audit export, priority support, custom policy rules |
| **Pay as you go** (our key) | $0.12 per incident; $0.60 per deep investigation (large model) | Any paid plan | Hard monthly spend cap, default $20 |
| **Partner (MSP / agency)** | 30% off at 20+ environments | Service providers that run on-call for clients | Multi-client view |
| **Annual billing** | 2 months free | All paid plans | — |

Optional: purchasing-power pricing (for example a lower price in INR for India).

## 4. What else we add (product ideas that support the plan)

1. **Open-source core.** The agent, connectors and policy service are open source and self-hosted for free. This builds trust (no data leaves) and brings users at no ad cost. We charge for the hosted control plane: Slack app, approvals, audit history, knowledge base, team features.
2. **Knowledge base per customer (RAG), not model training.** Runbooks, postmortems, past incidents and the service map are indexed per customer and cited in each plan. The data stays in the customer's tenant and can be deleted at any time. We never fine-tune a model on customer data.
3. **Learning from approvals.** Each approve, reject and instruction is a labelled example. After a human reviews it, a repeated fix becomes a runbook entry. The agent gets better for that customer every week.
4. **Bring your own key, any Claude-compatible endpoint.** Direct Anthropic key, AWS Bedrock, or a company LLM gateway (LiteLLM). Already works in the code.
5. **Cost control on our key.** Small model by default; a large model only for a deep investigation that the customer starts and pays for; a hard monthly cap.
6. **Free 14-day shadow trial.** NightWatch only investigates and posts in Slack, with no changes. At the end it sends a report: time to root cause compared with the team.
7. **Postmortem drafts.** After each incident, a short postmortem in ASD-STE100, ready for the team to edit. This is a clear time saving that customers see.
8. **Connector and runbook packs.** Community packs (for example "Postgres", "Nginx", "Kafka"). Later a marketplace with revenue share.
9. **Partner channel.** Managed service providers sell NightWatch to their clients.

## 5. Unit costs per environment per month (assumption unless marked)

| Cost item | Value |
|---|---|
| Hosted control plane (approvals, audit, Slack app) | $0.80 |
| Knowledge base storage and embeddings (RAG) | $0.40 (one-off ingest of a large runbook set: under $1) |
| Observability (traces) | $0.20 |
| Model use on our key | $0.06 per incident (**measured**) |
| Support per customer | Starter $0.40, Team $1.50, Business $5.00 |
| Payment processing | 5% of revenue |
| Acquisition cost per paying customer | $15 (open source and community; little paid ads) |

## 6. Company costs per month (assumption, lean)

| Item | Monthly |
|---|---|
| Two founders, small stipend | $3,000 |
| Cloud | $300 |
| Tools (GitHub, Entire, neatlogs, domain) | $150 |
| Model use for development and evals | $100 |
| Marketing and community | $300 |
| Other | $200 |
| **Total fixed** | **$4,050** |

Starting cash (assumption): $15,000.

## 7. Customer assumptions (assumption)

- Plan mix of paying customers: Starter 55%, Team 35%, Business 10%.
- Environments per customer: Starter 1, Team 2, Business 4.
- Incidents per customer per month on our key: Team 80, Business 300 (Starter is BYOK only).
- New paying customers per month: Downside 15, Base 40, Upside 100.
- Monthly churn: Downside 6%, Base 4%, Upside 3%.
- Free-to-paid conversion: 3–5% of Community users.

## 8. Our quick check (estimate, for comparison with Ari)

A simple month-by-month model (a short Python script, not cfo.ai) with the inputs above:

| Plan | Revenue per customer | Variable cost | Gross margin |
|---|---|---|---|
| Starter | $5.00 | $2.05 | 59% |
| Team | $30.00 | $10.60 | 65% |
| Business | $156.00 | $36.40 | 77% |

- Blended revenue per paying customer: about $29 per month. Contribution: about $20.
- Break-even: about 200 paying customers.

| Scenario | Month 12 | Month 18 | Break-even | Lowest cash |
|---|---|---|---|---|
| Downside | 131 customers, $3.8k MRR | 168 customers, $4.8k MRR | not within 18 months | −$24k (needs more cash or lower costs) |
| Base | 387 customers, $11.2k MRR | 520 customers, $15.0k MRR | month 7 | $3.1k |
| Upside | 1,021 customers, $29.4k MRR | 1,407 customers, $40.6k MRR | month 3 | $10.0k |

Main risk: at these prices the company needs volume. The downside case runs out of cash. Levers: more starting cash,
founders' stipend later, or a higher Team price.

## 9. Prompt to paste into Ari (cfo.ai)

Copy the text in the box into Ari as one message. Then change any assumption Ari asks about.

```
I am planning a low-price SaaS company. Please build an 18-month financial model with Downside, Base and Upside scenarios. Show pricing, costs, revenue, gross margin, cash and runway, and the month of break-even.

Product: NightWatch, an AI on-call agent for small engineering teams (Docker, Kubernetes, Linux, AWS). It investigates incidents, asks in Slack before any change, and verifies the fix. Customers add their own runbooks and past incidents (knowledge base). They bring their own AI model key, or use ours and pay per incident.

Pricing per environment per month: Community $0 (self-hosted); Starter $5 (bring your own key only); Team $15 (includes 50 incidents per environment on our key); Business $39 (includes 100 incidents per environment). Extra incidents on our key: $0.12 each. Annual billing: 2 months free.

Customer mix: Starter 55%, Team 35%, Business 10%. Environments per customer: Starter 1, Team 2, Business 4. Incidents per customer per month on our key: Team 80, Business 300.

Variable costs: our AI model cost is $0.06 per incident (measured). Per environment per month: hosting $0.80, knowledge base storage $0.40, observability $0.20. Support per customer per month: Starter $0.40, Team $1.50, Business $5. Payment fees 5% of revenue. Acquisition cost $15 per new paying customer.

Fixed costs per month: two founders $3,000 (stipend), cloud $300, tools $150, AI for development $100, marketing $300, other $200.

New paying customers per month: Downside 15, Base 40, Upside 100. Monthly churn: 6%, 4%, 3%. Starting cash: $15,000.

Questions:
1. Runway and break-even month in each scenario.
2. Gross margin per plan and blended.
3. How many paying customers do we need to break even?
4. In the Downside case, how much starting cash do we need to survive 18 months?
5. What happens if the Team price is $19 instead of $15?
6. What happens if 50% of Team and Business customers bring their own key (so we have no model cost for them, but they also use no paid extra incidents)?
```
