# Development

## Setup

```bash
cd engine
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,providers,api]"
python -m pytest                 # 269 tests, fully offline
```

Core runtime needs only `numpy` and `PyYAML`. `requests` is needed for live
ingestion, `fastapi`/`uvicorn` for the API, `pytest`/`httpx` for the suite.

Dashboard:

```bash
cd web && npm install && npm run dev     # http://localhost:5173
```

## Try it without network access

```bash
cd engine
python scripts/generate_sample_data.py --out data/local
export RE__APP__OFFLINE=true
python -m research_engine.cli universe --refresh
python -m research_engine.cli ingest --symbols SYNCMP SYNGRW SYNVAL SYNDCL SYNIDX
python -m research_engine.cli daily --as-of "$(date +%F)"
python -m research_engine.cli serve      # then open the dashboard
```

The generated data is synthetic, labelled as such in every file, and uses
`SYN`-prefixed symbols. It exercises the pipeline; it says nothing about any
real asset.

## Test strategy

Tests are grouped by the property they protect, not by file layout.

| File | Protects |
| --- | --- |
| `test_core.py` | numeric honesty (`None`, not `0`), series invariants, look-ahead guards, secret redaction |
| `test_config.py` | precedence, validation, the trading gate, secrets from env only |
| `test_storage.py` | point-in-time reads, restatement handling, append-only history, survivorship retention |
| `test_ingestion.py` | provider parsing, retries, rate limits, failover, universe classification |
| `test_quality.py` | every synthetic corruption is detected; bias detectors fire |
| `test_features.py` | indicator causality, known-value maths, graceful degradation |
| `test_analysis.py` | scoring/risk/ensemble/probability/sell behaviour and recommendation gates |
| `test_backtest.py` | deliberate cheating attempts fail; costs bite; walk-forward separation |
| `test_learning.py` | outcome grading, bucketed performance, bounded weight updates, promotion policy |
| `test_pipeline.py` | prioritisation, discovery, alerts, memos, and a full daily run against a real database |
| `test_api_cli.py` | read-only API surface, no credential leakage, no false precision, CLI behaviour |
| `test_no_fabrication.py` | the system-wide invariant: nothing is ever invented |

Three rules for new tests:

1. **No network.** `FakeTransport` or `csv_local` only. A test that reaches the
   internet is a flaky test and an unreproducible result.
2. **Test the honesty property, not just the happy path.** For every feature,
   ask "what does this do when the data is missing or wrong?" and test that.
3. **Synthetic data stays in tests.** `conftest.make_prices` exists to exercise
   computations. It must never become a source the engine can read from in a
   production path.

## Adding things

**A data provider** — subclass `DataProvider`, declare capabilities and source
tier, return `ProviderResult` with `missing` populated, register in
`ingestion/factory.py::BUILDERS`, add a `providers.<name>` config block and put
it in the relevant chains. Test with `FakeTransport`.

**A scoring factor** — add a `score_*` function in `analysis/scoring.py` (return
`(score, detail)` with `None` when inputs are absent), wire it in
`analysis/pipeline.py::_factor_scores`, add a weight in `default.yaml`, and add
it to a view group in `ensemble.build_views`. Note that adding a factor changes
model behaviour: register a new model version.

**An alert** — add a rule method to `AlertRules` with its threshold in
`alerts.thresholds`, and call it from `evaluate_all`.

**A sell condition** — add a `SellCondition` in `sell.build_conditions` with a
metric the daily loop can actually evaluate, and make sure `_live_metrics`
supplies it.

## Code conventions

* Type hints throughout; `from __future__ import annotations` at the top.
* Return `None` for "unknown". Never `0`, never `NaN` as a sentinel outside
  numpy arrays, never a default that flatters the result.
* Comments explain *why*, especially where the honest choice is the harder one.
* No network I/O outside `ingestion/`. No I/O at all in `features/` or
  `analysis/`.
* Every new threshold goes in `default.yaml`, not in the code.

## Operations

```bash
research-engine doctor          # is the system fit to produce output?
research-engine providers       # what works, what needs a key
research-engine daily           # the loop; exit code 1 if any step failed
research-engine report --latest
```

Schedule the daily run after the market close of your primary venue plus the
delay your data providers need. Cron:

```cron
30 22 * * 1-5 cd /srv/research/engine && ./.venv/bin/research-engine daily >> /var/log/research.log 2>&1
```

The loop is idempotent for a given as-of date: rerunning it recomputes and
upserts rather than duplicating.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `INSUFFICIENT_DATA` for everything | no fundamentals ingested; check `research-engine providers` and `INGESTION_CONTACT_EMAIL` |
| Every score withheld | too much factor weight missing; check the data-quality reports for the affected assets |
| Regime is `unknown` | the benchmark symbol has no stored history; ingest it or change `analysis.benchmark_equity` |
| Nothing reaches BUY | expected on thin data — read "Why the system will not go further"; the gates are working |
| Provider retries for minutes | you are offline; set `RE__APP__OFFLINE=true` to fail fast |
