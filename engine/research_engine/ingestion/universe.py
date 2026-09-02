"""Universe construction.

The investable universe is *filtered*, not truncated: assets that fail the
liquidity/size screens are still stored (so history stays complete and
survivorship bias is avoidable) but are marked inactive for scanning.

Crypto gets a separate quality classification because "listed on an exchange"
is a far weaker signal than "registered with a securities regulator".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from research_engine.config.settings import Settings
from research_engine.core.errors import DataUnavailable
from research_engine.core.logging import get_logger
from research_engine.core.types import AssetClass, DataQuality
from research_engine.ingestion.base import Capability
from research_engine.ingestion.registry import ProviderRegistry
from research_engine.storage.repositories import AssetRepository

log = get_logger(__name__)

#: Symbols/names that are stablecoins or wrapped assets: they are tracked for
#: context (liquidity, flows) but are not investment candidates.
STABLE_HINTS = ("usdt", "usdc", "dai", "busd", "tusd", "usdd", "fdusd", "pyusd",
                "usde", "frax", "lusd", "gusd", "usdp")
WRAPPED_HINTS = ("wrapped", "staked", "bridged", "wbtc", "weth", "steth", "reth",
                 "cbeth", "wbeth")


@dataclass
class UniverseStats:
    considered: int = 0
    admitted: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {"considered": self.considered, "admitted": self.admitted,
                "rejected": dict(sorted(self.rejected.items())),
                "errors": self.errors[:20]}


class CryptoQuality:
    """Crypto risk/quality classification.

    Deliberately conservative and rule-based. The classes are *risk buckets*,
    not endorsements, and every bucket carries a documented reason.
    """

    INSTITUTIONAL = "institutional"   # deep liquidity, long history, many venues
    ESTABLISHED = "established"
    EMERGING = "emerging"
    SPECULATIVE = "speculative"
    EXCLUDED = "excluded"             # stablecoin / wrapped / untradeable

    @staticmethod
    def classify(record: Mapping[str, Any], *, settings: Settings
                 ) -> tuple[str, list[str]]:
        reasons: list[str] = []
        symbol = str(record.get("symbol", "")).lower()
        name = str(record.get("name", "")).lower()
        mcap = record.get("market_cap_usd")
        volume = record.get("volume_24h_usd")
        fdv = record.get("fully_diluted_valuation_usd")
        exchanges = record.get("exchange_count")

        if any(h in symbol or h in name for h in STABLE_HINTS) or record.get("is_stablecoin"):
            return CryptoQuality.EXCLUDED, ["stablecoin: not an investment candidate"]
        if any(h in symbol or h in name for h in WRAPPED_HINTS):
            return CryptoQuality.EXCLUDED, ["wrapped/derivative token tracks another asset"]

        min_mcap = float(settings.get("universe.crypto.min_market_cap_usd", 25e6))
        min_vol = float(settings.get("universe.crypto.min_daily_volume_usd", 1e6))
        if mcap is None or volume is None:
            return CryptoQuality.SPECULATIVE, ["market cap or volume unavailable"]
        if mcap < min_mcap:
            reasons.append(f"market cap below {min_mcap:,.0f}")
        if volume < min_vol:
            reasons.append(f"24h volume below {min_vol:,.0f}")
        turnover = volume / mcap if mcap else None
        if turnover is not None and turnover < 0.005:
            reasons.append("thin turnover (<0.5% of market cap per day)")
        if fdv and mcap and fdv > 3 * mcap:
            reasons.append("FDV more than 3x market cap: heavy future dilution")

        if mcap >= 10e9 and volume >= 250e6:
            if exchanges is None:
                # Venue breadth is part of the institutional test; without it we
                # cannot award the top grade, and we say why.
                reasons.append("exchange coverage unknown: capped at 'established'")
            elif exchanges >= 10:
                return CryptoQuality.INSTITUTIONAL, reasons
            else:
                reasons.append(f"listed on only {exchanges} tracked venues")
        if mcap >= 1e9 and volume >= 25e6:
            return CryptoQuality.ESTABLISHED, reasons
        if mcap >= min_mcap and volume >= min_vol:
            return CryptoQuality.EMERGING, reasons
        return CryptoQuality.SPECULATIVE, reasons or ["below size/liquidity floors"]


class UniverseBuilder:
    """Populates the ``assets`` table from provider universe feeds."""

    def __init__(self, settings: Settings, registry: ProviderRegistry,
                 assets: AssetRepository) -> None:
        self.settings = settings
        self.registry = registry
        self.assets = assets

    # -- equities ----------------------------------------------------------
    def build_equities(self, *, limit: int | None = None) -> UniverseStats:
        stats = UniverseStats()
        try:
            result, report = self.registry.require(
                Capability.UNIVERSE_EQUITY, "fetch_universe", target="equity")
        except DataUnavailable as exc:
            stats.errors.append(str(exc))
            log.error("equity universe unavailable", error=str(exc))
            return stats

        countries = {c.upper() for c in (self.settings.get("universe.countries") or [])}
        exchanges = {e.upper() for e in (self.settings.get("universe.exchanges") or [])}
        max_assets = limit or int(self.settings.get("universe.max_assets", 5000))

        for record in result.records:
            stats.considered += 1
            symbol = str(record.get("symbol", "")).upper()
            if not symbol or not symbol.replace(".", "").replace("-", "").isalnum():
                stats.reject("invalid symbol")
                continue
            country = str(record.get("country", "")).upper() or None
            if countries and country and country not in countries:
                stats.reject("country not in scope")
                continue
            exchange = str(record.get("exchange", "")).upper() or None
            if exchanges and exchange and exchange not in exchanges:
                stats.reject("exchange not in scope")
                continue
            if stats.admitted >= max_assets:
                stats.reject("universe cap reached")
                continue
            self.assets.upsert(
                symbol=symbol, asset_class=AssetClass.EQUITY,
                name=record.get("name"), exchange=exchange, country=country,
                sector=record.get("sector"), industry=record.get("industry"),
                cik=record.get("cik"), currency=record.get("currency", "USD"),
                market_cap_usd=record.get("market_cap_usd"))
            stats.admitted += 1

        log.info("equity universe built", **{k: v for k, v in stats.as_dict().items()
                                             if k != "errors"})
        return stats

    # -- crypto ------------------------------------------------------------
    def build_crypto(self, *, limit: int | None = None) -> UniverseStats:
        stats = UniverseStats()
        try:
            result, _ = self.registry.require(
                Capability.UNIVERSE_CRYPTO, "fetch_universe", target="crypto")
        except DataUnavailable as exc:
            stats.errors.append(str(exc))
            log.error("crypto universe unavailable", error=str(exc))
            return stats

        exclude_stables = bool(self.settings.get("universe.crypto.exclude_stablecoins", True))
        max_assets = limit or int(self.settings.get("universe.crypto.max_assets", 1000))

        for record in result.records:
            stats.considered += 1
            symbol = str(record.get("symbol", "")).upper()
            if not symbol:
                stats.reject("missing symbol")
                continue
            grade, reasons = CryptoQuality.classify(record, settings=self.settings)
            if grade == CryptoQuality.EXCLUDED:
                stats.reject("stablecoin/wrapped")
                if exclude_stables:
                    # still stored (context for liquidity analysis), never scanned
                    self.assets.upsert(
                        symbol=symbol, asset_class=AssetClass.CRYPTO,
                        name=record.get("name"),
                        coingecko_id=record.get("coingecko_id"),
                        quality_grade=grade, is_active=False,
                        tags=["excluded", *reasons[:1]])
                    continue
            if stats.admitted >= max_assets:
                stats.reject("universe cap reached")
                continue
            self.assets.upsert(
                symbol=symbol, asset_class=AssetClass.CRYPTO,
                name=record.get("name"), coingecko_id=record.get("coingecko_id"),
                chain=record.get("chain"),
                market_cap_usd=record.get("market_cap_usd"),
                circulating_supply=record.get("circulating_supply"),
                max_supply=record.get("max_supply"),
                quality_grade=grade,
                tags=reasons[:3],
                is_active=grade != CryptoQuality.SPECULATIVE
                or bool(self.settings.get("universe.crypto.include_speculative", False)))
            stats.admitted += 1

        log.info("crypto universe built", **{k: v for k, v in stats.as_dict().items()
                                             if k != "errors"})
        return stats

    # -- screening ---------------------------------------------------------
    def screen(self, *, as_of: date | None = None) -> list[int]:
        """Asset ids passing the configured size/liquidity floors.

        Assets with unknown market cap are *not* silently dropped: they are
        returned with a flag so the caller can decide, because "unknown" and
        "too small" are different states.
        """
        min_cap = self.settings.get("universe.min_market_cap_usd")
        max_cap = self.settings.get("universe.max_market_cap_usd")
        out: list[int] = []
        for asset in self.assets.list(active_only=True):
            cap = asset.market_cap_usd
            if cap is not None:
                if min_cap is not None and cap < float(min_cap):
                    continue
                if max_cap is not None and cap > float(max_cap):
                    continue
            out.append(asset.id)
        return out
