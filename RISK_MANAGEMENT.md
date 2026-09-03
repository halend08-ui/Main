# Risk management

## Risk is a first-class output

Not a footnote on a return forecast. The engine reports five distinct kinds of
risk and refuses to collapse them into one number.

### 1. Volatility risk
Annualised realised volatility, rolling volatility, and volatility-regime
breaks. Thresholds are wider for crypto (an equity at 70% annualised volatility
is extreme; a token at 70% is ordinary).

### 2. Tail risk
Historical VaR (95%), expected shortfall (97.5%), skew, excess kurtosis, and
**gap risk** — the share of sessions with an overnight gap beyond 10%. Gap risk
is measured separately because it is the risk a stop-loss does not protect you
from.

### 3. Permanent-loss risk
The probability that capital does not come back. Computed from solvency and
structural inputs **only** — interest coverage, net debt to EBITDA, cash runway,
Altman Z, dilution rate, free-cash-flow sign, market-cap tier, crypto structural
factors — and never from price action, which reflects sentiment as much as
solvency.

This is the number that stops a "cheap" asset from being bought. It dominates
the reported risk level: an asset that can go to zero is high risk regardless of
how calm its chart looks. When solvency inputs are unavailable, risk is *raised*
to elevated and the gap is stated — unknown is not treated as safe.

### 4. Liquidity risk
Average dollar volume, turnover, venue breadth, and **days to exit** at the
configured participation cap. A position needing 50 trading days to unwind is
reported as such.

### 5. Event risk
Dated, known events — earnings, token unlocks, regulatory decisions — with
expected impact, duration and whether they change the thesis.

## Crypto-specific risk

Eight factors, each scored 0–1 with reasons, and with **coverage reported**:
tokenomics, liquidity, centralisation, smart contract, regulatory, narrative,
unlock and volatility. Unknown factors are listed by name, and the overall score
is computed only over what is actually known.

Unlock overhang is measured in **days of trading volume**, not just percentage
of supply — a 5% unlock into thin liquidity is a very different event from the
same unlock into deep liquidity. When no unlock schedule is available, the
result says "unknown, not zero" in those words.

## Position and portfolio limits

Configurable in `risk`:

| Limit | Default | Rationale |
| --- | --- | --- |
| `max_position_weight` | 12% | single-name blow-up survivable |
| `max_sector_weight` | 30% | correlated-cluster control |
| `max_crypto_weight` | 20% | asset-class volatility budget |
| `concentration_warning_hhi` | 0.18 | ≈ 5.5 effective positions |
| `liquidity_participation_cap` | 5% of ADV | exit feasibility |
| `stop_atr_multiple` | 3× ATR | volatility-adjusted trend break |

Breaches are reported as explicit, quotable sentences ("crypto exposure is 24.0%
(limit 20%)"), not as a silent rebalance. Portfolio volatility is computed from
the covariance of aligned returns and reports **how much of the portfolio could
actually be measured** — a portfolio volatility computed from 40% of the
holdings and presented as the whole is a quiet lie.

## The sell engine

The engine never sells because the price went down. A price fall is a fact about
the market, not about the business, and on its own it is as likely to be an
opportunity as a warning — it triggers a **thesis review**, not a sale.

Five sell triggers:

1. **Thesis deterioration** — growth, margins or cash flow fall below the levels
   the thesis assumed.
2. **Valuation** — price exceeds base-case fair value, or fair value is revised
   below today's bear case.
3. **Risk increase** — interest coverage below 2×, permanent-loss risk above
   0.6, an unlock above 10% of supply without matching demand.
4. **Opportunity cost** — a materially better risk-adjusted opportunity, subject
   to a switching hurdle that covers turnover cost and the risk of being wrong
   again.
5. **Structural breakdown** — a volatility-adjusted trend break, or liquidity
   falling below the level at which the position can be exited.

Every condition is machine-evaluable (`metric`, `operator`, `threshold`), so the
daily loop tests them automatically rather than relying on someone rereading
prose. Conditions whose metric is unavailable are reported as *unevaluable* —
unknown is never treated as "not breached".

## Thesis invalidation

Every recommendation carries a "what would make this wrong" list written about
**assumptions**, not price:

> Revenue growth settles materially below the 15% the valuation assumes.
> Operating margin fails to hold near 31%, indicating the cost structure is not
> defensible.
> Gross margin compresses for two or more consecutive years, contradicting the
> pricing-power claim.

A thesis that can only be invalidated by the price falling is not a thesis.

## Gates before any BUY

| Gate | Requirement |
| --- | --- |
| Data quality | at least `scoring.min_quality_for_buy` (default: good) |
| Factor coverage | ≥60% of factor weight had data |
| Confidence | ≥ `analysis.min_confidence_to_recommend` |
| Permanent-loss risk | < 0.6 |
| Bear case | survivable (better than −60%) |
| Model agreement | not in open conflict at high conviction |

A failed gate does not produce a fabricated HOLD. It produces WATCH plus the
specific reason, printed under "Why the system will not go further".

## Human oversight

* The system is decision support. It does not place orders, and the
  configuration that would enable execution is rejected at load time.
* Every report and memo carries the uncertainty disclaimer.
* Recommendations are versioned and attributable to an exact model.
* The daily self-evaluation reports what the system got wrong, and when its
  confidence has not tracked its accuracy it says so.

Position sizing, tax, jurisdiction and personal circumstances are outside the
system's knowledge. It cannot tell you what fraction of your capital to risk.
