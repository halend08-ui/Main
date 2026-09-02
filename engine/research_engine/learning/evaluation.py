"""Prediction evaluation.

Every prediction the engine makes is stored before the outcome is known, and
this module grades it once the horizon has elapsed. Grading is deliberately
multi-dimensional, because "was the direction right?" is a poor summary:

* **Return** -- what actually happened.
* **Excess return** -- versus the relevant benchmark, so a rising tide is not
  mistaken for skill.
* **Path** -- maximum drawdown during the holding period; a prediction that was
  eventually right but fell 60% first was not a good call at the size implied.
* **Thesis outcome** -- did the reasoning hold, or was the result luck? A
  correct direction for the wrong reason is recorded as such.

Nothing here is allowed to look at data after ``due_at`` when computing the
outcome, and nothing may re-grade a prediction it has already graded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

import numpy as np

from research_engine.core.logging import get_logger
from research_engine.core.numeric import clamp, is_finite
from research_engine.core.series import PriceSeries
from research_engine.core.timeutil import to_date
from research_engine.core.types import DataQuality, Recommendation

log = get_logger(__name__)

BULLISH = {Recommendation.BUY}
BEARISH = {Recommendation.SELL, Recommendation.AVOID}
NEUTRAL = {Recommendation.HOLD, Recommendation.WATCH}


@dataclass
class Outcome:
    prediction_id: int
    symbol: str
    price_at_prediction: float
    price_at_due: float | None
    actual_return: float | None
    benchmark_return: float | None
    excess_return: float | None
    max_drawdown: float | None
    realized_vol: float | None
    hit: bool | None
    thesis_outcome: str            # succeeded | failed | partial | luck | open
    failure_reason: str | None
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"prediction_id": self.prediction_id, "symbol": self.symbol,
                "price_at_prediction": self.price_at_prediction,
                "price_at_due": self.price_at_due,
                "actual_return": _r(self.actual_return),
                "benchmark_return": _r(self.benchmark_return),
                "excess_return": _r(self.excess_return),
                "max_drawdown": _r(self.max_drawdown),
                "realized_vol": _r(self.realized_vol), "hit": self.hit,
                "thesis_outcome": self.thesis_outcome,
                "failure_reason": self.failure_reason, "notes": self.notes}


def _r(value: float | None) -> float | None:
    return None if value is None or not is_finite(value) else round(float(value), 4)


def evaluate_prediction(prediction: Mapping[str, Any], series: PriceSeries, *,
                        benchmark: PriceSeries | None = None,
                        as_of: date | None = None,
                        tolerance_days: int = 7) -> Outcome | None:
    """Grade one prediction. Returns None if the horizon has not elapsed."""
    due = to_date(prediction["due_at"])
    today = as_of or date.today()
    if due > today:
        return None

    start = to_date(prediction["as_of"])
    entry = float(prediction["price_at_prediction"])
    notes: list[str] = []

    exit_price = series.price_on(due, tolerance_days=tolerance_days)
    if exit_price is None:
        # No price at the due date: the asset may have stopped trading, which is
        # itself an outcome and must not be dropped.
        last = series.adj_close[-1] if len(series) else None
        if series.end < due and last is not None:
            exit_price = float(last)
            notes.append(f"no price on {due}; used last available "
                         f"({series.end}) -- possible delisting")
        else:
            return Outcome(int(prediction["id"]), str(prediction.get("symbol", "")),
                           entry, None, None, None, None, None, None, None,
                           "open", "no price available at the due date",
                           ["outcome could not be measured"])

    actual = exit_price / entry - 1.0 if entry > 0 else None

    benchmark_return = None
    if benchmark is not None:
        b_start = benchmark.price_on(start, tolerance_days=tolerance_days)
        b_end = benchmark.price_on(due, tolerance_days=tolerance_days)
        if b_start and b_end and b_start > 0:
            benchmark_return = b_end / b_start - 1.0
    excess = (actual - benchmark_return
              if actual is not None and benchmark_return is not None else None)

    # Path statistics over the holding period only.
    path_dd = path_vol = None
    try:
        window = series.between(start, due)
        if len(window) >= 5:
            px = window.adj_close
            peaks = np.maximum.accumulate(px)
            path_dd = float(np.min(px / peaks - 1.0))
            returns = window.returns()
            finite = returns[np.isfinite(returns)]
            if finite.size >= 10:
                path_vol = float(np.std(finite, ddof=1) * np.sqrt(window.periods_per_year))
    except Exception:
        notes.append("path statistics unavailable for the holding period")

    recommendation = Recommendation(str(prediction["recommendation"]))
    hit = _direction_correct(recommendation, actual)
    thesis, reason = _thesis_outcome(recommendation, actual, excess,
                                     prediction.get("expected_return"),
                                     prediction.get("expected_downside"), path_dd)

    return Outcome(prediction_id=int(prediction["id"]),
                   symbol=str(prediction.get("symbol", "")),
                   price_at_prediction=entry, price_at_due=exit_price,
                   actual_return=actual, benchmark_return=benchmark_return,
                   excess_return=excess, max_drawdown=path_dd, realized_vol=path_vol,
                   hit=hit, thesis_outcome=thesis, failure_reason=reason, notes=notes)


def _direction_correct(recommendation: Recommendation,
                       actual: float | None) -> bool | None:
    if actual is None:
        return None
    if recommendation in BULLISH:
        return actual > 0
    if recommendation in BEARISH:
        return actual < 0
    return None       # HOLD/WATCH make no directional claim; scoring them as
                      # hits or misses would inflate or deflate the record


def _thesis_outcome(recommendation: Recommendation, actual: float | None,
                    excess: float | None, expected: float | None,
                    expected_downside: float | None,
                    path_drawdown: float | None) -> tuple[str, str | None]:
    """Distinguish "right" from "right for the stated reason"."""
    if actual is None:
        return "open", "no measurable return"

    if recommendation in BULLISH:
        if actual <= -0.20:
            return "failed", f"position lost {abs(actual):.0%} against a buy call"
        if expected is not None and actual >= expected * 0.5:
            if path_drawdown is not None and expected_downside is not None \
                    and path_drawdown < expected_downside * 2:
                return ("partial",
                        f"reached the return target but drew down "
                        f"{abs(path_drawdown):.0%}, worse than the "
                        f"{abs(expected_downside):.0%} anticipated")
            return "succeeded", None
        if actual > 0 and (excess is None or excess <= 0):
            return ("luck",
                    "positive return came from the market, not from the thesis "
                    "(no excess versus benchmark)")
        if actual > 0:
            return "partial", "positive but short of the expected return"
        return "failed", "negative return against a buy call"

    if recommendation in BEARISH:
        if actual < 0:
            return "succeeded", None
        if actual > 0.20:
            return "failed", f"asset rose {actual:.0%} after a sell/avoid call"
        return "partial", "asset did not fall as expected"

    # HOLD / WATCH: judged on whether avoiding action was right
    if actual is not None and abs(actual) < 0.10:
        return "succeeded", None
    if actual > 0.25:
        return "failed", f"missed a {actual:.0%} move while on the sidelines"
    if actual < -0.25:
        return "succeeded", "avoided a significant decline"
    return "partial", None


def evaluate_batch(predictions: Sequence[Mapping[str, Any]],
                   series_by_symbol: Mapping[str, PriceSeries], *,
                   benchmark_by_class: Mapping[str, PriceSeries] | None = None,
                   as_of: date | None = None) -> list[Outcome]:
    """Grade a batch, skipping predictions whose price history is unavailable."""
    outcomes: list[Outcome] = []
    benchmarks = benchmark_by_class or {}
    for prediction in predictions:
        symbol = str(prediction.get("symbol", ""))
        series = series_by_symbol.get(symbol)
        if series is None:
            log.warning("cannot evaluate prediction: no price history",
                        symbol=symbol, prediction_id=prediction.get("id"))
            continue
        benchmark = benchmarks.get(str(prediction.get("asset_class", "equity")))
        outcome = evaluate_prediction(prediction, series, benchmark=benchmark,
                                      as_of=as_of)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def factor_attribution(prediction: Mapping[str, Any],
                       outcome: Outcome) -> dict[str, float]:
    """Credit or blame each factor in proportion to its stated contribution.

    This is attribution, not causation: it records which factors were present
    when the call worked, so that systematic patterns can be detected across
    hundreds of predictions. A single attribution means nothing.
    """
    factors = prediction.get("factors") or {}
    if not factors or outcome.actual_return is None:
        return {}
    total = sum(abs(float(v)) for v in factors.values() if is_finite(v))
    if total <= 0:
        return {}
    sign = 1.0 if outcome.actual_return > 0 else -1.0
    return {name: round(sign * abs(float(value)) / total, 4)
            for name, value in factors.items() if is_finite(value)}
