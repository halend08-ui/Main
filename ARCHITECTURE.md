# Architecture

## What this system is

An automated research laboratory. It ingests market, fundamental, news and macro
data; validates it; analyses it across horizons; scores opportunities; records
every prediction it makes; and grades itself against what actually happened.

It is **decision-support software**. It does not trade, and
`app.allow_trading` is validated to stay `false` — a configuration that enables
it is rejected at load time.

## Layer map

```
                 ┌──────────────────────────────────────────────┐
  DATA SOURCES   │ SEC EDGAR · Stooq · CoinGecko · FRED · RSS    │
                 │ local CSV extracts (offline / vendor imports) │
                 └───────────────────┬──────────────────────────┘
                                     │  ingestion/  (rate limits, retries,
                                     │              caching, failover)
                 ┌───────────────────▼──────────────────────────┐
  VALIDATION     │ quality/  checks · grading · bias detectors   │
                 └───────────────────┬──────────────────────────┘
                                     │  storage/  (point-in-time SQL)
                 ┌───────────────────▼──────────────────────────┐
  FEATURES       │ features/  technical · returns · fundamental  │
                 │            valuation · crypto · macro · regime│
                 └───────────────────┬──────────────────────────┘
                 ┌───────────────────▼──────────────────────────┐
  ANALYSIS       │ analysis/  scoring · risk · ensemble ·        │
                 │            probability · events · sentiment · │
                 │            anomaly · sell · comparison ·      │
                 │            recommendation                     │
                 └───────────────────┬──────────────────────────┘
                 ┌───────────────────▼──────────────────────────┐
  ORCHESTRATION  │ pipeline/  discovery · prioritisation ·       │
                 │            parallel scan · daily loop ·       │
                 │            alerts · reports                   │
                 └───────────────────┬──────────────────────────┘
                 ┌───────────────────▼──────────────────────────┐
  EVALUATION     │ learning/  evaluation · performance ·         │
                 │            calibration · registry · retrain   │
                 └───────────────────┬──────────────────────────┘
                 ┌───────────────────▼──────────────────────────┐
  INTERFACES     │ cli.py · api/ (read-only) · web/ (dashboard)  │
                 └──────────────────────────────────────────────┘
```

Dependencies point strictly downward. `features/` and `analysis/` perform no
I/O and import nothing from `pipeline/`, which is what makes them testable in
isolation and reusable identically in live runs, backtests and unit tests.

## The two invariants

### 1. Never fabricate

There is no code path that substitutes a plausible value for a missing one.

* Numeric helpers return `None` rather than `0` (`core/numeric.py`).
* Providers declare what they could **not** supply (`ProviderResult.missing`).
* Statistics refuse to compute on samples too small to support them.
* A composite score is *withheld* when too much factor weight is missing, rather
  than being computed from the remaining evidence and presented as complete.
* When every provider in a chain fails, the registry raises `DataUnavailable`.

Test: `tests/test_no_fabrication.py` asserts these properties directly.

### 2. Never use future information

Every read is point-in-time:

| Data | Point-in-time key | Enforced in |
| --- | --- | --- |
| Prices | session `date` | `PriceRepository.series(as_of=…)` |
| Fundamentals | `filed_date` | `FundamentalRepository.history(as_of=…)` |
| Macro | `release_date` | `MacroRepository.series(as_of=…)` |
| Events | `detected_at` | `EventRepository.for_asset(as_of=…)` |
| Backtest universe | `listed_date` / `delisted_date` | `Backtester.universe_as_of` |

`PriceSeries.require_no_future()` is a hard assertion, called by the backtest
engine before a strategy sees any data. `quality/bias.py` provides the
detectors: look-ahead, same-bar execution, survivorship, train/test leakage.

## Design decisions and why

**SQLite with portable SQL, not an ORM.** The workload is a single-writer
analytical pipeline where reproducibility matters more than concurrency. Raw SQL
keeps the point-in-time semantics visible at the call site instead of hiding
them behind lazy relationships. The SQL stays close to ANSI so a PostgreSQL
backend can be added by replacing `storage/db.py`.

**numpy-only numerical core, no pandas.** `PriceSeries` enforces its own
invariants (sorted, unique dates, positive closes) and makes `as_of` slicing a
first-class operation. Avoiding pandas removes a large version-coupling surface
from the code that produces research numbers.

**Rules, not opaque models, for classification.** Event classification and
sentiment use auditable rule sets and lexicons. A transformer would score better
on a benchmark and worse on the requirement that every judgement in a research
memo be explainable to the person reading it.

**Base rates before evidence.** Probability forecasting starts from the
unconditional frequency of a positive return and moves away from it by a bounded
amount. This makes the model's default behaviour "the market's historical base
rate", which is the correct prior and a far better failure mode than an
unanchored score-to-probability mapping.

**Permanent-loss risk is separate from volatility.** A volatile,
well-capitalised business and a stable, over-levered one are different risks.
Collapsing them into one number destroys the distinction that matters most, so
`analysis/risk.py` computes permanent-loss risk from solvency inputs only —
never from price action.

**Disagreement is surfaced, not averaged.** When the ensemble's views conflict,
the conflict is named in the output and the probability estimate is pulled
toward the base rate. An average of two incompatible views is not a better view.

**Learning is bounded and versioned.** The system never rewrites its own logic.
It may update factor weights (capped per step), recalibrate probabilities,
replace priors with measured base rates, and select between model versions —
each producing a new immutable version. See `MODELING.md`.

## Data flow through one day

1. `ingestion.service` refreshes prices, fundamentals and news for the active
   universe, writing a data-quality report per series.
2. `pipeline.daily` classifies the market regime and macro stance.
3. `pipeline.discovery` looks for assets the system is not already watching.
4. `pipeline.prioritization` ranks the universe and funnels it: cheap screen →
   standard analysis → deep research → high-conviction review.
5. `analysis.pipeline.analyze` runs per asset: quality → features → factor
   scores → ensemble → risk → valuation → probability → gates → recommendation.
   With `pipeline.parallel_workers > 1` this runs across worker processes.
6. `analysis.comparison` ranks every analysed asset against its peer group,
   picks the leaders per group, and compares those across groups on
   risk-adjusted expected return — so the shortlist is not simply whichever
   sector is currently in favour.
7. Results, predictions, alerts and memos are persisted.
8. Predictions whose horizon has elapsed are graded; performance is recomputed
   per bucket; a weight proposal is generated (never auto-applied).
9. The daily report is rendered, including the system's self-evaluation.

## Extension points

| To add | Implement | Register in |
| --- | --- | --- |
| A data provider | `ingestion.base.DataProvider` | `ingestion/factory.py` `BUILDERS` |
| A scoring factor | a `score_*` function | `analysis/scoring.py` + weights in `default.yaml` |
| An ensemble view | a `ModelView` producer | `analysis/ensemble.py` `build_views` |
| An alert | a rule method | `pipeline/alerts.py` `AlertRules` |
| A sell trigger | a `SellCondition` | `analysis/sell.py` `build_conditions` |
| A backtest strategy | a `Strategy` callable | pass to `Backtester.run` |

## Performance

Measured on a single container core, SQLite on local disk:

| Operation | Scale | Time |
| --- | --- | --- |
| Load prices + fundamentals | 500 assets, 300k bars, 25k facts | 8.6 s |
| Full daily loop | 500 assets analysed end to end | 44 s (88 ms/asset) |
| Parallel scan + ranking | 1,000 assets, 4 workers | 17 s (61 assets/s) |
| Database size | 500 assets x 600 sessions | 71 MB |

At 88 ms per asset, a 600-asset stage-2 scan takes about a minute and a
5,000-asset stage-1 screen is dominated by prioritisation, not analysis. The
funnel — not concurrency — is what makes a large universe affordable.

**On parallelism.** Two approaches were implemented and measured.

*Threads made it worse.* 120 assets took 9.5 s sequentially and 23.0 s across
four threads — GIL contention on numpy work plus SQLite lock contention. Not
shipped.

*Processes scale close to linearly.* Each worker gets its own interpreter and
its own database handle, so neither bottleneck applies. Measured over 1,000
assets on 4 cores:

| Workers | Time | Assets/s | Speedup |
| --- | --- | --- | --- |
| 1 | 63.3 s | 15.8 | 1.00x |
| 2 | 32.6 s | 30.7 | 1.94x |
| 4 | 17.1 s | 58.6 | 3.70x |

`pipeline/parallel.py` ships this. The pool is skipped below ~60 assets, where
spawning interpreters costs more than it saves. Workers are stateless: they
receive a symbol list and an as-of date, and return flat records — never live
objects holding a connection. A worker that dies loses its own slice and is
recorded; the other slices complete.

Indexes are sized for the access patterns that matter: `(asset_id, date DESC)`
on prices, `(asset_id, filed_date)` for point-in-time fundamental reads, and
`(as_of DESC, total_score DESC)` for the daily ranking.

## Known limitations

These are design boundaries, not bugs; each is stated where it matters in the
output as well as here.

* **No intraday data.** The engine is end-of-day. Nothing here is suitable for
  short-horizon trading.
* **No options, futures or FX analytics.**
* **Peer groups are configuration, not inference.** Sector labels come from the
  universe provider; the engine does not cluster businesses by economics.
* **On-chain crypto metrics require a provider that supplies them.** Without
  one, TVL/active-address analysis is reported as unavailable — not estimated.
* **Management quality, customer concentration and contract structure are not
  ingested by any configured provider**, so the research agent lists them as
  questions it cannot answer.
* **Exchange holiday calendars are weekday-based** unless holidays are supplied
  in configuration; the coverage check will flag the resulting gaps.
* **Short interest and institutional ownership** have schema and repository
  support but no free provider is wired in.
