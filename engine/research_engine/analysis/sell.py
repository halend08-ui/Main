"""Sell / exit decisions.

The engine never sells because "the price went down". A price fall is a fact
about the market, not about the business; on its own it is as likely to be an
opportunity as a warning. Sells are triggered by one of five things:

1. **Thesis deterioration** -- the reason for owning it stopped being true.
2. **Valuation** -- the expected return no longer compensates for the risk.
3. **Risk** -- the probability of permanent loss has risen materially.
4. **Opportunity cost** -- something materially better is available.
5. **Structural breakdown** -- liquidity, trend or solvency has broken in a way
   that changes the distribution of outcomes.

Every recommendation the system publishes carries measurable ``SELL / EXIT IF``
conditions, so the exit is defined *before* the position exists.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from research_engine.core.numeric import clamp, is_finite
from research_engine.core.types import ClaimType, DataQuality, Evidence


class SellTrigger(str, enum.Enum):
    THESIS_DETERIORATION = "thesis_deterioration"
    VALUATION = "valuation"
    RISK_INCREASE = "risk_increase"
    OPPORTUNITY_COST = "opportunity_cost"
    STRUCTURAL_BREAKDOWN = "structural_breakdown"
    TIME_STOP = "time_stop"


@dataclass(frozen=True, slots=True)
class SellCondition:
    """A measurable, checkable exit condition.

    ``metric``/``operator``/``threshold`` make it machine-evaluable so the daily
    loop can test it automatically instead of relying on a human rereading prose.
    """

    trigger: SellTrigger
    description: str
    metric: str
    operator: str            # "<" | ">" | "<=" | ">=" | "crosses_below" | "crosses_above"
    threshold: float
    severity: str = "sell"   # "review" | "trim" | "sell"

    def evaluate(self, value: float | None) -> bool | None:
        """True when breached, False when not, None when the metric is unavailable."""
        if value is None or not is_finite(value):
            return None
        return {
            "<": value < self.threshold,
            "<=": value <= self.threshold,
            ">": value > self.threshold,
            ">=": value >= self.threshold,
            "crosses_below": value < self.threshold,
            "crosses_above": value > self.threshold,
        }[self.operator]

    def to_dict(self) -> dict[str, Any]:
        return {"trigger": self.trigger.value, "description": self.description,
                "metric": self.metric, "operator": self.operator,
                "threshold": round(self.threshold, 4), "severity": self.severity}


def build_conditions(*, price: float | None, fair_value_base: float | None,
                     fair_value_bear: float | None = None,
                     revenue_growth: float | None = None,
                     operating_margin: float | None = None,
                     interest_coverage: float | None = None,
                     fcf: float | None = None,
                     atr: float | None = None,
                     stop_atr_multiple: float = 3.0,
                     max_drawdown_tolerance: float = 0.25,
                     is_crypto: bool = False,
                     unlock_pct: float | None = None,
                     horizon_months: int = 12) -> list[SellCondition]:
    """Generate the exit conditions attached to a recommendation.

    Thresholds are derived from the actual thesis inputs, so they mean something
    specific to this asset rather than being generic round numbers.
    """
    conditions: list[SellCondition] = []

    # -- thesis deterioration ---------------------------------------------
    if revenue_growth is not None:
        floor = max(-0.05, revenue_growth * 0.4)
        conditions.append(SellCondition(
            SellTrigger.THESIS_DETERIORATION,
            f"revenue growth falls below {floor:.0%} for two consecutive quarters "
            f"(thesis assumes about {revenue_growth:.0%})",
            "revenue_growth_ttm", "<", floor))
    if operating_margin is not None:
        floor = operating_margin - 0.05
        conditions.append(SellCondition(
            SellTrigger.THESIS_DETERIORATION,
            f"operating margin falls below {floor:.1%} (5 points beneath the level "
            f"the thesis relies on)",
            "operating_margin", "<", floor))
    if fcf is not None and fcf > 0:
        conditions.append(SellCondition(
            SellTrigger.THESIS_DETERIORATION,
            "free cash flow turns negative for a full trailing year",
            "free_cash_flow_ttm", "<", 0.0))

    # -- valuation ---------------------------------------------------------
    if fair_value_base is not None and price:
        target = fair_value_base * 1.05
        conditions.append(SellCondition(
            SellTrigger.VALUATION,
            f"price exceeds {target:,.2f}, above the base-case fair value "
            f"(expected return no longer compensates for the risk)",
            "price", ">", target, severity="trim"))
    if fair_value_bear is not None and price and fair_value_bear > 0:
        conditions.append(SellCondition(
            SellTrigger.VALUATION,
            f"base-case fair value is revised below {fair_value_bear:,.2f} "
            f"(today's bear case)",
            "fair_value_base", "<", fair_value_bear, severity="review"))

    # -- risk --------------------------------------------------------------
    if interest_coverage is not None:
        conditions.append(SellCondition(
            SellTrigger.RISK_INCREASE,
            "interest coverage falls below 2x (debt service becomes fragile)",
            "interest_coverage", "<", 2.0))
    conditions.append(SellCondition(
        SellTrigger.RISK_INCREASE,
        "permanent-loss risk score rises above 0.6",
        "permanent_loss_score", ">", 0.6))

    # -- structural --------------------------------------------------------
    if price and atr:
        stop = price - stop_atr_multiple * atr
        conditions.append(SellCondition(
            SellTrigger.STRUCTURAL_BREAKDOWN,
            f"price closes below {stop:,.2f} ({stop_atr_multiple:.0f}x ATR beneath "
            f"the current price), indicating a volatility-adjusted trend break",
            "price", "<", stop, severity="review"))
    if price:
        drawdown_stop = price * (1 - max_drawdown_tolerance)
        conditions.append(SellCondition(
            SellTrigger.STRUCTURAL_BREAKDOWN,
            f"position falls {max_drawdown_tolerance:.0%} from entry without a "
            f"fundamental explanation (forces a thesis re-review, not an automatic sell)",
            "price", "<", drawdown_stop, severity="review"))

    if is_crypto:
        conditions.append(SellCondition(
            SellTrigger.STRUCTURAL_BREAKDOWN,
            "30-day average volume falls below $1m (exit becomes impractical)",
            "avg_dollar_volume", "<", 1_000_000))
        if unlock_pct is not None and unlock_pct > 0:
            conditions.append(SellCondition(
                SellTrigger.RISK_INCREASE,
                "an unlock exceeding 10% of circulating supply is scheduled within "
                "60 days without matching demand growth",
                "unlock_pct_60d", ">", 0.10, severity="trim"))

    # -- time stop ---------------------------------------------------------
    conditions.append(SellCondition(
        SellTrigger.TIME_STOP,
        f"the thesis has not progressed after {horizon_months} months "
        f"(capital is better deployed elsewhere)",
        "months_held_without_progress", ">=", float(horizon_months),
        severity="review"))
    return conditions


def build_invalidation(*, evidence_labels: Sequence[str],
                       moat_verdict: str | None = None,
                       growth_assumption: float | None = None,
                       margin_assumption: float | None = None,
                       implied_growth: float | None = None,
                       key_risks: Sequence[str] = ()) -> list[str]:
    """The "what would make me wrong?" list.

    Written as falsifiable statements about the *assumptions*, not about the
    price -- a thesis that can only be invalidated by the price falling is not a
    thesis.
    """
    out: list[str] = []
    if growth_assumption is not None:
        out.append(f"Revenue growth settles materially below the {growth_assumption:.0%} "
                   f"the valuation assumes")
    if margin_assumption is not None:
        out.append(f"Operating margin fails to hold near {margin_assumption:.0%}, "
                   f"indicating the cost structure is not defensible")
    if implied_growth is not None:
        out.append(f"The market's implied growth of {implied_growth:.0%} proves "
                   f"achievable by competitors too, eliminating the advantage")
    if moat_verdict in ("narrow", "wide"):
        out.append("Gross margin compresses for two or more consecutive years, "
                   "contradicting the pricing-power claim")
    elif moat_verdict == "none":
        out.append("The absence of a durable advantage means any excess returns are "
                   "competed away faster than the model assumes")
    for risk in list(key_risks)[:3]:
        out.append(f"Risk materialises: {risk}")
    if not out:
        out.append("The evidence supporting this view is too thin to state a "
                   "falsifiable invalidation condition, which is itself a reason "
                   "for caution")
    return out


@dataclass
class SellEvaluation:
    should_sell: bool
    breached: list[SellCondition]
    review: list[SellCondition]
    unevaluable: list[SellCondition]
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"should_sell": self.should_sell,
                "breached": [c.to_dict() for c in self.breached],
                "review": [c.to_dict() for c in self.review],
                "unevaluable": [c.metric for c in self.unevaluable],
                "reasons": self.reasons}


def evaluate(conditions: Sequence[SellCondition],
             metrics: Mapping[str, float | None]) -> SellEvaluation:
    """Check live metrics against the stated exit conditions."""
    breached: list[SellCondition] = []
    review: list[SellCondition] = []
    unevaluable: list[SellCondition] = []
    reasons: list[str] = []

    for condition in conditions:
        result = condition.evaluate(metrics.get(condition.metric))
        if result is None:
            unevaluable.append(condition)
            continue
        if not result:
            continue
        if condition.severity == "sell":
            breached.append(condition)
            reasons.append(condition.description)
        else:
            review.append(condition)
            reasons.append(f"[{condition.severity}] {condition.description}")

    return SellEvaluation(should_sell=bool(breached), breached=breached,
                          review=review, unevaluable=unevaluable, reasons=reasons)


def opportunity_cost_check(*, current_expected_return: float | None,
                           current_risk: float | None,
                           alternative_expected_return: float | None,
                           alternative_risk: float | None,
                           margin_required: float = 0.5) -> dict[str, Any]:
    """Would switching materially improve risk-adjusted expected return?

    ``margin_required`` exists because switching is not free: turnover costs
    money and each switch is another chance to be wrong. A marginal improvement
    is not a reason to trade.
    """
    if None in (current_expected_return, alternative_expected_return):
        return {"switch": False, "reason": "expected returns unavailable for comparison"}
    current_ratio = (current_expected_return / max(current_risk or 0.2, 0.05))
    alternative_ratio = (alternative_expected_return / max(alternative_risk or 0.2, 0.05))
    improvement = alternative_ratio - current_ratio
    if improvement > margin_required:
        return {"switch": True, "improvement": round(improvement, 3),
                "reason": f"the alternative offers {improvement:.2f} more expected "
                          f"return per unit of risk, beyond the {margin_required} "
                          f"hurdle that covers turnover cost and switching risk"}
    return {"switch": False, "improvement": round(improvement, 3),
            "reason": "improvement does not clear the switching hurdle"}


def sell_evidence(evaluation: SellEvaluation) -> list[Evidence]:
    out: list[Evidence] = []
    for condition in evaluation.breached:
        out.append(Evidence(
            label=f"Sell condition breached: {condition.trigger.value}",
            detail=condition.description, direction=-1.0, weight=1.0,
            claim_type=ClaimType.OBSERVATION, quality=DataQuality.GOOD,
            sources=("sell engine",)))
    for condition in evaluation.review:
        out.append(Evidence(
            label=f"Review triggered: {condition.trigger.value}",
            detail=condition.description, direction=-0.4, weight=0.5,
            claim_type=ClaimType.OBSERVATION, quality=DataQuality.GOOD,
            sources=("sell engine",)))
    if evaluation.unevaluable:
        out.append(Evidence(
            label="Exit conditions not checkable",
            detail=f"{len(evaluation.unevaluable)} exit conditions could not be "
                   f"evaluated for lack of data "
                   f"({', '.join(c.metric for c in evaluation.unevaluable[:3])})",
            direction=-0.2, weight=0.3, claim_type=ClaimType.ASSUMPTION,
            quality=DataQuality.POOR, sources=()))
    return out
