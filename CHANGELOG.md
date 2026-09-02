# Changelog

All notable changes to this project. Phases refer to the staged build plan in
`ARCHITECTURE.md`.

## [Unreleased]

### Phase 1 - Foundation (repository, configuration, storage, ingestion)

**Added**

- Monorepo layout: `engine/` (Python research engine) and `web/` (dashboard).
  The pre-existing Vite/React app moved to `web/` unchanged.
- `research_engine.core`: ordered domain enums (data quality, source tier,
  opportunity tier, risk level), provenance and evidence value objects,
  `None`-returning numeric helpers (no zero-fill, no false precision),
  JSON logging with mandatory secret redaction, a point-in-time clock
  (`as_of_context`) and trading calendars, and the numpy-backed `PriceSeries`
  with `as_of()` slicing and an explicit look-ahead assertion.
- `research_engine.config`: layered settings (defaults -> YAML file ->
  `RE__SECTION__KEY` environment overrides), validation that rejects
  inconsistent thresholds and degenerate scoring weights, and a
  `SecretResolver` that reads credentials only from the environment.
  `app.allow_trading` is validated to stay false.
- `research_engine.storage`: 31-table SQLite schema with point-in-time columns
  (`filed_date`, `release_date`, `retrieved_at`) and indexes for
  universe-scale scans; repositories for assets, prices, fundamentals, news,
  events, macro, crypto metrics, ownership, scores, recommendations, signals,
  predictions, model versions, calibration, backtests, portfolios, alerts,
  the research queue and provider health.
- `research_engine.ingestion`: provider base class with token-bucket rate
  limiting, exponential backoff with jitter, response caching (stale entries
  served only after failure, and labelled), injectable HTTP transport, and a
  capability registry with configurable failover chains.
- Providers: SEC EDGAR (primary-source XBRL fundamentals with filing dates),
  Stooq (free EOD prices), CoinGecko (crypto market data and history), FRED
  (macro with vintage support), generic RSS news, and a local CSV provider for
  offline/air-gapped operation.
- Universe builder with configurable size/liquidity screens and a separate,
  rule-based crypto quality classification (institutional / established /
  emerging / speculative / excluded), with reasons attached.
- Offline test suite (68 tests) covering configuration precedence,
  point-in-time restatement handling, provider parsing, retry/rate-limit
  behaviour, failover, and secret redaction.

**Decisions**

- SQLite with portable SQL rather than an ORM: the workload is single-writer
  analytical, and reproducibility matters more than concurrency.
- numpy-only numerical core (no pandas dependency) so `PriceSeries` can enforce
  its own invariants and avoid version coupling.
- Stooq closes are split- but not dividend-adjusted; the provider reports
  `adj_close` as *missing* rather than passing raw closes off as total-return
  adjusted.
