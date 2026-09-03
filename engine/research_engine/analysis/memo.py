"""Investment memo generation.

The memo is the system's long-form output: everything a reader needs to
disagree with it. Sections follow the structure a human analyst would use, and
each one states its epistemic status -- observation, model prediction,
interpretation or assumption.

Two rules distinguish this from generated filler:

* No section is invented. If there is no data for the industry section, the
  memo says so rather than producing plausible-sounding prose.
* The bear case is written from the actual negative evidence, and it appears
  before the bull case so it cannot be skimmed past.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from research_engine.core.numeric import fmt_money, fmt_pct
from research_engine.core.types import ClaimType, DataQuality, Evidence
from research_engine.analysis.recommendation import RecommendationResult

DISCLAIMER = (
    "This memo is automated research output, not investment advice. Every "
    "forward-looking statement is a model estimate with material uncertainty. "
    "Backtests are simulations, historical performance does not guarantee future "
    "results, and this system can be wrong."
)


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _section(title: str, body: str | Sequence[str], *,
             fallback: str = "No reliable data available for this section.") -> str:
    if isinstance(body, str):
        content = body.strip()
    else:
        items = [str(b).strip() for b in body if str(b).strip()]
        content = "\n".join(f"- {item}" for item in items)
    return f"## {title}\n\n{content or fallback}\n"


def generate(result: RecommendationResult, *,
             fundamental: Mapping[str, Any] | None = None,
             valuation: Mapping[str, Any] | None = None,
             technical: Mapping[str, Any] | None = None,
             crypto: Mapping[str, Any] | None = None,
             industry: Mapping[str, Any] | None = None,
             peers: Sequence[Mapping[str, Any]] = (),
             sources: Sequence[str] = (),
             data_quality_detail: Mapping[str, Any] | None = None) -> str:
    """Render a full investment memo in Markdown."""
    parts: list[str] = []
    add = parts.append

    add(f"# {result.symbol} - Investment Memo\n")
    add(f"*As of {result.as_of.isoformat()} | model {result.model_version} | "
        f"data quality: {result.data_quality.value}*\n")

    # -- executive summary -------------------------------------------------
    returns = result.expected_return
    add(_section("Executive Summary", [
        f"**Recommendation: {result.recommendation.value}**"
        + (f" (tier: {result.tier.value})" if result.score is not None else ""),
        f"Score: {result.score:.0f}/100" if result.score is not None
        else "Score: not computable from the available data",
        f"Confidence: {result.confidence:.0%} "
        f"(confidence in the estimate, not a promise about the outcome)",
        f"Time horizon: {result.horizon.value}",
        f"Current price: {_money(result.price)}",
        f"Fair value - bear {_money(result.fair_value.get('bear'))}, "
        f"base {_money(result.fair_value.get('base'))}, "
        f"bull {_money(result.fair_value.get('bull'))}",
        f"Expected return - bear {fmt_pct(returns.get('bear'), 0)}, "
        f"base {fmt_pct(returns.get('base'), 0)}, "
        f"bull {fmt_pct(returns.get('bull'), 0)}",
        (f"Estimated probability of a positive return over {result.horizon.value}: "
         f"{result.prob_positive:.0%} *(model prediction)*"
         if result.prob_positive is not None else
         "Probability estimate unavailable"),
        f"Risk level: {result.risk_level.value}",
    ]))

    # -- what the business is ---------------------------------------------
    overview: list[str] = []
    if fundamental:
        for label, key, formatter in (
                ("Revenue (latest reported)", "revenue", fmt_money),
                ("Free cash flow", "free_cash_flow", fmt_money),
                ("Gross margin", "gross_margin", lambda v: fmt_pct(v, 1)),
                ("Operating margin", "operating_margin", lambda v: fmt_pct(v, 1)),
                ("ROIC", "roic", lambda v: fmt_pct(v, 1)),
                ("Revenue CAGR (3y)", "revenue_cagr_3y", lambda v: fmt_pct(v, 1))):
            value = fundamental.get(key)
            if value is not None:
                overview.append(f"{label}: {formatter(value)}")
    if crypto:
        for label, key in (("Market cap / FDV", "mcap_to_fdv"),
                           ("Circulating float", "float_ratio"),
                           ("Daily turnover", "turnover"),
                           ("Annualised protocol fees", "fees_annualised")):
            value = crypto.get(key)
            if value is not None:
                overview.append(f"{label}: {value:,.4f}" if abs(value) < 100
                                else f"{label}: {value:,.0f}")
    add(_section("Business / Project Overview", overview,
                 fallback="No structured fundamental data was available for this "
                          "asset, which itself limits how much confidence any "
                          "conclusion deserves."))

    # -- analysis sections -------------------------------------------------
    add(_section("Fundamental Analysis",
                 [f"{e.label}: {e.detail}" for e in result.evidence
                  if e.claim_type in (ClaimType.OBSERVATION, ClaimType.INTERPRETATION)
                  and any(k in e.label.lower() for k in
                          ("growth", "margin", "competitive", "capital", "debt",
                           "cash", "operating"))]))

    valuation_lines: list[str] = []
    if valuation:
        assumptions = valuation.get("assumptions") or {}
        if assumptions:
            valuation_lines.append("Assumptions used (all disclosed, none implicit):")
            for key, value in sorted(assumptions.items()):
                valuation_lines.append(f"  - {key}: {value}")
        for warning in valuation.get("warnings", [])[:4]:
            valuation_lines.append(f"Caveat: {warning}")
    if result.fair_value.get("base") is not None and result.price:
        valuation_lines.append(
            f"Base case implies {fmt_pct(returns.get('base'), 0)} from "
            f"{_money(result.price)} *(model prediction)*")
    add(_section("Valuation", valuation_lines))

    add(_section("Technical Analysis",
                 [f"{k}: {v}" for k, v in (technical or {}).items()],
                 fallback="Technical structure was not assessable from the "
                          "available price history."))

    add(_section("Industry and Competitive Position",
                 [f"{k}: {v}" for k, v in (industry or {}).items()]
                 + [f"Peer: {p.get('symbol')} - {p.get('note', '')}" for p in peers],
                 fallback="No peer or industry dataset was configured, so relative "
                          "positioning is unassessed. This is a gap, not a "
                          "judgement that the position is strong."))

    # -- forward looking ---------------------------------------------------
    add(_section("Catalysts", result.catalysts,
                 fallback="No dated catalysts were identified within the horizon."))
    add(_section("Risks", result.risks))
    add(_section("Bear Case", result.bear_case))
    add(_section("Bull Case", result.bull_case))

    thesis = [f"{e.label}: {e.detail}" for e in result.strongest_positive(5)]
    add(_section("Thesis", thesis,
                 fallback="No positive evidence of sufficient weight was found; "
                          "there is no thesis to state."))

    add(_section("What Would Change My Mind?", result.invalidation))
    add(_section("Sell Conditions",
                 [c.description for c in result.sell_conditions]))

    # -- model transparency ------------------------------------------------
    if result.ensemble:
        table = ["| Model | View |", "| --- | --- |"]
        table.extend(f"| {name} | {stance} |"
                     for name, stance in result.ensemble.summary_table())
        disagreement = result.ensemble.conflicts
        body = "\n".join(table)
        if disagreement:
            body += "\n\n**Disagreements:** " + "; ".join(disagreement)
        body += (f"\n\nModel agreement: {result.ensemble.agreement:.0%}. "
                 f"Lower agreement means the probability estimate has been pulled "
                 f"toward the base rate.")
        add(f"## Model Views\n\n{body}\n")

    quality_lines = [f"Overall: {result.data_quality.value}"]
    if data_quality_detail:
        for scope, detail in data_quality_detail.items():
            issues = detail.get("issues", []) if isinstance(detail, Mapping) else []
            quality_lines.append(
                f"{scope}: {detail.get('grade', 'unknown')} "
                f"({len(issues)} issue(s) found)")
            for issue in issues[:3]:
                quality_lines.append(f"  - {issue.get('message')}")
    add(_section("Data Quality", quality_lines))

    add(_section("Model Information", [
        f"Model version: {result.model_version}",
        f"Data version: {result.data_version or 'not recorded'}",
        f"Gates failed: {'; '.join(result.gates_failed) if result.gates_failed else 'none'}",
        f"Changes since last analysis: {'; '.join(result.changes)}",
    ]))

    add(_section("Sources", list(sources) or _default_sources(result),
                 fallback="No sources recorded."))

    add(f"---\n\n*{DISCLAIMER}*\n")
    return "\n".join(parts)


def _default_sources(result: RecommendationResult) -> list[str]:
    found: set[str] = set()
    for evidence in result.evidence:
        found.update(evidence.sources)
    return sorted(s for s in found if s)
