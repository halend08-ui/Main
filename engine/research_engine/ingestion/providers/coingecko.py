"""CoinGecko crypto market-data provider.

Upstream: https://api.coingecko.com/api/v3 -- free "demo" tier without a key
(roughly 5-15 requests/minute, subject to change), higher limits with a demo or
pro key supplied via ``COINGECKO_API_KEY``.

Supplies: market snapshot (price, market cap, FDV, volume, supply), daily price
history, and per-coin metadata (categories, developer/community stats, exchange
listings). It does **not** supply audited on-chain metrics such as TVL or
active addresses; those are marked missing so downstream scoring degrades
rather than guessing.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from research_engine.core.errors import DataUnavailable
from research_engine.core.logging import get_logger
from research_engine.core.types import DataQuality, SourceTier
from research_engine.ingestion.base import Capability, DataProvider, ProviderResult

log = get_logger(__name__)

STABLECOIN_CATEGORIES = {"stablecoins", "usd stablecoin", "eur stablecoin"}


class CoinGeckoProvider(DataProvider):
    name = "coingecko"
    capabilities = frozenset({
        Capability.CRYPTO_MARKET, Capability.CRYPTO_HISTORY,
        Capability.UNIVERSE_CRYPTO, Capability.PRICES_EOD,
    })
    source_tier = SourceTier.DATA_PROVIDER
    default_quality = DataQuality.GOOD
    requires_key = False
    base_url = "https://api.coingecko.com/api/v3"
    documentation = ("Free tier without key (low rate limit). Provides price, "
                     "market cap, FDV, supply, volume, dev/community activity. "
                     "No TVL / active-address data.")

    def __init__(self, *, base_url: str | None = None,
                 ttl_market: float = 900, ttl_history: float = 43_200,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = base_url or type(self).base_url
        self.ttl_market = ttl_market
        self.ttl_history = ttl_history

    def headers(self) -> dict[str, str]:
        h = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self._api_key:
            # Header name differs between demo and pro plans; send the demo one
            # by default, documented in DATA_SOURCES.md.
            h["x-cg-demo-api-key"] = self._api_key
        return h

    # -- market snapshot ---------------------------------------------------
    def fetch_crypto_market(self, symbols: Sequence[str] | None = None, *,
                            page: int = 1, per_page: int = 250,
                            vs_currency: str = "usd") -> ProviderResult:
        url = f"{self.base_url}/coins/markets"
        params: dict[str, Any] = {
            "vs_currency": vs_currency, "order": "market_cap_desc",
            "per_page": min(int(per_page), 250), "page": int(page),
            "price_change_percentage": "24h,7d,30d",
        }
        if symbols:
            params["ids"] = ",".join(s.lower() for s in symbols)
        payload = self.request_json(url, params=params,
                                    cache_key=f"markets:{params}",
                                    ttl_seconds=self.ttl_market)
        if not isinstance(payload, list):
            raise DataUnavailable(f"{self.name}: unexpected markets payload")
        records = []
        for coin in payload:
            records.append({
                "coingecko_id": coin.get("id"),
                "symbol": (coin.get("symbol") or "").upper(),
                "name": coin.get("name"),
                "price_usd": coin.get("current_price"),
                "market_cap_usd": coin.get("market_cap"),
                "fully_diluted_valuation_usd": coin.get("fully_diluted_valuation"),
                "volume_24h_usd": coin.get("total_volume"),
                "circulating_supply": coin.get("circulating_supply"),
                "total_supply": coin.get("total_supply"),
                "max_supply": coin.get("max_supply"),
                "ath": coin.get("ath"),
                "ath_change_pct": coin.get("ath_change_percentage"),
                "price_change_pct_24h": coin.get("price_change_percentage_24h"),
                "price_change_pct_7d": coin.get(
                    "price_change_percentage_7d_in_currency"),
                "price_change_pct_30d": coin.get(
                    "price_change_percentage_30d_in_currency"),
                "market_cap_rank": coin.get("market_cap_rank"),
                "last_updated": coin.get("last_updated"),
            })
        return self.result(Capability.CRYPTO_MARKET, records, url=url,
                           missing=("tvl", "active_addresses", "fees", "unlocks"),
                           partial=True)

    def fetch_universe(self, pages: int = 4, per_page: int = 250) -> ProviderResult:
        records: list[Mapping[str, Any]] = []
        for page in range(1, int(pages) + 1):
            result = self.fetch_crypto_market(page=page, per_page=per_page)
            records.extend(result.records)
            if len(result.records) < per_page:
                break
        return self.result(Capability.UNIVERSE_CRYPTO, records,
                           url=f"{self.base_url}/coins/markets",
                           missing=("chain", "exchange_count"), partial=True)

    # -- history -----------------------------------------------------------
    def fetch_crypto_history(self, coin_id: str, *, days: int = 365,
                             vs_currency: str = "usd") -> ProviderResult:
        url = f"{self.base_url}/coins/{coin_id}/market_chart"
        params = {"vs_currency": vs_currency, "days": int(days), "interval": "daily"}
        payload = self.request_json(url, params=params,
                                    cache_key=f"chart:{coin_id}:{days}",
                                    ttl_seconds=self.ttl_history)
        prices = payload.get("prices") or []
        volumes = {int(ts): vol for ts, vol in (payload.get("total_volumes") or [])}
        caps = {int(ts): cap for ts, cap in (payload.get("market_caps") or [])}
        bars = []
        for ts, price in prices:
            if price is None:
                continue
            day = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).date()
            bars.append({
                "date": day, "open": None, "high": None, "low": None,
                "close": float(price), "volume": volumes.get(int(ts)),
                # Crypto has no splits or dividends: the close *is* the adjusted
                # close, so total-return maths is exact here.
                "adj_close": float(price),
                "market_cap_usd": caps.get(int(ts)),
            })
        if not bars:
            raise DataUnavailable(f"{self.name}: no history for {coin_id}")
        # Deduplicate: CoinGecko returns both a daily bar and a partial "now" bar.
        seen: dict[date, dict[str, Any]] = {}
        for bar in bars:
            seen[bar["date"]] = bar
        ordered = [seen[d] for d in sorted(seen)]
        return self.result(Capability.CRYPTO_HISTORY, ordered,
                           as_of=ordered[-1]["date"], url=url,
                           missing=("open", "high", "low"), partial=True)

    def fetch_prices(self, symbol: str, *, start: date | None = None,
                     end: date | None = None, coin_id: str | None = None
                     ) -> ProviderResult:
        """EOD prices for a crypto asset; ``coin_id`` is the CoinGecko slug."""
        ident = coin_id or symbol.lower()
        days = 3650
        if start:
            days = max(1, (date.today() - start).days + 1)
        result = self.fetch_crypto_history(ident, days=min(days, 3650))
        bars = [b for b in result.records
                if (start is None or b["date"] >= start)
                and (end is None or b["date"] <= end)]
        if not bars:
            raise DataUnavailable(f"{self.name}: no prices for {ident} in range")
        return self.result(Capability.PRICES_EOD, bars, as_of=bars[-1]["date"],
                           missing=("open", "high", "low"), partial=True)

    # -- coin detail -------------------------------------------------------
    def fetch_coin_detail(self, coin_id: str) -> ProviderResult:
        url = f"{self.base_url}/coins/{coin_id}"
        params = {"localization": "false", "tickers": "true", "market_data": "true",
                  "community_data": "true", "developer_data": "true",
                  "sparkline": "false"}
        payload = self.request_json(url, params=params, cache_key=f"coin:{coin_id}",
                                    ttl_seconds=self.ttl_market)
        dev = payload.get("developer_data") or {}
        community = payload.get("community_data") or {}
        market = payload.get("market_data") or {}
        tickers = payload.get("tickers") or []
        exchanges = {t.get("market", {}).get("name") for t in tickers if t.get("market")}
        categories = [c.lower() for c in (payload.get("categories") or []) if c]
        record = {
            "coingecko_id": payload.get("id"),
            "symbol": (payload.get("symbol") or "").upper(),
            "name": payload.get("name"),
            "categories": categories,
            "is_stablecoin": any(c in STABLECOIN_CATEGORIES for c in categories),
            "chain": (payload.get("asset_platform_id")
                      or ("native" if payload.get("platforms") == {} else None)),
            "platforms": list((payload.get("platforms") or {}).keys()),
            "genesis_date": payload.get("genesis_date"),
            "market_cap_rank": payload.get("market_cap_rank"),
            "developer_stars": dev.get("stars"),
            "developer_forks": dev.get("forks"),
            "developer_commits_4w": dev.get("commit_count_4_weeks"),
            "developer_contributors": dev.get("pull_request_contributors"),
            "community_twitter": community.get("twitter_followers"),
            "exchange_count": len({e for e in exchanges if e}),
            "trust_score_high_count": sum(
                1 for t in tickers if t.get("trust_score") == "green"),
            "circulating_supply": market.get("circulating_supply"),
            "total_supply": market.get("total_supply"),
            "max_supply": market.get("max_supply"),
            "market_cap_usd": (market.get("market_cap") or {}).get("usd"),
            "fully_diluted_valuation_usd": (
                market.get("fully_diluted_valuation") or {}).get("usd"),
            "volume_24h_usd": (market.get("total_volume") or {}).get("usd"),
            "price_usd": (market.get("current_price") or {}).get("usd"),
        }
        return self.result(Capability.CRYPTO_MARKET, [record], url=url,
                           missing=("tvl", "active_addresses", "unlock_schedule",
                                    "holder_concentration"),
                           partial=True)
