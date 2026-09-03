# Configuration

## Precedence

Lowest to highest:

1. `engine/research_engine/config/default.yaml` — every setting, documented.
2. A YAML file: `--config path.yaml` or `RESEARCH_ENGINE_CONFIG=path.yaml`.
3. Environment variables: `RE__SECTION__KEY=value` (double underscores separate
   levels, values are parsed as numbers/booleans/lists).
4. Runtime overrides passed in code (used by tests).

```bash
export RE__SCORING__THRESHOLDS__STRONG=72
export RE__UNIVERSE__MIN_MARKET_CAP_USD=1e9
export RE__APP__LOG_JSON=true
```

Validation runs at load time and **rejects** configurations that would silently
corrupt research: non-monotonic score thresholds, negative or all-zero scoring
weights, a negative embargo, and `app.allow_trading: true`.

## Secrets

Credentials are read **only** from environment variables, never from any config
file, and are registered with the logging redactor the first time they are read.

| Variable | Used by | Required? |
| --- | --- | --- |
| `FRED_API_KEY` | macro series | yes, for macro data (free key) |
| `COINGECKO_API_KEY` | crypto data | optional (raises rate limits) |
| `INGESTION_CONTACT_EMAIL` | SEC EDGAR User-Agent | yes, for SEC (their policy) |
| `RISK_FREE_RATE` | valuation | optional (overrides the default) |

A provider whose key is absent is still registered but reports itself
unavailable, so the failover chain skips it and `research-engine providers`
shows exactly why.

The API never returns a credential value; it reports the *name* of the variable
a provider expects.

## Section reference

### `app`
| Key | Default | Meaning |
| --- | --- | --- |
| `environment` | `development` | free-form label |
| `log_level` / `log_json` | `INFO` / `false` | logging |
| `data_dir` | `./data` | root for the database, cache, reports, models |
| `allow_trading` | `false` | **must stay false**; validated |
| `offline` | `false` | replace network transports with one that refuses immediately |

### `database`
`path` (relative to `data_dir` unless absolute), `busy_timeout_ms`,
`journal_mode`, `synchronous`, `cache_size_kb`.

### `universe`
Size and liquidity floors, countries, exchanges, sectors, caps, and
`include_delisted_in_history` (a survivorship-bias control: leave it on).
`universe.crypto` has its own floors plus `exclude_stablecoins` and
`require_exchange_count`.

### `ingestion`
Timeouts, retry count and backoff, cache directory and per-kind TTLs, the
user-agent string, and `ingestion.providers` — the **failover chain per
capability**. Live sources come first; `csv_local` last as an operator-supplied
fallback. Reordering this list is how you swap providers; no code changes.

### `providers`
Per-provider `enabled`, `base_url`, `requests_per_minute`, `api_key_env` and
`source_tier`. `source_tier` feeds the source hierarchy used to resolve
conflicting values, so setting it dishonestly corrupts fact-checking.

### `quality`
Staleness limits (separate for crypto), the extreme-move flag threshold, minimum
history for analysis and for valuation, and the minimum session-coverage ratio.

### `analysis`
Horizons, the default horizon, risk-free rate and equity risk premium,
benchmarks, `min_confidence_to_recommend`, technical windows, and the valuation
block (DCF years, terminal growth caps, discount-rate band, and the bear/base/
bull scenario deltas — `revenue_growth_delta`, `margin_delta`,
`discount_delta`).

### `scoring`
`weights` and `crypto_weights` (normalised at load), `thresholds` for the
opportunity tiers, `min_quality_for_buy`, and `max_missing_factor_ratio` — the
share of factor weight that may be missing before the score is withheld.

### `risk`
VaR confidence, lookback, position/sector/crypto weight limits, the
concentration warning level, the liquidity participation cap and the ATR stop
multiple.

### `backtest`
Window, capital, commission and slippage (separate for crypto), the liquidity
multiple, walk-forward windows, the **embargo** (must be at least the label
horizon), benchmarks and the complexity penalty.

### `learning`
Minimum samples before retraining or before a bucket is reported, calibration
bins, the per-update weight-change cap, evaluation horizons, the improvement
required to promote a model, and the model registry directory.

### `pipeline`
Funnel capacities per stage, the discovery cap, the report directory and worker
count.

### `alerts`
`enabled`, channels, file path, and every threshold (`score_change_abs`,
`price_move_abs_1d`, `crypto_price_move_abs_1d`, `drawdown_from_entry`,
`risk_increase_levels`, `token_unlock_days_ahead`, …).

### `api`
Host, port, CORS origins, `read_only` (the API has no mutating routes at all).

## Common recipes

**Air-gapped run against a vendor extract**

```bash
export RE__APP__OFFLINE=true
export RE__PROVIDERS__CSV_LOCAL__ROOT=/data/vendor-extract
research-engine universe --refresh && research-engine daily
```

**Be more conservative**

```yaml
scoring:
  thresholds: {watch: 50, moderate: 65, strong: 78, exceptional: 88}
  min_quality_for_buy: excellent
analysis:
  min_confidence_to_recommend: 0.5
```

**Crypto only**

```yaml
universe:
  asset_classes: [crypto]
  crypto: {min_market_cap_usd: 100000000, min_daily_volume_usd: 10000000}
```
