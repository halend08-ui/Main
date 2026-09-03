"""Discovery: finding assets the system is not already watching.

A research system that only re-examines the same famous names will never find
anything. The discovery engine looks for assets that are *newly interesting*
rather than merely large:

* recent listings and IPOs (with an explicit warning about short history);
* unusual volume or price behaviour against the asset's own baseline;
* accelerating fundamentals;
* clustered insider buying;
* crypto assets with rapidly growing usage or liquidity;
* assets whose sector peers are moving without them.

Everything discovered enters the research queue with a reason. Nothing
discovered is ever a recommendation -- discovery answers "what deserves a
look?", not "what should I own?".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from research_engine.analysis import anomaly as AN
from research_engine.core.logging import get_logger
from research_engine.core.numeric import clamp, is_finite, median, pct_change
from research_engine.core.series import PriceSeries

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Discovery:
    symbol: str
    asset_class: str
    reason: str
    trigger: str
    score: float                     # 0..1 interest level
    detail: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "asset_class": self.asset_class,
                "reason": self.reason, "trigger": self.trigger,
                "score": round(self.score, 3), "detail": dict(self.detail),
                "warnings": list(self.warnings),
                "status": "research candidate, not a recommendation"}


def recent_listings(assets: Sequence[Mapping[str, Any]], *, as_of: date,
                    window_days: int = 365) -> list[Discovery]:
    """New listings, flagged for their thin history rather than hidden."""
    out: list[Discovery] = []
    for asset in assets:
        listed = asset.get("listed_date") or asset.get("first_price_date")
        if not listed:
            continue
        listed_date = listed if isinstance(listed, date) else date.fromisoformat(str(listed)[:10])
        age = (as_of - listed_date).days
        if 0 <= age <= window_days:
            out.append(Discovery(
                symbol=str(asset["symbol"]), asset_class=str(asset.get("asset_class", "equity")),
                reason=f"listed {age} days ago", trigger="new_listing",
                score=clamp(1.0 - age / window_days, 0.3, 1.0),
                detail={"listed_date": listed_date.isoformat(), "age_days": age},
                warnings=("limited price history: statistics and valuation ranges "
                          "will be wide, and no full-cycle behaviour is observable",
                          "recent listings often face lock-up expiries that add "
                          "supply pressure")))
    return out


def unusual_activity(series_by_symbol: Mapping[str, PriceSeries], *,
                     asset_class: str = "equity",
                     min_severity: float = 0.4) -> list[Discovery]:
    """Volume, price and volatility anomalies across the universe."""
    out: list[Discovery] = []
    for symbol, series in series_by_symbol.items():
        anomalies = AN.scan(series)
        if not anomalies:
            continue
        top = anomalies[0]
        if top.severity < min_severity:
            continue
        out.append(Discovery(
            symbol=symbol, asset_class=asset_class, reason=top.detail,
            trigger=top.kind, score=AN.research_priority(anomalies),
            detail={"anomalies": [a.to_dict() for a in anomalies[:3]]}))
    return out


def accelerating_fundamentals(growth_by_symbol: Mapping[str, Sequence[float]], *,
                              asset_class: str = "equity",
                              min_acceleration: float = 0.08) -> list[Discovery]:
    """Companies whose growth rate is itself increasing.

    Acceleration is more informative than level: a business going from 5% to 20%
    growth is usually a bigger change in outlook than one steady at 25%.
    """
    out: list[Discovery] = []
    for symbol, history in growth_by_symbol.items():
        values = [v for v in history if is_finite(v)]
        if len(values) < 3:
            continue
        rates = [pct_change(values[i], values[i - 1]) for i in range(1, len(values))]
        rates = [r for r in rates if r is not None]
        if len(rates) < 2:
            continue
        acceleration = rates[-1] - rates[-2]
        if acceleration < min_acceleration:
            continue
        out.append(Discovery(
            symbol=symbol, asset_class=asset_class,
            reason=f"growth accelerated from {rates[-2]:.0%} to {rates[-1]:.0%}",
            trigger="fundamental_acceleration",
            score=clamp(acceleration / 0.3, 0.3, 1.0),
            detail={"latest_growth": round(rates[-1], 4),
                    "prior_growth": round(rates[-2], 4),
                    "acceleration": round(acceleration, 4)},
            warnings=("a single accelerating period may reflect an easy "
                      "comparison rather than a durable change",)))
    return out


def insider_buying(transactions_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
                   *, asset_class: str = "equity") -> list[Discovery]:
    out: list[Discovery] = []
    for symbol, transactions in transactions_by_symbol.items():
        cluster = AN.insider_cluster(transactions)
        if cluster is None or cluster.context.get("direction") != "buying":
            continue
        out.append(Discovery(
            symbol=symbol, asset_class=asset_class, reason=cluster.detail,
            trigger="insider_cluster", score=cluster.severity,
            detail=dict(cluster.context),
            warnings=("insider purchases are a weak, lagging signal and are "
                      "sometimes scheduled rather than discretionary",)))
    return out


def crypto_traction(markets: Sequence[Mapping[str, Any]], *,
                    min_volume_growth: float = 1.0,
                    min_market_cap: float = 25e6) -> list[Discovery]:
    """Tokens whose liquidity or usage is growing quickly from a real base."""
    out: list[Discovery] = []
    for record in markets:
        market_cap = record.get("market_cap_usd")
        volume = record.get("volume_24h_usd")
        prior_volume = record.get("volume_24h_usd_prior")
        if not market_cap or market_cap < min_market_cap or not volume:
            continue
        growth = pct_change(volume, prior_volume) if prior_volume else None
        if growth is None or growth < min_volume_growth:
            continue
        out.append(Discovery(
            symbol=str(record.get("symbol", "")).upper(), asset_class="crypto",
            reason=f"24h volume up {growth:.0%} on a ${market_cap:,.0f} market cap",
            trigger="crypto_volume_growth", score=clamp(growth / 3.0, 0.3, 1.0),
            detail={"volume_growth": round(growth, 3), "market_cap": market_cap},
            warnings=("volume spikes in tokens are frequently wash trading or "
                      "single-venue listings rather than genuine demand",)))
    return out


def sector_laggards(returns_by_symbol: Mapping[str, float],
                    sectors: Mapping[str, str], *,
                    min_peers: int = 5, min_gap: float = 0.15) -> list[Discovery]:
    """Assets that did not move with their sector -- either an opportunity or a
    signal that something is wrong. Both are worth a look."""
    by_sector: dict[str, list[tuple[str, float]]] = {}
    for symbol, ret in returns_by_symbol.items():
        if not is_finite(ret):
            continue
        by_sector.setdefault(sectors.get(symbol, "Unknown"), []).append((symbol, ret))

    out: list[Discovery] = []
    for sector, members in by_sector.items():
        if len(members) < min_peers or sector == "Unknown":
            continue
        sector_median = median([r for _, r in members])
        if sector_median is None:
            continue
        for symbol, ret in members:
            gap = sector_median - ret
            if gap >= min_gap:
                out.append(Discovery(
                    symbol=symbol, asset_class="equity",
                    reason=f"lagged {sector} peers by {gap:.0%} over the window",
                    trigger="sector_laggard", score=clamp(gap / 0.5, 0.3, 1.0),
                    detail={"sector": sector, "asset_return": round(ret, 4),
                            "sector_median": round(sector_median, 4)},
                    warnings=("underperformance versus peers is as often a "
                              "warning as an opportunity: check for company-"
                              "specific deterioration first",)))
    return out


def deduplicate(discoveries: Iterable[Discovery], *,
                known_symbols: Iterable[str] = (),
                max_results: int = 50) -> list[Discovery]:
    """Merge triggers per symbol, drop already-covered names, cap the output."""
    known = {s.upper() for s in known_symbols}
    best: dict[str, Discovery] = {}
    triggers: dict[str, list[str]] = {}
    for discovery in discoveries:
        symbol = discovery.symbol.upper()
        if symbol in known:
            continue
        triggers.setdefault(symbol, []).append(discovery.trigger)
        current = best.get(symbol)
        if current is None or discovery.score > current.score:
            best[symbol] = discovery

    merged: list[Discovery] = []
    for symbol, discovery in best.items():
        found = triggers[symbol]
        if len(found) > 1:
            discovery = Discovery(
                symbol=discovery.symbol, asset_class=discovery.asset_class,
                reason=discovery.reason + f" (plus {len(found) - 1} other trigger(s))",
                trigger="+".join(sorted(set(found))),
                # Multiple independent triggers are more interesting than one.
                score=clamp(discovery.score * (1 + 0.15 * (len(set(found)) - 1)), 0, 1),
                detail=discovery.detail, warnings=discovery.warnings)
        merged.append(discovery)
    merged.sort(key=lambda d: d.score, reverse=True)
    return merged[:max_results]
