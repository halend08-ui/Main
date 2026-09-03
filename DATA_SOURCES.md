# Data sources

## Source hierarchy

Every value carries a `source_tier`. When sources disagree, the higher tier
wins; when two equally authoritative sources disagree, the conflict is left
**explicitly unresolved** and the value is treated as unavailable
(`analysis/sentiment.py::reconcile`). Averaging conflicting numbers is never
done.

| Tier | Weight | Examples |
| --- | --- | --- |
| Regulatory filing | 1.00 | SEC EDGAR, FRED |
| Company filing | 0.97 | 10-K/10-Q as filed |
| Company documentation | 0.90 | IR site, protocol docs |
| Earnings material | 0.90 | transcripts, releases |
| Data provider | 0.75 | Stooq, CoinGecko |
| Financial journalism | 0.50 | reputable outlets |
| Secondary research | 0.35 | third-party notes |
| Social media | 0.10 | never treated as equivalent to a filing |

## Wired-in providers

| Provider | Cost | Key | Supplies | Does **not** supply |
| --- | --- | --- | --- | --- |
| **SEC EDGAR** | free | UA email | XBRL fundamentals with `filed_date`, accession and form; ticker→CIK map | non-US issuers, market data, estimates |
| **Stooq** | free | none | daily OHLCV for US/CA/UK/DE | dividend-adjusted closes, fundamentals, corporate actions |
| **CoinGecko** | free tier | optional | crypto price/market cap/FDV/supply/volume, dev + community stats, venue counts | TVL, active addresses, unlock schedules, holder concentration |
| **FRED** | free | required | US macro with vintage (point-in-time) support | company data |
| **RSS** | free | none | operator-configured feeds, each with its own tier | structured event data |
| **csv_local** | n/a | none | prices, fundamentals, macro, news, universes from local files | anything not in the files |

### Provider caveats that affect research

* **Stooq closes are split-adjusted but not dividend-adjusted.** The provider
  reports `adj_close` as *missing* rather than passing raw closes off as
  total-return adjusted, so total-return figures built on Stooq alone
  understate returns for dividend payers. Supply a total-return source, or read
  the numbers as price-return.
* **SEC concept mapping is a judgement.** Companies tag revenue under several
  US-GAAP concepts; the provider tries a documented priority list per canonical
  metric and records which concept was used. It never blends concepts.
* **CoinGecko's markets endpoint carries no venue count**, so the crypto quality
  classifier cannot award its top grade from that endpoint and says so in the
  asset's tags.
* **FRED revises series.** Historical runs must pass an as-of date so the
  vintage endpoint is used; otherwise revised data leaks backwards.
* **RSS feeds are whatever the operator configures.** Set each feed's tier
  honestly: it directly weights the evidence.

## Not wired in

Schema and repository support exist, but no free provider is configured:
insider transactions (SEC Form 4), institutional holdings (13F), short interest,
analyst estimates, earnings transcripts, options data, on-chain metrics
(TVL/addresses/fees), token unlock schedules, developer activity beyond
CoinGecko's summary, and web/search-trend data.

The engine treats all of these as **unavailable**, which lowers confidence and
appears in the research agent's "questions I could not answer" list. It does not
estimate them.

## Paid options, if you want the gaps filled

Grouped by what they unlock; none is required, and adding one is a provider
class plus a chain entry in `default.yaml`.

* **Fundamentals + estimates**: FactSet, S&P Capital IQ, Refinitiv (enterprise);
  FinancialModelingPrep, Intrinio, Sharadar/Nasdaq Data Link (mid-market).
* **Total-return prices and corporate actions**: Polygon, Tiingo, EOD Historical
  Data, Nasdaq Data Link.
* **Ownership, insiders, short interest**: Quiver, SEC bulk data (free but
  needs parsing), S3 Partners / Ortex for short interest.
* **News with entity tagging**: Benzinga, RavenPack, Bloomberg.
* **Crypto on-chain**: Glassnode, Nansen, Dune, DefiLlama (TVL, free tier),
  Token Unlocks / CryptoRank for vesting schedules.

## Rate limits and etiquette

Every provider has a token-bucket limiter, exponential backoff with jitter, a
retry cap and a response cache. `Retry-After` is honoured. On exhaustion the
engine will serve a **stale** cache entry, clearly labelled in the logs, rather
than fabricating — and if there is no cache entry, it reports the data as
unavailable.

Cache TTLs are per data kind (`ingestion.cache_ttl_seconds`) and default to 12h
for EOD prices, 24h for fundamentals and filings, 30m for news, 15m for crypto
market snapshots.

## Adding a provider

1. Subclass `DataProvider`, declare `capabilities` and `source_tier`.
2. Implement the `fetch_*` methods you support; return `ProviderResult` with
   `missing` listing every field you could not supply.
3. Register the class in `ingestion/factory.py::BUILDERS`.
4. Add a `providers.<name>` block and put it in the relevant chains.
5. Write a test using `FakeTransport` — no test may touch the network.
