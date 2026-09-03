"""Return, risk and drawdown statistics.

Everything here refuses to compute on insufficient samples: a Sharpe ratio from
twenty observations is not a Sharpe ratio, and reporting one as though it were
is how research becomes noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np

from research_engine.core.numeric import ols_beta_alpha
from research_engine.core.series import PriceSeries, aligned_returns

MIN_OBS_FOR_MOMENTS = 20
MIN_OBS_FOR_RATIOS = 60


def _clean(returns: Sequence[float]) -> np.ndarray:
    arr = np.asarray(returns, dtype=float)
    return arr[np.isfinite(arr)]


def total_return(series: PriceSeries, lookback: int | None = None) -> float | None:
    px = series.adj_close
    if px.size < 2:
        return None
    start_idx = 0 if lookback is None else max(0, px.size - lookback - 1)
    start, end = px[start_idx], px[-1]
    if not (np.isfinite(start) and np.isfinite(end)) or start <= 0:
        return None
    return float(end / start - 1.0)


def annualized_return(returns: Sequence[float], periods_per_year: int = 252
                      ) -> float | None:
    r = _clean(returns)
    if r.size < MIN_OBS_FOR_MOMENTS:
        return None
    growth = float(np.prod(1.0 + r))
    if growth <= 0:
        return None            # a wiped-out path has no meaningful CAGR
    years = r.size / periods_per_year
    if years <= 0:
        return None
    return growth ** (1.0 / years) - 1.0


def volatility(returns: Sequence[float], periods_per_year: int = 252) -> float | None:
    r = _clean(returns)
    if r.size < MIN_OBS_FOR_MOMENTS:
        return None
    return float(np.std(r, ddof=1) * np.sqrt(periods_per_year))


def downside_deviation(returns: Sequence[float], *, threshold: float = 0.0,
                       periods_per_year: int = 252) -> float | None:
    r = _clean(returns)
    if r.size < MIN_OBS_FOR_MOMENTS:
        return None
    shortfall = np.minimum(r - threshold, 0.0)
    return float(np.sqrt(np.mean(shortfall ** 2)) * np.sqrt(periods_per_year))


def sharpe_ratio(returns: Sequence[float], *, risk_free_rate: float = 0.0,
                 periods_per_year: int = 252) -> float | None:
    r = _clean(returns)
    if r.size < MIN_OBS_FOR_RATIOS:
        return None
    excess = r - risk_free_rate / periods_per_year
    sd = float(np.std(excess, ddof=1))
    if sd < 1e-12:
        return None
    return float(np.mean(excess) / sd * np.sqrt(periods_per_year))


def sortino_ratio(returns: Sequence[float], *, risk_free_rate: float = 0.0,
                  periods_per_year: int = 252) -> float | None:
    r = _clean(returns)
    if r.size < MIN_OBS_FOR_RATIOS:
        return None
    excess = r - risk_free_rate / periods_per_year
    dd = downside_deviation(excess, periods_per_year=periods_per_year)
    if dd is None or dd < 1e-12:
        return None
    return float(np.mean(excess) * periods_per_year / dd)


def max_drawdown(values: Sequence[float]) -> float | None:
    """Worst peak-to-trough decline as a negative fraction."""
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return None
    peaks = np.maximum.accumulate(arr)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peaks > 0, arr / peaks - 1.0, np.nan)
    dd = dd[np.isfinite(dd)]
    return float(np.min(dd)) if dd.size else None


@dataclass(frozen=True, slots=True)
class DrawdownEpisode:
    start: date
    trough: date
    recovery: date | None
    depth: float
    length_days: int
    recovery_days: int | None


def drawdown_episodes(series: PriceSeries, *, min_depth: float = 0.10
                      ) -> list[DrawdownEpisode]:
    """Identify distinct drawdowns deeper than ``min_depth``."""
    px = series.adj_close
    episodes: list[DrawdownEpisode] = []
    peak_idx = 0
    trough_idx = None
    for i in range(1, px.size):
        if not np.isfinite(px[i]):
            continue
        if px[i] >= px[peak_idx]:
            if trough_idx is not None:
                depth = px[trough_idx] / px[peak_idx] - 1.0
                if depth <= -abs(min_depth):
                    episodes.append(DrawdownEpisode(
                        start=series.dates[peak_idx], trough=series.dates[trough_idx],
                        recovery=series.dates[i], depth=float(depth),
                        length_days=(series.dates[i] - series.dates[peak_idx]).days,
                        recovery_days=(series.dates[i] - series.dates[trough_idx]).days))
                trough_idx = None
            peak_idx = i
        else:
            if trough_idx is None or px[i] < px[trough_idx]:
                trough_idx = i
    if trough_idx is not None:
        depth = px[trough_idx] / px[peak_idx] - 1.0
        if depth <= -abs(min_depth):
            episodes.append(DrawdownEpisode(
                start=series.dates[peak_idx], trough=series.dates[trough_idx],
                recovery=None, depth=float(depth),
                length_days=(series.dates[-1] - series.dates[peak_idx]).days,
                recovery_days=None))
    return episodes


def value_at_risk(returns: Sequence[float], confidence: float = 0.95) -> float | None:
    """Historical VaR: the loss exceeded (1-confidence) of the time (negative)."""
    r = _clean(returns)
    if r.size < 100:            # empirical quantiles need a real sample
        return None
    return float(np.quantile(r, 1.0 - confidence))


def expected_shortfall(returns: Sequence[float], confidence: float = 0.975
                       ) -> float | None:
    """Mean loss in the tail beyond VaR (negative)."""
    r = _clean(returns)
    if r.size < 100:
        return None
    cutoff = np.quantile(r, 1.0 - confidence)
    tail = r[r <= cutoff]
    if tail.size < 5:
        return None
    return float(np.mean(tail))


def skewness(returns: Sequence[float]) -> float | None:
    r = _clean(returns)
    if r.size < MIN_OBS_FOR_RATIOS:
        return None
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return None
    return float(np.mean(((r - r.mean()) / sd) ** 3))


def kurtosis(returns: Sequence[float]) -> float | None:
    """Excess kurtosis. Fat tails are the norm, not the exception, in markets."""
    r = _clean(returns)
    if r.size < MIN_OBS_FOR_RATIOS:
        return None
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return None
    return float(np.mean(((r - r.mean()) / sd) ** 4) - 3.0)


def beta_alpha(asset: PriceSeries, benchmark: PriceSeries, *,
               periods_per_year: int = 252) -> tuple[float, float] | None:
    """Beta and annualised alpha versus a benchmark, on aligned dates."""
    ra, rb = aligned_returns(asset, benchmark)
    result = ols_beta_alpha(ra, rb)
    if result is None:
        return None
    beta, alpha_per_period = result
    return beta, alpha_per_period * periods_per_year


def tracking_error(asset: PriceSeries, benchmark: PriceSeries, *,
                   periods_per_year: int = 252) -> float | None:
    ra, rb = aligned_returns(asset, benchmark)
    if ra.size < MIN_OBS_FOR_RATIOS:
        return None
    diff = ra - rb
    return float(np.std(diff, ddof=1) * np.sqrt(periods_per_year))


def horizon_returns(series: PriceSeries, horizons_days: Sequence[int]
                    ) -> dict[int, float | None]:
    """Trailing total return over each horizon, given in CALENDAR DAYS.

    The calendar-day horizon is converted into an observation count using the
    series' own sampling frequency. This previously treated the argument as an
    observation count, so a monthly series asked for "63" returned a 63-MONTH
    return labelled as three months -- on real AAPL data that reported the
    2004-2010 gain of +593% as a three-month move.
    """
    px = series.adj_close
    periods_per_year = series.periods_per_year
    out: dict[int, float | None] = {}
    for days in horizons_days:
        observations = max(1, round(days / 365.25 * periods_per_year))
        if px.size <= observations:
            out[days] = None
            continue
        start, end = px[-(observations + 1)], px[-1]
        out[days] = (float(end / start - 1.0)
                     if np.isfinite(start) and start > 0 else None)
    return out


def summarize(series: PriceSeries, *, risk_free_rate: float = 0.0,
              benchmark: PriceSeries | None = None) -> dict[str, float | None]:
    """One-call risk/return summary used by the risk engine and reports."""
    ppy = series.periods_per_year
    returns = series.returns()
    summary: dict[str, float | None] = {
        "observations": float(len(series)),
        "total_return": total_return(series),
        "cagr": annualized_return(returns, ppy),
        "volatility": volatility(returns, ppy),
        "downside_deviation": downside_deviation(returns, periods_per_year=ppy),
        "sharpe": sharpe_ratio(returns, risk_free_rate=risk_free_rate,
                               periods_per_year=ppy),
        "sortino": sortino_ratio(returns, risk_free_rate=risk_free_rate,
                                 periods_per_year=ppy),
        "max_drawdown": max_drawdown(series.adj_close),
        "var_95": value_at_risk(returns, 0.95),
        "expected_shortfall_975": expected_shortfall(returns, 0.975),
        "skew": skewness(returns),
        "excess_kurtosis": kurtosis(returns),
    }
    if benchmark is not None:
        ba = beta_alpha(series, benchmark, periods_per_year=ppy)
        summary["beta"] = ba[0] if ba else None
        summary["alpha"] = ba[1] if ba else None
        summary["tracking_error"] = tracking_error(series, benchmark,
                                                   periods_per_year=ppy)
    return summary
