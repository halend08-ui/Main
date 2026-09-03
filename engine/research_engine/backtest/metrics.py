"""Performance metrics for equity curves and trade lists.

Every metric returns ``None`` when the sample cannot support it, and the report
always includes turnover and the cost drag, because a strategy's gross return is
not the number an investor receives.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

import numpy as np

from research_engine.core.numeric import is_finite, ols_beta_alpha, safe_div
from research_engine.core.timeutil import infer_periods_per_year
from research_engine.features import returns as R


def equity_curve_returns(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size < 2:
        return np.array([])
    with np.errstate(divide="ignore", invalid="ignore"):
        out = arr[1:] / arr[:-1] - 1.0
    return out[np.isfinite(out)]


def summarize_curve(dates: Sequence[date], values: Sequence[float], *,
                    benchmark_values: Sequence[float] | None = None,
                    risk_free_rate: float = 0.0,
                    periods_per_year: int | None = None) -> dict[str, Any]:
    """Full performance summary for one equity curve.

    ``periods_per_year`` is inferred from the observation spacing unless given.
    Hard-defaulting it to 252 annualised a MONTHLY curve as though it were
    daily, inflating volatility and Sharpe by sqrt(21) -- a real run reported a
    Sharpe of 4.11 where the honest figure was about 0.90.
    """
    values = [float(v) for v in values]
    if len(values) < 2:
        return {"error": "equity curve too short to summarise"}
    if periods_per_year is None:
        periods_per_year = infer_periods_per_year(dates)

    returns = equity_curve_returns(values)
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    total = values[-1] / values[0] - 1.0 if values[0] > 0 else None
    cagr = ((values[-1] / values[0]) ** (1 / years) - 1.0
            if values[0] > 0 and values[-1] > 0 else None)

    summary: dict[str, Any] = {
        "start": dates[0].isoformat(), "end": dates[-1].isoformat(),
        "years": round(years, 2), "periods_per_year": periods_per_year,
        "total_return": _r(total), "cagr": _r(cagr),
        "volatility": _r(R.volatility(returns, periods_per_year)),
        "sharpe": _r(R.sharpe_ratio(returns, risk_free_rate=risk_free_rate,
                                    periods_per_year=periods_per_year)),
        "sortino": _r(R.sortino_ratio(returns, risk_free_rate=risk_free_rate,
                                      periods_per_year=periods_per_year)),
        "max_drawdown": _r(R.max_drawdown(values)),
        "var_95": _r(R.value_at_risk(returns, 0.95)),
        "expected_shortfall_975": _r(R.expected_shortfall(returns, 0.975)),
        "skew": _r(R.skewness(returns)),
        "excess_kurtosis": _r(R.kurtosis(returns)),
        "observations": len(values),
    }
    mdd = summary["max_drawdown"]
    if cagr is not None and mdd:
        summary["calmar"] = _r(cagr / abs(mdd))

    if benchmark_values is not None and len(benchmark_values) == len(values):
        bench_returns = equity_curve_returns(benchmark_values)
        bench_years = years
        bench_cagr = ((benchmark_values[-1] / benchmark_values[0]) ** (1 / bench_years) - 1.0
                      if benchmark_values[0] > 0 and benchmark_values[-1] > 0 else None)
        summary["benchmark_cagr"] = _r(bench_cagr)
        summary["benchmark_max_drawdown"] = _r(R.max_drawdown(list(benchmark_values)))
        if cagr is not None and bench_cagr is not None:
            summary["excess_cagr"] = _r(cagr - bench_cagr)
        n = min(returns.size, bench_returns.size)
        if n >= 20:
            fit = ols_beta_alpha(returns[-n:], bench_returns[-n:])
            if fit:
                beta, alpha_per_period = fit
                summary["beta"] = _r(beta)
                summary["alpha"] = _r(alpha_per_period * periods_per_year)
                active = returns[-n:] - bench_returns[-n:]
                te = float(np.std(active, ddof=1) * np.sqrt(periods_per_year))
                summary["tracking_error"] = _r(te)
                summary["information_ratio"] = _r(
                    float(np.mean(active) * periods_per_year / te) if te > 1e-9 else None)
    return summary


def summarize_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Win rate, profit factor, average winner/loser, holding period, costs."""
    closed = [t for t in trades if t.get("return_pct") is not None]
    if not closed:
        return {"trades": 0, "note": "no closed trades"}
    returns = [float(t["return_pct"]) for t in closed]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    gross_win = sum(float(t.get("pnl") or 0) for t in closed
                    if (t.get("pnl") or 0) > 0)
    gross_loss = abs(sum(float(t.get("pnl") or 0) for t in closed
                         if (t.get("pnl") or 0) < 0))
    holding = [t["holding_days"] for t in closed if t.get("holding_days") is not None]
    return {
        "trades": len(closed),
        "win_rate": _r(len(wins) / len(closed)),
        "avg_return": _r(float(np.mean(returns))),
        "median_return": _r(float(np.median(returns))),
        "avg_winner": _r(float(np.mean(wins))) if wins else None,
        "avg_loser": _r(float(np.mean(losses))) if losses else None,
        "profit_factor": _r(gross_loss and gross_win / gross_loss),
        "best": _r(max(returns)), "worst": _r(min(returns)),
        "avg_holding_days": _r(float(np.mean(holding))) if holding else None,
        "total_costs": _r(sum(float(t.get("costs") or 0) for t in closed), 2),
        "cost_drag_bps": _r(_cost_drag(closed), 1),
    }


def _cost_drag(trades: Sequence[Mapping[str, Any]]) -> float | None:
    notional = sum(abs(float(t.get("entry_price", 0)) * float(t.get("quantity", 0)))
                   for t in trades)
    costs = sum(float(t.get("costs") or 0) for t in trades)
    if notional <= 0:
        return None
    return costs / notional * 10_000


def turnover(trades: Sequence[Mapping[str, Any]], *, average_equity: float,
             years: float) -> float | None:
    """Annualised turnover as a fraction of portfolio value."""
    if average_equity <= 0 or years <= 0:
        return None
    traded = sum(abs(float(t.get("entry_price", 0)) * float(t.get("quantity", 0)))
                 for t in trades)
    return traded / average_equity / years


def compare_to_benchmarks(strategy: Mapping[str, Any],
                          benchmarks: Mapping[str, Mapping[str, Any]]
                          ) -> dict[str, Any]:
    """Side-by-side comparison. A strategy that loses to cash must say so."""
    out: dict[str, Any] = {"strategy": strategy, "benchmarks": dict(benchmarks),
                           "verdict": []}
    cagr = strategy.get("cagr")
    sharpe = strategy.get("sharpe")
    for name, metrics in benchmarks.items():
        b_cagr = metrics.get("cagr")
        b_sharpe = metrics.get("sharpe")
        if cagr is not None and b_cagr is not None:
            delta = cagr - b_cagr
            out["verdict"].append(
                f"{'beat' if delta > 0 else 'trailed'} {name} by "
                f"{abs(delta) * 100:.1f}pp CAGR")
        if sharpe is not None and b_sharpe is not None and sharpe < b_sharpe:
            out["verdict"].append(
                f"risk-adjusted return is worse than {name} "
                f"(Sharpe {sharpe:.2f} vs {b_sharpe:.2f})")
    return out


def deflated_metrics(metrics: Mapping[str, Any], *, configurations_tried: int,
                     parameters: int, observations: int) -> dict[str, Any]:
    """Discount headline results for the search that produced them."""
    from research_engine.quality.bias import overfitting_pressure

    pressure = overfitting_pressure(parameters=parameters, observations=observations,
                                    configurations_tried=configurations_tried)
    factor = pressure["deflation_factor"]
    out = dict(metrics)
    for key in ("cagr", "sharpe", "sortino", "alpha", "information_ratio"):
        value = metrics.get(key)
        if value is not None:
            out[f"{key}_deflated"] = _r(value * factor)
    out["overfitting"] = pressure
    out["deflation_note"] = (
        f"headline figures multiplied by {factor:.2f} to reflect "
        f"{configurations_tried} configuration(s) tried over {observations} "
        f"observations with {parameters} parameter(s)")
    return out


def _r(value: Any, digits: int = 4) -> float | None:
    if value is None or not is_finite(value):
        return None
    return round(float(value), digits)
