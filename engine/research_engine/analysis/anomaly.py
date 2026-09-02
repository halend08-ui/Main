"""Anomaly detection.

Anomalies are *research triggers*, never signals. A volume spike does not mean
"buy"; it means "something happened here, find out what". Everything detected in
this module feeds the research queue, and nothing in it can move a
recommendation on its own.

Robust statistics (median/MAD) are used throughout, because financial series
have fat tails that make ordinary z-scores fire constantly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

import numpy as np

from research_engine.core.numeric import clamp, is_finite, median, robust_zscore
from research_engine.core.series import PriceSeries


@dataclass(frozen=True, slots=True)
class Anomaly:
    kind: str
    severity: float           # 0..1
    detail: str
    value: float | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "severity": round(self.severity, 3),
                "detail": self.detail,
                "value": None if self.value is None else round(self.value, 4),
                "context": dict(self.context),
                "action": "queue for research; not a trading signal"}


def volume_spike(series: PriceSeries, *, lookback: int = 60,
                 threshold: float = 3.0) -> Anomaly | None:
    if len(series) < lookback + 5:
        return None
    volume = series.volume[-(lookback + 1):]
    if not np.all(np.isfinite(volume[:-1])) or not np.isfinite(volume[-1]):
        return None
    baseline = median(volume[:-1].tolist())
    if not baseline or baseline <= 0:
        return None
    ratio = float(volume[-1]) / baseline
    if ratio < threshold:
        return None
    return Anomaly("volume_spike", clamp((ratio - threshold) / (threshold * 2), 0.2, 1.0),
                   f"volume is {ratio:.1f}x its 60-session median", ratio,
                   {"median_volume": baseline})


def price_anomaly(series: PriceSeries, *, lookback: int = 120,
                  z_threshold: float = 4.0) -> Anomaly | None:
    returns = series.returns()
    if returns.size < lookback:
        return None
    window = returns[-lookback:]
    latest = float(window[-1])
    z = robust_zscore(window[:-1].tolist(), latest)
    if z is None or abs(z) < z_threshold:
        return None
    direction = "gain" if latest > 0 else "loss"
    return Anomaly("price_move", clamp(abs(z) / (z_threshold * 2.5), 0.2, 1.0),
                   f"single-session {direction} of {latest:+.1%} "
                   f"({abs(z):.1f} robust standard deviations)", latest,
                   {"robust_z": round(z, 2)})


def volatility_regime_break(series: PriceSeries, *, short: int = 20,
                            long: int = 120, ratio_threshold: float = 2.2
                            ) -> Anomaly | None:
    returns = series.returns()
    if returns.size < long + short:
        return None
    recent = returns[-short:]
    prior = returns[-long:-short]
    if recent.size < short or prior.size < 20:
        return None
    recent_vol = float(np.std(recent, ddof=1))
    prior_vol = float(np.std(prior, ddof=1))
    if prior_vol <= 1e-9:
        return None
    ratio = recent_vol / prior_vol
    if ratio < ratio_threshold:
        return None
    return Anomaly("volatility_break", clamp((ratio - ratio_threshold) / 3.0, 0.2, 1.0),
                   f"20-session volatility is {ratio:.1f}x its prior level", ratio)


def fundamental_shift(current: float | None, history: Sequence[float], *,
                      metric: str, z_threshold: float = 2.5) -> Anomaly | None:
    """A reported figure far outside its own history."""
    if current is None or len(history) < 5:
        return None
    z = robust_zscore(list(history), current)
    if z is None or abs(z) < z_threshold:
        return None
    direction = "above" if z > 0 else "below"
    return Anomaly("fundamental_shift", clamp(abs(z) / (z_threshold * 2), 0.2, 1.0),
                   f"{metric} is {abs(z):.1f} robust deviations {direction} its history",
                   current, {"metric": metric, "robust_z": round(z, 2)})


def valuation_dislocation(current_multiple: float | None,
                          history: Sequence[float], *, metric: str = "ev_ebitda"
                          ) -> Anomaly | None:
    values = [v for v in history if is_finite(v) and v > 0]
    if current_multiple is None or len(values) < 12:
        return None
    med = median(values)
    if not med or med <= 0:
        return None
    ratio = current_multiple / med
    if 0.5 <= ratio <= 2.0:
        return None
    cheap = ratio < 0.5
    return Anomaly("valuation_dislocation", clamp(abs(np.log(ratio)) / 1.4, 0.2, 1.0),
                   f"{metric} is {ratio:.2f}x its own historical median "
                   f"({'unusually cheap -- check for deterioration' if cheap else 'unusually expensive'})",
                   current_multiple, {"median": med, "ratio": round(ratio, 2)})


def liquidity_deterioration(series: PriceSeries, *, short: int = 20,
                            long: int = 120, drop_threshold: float = 0.5
                            ) -> Anomaly | None:
    if len(series) < long + short:
        return None
    dollar_volume = series.close * series.volume
    recent = dollar_volume[-short:]
    prior = dollar_volume[-long:-short]
    if not (np.all(np.isfinite(recent)) and np.any(np.isfinite(prior))):
        return None
    recent_med = median(recent.tolist())
    prior_med = median(prior[np.isfinite(prior)].tolist())
    if not recent_med or not prior_med or prior_med <= 0:
        return None
    ratio = recent_med / prior_med
    if ratio > drop_threshold:
        return None
    return Anomaly("liquidity_deterioration", clamp((drop_threshold - ratio) * 2, 0.2, 1.0),
                   f"dollar volume has fallen to {ratio:.0%} of its prior level", ratio)


def insider_cluster(transactions: Sequence[Mapping[str, Any]], *,
                    window_days: int = 90, min_count: int = 3) -> Anomaly | None:
    """Several insiders transacting in the same direction is the informative case."""
    if len(transactions) < min_count:
        return None
    buys = [t for t in transactions if (t.get("change_shares") or 0) > 0]
    sells = [t for t in transactions if (t.get("change_shares") or 0) < 0]
    for group, label, sign in ((buys, "buying", 1.0), (sells, "selling", -1.0)):
        holders = {t.get("holder") for t in group if t.get("holder")}
        if len(holders) >= min_count:
            total = sum(abs(float(t.get("change_shares") or 0)) for t in group)
            return Anomaly("insider_cluster", clamp(len(holders) / 8.0, 0.3, 1.0),
                           f"{len(holders)} insiders {label} within {window_days} days "
                           f"({total:,.0f} shares)", sign * total,
                           {"direction": label, "insiders": len(holders)})
    return None


def scan(series: PriceSeries | None = None, *,
         fundamentals: Mapping[str, Sequence[float]] | None = None,
         current_fundamentals: Mapping[str, float | None] | None = None,
         multiple: float | None = None,
         multiple_history: Sequence[float] = (),
         insider_transactions: Sequence[Mapping[str, Any]] = ()) -> list[Anomaly]:
    """Run every detector that has the inputs it needs."""
    found: list[Anomaly] = []
    if series is not None:
        for detector in (volume_spike, price_anomaly, volatility_regime_break,
                         liquidity_deterioration):
            result = detector(series)
            if result is not None:
                found.append(result)
    if fundamentals and current_fundamentals:
        for metric, history in fundamentals.items():
            result = fundamental_shift(current_fundamentals.get(metric), history,
                                       metric=metric)
            if result is not None:
                found.append(result)
    dislocation = valuation_dislocation(multiple, multiple_history)
    if dislocation is not None:
        found.append(dislocation)
    cluster = insider_cluster(insider_transactions)
    if cluster is not None:
        found.append(cluster)
    return sorted(found, key=lambda a: a.severity, reverse=True)


def research_priority(anomalies: Sequence[Anomaly]) -> float:
    """Convert anomalies into a 0..1 research-queue priority contribution."""
    if not anomalies:
        return 0.0
    top = max(a.severity for a in anomalies)
    breadth = min(len(anomalies) / 4.0, 1.0)
    return clamp(0.6 * top + 0.4 * breadth, 0.0, 1.0)
