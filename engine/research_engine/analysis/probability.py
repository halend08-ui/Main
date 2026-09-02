"""Probability forecasting and confidence calibration.

The system never says "this will rise 40%". It says "over 12 months, the
estimated probability of a positive return is X%, with a bear/base/bull range
of A/B/C", and it labels each of those as a model prediction rather than an
observation.

How the probability is built:

1. **Base rate first.** Start from the historical unconditional frequency of a
   positive return over the horizon for this asset class -- the anchor that
   stops the model drifting into storytelling.
2. **Evidence tilts it.** The composite score and ensemble agreement move the
   probability away from the base rate by a bounded amount.
3. **Regime and macro adjust it.** Small, bounded, and always itemised.
4. **Calibration corrects it.** Learned reliability from past predictions maps
   the raw probability onto one that has actually held up.
5. **Uncertainty widens it.** Poor data or thin history pulls the probability
   toward 50% -- the honest expression of "we do not know".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from research_engine.core.numeric import clamp, is_finite, mean
from research_engine.core.types import ClaimType, DataQuality, Horizon

#: Unconditional base rates for a positive nominal return, by asset class and
#: horizon. Sourced from long-run index behaviour and used only as a prior; the
#: learning layer replaces these with measured rates once enough outcomes exist.
DEFAULT_BASE_RATES: dict[str, dict[str, float]] = {
    "equity": {"1w": 0.53, "1m": 0.56, "3m": 0.60, "6m": 0.63, "1y": 0.68,
               "3y": 0.74, "5y": 0.80, "10y": 0.88},
    "crypto": {"1w": 0.50, "1m": 0.51, "3m": 0.52, "6m": 0.53, "1y": 0.55,
               "3y": 0.58, "5y": 0.60, "10y": 0.62},
}

MAX_EVIDENCE_TILT = 0.22        # how far evidence alone may move the base rate
MAX_TOTAL_DEVIATION = 0.34      # hard cap on distance from the base rate


@dataclass
class ProbabilityForecast:
    horizon: Horizon
    prob_positive: float
    base_rate: float
    expected_return: dict[str, float | None]     # bear / base / bull
    scenario_probabilities: dict[str, float]
    confidence: float
    adjustments: list[dict[str, Any]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    claim_type: ClaimType = ClaimType.MODEL_PREDICTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon.value,
            "probability_positive": round(self.prob_positive, 3),
            "base_rate": round(self.base_rate, 3),
            "expected_return": {k: (round(v, 4) if v is not None else None)
                                for k, v in self.expected_return.items()},
            "scenario_probabilities": {k: round(v, 3)
                                       for k, v in self.scenario_probabilities.items()},
            "confidence": round(self.confidence, 3),
            "adjustments": self.adjustments,
            "caveats": self.caveats,
            "claim_type": self.claim_type.value,
            "disclaimer": ("Probability estimate from a model, not a guarantee. "
                           "Ranges are scenario outputs, not confidence intervals."),
        }


def base_rate(asset_class: str, horizon: Horizon,
              learned: Mapping[str, Mapping[str, float]] | None = None) -> float:
    """Historical frequency of a positive return, preferring measured rates."""
    if learned:
        measured = (learned.get(asset_class) or {}).get(horizon.value)
        if measured is not None and 0.0 < measured < 1.0:
            return float(measured)
    table = DEFAULT_BASE_RATES.get(asset_class, DEFAULT_BASE_RATES["equity"])
    return table.get(horizon.value, 0.55)


def forecast(*, asset_class: str, horizon: Horizon, score: float | None,
             ensemble_agreement: float | None = None,
             scenario_returns: Mapping[str, float | None] | None = None,
             data_quality: DataQuality = DataQuality.GOOD,
             regime_adjustment: float = 0.0,
             macro_adjustment: float = 0.0,
             risk_penalty: float = 0.0,
             learned_base_rates: Mapping[str, Mapping[str, float]] | None = None,
             calibrator: "Calibrator | None" = None,
             observations: int | None = None) -> ProbabilityForecast:
    """Produce a calibrated probability with every adjustment itemised."""
    anchor = base_rate(asset_class, horizon, learned_base_rates)
    probability = anchor
    adjustments: list[dict[str, Any]] = [
        {"source": "base_rate", "value": round(anchor, 3),
         "detail": f"unconditional {horizon.value} positive-return frequency for "
                   f"{asset_class}"}]
    caveats: list[str] = []

    # 1. evidence
    if score is not None:
        tilt = ((score - 55.0) / 45.0) * MAX_EVIDENCE_TILT
        tilt = clamp(tilt, -MAX_EVIDENCE_TILT, MAX_EVIDENCE_TILT)
        probability += tilt
        adjustments.append({"source": "composite_score", "value": round(tilt, 3),
                            "detail": f"score of {score:.0f}/100"})
    else:
        caveats.append("no composite score: probability is the base rate only")

    # 2. model agreement -- disagreement pulls toward the base rate
    if ensemble_agreement is not None:
        shrink = (1.0 - clamp(ensemble_agreement, 0.0, 1.0)) * 0.5
        pulled = probability - (probability - anchor) * shrink
        if abs(pulled - probability) > 1e-9:
            adjustments.append({"source": "model_disagreement",
                                "value": round(pulled - probability, 3),
                                "detail": f"agreement {ensemble_agreement:.0%}: "
                                          f"pulled toward the base rate"})
        probability = pulled

    # 3. regime / macro
    for name, value in (("regime", regime_adjustment), ("macro", macro_adjustment)):
        if value:
            bounded = clamp(value, -0.10, 0.10)
            probability += bounded
            adjustments.append({"source": name, "value": round(bounded, 3),
                                "detail": f"{name} conditions"})

    # 4. risk penalty (permanent-loss risk lowers the odds of a good outcome)
    if risk_penalty:
        bounded = clamp(-abs(risk_penalty), -0.15, 0.0)
        probability += bounded
        adjustments.append({"source": "risk", "value": round(bounded, 3),
                            "detail": "elevated permanent-loss risk"})

    # cap total deviation from the anchor
    probability = clamp(probability, anchor - MAX_TOTAL_DEVIATION,
                        anchor + MAX_TOTAL_DEVIATION)

    # 5. data quality shrinkage toward 0.5
    quality_shrink = {DataQuality.EXCELLENT: 0.0, DataQuality.GOOD: 0.05,
                      DataQuality.FAIR: 0.20, DataQuality.POOR: 0.45,
                      DataQuality.INSUFFICIENT: 0.85}[data_quality]
    if quality_shrink:
        before = probability
        probability = probability + (0.5 - probability) * quality_shrink
        adjustments.append({"source": "data_quality",
                            "value": round(probability - before, 3),
                            "detail": f"{data_quality.value} data: pulled toward 50%"})
        if data_quality <= DataQuality.POOR:
            caveats.append(f"data quality is {data_quality.value}; treat the "
                           f"probability as weakly informative")

    if observations is not None and observations < 250:
        before = probability
        probability = probability + (0.5 - probability) * 0.3
        adjustments.append({"source": "short_history",
                            "value": round(probability - before, 3),
                            "detail": f"only {observations} observations"})
        caveats.append("limited history: the estimate leans heavily on the base rate")

    # 6. calibration
    raw = probability
    if calibrator is not None and calibrator.is_fitted:
        probability = calibrator.apply(probability)
        if abs(probability - raw) > 0.005:
            adjustments.append({"source": "calibration",
                                "value": round(probability - raw, 3),
                                "detail": "historical reliability correction"})

    probability = clamp(probability, 0.02, 0.98)

    scenarios = scenario_returns or {}
    scenario_probabilities = _scenario_weights(probability)
    expected = {"bear": scenarios.get("bear"), "base": scenarios.get("base"),
                "bull": scenarios.get("bull")}
    expected["probability_weighted"] = _weighted_expectation(expected,
                                                             scenario_probabilities)

    confidence = _confidence(probability, data_quality, ensemble_agreement,
                             observations, calibrator)
    return ProbabilityForecast(horizon=horizon, prob_positive=probability,
                               base_rate=anchor, expected_return=expected,
                               scenario_probabilities=scenario_probabilities,
                               confidence=confidence, adjustments=adjustments,
                               caveats=caveats)


def _scenario_weights(prob_positive: float) -> dict[str, float]:
    """Split probability mass across bear/base/bull consistently with P(up).

    The base case always keeps the largest share; the tails shift with the
    directional view. Weights sum to 1 by construction.
    """
    bull = clamp(prob_positive * 0.45, 0.05, 0.5)
    bear = clamp((1.0 - prob_positive) * 0.55, 0.05, 0.6)
    base = max(0.05, 1.0 - bull - bear)
    total = bull + bear + base
    return {"bear": bear / total, "base": base / total, "bull": bull / total}


def _weighted_expectation(returns: Mapping[str, float | None],
                          weights: Mapping[str, float]) -> float | None:
    pairs = [(returns.get(k), weights.get(k, 0.0)) for k in ("bear", "base", "bull")]
    usable = [(r, w) for r, w in pairs if r is not None and w > 0]
    if len(usable) < 2:
        return None
    total = sum(w for _, w in usable)
    return sum(r * w for r, w in usable) / total


def _confidence(probability: float, quality: DataQuality,
                agreement: float | None, observations: int | None,
                calibrator: "Calibrator | None") -> float:
    """Confidence is about the *estimate*, not the direction.

    A 50/50 probability held with strong evidence is a confident "coin flip";
    a 70% probability from thin data is not confident at all.
    """
    from research_engine.quality.grading import confidence_multiplier

    conviction = abs(probability - 0.5) * 2.0        # 0..1
    base = 0.35 + 0.4 * conviction
    base *= confidence_multiplier(quality)
    if agreement is not None:
        base *= 0.6 + 0.4 * clamp(agreement, 0.0, 1.0)
    if observations is not None:
        base *= clamp(0.5 + observations / 1000.0, 0.5, 1.0)
    if calibrator is not None and calibrator.is_fitted:
        base *= clamp(1.0 - calibrator.overconfidence_penalty(), 0.5, 1.0)
    else:
        base *= 0.9      # unproven calibration is itself a reason for humility
    return clamp(base, 0.05, 0.9)


# ----------------------------------------------------------- calibration ---
@dataclass
class CalibrationBin:
    low: float
    high: float
    predicted_mean: float
    observed_rate: float
    samples: int

    def to_dict(self) -> dict[str, Any]:
        return {"bin_low": round(self.low, 3), "bin_high": round(self.high, 3),
                "predicted_mean": round(self.predicted_mean, 4),
                "observed_rate": round(self.observed_rate, 4), "samples": self.samples}


class Calibrator:
    """Reliability-diagram calibration with monotone interpolation.

    Fitted from (predicted probability, realised outcome) pairs. Bins with too
    few samples are merged into their neighbours rather than trusted, and the
    mapping is forced monotone so a higher raw probability never maps to a lower
    calibrated one.
    """

    def __init__(self, bins: Sequence[CalibrationBin] = (), *,
                 min_samples_per_bin: int = 20) -> None:
        self.bins = list(bins)
        self.min_samples_per_bin = min_samples_per_bin

    @property
    def is_fitted(self) -> bool:
        return len(self.bins) >= 2

    @classmethod
    def fit(cls, predictions: Sequence[float], outcomes: Sequence[bool], *,
            n_bins: int = 10, min_samples_per_bin: int = 20) -> "Calibrator":
        pairs = [(float(p), bool(o)) for p, o in zip(predictions, outcomes)
                 if is_finite(p) and 0.0 <= p <= 1.0]
        if len(pairs) < min_samples_per_bin * 2:
            return cls([], min_samples_per_bin=min_samples_per_bin)
        pairs.sort()
        # Equal-count bins: equal-width bins leave empty tails on real data.
        per_bin = max(min_samples_per_bin, len(pairs) // n_bins)
        bins: list[CalibrationBin] = []
        for start in range(0, len(pairs), per_bin):
            chunk = pairs[start:start + per_bin]
            if len(chunk) < min_samples_per_bin and bins:
                # merge the short tail into the previous bin
                previous = bins.pop()
                merged_n = previous.samples + len(chunk)
                merged_pred = ((previous.predicted_mean * previous.samples
                                + sum(p for p, _ in chunk)) / merged_n)
                merged_obs = ((previous.observed_rate * previous.samples
                               + sum(1 for _, o in chunk if o)) / merged_n)
                bins.append(CalibrationBin(previous.low, chunk[-1][0], merged_pred,
                                           merged_obs, merged_n))
                continue
            if len(chunk) < min_samples_per_bin:
                continue
            bins.append(CalibrationBin(
                low=chunk[0][0], high=chunk[-1][0],
                predicted_mean=float(np.mean([p for p, _ in chunk])),
                observed_rate=float(np.mean([1.0 if o else 0.0 for _, o in chunk])),
                samples=len(chunk)))
        return cls(bins, min_samples_per_bin=min_samples_per_bin)

    def apply(self, probability: float) -> float:
        """Map a raw probability onto the observed frequency."""
        if not self.is_fitted:
            return probability
        xs = [b.predicted_mean for b in self.bins]
        ys = _monotone(list(b.observed_rate for b in self.bins))
        p = clamp(probability, 0.0, 1.0)
        if p <= xs[0]:
            return clamp(ys[0], 0.02, 0.98)
        if p >= xs[-1]:
            return clamp(ys[-1], 0.02, 0.98)
        for i in range(1, len(xs)):
            if p <= xs[i]:
                span = xs[i] - xs[i - 1]
                if span <= 1e-9:
                    return clamp(ys[i], 0.02, 0.98)
                t = (p - xs[i - 1]) / span
                return clamp(ys[i - 1] + t * (ys[i] - ys[i - 1]), 0.02, 0.98)
        return clamp(ys[-1], 0.02, 0.98)

    def calibration_error(self) -> float | None:
        """Expected calibration error: sample-weighted |predicted - observed|."""
        if not self.bins:
            return None
        total = sum(b.samples for b in self.bins)
        if not total:
            return None
        return sum(abs(b.predicted_mean - b.observed_rate) * b.samples
                   for b in self.bins) / total

    def overconfidence_penalty(self) -> float:
        """How much predictions overshoot reality, as a 0..1 penalty.

        Only *overshooting* is penalised: a model that is systematically
        under-confident is wrong too, but it does not cause the harm that
        overconfidence does.
        """
        if not self.bins:
            return 0.0
        total = sum(b.samples for b in self.bins)
        if not total:
            return 0.0
        overshoot = sum(max(0.0, b.predicted_mean - b.observed_rate) * b.samples
                        for b in self.bins) / total
        return clamp(overshoot * 2.0, 0.0, 0.5)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [b.to_dict() for b in self.bins]


def _monotone(values: Sequence[float]) -> list[float]:
    """Pool-adjacent-violators: enforce a non-decreasing sequence."""
    out = list(values)
    changed = True
    while changed:
        changed = False
        for i in range(1, len(out)):
            if out[i] < out[i - 1]:
                pooled = (out[i] + out[i - 1]) / 2.0
                out[i] = out[i - 1] = pooled
                changed = True
    return out


def brier_score(predictions: Sequence[float], outcomes: Sequence[bool]
                ) -> float | None:
    """Mean squared error of probabilistic predictions. Lower is better."""
    pairs = [(float(p), 1.0 if o else 0.0) for p, o in zip(predictions, outcomes)
             if is_finite(p)]
    if len(pairs) < 10:
        return None
    return float(np.mean([(p - o) ** 2 for p, o in pairs]))


def brier_skill_score(predictions: Sequence[float], outcomes: Sequence[bool]
                      ) -> float | None:
    """Brier score relative to always predicting the base rate.

    Positive means the model adds information; zero or negative means it does
    not beat simply quoting the historical frequency -- which is the comparison
    that matters and is usually omitted.
    """
    brier = brier_score(predictions, outcomes)
    if brier is None:
        return None
    outcome_values = [1.0 if o else 0.0 for o in outcomes]
    climatology = float(np.mean(outcome_values))
    reference = float(np.mean([(climatology - o) ** 2 for o in outcome_values]))
    if reference <= 1e-12:
        return None
    return 1.0 - brier / reference
