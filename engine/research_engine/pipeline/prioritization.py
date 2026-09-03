"""Research prioritisation and the compute funnel.

Thousands of assets cannot receive deep analysis every day, and pretending
otherwise produces either a bankrupt compute budget or shallow work everywhere.
The funnel spends effort where it can change a decision:

    Stage 1  cheap screen over the whole universe      (thousands)
    Stage 2  standard analysis                         (hundreds)
    Stage 3  deep research: valuation, events, memo    (dozens)
    Stage 4  high-conviction review with bear case     (a handful)

Priority is not just "highest score". An asset already understood and unchanged
is low value to re-analyse; an asset whose fundamentals just inflected is high
value even if its score is mediocre. Novelty, magnitude of change, data quality
and staleness all enter the ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.numeric import clamp, is_finite
from research_engine.core.types import DataQuality


@dataclass(frozen=True, slots=True)
class PriorityInputs:
    symbol: str
    score: float | None = None
    previous_score: float | None = None
    anomaly_priority: float = 0.0
    days_since_analysis: int | None = None
    data_quality: DataQuality | None = None
    is_new: bool = False
    is_held: bool = False
    market_cap: float | None = None
    fundamental_change: float | None = None      # e.g. growth acceleration
    upcoming_catalyst_days: int | None = None


@dataclass(frozen=True, slots=True)
class PriorityResult:
    symbol: str
    priority: float
    components: Mapping[str, float]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "priority": round(self.priority, 4),
                "components": {k: round(v, 4) for k, v in self.components.items()},
                "reasons": list(self.reasons)}


#: Component weights. Configurable, but the ordering is intentional: a position
#: you hold and a thing that just changed matter more than a high static score.
DEFAULT_WEIGHTS = {
    "opportunity": 0.30,
    "change": 0.22,
    "anomaly": 0.15,
    "holding": 0.12,
    "staleness": 0.10,
    "novelty": 0.06,
    "catalyst": 0.05,
}


def score_priority(inputs: PriorityInputs,
                   weights: Mapping[str, float] | None = None) -> PriorityResult:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    components: dict[str, float] = {}
    reasons: list[str] = []

    if inputs.score is not None:
        components["opportunity"] = clamp(inputs.score / 100.0, 0.0, 1.0)
        if inputs.score >= 75:
            reasons.append(f"high score ({inputs.score:.0f})")
    else:
        components["opportunity"] = 0.35      # unknown is worth investigating

    if inputs.score is not None and inputs.previous_score is not None:
        delta = abs(inputs.score - inputs.previous_score)
        components["change"] = clamp(delta / 15.0, 0.0, 1.0)
        if delta >= 8:
            direction = "up" if inputs.score > inputs.previous_score else "down"
            reasons.append(f"score moved {direction} {delta:.0f} points")
    elif inputs.fundamental_change is not None:
        components["change"] = clamp(abs(inputs.fundamental_change) / 0.2, 0.0, 1.0)
        if abs(inputs.fundamental_change) > 0.1:
            reasons.append(f"fundamentals shifted {inputs.fundamental_change:+.0%}")
    else:
        components["change"] = 0.0

    components["anomaly"] = clamp(inputs.anomaly_priority, 0.0, 1.0)
    if inputs.anomaly_priority > 0.5:
        reasons.append("anomaly detected")

    components["holding"] = 1.0 if inputs.is_held else 0.0
    if inputs.is_held:
        reasons.append("currently held: monitoring obligation")

    if inputs.days_since_analysis is None:
        components["staleness"] = 1.0
        reasons.append("never analysed")
    else:
        components["staleness"] = clamp(inputs.days_since_analysis / 30.0, 0.0, 1.0)
        if inputs.days_since_analysis > 30:
            reasons.append(f"last analysed {inputs.days_since_analysis} days ago")

    components["novelty"] = 1.0 if inputs.is_new else 0.0
    if inputs.is_new:
        reasons.append("newly discovered")

    if inputs.upcoming_catalyst_days is not None and inputs.upcoming_catalyst_days >= 0:
        components["catalyst"] = clamp(1.0 - inputs.upcoming_catalyst_days / 30.0,
                                       0.0, 1.0)
        if inputs.upcoming_catalyst_days <= 14:
            reasons.append(f"catalyst in {inputs.upcoming_catalyst_days} days")
    else:
        components["catalyst"] = 0.0

    priority = sum(components[k] * w.get(k, 0.0) for k in components)

    # Poor data caps priority: spending deep-research compute on an asset whose
    # inputs are unreliable produces a confident-looking wrong answer.
    if inputs.data_quality is not None and inputs.data_quality <= DataQuality.POOR:
        priority *= 0.5
        reasons.append(f"priority halved: {inputs.data_quality.value} data quality")

    return PriorityResult(inputs.symbol, clamp(priority, 0.0, 1.0), components,
                          tuple(reasons))


def rank(candidates: Sequence[PriorityInputs],
         weights: Mapping[str, float] | None = None) -> list[PriorityResult]:
    return sorted((score_priority(c, weights) for c in candidates),
                  key=lambda r: r.priority, reverse=True)


@dataclass
class FunnelConfig:
    stage1_max: int = 5000
    stage2_max: int = 600
    stage3_max: int = 120
    stage4_max: int = 25

    def limit(self, stage: int) -> int:
        return {1: self.stage1_max, 2: self.stage2_max, 3: self.stage3_max,
                4: self.stage4_max}[stage]


@dataclass
class FunnelResult:
    stages: dict[int, list[str]] = field(default_factory=dict)
    dropped: dict[int, list[tuple[str, str]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"stages": {str(k): v for k, v in self.stages.items()},
                "counts": {str(k): len(v) for k, v in self.stages.items()},
                "dropped": {str(k): [{"symbol": s, "reason": r} for s, r in v]
                            for k, v in self.dropped.items()}}


def run_funnel(ranked: Sequence[PriorityResult], config: FunnelConfig, *,
               stage2_screen: Mapping[str, bool] | None = None,
               stage3_screen: Mapping[str, bool] | None = None,
               stage4_screen: Mapping[str, bool] | None = None) -> FunnelResult:
    """Advance assets through the funnel, recording why each was dropped.

    The screens are supplied by the caller (they need the analysis results the
    previous stage produced); this function owns only the ordering, the caps and
    the audit trail.
    """
    result = FunnelResult()
    result.stages[1] = [r.symbol for r in ranked[:config.limit(1)]]
    result.dropped[1] = [(r.symbol, "below stage-1 capacity")
                         for r in ranked[config.limit(1):]]

    for stage, screen in ((2, stage2_screen), (3, stage3_screen), (4, stage4_screen)):
        previous = result.stages[stage - 1]
        passed: list[str] = []
        dropped: list[tuple[str, str]] = []
        for symbol in previous:
            if screen is not None and not screen.get(symbol, False):
                dropped.append((symbol, f"did not pass the stage-{stage} screen"))
                continue
            if len(passed) >= config.limit(stage):
                dropped.append((symbol, f"below stage-{stage} capacity"))
                continue
            passed.append(symbol)
        result.stages[stage] = passed
        result.dropped[stage] = dropped
    return result
