"""Ingestion tests. Everything runs offline through FakeTransport."""

import datetime as dt
import json

import pytest

from research_engine.core.errors import (AuthenticationError, DataUnavailable,
                                         ProviderError, RateLimitError)
from research_engine.core.types import SourceTier
from research_engine.ingestion.base import Capability, DataProvider
from research_engine.ingestion.cache import ResponseCache
from research_engine.ingestion.factory import build_registry
from research_engine.ingestion.http import FakeTransport, OfflineTransport, Response
from research_engine.ingestion.providers import (CoinGeckoProvider, CsvLocalProvider,
                                                 FredProvider, RssNewsProvider,
                                                 SecEdgarProvider, StooqProvider)
from research_engine.ingestion.ratelimit import RetryPolicy, TokenBucket
from research_engine.ingestion.registry import ProviderRegistry


# --------------------------------------------------------------- limits ----
def test_token_bucket_enforces_rate():
    clock = [0.0]
    bucket = TokenBucket(60, capacity=2, clock=lambda: clock[0],
                         sleeper=lambda s: clock.__setitem__(0, clock[0] + s))
    assert bucket.try_acquire() and bucket.try_acquire()
    assert not bucket.try_acquire()
    assert bucket.acquire(timeout=10) and clock[0] == pytest.approx(1.0, abs=0.01)


def test_backoff_is_exponential_and_capped():
    policy = RetryPolicy(base_seconds=1, max_seconds=8, jitter=0)
    assert [policy.delay(i) for i in range(1, 6)] == [1, 2, 4, 8, 8]
    assert policy.delay(3, retry_after=2.5) == 2.5


# ------------------------------------------------------------- retrying ----
class _Flaky(DataProvider):
    name = "flaky"
    capabilities = frozenset({Capability.PRICES_EOD})


def test_provider_retries_then_succeeds(tmp_path):
    calls = {"n": 0}

    def route(url, params):
        calls["n"] += 1
        if calls["n"] < 3:
            return Response(503, "server error", {}, url)
        return Response(200, json.dumps({"ok": True}), {}, url)

    transport = FakeTransport({"example.com": route})
    slept: list[float] = []
    provider = _Flaky(transport=transport, cache=ResponseCache(tmp_path / "c"),
                      requests_per_minute=10_000,
                      retry=RetryPolicy(max_retries=4, base_seconds=0.01, jitter=0),
                      sleeper=slept.append)
    assert provider.request_json("https://example.com/x") == {"ok": True}
    assert calls["n"] == 3 and len(slept) == 2
    assert provider.stats.retries == 2


def test_rate_limit_is_classified_and_retry_after_honoured(tmp_path):
    transport = FakeTransport({"example.com": Response(429, "slow down",
                                                       {"Retry-After": "1.5"}, "u")})
    slept: list[float] = []
    provider = _Flaky(transport=transport, requests_per_minute=10_000,
                      retry=RetryPolicy(max_retries=1, base_seconds=10, jitter=0),
                      sleeper=slept.append)
    with pytest.raises(RateLimitError):
        provider.request_json("https://example.com/x")
    assert slept == [1.5]
    assert provider.stats.rate_limited >= 1


def test_auth_failures_are_not_retried(tmp_path):
    transport = FakeTransport({"example.com": Response(401, "nope", {}, "u")})
    slept: list[float] = []
    provider = _Flaky(transport=transport, requests_per_minute=10_000,
                      sleeper=slept.append)
    with pytest.raises(AuthenticationError):
        provider.request_json("https://example.com/x")
    assert slept == []


def test_stale_cache_served_only_after_failure_and_marked(tmp_path):
    cache = ResponseCache(tmp_path / "c")
    cache.set("flaky", "k", json.dumps({"v": 1}), ttl_seconds=0)  # already stale
    transport = FakeTransport({"example.com": Response(500, "boom", {}, "u")})
    provider = _Flaky(transport=transport, cache=cache, requests_per_minute=10_000,
                      retry=RetryPolicy(max_retries=1, base_seconds=0, jitter=0),
                      sleeper=lambda s: None)
    assert provider.request_json("https://example.com/x", cache_key="k",
                                 ttl_seconds=60) == {"v": 1}


def test_offline_transport_refuses_loudly():
    provider = _Flaky(transport=OfflineTransport(), requests_per_minute=1000)
    with pytest.raises(ProviderError):
        provider.request_json("https://example.com/x")


# ------------------------------------------------------------ providers ----
STOOQ_CSV = ("Date,Open,High,Low,Close,Volume\n"
             "2026-01-02,10.0,10.5,9.8,10.4,1000000\n"
             "2026-01-05,10.4,10.9,10.3,10.8,1200000\n")


def test_stooq_parses_csv_and_declares_no_dividend_adjustment():
    transport = FakeTransport().add_text("stooq.com", STOOQ_CSV)
    provider = StooqProvider(transport=transport, requests_per_minute=1000)
    result = provider.fetch_prices("AAPL")
    assert len(result) == 2
    assert result.records[0]["close"] == 10.4
    assert result.records[0]["adj_close"] is None
    assert "adj_close" in result.missing and result.partial


def test_stooq_symbol_suffixing():
    provider = StooqProvider(transport=FakeTransport(), requests_per_minute=1000)
    assert provider.stooq_symbol("AAPL") == "aapl.us"
    assert provider.stooq_symbol("SHOP", "TSX") == "shop.ca"
    assert provider.stooq_symbol("aapl.us") == "aapl.us"


def test_stooq_no_data_raises_unavailable():
    transport = FakeTransport().add_text("stooq.com", "No data")
    provider = StooqProvider(transport=transport, requests_per_minute=1000)
    with pytest.raises(DataUnavailable):
        provider.fetch_prices("NOPE")


COINGECKO_MARKETS = [{
    "id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 65000.0,
    "market_cap": 1.3e12, "fully_diluted_valuation": 1.4e12, "total_volume": 3e10,
    "circulating_supply": 19.7e6, "total_supply": 21e6, "max_supply": 21e6,
    "market_cap_rank": 1, "price_change_percentage_24h": 1.5,
}]


def test_coingecko_market_snapshot_marks_onchain_missing():
    transport = FakeTransport().add_json("coins/markets", COINGECKO_MARKETS)
    provider = CoinGeckoProvider(transport=transport, requests_per_minute=1000)
    result = provider.fetch_crypto_market()
    assert result.records[0]["symbol"] == "BTC"
    assert result.records[0]["fully_diluted_valuation_usd"] == 1.4e12
    assert set(result.missing) >= {"tvl", "active_addresses"}


def test_coingecko_history_dedupes_and_marks_ohlc_missing():
    ts = lambda d: int(dt.datetime(2026, 1, d, tzinfo=dt.timezone.utc).timestamp() * 1000)
    payload = {"prices": [[ts(1), 100.0], [ts(2), 101.0], [ts(2), 101.5]],
               "total_volumes": [[ts(1), 5.0], [ts(2), 6.0]],
               "market_caps": [[ts(1), 50.0], [ts(2), 51.0]]}
    transport = FakeTransport().add_json("market_chart", payload)
    provider = CoinGeckoProvider(transport=transport, requests_per_minute=1000)
    result = provider.fetch_crypto_history("bitcoin", days=30)
    assert len(result) == 2                       # duplicate day collapsed
    assert result.records[-1]["close"] == 101.5   # latest wins
    assert result.records[-1]["adj_close"] == 101.5
    assert "open" in result.missing


SEC_FACTS = {
    "cik": 320193,
    "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            {"start": "2023-01-01", "end": "2023-12-31", "val": 1000, "fy": 2023,
             "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "0001"},
            {"start": "2024-01-01", "end": "2024-12-31", "val": 1200, "fy": 2024,
             "fp": "FY", "form": "10-K", "filed": "2025-02-01", "accn": "0002"},
            {"start": "2024-10-01", "end": "2024-12-31", "val": 350, "fy": 2024,
             "fp": "Q4", "form": "10-Q", "filed": "2025-01-15", "accn": "0003"},
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2024-01-01", "end": "2024-12-31", "val": 200, "fy": 2024,
             "fp": "FY", "form": "10-K", "filed": "2025-02-01", "accn": "0002"},
        ]}},
    }},
}


def test_sec_edgar_extracts_filing_dates_and_period_lengths():
    transport = FakeTransport().add_json("companyfacts", SEC_FACTS)
    provider = SecEdgarProvider(transport=transport, requests_per_minute=1000)
    result = provider.fetch_fundamentals("AAPL", identifiers={"cik": "320193"},
                                         metrics=["revenue", "net_income", "capex"])
    annual = [r for r in result.records if r["metric"] == "revenue" and r["period"] == "annual"]
    quarterly = [r for r in result.records if r["metric"] == "revenue" and r["period"] == "quarterly"]
    assert len(annual) == 2 and len(quarterly) == 1
    assert annual[-1]["filed_date"] == dt.date(2025, 2, 1)
    assert annual[-1]["accession"] == "0002"
    assert "capex" in result.missing          # explicitly reported as absent


def test_sec_edgar_requires_cik():
    provider = SecEdgarProvider(transport=FakeTransport(), requests_per_minute=1000)
    with pytest.raises(DataUnavailable):
        provider.fetch_fundamentals("AAPL")


def test_fred_skips_missing_markers_and_keeps_release_dates():
    payload = {"observations": [
        {"date": "2026-01-01", "value": "3.2", "realtime_start": "2026-01-15"},
        {"date": "2026-02-01", "value": ".", "realtime_start": "2026-02-15"},
    ]}
    transport = FakeTransport().add_json("stlouisfed", payload)
    provider = FredProvider(transport=transport, api_key="k", requests_per_minute=1000)
    result = provider.fetch_macro("CPIAUCSL")
    assert len(result) == 1
    assert result.records[0]["release_date"] == dt.date(2026, 1, 15)


def test_fred_without_key_is_unavailable():
    provider = FredProvider(transport=FakeTransport(), requests_per_minute=1000)
    assert not provider.available
    assert "API key" in provider.unavailable_reason()


RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>ACME beats estimates</title><link>https://x/1</link>
<pubDate>Mon, 05 Jan 2026 12:00:00 GMT</pubDate>
<description>&lt;p&gt;Revenue up&lt;/p&gt;</description></item>
<item><title>Unrelated story</title><link>https://x/2</link>
<pubDate>Mon, 05 Jan 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_rss_parses_and_filters_by_symbol():
    transport = FakeTransport().add_text("feed", RSS)
    provider = RssNewsProvider([{"url": "https://feed", "name": "Wire",
                                 "tier": "financial_journalism"}],
                               transport=transport, requests_per_minute=1000)
    result = provider.fetch_news("ACME")
    assert len(result) == 1
    assert result.records[0]["summary"] == "Revenue up"
    assert result.records[0]["source_tier"] is SourceTier.FINANCIAL_JOURNALISM


def test_csv_local_reads_fixtures(tmp_path):
    (tmp_path / "prices").mkdir()
    (tmp_path / "prices" / "ACME.csv").write_text(
        "date,open,high,low,close,volume\n2026-01-02,1,2,0.5,1.5,100\n")
    provider = CsvLocalProvider(tmp_path)
    result = provider.fetch_prices("acme")
    assert result.records[0]["close"] == 1.5
    assert "adj_close" in result.missing


def test_csv_local_defaults_filed_date_to_period_end(tmp_path):
    (tmp_path / "fundamentals").mkdir()
    (tmp_path / "fundamentals" / "ACME.csv").write_text(
        "metric,period,period_end,value,unit\nrevenue,annual,2025-12-31,100,USD\n")
    result = CsvLocalProvider(tmp_path).fetch_fundamentals("ACME")
    assert result.records[0]["filed_date"] == dt.date(2025, 12, 31)
    assert result.notes  # the assumption is disclosed


# ------------------------------------------------------------- registry ----
def test_registry_failover_and_health(settings, tmp_path):
    transport = FakeTransport().add_text("stooq.com", STOOQ_CSV)
    health: list[tuple[str, bool]] = []
    registry = build_registry(settings, transport=transport,
                              cache=ResponseCache(tmp_path / "cache"),
                              health_hook=lambda n, ok: health.append((n, ok)))
    # csv_local comes first in the default chain but has no data directory
    result, report = registry.require(Capability.PRICES_EOD, "fetch_prices",
                                      "AAPL", target="AAPL")
    assert result.provider == "stooq"
    assert any(a.skipped_reason for a in report.attempts)
    assert ("stooq", True) in health


def test_registry_reports_unavailable_rather_than_inventing(settings):
    registry = build_registry(settings, transport=FakeTransport())
    with pytest.raises(DataUnavailable):
        registry.require(Capability.PRICES_EOD, "fetch_prices", "NOSUCH",
                         target="NOSUCH")


def test_registry_describe_lists_key_requirements(settings):
    registry = build_registry(settings, transport=FakeTransport())
    described = {d["name"]: d for d in registry.describe()}
    assert described["fred"]["requires_key"] is True
    assert described["fred"]["available"] is False
    assert described["sec_edgar"]["source_tier"] == "regulatory_filing"


# ------------------------------------------------------------- universe ----
def test_crypto_quality_classification(settings):
    from research_engine.ingestion.universe import CryptoQuality

    big = {"symbol": "BTC", "name": "Bitcoin", "market_cap_usd": 1.3e12,
           "volume_24h_usd": 3e10, "exchange_count": 50}
    stable = {"symbol": "USDT", "name": "Tether", "market_cap_usd": 1e11,
              "volume_24h_usd": 5e10}
    tiny = {"symbol": "SHIBBY", "name": "Shibby", "market_cap_usd": 5e6,
            "volume_24h_usd": 1e4}
    dilutive = {"symbol": "NEW", "name": "Newcoin", "market_cap_usd": 2e9,
                "volume_24h_usd": 5e7, "fully_diluted_valuation_usd": 2e10}

    assert CryptoQuality.classify(big, settings=settings)[0] == CryptoQuality.INSTITUTIONAL
    assert CryptoQuality.classify(stable, settings=settings)[0] == CryptoQuality.EXCLUDED
    assert CryptoQuality.classify(tiny, settings=settings)[0] == CryptoQuality.SPECULATIVE
    grade, reasons = CryptoQuality.classify(dilutive, settings=settings)
    assert grade == CryptoQuality.ESTABLISHED
    assert any("FDV" in r for r in reasons)


def test_universe_builder_admits_and_flags(settings, db):
    from research_engine.ingestion.universe import UniverseBuilder
    from research_engine.storage.repositories import AssetRepository

    transport = FakeTransport().add_json("coins/markets", COINGECKO_MARKETS + [
        {"id": "tether", "symbol": "usdt", "name": "Tether", "market_cap": 1e11,
         "total_volume": 5e10, "circulating_supply": 1e11},
    ])
    registry = build_registry(settings, transport=transport)
    assets = AssetRepository(db)
    stats = UniverseBuilder(settings, registry, assets).build_crypto()
    assert stats.admitted == 1
    assert assets.get("USDT").is_active is False       # stored but never scanned
    # markets endpoint carries no venue count, so the top grade is withheld
    assert assets.get("BTC").quality_grade == "established"
    assert any("exchange coverage unknown" in t for t in assets.get("BTC").tags)
