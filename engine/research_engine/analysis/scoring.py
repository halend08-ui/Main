"""Multi-factor scoring.

Rules that keep the score honest:

1. **Not an average of everything.** Factors carry configured weights, and the
   weights are validated historically by the learning layer.
2. **Missing factors are not zero.** A factor with no data is *excluded* and the
   remaining weights are renormalised; the fraction of weight actually covered
   is reported and caps the score's credibility.
3. **Non-linear where reality is non-linear.** Cheapness helps until it signals
   distress; growth helps until it is implausible. Sub-scores use explicit
   piecewise maps, not raw z-scores.
4. **Cross-sectional context where it exists.** A P/E means little in isolation;
   percentile ranks against a peer set are used when the set is large enough.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.numeric import clamp, is_finite, linear_score, percentile_rank
from research_engine.core.types import (DataQuality, OpportunityTier)


@dataclass(frozen=True, slots=True)
class FactorScore:
    """One factor's contribution, with its inputs kept for explainability."""

    name: str
    score: float | None            # 0..100, or None when not computable
    weight: float
    quality: DataQuality
    detail: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.score is not None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name,
                "score": None if self.score is None else round(self.score, 1),
                "weight": round(self.weight, 4), "quality": self.quality.value,
                "reason": self.reason, "detail": dict(self.detail)}


@dataclass
class CompositeScore:
    total: float | None
    tier: OpportunityTier
    factors: list[FactorScore]
    coverage: float                 # fraction of configured weight with data
    quality: DataQuality
    notes: list[str] = field(default_factory=list)

    def top_positive(self, n: int = 3) -> list[FactorScore]:
        available = [f for f in self.factors if f.available]
        return sorted(available, key=lambda f: (f.score or 0) * f.weight,
                      reverse=True)[:n]

    def top_negative(self, n: int = 3) -> list[FactorScore]:
        available = [f for f in self.factors if f.available]
        return sorted(available, key=lambda f: (100 - (f.score or 0)) * f.weight,
                      reverse=True)[:n]

    def missing_factors(self) -> list[str]:
        return [f.name for f in self.factors if not f.available]

    def to_dict(self) -> dict[str, Any]:
        return {"total": None if self.total is None else round(self.total, 1),
                "tier": self.tier.value, "coverage": round(self.coverage, 3),
                "quality": self.quality.value,
                "factors": [f.to_dict() for f in self.factors],
                "missing": self.missing_factors(), "notes": self.notes}


# --------------------------------------------------------- sub-scorers -----
def score_growth(revenue_cagr_3y: float | None, consistency: float | None,
                 acceleration: float | None) -> tuple[float | None, dict[str, Any]]:
    """Growth quality, not just growth rate.

    Above ~45% CAGR the marginal credit stops: such rates are rarely sustained
    and usually come with valuation and execution risk priced elsewhere.
    """
    if revenue_cagr_3y is None:
        return None, {"reason": "no multi-year revenue history"}
    rate = revenue_cagr_3y
    if rate <= -0.10:
        base = 5.0
    elif rate <= 0.0:
        base = 5.0 + 20.0 * (rate + 0.10) / 0.10
    elif rate <= 0.45:
        base = 25.0 + 60.0 * (rate / 0.45)
    else:
        base = 85.0 + 5.0 * clamp((rate - 0.45) / 0.55, 0, 1)
    detail: dict[str, Any] = {"revenue_cagr_3y": round(rate, 4), "base": round(base, 1)}
    if consistency is not None:
        adjustment = (consistency - 0.5) * 16.0
        base += adjustment
        detail["consistency_adjustment"] = round(adjustment, 1)
    if acceleration is not None:
        adjustment = clamp(acceleration * 40.0, -8.0, 8.0)
        base += adjustment
        detail["acceleration_adjustment"] = round(adjustment, 1)
    return clamp(base, 0.0, 100.0), detail


def score_valuation(*, fcf_yield: float | None, earnings_yield: float | None,
                    pe: float | None, ev_ebitda: float | None,
                    peg: float | None,
                    history_percentile: float | None = None,
                    peer_percentile: float | None = None,
                    dcf_upside: float | None = None
                    ) -> tuple[float | None, dict[str, Any]]:
    """Cheapness, with a deliberate penalty for *implausible* cheapness.

    A 40% free-cash-flow yield is almost never a bargain; it usually means the
    market expects the cash flow to disappear. The curve therefore rises to a
    plateau and then falls back.
    """
    parts: list[tuple[float, float]] = []      # (score, weight)
    detail: dict[str, Any] = {}

    if fcf_yield is not None:
        if fcf_yield <= 0:
            s = 15.0
        elif fcf_yield <= 0.12:
            s = 25.0 + 65.0 * (fcf_yield / 0.12)
        elif fcf_yield <= 0.25:
            s = 90.0
        else:
            s = 90.0 - 40.0 * clamp((fcf_yield - 0.25) / 0.25, 0, 1)
        parts.append((s, 1.4))
        detail["fcf_yield"] = round(fcf_yield, 4)

    if earnings_yield is not None and fcf_yield is None:
        s = clamp(20.0 + earnings_yield * 500.0, 0.0, 95.0)
        parts.append((s, 1.0))
        detail["earnings_yield"] = round(earnings_yield, 4)

    if pe is not None and 0 < pe < 200:
        s = clamp(100.0 - (pe - 8.0) * 2.2, 5.0, 95.0)
        parts.append((s, 0.8))
        detail["pe"] = round(pe, 2)

    if ev_ebitda is not None and 0 < ev_ebitda < 80:
        s = clamp(100.0 - (ev_ebitda - 5.0) * 4.0, 5.0, 95.0)
        parts.append((s, 0.9))
        detail["ev_ebitda"] = round(ev_ebitda, 2)

    if peg is not None and 0 < peg < 10:
        s = clamp(100.0 - (peg - 0.8) * 45.0, 5.0, 95.0)
        parts.append((s, 0.7))
        detail["peg"] = round(peg, 2)

    if history_percentile is not None:
        s = (1.0 - history_percentile) * 100.0
        parts.append((s, 0.8))
        detail["own_history_percentile"] = round(history_percentile, 3)

    if peer_percentile is not None:
        s = (1.0 - peer_percentile) * 100.0
        parts.append((s, 0.8))
        detail["peer_percentile"] = round(peer_percentile, 3)

    if dcf_upside is not None:
        s = clamp(50.0 + dcf_upside * 100.0, 0.0, 100.0)
        parts.append((s, 1.2))
        detail["dcf_upside"] = round(dcf_upside, 4)

    if not parts:
        return None, {"reason": "no usable valuation inputs"}
    total_weight = sum(w for _, w in parts)
    score = sum(s * w for s, w in parts) / total_weight
    detail["inputs_used"] = len(parts)
    return clamp(score, 0.0, 100.0), detail


def score_quality(*, roic: float | None, roe: float | None,
                  gross_margin: float | None, fcf_conversion: float | None,
                  margin_trend: float | None) -> tuple[float | None, dict[str, Any]]:
    parts: list[tuple[float, float]] = []
    detail: dict[str, Any] = {}
    if roic is not None:
        parts.append((clamp(15.0 + roic * 300.0, 0.0, 98.0), 1.5))
        detail["roic"] = round(roic, 4)
    if roe is not None and roic is None:
        parts.append((clamp(15.0 + roe * 200.0, 0.0, 95.0), 1.0))
        detail["roe"] = round(roe, 4)
    if gross_margin is not None:
        parts.append((clamp(gross_margin * 130.0, 0.0, 95.0), 0.8))
        detail["gross_margin"] = round(gross_margin, 4)
    if fcf_conversion is not None:
        parts.append((clamp(30.0 + fcf_conversion * 55.0, 0.0, 95.0), 1.0))
        detail["fcf_conversion"] = round(fcf_conversion, 3)
    if margin_trend is not None:
        parts.append((clamp(50.0 + margin_trend * 800.0, 0.0, 100.0), 0.7))
        detail["margin_trend_3y"] = round(margin_trend, 4)
    if not parts:
        return None, {"reason": "no profitability data"}
    total = sum(w for _, w in parts)
    return clamp(sum(s * w for s, w in parts) / total, 0.0, 100.0), detail


def score_financial_health(*, debt_to_equity: float | None,
                           interest_coverage: float | None,
                           current_ratio: float | None,
                           net_debt_to_ebitda: float | None,
                           cash_runway_years: float | None,
                           altman_z: float | None) -> tuple[float | None, dict[str, Any]]:
    parts: list[tuple[float, float]] = []
    detail: dict[str, Any] = {}
    if debt_to_equity is not None:
        parts.append((clamp(95.0 - debt_to_equity * 35.0, 5.0, 95.0), 1.0))
        detail["debt_to_equity"] = round(debt_to_equity, 3)
    if interest_coverage is not None:
        parts.append((clamp(10.0 + interest_coverage * 8.0, 0.0, 95.0), 1.3))
        detail["interest_coverage"] = round(interest_coverage, 2)
    if current_ratio is not None:
        parts.append((clamp(20.0 + (current_ratio - 0.5) * 45.0, 0.0, 95.0), 0.7))
        detail["current_ratio"] = round(current_ratio, 2)
    if net_debt_to_ebitda is not None:
        parts.append((clamp(95.0 - net_debt_to_ebitda * 18.0, 0.0, 95.0), 1.1))
        detail["net_debt_to_ebitda"] = round(net_debt_to_ebitda, 2)
    if cash_runway_years is not None:
        # Only present when the company burns cash -- a hard risk, weighted high.
        parts.append((clamp(cash_runway_years * 22.0, 0.0, 90.0), 1.6))
        detail["cash_runway_years"] = round(cash_runway_years, 2)
    if altman_z is not None:
        parts.append((clamp((altman_z - 1.0) * 22.0, 0.0, 95.0), 1.0))
        detail["altman_z"] = round(altman_z, 2)
    if not parts:
        return None, {"reason": "no balance-sheet data"}
    total = sum(w for _, w in parts)
    return clamp(sum(s * w for s, w in parts) / total, 0.0, 100.0), detail


def score_momentum(*, returns_by_window: Mapping[int, float | None],
                   relative_strength: float | None = None,
                   reversal_1m: float | None = None
                   ) -> tuple[float | None, dict[str, Any]]:
    """Cross-horizon momentum with the standard short-term reversal control.

    The 12-1 convention (skip the most recent month) is used because the last
    month tends to mean-revert; including it dilutes the medium-term signal.
    """
    # keyed by CALENDAR days, so the same weights apply whatever the sampling
    # frequency of the underlying series
    horizons = {30: 0.5, 91: 1.0, 182: 1.2, 365: 1.0}
    parts: list[tuple[float, float]] = []
    detail: dict[str, Any] = {}
    for window, weight in horizons.items():
        value = returns_by_window.get(window)
        if value is None:
            continue
        scaled = clamp(50.0 + value * 120.0, 0.0, 100.0)
        parts.append((scaled, weight))
        detail[f"return_{window}d"] = round(value, 4)
    if relative_strength is not None:
        parts.append((clamp(50.0 + relative_strength * 130.0, 0.0, 100.0), 1.2))
        detail["relative_strength"] = round(relative_strength, 4)
    if reversal_1m is not None and reversal_1m > 0.25:
        parts.append((30.0, 0.6))       # sharp recent spike: fade it slightly
        detail["short_term_reversal_penalty"] = True
    if not parts:
        return None, {"reason": "insufficient price history for momentum"}
    total = sum(w for _, w in parts)
    return clamp(sum(s * w for s, w in parts) / total, 0.0, 100.0), detail


def score_technical(*, price_vs_200dma: float | None, rsi: float | None,
                    macd_hist: float | None, adx: float | None,
                    above_donchian: bool | None) -> tuple[float | None, dict[str, Any]]:
    parts: list[tuple[float, float]] = []
    detail: dict[str, Any] = {}
    if price_vs_200dma is not None:
        parts.append((clamp(50.0 + price_vs_200dma * 200.0, 0.0, 100.0), 1.2))
        detail["price_vs_200dma"] = round(price_vs_200dma, 4)
    if rsi is not None:
        # Mid-range RSI is healthiest; both extremes are penalised.
        s = 100.0 - abs(rsi - 55.0) * 1.6
        parts.append((clamp(s, 0.0, 100.0), 0.7))
        detail["rsi"] = round(rsi, 1)
    if macd_hist is not None:
        parts.append((60.0 if macd_hist > 0 else 40.0, 0.5))
        detail["macd_histogram_positive"] = macd_hist > 0
    if adx is not None:
        parts.append((clamp(30.0 + adx * 1.5, 0.0, 90.0), 0.6))
        detail["adx"] = round(adx, 1)
    if above_donchian is not None:
        parts.append((70.0 if above_donchian else 45.0, 0.6))
        detail["breakout"] = above_donchian
    if not parts:
        return None, {"reason": "no technical inputs"}
    total = sum(w for _, w in parts)
    return clamp(sum(s * w for s, w in parts) / total, 0.0, 100.0), detail


def score_liquidity(*, dollar_volume: float | None, turnover: float | None,
                    market_cap: float | None) -> tuple[float | None, dict[str, Any]]:
    parts: list[tuple[float, float]] = []
    detail: dict[str, Any] = {}
    if dollar_volume is not None and dollar_volume > 0:
        import math
        s = clamp((math.log10(dollar_volume) - 4.0) * 25.0, 0.0, 100.0)
        parts.append((s, 1.2))
        detail["avg_dollar_volume"] = round(dollar_volume)
    if turnover is not None:
        parts.append((clamp(turnover * 1200.0, 0.0, 100.0), 0.8))
        detail["turnover"] = round(turnover, 4)
    if market_cap is not None and market_cap > 0:
        import math
        s = clamp((math.log10(market_cap) - 7.0) * 22.0, 0.0, 100.0)
        parts.append((s, 0.6))
        detail["market_cap"] = round(market_cap)
    if not parts:
        return None, {"reason": "no liquidity data"}
    total = sum(w for _, w in parts)
    return clamp(sum(s * w for s, w in parts) / total, 0.0, 100.0), detail


def score_downside_risk(*, max_drawdown: float | None, volatility: float | None,
                        var_95: float | None, bear_case_return: float | None
                        ) -> tuple[float | None, dict[str, Any]]:
    """Higher score = *less* downside risk, so it aligns with the others."""
    parts: list[tuple[float, float]] = []
    detail: dict[str, Any] = {}
    if max_drawdown is not None:
        parts.append((clamp(100.0 + max_drawdown * 130.0, 0.0, 100.0), 1.2))
        detail["max_drawdown"] = round(max_drawdown, 4)
    if volatility is not None:
        parts.append((clamp(100.0 - volatility * 110.0, 0.0, 100.0), 1.0))
        detail["volatility"] = round(volatility, 4)
    if var_95 is not None:
        parts.append((clamp(100.0 + var_95 * 900.0, 0.0, 100.0), 0.8))
        detail["var_95"] = round(var_95, 4)
    if bear_case_return is not None:
        parts.append((clamp(70.0 + bear_case_return * 130.0, 0.0, 100.0), 1.3))
        detail["bear_case_return"] = round(bear_case_return, 4)
    if not parts:
        return None, {"reason": "no risk statistics"}
    total = sum(w for _, w in parts)
    return clamp(sum(s * w for s, w in parts) / total, 0.0, 100.0), detail


# ------------------------------------------------------------- composite ---
def compose(factors: Sequence[FactorScore], *,
            thresholds: Mapping[str, float],
            max_missing_weight: float = 0.4,
            data_quality: DataQuality = DataQuality.GOOD,
            min_quality_for_top_tier: DataQuality = DataQuality.GOOD
            ) -> CompositeScore:
    """Weighted composite over the factors that actually have data."""
    notes: list[str] = []
    available = [f for f in factors if f.available]
    total_weight = sum(f.weight for f in factors)
    covered_weight = sum(f.weight for f in available)
    coverage = covered_weight / total_weight if total_weight else 0.0

    if not available or coverage <= 0:
        return CompositeScore(None, OpportunityTier.WATCH, list(factors), 0.0,
                              DataQuality.INSUFFICIENT,
                              ["no factors could be computed"])

    if 1.0 - coverage > max_missing_weight:
        notes.append(
            f"only {coverage:.0%} of factor weight had data (minimum "
            f"{1 - max_missing_weight:.0%}); score withheld")
        return CompositeScore(None, OpportunityTier.WATCH, list(factors), coverage,
                              DataQuality.INSUFFICIENT, notes)

    total = sum((f.score or 0.0) * f.weight for f in available) / covered_weight

    # Coverage haircut: a score built on 65% of the evidence should not compete
    # on equal terms with one built on 100%.
    if coverage < 0.95:
        haircut = (1.0 - coverage) * 12.0
        total -= haircut
        notes.append(f"{haircut:.1f} point haircut for {1 - coverage:.0%} missing evidence")

    total = clamp(total, 0.0, 100.0)
    tier = tier_for(total, thresholds)

    if tier >= OpportunityTier.STRONG and data_quality < min_quality_for_top_tier:
        notes.append(f"tier capped at moderate: data quality is {data_quality.value}")
        tier = OpportunityTier.MODERATE

    return CompositeScore(total, tier, list(factors), coverage, data_quality, notes)


def tier_for(score: float, thresholds: Mapping[str, float]) -> OpportunityTier:
    if score >= float(thresholds.get("exceptional", 82)):
        return OpportunityTier.EXCEPTIONAL
    if score >= float(thresholds.get("strong", 70)):
        return OpportunityTier.STRONG
    if score >= float(thresholds.get("moderate", 58)):
        return OpportunityTier.MODERATE
    if score >= float(thresholds.get("watch", 45)):
        return OpportunityTier.WATCH
    return OpportunityTier.AVOID


def cross_sectional_percentiles(values_by_asset: Mapping[str, float | None],
                                *, higher_is_better: bool = True,
                                min_sample: int = 20) -> dict[str, float | None]:
    """Rank a metric across the universe. Requires a real sample to be meaningful."""
    usable = {k: v for k, v in values_by_asset.items() if is_finite(v)}
    if len(usable) < min_sample:
        return {k: None for k in values_by_asset}
    population = list(usable.values())
    out: dict[str, float | None] = {}
    for key in values_by_asset:
        value = usable.get(key)
        if value is None:
            out[key] = None
            continue
        rank = percentile_rank(population, value)
        out[key] = rank if higher_is_better else (1.0 - rank if rank is not None else None)
    return out
