"""Market-regime detection.

A regime label is a *summary of observable conditions*, not a forecast. The
classifier uses only information available at the as-of date:

* trend: price relative to its long moving average, and the average's slope;
* volatility: realised volatility versus its own history;
* breadth/participation, when a cross-section is supplied;
* risk appetite: benchmark equity versus a defensive proxy, and credit spreads.

Regimes matter because factor performance is regime-dependent. The learning
layer tracks accuracy per regime, so the system can discover -- rather than
assume -- that (say) momentum works poorly in high-volatility bear markets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

import numpy as np

from research_engine.core.numeric import clamp, percentile_rank
from research_engine.core.series import PriceSeries
from research_engine.core.types import MarketRegime
from research_engine.features.technical import sma

TREND_WINDOW = 200
SLOPE_WINDOW = 60
VOL_WINDOW = 20
VOL_HISTORY = 504          # ~2 years for the volatility percentile


@dataclass(frozen=True, slots=True)
class RegimeState:
    as_of: date
    regime: MarketRegime
    volatility_regime: MarketRegime
    risk_appetite: MarketRegime
    confidence: float
    detail: Mapping[str, Any] = field(default_factory=dict)

    def labels(self) -> list[str]:
        return [self.regime.value, self.volatility_regime.value,
                self.risk_appetite.value]

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(), "regime": self.regime.value,
                "volatility_regime": self.volatility_regime.value,
                "risk_appetite": self.risk_appetite.value,
                "confidence": round(self.confidence, 3), "detail": dict(self.detail)}


def classify(benchmark: PriceSeries, *, as_of: date | None = None,
             defensive: PriceSeries | None = None,
             credit_spread: float | None = None,
             breadth: float | None = None,
             unemployment_change_12m: float | None = None) -> RegimeState:
    """Classify the market regime as of a date.

    ``breadth`` is the fraction of a universe above its own 200-day average
    (supplied by the pipeline); ``credit_spread`` is the high-yield spread in
    percent. Both are optional, and their absence lowers confidence rather than
    being assumed away.
    """
    series = benchmark.as_of(as_of) if as_of else benchmark
    ref_date = series.end
    px = series.adj_close
    detail: dict[str, Any] = {}
    confidence_inputs: list[float] = []

    if px.size < TREND_WINDOW + SLOPE_WINDOW:
        return RegimeState(ref_date, MarketRegime.UNKNOWN, MarketRegime.UNKNOWN,
                           MarketRegime.UNKNOWN, 0.0,
                           {"reason": f"only {px.size} observations; need "
                                      f"{TREND_WINDOW + SLOPE_WINDOW}"})

    # -- trend -------------------------------------------------------------
    ma = sma(px, TREND_WINDOW)
    last_price = float(px[-1])
    last_ma = float(ma[-1])
    prior_ma = float(ma[-SLOPE_WINDOW])
    above = last_price / last_ma - 1.0
    slope = (last_ma / prior_ma - 1.0) if prior_ma > 0 else 0.0
    detail["price_vs_200dma"] = round(above, 4)
    detail["ma_slope_60d"] = round(slope, 4)

    drawdown = float(px[-1] / np.max(px[-252:]) - 1.0) if px.size >= 252 else None
    detail["drawdown_1y"] = round(drawdown, 4) if drawdown is not None else None

    if above > 0.02 and slope > 0.005:
        trend = MarketRegime.BULL
        confidence_inputs.append(clamp(abs(above) * 8 + abs(slope) * 20, 0.3, 1.0))
    elif above < -0.05 and slope < -0.005:
        trend = MarketRegime.BEAR
        confidence_inputs.append(clamp(abs(above) * 6 + abs(slope) * 20, 0.3, 1.0))
    else:
        trend = MarketRegime.SIDEWAYS
        confidence_inputs.append(0.45)

    if drawdown is not None and drawdown < -0.2:
        trend = MarketRegime.BEAR
        detail["bear_trigger"] = "drawdown beyond 20% from the 1-year high"

    # -- volatility --------------------------------------------------------
    returns = series.returns()
    finite = returns[np.isfinite(returns)]
    vol_regime = MarketRegime.UNKNOWN
    if finite.size >= VOL_HISTORY // 2:
        ppy = series.periods_per_year
        recent_vol = float(np.std(finite[-VOL_WINDOW:], ddof=1) * np.sqrt(ppy))
        history = [float(np.std(finite[i - VOL_WINDOW:i], ddof=1) * np.sqrt(ppy))
                   for i in range(VOL_WINDOW, min(finite.size, VOL_HISTORY))]
        rank = percentile_rank(history, recent_vol) if history else None
        detail["realized_vol_20d"] = round(recent_vol, 4)
        detail["vol_percentile"] = round(rank, 3) if rank is not None else None
        if rank is not None:
            if rank > 0.8:
                vol_regime = MarketRegime.HIGH_VOL
            elif rank < 0.3:
                vol_regime = MarketRegime.LOW_VOL
            else:
                vol_regime = MarketRegime.SIDEWAYS
            confidence_inputs.append(0.8)
    else:
        confidence_inputs.append(0.3)

    # -- risk appetite -----------------------------------------------------
    risk_appetite = MarketRegime.UNKNOWN
    signals: list[float] = []
    if defensive is not None:
        from research_engine.core.series import align
        a, b, dates = align(series, defensive)
        if len(a) > 63:
            rel = (a[-1] / a[-63]) - (b[-1] / b[-63])
            detail["risk_asset_vs_defensive_3m"] = round(float(rel), 4)
            signals.append(1.0 if rel > 0 else -1.0)
    if credit_spread is not None:
        detail["credit_spread"] = credit_spread
        signals.append(-1.0 if credit_spread > 5.0 else 1.0)
    if breadth is not None:
        detail["breadth_above_200dma"] = round(breadth, 3)
        signals.append(1.0 if breadth > 0.5 else -1.0)
    if signals:
        score = sum(signals) / len(signals)
        risk_appetite = MarketRegime.RISK_ON if score > 0 else MarketRegime.RISK_OFF
        detail["risk_appetite_score"] = round(score, 2)
        confidence_inputs.append(0.5 + 0.3 * abs(score))
    else:
        detail["risk_appetite_note"] = "no defensive proxy, credit or breadth input"
        confidence_inputs.append(0.25)

    # -- cycle overlay -----------------------------------------------------
    regime = trend
    if unemployment_change_12m is not None and unemployment_change_12m > 0.5:
        regime = MarketRegime.RECESSIONARY
        detail["cycle_trigger"] = (f"unemployment up {unemployment_change_12m:.1f}pp "
                                   f"year-on-year")
    elif (trend is MarketRegime.BULL and drawdown is not None and drawdown > -0.05
          and unemployment_change_12m is not None and unemployment_change_12m < -0.2):
        regime = MarketRegime.RECOVERY

    confidence = float(np.mean(confidence_inputs)) if confidence_inputs else 0.0
    return RegimeState(ref_date, regime, vol_regime, risk_appetite,
                       clamp(confidence, 0.0, 0.95), detail)


def breadth_from_universe(series_by_symbol: Mapping[str, PriceSeries], *,
                          window: int = TREND_WINDOW) -> float | None:
    """Fraction of assets trading above their own moving average."""
    above = 0
    counted = 0
    for series in series_by_symbol.values():
        px = series.adj_close
        if px.size < window:
            continue
        ma = sma(px, window)
        if not np.isfinite(ma[-1]) or ma[-1] <= 0:
            continue
        counted += 1
        if px[-1] > ma[-1]:
            above += 1
    if counted < 10:
        return None
    return above / counted


def regime_history(benchmark: PriceSeries, *, step_days: int = 21,
                   lookback_years: int = 5) -> list[RegimeState]:
    """Classify at regular intervals -- used to bucket historical performance."""
    states: list[RegimeState] = []
    total = len(benchmark)
    start = max(TREND_WINDOW + SLOPE_WINDOW,
                total - int(lookback_years * benchmark.periods_per_year))
    for idx in range(start, total, step_days):
        window = benchmark.as_of(benchmark.dates[idx])
        states.append(classify(window))
    return states
