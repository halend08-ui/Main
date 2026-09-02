"""Valuation.

Principles enforced in code:

* **Every assumption is an object.** :class:`DcfAssumptions` is returned with
  the result, so no number in a report is unexplained.
* **Three scenarios, always.** Bear / base / bull are produced together;
  a single point estimate of fair value is a false-precision trap.
* **Multiple methods, cross-checked.** A DCF and a multiples view are blended
  only when they broadly agree; when they disagree the disagreement is
  reported, not averaged away.
* **Reverse DCF for discipline.** Instead of only asking "what is it worth?",
  the engine asks "what must be true for today's price to be fair?" -- which is
  far harder to fool yourself with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.numeric import (clamp, is_finite, mean, median,
                                          percentile_rank, safe_div)
from research_engine.core.types import ClaimType, DataQuality, Evidence, Metric


# ------------------------------------------------------------- multiples ---
def compute_multiples(*, price: float | None, shares: float | None,
                      market_cap: float | None = None,
                      net_debt: float | None = None,
                      earnings: float | None = None,
                      revenue: float | None = None,
                      ebitda: float | None = None,
                      ebit: float | None = None,
                      free_cash_flow: float | None = None,
                      book_value: float | None = None,
                      dividends: float | None = None,
                      growth: float | None = None) -> dict[str, float | None]:
    """Standard valuation multiples. ``None`` wherever an input is missing or
    the ratio is not meaningful (e.g. P/E on negative earnings)."""
    cap = market_cap
    if cap is None and price is not None and shares:
        cap = price * shares
    ev = None
    if cap is not None and net_debt is not None:
        ev = cap + net_debt

    def positive_ratio(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator is None or denominator <= 0:
            return None       # negative denominators make multiples meaningless
        return numerator / denominator

    pe = positive_ratio(cap, earnings)
    peg = None
    if pe is not None and growth is not None and growth > 0.01:
        peg = pe / (growth * 100)

    return {
        "market_cap": cap,
        "enterprise_value": ev,
        "pe": pe,
        "ev_ebitda": positive_ratio(ev, ebitda),
        "ev_ebit": positive_ratio(ev, ebit),
        "ev_sales": positive_ratio(ev, revenue),
        "ps": positive_ratio(cap, revenue),
        "pb": positive_ratio(cap, book_value),
        "p_fcf": positive_ratio(cap, free_cash_flow),
        "fcf_yield": safe_div(free_cash_flow, cap),
        "earnings_yield": safe_div(earnings, cap),
        "dividend_yield": safe_div(abs(dividends) if dividends else None, cap),
        "peg": peg,
    }


def relative_valuation(current: float | None, history: Sequence[float] | None = None,
                       peers: Sequence[float] | None = None
                       ) -> dict[str, float | None]:
    """Position a multiple against its own history and its peer group.

    Deliberately *not* interpreted as fair value: a stock trading below its
    5-year average multiple may be cheap, or the business may have deteriorated.
    Both readings are returned so the scoring layer can weigh them with other
    evidence.
    """
    out: dict[str, float | None] = {
        "current": current, "history_median": None, "history_percentile": None,
        "peer_median": None, "premium_to_peers": None, "premium_to_history": None}
    if current is None:
        return out
    if history:
        clean = [h for h in history if is_finite(h) and h > 0]
        if len(clean) >= 8:
            hist_med = median(clean)
            out["history_median"] = hist_med
            out["history_percentile"] = percentile_rank(clean, current)
            out["premium_to_history"] = safe_div(current - (hist_med or 0), hist_med)
    if peers:
        clean_peers = [p for p in peers if is_finite(p) and p > 0]
        if len(clean_peers) >= 3:
            peer_med = median(clean_peers)
            out["peer_median"] = peer_med
            out["premium_to_peers"] = safe_div(current - (peer_med or 0), peer_med)
    return out


# ------------------------------------------------------------------- DCF ---
@dataclass(frozen=True, slots=True)
class DcfAssumptions:
    """Every input to a DCF, stated. Nothing is hidden inside the maths."""

    base_fcf: float
    revenue_growth: float
    growth_fade_to: float
    fcf_margin: float | None
    discount_rate: float
    terminal_growth: float
    years: int
    tax_rate: float
    shares: float
    net_debt: float
    terminal_method: str = "perpetuity"     # perpetuity | exit_multiple
    exit_multiple: float | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_fcf": self.base_fcf,
            "revenue_growth_year1": round(self.revenue_growth, 4),
            "growth_fade_to": round(self.growth_fade_to, 4),
            "fcf_margin": None if self.fcf_margin is None else round(self.fcf_margin, 4),
            "discount_rate": round(self.discount_rate, 4),
            "terminal_growth": round(self.terminal_growth, 4),
            "projection_years": self.years,
            "tax_rate": round(self.tax_rate, 4),
            "shares_diluted": self.shares,
            "net_debt": self.net_debt,
            "terminal_method": self.terminal_method,
            "exit_multiple": self.exit_multiple,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class DcfResult:
    equity_value: float
    value_per_share: float
    terminal_value_share: float          # fraction of PV coming from terminal value
    projected_fcf: tuple[float, ...]
    discounted_fcf: tuple[float, ...]
    assumptions: DcfAssumptions
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "equity_value": round(self.equity_value, 2),
            "value_per_share": round(self.value_per_share, 2),
            "terminal_value_share": round(self.terminal_value_share, 3),
            "projected_fcf": [round(v, 2) for v in self.projected_fcf],
            "assumptions": self.assumptions.to_dict(),
            "warnings": list(self.warnings),
        }


def discounted_cash_flow(assumptions: DcfAssumptions) -> DcfResult:
    """Two-stage DCF: growth fading linearly to a terminal rate.

    Guards: the discount rate must exceed terminal growth (otherwise the
    perpetuity is infinite), and a terminal value above ~80% of total present
    value is flagged -- such a valuation is an assumption about year 11, not an
    analysis of the business.
    """
    a = assumptions
    warnings: list[str] = []
    if a.years <= 0:
        raise ValueError("projection years must be positive")
    if a.discount_rate <= a.terminal_growth:
        raise ValueError("discount rate must exceed terminal growth")
    if a.shares <= 0:
        raise ValueError("share count must be positive")

    projected: list[float] = []
    fcf = a.base_fcf
    for year in range(1, a.years + 1):
        # linear fade from year-1 growth to the terminal rate
        weight = (year - 1) / max(1, a.years - 1)
        growth = a.revenue_growth + (a.growth_fade_to - a.revenue_growth) * weight
        fcf = fcf * (1.0 + growth)
        projected.append(fcf)

    discounted = [cf / ((1.0 + a.discount_rate) ** (i + 1))
                  for i, cf in enumerate(projected)]

    if a.terminal_method == "exit_multiple" and a.exit_multiple:
        terminal_value = projected[-1] * a.exit_multiple
    else:
        terminal_value = (projected[-1] * (1.0 + a.terminal_growth)
                          / (a.discount_rate - a.terminal_growth))
    discounted_terminal = terminal_value / ((1.0 + a.discount_rate) ** a.years)

    enterprise_value = sum(discounted) + discounted_terminal
    equity_value = enterprise_value - a.net_debt
    per_share = equity_value / a.shares

    tv_share = discounted_terminal / enterprise_value if enterprise_value else 1.0
    if tv_share > 0.8:
        warnings.append(
            f"{tv_share:.0%} of value sits in the terminal value: this is a bet on "
            f"year {a.years + 1} and beyond, not on the forecast period")
    if a.base_fcf <= 0:
        warnings.append("base free cash flow is negative: DCF output is unreliable")
    if per_share <= 0:
        warnings.append("model implies zero or negative equity value")
    if a.revenue_growth > 0.4:
        warnings.append(f"year-1 growth of {a.revenue_growth:.0%} is aggressive and "
                        f"rarely sustained")

    return DcfResult(equity_value=equity_value, value_per_share=per_share,
                     terminal_value_share=tv_share, projected_fcf=tuple(projected),
                     discounted_fcf=tuple(discounted), assumptions=a,
                     warnings=tuple(warnings))


def reverse_dcf(*, price: float, shares: float, base_fcf: float, net_debt: float,
                discount_rate: float, terminal_growth: float, years: int = 10,
                tolerance: float = 0.005, max_iterations: int = 60
                ) -> dict[str, Any]:
    """Solve for the growth rate that today's price implies.

    The most useful question in valuation: *what does the market already
    believe?* Returns the implied constant growth rate and how it compares with
    history, or an explanation of why no rate can justify the price.
    """
    if price <= 0 or shares <= 0:
        return {"implied_growth": None,
                "reason": "price and share count must be positive"}
    if base_fcf <= 0:
        return {"implied_growth": None,
                "reason": "reverse DCF requires positive base free cash flow"}

    def value_at(growth: float) -> float:
        assumptions = DcfAssumptions(
            base_fcf=base_fcf, revenue_growth=growth, growth_fade_to=terminal_growth,
            fcf_margin=None, discount_rate=discount_rate,
            terminal_growth=terminal_growth, years=years, tax_rate=0.0,
            shares=shares, net_debt=net_debt)
        return discounted_cash_flow(assumptions).value_per_share

    low, high = -0.5, 1.0
    if value_at(high) < price:
        return {"implied_growth": None,
                "reason": f"even {high:.0%} growth for {years} years does not justify "
                          f"the current price under these assumptions",
                "max_tested_growth": high}
    if value_at(low) > price:
        return {"implied_growth": None,
                "reason": "the price is below the value implied by steep decline; "
                          "the market may be pricing in distress or the inputs are wrong",
                "min_tested_growth": low}

    for _ in range(max_iterations):
        mid = (low + high) / 2
        val = value_at(mid)
        if abs(val - price) / price < tolerance:
            return {"implied_growth": round(mid, 4),
                    "implied_value": round(val, 2),
                    "assumptions": {"discount_rate": discount_rate,
                                    "terminal_growth": terminal_growth,
                                    "years": years}}
        if val < price:
            low = mid
        else:
            high = mid
    return {"implied_growth": round((low + high) / 2, 4), "converged": False}


def sensitivity_grid(assumptions: DcfAssumptions, *,
                     discount_deltas: Sequence[float] = (-0.02, -0.01, 0.0, 0.01, 0.02),
                     growth_deltas: Sequence[float] = (-0.02, -0.01, 0.0, 0.01, 0.02)
                     ) -> dict[str, Any]:
    """Value per share across discount-rate and terminal-growth perturbations.

    The spread across the grid is the honest expression of model uncertainty;
    a DCF quoted to the cent without one is theatre.
    """
    rows: list[dict[str, Any]] = []
    values: list[float] = []
    for d_delta in discount_deltas:
        row: dict[str, Any] = {"discount_rate": round(assumptions.discount_rate + d_delta, 4),
                               "values": []}
        for g_delta in growth_deltas:
            rate = assumptions.discount_rate + d_delta
            growth = assumptions.terminal_growth + g_delta
            if rate <= growth:
                row["values"].append(None)
                continue
            perturbed = DcfAssumptions(**{**_as_kwargs(assumptions),
                                          "discount_rate": rate,
                                          "terminal_growth": growth})
            try:
                value = discounted_cash_flow(perturbed).value_per_share
            except ValueError:
                row["values"].append(None)
                continue
            row["values"].append(round(value, 2))
            values.append(value)
        rows.append(row)
    return {
        "discount_rates": [round(assumptions.discount_rate + d, 4) for d in discount_deltas],
        "terminal_growths": [round(assumptions.terminal_growth + g, 4) for g in growth_deltas],
        "grid": rows,
        "min": round(min(values), 2) if values else None,
        "max": round(max(values), 2) if values else None,
        "spread_ratio": round(max(values) / min(values), 2)
        if values and min(values) > 0 else None,
    }


def _as_kwargs(assumptions: DcfAssumptions) -> dict[str, Any]:
    return {f: getattr(assumptions, f) for f in assumptions.__slots__}


# ------------------------------------------------------------- scenarios ---
@dataclass(frozen=True, slots=True)
class ValuationScenarios:
    bear: float | None
    base: float | None
    bull: float | None
    method: str
    price: float | None
    assumptions: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def expected_returns(self) -> dict[str, float | None]:
        if not self.price or self.price <= 0:
            return {"bear": None, "base": None, "bull": None}
        return {name: (value / self.price - 1.0) if value else None
                for name, value in (("bear", self.bear), ("base", self.base),
                                    ("bull", self.bull))}

    def to_dict(self) -> dict[str, Any]:
        return {"method": self.method, "price": self.price,
                "fair_value": {"bear": _r(self.bear), "base": _r(self.base),
                               "bull": _r(self.bull)},
                "expected_return": {k: (round(v, 4) if v is not None else None)
                                    for k, v in self.expected_returns().items()},
                "assumptions": dict(self.assumptions),
                "warnings": list(self.warnings)}


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def scenario_valuation(*, base_fcf: float, shares: float, net_debt: float,
                       growth: float, discount_rate: float, terminal_growth: float,
                       price: float | None, years: int = 10,
                       scenario_config: Mapping[str, Mapping[str, float]] | None = None
                       ) -> ValuationScenarios:
    """Bear/base/bull DCF driven by configured deltas, not by intuition."""
    config = scenario_config or {
        "bear": {"revenue_growth_delta": -0.05, "margin_delta": -0.03,
                 "discount_delta": 0.02},
        "base": {},
        "bull": {"revenue_growth_delta": 0.04, "margin_delta": 0.02,
                 "discount_delta": -0.01},
    }
    results: dict[str, float | None] = {}
    warnings: list[str] = []
    base_assumptions: dict[str, Any] = {}

    for name in ("bear", "base", "bull"):
        deltas = config.get(name, {})
        g = growth + float(deltas.get("revenue_growth_delta", 0.0))
        r = discount_rate + float(deltas.get("discount_delta", 0.0))
        tg = terminal_growth + float(deltas.get("terminal_growth_delta", 0.0))
        tg = min(tg, r - 0.005)
        # A margin delta shifts the cash-flow base itself: three points of margin
        # on a 20%-margin business is a 15% change in cash flow, and ignoring it
        # would make the bear case far too gentle.
        margin_delta = float(deltas.get("margin_delta", 0.0))
        scenario_fcf = base_fcf * (1.0 + margin_delta * 5.0) if margin_delta else base_fcf
        assumptions = DcfAssumptions(
            base_fcf=scenario_fcf, revenue_growth=g, growth_fade_to=tg, fcf_margin=None,
            discount_rate=r, terminal_growth=tg, years=years, tax_rate=0.0,
            shares=shares, net_debt=net_debt,
            notes=(f"scenario={name}", f"margin_delta={margin_delta:+.3f}")
            if margin_delta else (f"scenario={name}",))
        try:
            result = discounted_cash_flow(assumptions)
        except ValueError as exc:
            results[name] = None
            warnings.append(f"{name} case not computable: {exc}")
            continue
        results[name] = result.value_per_share
        warnings.extend(f"{name}: {w}" for w in result.warnings)
        if name == "base":
            base_assumptions = assumptions.to_dict()

    bear, base, bull = results.get("bear"), results.get("base"), results.get("bull")
    if bear is not None and bull is not None and bear > bull:
        warnings.append("bear case exceeds bull case: scenario deltas are inconsistent")
    return ValuationScenarios(bear=bear, base=base, bull=bull, method="dcf",
                              price=price, assumptions=base_assumptions,
                              warnings=tuple(warnings))


def multiples_valuation(*, metric_value: float | None, multiple_bear: float | None,
                        multiple_base: float | None, multiple_bull: float | None,
                        shares: float | None, net_debt: float = 0.0,
                        price: float | None, metric_name: str = "ebitda",
                        enterprise_based: bool = True) -> ValuationScenarios:
    """Fair value from applying peer/historical multiples to a fundamental."""
    if metric_value is None or not shares or shares <= 0 or metric_value <= 0:
        return ValuationScenarios(None, None, None, f"multiples:{metric_name}", price,
                                  warnings=("insufficient inputs for a multiples view",))

    def per_share(multiple: float | None) -> float | None:
        if multiple is None or multiple <= 0:
            return None
        gross = metric_value * multiple
        equity = gross - net_debt if enterprise_based else gross
        return equity / shares

    return ValuationScenarios(
        bear=per_share(multiple_bear), base=per_share(multiple_base),
        bull=per_share(multiple_bull), method=f"multiples:{metric_name}", price=price,
        assumptions={"metric": metric_name, "metric_value": metric_value,
                     "multiples": {"bear": multiple_bear, "base": multiple_base,
                                   "bull": multiple_bull},
                     "enterprise_based": enterprise_based})


def blend(scenarios: Sequence[ValuationScenarios], *,
          weights: Sequence[float] | None = None,
          disagreement_threshold: float = 0.4) -> ValuationScenarios:
    """Combine independent valuation methods, surfacing disagreement.

    When methods disagree by more than ``disagreement_threshold`` on base value,
    the blend is still returned but flagged loudly: an average of two
    incompatible views is not a better view.
    """
    usable = [s for s in scenarios if s.base is not None]
    if not usable:
        return ValuationScenarios(None, None, None, "blend", 
                                  scenarios[0].price if scenarios else None,
                                  warnings=("no valuation method produced a value",))
    w = list(weights or [1.0] * len(usable))
    w = w[:len(usable)] + [1.0] * max(0, len(usable) - len(w))
    total = sum(w) or 1.0

    def combine(field_name: str) -> float | None:
        pairs = [(getattr(s, field_name), wi) for s, wi in zip(usable, w)
                 if getattr(s, field_name) is not None]
        if not pairs:
            return None
        return sum(v * wi for v, wi in pairs) / sum(wi for _, wi in pairs)

    bases = [s.base for s in usable]
    warnings: list[str] = []
    for s in usable:
        warnings.extend(s.warnings)
    if len(bases) >= 2:
        spread = (max(bases) - min(bases)) / max(bases) if max(bases) > 0 else 0.0
        if spread > disagreement_threshold:
            warnings.append(
                f"valuation methods disagree by {spread:.0%} "
                f"({', '.join(f'{s.method}={s.base:.2f}' for s in usable)}): "
                f"treat the range, not the midpoint, as the answer")
    return ValuationScenarios(
        bear=combine("bear"), base=combine("base"), bull=combine("bull"),
        method="blend(" + ", ".join(s.method for s in usable) + ")",
        price=usable[0].price,
        assumptions={"components": [s.method for s in usable],
                     "weights": [round(x / total, 3) for x in w[:len(usable)]]},
        warnings=tuple(warnings))


def valuation_evidence(scenarios: ValuationScenarios) -> list[Evidence]:
    out: list[Evidence] = []
    returns = scenarios.expected_returns()
    base = returns.get("base")
    if base is not None:
        out.append(Evidence(
            label="Valuation (base case)",
            detail=f"{scenarios.method} implies {base:+.0%} versus the current price",
            direction=clamp(base / 0.5, -1, 1), weight=0.8,
            claim_type=ClaimType.MODEL_PREDICTION, quality=DataQuality.FAIR,
            sources=("model",)))
    bear = returns.get("bear")
    if bear is not None and bear < -0.2:
        out.append(Evidence(
            label="Downside in the bear case",
            detail=f"bear-case fair value implies {bear:.0%}",
            direction=-0.6, weight=0.6, claim_type=ClaimType.MODEL_PREDICTION,
            quality=DataQuality.FAIR, sources=("model",)))
    for warning in scenarios.warnings:
        if "terminal value" in warning or "disagree" in warning:
            out.append(Evidence(
                label="Valuation fragility", detail=warning, direction=-0.3,
                weight=0.4, claim_type=ClaimType.ASSUMPTION,
                quality=DataQuality.FAIR, sources=("model",)))
    return out


def cost_of_equity(*, risk_free_rate: float, equity_risk_premium: float,
                   beta: float | None, size_premium: float = 0.0,
                   min_rate: float = 0.06, max_rate: float = 0.20) -> tuple[float, str]:
    """CAPM cost of equity, clamped to a defensible band.

    Returns the rate and a plain-language note about how it was derived, because
    a discount rate is the single most leverage-heavy assumption in any DCF.
    """
    if beta is None:
        rate = risk_free_rate + equity_risk_premium + size_premium
        note = (f"beta unavailable; used risk-free {risk_free_rate:.1%} + ERP "
                f"{equity_risk_premium:.1%}" +
                (f" + size premium {size_premium:.1%}" if size_premium else ""))
    else:
        capped_beta = clamp(beta, 0.3, 3.0)
        rate = risk_free_rate + capped_beta * equity_risk_premium + size_premium
        note = (f"CAPM: {risk_free_rate:.1%} + beta {capped_beta:.2f} x ERP "
                f"{equity_risk_premium:.1%}" +
                (f" + size premium {size_premium:.1%}" if size_premium else ""))
        if beta != capped_beta:
            note += f" (beta clamped from {beta:.2f})"
    clamped = clamp(rate, min_rate, max_rate)
    if clamped != rate:
        note += f"; clamped to {clamped:.1%}"
    return clamped, note
