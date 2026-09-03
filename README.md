# Autonomous Investment Research Engine

An automated **investment research laboratory** for stocks and cryptocurrencies:
it ingests market, fundamental, news and macro data, validates it, analyses it
across multiple horizons, scores opportunities, records every prediction it
makes, and grades itself against what actually happened.

> **This is decision-support software, not investment advice.** Predictions are
> uncertain, backtests are simulations, and recommendations can be wrong. The
> system never places trades: order execution is gated off in configuration and
> not implemented. Consider your own risk tolerance and circumstances, and treat
> every number here as an estimate with error bars.

## Two hard rules

1. **Never fabricate data.** If a provider is down or a metric cannot be
   computed, the system reports *"Insufficient reliable data"* and lowers its
   confidence. There is no silent default, no zero-fill, no invented price.
2. **Never use future information.** Every read is point-in-time: fundamentals
   are filtered by *filing date*, macro series by *release date*, prices by
   *session date*. Look-ahead protection lives in the storage layer, so analysis
   inherits it instead of re-implementing it.

## Repository layout

```
engine/                Python research engine (the system)
  research_engine/
    core/              types, numerics, logging, point-in-time clock, series
    config/            layered configuration (defaults -> file -> environment)
    storage/           SQLite schema + repositories (point-in-time reads)
    ingestion/         providers, rate limiting, caching, failover, universe
    quality/           data-quality grading, leakage and bias detection
    features/          technical, fundamental, valuation, crypto, macro, regime
    analysis/          scoring, risk, ensemble, probability, recommendation, memo
    backtest/          walk-forward engine, costs, metrics
    learning/          prediction tracking, calibration, model registry
    pipeline/          daily research loop, discovery, alerts, reports
    api/               read-only HTTP API for the dashboard
  tests/               offline test suite (no network required)
web/                   React + Vite dashboard
```

## Quick start

```bash
cd engine
pip install -e ".[dev]"          # numpy + PyYAML core; requests/fastapi optional
python -m pytest                 # full suite, fully offline
python -m research_engine.cli --help
```

Nothing runs against the network until you configure providers and supply keys
via environment variables. See `CONFIGURATION.md` and `DATA_SOURCES.md`.

## Documentation

| Document | Contents |
| --- | --- |
| `ARCHITECTURE.md` | layer map, data flow, design decisions and why |
| `CONFIGURATION.md` | every setting, precedence rules, secrets handling |
| `DATA_SOURCES.md` | providers, free vs paid tiers, coverage and limits |
| `DEVELOPMENT.md` | local setup, test strategy, adding a provider or factor |
| `BACKTESTING.md` | walk-forward protocol, bias controls, cost model |
| `MODELING.md` | scoring, probability, calibration, model versioning |
| `RISK_MANAGEMENT.md` | risk measures, position/portfolio limits, sell engine |
| `CHANGELOG.md` | what shipped in each phase |

## Try it in five minutes, with no API keys

The engine ships a generator for clearly-labelled **synthetic** data so the full
pipeline can be exercised offline. Every generated file carries a "not market
data" banner and every symbol is `SYN`-prefixed.

```bash
cd engine
pip install -e ".[dev,api]"
python scripts/generate_sample_data.py --out data/local

export RE__APP__OFFLINE=true          # fail fast instead of retrying the network
python -m research_engine.cli universe --refresh
python -m research_engine.cli ingest --symbols SYNCMP SYNGRW SYNVAL SYNDCL SYNIDX
python -m research_engine.cli daily --as-of "$(date +%F)"
python -m research_engine.cli analyze SYNCMP --memo
python -m research_engine.cli serve                    # read-only API on :8000
```

```bash
cd web && npm install && npm run dev                   # dashboard on :5173
```

## Running against real data

```bash
export INGESTION_CONTACT_EMAIL="you@example.com"   # SEC requires a contact UA
export FRED_API_KEY="…"                            # free, for macro series
research-engine doctor            # is the system fit to produce output?
research-engine universe --refresh
research-engine daily
```

`research-engine providers` shows which sources are live, which need a key, and
what each one can and cannot supply. See `DATA_SOURCES.md` for the coverage
gaps — the engine reports them as unavailable rather than estimating around them.

## What the output looks like

```
ASSET: SYNCMP

Recommendation: WATCH
Score: 74/100
Confidence: 34%
Time Horizon: 12 months
Current Price: $60.01

Estimated Fair Value:
  Bear: $82.76      Base: $178.85      Bull: $272.42

Expected Return:
  Bear: 38%         Base: 198%         Bull: 354%

Estimated probability of a positive return over 12 months: 71%
(model estimate, not a guarantee)

Risk: Elevated
...
SELL / EXIT IF:
  * revenue growth falls below 6% for two consecutive quarters
    (thesis assumes about 15%)
  * operating margin falls below 21.9% (5 points beneath the level the
    thesis relies on)
  ...

Why the system will not go further:
  * confidence of 34% is below the 35% minimum

Data Quality: Good
Model Version: scoring_v1
```

Note the last block: when a gate fails, the system says **which** gate and
stops, rather than producing a recommendation it cannot support.

## Status

| Area | State |
| --- | --- |
| Ingestion, quality, storage | complete, 6 providers wired |
| Features, analysis, risk, valuation | complete |
| Backtesting, learning, calibration | complete |
| Daily loop, discovery, alerts, reports | complete |
| CLI, read-only API, dashboard | complete |
| Tests | 300 tests, 83% line coverage, fully offline |

Known gaps are listed in `ARCHITECTURE.md` ("Known limitations") and
`DATA_SOURCES.md` ("Not wired in"). They are reported by the system as
unavailable data, never estimated.
