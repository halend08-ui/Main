"""Crypto-native analysis.

Traditional equity valuation does not transfer to tokens: there is no equity
claim, no residual cash flow to owners in most cases, and supply schedules are
policy choices rather than accounting facts. This module therefore models what
actually drives token risk and return:

* **Supply**: circulating vs total vs max, emission rate, unlock overhang.
* **Demand/usage**: fees, active addresses, transactions, TVL -- when reliable
  data exists, and explicitly "unavailable" when it does not.
* **Market structure**: liquidity depth, turnover, venue breadth, concentration.
* **Fragility**: drawdown behaviour, correlation to BTC, regulatory exposure.

Every metric here can be absent. Absence lowers confidence; it never becomes a
zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

import numpy as np

from research_engine.core.numeric import (clamp, is_finite, mean, median,
                                          pct_change, safe_div)
from research_engine.core.series import PriceSeries, aligned_returns
from research_engine.core.types import ClaimType, DataQuality, Evidence, Metric


@dataclass
class CryptoSnapshot:
    symbol: str
    as_of: date
    metrics: dict[str, Metric] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    def set(self, name: str, value: float | None, unit: str = "",
            quality: DataQuality = DataQuality.FAIR, **detail: Any) -> None:
        self.metrics[name] = Metric(name, value, unit, quality, detail=detail)
        if value is None:
            self.unavailable.append(name)

    def value(self, name: str) -> float | None:
        metric = self.metrics.get(name)
        return metric.value if metric else None

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "as_of": self.as_of.isoformat(),
                "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
                "risks": self.risks, "unavailable": sorted(set(self.unavailable)),
                "evidence": [e.to_dict() for e in self.evidence]}


# ------------------------------------------------------------ tokenomics ---
def supply_metrics(*, circulating: float | None, total: float | None,
                   max_supply: float | None, market_cap: float | None,
                   fdv: float | None) -> dict[str, float | None]:
    """Dilution arithmetic. ``mcap_to_fdv`` below ~0.5 means most of the supply
    has yet to hit the market -- future sellers today's price does not reflect."""
    float_ratio = safe_div(circulating, max_supply or total)
    return {
        "circulating_supply": circulating,
        "total_supply": total,
        "max_supply": max_supply,
        "float_ratio": float_ratio,
        "mcap_to_fdv": safe_div(market_cap, fdv),
        "implied_dilution": (1.0 - float_ratio) if float_ratio is not None else None,
    }


def emission_rate(supply_history: Sequence[tuple[date, float]]) -> float | None:
    """Annualised circulating-supply growth from observed history."""
    points = [(d, v) for d, v in supply_history if is_finite(v) and v > 0]
    if len(points) < 30:
        return None
    (d0, v0), (d1, v1) = points[0], points[-1]
    years = (d1 - d0).days / 365.25
    if years < 0.25 or v0 <= 0:
        return None
    return (v1 / v0) ** (1.0 / years) - 1.0


def unlock_overhang(unlocks: Sequence[Mapping[str, Any]], *, as_of: date,
                    circulating: float | None,
                    daily_volume_usd: float | None = None,
                    price: float | None = None,
                    horizon_days: int = 180) -> dict[str, Any]:
    """Quantify scheduled supply increases and how many days of volume they are.

    Absence of unlock data is reported as unknown -- many tokens have vesting
    schedules that no free API exposes, and treating "unknown" as "none" is the
    exact error that gets investors hurt.
    """
    if not unlocks:
        return {"known": False, "tokens": None, "pct_of_circulating": None,
                "days_of_volume": None,
                "note": "no unlock schedule available from configured providers; "
                        "treat supply overhang as unknown, not as zero"}
    horizon_end = as_of + timedelta(days=horizon_days)
    upcoming = [u for u in unlocks
                if u.get("unlock_date") and as_of <= _as_date(u["unlock_date"]) <= horizon_end]
    tokens = sum(float(u.get("tokens") or 0.0) for u in upcoming)
    pct = safe_div(tokens, circulating)
    days_of_volume = None
    if price and daily_volume_usd and daily_volume_usd > 0:
        days_of_volume = (tokens * price) / daily_volume_usd
    return {"known": True, "events": len(upcoming), "tokens": tokens,
            "pct_of_circulating": pct, "days_of_volume": days_of_volume,
            "next_unlock": min((str(_as_date(u["unlock_date"])) for u in upcoming),
                               default=None)}


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# ------------------------------------------------------------- liquidity ---
def liquidity_metrics(*, market_cap: float | None, volume_24h: float | None,
                      exchange_count: int | None = None,
                      series: PriceSeries | None = None) -> dict[str, float | None]:
    """Turnover, volume stability and venue breadth.

    Turnover (volume / market cap) is the single most useful liquidity screen
    for tokens: it separates assets you can exit from assets that merely have a
    quoted price.
    """
    out: dict[str, float | None] = {
        "volume_24h_usd": volume_24h,
        "turnover": safe_div(volume_24h, market_cap),
        "exchange_count": float(exchange_count) if exchange_count is not None else None,
        "volume_stability": None,
        "amihud_illiquidity": None,
    }
    if series is not None and len(series) > 30:
        vol = series.volume[-30:]
        finite = vol[np.isfinite(vol)]
        if finite.size >= 20 and finite.mean() > 0:
            out["volume_stability"] = float(
                clamp(1.0 - finite.std(ddof=1) / finite.mean(), 0.0, 1.0))
        # Amihud: average |return| per dollar of volume -- higher = more impact
        returns = series.returns()[-30:]
        dollar_volume = (series.close * series.volume)[-30:]
        pairs = [(abs(r), dv) for r, dv in zip(returns, dollar_volume[1:])
                 if is_finite(r) and is_finite(dv) and dv > 0]
        if len(pairs) >= 20:
            out["amihud_illiquidity"] = float(np.mean([r / dv for r, dv in pairs]) * 1e9)
    return out


# --------------------------------------------------------------- network ---
def network_metrics(*, active_addresses: Sequence[tuple[date, float]] = (),
                    transactions: Sequence[tuple[date, float]] = (),
                    fees: Sequence[tuple[date, float]] = (),
                    tvl: Sequence[tuple[date, float]] = (),
                    market_cap: float | None = None) -> dict[str, float | None]:
    """Usage trends. Every series is optional and reported as None when absent."""
    def trend(points: Sequence[tuple[date, float]], window: int = 30) -> float | None:
        values = [v for _, v in points if is_finite(v)]
        if len(values) < window * 2:
            return None
        recent = mean(values[-window:])
        prior = mean(values[-2 * window:-window])
        return pct_change(recent, prior)

    def latest(points: Sequence[tuple[date, float]]) -> float | None:
        values = [v for _, v in points if is_finite(v)]
        return values[-1] if values else None

    annualised_fees = None
    fee_values = [v for _, v in fees if is_finite(v)]
    if len(fee_values) >= 30:
        annualised_fees = float(mean(fee_values[-30:]) or 0.0) * 365

    return {
        "active_addresses": latest(active_addresses),
        "active_addresses_trend_30d": trend(active_addresses),
        "transactions": latest(transactions),
        "transactions_trend_30d": trend(transactions),
        "fees_annualised": annualised_fees,
        # The closest thing to a P/S ratio in crypto: market cap per unit of
        # annualised protocol revenue. Meaningful only where fees are real.
        "mcap_to_fees": safe_div(market_cap, annualised_fees),
        "tvl": latest(tvl),
        "tvl_trend_30d": trend(tvl),
        "mcap_to_tvl": safe_div(market_cap, latest(tvl)),
    }


# ------------------------------------------------------------------ risk ---
CRYPTO_RISK_FACTORS = (
    "tokenomics", "liquidity", "centralization", "smart_contract",
    "regulatory", "narrative", "unlock", "volatility",
)


def risk_assessment(snapshot: CryptoSnapshot, *,
                    series: PriceSeries | None = None,
                    btc_series: PriceSeries | None = None,
                    quality_grade: str | None = None,
                    chain: str | None = None,
                    category_tags: Sequence[str] = ()) -> dict[str, Any]:
    """Score each crypto-specific risk 0..1 (higher = riskier), with reasons.

    Unknown inputs produce ``None`` for that factor plus an explicit note; the
    overall score is computed only over what is actually known, and the caller
    is told how much was known.
    """
    factors: dict[str, float | None] = {k: None for k in CRYPTO_RISK_FACTORS}
    reasons: dict[str, list[str]] = {k: [] for k in CRYPTO_RISK_FACTORS}

    # -- tokenomics / unlock ----------------------------------------------
    float_ratio = snapshot.value("float_ratio")
    mcap_fdv = snapshot.value("mcap_to_fdv")
    emission = snapshot.value("emission_rate")
    tokenomics_points: list[float] = []
    if mcap_fdv is not None:
        tokenomics_points.append(clamp(1.0 - mcap_fdv, 0.0, 1.0))
        if mcap_fdv < 0.5:
            reasons["tokenomics"].append(
                f"only {mcap_fdv:.0%} of fully diluted value is circulating")
    if emission is not None:
        tokenomics_points.append(clamp(emission / 0.5, 0.0, 1.0))
        if emission > 0.15:
            reasons["tokenomics"].append(f"supply inflating {emission:.0%} per year")
    if tokenomics_points:
        factors["tokenomics"] = float(mean(tokenomics_points) or 0.0)

    unlock_pct = snapshot.value("unlock_pct_180d")
    if unlock_pct is not None:
        factors["unlock"] = clamp(unlock_pct / 0.25, 0.0, 1.0)
        if unlock_pct > 0.05:
            reasons["unlock"].append(
                f"{unlock_pct:.1%} of circulating supply unlocks within 180 days")
    else:
        reasons["unlock"].append("unlock schedule unavailable: overhang unknown")

    # -- liquidity ---------------------------------------------------------
    turnover = snapshot.value("turnover")
    venues = snapshot.value("exchange_count")
    liquidity_points: list[float] = []
    if turnover is not None:
        liquidity_points.append(clamp(1.0 - turnover / 0.10, 0.0, 1.0))
        if turnover < 0.01:
            reasons["liquidity"].append(
                f"daily volume is only {turnover:.1%} of market cap")
    if venues is not None:
        liquidity_points.append(clamp(1.0 - venues / 15.0, 0.0, 1.0))
        if venues < 3:
            reasons["liquidity"].append(f"listed on only {int(venues)} tracked venues")
    if liquidity_points:
        factors["liquidity"] = float(mean(liquidity_points) or 0.0)

    # -- volatility / drawdown --------------------------------------------
    if series is not None and len(series) > 90:
        returns = series.returns()
        finite = returns[np.isfinite(returns)]
        if finite.size >= 60:
            annual_vol = float(finite.std(ddof=1) * np.sqrt(series.periods_per_year))
            factors["volatility"] = clamp(annual_vol / 2.0, 0.0, 1.0)
            if annual_vol > 1.0:
                reasons["volatility"].append(
                    f"annualised volatility of {annual_vol:.0%}")
        px = series.adj_close
        peak = np.maximum.accumulate(px)
        dd = float(np.min(px / peak - 1.0))
        if dd < -0.8:
            reasons["volatility"].append(
                f"has previously fallen {abs(dd):.0%} from its peak")

    # -- narrative / correlation ------------------------------------------
    if series is not None and btc_series is not None:
        ra, rb = aligned_returns(series, btc_series)
        if ra.size >= 60:
            corr = float(np.corrcoef(ra, rb)[0, 1])
            snapshot.set("btc_correlation", corr)
            if corr > 0.85:
                factors["narrative"] = 0.6
                reasons["narrative"].append(
                    f"{corr:.0%} correlated with bitcoin: little independent thesis")
            elif corr < 0.3:
                factors["narrative"] = 0.4
                reasons["narrative"].append(
                    "low correlation with bitcoin: idiosyncratic, thinner support")
            else:
                factors["narrative"] = 0.45

    # -- centralization / smart contract / regulatory ---------------------
    tags = {t.lower() for t in category_tags}
    if chain and chain not in ("native", "bitcoin", "ethereum"):
        factors["smart_contract"] = 0.55
        reasons["smart_contract"].append(
            f"token deployed on {chain}: inherits that chain's contract and bridge risk")
    elif chain in ("native", "bitcoin"):
        factors["smart_contract"] = 0.2
    if tags & {"defi", "yield farming", "lending", "derivatives"}:
        factors["smart_contract"] = max(factors["smart_contract"] or 0.0, 0.7)
        reasons["smart_contract"].append("DeFi protocol: exploit risk is material")
    if tags & {"privacy", "gambling", "meme"}:
        factors["regulatory"] = 0.75
        reasons["regulatory"].append(
            "category has elevated regulatory or enforcement exposure")
    elif tags:
        factors["regulatory"] = 0.4

    if quality_grade in ("speculative", None):
        factors["centralization"] = 0.6
        reasons["centralization"].append(
            "holder concentration data unavailable; small-cap tokens are "
            "typically concentrated")

    known = {k: v for k, v in factors.items() if v is not None}
    overall = float(mean(known.values())) if known else None
    return {
        "factors": factors,
        "reasons": {k: v for k, v in reasons.items() if v},
        "overall": overall,
        "coverage": len(known) / len(CRYPTO_RISK_FACTORS),
        "unknown_factors": [k for k, v in factors.items() if v is None],
    }


# -------------------------------------------------------------- snapshot ---
def build_snapshot(symbol: str, as_of: date, *, market: Mapping[str, Any],
                   series: PriceSeries | None = None,
                   btc_series: PriceSeries | None = None,
                   supply_history: Sequence[tuple[date, float]] = (),
                   unlocks: Sequence[Mapping[str, Any]] = (),
                   onchain: Mapping[str, Sequence[tuple[date, float]]] | None = None,
                   quality_grade: str | None = None,
                   category_tags: Sequence[str] = ()) -> CryptoSnapshot:
    snap = CryptoSnapshot(symbol=symbol, as_of=as_of)
    onchain = onchain or {}
    market_cap = market.get("market_cap_usd")
    price = market.get("price_usd")

    for key, value in supply_metrics(
            circulating=market.get("circulating_supply"),
            total=market.get("total_supply"), max_supply=market.get("max_supply"),
            market_cap=market_cap,
            fdv=market.get("fully_diluted_valuation_usd")).items():
        snap.set(key, value)
    snap.set("emission_rate", emission_rate(supply_history))

    overhang = unlock_overhang(unlocks, as_of=as_of,
                               circulating=market.get("circulating_supply"),
                               daily_volume_usd=market.get("volume_24h_usd"),
                               price=price)
    snap.set("unlock_pct_180d", overhang.get("pct_of_circulating"),
             detail=overhang)
    if not overhang["known"]:
        snap.risks.append(overhang["note"])

    for key, value in liquidity_metrics(
            market_cap=market_cap, volume_24h=market.get("volume_24h_usd"),
            exchange_count=market.get("exchange_count"), series=series).items():
        snap.set(key, value)

    for key, value in network_metrics(
            active_addresses=onchain.get("active_addresses", ()),
            transactions=onchain.get("transactions", ()),
            fees=onchain.get("fees", ()), tvl=onchain.get("tvl", ()),
            market_cap=market_cap).items():
        snap.set(key, value)

    risk = risk_assessment(snap, series=series, btc_series=btc_series,
                           quality_grade=quality_grade,
                           chain=market.get("chain"), category_tags=category_tags)
    snap.metrics["risk_overall"] = Metric("risk_overall", risk["overall"], "",
                                          DataQuality.FAIR, detail=risk)
    for factor_reasons in risk["reasons"].values():
        snap.risks.extend(factor_reasons)
    snap.evidence.extend(_crypto_evidence(snap, risk))
    return snap


def _crypto_evidence(snap: CryptoSnapshot, risk: Mapping[str, Any]) -> list[Evidence]:
    out: list[Evidence] = []
    mcap_fdv = snap.value("mcap_to_fdv")
    if mcap_fdv is not None:
        out.append(Evidence(
            label="Supply overhang",
            detail=f"market cap is {mcap_fdv:.0%} of fully diluted value",
            direction=clamp((mcap_fdv - 0.6) * 2.5, -1, 1), weight=0.7,
            claim_type=ClaimType.OBSERVATION, quality=DataQuality.GOOD,
            sources=("market data provider",)))
    turnover = snap.value("turnover")
    if turnover is not None:
        out.append(Evidence(
            label="Liquidity",
            detail=f"daily volume equals {turnover:.1%} of market cap",
            direction=clamp((turnover - 0.02) * 20, -1, 1), weight=0.6,
            claim_type=ClaimType.OBSERVATION, quality=DataQuality.GOOD,
            sources=("market data provider",)))
    fee_ratio = snap.value("mcap_to_fees")
    if fee_ratio is not None:
        out.append(Evidence(
            label="Protocol revenue multiple",
            detail=f"market cap is {fee_ratio:.0f}x annualised fees",
            direction=clamp((60 - fee_ratio) / 60, -1, 1), weight=0.6,
            claim_type=ClaimType.OBSERVATION, quality=DataQuality.FAIR,
            sources=("on-chain data",)))
    coverage = risk.get("coverage", 0.0)
    if coverage < 0.6:
        out.append(Evidence(
            label="Incomplete risk picture",
            detail=f"only {coverage:.0%} of crypto risk factors could be measured "
                   f"({', '.join(risk.get('unknown_factors', [])[:4])} unknown)",
            direction=-0.3, weight=0.5, claim_type=ClaimType.ASSUMPTION,
            quality=DataQuality.POOR, sources=()))
    return out
