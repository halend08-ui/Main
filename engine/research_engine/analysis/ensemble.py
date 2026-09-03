"""Ensemble of independent model views.

Eight views look at the same asset from different angles: fundamental,
valuation, technical, momentum, risk, sentiment, event and macro. Each returns a
stance and a confidence, and each is computed from a *different* input family so
that agreement between them carries real information.

The ensemble never hides disagreement. When the views split, the output says so
explicitly and the probability layer shrinks toward the base rate. A confident
recommendation from three views that contradict each other would be a fiction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, ClassVar, Iterable, Mapping, Sequence

import numpy as np

from research_engine.core.numeric import clamp, mean, stdev
from research_engine.core.types import ClaimType, DataQuality, Evidence


class Stance(str, enum.Enum):
    STRONGLY_BEARISH = "strongly_bearish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    BULLISH = "bullish"
    STRONGLY_BULLISH = "strongly_bullish"
    NO_VIEW = "no_view"

    @property
    def direction(self) -> float | None:
        return {"strongly_bearish": -1.0, "bearish": -0.5, "neutral": 0.0,
                "bullish": 0.5, "strongly_bullish": 1.0}.get(self.value)

    @classmethod
    def from_score(cls, score: float | None) -> "Stance":
        """Map a 0..100 factor score onto a stance."""
        if score is None:
            return cls.NO_VIEW
        if score >= 80:
            return cls.STRONGLY_BULLISH
        if score >= 62:
            return cls.BULLISH
        if score >= 45:
            return cls.NEUTRAL
        if score >= 30:
            return cls.BEARISH
        return cls.STRONGLY_BEARISH


@dataclass(frozen=True, slots=True)
class ModelView:
    """One model's opinion, with the reasoning that produced it."""

    name: str
    stance: Stance
    confidence: float                 # 0..1 in this view specifically
    score: float | None = None        # 0..100 where applicable
    rationale: str = ""
    inputs: Mapping[str, Any] = field(default_factory=dict)
    quality: DataQuality = DataQuality.FAIR

    #: Views whose stance is about conditions rather than direction read badly
    #: as "bullish"/"bearish"; they get their own vocabulary in reports.
    _VOCABULARY: ClassVar[dict[str, dict[str, str]]] = {
        "risk": {"strongly_bullish": "risk supportive",
                 "bullish": "risk mildly supportive", "neutral": "risk neutral",
                 "bearish": "risk adverse", "strongly_bearish": "risk strongly adverse",
                 "no_view": "no view"},
        "event": {"strongly_bullish": "events favourable",
                  "bullish": "events mildly favourable", "neutral": "events neutral",
                  "bearish": "events adverse", "strongly_bearish": "events strongly adverse",
                  "no_view": "no view"},
    }

    @property
    def has_view(self) -> bool:
        return self.stance is not Stance.NO_VIEW

    def display_stance(self) -> str:
        vocabulary = ModelView._VOCABULARY.get(self.name)
        if vocabulary:
            return vocabulary[self.stance.value]
        return self.stance.value.replace("_", " ")

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.name, "stance": self.stance.value,
                "stance_display": self.display_stance(),
                "confidence": round(self.confidence, 3),
                "score": None if self.score is None else round(self.score, 1),
                "rationale": self.rationale, "quality": self.quality.value,
                "inputs": dict(self.inputs)}


@dataclass
class EnsembleResult:
    views: list[ModelView]
    consensus: Stance
    consensus_score: float | None      # 0..100
    agreement: float                   # 0..1
    dispersion: float | None
    conflicts: list[str] = field(default_factory=list)
    abstained: list[str] = field(default_factory=list)

    @property
    def views_with_opinion(self) -> list[ModelView]:
        return [v for v in self.views if v.has_view]

    def summary_table(self) -> list[tuple[str, str]]:
        """The 'show the disagreement' table used verbatim in reports."""
        return [(v.name, v.display_stance().title()) for v in self.views]

    def to_dict(self) -> dict[str, Any]:
        return {"consensus": self.consensus.value,
                "consensus_score": None if self.consensus_score is None
                else round(self.consensus_score, 1),
                "agreement": round(self.agreement, 3),
                "dispersion": None if self.dispersion is None else round(self.dispersion, 3),
                "views": [v.to_dict() for v in self.views],
                "conflicts": self.conflicts, "abstained": self.abstained}


DEFAULT_VIEW_WEIGHTS: dict[str, float] = {
    "fundamental": 1.3,
    "valuation": 1.3,
    "risk": 1.2,
    "momentum": 0.9,
    "technical": 0.7,
    "event": 0.8,
    "sentiment": 0.5,      # deliberately the lowest: sentiment is not evidence
    "macro": 0.6,
    "crypto_fundamental": 1.3,
}


def combine(views: Sequence[ModelView], *,
            weights: Mapping[str, float] | None = None,
            max_single_weight: float = 0.35) -> EnsembleResult:
    """Weighted consensus that no single model can dominate.

    ``max_single_weight`` caps any one view's share of the total, so a highly
    confident sentiment model cannot outvote the fundamentals.
    """
    view_weights = dict(DEFAULT_VIEW_WEIGHTS)
    view_weights.update(weights or {})

    opinionated = [v for v in views if v.has_view]
    abstained = [v.name for v in views if not v.has_view]
    if not opinionated:
        return EnsembleResult(list(views), Stance.NO_VIEW, None, 0.0, None,
                              ["no model could form a view"], abstained)

    raw = {v.name: view_weights.get(v.name, 1.0) * max(v.confidence, 0.05)
           for v in opinionated}
    total_raw = sum(raw.values()) or 1.0
    capped = {name: min(w / total_raw, max_single_weight) for name, w in raw.items()}
    total = sum(capped.values()) or 1.0
    normalised = {name: w / total for name, w in capped.items()}

    directions = [(v.stance.direction or 0.0, normalised[v.name]) for v in opinionated]
    consensus_direction = sum(d * w for d, w in directions)

    scored = [(v.score, normalised[v.name]) for v in opinionated if v.score is not None]
    consensus_score = (sum(s * w for s, w in scored) / sum(w for _, w in scored)
                       if scored else None)

    raw_directions = [v.stance.direction or 0.0 for v in opinionated]
    dispersion = stdev(raw_directions) if len(raw_directions) >= 2 else None
    # Agreement: 1 when all views point the same way, 0 when maximally split.
    agreement = 1.0 - clamp((dispersion or 0.0) / 1.0, 0.0, 1.0)

    conflicts = _describe_conflicts(opinionated)

    return EnsembleResult(
        views=list(views),
        consensus=_stance_from_direction(consensus_direction),
        consensus_score=consensus_score, agreement=agreement, dispersion=dispersion,
        conflicts=conflicts, abstained=abstained)


def _stance_from_direction(direction: float) -> Stance:
    if direction >= 0.6:
        return Stance.STRONGLY_BULLISH
    if direction >= 0.2:
        return Stance.BULLISH
    if direction > -0.2:
        return Stance.NEUTRAL
    if direction > -0.6:
        return Stance.BEARISH
    return Stance.STRONGLY_BEARISH


def _describe_conflicts(views: Sequence[ModelView]) -> list[str]:
    """Name the specific disagreements, in the language a reader needs."""
    bulls = [v for v in views if (v.stance.direction or 0) > 0.2]
    bears = [v for v in views if (v.stance.direction or 0) < -0.2]
    conflicts: list[str] = []
    if bulls and bears:
        conflicts.append(
            f"{', '.join(v.name for v in bulls)} bullish vs "
            f"{', '.join(v.name for v in bears)} bearish")
    fundamental = next((v for v in views if v.name == "fundamental"), None)
    valuation = next((v for v in views if v.name == "valuation"), None)
    if fundamental and valuation:
        fd, vd = fundamental.stance.direction or 0, valuation.stance.direction or 0
        if fd > 0.4 and vd < -0.2:
            conflicts.append("strong business, unattractive price: quality is "
                             "already reflected in the valuation")
        elif fd < -0.2 and vd > 0.4:
            conflicts.append("cheap on the numbers but the business is deteriorating: "
                             "a possible value trap")
    risk = next((v for v in views if v.name == "risk"), None)
    if risk and (risk.stance.direction or 0) < -0.4 and bulls:
        conflicts.append("the risk model dissents from the bullish case")
    return conflicts


def ensemble_evidence(result: EnsembleResult) -> list[Evidence]:
    out: list[Evidence] = []
    for view in result.views_with_opinion:
        direction = view.stance.direction or 0.0
        if abs(direction) < 0.2:
            continue
        out.append(Evidence(
            label=f"{view.name.title()} model: {view.display_stance()}",
            detail=view.rationale or "no rationale recorded",
            direction=direction, weight=clamp(view.confidence, 0.1, 1.0),
            claim_type=ClaimType.MODEL_PREDICTION, quality=view.quality,
            sources=(view.name,)))
    for conflict in result.conflicts:
        out.append(Evidence(
            label="Model disagreement", detail=conflict, direction=0.0, weight=0.5,
            claim_type=ClaimType.INTERPRETATION, quality=DataQuality.GOOD,
            sources=("ensemble",)))
    if result.abstained:
        out.append(Evidence(
            label="Models without a view",
            detail=f"{', '.join(result.abstained)} had insufficient data",
            direction=0.0, weight=0.3, claim_type=ClaimType.ASSUMPTION,
            quality=DataQuality.POOR, sources=()))
    return out


def build_views(*, factor_scores: Mapping[str, float | None],
                rationales: Mapping[str, str] | None = None,
                confidences: Mapping[str, float] | None = None,
                qualities: Mapping[str, DataQuality] | None = None
                ) -> list[ModelView]:
    """Turn factor sub-scores into model views, grouping related factors.

    The grouping matters: technical and momentum are separated because they can
    genuinely disagree (a stock can be in a downtrend after a strong year), and
    collapsing them would hide that.
    """
    groups: dict[str, tuple[str, ...]] = {
        "fundamental": ("fundamental_quality", "growth", "financial_health",
                        "competitive_advantage", "capital_allocation"),
        "valuation": ("valuation",),
        "momentum": ("momentum",),
        "technical": ("technical_structure",),
        "risk": ("downside_risk", "liquidity"),
        "sentiment": ("sentiment",),
        "event": ("event_risk",),
        "macro": ("macro_environment",),
        "crypto_fundamental": ("tokenomics", "network_activity", "developer_activity",
                               "concentration_risk"),
    }
    rationales = rationales or {}
    confidences = confidences or {}
    qualities = qualities or {}

    views: list[ModelView] = []
    for view_name, factors in groups.items():
        available = [factor_scores.get(f) for f in factors
                     if factor_scores.get(f) is not None]
        if not available:
            views.append(ModelView(view_name, Stance.NO_VIEW, 0.0,
                                   rationale="no inputs available"))
            continue
        score = float(mean(available) or 0.0)
        coverage = len(available) / len(factors)
        confidence = confidences.get(view_name, clamp(0.35 + 0.5 * coverage, 0.1, 0.9))
        views.append(ModelView(
            name=view_name, stance=Stance.from_score(score), confidence=confidence,
            score=score, rationale=rationales.get(view_name, ""),
            inputs={f: factor_scores.get(f) for f in factors},
            quality=qualities.get(view_name, DataQuality.FAIR)))
    return views
