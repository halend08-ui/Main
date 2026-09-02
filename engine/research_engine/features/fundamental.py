"""Fundamental analysis.

Input is a point-in-time fundamental record (metric -> ordered history), so
everything computed here is what an analyst could have known on the as-of date.

Design decisions worth stating:

* **TTM over last-reported.** Trailing-twelve-month aggregates are used where
  quarterly data exists, because comparing a Q1 figure to an annual one is a
  common and invisible error.
* **Growth durability over growth rate.** A single 40% year is weaker evidence
  than four consecutive 15% years; the engine scores consistency separately.
* **A moat must be earned.** ``assess_moat`` returns "no evidence" unless
  sustained returns on capital, stable-or-rising margins and pricing power show
  up in the numbers. Narrative alone never qualifies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.numeric import (cagr, clamp, is_finite, linear_score,
                                          mean, median, pct_change, safe_div,
                                          stdev)
from research_engine.core.types import (ClaimType, DataQuality, Evidence, Metric,
                                        Period)

#: Metrics the engine needs; everything else is optional context.
CORE_METRICS = ("revenue", "net_income", "operating_income", "gross_profit",
                "total_assets", "total_equity", "total_liabilities",
                "current_assets", "current_liabilities", "cash_and_equivalents",
                "long_term_debt", "short_term_debt", "operating_cash_flow",
                "capex", "shares_diluted", "interest_expense", "income_tax",
                "pretax_income", "buybacks", "dividends_paid",
                "stock_compensation", "depreciation_amortization", "inventory")


@dataclass
class FundamentalSnapshot:
    """Derived fundamentals for one asset at one point in time."""

    symbol: str
    as_of: date
    metrics: dict[str, Metric] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def value(self, name: str) -> float | None:
        metric = self.metrics.get(name)
        return metric.value if metric else None

    def set(self, name: str, value: float | None, unit: str = "",
            quality: DataQuality = DataQuality.GOOD, **detail: Any) -> None:
        self.metrics[name] = Metric(name=name, value=value, unit=unit,
                                    quality=quality, detail=detail)
        if value is None:
            self.missing.append(name)

    def coverage(self) -> float:
        available = sum(1 for m in self.metrics.values() if m.available)
        return available / len(self.metrics) if self.metrics else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "as_of": self.as_of.isoformat(),
                "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
                "evidence": [e.to_dict() for e in self.evidence],
                "missing": sorted(set(self.missing)), "notes": self.notes,
                "coverage": round(self.coverage(), 3)}


def _values(history: Mapping[str, Sequence[Any]], metric: str) -> list[float]:
    """Ordered non-null values for a metric (oldest first)."""
    return [float(p.value) for p in (history.get(metric) or [])
            if getattr(p, "value", None) is not None]


def _latest(history: Mapping[str, Sequence[Any]], metric: str) -> float | None:
    values = _values(history, metric)
    return values[-1] if values else None


def ttm(quarterly: Mapping[str, Sequence[Any]], metric: str) -> float | None:
    """Trailing twelve months from the last four quarters (flow metrics only)."""
    values = _values(quarterly, metric)
    if len(values) < 4:
        return None
    return float(sum(values[-4:]))


# ---------------------------------------------------------------- growth ---
def growth_profile(history: Mapping[str, Sequence[Any]], metric: str, *,
                   years: Sequence[int] = (1, 3, 5, 10)) -> dict[str, float | None]:
    """CAGR over several windows plus a consistency measure.

    ``consistency`` is the share of periods with positive growth; ``stability``
    is 1 - (stdev of yearly growth / |mean growth|), floored at 0. High growth
    with low stability is a different (and more fragile) asset than steady
    compounding, and the score keeps them apart.
    """
    values = _values(history, metric)
    out: dict[str, float | None] = {}
    for window in years:
        if len(values) > window:
            out[f"cagr_{window}y"] = cagr(values[-(window + 1)], values[-1], window)
        else:
            out[f"cagr_{window}y"] = None

    yearly = [pct_change(values[i], values[i - 1]) for i in range(1, len(values))]
    yearly = [g for g in yearly if g is not None]
    if yearly:
        positive = sum(1 for g in yearly if g > 0)
        out["consistency"] = positive / len(yearly)
        avg = mean(yearly)
        sd = stdev(yearly)
        if avg is not None and sd is not None and abs(avg) > 1e-6:
            out["stability"] = clamp(1.0 - sd / abs(avg), 0.0, 1.0)
        else:
            out["stability"] = None
        out["latest_growth"] = yearly[-1]
        out["acceleration"] = (yearly[-1] - yearly[-2]) if len(yearly) >= 2 else None
    else:
        out.update({"consistency": None, "stability": None,
                    "latest_growth": None, "acceleration": None})
    return out


# --------------------------------------------------------- profitability ---
def margins(history: Mapping[str, Sequence[Any]]) -> dict[str, float | None]:
    revenue = _latest(history, "revenue")
    return {
        "gross_margin": safe_div(_latest(history, "gross_profit"), revenue),
        "operating_margin": safe_div(_latest(history, "operating_income"), revenue),
        "net_margin": safe_div(_latest(history, "net_income"), revenue),
        "fcf_margin": safe_div(free_cash_flow(history), revenue),
    }


def margin_trend(history: Mapping[str, Sequence[Any]], metric: str = "operating_income",
                 periods: int = 4) -> float | None:
    """Change in margin (percentage points) over ``periods`` reporting years."""
    revenues = _values(history, "revenue")
    numerators = _values(history, metric)
    n = min(len(revenues), len(numerators))
    if n <= periods:
        return None
    recent = safe_div(numerators[-1], revenues[-1])
    earlier = safe_div(numerators[-(periods + 1)], revenues[-(periods + 1)])
    if recent is None or earlier is None:
        return None
    return recent - earlier


def free_cash_flow(history: Mapping[str, Sequence[Any]]) -> float | None:
    ocf = _latest(history, "operating_cash_flow")
    capex = _latest(history, "capex")
    if ocf is None:
        return None
    # capex is reported as a positive outflow in XBRL Payments* concepts
    return ocf - abs(capex) if capex is not None else None


def fcf_history(history: Mapping[str, Sequence[Any]]) -> list[float]:
    ocf = _values(history, "operating_cash_flow")
    capex = _values(history, "capex")
    n = min(len(ocf), len(capex))
    if n == 0:
        return []
    return [ocf[-n + i] - abs(capex[-n + i]) for i in range(n)]


def return_on_equity(history: Mapping[str, Sequence[Any]]) -> float | None:
    equity = _values(history, "total_equity")
    net_income = _latest(history, "net_income")
    if net_income is None or not equity:
        return None
    # average equity avoids flattering companies that shrank equity via buybacks
    avg_equity = mean(equity[-2:]) if len(equity) >= 2 else equity[-1]
    if avg_equity is None or avg_equity <= 0:
        return None            # negative-equity ROE is not interpretable
    return net_income / avg_equity


def invested_capital(history: Mapping[str, Sequence[Any]]) -> float | None:
    equity = _latest(history, "total_equity")
    debt = total_debt(history)
    cash = _latest(history, "cash_and_equivalents")
    if equity is None or debt is None:
        return None
    capital = equity + debt - (cash or 0.0)
    return capital if capital > 0 else None


def nopat(history: Mapping[str, Sequence[Any]], *,
          default_tax_rate: float = 0.21) -> float | None:
    operating = _latest(history, "operating_income")
    if operating is None:
        return None
    tax = _latest(history, "income_tax")
    pretax = _latest(history, "pretax_income")
    rate = safe_div(tax, pretax)
    if rate is None or not 0.0 <= rate <= 0.6:
        rate = default_tax_rate      # documented fallback, surfaced in detail
    return operating * (1.0 - rate)


def return_on_invested_capital(history: Mapping[str, Sequence[Any]], *,
                               default_tax_rate: float = 0.21) -> float | None:
    return safe_div(nopat(history, default_tax_rate=default_tax_rate),
                    invested_capital(history))


# ---------------------------------------------------------- balance sheet --
def total_debt(history: Mapping[str, Sequence[Any]]) -> float | None:
    explicit = _latest(history, "total_debt")
    if explicit is not None:
        return explicit
    lt = _latest(history, "long_term_debt")
    st = _latest(history, "short_term_debt")
    if lt is None and st is None:
        return None
    return (lt or 0.0) + (st or 0.0)


def net_debt(history: Mapping[str, Sequence[Any]]) -> float | None:
    debt = total_debt(history)
    cash = _latest(history, "cash_and_equivalents")
    if debt is None:
        return None
    return debt - (cash or 0.0)


def balance_sheet_health(history: Mapping[str, Sequence[Any]]) -> dict[str, float | None]:
    equity = _latest(history, "total_equity")
    debt = total_debt(history)
    ebit = _latest(history, "operating_income")
    interest = _latest(history, "interest_expense")
    fcf = free_cash_flow(history)
    cash = _latest(history, "cash_and_equivalents")

    out: dict[str, float | None] = {
        "debt_to_equity": safe_div(debt, equity) if (equity or 0) > 0 else None,
        "net_debt": net_debt(history),
        "current_ratio": safe_div(_latest(history, "current_assets"),
                                  _latest(history, "current_liabilities")),
        "interest_coverage": safe_div(ebit, abs(interest)) if interest else None,
        "debt_to_ebitda": None,
        "cash_runway_years": None,
    }
    ebitda = None
    if ebit is not None:
        da = _latest(history, "depreciation_amortization")
        ebitda = ebit + (da or 0.0)
    if ebitda and ebitda > 0 and debt is not None:
        out["debt_to_ebitda"] = debt / ebitda
    # Runway only matters when the company burns cash; for profitable firms it
    # is not applicable rather than infinite.
    if fcf is not None and fcf < 0 and cash is not None:
        out["cash_runway_years"] = cash / abs(fcf)
    return out


def altman_z(history: Mapping[str, Sequence[Any]], market_cap: float | None
             ) -> float | None:
    """Altman Z-score for bankruptcy risk (manufacturing/general form).

    Not applicable to financials or early-stage companies; callers must gate on
    sector. Returns None when any component is unavailable rather than
    substituting zeros, since a zeroed component silently flatters the score.
    """
    assets = _latest(history, "total_assets")
    if not assets or assets <= 0:
        return None
    working_capital = None
    ca, cl = _latest(history, "current_assets"), _latest(history, "current_liabilities")
    if ca is not None and cl is not None:
        working_capital = ca - cl
    ebit = _latest(history, "operating_income")
    revenue = _latest(history, "revenue")
    liabilities = _latest(history, "total_liabilities")
    retained = _latest(history, "total_equity")   # proxy; documented below
    components = [working_capital, retained, ebit, market_cap, liabilities, revenue]
    if any(c is None for c in components):
        return None
    return (1.2 * (working_capital / assets)
            + 1.4 * (retained / assets)          # equity used as retained-earnings proxy
            + 3.3 * (ebit / assets)
            + 0.6 * (market_cap / liabilities if liabilities else 0)
            + 1.0 * (revenue / assets))


# ------------------------------------------------------ capital allocation --
def capital_allocation(history: Mapping[str, Sequence[Any]]) -> dict[str, float | None]:
    shares = _values(history, "shares_diluted")
    dilution = None
    if len(shares) >= 2 and shares[-2] > 0:
        dilution = shares[-1] / shares[-2] - 1.0
    dilution_5y = None
    if len(shares) >= 6 and shares[-6] > 0:
        dilution_5y = cagr(shares[-6], shares[-1], 5)

    fcf = free_cash_flow(history)
    buybacks = _latest(history, "buybacks")
    dividends = _latest(history, "dividends_paid")
    sbc = _latest(history, "stock_compensation")
    revenue = _latest(history, "revenue")

    return {
        "share_change_1y": dilution,
        "share_cagr_5y": dilution_5y,
        "buyback_yield_of_fcf": safe_div(abs(buybacks) if buybacks else None, fcf),
        "dividend_payout_of_fcf": safe_div(abs(dividends) if dividends else None, fcf),
        "sbc_pct_revenue": safe_div(abs(sbc) if sbc else None, revenue),
        "fcf_conversion": safe_div(fcf, _latest(history, "net_income")),
        "reinvestment_rate": safe_div(
            abs(_latest(history, "capex")) if _latest(history, "capex") else None,
            _latest(history, "operating_cash_flow")),
    }


# ------------------------------------------------------------------ moat ---
@dataclass(frozen=True, slots=True)
class MoatAssessment:
    verdict: str                  # none | possible | narrow | wide
    score: float                  # 0..1
    supporting: tuple[str, ...]
    contradicting: tuple[str, ...]
    caveat: str = ("Quantitative proxies only. A durable competitive advantage "
                   "cannot be confirmed from financial statements alone.")

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "score": round(self.score, 3),
                "supporting": list(self.supporting),
                "contradicting": list(self.contradicting), "caveat": self.caveat}


def assess_moat(history: Mapping[str, Sequence[Any]], *,
                default_tax_rate: float = 0.21) -> MoatAssessment:
    """Infer competitive advantage from *sustained* financial evidence only."""
    supporting: list[str] = []
    contradicting: list[str] = []
    points = 0.0
    possible = 0.0

    # 1. Sustained high returns on capital -- the core signature of a moat.
    roic = return_on_invested_capital(history, default_tax_rate=default_tax_rate)
    possible += 2.0
    if roic is not None:
        if roic > 0.20:
            points += 2.0
            supporting.append(f"ROIC of {roic:.0%} is well above a typical cost of capital")
        elif roic > 0.12:
            points += 1.0
            supporting.append(f"ROIC of {roic:.0%} exceeds a typical cost of capital")
        elif roic < 0.06:
            contradicting.append(f"ROIC of {roic:.0%} does not clear a typical cost of capital")

    # 2. Gross margin level and stability -- pricing power.
    gross_values = []
    revenues = _values(history, "revenue")
    gross = _values(history, "gross_profit")
    n = min(len(revenues), len(gross))
    for i in range(n):
        gm = safe_div(gross[-n + i], revenues[-n + i])
        if gm is not None:
            gross_values.append(gm)
    possible += 2.0
    if len(gross_values) >= 4:
        level = median(gross_values)
        sd = stdev(gross_values)
        if level is not None and level > 0.5:
            points += 1.0
            supporting.append(f"gross margin sustained near {level:.0%}")
        if sd is not None and sd < 0.03:
            points += 1.0
            supporting.append("gross margin stable across cycles (pricing power)")
        elif sd is not None and sd > 0.08:
            contradicting.append("gross margin swings widely: limited pricing power")

    # 3. Margin direction -- expanding margins suggest widening advantage.
    trend = margin_trend(history, "operating_income", periods=3)
    possible += 1.0
    if trend is not None:
        if trend > 0.02:
            points += 1.0
            supporting.append(f"operating margin expanded {trend * 100:.1f}pp over 3 years")
        elif trend < -0.03:
            contradicting.append(
                f"operating margin contracted {abs(trend) * 100:.1f}pp over 3 years")

    # 4. Cash conversion -- accounting profit that becomes cash.
    conversion = safe_div(free_cash_flow(history), _latest(history, "net_income"))
    possible += 1.0
    if conversion is not None:
        if conversion > 0.9:
            points += 1.0
            supporting.append(f"free cash flow covers {conversion:.0%} of net income")
        elif conversion < 0.5:
            contradicting.append(
                f"free cash flow is only {conversion:.0%} of reported earnings")

    # 5. Growth without dilution.
    allocation = capital_allocation(history)
    dilution = allocation.get("share_cagr_5y")
    possible += 1.0
    if dilution is not None:
        if dilution < 0:
            points += 1.0
            supporting.append(f"share count shrinking {abs(dilution):.1%}/yr")
        elif dilution > 0.04:
            contradicting.append(f"share count growing {dilution:.1%}/yr dilutes owners")

    score = points / possible if possible else 0.0
    if score >= 0.75 and len(supporting) >= 4:
        verdict = "wide"
    elif score >= 0.55 and len(supporting) >= 3:
        verdict = "narrow"
    elif score >= 0.35:
        verdict = "possible"
    else:
        verdict = "none"
    if not supporting:
        verdict = "none"
    return MoatAssessment(verdict, score, tuple(supporting), tuple(contradicting))


# ------------------------------------------------------------- snapshot ----
def build_snapshot(symbol: str, as_of: date,
                   annual: Mapping[str, Sequence[Any]], *,
                   quarterly: Mapping[str, Sequence[Any]] | None = None,
                   market_cap: float | None = None,
                   default_tax_rate: float = 0.21,
                   sector: str | None = None) -> FundamentalSnapshot:
    """Compute the full derived fundamental picture from raw history."""
    snap = FundamentalSnapshot(symbol=symbol, as_of=as_of)
    quarterly = quarterly or {}

    revenue_growth = growth_profile(annual, "revenue")
    earnings_growth = growth_profile(annual, "net_income")
    fcf_values = fcf_history(annual)
    fcf_growth = (cagr(fcf_values[-4], fcf_values[-1], 3)
                  if len(fcf_values) >= 4 and fcf_values[-4] > 0 else None)

    for key, value in revenue_growth.items():
        snap.set(f"revenue_{key}", value)
    for key in ("cagr_1y", "cagr_3y", "cagr_5y", "consistency"):
        snap.set(f"earnings_{key}", earnings_growth.get(key))
    snap.set("fcf_cagr_3y", fcf_growth)

    for key, value in margins(annual).items():
        snap.set(key, value)
    snap.set("gross_margin_trend_3y", margin_trend(annual, "gross_profit", 3))
    snap.set("operating_margin_trend_3y", margin_trend(annual, "operating_income", 3))
    snap.set("roe", return_on_equity(annual))
    snap.set("roic", return_on_invested_capital(annual, default_tax_rate=default_tax_rate),
             detail={"tax_rate_source": "effective" if _latest(annual, "income_tax")
                     else "default"})
    snap.set("free_cash_flow", free_cash_flow(annual), unit="USD")
    snap.set("revenue_ttm", ttm(quarterly, "revenue"), unit="USD")
    snap.set("net_income_ttm", ttm(quarterly, "net_income"), unit="USD")

    for key, value in balance_sheet_health(annual).items():
        snap.set(key, value)
    for key, value in capital_allocation(annual).items():
        snap.set(key, value)

    # Z-score is only meaningful for non-financial operating companies.
    financial_sector = bool(sector and "financ" in sector.lower())
    if financial_sector:
        snap.set("altman_z", None)
        snap.notes.append("Altman Z-score not applicable to financial companies")
    else:
        snap.set("altman_z", altman_z(annual, market_cap))

    moat = assess_moat(annual, default_tax_rate=default_tax_rate)
    snap.metrics["moat_score"] = Metric("moat_score", moat.score, "",
                                        DataQuality.FAIR, detail=moat.to_dict())
    snap.evidence.extend(_moat_evidence(moat))
    snap.evidence.extend(_growth_evidence(revenue_growth, earnings_growth))
    snap.evidence.extend(_health_evidence(snap))
    return snap


def _moat_evidence(moat: MoatAssessment) -> list[Evidence]:
    out = [Evidence(label=f"Competitive position: {moat.verdict}",
                    detail=", ".join(moat.supporting) or "no supporting evidence found",
                    direction=clamp(moat.score * 2 - 1, -1, 1), weight=0.6,
                    claim_type=ClaimType.INTERPRETATION, quality=DataQuality.FAIR,
                    sources=("company filings",))]
    for contra in moat.contradicting:
        out.append(Evidence(label="Competitive concern", detail=contra,
                            direction=-0.5, weight=0.4,
                            claim_type=ClaimType.OBSERVATION,
                            quality=DataQuality.GOOD, sources=("company filings",)))
    return out


def _growth_evidence(revenue: Mapping[str, float | None],
                     earnings: Mapping[str, float | None]) -> list[Evidence]:
    out: list[Evidence] = []
    r3 = revenue.get("cagr_3y")
    if r3 is not None:
        direction = clamp(r3 / 0.25, -1, 1)
        out.append(Evidence(
            label="Revenue growth (3y CAGR)", detail=f"{r3:.1%} compound annual growth",
            direction=direction, weight=0.7, claim_type=ClaimType.OBSERVATION,
            quality=DataQuality.GOOD, sources=("company filings",)))
    consistency = revenue.get("consistency")
    if consistency is not None and consistency >= 0.8:
        out.append(Evidence(
            label="Growth durability",
            detail=f"revenue rose in {consistency:.0%} of reported years",
            direction=0.5, weight=0.5, claim_type=ClaimType.OBSERVATION,
            quality=DataQuality.GOOD, sources=("company filings",)))
    accel = revenue.get("acceleration")
    if accel is not None and abs(accel) > 0.05:
        out.append(Evidence(
            label="Growth inflection",
            detail=f"latest-year growth {'accelerated' if accel > 0 else 'decelerated'}"
                   f" by {abs(accel) * 100:.1f}pp",
            direction=clamp(accel * 4, -1, 1), weight=0.5,
            claim_type=ClaimType.OBSERVATION, quality=DataQuality.GOOD,
            sources=("company filings",)))
    e3 = earnings.get("cagr_3y")
    if e3 is not None and r3 is not None and e3 > r3 + 0.03:
        out.append(Evidence(
            label="Operating leverage",
            detail=f"earnings compounding {e3:.1%} vs revenue {r3:.1%}",
            direction=0.6, weight=0.5, claim_type=ClaimType.OBSERVATION,
            quality=DataQuality.GOOD, sources=("company filings",)))
    return out


def _health_evidence(snap: FundamentalSnapshot) -> list[Evidence]:
    out: list[Evidence] = []
    coverage = snap.value("interest_coverage")
    if coverage is not None and coverage < 2.0:
        out.append(Evidence(
            label="Debt service risk",
            detail=f"operating income covers interest only {coverage:.1f}x",
            direction=-0.8, weight=0.8, claim_type=ClaimType.OBSERVATION,
            quality=DataQuality.GOOD, sources=("company filings",)))
    runway = snap.value("cash_runway_years")
    if runway is not None and runway < 2.0:
        out.append(Evidence(
            label="Financing risk",
            detail=f"cash burn implies about {runway:.1f} years of runway",
            direction=-0.9, weight=0.9, claim_type=ClaimType.MODEL_PREDICTION,
            quality=DataQuality.FAIR, sources=("company filings",)))
    z = snap.value("altman_z")
    if z is not None and z < 1.8:
        out.append(Evidence(
            label="Distress risk",
            detail=f"Altman Z of {z:.2f} sits in the distress zone",
            direction=-0.8, weight=0.7, claim_type=ClaimType.MODEL_PREDICTION,
            quality=DataQuality.FAIR, sources=("company filings",)))
    dilution = snap.value("share_cagr_5y")
    if dilution is not None and dilution > 0.05:
        out.append(Evidence(
            label="Shareholder dilution",
            detail=f"share count growing {dilution:.1%} per year",
            direction=-0.5, weight=0.5, claim_type=ClaimType.OBSERVATION,
            quality=DataQuality.GOOD, sources=("company filings",)))
    return out
