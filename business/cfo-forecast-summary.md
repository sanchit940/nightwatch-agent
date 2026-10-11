# NightWatch — cfo.ai forecast summary

Source: `business/nightwatch-forecast-cfo-ai.xlsx`, exported from cfo.ai (Ari) on 2026-10-11 with the inputs in
`business/cfo-plan-inputs.md` (section 9). Forecast period: October 2026 to March 2028 (18 months). All prices and
customer numbers are planning assumptions; the model cost per incident ($0.06) is measured.

## Pricing in the model

| Plan | Price per environment / month | Revenue per customer | Gross profit per customer |
|---|---|---|---|
| Community (self-hosted) | $0 | $0 | $0 |
| Starter (bring your own key) | $5 | $5 | $2.95 |
| Team | $15 | $30 (2 environments) | $19.40 |
| Business | $39 | $156 (4 environments) | $119.60 |

Blended: $28.85 revenue and $20.37 gross profit per paying customer per month. Gross margin 71%.

## Base case (40 new paying customers per month, 4% monthly churn, $15,000 starting cash)

| Month | Paying customers | Revenue (MRR) | Operating profit | Cash |
|---|---|---|---|---|
| 1 (Oct 2026) | 40 | $1,154 | −$3,835 | $11,165 |
| 6 (Mar 2027) | 217 | $6,267 | −$224 | **$3,117 (lowest)** |
| 7 (Apr 2027) | 249 | $7,171 | **+$414 (break-even)** | $3,530 |
| 12 (Sep 2027) | 387 | $11,173 | +$3,240 | $14,308 |
| 18 (Mar 2028) | 520 | $15,013 | +$5,952 | $43,562 |

## Scenarios that cfo.ai ran

| Scenario | Change | Gross margin | Break-even | Lowest cash | Cash at month 18 |
|---|---|---|---|---|---|
| Base | as above | 71% | Apr 2027 (month 7) | $3,117 | $43,562 |
| Higher Team price | Team $19 instead of $15 | 73% | Mar 2027 (month 6) | $4,854 | $58,220 |
| Bring-your-own-key mix | 50% of Team and Business customers use their own key | 77% | Mar 2027 (month 6) | $4,331 | $53,150 |
| Acquisition slowdown (stress) | 30 new customers per month instead of 40 | — | Jun 2027 (month 9) | **−$933 (cash runs out Apr–Jul 2027)** | $18,197 |

## What this tells us

1. The low price works in the base case, but the cash margin is thin: the lowest point is $3,117 of $15,000.
2. A small slowdown (30 instead of 40 new customers per month) runs out of cash for four months. We need about
   $17,000–20,000 of starting cash, or a smaller founder stipend in the first six months.
3. More customers with their own key is good for us: margin rises from 71% to 77%, because we pay no model cost
   for them and the price stays the same.
4. Team at $19 instead of $15 brings break-even one month earlier and adds about $15,000 of cash by month 18.
   The price is still low for teams.
5. Our own quick check gave the same base-case numbers (lowest cash $3,117, month-18 cash $43,562).

## Gaps

- The export contains the Base case and the stress case. The Downside case (15 new customers per month) is not in
  the export. Our quick check shows that it does not break even within 18 months with $15,000 starting cash.
- Two cells are values, not formulas (cfo.ai note): the higher-price revenue and the BYOK share.

## Decisions for the builder

- Keep Starter at $5. Consider Team at $19.
- Plan for at least $17,000–20,000 of starting cash, or start the stipend after month 6.
- Push bring-your-own-key in onboarding: it improves margin and keeps the price low.
