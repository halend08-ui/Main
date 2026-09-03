"""Daily report generation.

The report answers, in order: what is the market doing, what are the best
opportunities, what changed, what is new, how do existing positions look, and
how has the system itself been performing. That last section is not optional --
a research report that never grades its own past output is marketing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from research_engine.core.numeric import fmt_money, fmt_pct
from research_engine.core.types import DataQuality

DISCLAIMER = (
    "Automated research output. Not investment advice. All forward-looking "
    "figures are model estimates with material uncertainty; past performance "
    "does not guarantee future results."
)


@dataclass
class DailyReport:
    as_of: date
    market: dict[str, Any] = field(default_factory=dict)
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    changes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    discoveries: list[dict[str, Any]] = field(default_factory=list)
    portfolio: dict[str, Any] = field(default_factory=dict)
    model_performance: dict[str, Any] = field(default_factory=dict)
    self_evaluation: dict[str, Any] = field(default_factory=dict)
    data_health: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(), "market": self.market,
                "opportunities": self.opportunities, "changes": self.changes,
                "discoveries": self.discoveries, "portfolio": self.portfolio,
                "model_performance": self.model_performance,
                "self_evaluation": self.self_evaluation,
                "data_health": self.data_health, "warnings": self.warnings,
                "disclaimer": DISCLAIMER}

    def render(self) -> str:
        lines: list[str] = []
        add = lines.append

        add(f"# Daily Research Report - {self.as_of.isoformat()}\n")
        if self.warnings:
            add("> **Run warnings:** " + "; ".join(self.warnings[:5]) + "\n")

        # -- market ---------------------------------------------------------
        add("## Market Overview\n")
        if self.market:
            regime = self.market.get("regime", {})
            add(f"- Regime: **{regime.get('regime', 'unknown')}** "
                f"(volatility: {regime.get('volatility_regime', 'unknown')}, "
                f"risk appetite: {regime.get('risk_appetite', 'unknown')}, "
                f"confidence {float(regime.get('confidence', 0)):.0%})")
            for name, value in (self.market.get("indexes") or {}).items():
                add(f"- {name}: {fmt_pct(value, 1)} over the session")
            macro = self.market.get("macro_stance") or {}
            if macro:
                add("- Macro stance: " + ", ".join(f"{k}={v}" for k, v in macro.items()))
            for risk in (self.market.get("major_risks") or [])[:5]:
                add(f"- Risk: {risk}")
        else:
            add("- Market context unavailable for this run.")
        add("")

        # -- opportunities --------------------------------------------------
        add("## Top Opportunities\n")
        if self.opportunities:
            add("| # | Asset | Rec | Score | Conf | Price | Base FV | Base return | "
                "Risk | Quality |")
            add("|---|-------|-----|-------|------|-------|---------|-------------|"
                "------|---------|")
            for i, item in enumerate(self.opportunities[:25], start=1):
                fair = (item.get("fair_value") or {}).get("base")
                base_return = (item.get("expected_return") or {}).get("base")
                add(f"| {i} | {item.get('symbol')} | {item.get('recommendation')} | "
                    f"{_num(item.get('score'), 0)} | "
                    f"{fmt_pct(item.get('confidence'), 0)} | "
                    f"{_money(item.get('price'))} | {_money(fair)} | "
                    f"{fmt_pct(base_return, 0)} | {item.get('risk_level', '-')} | "
                    f"{item.get('data_quality', '-')} |")
            add("")
            for item in self.opportunities[:5]:
                add(f"**{item.get('symbol')}** - {item.get('recommendation')}")
                for reason in (item.get("why") or [])[:3]:
                    add(f"  - {reason}")
                for risk in (item.get("key_risks") or [])[:2]:
                    add(f"  - risk: {risk}")
                for condition in (item.get("sell_conditions") or [])[:2]:
                    add(f"  - sell if: {condition}")
                add("")
        else:
            add("No asset cleared the minimum evidence and quality bars today. "
                "That is a legitimate output, not a failure of the run.\n")

        # -- changes --------------------------------------------------------
        add("## Biggest Changes\n")
        any_change = False
        for label, key in (("New BUY signals", "new_buys"),
                           ("Upgrades", "upgrades"),
                           ("Downgrades", "downgrades"),
                           ("Moved to SELL", "new_sells"),
                           ("Large score moves", "score_moves")):
            items = self.changes.get(key) or []
            if not items:
                continue
            any_change = True
            add(f"### {label}\n")
            for item in items[:10]:
                add(f"- **{item.get('symbol')}**: {item.get('detail', '')}")
            add("")
        if not any_change:
            add("No material changes since the previous run.\n")

        # -- discoveries ----------------------------------------------------
        add("## New Assets Discovered\n")
        if self.discoveries:
            for item in self.discoveries[:15]:
                warnings = "; ".join(item.get("warnings", [])[:1])
                add(f"- **{item.get('symbol')}** ({item.get('asset_class')}): "
                    f"{item.get('reason')} [{item.get('trigger')}]"
                    + (f" - caveat: {warnings}" if warnings else ""))
            add("\n*Discoveries are research candidates only; none of the above is "
                "a recommendation.*\n")
        else:
            add("Nothing new met the discovery thresholds today.\n")

        # -- portfolio ------------------------------------------------------
        add("## Existing Portfolio\n")
        if self.portfolio.get("positions"):
            add("| Asset | Entry | Price | Return | Rec | Thesis health | Risk |")
            add("|-------|-------|-------|--------|-----|---------------|------|")
            for position in self.portfolio["positions"]:
                add(f"| {position.get('symbol')} | "
                    f"{_money(position.get('entry_price'))} | "
                    f"{_money(position.get('price'))} | "
                    f"{fmt_pct(position.get('unrealized_return'), 1)} | "
                    f"{position.get('recommendation', '-')} | "
                    f"{position.get('thesis_health', '-')} | "
                    f"{position.get('risk_level', '-')} |")
            add("")
            risk = self.portfolio.get("risk") or {}
            if risk:
                add(f"- Portfolio volatility: {fmt_pct(risk.get('volatility'), 1)} "
                    f"(measured on {risk.get('holdings_measured', 0)} holdings)")
                add(f"- Concentration (HHI): {_num(risk.get('hhi'), 3)} "
                    f"-> about {_num(risk.get('effective_positions'), 1)} "
                    f"equally weighted positions")
            for breach in self.portfolio.get("breaches", []):
                add(f"- **Limit breach:** {breach}")
            add("")
        else:
            add("No hypothetical positions are open.\n")

        # -- model performance ----------------------------------------------
        add("## Model Performance\n")
        if self.model_performance.get("overall"):
            overall = self.model_performance["overall"]
            if overall.get("sufficient", True):
                add(f"- Evaluated predictions: {overall.get('samples')}")
                add(f"- Hit rate: {fmt_pct(overall.get('hit_rate'), 1)}")
                add(f"- Average return: {fmt_pct(overall.get('avg_return'), 1)} "
                    f"(excess: {fmt_pct(overall.get('avg_excess'), 1)})")
                add(f"- Brier skill vs base rate: "
                    f"{_num(overall.get('brier_skill'), 3)} "
                    f"(positive means the probabilities add information)")
                add(f"- Calibration error: {fmt_pct(overall.get('calibration_error'), 1)}")
            else:
                add(f"- {overall.get('note', 'insufficient evaluated predictions')}")
            for bucket in self.model_performance.get("weak_buckets", [])[:5]:
                add(f"- Weak spot: {bucket.get('detail')}")
            confidence = self.model_performance.get("confidence_check") or {}
            if confidence.get("assessable"):
                add(f"- Confidence check: {confidence.get('verdict')}")
        else:
            add("- Not enough evaluated predictions yet to report performance. "
                "Predictions are stored and will be graded as horizons elapse.\n")
        add("")

        # -- self evaluation -------------------------------------------------
        if self.self_evaluation:
            add("## Daily Self-Evaluation\n")
            for key in ("what_worked", "what_failed", "why", "systematic_errors",
                        "actions_taken"):
                items = self.self_evaluation.get(key) or []
                if not items:
                    continue
                add(f"**{key.replace('_', ' ').title()}**")
                for item in items[:5]:
                    add(f"- {item}")
                add("")

        # -- data health -----------------------------------------------------
        if self.data_health:
            add("## Data Health\n")
            for provider, stats in (self.data_health.get("providers") or {}).items():
                add(f"- {provider}: {stats.get('requests', 0)} requests, "
                    f"{stats.get('failures', 0)} failures, "
                    f"{stats.get('cache_hits', 0)} cache hits")
            coverage = self.data_health.get("coverage")
            if coverage:
                add(f"- Universe coverage: {coverage.get('with_prices', 0)} of "
                    f"{coverage.get('total', 0)} assets have usable price history")
            for issue in (self.data_health.get("issues") or [])[:5]:
                add(f"- Issue: {issue}")
            add("")

        add(f"---\n\n*{DISCLAIMER}*")
        return "\n".join(lines)


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _money(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def build_opportunity_row(result: Any) -> dict[str, Any]:
    """Convert a RecommendationResult into a report row."""
    return {
        "symbol": result.symbol,
        "recommendation": result.recommendation.value,
        "score": result.score, "confidence": result.confidence,
        "price": result.price, "fair_value": result.fair_value,
        "expected_return": result.expected_return,
        "risk_level": result.risk_level.value,
        "data_quality": result.data_quality.value,
        "horizon": result.horizon.value,
        "why": [f"{e.label}: {e.detail}" for e in result.strongest_positive(3)],
        "key_risks": result.risks[:3],
        "catalysts": result.catalysts[:3],
        "sell_conditions": [c.description for c in result.sell_conditions[:3]],
    }
