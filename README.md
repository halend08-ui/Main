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
