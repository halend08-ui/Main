# Backtesting

A backtest is a **simulation**, not evidence that a strategy works. This
document describes what the engine does to make its simulations less misleading,
and what it still cannot fix.

## Protocol

### Walk-forward, not a single split

```
|--------- train ---------|--embargo--|--- test ---|
                          |--------- train ---------|--embargo--|--- test ---|
```

`Backtester.walk_forward(strategy_factory)` calls the factory with each training
window; the returned strategy is evaluated only on the following test window.
Out-of-sample results are stitched into one curve, and per-fold CAGRs plus a
fold win rate are reported — a strategy that works in one fold and fails in four
is visible rather than averaged into a single number.

### The embargo

The gap between train and test must be at least the label horizon. A label
observed at time *T* is computed from returns *after* T, so without a gap the
tail of training overlaps the head of testing through the label.
`check_label_horizon_embargo` warns whenever `embargo_days < label_horizon_days`.

### Point-in-time universe

`universe_as_of(day)` returns only assets listed on that day, with each series
truncated at that day. Before a strategy is called, every series it will see is
asserted future-free (`PriceSeries.require_no_future`). A strategy physically
cannot read tomorrow's price, because it is never handed it.

### Next-bar execution

Signals are computed from the close of day *T-1* and filled on day *T*. The
engine records signal and execution dates and runs `shifted_signal_is_safe` over
them as a tripwire.

### Delistings

A position in an asset that stops trading is liquidated at its last price times
`delisting_recovery` (default 30%). Dropping the position — the common default —
is the mechanism of survivorship bias. If the historical universe contains no
delisted assets at all, the run emits a survivorship warning.

## Cost model

| Component | Model |
| --- | --- |
| Commission | basis points of notional |
| Spread | basis points, wider for crypto and for small/micro caps |
| Impact | `k · σ · √(Q/ADV)` — the square-root law |
| Borrow | annualised bps × holding days (shorts) |
| Executability | orders above `max_participation` of ADV are **rejected**, not filled |

Missing volume data makes execution *unverifiable*, so the order is rejected
rather than assumed free. Rejected orders are counted and surfaced in the result.

## Metrics

CAGR, volatility, Sharpe, Sortino, Calmar, max drawdown, VaR/ES, skew, excess
kurtosis, beta, alpha, tracking error, information ratio; trade-level win rate,
profit factor, average winner/loser, holding period, total costs, cost drag in
bps, and annual turnover.

### Benchmarks, always including cash

Every run reports the strategy against its benchmark **and against cash at the
risk-free rate**. `compare_to_benchmarks` states plainly when the strategy
trailed, including on a risk-adjusted basis.

### Deflation for search

`deflated_metrics(metrics, configurations_tried, parameters, observations)`
multiplies headline figures by a factor that falls with the number of
configurations tried and with observations per parameter. Reporting the best of
400 backtested variants at face value is the single most common way quantitative
research misleads its own authors.

## What we do *not* do

* **We do not optimise for backtested return.** There is no parameter search in
  the daily pipeline. Weights come from configuration and change only through
  the bounded, validated learning process in `MODELING.md`.
* **We do not report a backtest without its warnings.** Survivorship, embargo
  and rejected-order counts travel with the result.
* **We do not treat a backtest as validation of a recommendation.** Live
  prediction tracking (`learning/`) is the primary evidence; the backtest is a
  sanity check on strategy mechanics.

## Residual limitations

Stated plainly, because a reader deserves to know where the simulation is still
optimistic:

* **Survivorship depends on the data.** The engine keeps delisted assets it
  knows about, but free price sources rarely carry them. A universe built from
  Stooq alone will be survivorship-biased no matter what the engine does; the
  warning fires, and it should be believed.
* **Point-in-time fundamentals depend on filing dates.** SEC data carries them.
  A CSV extract without `filed_date` defaults to period end, which is
  *later*-conservative for annual data but not exact.
* **No intraday microstructure.** Fills are at the daily close plus modelled
  costs. Gap risk is measured but not simulated bar by bar.
* **Index membership history is not modelled**, so benchmark-relative results
  inherit the benchmark provider's own biases.
* **Borrow availability for shorts is assumed**, and shorting is off by default.

## Running one

```bash
research-engine backtest --name momentum --strategy momentum --start 2018-01-01
```

The result is persisted in the `backtests` table with the full configuration, so
it can be reproduced and compared later.
