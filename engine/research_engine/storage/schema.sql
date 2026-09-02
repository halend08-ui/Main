-- ---------------------------------------------------------------------------
-- Research engine schema.
--
-- Conventions
--   * Every fact-bearing table carries (source, as_of, retrieved_at) so that
--     point-in-time queries can filter on what was *knowable* at a given time.
--   * Timestamps are ISO-8601 UTC strings; dates are 'YYYY-MM-DD'.
--   * Prices are stored raw AND split/dividend adjusted; adjustment factors are
--     kept so historical adjustments can be recomputed and audited.
--   * Nothing is ever deleted for "corrections": superseded rows are marked
--     via revision counters so history stays reproducible.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_migrations (
    version      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    applied_at   TEXT NOT NULL
);

-- --------------------------------------------------------------- assets ----
CREATE TABLE IF NOT EXISTS assets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    asset_class         TEXT NOT NULL,
    name                TEXT,
    exchange            TEXT,
    country             TEXT,
    currency            TEXT,
    sector              TEXT,
    industry            TEXT,
    cik                 TEXT,             -- SEC identifier (equities)
    figi                TEXT,
    coingecko_id        TEXT,             -- crypto identifier
    chain               TEXT,             -- crypto: settlement chain
    is_active           INTEGER NOT NULL DEFAULT 1,
    listed_date         TEXT,
    delisted_date       TEXT,             -- survivorship-bias control
    delisting_reason    TEXT,
    first_price_date    TEXT,
    last_price_date     TEXT,
    market_cap_usd      REAL,
    shares_outstanding  REAL,
    float_shares        REAL,
    max_supply          REAL,
    circulating_supply  REAL,
    quality_grade       TEXT,
    tags                TEXT,             -- JSON array
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (asset_class, symbol)
);
CREATE INDEX IF NOT EXISTS idx_assets_class_active ON assets (asset_class, is_active);
CREATE INDEX IF NOT EXISTS idx_assets_sector       ON assets (sector, industry);
CREATE INDEX IF NOT EXISTS idx_assets_mcap         ON assets (market_cap_usd DESC);
CREATE INDEX IF NOT EXISTS idx_assets_cik          ON assets (cik);

-- --------------------------------------------------------------- prices ----
CREATE TABLE IF NOT EXISTS prices_daily (
    asset_id        INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    date            TEXT    NOT NULL,
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL    NOT NULL,
    volume          REAL,
    adj_close       REAL,
    split_factor    REAL    NOT NULL DEFAULT 1.0,
    dividend        REAL    NOT NULL DEFAULT 0.0,
    currency        TEXT,
    source          TEXT    NOT NULL,
    retrieved_at    TEXT    NOT NULL,
    quality         TEXT    NOT NULL DEFAULT 'fair',
    revision        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (asset_id, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_date        ON prices_daily (date);
CREATE INDEX IF NOT EXISTS idx_prices_asset_date  ON prices_daily (asset_id, date DESC);

-- ------------------------------------------------------- corporate actions --
CREATE TABLE IF NOT EXISTS corporate_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    action_type  TEXT NOT NULL,           -- split | dividend | spinoff | ticker_change
    ex_date      TEXT NOT NULL,
    value        REAL,                    -- ratio for splits, amount for dividends
    detail       TEXT,
    source       TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE (asset_id, action_type, ex_date, value)
);
CREATE INDEX IF NOT EXISTS idx_actions_asset ON corporate_actions (asset_id, ex_date);

-- --------------------------------------------------------- fundamentals ----
-- One row per (asset, statement line item, fiscal period, source revision).
-- `filed_date` is the point-in-time key: analysis as of T may only read rows
-- with filed_date <= T. This is the primary look-ahead-bias control.
CREATE TABLE IF NOT EXISTS fundamentals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    metric        TEXT    NOT NULL,
    statement     TEXT,                   -- income | balance | cashflow | derived
    period        TEXT    NOT NULL,       -- annual | quarterly | ttm
    period_start  TEXT,
    period_end    TEXT    NOT NULL,
    fiscal_year   INTEGER,
    fiscal_period TEXT,
    value         REAL,                   -- NULL means explicitly unavailable
    unit          TEXT,
    filed_date    TEXT    NOT NULL,
    accession     TEXT,                   -- filing identifier for audit
    form          TEXT,                   -- 10-K, 10-Q, 40-F, ...
    source        TEXT    NOT NULL,
    source_tier   TEXT    NOT NULL,
    retrieved_at  TEXT    NOT NULL,
    quality       TEXT    NOT NULL DEFAULT 'fair',
    revision      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (asset_id, metric, period, period_end, source, accession)
);
CREATE INDEX IF NOT EXISTS idx_fund_asset_metric ON fundamentals (asset_id, metric, period, period_end DESC);
CREATE INDEX IF NOT EXISTS idx_fund_pit          ON fundamentals (asset_id, filed_date);
CREATE INDEX IF NOT EXISTS idx_fund_metric       ON fundamentals (metric, period_end DESC);

-- ------------------------------------------------------- crypto metrics ----
CREATE TABLE IF NOT EXISTS crypto_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    metric       TEXT NOT NULL,           -- tvl | active_addresses | fees | ...
    date         TEXT NOT NULL,
    value        REAL,
    unit         TEXT,
    source       TEXT NOT NULL,
    source_tier  TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    quality      TEXT NOT NULL DEFAULT 'fair',
    UNIQUE (asset_id, metric, date, source)
);
CREATE INDEX IF NOT EXISTS idx_crypto_metric ON crypto_metrics (asset_id, metric, date DESC);

CREATE TABLE IF NOT EXISTS token_unlocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    unlock_date   TEXT NOT NULL,
    tokens        REAL,
    pct_of_supply REAL,
    recipient     TEXT,
    source        TEXT NOT NULL,
    retrieved_at  TEXT NOT NULL,
    UNIQUE (asset_id, unlock_date, recipient)
);
CREATE INDEX IF NOT EXISTS idx_unlocks_date ON token_unlocks (unlock_date);

-- ------------------------------------------------------------ ownership ----
CREATE TABLE IF NOT EXISTS ownership (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,          -- insider | institutional | short_interest
    as_of_date    TEXT NOT NULL,
    holder        TEXT,
    shares        REAL,
    change_shares REAL,
    value_usd     REAL,
    pct_of_float  REAL,
    filed_date    TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_tier   TEXT NOT NULL,
    retrieved_at  TEXT NOT NULL,
    UNIQUE (asset_id, kind, as_of_date, holder, filed_date)
);
CREATE INDEX IF NOT EXISTS idx_ownership_asset ON ownership (asset_id, kind, as_of_date DESC);

-- ----------------------------------------------------------------- news ----
CREATE TABLE IF NOT EXISTS news (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id   TEXT UNIQUE,            -- dedupe key (url hash)
    asset_id      INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    symbol        TEXT,
    headline      TEXT NOT NULL,
    summary       TEXT,
    url           TEXT,
    published_at  TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_tier   TEXT NOT NULL,
    language      TEXT DEFAULT 'en',
    headline_sentiment REAL,
    body_sentiment     REAL,
    hype_score         REAL,
    duplicate_of       INTEGER REFERENCES news(id),
    retrieved_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_asset_time ON news (asset_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_time       ON news (published_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id       INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    event_type     TEXT NOT NULL,
    occurred_at    TEXT NOT NULL,
    detected_at    TEXT NOT NULL,
    impact         TEXT NOT NULL,          -- extremely_negative .. extremely_positive
    expected_impact_pct REAL,
    confidence     REAL,
    duration_days  INTEGER,
    changes_thesis INTEGER NOT NULL DEFAULT 0,
    headline       TEXT,
    detail         TEXT,                   -- JSON
    news_id        INTEGER REFERENCES news(id),
    source         TEXT NOT NULL,
    source_tier    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_asset ON events (asset_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type  ON events (event_type, occurred_at DESC);

-- ---------------------------------------------------------------- macro ----
CREATE TABLE IF NOT EXISTS macro_series (
    series_id    TEXT NOT NULL,
    date         TEXT NOT NULL,
    value        REAL,
    unit         TEXT,
    release_date TEXT,                     -- point-in-time: when it was published
    source       TEXT NOT NULL,
    source_tier  TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (series_id, date, source)
);
CREATE INDEX IF NOT EXISTS idx_macro_release ON macro_series (series_id, release_date);

CREATE TABLE IF NOT EXISTS market_regimes (
    date         TEXT PRIMARY KEY,
    regime       TEXT NOT NULL,
    volatility_regime TEXT,
    risk_appetite     TEXT,
    confidence   REAL,
    detail       TEXT,                     -- JSON of the inputs used
    model_version TEXT NOT NULL,
    computed_at  TEXT NOT NULL
);

-- ------------------------------------------------------- analysis output ---
CREATE TABLE IF NOT EXISTS scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    as_of         TEXT NOT NULL,
    total_score   REAL NOT NULL,
    tier          TEXT NOT NULL,
    components    TEXT NOT NULL,           -- JSON {factor: {score, weight, quality}}
    data_quality  TEXT NOT NULL,
    coverage      REAL,
    model_version TEXT NOT NULL,
    computed_at   TEXT NOT NULL,
    UNIQUE (asset_id, as_of, model_version)
);
CREATE INDEX IF NOT EXISTS idx_scores_asof  ON scores (as_of DESC, total_score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_asset ON scores (asset_id, as_of DESC);

CREATE TABLE IF NOT EXISTS recommendations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id         INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    as_of            TEXT NOT NULL,
    recommendation   TEXT NOT NULL,
    previous_recommendation TEXT,
    tier             TEXT,
    score            REAL,
    confidence       REAL NOT NULL,
    horizon          TEXT NOT NULL,
    price            REAL,
    fair_value_bear  REAL,
    fair_value_base  REAL,
    fair_value_bull  REAL,
    expected_return_bear REAL,
    expected_return_base REAL,
    expected_return_bull REAL,
    prob_positive    REAL,
    risk_level       TEXT NOT NULL,
    data_quality     TEXT NOT NULL,
    rationale        TEXT,                 -- JSON: evidence, catalysts, risks
    sell_conditions  TEXT,                 -- JSON list of measurable conditions
    invalidation     TEXT,                 -- JSON list
    model_version    TEXT NOT NULL,
    data_version     TEXT,
    computed_at      TEXT NOT NULL,
    UNIQUE (asset_id, as_of, model_version)
);
CREATE INDEX IF NOT EXISTS idx_recs_asof  ON recommendations (as_of DESC, score DESC);
CREATE INDEX IF NOT EXISTS idx_recs_asset ON recommendations (asset_id, as_of DESC);

CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    as_of        TEXT NOT NULL,
    family       TEXT NOT NULL,            -- technical | fundamental | flow | onchain
    name         TEXT NOT NULL,
    value        REAL,
    direction    REAL,
    strength     REAL,
    quality      TEXT NOT NULL,
    model_version TEXT,
    UNIQUE (asset_id, as_of, family, name)
);
CREATE INDEX IF NOT EXISTS idx_signals_asof ON signals (as_of DESC, family);

-- ------------------------------------------------------------ learning -----
CREATE TABLE IF NOT EXISTS predictions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id          INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    recommendation_id INTEGER REFERENCES recommendations(id),
    created_at        TEXT NOT NULL,
    as_of             TEXT NOT NULL,
    horizon           TEXT NOT NULL,
    due_at            TEXT NOT NULL,
    price_at_prediction REAL NOT NULL,
    recommendation    TEXT NOT NULL,
    confidence        REAL NOT NULL,
    prob_positive     REAL,
    expected_return   REAL,
    expected_downside REAL,
    factors           TEXT,                -- JSON {factor: contribution}
    regime            TEXT,
    sector            TEXT,
    asset_class       TEXT NOT NULL,
    model_version     TEXT NOT NULL,
    data_version      TEXT,
    data_quality      TEXT NOT NULL,
    evaluated         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pred_due    ON predictions (evaluated, due_at);
CREATE INDEX IF NOT EXISTS idx_pred_asset  ON predictions (asset_id, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_pred_model  ON predictions (model_version, horizon);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    prediction_id   INTEGER PRIMARY KEY REFERENCES predictions(id) ON DELETE CASCADE,
    evaluated_at    TEXT NOT NULL,
    price_at_due    REAL,
    actual_return   REAL,
    benchmark_return REAL,
    excess_return   REAL,
    max_drawdown    REAL,
    realized_vol    REAL,
    hit             INTEGER,               -- direction correct (1/0)
    thesis_outcome  TEXT,                  -- succeeded | failed | partial | open
    failure_reason  TEXT,
    factor_attribution TEXT,               -- JSON
    data_quality    TEXT
);

CREATE TABLE IF NOT EXISTS model_versions (
    version         TEXT PRIMARY KEY,
    family          TEXT NOT NULL,         -- scoring | probability | regime | ...
    created_at      TEXT NOT NULL,
    parent_version  TEXT,
    train_start     TEXT,
    train_end       TEXT,
    test_start      TEXT,
    test_end        TEXT,
    features        TEXT,                  -- JSON
    parameters      TEXT,                  -- JSON
    validation_metrics TEXT,               -- JSON
    test_metrics    TEXT,                  -- JSON
    data_sources    TEXT,                  -- JSON
    code_fingerprint TEXT,
    status          TEXT NOT NULL DEFAULT 'candidate',  -- candidate|active|retired
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_models_family ON model_versions (family, status);

CREATE TABLE IF NOT EXISTS model_performance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version TEXT NOT NULL REFERENCES model_versions(version),
    computed_at   TEXT NOT NULL,
    bucket_kind   TEXT NOT NULL,           -- overall|asset_class|sector|regime|horizon|confidence
    bucket_value  TEXT NOT NULL,
    horizon       TEXT,
    samples       INTEGER NOT NULL,
    hit_rate      REAL,
    avg_return    REAL,
    avg_excess    REAL,
    sharpe        REAL,
    sortino       REAL,
    max_drawdown  REAL,
    brier         REAL,
    calibration_error REAL,
    profit_factor REAL,
    avg_winner    REAL,
    avg_loser     REAL,
    detail        TEXT,
    UNIQUE (model_version, computed_at, bucket_kind, bucket_value, horizon)
);
CREATE INDEX IF NOT EXISTS idx_modelperf ON model_performance (model_version, bucket_kind);

CREATE TABLE IF NOT EXISTS calibration (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version TEXT NOT NULL,
    computed_at   TEXT NOT NULL,
    horizon       TEXT,
    bin_low       REAL NOT NULL,
    bin_high      REAL NOT NULL,
    predicted_mean REAL,
    observed_rate REAL,
    samples       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calib ON calibration (model_version, computed_at DESC);

-- ----------------------------------------------------------- backtests -----
CREATE TABLE IF NOT EXISTS backtests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    model_version TEXT,
    config        TEXT NOT NULL,           -- JSON: full parameters for reproduction
    start_date    TEXT NOT NULL,
    end_date      TEXT NOT NULL,
    metrics       TEXT NOT NULL,           -- JSON
    benchmark_metrics TEXT,
    folds         TEXT,                    -- JSON walk-forward folds
    warnings      TEXT,
    code_fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS idx_backtests_name ON backtests (name, created_at DESC);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_id INTEGER NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
    asset_id    INTEGER REFERENCES assets(id),
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    entry_date  TEXT NOT NULL,
    exit_date   TEXT,
    entry_price REAL NOT NULL,
    exit_price  REAL,
    quantity    REAL NOT NULL,
    costs       REAL NOT NULL DEFAULT 0,
    pnl         REAL,
    return_pct  REAL,
    reason      TEXT
);
CREATE INDEX IF NOT EXISTS idx_bt_trades ON backtest_trades (backtest_id, entry_date);

-- ----------------------------------------------------------- portfolio -----
CREATE TABLE IF NOT EXISTS portfolios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    base_currency TEXT NOT NULL DEFAULT 'USD',
    cash        REAL NOT NULL DEFAULT 0,
    notes       TEXT,
    is_hypothetical INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id  INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    asset_id      INTEGER NOT NULL REFERENCES assets(id),
    opened_at     TEXT NOT NULL,
    closed_at     TEXT,
    entry_price   REAL NOT NULL,
    quantity      REAL NOT NULL,
    thesis        TEXT,
    target_price  REAL,
    stop_price    REAL,
    max_loss_pct  REAL,
    horizon       TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    exit_price    REAL,
    exit_reason   TEXT,
    UNIQUE (portfolio_id, asset_id, opened_at)
);
CREATE INDEX IF NOT EXISTS idx_positions_pf ON positions (portfolio_id, status);

-- ------------------------------------------------- research & operations ---
CREATE TABLE IF NOT EXISTS research_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    queued_at     TEXT NOT NULL,
    priority      REAL NOT NULL,
    stage         INTEGER NOT NULL DEFAULT 1,
    reason        TEXT NOT NULL,
    trigger       TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    last_analyzed_at TEXT,
    UNIQUE (asset_id, stage, status)
);
CREATE INDEX IF NOT EXISTS idx_queue_priority ON research_queue (status, priority DESC);

CREATE TABLE IF NOT EXISTS research_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,           -- memo | daily | self_evaluation
    as_of         TEXT NOT NULL,
    title         TEXT NOT NULL,
    body_markdown TEXT NOT NULL,
    payload       TEXT,                    -- JSON structured version
    model_version TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports ON research_reports (kind, as_of DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    asset_id     INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    severity     TEXT NOT NULL,
    title        TEXT NOT NULL,
    detail       TEXT,
    payload      TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts (created_at DESC, severity);

CREATE TABLE IF NOT EXISTS data_sources (
    name            TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    source_tier     TEXT NOT NULL,
    base_url        TEXT,
    requires_key    INTEGER NOT NULL DEFAULT 0,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_success_at TEXT,
    last_failure_at TEXT,
    failure_count   INTEGER NOT NULL DEFAULT 0,
    success_count   INTEGER NOT NULL DEFAULT 0,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    scope        TEXT NOT NULL,            -- prices | fundamentals | news | universe
    as_of        TEXT NOT NULL,
    grade        TEXT NOT NULL,
    score        REAL NOT NULL,
    issues       TEXT NOT NULL,            -- JSON list of findings
    checked_at   TEXT NOT NULL,
    UNIQUE (asset_id, scope, as_of)
);
CREATE INDEX IF NOT EXISTS idx_dq_scope ON data_quality_reports (scope, as_of DESC);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    capability    TEXT NOT NULL,
    provider      TEXT NOT NULL,
    assets_requested INTEGER,
    assets_succeeded INTEGER,
    rows_written  INTEGER,
    failures      INTEGER,
    detail        TEXT,
    status        TEXT NOT NULL DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS idx_ingest_time ON ingestion_runs (started_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    as_of        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'running',
    steps        TEXT,                     -- JSON per-step timings/results
    error        TEXT,
    model_version TEXT,
    data_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_pipeline_time ON pipeline_runs (started_at DESC);
