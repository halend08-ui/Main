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

### Phase 2 - Data quality and bias controls

**Added**

- `research_engine.quality.checks`: price-series validation (impossible values,
  OHLC inconsistency, calendar gaps, staleness, frozen feeds, zero-volume runs,
  suspected unadjusted splits, extreme moves), fundamental validation (missing
  core metrics, impossible signs, balance-sheet identity, fiscal-year gaps,
  restatement detection) and news validation (syndication, low-tier sources).
- `research_engine.quality.grading`: severity-weighted scoring with per-code
  penalty caps, grade assignment, contagious FATAL handling when scopes are
  combined, and the confidence multiplier that quality imposes on every
  downstream recommendation. Clean-but-thin samples are capped at FAIR.
- `research_engine.quality.bias`: point-in-time assertions, feature/label
  look-ahead detection, same-bar execution detection, survivorship checks,
  train/test embargo validation against the label horizon, and overfitting
  pressure diagnostics that deflate results from heavily searched parameter
  spaces.

### Phase 3 - Analytical feature engines

**Added**

- `features.technical`: causal indicator library (SMA, EMA, Wilder smoothing,
  RSI, MACD, ATR, Bollinger, ADX/DI, OBV, MFI, Donchian, drawdown, rolling
  volatility, relative strength). Warm-up periods stay NaN; a regression test
  proves indicator values do not change when future bars are appended.
- `features.returns`: CAGR, volatility, downside deviation, Sharpe, Sortino,
  max drawdown, drawdown episodes, historical VaR, expected shortfall, skew,
  excess kurtosis, beta/alpha and tracking error - each refusing to compute on
  samples too small to support the statistic.
- `features.fundamental`: growth profiles with durability and stability,
  margins and trends, ROE/ROIC with effective tax rates, balance-sheet health,
  capital allocation, Altman Z (gated off financials), and an evidence-gated
  moat assessment that returns "none" unless the numbers support it.
- `features.valuation`: multiples with negative-denominator guards, relative
  valuation against history and peers, a fully-explicit two-stage DCF, reverse
  DCF, sensitivity grids, bear/base/bull scenarios, and a blend that surfaces
  method disagreement instead of averaging it away.
- `features.crypto`: supply/dilution metrics, emission rate, unlock overhang
  measured in days of volume (absent schedules reported as *unknown*, never
  zero), liquidity and Amihud illiquidity, network usage metrics, and an
  eight-factor crypto risk assessment that reports its own coverage.
- `features.macro`: point-in-time macro readings, policy/inflation/curve/credit
  stance classification, and bounded sector tilts (+/-15%) with stated priors.
- `features.regime`: bull/bear/sideways plus volatility and risk-appetite
  regimes from trend, realised-volatility percentile, breadth and credit, with
  confidence that falls when inputs are missing.

### Phase 4 - Scoring, risk, ensemble, probability, recommendations

**Added**

- `analysis.scoring`: factor sub-scorers with deliberately non-linear curves
  (implausible cheapness is penalised, growth credit saturates), composite
  scoring that *excludes* missing factors and renormalises rather than treating
  them as zero, a coverage haircut, a withheld score when too much evidence is
  missing, and a tier cap when data quality is below the configured minimum.
- `analysis.risk`: volatility, tail risk (VaR/expected shortfall), gap risk,
  permanent-loss risk from solvency inputs only, liquidity risk with days-to-exit,
  a risk level in which permanent-loss risk dominates, correlation matrices,
  portfolio volatility with explicit coverage reporting, and quotable limit
  breaches.
- `analysis.ensemble`: eight independent model views, a weighted consensus in
  which no single view may exceed 35% of the vote, agreement/dispersion metrics
  and named conflicts ("cheap but deteriorating: a possible value trap").
- `analysis.probability`: base-rate anchored probability forecasting with every
  adjustment itemised, bounded deviation from the anchor, shrinkage toward 50%
  for poor data or thin history, reliability-diagram calibration with monotone
  interpolation, Brier score and Brier *skill* score against the base rate.
- `analysis.sentiment`: auditable financial lexicon with negation handling,
  headline-versus-body divergence detection, hype and panic detection, source
  tier weighting, and conflict reconciliation by source hierarchy that leaves
  equally-authoritative disagreements explicitly unresolved.
- `analysis.events`: rule-based classification of 20+ event types with impact,
  expected magnitude, duration and thesis relevance; age-decayed aggregation.
- `analysis.anomaly`: robust-statistics detectors (volume, price, volatility,
  liquidity, fundamentals, valuation, insider clusters) that generate research
  candidates and are explicitly barred from generating signals.
- `analysis.sell`: five sell triggers, machine-evaluable exit conditions derived
  from the thesis itself, thesis-invalidation statements about assumptions
  rather than price, and an opportunity-cost check with a switching hurdle. A
  price decline alone triggers review, never a sell.
- `analysis.recommendation`: explicit gates (data quality, factor coverage,
  confidence, permanent-loss risk, survivable bear case, model agreement), the
  bear/bull case constructors, change description against the previous run, and
  the canonical rendered output block.
- `analysis.pipeline`: the per-asset orchestrator used identically by live runs,
  backtest replay and tests.

**Fixed**

- `analysis.valuation` ignored the `margin_delta` and `exit_multiple_delta` keys
  that `default.yaml` defined, so bear cases were far too gentle. Scenario
  deltas now shift the cash-flow base and the discount rate, and the config
  documents which keys are applied.

### Phase 5 - Backtesting

**Added**

- `backtest.costs`: commission, size- and asset-class-dependent spreads, and
  square-root market impact. Orders above the participation cap are rejected
  and recorded rather than silently filled; missing volume makes execution
  unverifiable rather than free.
- `backtest.engine`: walk-forward simulation with a point-in-time universe
  (assets truncated at the decision date and asserted future-free), next-bar
  execution, delisting liquidation at a configurable recovery haircut,
  survivorship warnings, and embargo validation against the label horizon.
- `backtest.metrics`: CAGR, volatility, Sharpe, Sortino, Calmar, max drawdown,
  VaR/ES, skew/kurtosis, beta/alpha, tracking error, information ratio, trade
  statistics with cost drag and turnover, benchmark comparison that states
  plainly when the strategy lost, and deflation of headline figures by the
  number of configurations tried.

### Phase 6 - Continuous learning

**Added**

- `learning.evaluation`: multi-dimensional grading of every stored prediction -
  return, excess return, path drawdown, realised volatility and a thesis
  outcome that distinguishes "succeeded" from "luck" (right direction, no
  excess return). HOLD/WATCH make no directional claim and are not scored as
  hits. Missing prices at the due date are flagged, never dropped.
- `learning.performance`: performance bucketed by asset class, sector, regime,
  horizon, recommendation, stated confidence and data quality; buckets below
  the sample floor report "insufficient" instead of a number. Detects
  systematic errors, no-skill probability forecasts, returns that came from
  market exposure rather than selection, and whether stated confidence actually
  tracks accuracy.
- `learning.registry`: immutable model versions with parameter and code
  fingerprints, promotion that retires rather than deletes, and a
  reproducibility report that says when code drift means historical output can
  no longer be re-derived.
- `learning.retrain`: bounded learning only - factor-effectiveness measurement
  (labelled association, not causation), weight proposals capped per update and
  refused without enough evidence, and promotion that requires out-of-sample
  samples, a margin, and no calibration regression.

### Phase 7 - Daily research loop, discovery, alerts and reporting

**Added**

- `pipeline.prioritization`: research priority from opportunity, magnitude of
  change, anomalies, holding status, staleness, novelty and imminent catalysts,
  with priority halved when data quality is poor; and a four-stage compute
  funnel that records why each asset was dropped.
- `pipeline.discovery`: recent listings (with explicit short-history warnings),
  unusual activity, accelerating fundamentals, insider clusters, crypto volume
  traction (warned as frequently wash trading) and sector laggards (flagged as
  "as often a warning as an opportunity"). Multiple independent triggers on one
  asset raise its interest; discoveries are labelled research candidates and
  never recommendations.
- `pipeline.alerts`: eleven threshold-driven rules with per-day deduplication,
  a file sink and database persistence. A drawdown alert says "re-review the
  thesis", not "sell".
- `analysis.memo`: full investment memo with the bear case placed before the
  bull case, epistemic status on forward-looking statements, and honest "no data
  for this section" fallbacks instead of generated filler.
- `pipeline.report`: daily report covering market overview, top opportunities,
  biggest changes, discoveries, portfolio, model performance and a daily
  self-evaluation. An empty report states that no asset cleared the bar and
  calls that a legitimate outcome.
- `pipeline.daily`: the 15-step loop (ingest, universe, market context,
  discovery, prioritise, analyse, detect changes, portfolio, alerts, store
  predictions, evaluate matured predictions, performance, learning, memos,
  report). Each step is timed and isolated: one failing step is recorded and
  skipped rather than aborting the run.
- `pipeline.data_access`: the single adapter through which the loop reads data,
  so point-in-time filtering cannot be bypassed by a new step.
