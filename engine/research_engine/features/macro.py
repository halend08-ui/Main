"""Macroeconomic context.

Stance, not prediction. Macro variables cannot forecast individual asset prices
with any reliability, and this module does not pretend otherwise. What it does
is adjust *probabilities* and flag sector exposures:

* inflation direction and level;
* policy stance (real policy rate vs a neutral estimate);
* the yield curve;
* growth and labour-market direction;
* credit conditions;
* the dollar and commodity backdrop.

Each reading carries the release date of its underlying observation, so a
historical run sees only what had actually been published.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from research_engine.core.numeric import clamp, mean, pct_change, safe_div
from research_engine.core.types import ClaimType, DataQuality, Evidence

#: Sector sensitivities to macro factors, as directional priors (-1..+1).
#: These are *priors*, not fitted coefficients: they shade probabilities and are
#: always visible in the output so a reader can disagree with them.
SECTOR_SENSITIVITY: dict[str, dict[str, float]] = {
    "Technology":             {"rates": -0.7, "growth": 0.6, "inflation": -0.3, "dollar": -0.2},
    "Communication Services": {"rates": -0.5, "growth": 0.5, "inflation": -0.2, "dollar": -0.2},
    "Consumer Discretionary": {"rates": -0.6, "growth": 0.8, "inflation": -0.5, "dollar": 0.0},
    "Consumer Staples":       {"rates": -0.2, "growth": 0.1, "inflation": -0.3, "dollar": -0.1},
    "Health Care":            {"rates": -0.3, "growth": 0.2, "inflation": -0.1, "dollar": -0.2},
    "Financials":             {"rates": 0.4, "growth": 0.5, "inflation": 0.1, "dollar": 0.1},
    "Industrials":            {"rates": -0.4, "growth": 0.7, "inflation": -0.2, "dollar": -0.3},
    "Energy":                 {"rates": 0.0, "growth": 0.4, "inflation": 0.6, "dollar": -0.4},
    "Materials":              {"rates": -0.2, "growth": 0.6, "inflation": 0.4, "dollar": -0.5},
    "Utilities":              {"rates": -0.8, "growth": 0.0, "inflation": -0.2, "dollar": 0.0},
    "Real Estate":            {"rates": -0.9, "growth": 0.4, "inflation": -0.1, "dollar": 0.0},
    "Crypto":                 {"rates": -0.8, "growth": 0.3, "inflation": 0.1, "dollar": -0.6},
}

NEUTRAL_REAL_RATE = 0.005     # documented assumption: ~0.5% real neutral policy rate


@dataclass
class MacroReading:
    series_id: str
    label: str
    latest: float | None
    as_of: date | None
    change_3m: float | None = None
    change_12m: float | None = None
    percentile_5y: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"series": self.series_id, "label": self.label, "value": self.latest,
                "as_of": self.as_of.isoformat() if self.as_of else None,
                "change_3m": self.change_3m, "change_12m": self.change_12m,
                "percentile_5y": self.percentile_5y}


@dataclass
class MacroState:
    as_of: date
    readings: dict[str, MacroReading] = field(default_factory=dict)
    stance: dict[str, str] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def value(self, series_id: str) -> float | None:
        reading = self.readings.get(series_id)
        return reading.latest if reading else None

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(),
                "readings": {k: v.to_dict() for k, v in self.readings.items()},
                "stance": self.stance, "missing": self.missing,
                "evidence": [e.to_dict() for e in self.evidence]}


def _reading(series_id: str, label: str,
             points: Sequence[tuple[date, float]]) -> MacroReading:
    values = [v for _, v in points]
    if not values:
        return MacroReading(series_id, label, None, None)
    latest = values[-1]
    as_of = points[-1][0]
    change_3m = pct_change(latest, values[-4]) if len(values) >= 4 else None
    change_12m = pct_change(latest, values[-13]) if len(values) >= 13 else None
    percentile = None
    window = values[-60:]
    if len(window) >= 24:
        below = sum(1 for v in window if v <= latest)
        percentile = below / len(window)
    return MacroReading(series_id, label, latest, as_of, change_3m, change_12m,
                        percentile)


def build_state(as_of: date, series: Mapping[str, Sequence[tuple[date, float]]],
                labels: Mapping[str, str] | None = None) -> MacroState:
    """Assemble a macro state from point-in-time series."""
    labels = labels or {}
    state = MacroState(as_of=as_of)
    for series_id, points in series.items():
        reading = _reading(series_id, labels.get(series_id, series_id), points)
        state.readings[series_id] = reading
        if reading.latest is None:
            state.missing.append(series_id)

    state.stance = classify_stance(state)
    state.evidence = macro_evidence(state)
    return state


def classify_stance(state: MacroState) -> dict[str, str]:
    """Plain-language stance per macro dimension, or 'unknown'."""
    stance: dict[str, str] = {}

    cpi = state.readings.get("CPIAUCSL")
    if cpi and cpi.change_12m is not None:
        rate = cpi.change_12m
        stance["inflation"] = ("high" if rate > 0.04 else
                               "above_target" if rate > 0.025 else
                               "at_target" if rate > 0.015 else "low")
    else:
        stance["inflation"] = "unknown"

    fed = state.value("FEDFUNDS")
    if fed is not None and cpi is not None and cpi.change_12m is not None:
        real_rate = fed / 100.0 - cpi.change_12m
        stance["policy"] = ("restrictive" if real_rate > NEUTRAL_REAL_RATE + 0.01 else
                            "accommodative" if real_rate < NEUTRAL_REAL_RATE - 0.01
                            else "neutral")
        stance["real_policy_rate"] = f"{real_rate:.2%}"
    else:
        stance["policy"] = "unknown"

    curve = state.value("T10Y2Y")
    if curve is not None:
        stance["yield_curve"] = ("inverted" if curve < 0 else
                                 "flat" if curve < 0.5 else "normal")
    else:
        stance["yield_curve"] = "unknown"

    unemployment = state.readings.get("UNRATE")
    if unemployment and unemployment.latest is not None:
        change = ((unemployment.latest - 0) if unemployment.change_12m is None
                  else unemployment.change_12m)
        stance["labour"] = ("weakening" if (unemployment.change_12m or 0) > 0.1 else
                            "tightening" if (unemployment.change_12m or 0) < -0.05
                            else "stable")
    else:
        stance["labour"] = "unknown"

    spread = state.value("BAMLH0A0HYM2")
    if spread is not None:
        stance["credit"] = ("stressed" if spread > 6.0 else
                            "tightening" if spread > 4.5 else "easy")
    else:
        stance["credit"] = "unknown"

    growth = state.readings.get("INDPRO")
    if growth and growth.change_12m is not None:
        stance["growth"] = ("contracting" if growth.change_12m < -0.01 else
                            "slow" if growth.change_12m < 0.015 else "expanding")
    else:
        stance["growth"] = "unknown"
    return stance


def macro_evidence(state: MacroState) -> list[Evidence]:
    out: list[Evidence] = []
    stance = state.stance
    if stance.get("yield_curve") == "inverted":
        out.append(Evidence(
            label="Inverted yield curve",
            detail="the 10y-2y spread is negative; historically associated with "
                   "recessions at variable and long lags, and it is a correlation, "
                   "not a mechanism",
            direction=-0.4, weight=0.5, claim_type=ClaimType.INTERPRETATION,
            quality=DataQuality.EXCELLENT, sources=("FRED T10Y2Y",)))
    if stance.get("credit") == "stressed":
        out.append(Evidence(
            label="Credit stress",
            detail="high-yield spreads are wide, which raises refinancing risk for "
                   "leveraged issuers",
            direction=-0.6, weight=0.6, claim_type=ClaimType.OBSERVATION,
            quality=DataQuality.EXCELLENT, sources=("FRED BAMLH0A0HYM2",)))
    if stance.get("policy") == "restrictive":
        out.append(Evidence(
            label="Restrictive policy",
            detail=f"real policy rate {stance.get('real_policy_rate', 'n/a')} is above "
                   f"a neutral estimate of {NEUTRAL_REAL_RATE:.1%}",
            direction=-0.3, weight=0.4, claim_type=ClaimType.INTERPRETATION,
            quality=DataQuality.GOOD, sources=("FRED FEDFUNDS, CPIAUCSL",)))
    elif stance.get("policy") == "accommodative":
        out.append(Evidence(
            label="Accommodative policy", detail="real policy rate is below neutral",
            direction=0.3, weight=0.4, claim_type=ClaimType.INTERPRETATION,
            quality=DataQuality.GOOD, sources=("FRED FEDFUNDS, CPIAUCSL",)))
    if stance.get("growth") == "contracting":
        out.append(Evidence(
            label="Industrial production contracting",
            detail="year-on-year industrial production is negative",
            direction=-0.4, weight=0.4, claim_type=ClaimType.OBSERVATION,
            quality=DataQuality.EXCELLENT, sources=("FRED INDPRO",)))
    unknown = [k for k, v in stance.items() if v == "unknown"]
    if unknown:
        out.append(Evidence(
            label="Incomplete macro picture",
            detail=f"no data for: {', '.join(unknown)}",
            direction=0.0, weight=0.2, claim_type=ClaimType.ASSUMPTION,
            quality=DataQuality.POOR, sources=()))
    return out


def sector_adjustment(sector: str | None, state: MacroState) -> dict[str, Any]:
    """A small probability tilt for a sector given the macro stance.

    Bounded to +/-0.15 deliberately: macro should nudge a view, never drive it.
    """
    if not sector:
        return {"adjustment": 0.0, "reasons": ["sector unknown"], "applied": False}
    sensitivity = SECTOR_SENSITIVITY.get(sector)
    if sensitivity is None:
        return {"adjustment": 0.0, "reasons": [f"no prior for sector {sector}"],
                "applied": False}

    stance = state.stance
    tilt = 0.0
    reasons: list[str] = []

    if stance.get("policy") == "restrictive":
        contribution = sensitivity.get("rates", 0.0) * -0.5
        tilt += contribution
        reasons.append(f"restrictive policy: {contribution:+.2f} tilt "
                       f"(sector rate sensitivity {sensitivity['rates']:+.1f})")
    elif stance.get("policy") == "accommodative":
        contribution = sensitivity.get("rates", 0.0) * 0.5
        tilt += contribution
        reasons.append(f"accommodative policy: {contribution:+.2f} tilt")

    if stance.get("growth") == "contracting":
        contribution = -sensitivity.get("growth", 0.0) * 0.4
        tilt += contribution
        reasons.append(f"contracting growth: {contribution:+.2f} tilt")
    elif stance.get("growth") == "expanding":
        contribution = sensitivity.get("growth", 0.0) * 0.3
        tilt += contribution
        reasons.append(f"expanding growth: {contribution:+.2f} tilt")

    if stance.get("inflation") == "high":
        contribution = sensitivity.get("inflation", 0.0) * 0.4
        tilt += contribution
        reasons.append(f"high inflation: {contribution:+.2f} tilt")

    scaled = clamp(tilt * 0.1, -0.15, 0.15)
    return {"adjustment": round(scaled, 4), "raw_tilt": round(tilt, 3),
            "reasons": reasons, "applied": True,
            "caveat": "macro priors shade probabilities; they do not predict prices"}
