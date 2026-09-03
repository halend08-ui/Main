# Modelling

## Scoring

A 0–100 composite over configured factors.

* **Missing factors are excluded, not zeroed.** Remaining weights renormalise
  and the covered share is reported.
* **A coverage haircut** subtracts points proportional to the missing evidence,
  so a score built on 65% of the factors does not compete on equal terms with
  one built on all of them.
* **Above `max_missing_factor_ratio`, the score is withheld entirely.**
* **Sub-scores are non-linear where reality is.** Cheapness helps until it
  signals distress (a 40% FCF yield scores *worse* than 12%); growth credit
  saturates above ~45% CAGR; RSI is best mid-range, not extreme.
* **Tier is capped by data quality**: a top tier requires at least
  `scoring.min_quality_for_buy`.

Equity factors: fundamental quality, growth, valuation, momentum, technical
structure, financial health, competitive advantage, capital allocation, macro,
event risk, sentiment, liquidity, downside risk.
Crypto factors: tokenomics, network activity, liquidity, momentum, valuation,
developer activity, concentration risk, event risk, sentiment, downside risk.

## Ensemble

Eight independent views — fundamental, valuation, momentum, technical, risk,
event, sentiment, macro (plus a crypto-fundamental view) — each computed from a
different input family, so agreement carries information.

* No single view may exceed 35% of the vote.
* Sentiment carries the lowest weight by design.
* Disagreements are **named** ("cheap on the numbers but the business is
  deteriorating: a possible value trap") and shown in every report.
* Low agreement pulls the probability estimate toward the base rate.

## Probability

Never "this will rise 40%". Always "estimated probability of a positive return
over 12 months: X%", labelled a model prediction.

Construction, with every step itemised in the output:

1. **Base rate** — the unconditional frequency of a positive return for that
   asset class and horizon. Replaced by *measured* rates once ≥100 outcomes
   exist (`learning.performance.measured_base_rates`).
2. **Evidence tilt** — bounded at ±0.22 from the composite score.
3. **Model disagreement** — shrinks the tilt toward the anchor.
4. **Regime and macro** — bounded at ±0.10 each.
5. **Risk penalty** — permanent-loss risk reduces the odds of a good outcome.
6. **Total deviation from the base rate is capped at ±0.34.**
7. **Data-quality shrinkage** toward 50%: poor data pulls hard.
8. **Calibration** — the learned mapping from stated to observed frequency.

Scenario probabilities (bear/base/bull) are derived from P(up) and sum to 1, and
a probability-weighted expected return is reported alongside the three cases.

## Confidence

Confidence is about the *estimate*, not the direction. It rises with conviction,
data quality, model agreement and sample size; it falls with poor data, thin
history, disagreement and measured overconfidence. Unproven calibration itself
costs 10% — humility is the default.

`learning.performance.confidence_is_informative` checks explicitly whether
higher stated confidence has actually produced higher accuracy. If it has not,
the report says so in those words.

## Calibration

`Calibrator.fit(predictions, outcomes)` builds equal-count reliability bins,
merges bins below the sample floor, and enforces monotonicity by pool-adjacent-
violators so a higher raw probability can never map to a lower calibrated one.

Reported: expected calibration error, an overconfidence penalty (only
*overshooting* is penalised), the Brier score, and — importantly — the **Brier
skill score against the base rate**. A model that cannot beat quoting the
historical frequency has added nothing, and the skill score says so with a
negative number.

## Model versioning

Every model version records parameters, features, training window, data sources,
a parameter fingerprint and a code fingerprint.

* Versions are immutable once results are attributed to them.
* Promotion **retires** the previous version rather than deleting it, so
  "which model produced this 2024 recommendation?" always has an answer.
* `reproducibility_report(version, current_code_hash)` reports when the
  implementing code has changed since a version ran — its historical output is
  then a record, not something that can be re-derived.

## Learning (bounded, versioned, validated)

The system never rewrites its own logic. Learning is limited to four channels:

1. **Weight updates.** `factor_effectiveness` measures, per factor, the spread
   in realised excess return between the top and bottom tertile of that
   factor's contribution — labelled association, never causation. Proposals are
   capped (`max_weight_change_per_update`, default 25%), refused without
   `min_samples_for_retrain` observations, and refused if any single weight
   would move more than the cap.
2. **Calibration**, as above.
3. **Measured base rates** replacing priors.
4. **Model selection.** `should_promote` requires: enough out-of-sample
   predictions, no material calibration regression (a better-scoring but worse-
   calibrated model is *not* better), and improvement beyond a required margin.
   Ties go to the incumbent.

A proposal is never applied silently: it is reported in the daily
self-evaluation and requires explicit promotion of a new model version.

## Prediction evaluation

Each prediction is graded on return, excess return over the relevant benchmark,
maximum drawdown during the holding period, realised volatility, and a **thesis
outcome**:

| Outcome | Meaning |
| --- | --- |
| `succeeded` | the thesis played out |
| `partial` | positive but short of target, or right with a worse path than anticipated |
| `luck` | correct direction with **no excess return** — the market did the work |
| `failed` | the thesis was wrong |
| `open` | no measurable outcome (e.g. the asset stopped trading) |

HOLD and WATCH make no directional claim and are not scored as hits or misses;
counting them would inflate or deflate the record depending on the market.

Performance is bucketed by asset class, sector, regime, horizon, recommendation,
stated confidence and data quality. A bucket below the sample floor reports
"insufficient" rather than a number.

## Guards against the standard failure modes

| Failure | Guard |
| --- | --- |
| Overfitting | no parameter search in production; capped weight updates; result deflation by configurations tried |
| Look-ahead | point-in-time repositories; `require_no_future`; next-bar execution |
| Survivorship | delisted assets retained and liquidated with a haircut; explicit warning |
| Data leakage | train/test embargo validated against the label horizon |
| Confirmation bias | a bear case is constructed for every recommendation, from the actual negative evidence, and shown before the bull case |
| Narrative bias | sentiment weighted lowest; hype detection discounts promotional language |
| Recency bias | multi-horizon momentum with short-term reversal control; base-rate anchoring |
| Overconfidence | calibration, confidence shrinkage on poor data, explicit informativeness check |
| False precision | `round_sig`, presentation rounding at the API boundary, `n/a` for missing values |
| Correlation ≠ causation | factor effectiveness explicitly labelled association; macro described as probability-adjusting, not predictive |
