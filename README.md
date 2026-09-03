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
    analysis/          scoring, risk, ensemble, probability, comparison,
                       recommendation, memo, agent
    backtest/          walk-forward engine, costs, metrics
    learning/          prediction tracking, calibration, model registry
    pipeline/          daily loop, discovery, parallel scan, alerts, reports
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

## Scanning thousands of assets and comparing them

```bash
research-engine scan --workers 8 --per-group 1 --top 25
research-engine compare NVDA AMD
```

`scan` analyses the universe across worker processes, then ranks it
**cross-sectionally**: each asset is percentile-ranked against its own sector
peers, the leaders of each peer group are taken, and those winners are compared
across groups on risk-adjusted expected return. A global top-N on raw score
reliably returns whichever sector is currently in favour; this does not.

Measured on 4 cores: 1,000 assets in 17 s (3.7x the single-process time).

```
BEST OF BREED (top 1 within each peer group)
group                    symbol      score  pctile  base ret  risk-adj
Financials               S0292          68    100%       47%      1.56
Energy                   S0773          68    100%       50%      1.77
Technology               S0700          67    100%       44%      1.44

FINAL RANKING (compared across groups on risk-adjusted expected return)
 1. S0773  Energy      risk-adj 1.77  base 50%  WATCH
 2. S0292  Financials  risk-adj 1.56  base 47%  WATCH

WHERE THE TWO VIEWS DISAGREE
  * S0700 ranks 3 on the peer-relative view but 15 on raw score: it is top of
    its Technology peer group without being a high scorer in absolute terms
```

`compare` puts two assets side by side factor by factor and says which wins and
why, flagging when the comparison itself is weak (different sectors, different
data quality).

## Running against real data

A live configuration and a guided first run are included.

```bash
cd engine
cp .env.example .env          # fill in your contact address and FRED key
set -a && . ./.env && set +a

cp scripts/watchlist.example.txt watchlist.txt
$EDITOR watchlist.txt         # 10-30 tickers you actually want examined

./scripts/first_run.sh watchlist.txt
```

The script checks the environment *before* touching the network, so a missing
key fails in two seconds with an explanation rather than after twenty minutes of
retries. It then builds the universe, ingests your watchlist plus SPY, pulls
macro, runs a health check and produces the first report.

Two keys, both free:

| Variable | Needed for | Where |
| --- | --- | --- |
| `INGESTION_CONTACT_EMAIL` | SEC fundamentals — their policy requires a contact address | any working address of yours |
| `FRED_API_KEY` | macro readings and sector tilts | <https://fredaccount.stlouisfed.org/apikeys> |

Without the FRED key everything else still works; macro simply reports
"unknown" rather than being estimated.

`config/live.yaml` carries the live settings: real rate limits with the
reasoning behind each number, stricter score thresholds than the demo defaults,
and the SEC filings RSS feed. Add your own news feeds there — set each feed's
`tier` honestly, because it directly weights the evidence.

`research-engine providers` shows which sources are live, which need a key, and
what each one can and cannot supply. See `DATA_SOURCES.md` for the coverage
gaps — the engine reports them as unavailable rather than estimating around them.

**Start small.** At free-tier rate limits (Stooq 30/min) a few thousand names is
a multi-hour pull. Ingest 10–30 first and read one report closely; widening the
universe is one command once you trust what it says.

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
| Parallel scan + cross-sectional ranking | complete, 3.7x on 4 cores |
| CLI, read-only API, dashboard | complete |
| Tests | 337 tests, fully offline |

Known gaps are listed in `ARCHITECTURE.md` ("Known limitations") and
`DATA_SOURCES.md` ("Not wired in"). They are reported by the system as
unavailable data, never estimated.
