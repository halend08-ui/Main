"""Model performance statistics, bucketed the way failures actually cluster.

Aggregate accuracy hides everything that matters. A model can be 60% accurate
overall while being 30% accurate in bear markets, on small caps, or at high
stated confidence -- and those are precisely the conditions under which its
output does damage. Performance is therefore always computed per bucket, and a
bucket with too few samples reports "insufficient" rather than a number.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from research_engine.analysis.probability import (Calibrator, brier_score,
                                                  brier_skill_score)
from research_engine.core.numeric import clamp, is_finite, mean, safe_div
from research_engine.features import returns as R

MIN_SAMPLES = 30


@dataclass
class BucketPerformance:
    kind: str
    value: str
    samples: int
    hit_rate: float | None = None
    avg_return: float | None = None
    avg_excess: float | None = None
    median_return: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    profit_factor: float | None = None
    avg_winner: float | None = None
    avg_loser: float | None = None
    brier: float | None = None
    brier_skill: float | None = None
    calibration_error: float | None = None
    thesis_success_rate: float | None = None
    luck_rate: float | None = None
    sufficient: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


def _stats(records: Sequence[Mapping[str, Any]], kind: str, value: str,
           *, min_samples: int = MIN_SAMPLES) -> BucketPerformance:
    n = len(records)
    if n < min_samples:
        return BucketPerformance(
            kind=kind, value=value, samples=n, sufficient=False,
            note=f"only {n} evaluated predictions; at least {min_samples} are "
                 f"needed before performance means anything")

    returns = [float(r["actual_return"]) for r in records
               if is_finite(r.get("actual_return"))]
    excess = [float(r["excess_return"]) for r in records
              if is_finite(r.get("excess_return"))]
    hits = [bool(r["hit"]) for r in records if r.get("hit") is not None]
    winners = [r for r in returns if r > 0]
    losers = [r for r in returns if r <= 0]
    gross_win = sum(winners)
    gross_loss = abs(sum(losers))

    probabilities = [float(r["prob_positive"]) for r in records
                     if is_finite(r.get("prob_positive")) and r.get("hit") is not None]
    outcomes = [bool(r["hit"]) for r in records
                if is_finite(r.get("prob_positive")) and r.get("hit") is not None]

    thesis = [str(r.get("thesis_outcome", "")) for r in records]
    succeeded = sum(1 for t in thesis if t == "succeeded")
    lucky = sum(1 for t in thesis if t == "luck")

    calibrator = (Calibrator.fit(probabilities, outcomes, min_samples_per_bin=10)
                  if len(probabilities) >= 40 else None)

    return BucketPerformance(
        kind=kind, value=value, samples=n,
        hit_rate=(sum(hits) / len(hits)) if hits else None,
        avg_return=mean(returns), avg_excess=mean(excess),
        median_return=float(np.median(returns)) if returns else None,
        sharpe=_pseudo_sharpe(returns), sortino=_pseudo_sortino(returns),
        max_drawdown=min(returns) if returns else None,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        avg_winner=mean(winners), avg_loser=mean(losers),
        brier=brier_score(probabilities, outcomes) if probabilities else None,
        brier_skill=brier_skill_score(probabilities, outcomes) if probabilities else None,
        calibration_error=calibrator.calibration_error() if calibrator else None,
        thesis_success_rate=succeeded / n if n else None,
        luck_rate=lucky / n if n else None)


def _pseudo_sharpe(returns: Sequence[float]) -> float | None:
    """Mean/stdev of realised prediction returns.

    Deliberately *not* annualised: these are horizon returns from overlapping
    predictions, and annualising them would imply a portfolio that was never
    simulated. Use the backtest engine for portfolio-level Sharpe.
    """
    if len(returns) < MIN_SAMPLES:
        return None
    sd = float(np.std(returns, ddof=1))
    if sd < 1e-9:
        return None
    return float(np.mean(returns) / sd)


def _pseudo_sortino(returns: Sequence[float]) -> float | None:
    if len(returns) < MIN_SAMPLES:
        return None
    downside = [r for r in returns if r < 0]
    if not downside:
        return None
    dd = float(np.sqrt(np.mean(np.square(downside))))
    if dd < 1e-9:
        return None
    return float(np.mean(returns) / dd)


BUCKETERS: dict[str, Any] = {
    "overall": lambda r: "all",
    "asset_class": lambda r: str(r.get("asset_class") or "unknown"),
    "sector": lambda r: str(r.get("sector") or "unknown"),
    "regime": lambda r: str(r.get("regime") or "unknown"),
    "horizon": lambda r: str(r.get("horizon") or "unknown"),
    "recommendation": lambda r: str(r.get("recommendation") or "unknown"),
    "confidence": lambda r: _confidence_bucket(r.get("confidence")),
    "data_quality": lambda r: str(r.get("data_quality") or "unknown"),
}


def _confidence_bucket(value: Any) -> str:
    if not is_finite(value):
        return "unknown"
    v = float(value)
    for low in (0.8, 0.7, 0.6, 0.5, 0.4):
        if v >= low:
            return f"{low:.0%}+"
    return "<40%"


def compute(records: Sequence[Mapping[str, Any]], *,
            kinds: Sequence[str] = tuple(BUCKETERS),
            min_samples: int = MIN_SAMPLES) -> list[BucketPerformance]:
    """Performance for every requested bucket kind."""
    out: list[BucketPerformance] = []
    for kind in kinds:
        bucketer = BUCKETERS.get(kind)
        if bucketer is None:
            continue
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for record in records:
            groups[bucketer(record)].append(record)
        for value, group in sorted(groups.items()):
            out.append(_stats(group, kind, value, min_samples=min_samples))
    return out


def measured_base_rates(records: Sequence[Mapping[str, Any]], *,
                        min_samples: int = 100) -> dict[str, dict[str, float]]:
    """Observed positive-return frequency by asset class and horizon.

    These replace the hard-coded priors in ``analysis.probability`` once enough
    outcomes exist -- the system learns the base rate of its own universe rather
    than inheriting an assumption.
    """
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for record in records:
        actual = record.get("actual_return")
        if not is_finite(actual):
            continue
        key = (str(record.get("asset_class") or "equity"),
               str(record.get("horizon") or "1y"))
        groups[key].append(1.0 if float(actual) > 0 else 0.0)

    out: dict[str, dict[str, float]] = defaultdict(dict)
    for (asset_class, horizon), values in groups.items():
        if len(values) >= min_samples:
            out[asset_class][horizon] = round(float(np.mean(values)), 4)
    return dict(out)


def systematic_errors(buckets: Sequence[BucketPerformance], *,
                      hit_rate_floor: float = 0.45,
                      calibration_ceiling: float = 0.12) -> list[dict[str, Any]]:
    """Find where the model is reliably wrong -- the input to retraining.

    A bucket is only reported when it has enough samples, so noise is not
    mistaken for a systematic error.
    """
    findings: list[dict[str, Any]] = []
    for bucket in buckets:
        if not bucket.sufficient:
            continue
        if bucket.hit_rate is not None and bucket.hit_rate < hit_rate_floor:
            findings.append({
                "kind": bucket.kind, "value": bucket.value, "samples": bucket.samples,
                "issue": "low_hit_rate",
                "detail": f"directional accuracy of {bucket.hit_rate:.0%} in "
                          f"{bucket.kind}={bucket.value}",
                "severity": "high" if bucket.hit_rate < 0.4 else "medium"})
        if (bucket.calibration_error is not None
                and bucket.calibration_error > calibration_ceiling):
            findings.append({
                "kind": bucket.kind, "value": bucket.value, "samples": bucket.samples,
                "issue": "miscalibrated",
                "detail": f"stated probabilities are off by "
                          f"{bucket.calibration_error:.0%} on average in "
                          f"{bucket.kind}={bucket.value}",
                "severity": "high"})
        if bucket.brier_skill is not None and bucket.brier_skill < 0:
            findings.append({
                "kind": bucket.kind, "value": bucket.value, "samples": bucket.samples,
                "issue": "no_skill",
                "detail": f"probability forecasts add no information over the base "
                          f"rate (Brier skill {bucket.brier_skill:+.2f}) in "
                          f"{bucket.kind}={bucket.value}",
                "severity": "high"})
        if bucket.luck_rate is not None and bucket.luck_rate > 0.35:
            findings.append({
                "kind": bucket.kind, "value": bucket.value, "samples": bucket.samples,
                "issue": "returns_from_beta",
                "detail": f"{bucket.luck_rate:.0%} of wins produced no excess return "
                          f"over the benchmark: results reflect market exposure "
                          f"more than selection",
                "severity": "medium"})
    return findings


def confidence_is_informative(buckets: Sequence[BucketPerformance]) -> dict[str, Any]:
    """Does higher stated confidence actually mean higher accuracy?

    If it does not, the confidence number is decoration and must be fixed or
    withdrawn -- so this is checked explicitly rather than assumed.
    """
    confidence_buckets = [b for b in buckets
                          if b.kind == "confidence" and b.sufficient
                          and b.hit_rate is not None]
    if len(confidence_buckets) < 2:
        return {"assessable": False,
                "note": "not enough populated confidence buckets to test"}
    order = {"<40%": 0, "40%+": 1, "50%+": 2, "60%+": 3, "70%+": 4, "80%+": 5}
    ranked = sorted(confidence_buckets, key=lambda b: order.get(b.value, 0))
    rates = [b.hit_rate for b in ranked]
    monotone = all(b >= a - 0.05 for a, b in zip(rates, rates[1:]))
    spread = max(rates) - min(rates)
    return {"assessable": True, "monotone": monotone, "spread": round(spread, 3),
            "buckets": [{"confidence": b.value, "hit_rate": round(b.hit_rate, 3),
                         "samples": b.samples} for b in ranked],
            "verdict": ("confidence tracks accuracy" if monotone and spread > 0.05
                        else "confidence does NOT track accuracy: it should be "
                             "recalibrated or reported with a warning")}
