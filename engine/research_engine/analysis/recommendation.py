"""Recommendation assembly -- where every layer's output becomes one answer.

A recommendation is only issued when the evidence supports it. The gates are:

* data quality must clear the configured minimum for a BUY;
* the composite score must have enough factor coverage to be meaningful;
* a bear case must exist and be survivable;
* the ensemble must not be in open conflict at high conviction;
* permanent-loss risk must be acceptable for the tier.

Failing any gate does not produce a fabricated HOLD: it produces the specific
reason the system will not take a view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.numeric import clamp, fmt_pct, round_sig
from research_engine.core.types import (ClaimType, DataQuality, Evidence, Horizon,
                                        OpportunityTier, Recommendation, RiskLevel)
from research_engine.analysis.ensemble import EnsembleResult, Stance
from research_engine.analysis.probability import ProbabilityForecast
from research_engine.analysis.risk import RiskProfile
from research_engine.analysis.scoring import CompositeScore
from research_engine.analysis.sell import SellCondition


@dataclass
class RecommendationResult:
    """The complete, self-explaining output for one asset."""

    symbol: str
    as_of: date
    recommendation: Recommendation
    previous: Recommendation | None
    tier: OpportunityTier
    score: float | None
    confidence: float
    horizon: Horizon
    price: float | None
    fair_value: dict[str, float | None]
    expected_return: dict[str, float | None]
    prob_positive: float | None
    risk_level: RiskLevel
    data_quality: DataQuality
    evidence: list[Evidence] = field(default_factory=list)
    catalysts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)
    sell_conditions: list[SellCondition] = field(default_factory=list)
    bear_case: str = ""
    bull_case: str = ""
    changes: list[str] = field(default_factory=list)
    gates_failed: list[str] = field(default_factory=list)
    #: factor name -> 0..100 sub-score (None where not computable). Carried
    #: explicitly so cross-sectional comparison does not have to reach into the
    #: ensemble's internals.
    factor_scores: dict[str, float | None] = field(default_factory=dict)
    model_version: str = "unversioned"
    data_version: str | None = None
    ensemble: EnsembleResult | None = None

    # -- explainability ----------------------------------------------------
    def strongest_positive(self, n: int = 4) -> list[Evidence]:
        return sorted([e for e in self.evidence if e.direction > 0.1],
                      key=lambda e: e.signed_weight(), reverse=True)[:n]

    def strongest_negative(self, n: int = 4) -> list[Evidence]:
        return sorted([e for e in self.evidence if e.direction < -0.1],
                      key=lambda e: e.signed_weight())[:n]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "as_of": self.as_of.isoformat(),
            "recommendation": self.recommendation.value,
            "previous_recommendation": self.previous.value if self.previous else None,
            "tier": self.tier.value,
            "score": None if self.score is None else round(self.score, 1),
            "confidence": round(self.confidence, 3), "horizon": self.horizon.value,
            "price": self.price,
            "fair_value": {k: round_sig(v, 4) for k, v in self.fair_value.items()},
            "expected_return": {k: (round(v, 4) if v is not None else None)
                                for k, v in self.expected_return.items()},
            "probability_positive": (round(self.prob_positive, 3)
                                     if self.prob_positive is not None else None),
            "risk_level": self.risk_level.value,
            "data_quality": self.data_quality.value,
            "evidence": [e.to_dict() for e in self.evidence],
            "strongest_positive": [e.to_dict() for e in self.strongest_positive()],
            "strongest_negative": [e.to_dict() for e in self.strongest_negative()],
            "catalysts": self.catalysts, "risks": self.risks,
            "invalidation": self.invalidation,
            "sell_conditions": [c.to_dict() for c in self.sell_conditions],
            "bear_case": self.bear_case, "bull_case": self.bull_case,
            "changes_since_last": self.changes, "gates_failed": self.gates_failed,
            "model_version": self.model_version, "data_version": self.data_version,
            "factor_scores": {k: (round(v, 1) if v is not None else None)
                              for k, v in self.factor_scores.items()},
            "ensemble": self.ensemble.to_dict() if self.ensemble else None,
        }

    def render(self) -> str:
        """The canonical human-readable output block."""
        lines: list[str] = []
        add = lines.append
        add(f"ASSET: {self.symbol}")
        add("")
        add(f"Recommendation: {self.recommendation.value}")
        add(f"Score: {self.score:.0f}/100" if self.score is not None
            else "Score: not computable")
        add(f"Confidence: {self.confidence:.0%}")
        add(f"Time Horizon: {_horizon_text(self.horizon)}")
        add(f"Current Price: {_money(self.price)}")
        add("")
        add("Estimated Fair Value:")
        for case in ("bear", "base", "bull"):
            add(f"  {case.title()}: {_money(self.fair_value.get(case))}")
        add("")
        add("Expected Return:")
        for case in ("bear", "base", "bull"):
            add(f"  {case.title()}: {fmt_pct(self.expected_return.get(case), 0)}")
        if self.prob_positive is not None:
            add("")
            add(f"Estimated probability of a positive return over "
                f"{_horizon_text(self.horizon)}: {self.prob_positive:.0%} "
                f"(model estimate, not a guarantee)")
        add("")
        add(f"Risk: {self.risk_level.value.title()}")
        add("")
        add("Why:")
        for evidence in self.strongest_positive():
            add(f"  * {evidence.label}: {evidence.detail}")
        if not self.strongest_positive():
            add("  * no positive evidence of sufficient weight")
        add("")
        add("Strongest Catalysts:")
        for catalyst in self.catalysts[:5] or ["none identified"]:
            add(f"  * {catalyst}")
        add("")
        add("Biggest Risks:")
        for risk in self.risks[:5] or ["none identified"]:
            add(f"  * {risk}")
        add("")
        add("Bear Case:")
        add(f"  {self.bear_case}")
        add("")
        add("Bull Case:")
        add(f"  {self.bull_case}")
        add("")
        add("Thesis Invalidation:")
        for item in self.invalidation[:5] or ["not stated"]:
            add(f"  * {item}")
        add("")
        add("SELL / EXIT IF:")
        for condition in self.sell_conditions[:8] or []:
            add(f"  * {condition.description}")
        if not self.sell_conditions:
            add("  * no exit conditions could be derived (insufficient data)")
        if self.ensemble:
            add("")
            add("Model views:")
            for name, stance in self.ensemble.summary_table():
                add(f"  {name:<20} {stance}")
            for conflict in self.ensemble.conflicts:
                add(f"  ! disagreement: {conflict}")
        if self.changes:
            add("")
            add("Changed since last analysis:")
            for change in self.changes[:5]:
                add(f"  * {change}")
        if self.gates_failed:
            add("")
            add("Why the system will not go further:")
            for gate in self.gates_failed:
                add(f"  * {gate}")
        add("")
        add(f"Data Quality: {self.data_quality.value.title()}")
        add(f"Model Version: {self.model_version}")
        add(f"Last Updated: {self.as_of.isoformat()}")
        add("")
        add("Research and decision support only; not investment advice. Model output "
            "is uncertain and can be wrong.")
        return "\n".join(lines)


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _horizon_text(horizon: Horizon) -> str:
    return {"1w": "1 week", "1m": "1 month", "3m": "3 months", "6m": "6 months",
            "1y": "12 months", "3y": "3 years", "5y": "5 years",
            "10y": "10 years"}[horizon.value]


# ------------------------------------------------------------------ gates --
@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    passed: bool
    reason: str


def check_gates(*, score: CompositeScore, risk: RiskProfile,
                probability: ProbabilityForecast | None,
                ensemble: EnsembleResult | None,
                data_quality: DataQuality,
                min_quality_for_buy: DataQuality = DataQuality.GOOD,
                min_confidence: float = 0.35,
                bear_case_return: float | None = None) -> list[Gate]:
    """Every condition a BUY must satisfy, each with its own explanation."""
    gates: list[Gate] = []

    gates.append(Gate(
        "data_quality",
        data_quality >= min_quality_for_buy,
        f"data quality is {data_quality.value}; a buy requires at least "
        f"{min_quality_for_buy.value}"))

    gates.append(Gate(
        "factor_coverage", score.coverage >= 0.6,
        f"only {score.coverage:.0%} of factor weight had data; a buy requires 60%"))

    if probability is not None:
        gates.append(Gate(
            "confidence", probability.confidence >= min_confidence,
            f"confidence of {probability.confidence:.0%} is below the "
            f"{min_confidence:.0%} minimum"))

    gates.append(Gate(
        "permanent_loss",
        risk.permanent_loss_score is None or risk.permanent_loss_score < 0.6,
        f"permanent-loss risk of {risk.permanent_loss_score:.2f} is too high for a buy"
        if risk.permanent_loss_score is not None else ""))

    if bear_case_return is not None:
        gates.append(Gate(
            "survivable_bear_case", bear_case_return > -0.6,
            f"the bear case implies {bear_case_return:.0%}, which is not survivable "
            f"at a reasonable position size"))

    if ensemble is not None:
        conflicted = (ensemble.agreement < 0.35 and
                      ensemble.consensus in (Stance.STRONGLY_BULLISH, Stance.BULLISH))
        gates.append(Gate(
            "model_agreement", not conflicted,
            f"models are in open conflict (agreement {ensemble.agreement:.0%}); "
            f"a high-conviction call is not justified"))
    return gates


def decide(*, score: CompositeScore, risk: RiskProfile,
           probability: ProbabilityForecast | None,
           ensemble: EnsembleResult | None,
           data_quality: DataQuality,
           expected_return_base: float | None,
           bear_case_return: float | None = None,
           previous: Recommendation | None = None,
           sell_triggered: bool = False,
           min_quality_for_buy: DataQuality = DataQuality.GOOD,
           min_confidence: float = 0.35,
           held: bool = False) -> tuple[Recommendation, list[str]]:
    """Map the analysis onto BUY / HOLD / WATCH / SELL / AVOID.

    ``held`` distinguishes "should I buy this?" from "should I keep this?" --
    the two questions have different answers and conflating them causes
    unnecessary turnover.
    """
    failures: list[str] = []

    if sell_triggered:
        return Recommendation.SELL, ["an exit condition was breached"]

    if score.total is None or data_quality is DataQuality.INSUFFICIENT:
        return Recommendation.INSUFFICIENT_DATA, (
            score.notes or ["insufficient reliable data to form a view"])

    gates = check_gates(score=score, risk=risk, probability=probability,
                        ensemble=ensemble, data_quality=data_quality,
                        min_quality_for_buy=min_quality_for_buy,
                        min_confidence=min_confidence,
                        bear_case_return=bear_case_return)
    failures = [g.reason for g in gates if not g.passed and g.reason]

    if risk.level is RiskLevel.EXTREME and score.tier < OpportunityTier.EXCEPTIONAL:
        return (Recommendation.AVOID if not held else Recommendation.SELL,
                failures + ["risk is extreme relative to the opportunity"])

    if score.tier is OpportunityTier.AVOID:
        return (Recommendation.AVOID if not held else Recommendation.SELL,
                failures + ["score is in the avoid band"])

    if failures:
        # The evidence may be good, but a gate failed: watch rather than buy.
        return (Recommendation.WATCH if not held else Recommendation.HOLD), failures

    if expected_return_base is not None and expected_return_base < 0.05:
        return ((Recommendation.HOLD if held else Recommendation.WATCH),
                [f"base-case expected return of {expected_return_base:.0%} does not "
                 f"compensate for the risk taken"])

    if score.tier >= OpportunityTier.STRONG:
        return Recommendation.BUY, []
    if score.tier is OpportunityTier.MODERATE:
        return (Recommendation.HOLD if held else Recommendation.WATCH), [
            "evidence is interesting but not compelling"]
    return (Recommendation.HOLD if held else Recommendation.WATCH), [
        "waiting for a better price or a catalyst"]


def build_bear_case(*, evidence: Sequence[Evidence], risk: RiskProfile,
                    bear_value: float | None, price: float | None,
                    ensemble: EnsembleResult | None,
                    fragile_assumption: str | None = None) -> str:
    """Construct the strongest reasonable case *against* the position.

    Required for every high-conviction recommendation. It is assembled from the
    actual negative evidence rather than being a rhetorical gesture.
    """
    parts: list[str] = []
    negatives = sorted([e for e in evidence if e.direction < -0.1],
                       key=lambda e: e.signed_weight())[:4]
    if negatives:
        parts.append("The case against: " + "; ".join(
            f"{e.label.lower()} ({e.detail})" for e in negatives) + ".")
    if fragile_assumption:
        parts.append(f"The most fragile assumption is {fragile_assumption}.")
    if bear_value is not None and price:
        move = bear_value / price - 1.0
        if move < -0.01:
            parts.append(f"If the bear case is right, fair value is around "
                         f"{_money(bear_value)}, a {abs(move):.0%} decline from "
                         f"{_money(price)}.")
        else:
            # A bear case above the current price is not reassuring on its own:
            # it usually means the model's downside assumptions are too mild, and
            # saying "a 38% decline" about a higher number would be simply wrong.
            parts.append(
                f"Even the bear case values this at {_money(bear_value)}, "
                f"{move:+.0%} versus {_money(price)} -- the downside scenario is "
                f"not pricing in a loss, which means the real risk here is that "
                f"the bear assumptions are not pessimistic enough rather than "
                f"that the downside is small.")
    if risk.max_drawdown is not None:
        parts.append(f"This asset has previously fallen "
                     f"{abs(risk.max_drawdown):.0%} from a peak, so a decline of "
                     f"that order is within its demonstrated range.")
    if risk.permanent_loss_score is not None and risk.permanent_loss_score > 0.4:
        reasons = risk.detail.get("permanent_loss", {}).get("reasons", [])
        if reasons:
            parts.append("Structural vulnerabilities: " + "; ".join(reasons[:2]) + ".")
    if ensemble and ensemble.conflicts:
        parts.append("Internal disagreement: " + ensemble.conflicts[0] + ".")
    if not parts:
        parts.append("No substantive bear case could be constructed from the "
                     "available evidence, which usually means the evidence is thin "
                     "rather than that no risk exists.")
    return " ".join(parts)


def build_bull_case(*, evidence: Sequence[Evidence], bull_value: float | None,
                    price: float | None, catalysts: Sequence[str]) -> str:
    parts: list[str] = []
    positives = sorted([e for e in evidence if e.direction > 0.1],
                       key=lambda e: e.signed_weight(), reverse=True)[:4]
    if positives:
        parts.append("The case for: " + "; ".join(
            f"{e.label.lower()} ({e.detail})" for e in positives) + ".")
    if bull_value is not None and price:
        upside = bull_value / price - 1.0
        parts.append(f"If the bull case plays out, fair value is around "
                     f"{_money(bull_value)}, {upside:+.0%} from {_money(price)}.")
    if catalysts:
        parts.append("Catalysts that could close the gap: " +
                     "; ".join(list(catalysts)[:3]) + ".")
    if not parts:
        parts.append("No substantive bull case could be constructed from the "
                     "available evidence.")
    return " ".join(parts)


def describe_changes(current: Mapping[str, Any],
                     previous: Mapping[str, Any] | None) -> list[str]:
    """Plain-language diff against the previous analysis of the same asset."""
    if not previous:
        return ["first analysis of this asset"]
    changes: list[str] = []
    old_rec, new_rec = previous.get("recommendation"), current.get("recommendation")
    if old_rec and new_rec and old_rec != new_rec:
        changes.append(f"recommendation moved from {old_rec} to {new_rec}")
    old_score, new_score = previous.get("score"), current.get("score")
    if old_score is not None and new_score is not None:
        delta = new_score - old_score
        if abs(delta) >= 3:
            changes.append(f"score moved {delta:+.0f} points to {new_score:.0f}")
    old_price, new_price = previous.get("price"), current.get("price")
    if old_price and new_price:
        move = new_price / old_price - 1.0
        if abs(move) >= 0.05:
            changes.append(f"price moved {move:+.1%} since the last analysis")
    old_fv = (previous.get("fair_value") or {}).get("base")
    new_fv = (current.get("fair_value") or {}).get("base")
    if old_fv and new_fv and abs(new_fv / old_fv - 1.0) >= 0.10:
        changes.append(f"base-case fair value revised {new_fv / old_fv - 1.0:+.0%}")
    old_risk, new_risk = previous.get("risk_level"), current.get("risk_level")
    if old_risk and new_risk and old_risk != new_risk:
        changes.append(f"risk level changed from {old_risk} to {new_risk}")
    old_quality = previous.get("data_quality")
    new_quality = current.get("data_quality")
    if old_quality and new_quality and old_quality != new_quality:
        changes.append(f"data quality changed from {old_quality} to {new_quality}")
    return changes or ["no material change since the last analysis"]
