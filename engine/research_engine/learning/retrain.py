"""Guarded learning.

The system improves through *bounded, versioned, validated* updates -- never by
rewriting its own logic. Concretely, learning is limited to:

1. **Weight updates** -- factor weights nudged toward what has actually worked,
   with a hard cap on how far they may move in one step.
2. **Calibration** -- mapping stated probabilities onto observed frequencies.
3. **Base rates** -- replacing priors with measured frequencies.
4. **Model selection** -- promoting a candidate only when it beats the incumbent
   out of sample by a required margin.

Every proposal is validated before it is accepted, and every accepted change
creates a new model version. A rejected proposal is logged with its reason, so
the system's refusal to change is as auditable as its changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from research_engine.config.settings import normalise_weights
from research_engine.core.logging import get_logger
from research_engine.core.numeric import clamp, is_finite, mean
from research_engine.learning.performance import BucketPerformance

log = get_logger(__name__)


@dataclass
class WeightProposal:
    current: dict[str, float]
    proposed: dict[str, float]
    changes: dict[str, float]
    rationale: list[str]
    accepted: bool
    rejection_reason: str | None = None
    samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"current": {k: round(v, 4) for k, v in self.current.items()},
                "proposed": {k: round(v, 4) for k, v in self.proposed.items()},
                "changes": {k: round(v, 4) for k, v in self.changes.items()},
                "rationale": self.rationale, "accepted": self.accepted,
                "rejection_reason": self.rejection_reason, "samples": self.samples}


def factor_effectiveness(records: Sequence[Mapping[str, Any]], *,
                         min_samples: int = 100) -> dict[str, dict[str, Any]]:
    """Measure whether each factor's contribution predicted excess return.

    Uses the correlation between a factor's recorded contribution and the
    realised excess return. Correlation is not causation -- and it is labelled
    as such in the output -- but a factor whose contribution has *no*
    relationship to outcomes across hundreds of predictions has not earned its
    weight.
    """
    by_factor: dict[str, list[tuple[float, float]]] = {}
    for record in records:
        outcome = record.get("excess_return")
        if outcome is None or not is_finite(outcome):
            outcome = record.get("actual_return")
        if not is_finite(outcome):
            continue
        for name, contribution in (record.get("factors") or {}).items():
            if not is_finite(contribution):
                continue
            by_factor.setdefault(name, []).append((float(contribution), float(outcome)))

    out: dict[str, dict[str, Any]] = {}
    for name, pairs in by_factor.items():
        if len(pairs) < min_samples:
            out[name] = {"samples": len(pairs), "sufficient": False,
                         "note": f"only {len(pairs)} samples; need {min_samples}"}
            continue
        xs = np.array([p[0] for p in pairs])
        ys = np.array([p[1] for p in pairs])
        if xs.std(ddof=1) < 1e-9 or ys.std(ddof=1) < 1e-9:
            out[name] = {"samples": len(pairs), "sufficient": False,
                         "note": "no variation in factor or outcome"}
            continue
        correlation = float(np.corrcoef(xs, ys)[0, 1])
        # split by factor tertile to see whether high scores actually did better
        order = np.argsort(xs)
        third = max(1, len(order) // 3)
        low = float(np.mean(ys[order[:third]]))
        high = float(np.mean(ys[order[-third:]]))
        out[name] = {
            "samples": len(pairs), "sufficient": True,
            "correlation": round(correlation, 4),
            "top_tertile_return": round(high, 4),
            "bottom_tertile_return": round(low, 4),
            "spread": round(high - low, 4),
            "interpretation": ("association only; not evidence that the factor "
                               "caused the outcome"),
        }
    return out


def propose_weights(current_weights: Mapping[str, float],
                    effectiveness: Mapping[str, Mapping[str, Any]], *,
                    max_change: float = 0.25, min_samples: int = 100,
                    total_samples: int = 0) -> WeightProposal:
    """Nudge weights toward factors with measured spread, within a hard cap.

    The cap is the important part: a model that can reweight itself freely will
    chase noise. Anything beyond ``max_change`` relative movement per update is
    refused.
    """
    current = dict(current_weights)
    rationale: list[str] = []
    if total_samples < min_samples:
        return WeightProposal(current, current, {}, [], False,
                              f"only {total_samples} evaluated predictions; "
                              f"{min_samples} required before touching weights",
                              total_samples)

    proposed: dict[str, float] = {}
    for name, weight in current.items():
        stats = effectiveness.get(name)
        if not stats or not stats.get("sufficient"):
            proposed[name] = weight
            continue
        spread = float(stats.get("spread") or 0.0)
        # Map spread onto a bounded multiplier: +-max_change at +-10pp spread.
        multiplier = 1.0 + clamp(spread / 0.10, -1.0, 1.0) * max_change
        proposed[name] = weight * multiplier
        if abs(multiplier - 1.0) > 0.02:
            direction = "up" if multiplier > 1 else "down"
            rationale.append(
                f"{name}: top-tertile predictions returned "
                f"{stats['top_tertile_return']:+.1%} vs "
                f"{stats['bottom_tertile_return']:+.1%} for the bottom tertile "
                f"({stats['samples']} samples) -> weight adjusted {direction} "
                f"by {abs(multiplier - 1) * 100:.0f}%")

    try:
        proposed = normalise_weights(proposed)
    except Exception as exc:
        return WeightProposal(current, current, {}, rationale, False,
                              f"proposed weights were invalid: {exc}", total_samples)

    changes = {k: proposed[k] - current.get(k, 0.0) for k in proposed}
    largest = max((abs(v) for v in changes.values()), default=0.0)
    if largest > max_change:
        return WeightProposal(current, current, changes, rationale, False,
                              f"largest proposed weight change {largest:.2f} exceeds "
                              f"the {max_change:.2f} per-update cap", total_samples)
    if not rationale:
        return WeightProposal(current, current, changes, rationale, False,
                              "no factor showed a measurable effectiveness spread",
                              total_samples)
    return WeightProposal(current, proposed, changes, rationale, True, None,
                          total_samples)


@dataclass
class PromotionDecision:
    promote: bool
    candidate: str
    incumbent: str | None
    reason: str
    comparison: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"promote": self.promote, "candidate": self.candidate,
                "incumbent": self.incumbent, "reason": self.reason,
                "comparison": self.comparison}


def should_promote(candidate_metrics: Mapping[str, Any],
                   incumbent_metrics: Mapping[str, Any] | None, *,
                   candidate_version: str, incumbent_version: str | None,
                   required_improvement: float = 0.02,
                   min_samples: int = 200) -> PromotionDecision:
    """Promote only on a real, sufficiently-sampled out-of-sample improvement.

    Checks, in order: sample size, calibration (a better-scoring but
    miscalibrated model is not better), then the primary metric with a required
    margin. Ties go to the incumbent -- churn has costs and the incumbent has
    more observed history.
    """
    samples = int(candidate_metrics.get("samples") or 0)
    if samples < min_samples:
        return PromotionDecision(
            False, candidate_version, incumbent_version,
            f"candidate has only {samples} out-of-sample predictions; "
            f"{min_samples} required")

    if incumbent_metrics is None:
        return PromotionDecision(
            True, candidate_version, None,
            "no active model in this family; promoting the validated candidate",
            {"candidate": dict(candidate_metrics)})

    candidate_error = candidate_metrics.get("calibration_error")
    incumbent_error = incumbent_metrics.get("calibration_error")
    if (candidate_error is not None and incumbent_error is not None
            and candidate_error > incumbent_error + 0.05):
        return PromotionDecision(
            False, candidate_version, incumbent_version,
            f"candidate is materially worse calibrated "
            f"({candidate_error:.1%} vs {incumbent_error:.1%}) despite other metrics",
            {"candidate": dict(candidate_metrics),
             "incumbent": dict(incumbent_metrics)})

    primary = "avg_excess" if candidate_metrics.get("avg_excess") is not None else "hit_rate"
    candidate_value = candidate_metrics.get(primary)
    incumbent_value = incumbent_metrics.get(primary)
    if candidate_value is None or incumbent_value is None:
        return PromotionDecision(
            False, candidate_version, incumbent_version,
            f"cannot compare: {primary} unavailable for one of the models")

    improvement = float(candidate_value) - float(incumbent_value)
    comparison = {"metric": primary, "candidate": round(float(candidate_value), 4),
                  "incumbent": round(float(incumbent_value), 4),
                  "improvement": round(improvement, 4),
                  "required": required_improvement}
    if improvement < required_improvement:
        return PromotionDecision(
            False, candidate_version, incumbent_version,
            f"improvement of {improvement:+.3f} in {primary} does not clear the "
            f"{required_improvement:+.3f} hurdle; keeping the incumbent",
            comparison)
    return PromotionDecision(
        True, candidate_version, incumbent_version,
        f"candidate improves {primary} by {improvement:+.3f} over "
        f"{samples} out-of-sample predictions", comparison)


def learning_report(*, effectiveness: Mapping[str, Mapping[str, Any]],
                    proposal: WeightProposal,
                    systematic: Sequence[Mapping[str, Any]],
                    confidence_check: Mapping[str, Any],
                    promotion: PromotionDecision | None = None) -> dict[str, Any]:
    """The daily self-evaluation record, stored for audit."""
    return {
        "factor_effectiveness": {k: dict(v) for k, v in effectiveness.items()},
        "weight_proposal": proposal.to_dict(),
        "systematic_errors": list(systematic),
        "confidence_calibration": dict(confidence_check),
        "promotion": promotion.to_dict() if promotion else None,
        "policy": ("Learning is limited to bounded weight updates, calibration, "
                   "measured base rates and model selection. The engine never "
                   "rewrites its own logic, and every accepted change creates a "
                   "new immutable model version."),
    }
