"""Risk engine.

Risk is treated as a first-class output, not a footnote on a return forecast.
The engine distinguishes:

* **Volatility risk** -- how much the price moves.
* **Tail risk** -- how bad the bad days are (VaR, expected shortfall, gap risk).
* **Permanent-loss risk** -- the probability that capital does not come back
  (leverage, cash burn, distress, dilution, delisting, protocol failure).
* **Liquidity risk** -- whether a position can actually be exited.
* **Event risk** -- known dated events that can reprice the asset.

Volatility and permanent-loss risk are deliberately separate: a volatile,
well-capitalised business and a stable, over-levered one are not the same risk,
and collapsing them into one number destroys the distinction that matters most.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from research_engine.core.numeric import clamp, is_finite, mean, safe_div
from research_engine.core.series import PriceSeries
from research_engine.core.types import ClaimType, DataQuality, Evidence, RiskLevel
from research_engine.features import returns as R


@dataclass
class RiskProfile:
    symbol: str
    as_of: date
    level: RiskLevel
    volatility: float | None
    max_drawdown: float | None
    var_95: float | None
    expected_shortfall: float | None
    permanent_loss_score: float | None       # 0..1, higher = worse
    liquidity_score: float | None            # 0..1, higher = worse
    gap_risk: float | None
    beta: float | None = None
    drivers: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "as_of": self.as_of.isoformat(),
            "level": self.level.value,
            "volatility": _r(self.volatility), "max_drawdown": _r(self.max_drawdown),
            "var_95": _r(self.var_95), "expected_shortfall": _r(self.expected_shortfall),
            "permanent_loss_score": _r(self.permanent_loss_score),
            "liquidity_score": _r(self.liquidity_score), "gap_risk": _r(self.gap_risk),
            "beta": _r(self.beta), "drivers": self.drivers, "unknowns": self.unknowns,
            "detail": self.detail,
        }


def _r(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def gap_risk(series: PriceSeries, *, threshold: float = 0.10) -> float | None:
    """Share of sessions with an overnight gap beyond ``threshold``.

    Overnight gaps are the risk that a stop-loss does not protect you, which is
    why they are measured separately from ordinary volatility.
    """
    if len(series) < 60:
        return None
    opens, closes = series.open, series.close
    gaps = []
    for i in range(1, len(series)):
        if is_finite(opens[i]) and is_finite(closes[i - 1]) and closes[i - 1] > 0:
            gaps.append(abs(opens[i] / closes[i - 1] - 1.0))
    if len(gaps) < 40:
        # No usable opens (common for crypto history and some vendors):
        # fall back on close-to-close extremes, and say so via detail.
        returns = series.returns()
        finite = returns[np.isfinite(returns)]
        if finite.size < 40:
            return None
        return float(np.mean(np.abs(finite) > threshold))
    return float(np.mean(np.array(gaps) > threshold))


def permanent_loss_risk(*, interest_coverage: float | None = None,
                        net_debt_to_ebitda: float | None = None,
                        cash_runway_years: float | None = None,
                        altman_z: float | None = None,
                        dilution_rate: float | None = None,
                        fcf_positive: bool | None = None,
                        market_cap: float | None = None,
                        crypto_risk: float | None = None,
                        quality_grade: str | None = None) -> dict[str, Any]:
    """Probability-like score (0..1) that capital is permanently impaired.

    This is the number that should stop a "cheap" stock from being bought. It is
    computed only from balance-sheet and structural inputs -- never from price
    action, which reflects sentiment as much as solvency.
    """
    signals: list[tuple[float, float, str]] = []     # (score, weight, reason)

    if interest_coverage is not None:
        if interest_coverage < 1.0:
            signals.append((0.9, 1.5, f"operating income does not cover interest "
                                      f"({interest_coverage:.1f}x)"))
        elif interest_coverage < 2.5:
            signals.append((0.6, 1.2, f"thin interest coverage ({interest_coverage:.1f}x)"))
        elif interest_coverage < 6:
            signals.append((0.3, 0.8, "moderate interest coverage"))
        else:
            signals.append((0.08, 0.8, "comfortable interest coverage"))

    if net_debt_to_ebitda is not None:
        if net_debt_to_ebitda > 5:
            signals.append((0.8, 1.3, f"net debt is {net_debt_to_ebitda:.1f}x EBITDA"))
        elif net_debt_to_ebitda > 3:
            signals.append((0.5, 1.0, f"net debt is {net_debt_to_ebitda:.1f}x EBITDA"))
        elif net_debt_to_ebitda < 0:
            signals.append((0.05, 0.9, "net cash position"))
        else:
            signals.append((0.2, 0.8, "manageable leverage"))

    if cash_runway_years is not None:
        if cash_runway_years < 1:
            signals.append((0.95, 2.0, f"cash runway of only {cash_runway_years:.1f} "
                                       f"years at the current burn rate"))
        elif cash_runway_years < 2:
            signals.append((0.75, 1.5, f"{cash_runway_years:.1f} years of runway; "
                                       f"financing likely required"))
        else:
            signals.append((0.4, 1.0, f"{cash_runway_years:.1f} years of runway"))

    if altman_z is not None:
        if altman_z < 1.8:
            signals.append((0.85, 1.4, f"Altman Z of {altman_z:.2f} is in the distress zone"))
        elif altman_z < 3.0:
            signals.append((0.4, 0.9, f"Altman Z of {altman_z:.2f} is in the grey zone"))
        else:
            signals.append((0.1, 0.9, f"Altman Z of {altman_z:.2f} is in the safe zone"))

    if dilution_rate is not None and dilution_rate > 0.10:
        signals.append((0.6, 0.8, f"share count growing {dilution_rate:.0%} per year"))

    if fcf_positive is False:
        signals.append((0.55, 0.9, "negative free cash flow"))

    if market_cap is not None and market_cap < 300e6:
        signals.append((0.5, 0.7, "micro-cap: elevated failure and delisting base rate"))

    if crypto_risk is not None:
        signals.append((clamp(crypto_risk, 0.0, 1.0), 1.6,
                        "crypto structural risk factors"))
    if quality_grade == "speculative":
        signals.append((0.7, 1.2, "speculative-tier asset"))

    if not signals:
        return {"score": None, "reasons": [],
                "note": "no solvency inputs available; permanent-loss risk unknown"}
    total_weight = sum(w for _, w, _ in signals)
    score = sum(s * w for s, w, _ in signals) / total_weight
    reasons = [reason for score_i, _, reason
               in sorted(signals, key=lambda sig: -sig[0]) if score_i >= 0.5]
    return {"score": clamp(score, 0.0, 1.0), "reasons": reasons,
            "inputs_used": len(signals)}


def liquidity_risk(*, avg_dollar_volume: float | None,
                   position_size_usd: float | None = None,
                   participation_cap: float = 0.05,
                   turnover: float | None = None,
                   exchange_count: int | None = None) -> dict[str, Any]:
    """How hard it would be to exit, and how many days that would take."""
    if avg_dollar_volume is None or avg_dollar_volume <= 0:
        return {"score": None, "days_to_exit": None,
                "note": "no volume data: liquidity risk unknown"}
    score_parts: list[float] = []
    reasons: list[str] = []

    if avg_dollar_volume < 1e5:
        score_parts.append(0.95)
        reasons.append(f"average daily value traded is only ${avg_dollar_volume:,.0f}")
    elif avg_dollar_volume < 1e6:
        score_parts.append(0.7)
        reasons.append("thinly traded (under $1m per day)")
    elif avg_dollar_volume < 1e7:
        score_parts.append(0.35)
    else:
        score_parts.append(0.1)

    days_to_exit = None
    if position_size_usd:
        days_to_exit = position_size_usd / (avg_dollar_volume * participation_cap)
        if days_to_exit > 5:
            score_parts.append(0.9)
            reasons.append(f"exiting would take about {days_to_exit:.1f} trading days "
                           f"at {participation_cap:.0%} of volume")
        elif days_to_exit > 1:
            score_parts.append(0.5)

    if turnover is not None and turnover < 0.005:
        score_parts.append(0.7)
        reasons.append(f"turnover of {turnover:.2%} of market cap per day")
    if exchange_count is not None and exchange_count < 3:
        score_parts.append(0.6)
        reasons.append(f"only {exchange_count} venues quote this asset")

    return {"score": clamp(float(mean(score_parts) or 0.0), 0.0, 1.0),
            "days_to_exit": days_to_exit, "reasons": reasons}


def classify_risk_level(*, volatility: float | None, max_drawdown: float | None,
                        permanent_loss: float | None, liquidity: float | None,
                        is_crypto: bool = False) -> tuple[RiskLevel, list[str]]:
    """Map risk components onto the reported level.

    Permanent-loss risk dominates: an asset that can go to zero is high risk
    regardless of how calm its chart looks.
    """
    drivers: list[str] = []
    level = RiskLevel.LOW

    def raise_to(target: RiskLevel, reason: str) -> None:
        nonlocal level
        if target > level:
            level = target
        drivers.append(reason)

    if permanent_loss is not None:
        if permanent_loss > 0.7:
            raise_to(RiskLevel.EXTREME, "high probability of permanent capital loss")
        elif permanent_loss > 0.5:
            raise_to(RiskLevel.HIGH, "elevated probability of permanent capital loss")
        elif permanent_loss > 0.3:
            raise_to(RiskLevel.ELEVATED, "moderate solvency/structural risk")
    else:
        drivers.append("permanent-loss risk could not be assessed")
        raise_to(RiskLevel.ELEVATED, "solvency inputs unavailable")

    vol_thresholds = (1.2, 0.8, 0.5) if is_crypto else (0.6, 0.4, 0.25)
    if volatility is not None:
        if volatility > vol_thresholds[0]:
            raise_to(RiskLevel.EXTREME, f"annualised volatility of {volatility:.0%}")
        elif volatility > vol_thresholds[1]:
            raise_to(RiskLevel.HIGH, f"annualised volatility of {volatility:.0%}")
        elif volatility > vol_thresholds[2]:
            raise_to(RiskLevel.ELEVATED, f"annualised volatility of {volatility:.0%}")

    if max_drawdown is not None:
        if max_drawdown < -0.7:
            raise_to(RiskLevel.HIGH, f"has fallen {abs(max_drawdown):.0%} from a peak before")
        elif max_drawdown < -0.45:
            raise_to(RiskLevel.ELEVATED, f"prior drawdown of {abs(max_drawdown):.0%}")

    if liquidity is not None and liquidity > 0.6:
        raise_to(RiskLevel.HIGH, "position could be difficult to exit")

    if level is RiskLevel.LOW and volatility is None:
        level = RiskLevel.MODERATE
        drivers.append("insufficient history to measure volatility")
    return level, drivers


def build_profile(symbol: str, as_of: date, series: PriceSeries | None, *,
                  benchmark: PriceSeries | None = None,
                  solvency: Mapping[str, Any] | None = None,
                  liquidity_inputs: Mapping[str, Any] | None = None,
                  is_crypto: bool = False,
                  risk_free_rate: float = 0.0) -> RiskProfile:
    """Assemble the full risk picture for one asset."""
    unknowns: list[str] = []
    detail: dict[str, Any] = {}

    volatility = max_dd = var95 = es = beta = gap = None
    if series is not None and len(series) >= 60:
        stats = R.summarize(series, risk_free_rate=risk_free_rate, benchmark=benchmark)
        volatility = stats["volatility"]
        max_dd = stats["max_drawdown"]
        var95 = stats["var_95"]
        es = stats["expected_shortfall_975"]
        beta = stats.get("beta")
        gap = gap_risk(series)
        detail["return_stats"] = {k: _r(v) for k, v in stats.items()
                                  if isinstance(v, (int, float)) or v is None}
        if stats["excess_kurtosis"] is not None and stats["excess_kurtosis"] > 3:
            detail["fat_tails"] = True
    else:
        unknowns.append("price history too short for risk statistics")

    solvency_result = permanent_loss_risk(**dict(solvency or {}))
    permanent = solvency_result.get("score")
    if permanent is None:
        unknowns.append(solvency_result.get("note", "permanent-loss risk unknown"))
    detail["permanent_loss"] = solvency_result

    liquidity_result = liquidity_risk(**dict(liquidity_inputs or {}))
    liquidity = liquidity_result.get("score")
    if liquidity is None:
        unknowns.append(liquidity_result.get("note", "liquidity risk unknown"))
    detail["liquidity"] = liquidity_result

    level, drivers = classify_risk_level(
        volatility=volatility, max_drawdown=max_dd, permanent_loss=permanent,
        liquidity=liquidity, is_crypto=is_crypto)
    drivers.extend(solvency_result.get("reasons", [])[:3])
    drivers.extend(liquidity_result.get("reasons", [])[:2])

    return RiskProfile(symbol=symbol, as_of=as_of, level=level, volatility=volatility,
                       max_drawdown=max_dd, var_95=var95, expected_shortfall=es,
                       permanent_loss_score=permanent, liquidity_score=liquidity,
                       gap_risk=gap, beta=beta, drivers=drivers, unknowns=unknowns,
                       detail=detail)


def risk_evidence(profile: RiskProfile) -> list[Evidence]:
    out: list[Evidence] = []
    if profile.permanent_loss_score is not None and profile.permanent_loss_score > 0.5:
        out.append(Evidence(
            label="Permanent loss risk",
            detail="; ".join(profile.detail["permanent_loss"].get("reasons", []))
                   or "structural risk indicators are elevated",
            direction=-0.9, weight=1.0, claim_type=ClaimType.MODEL_PREDICTION,
            quality=DataQuality.FAIR, sources=("company filings",)))
    if profile.max_drawdown is not None and profile.max_drawdown < -0.5:
        out.append(Evidence(
            label="Historical drawdown",
            detail=f"has previously declined {abs(profile.max_drawdown):.0%} peak to trough",
            direction=-0.4, weight=0.6, claim_type=ClaimType.OBSERVATION,
            quality=DataQuality.GOOD, sources=("price history",)))
    if profile.liquidity_score is not None and profile.liquidity_score > 0.6:
        out.append(Evidence(
            label="Liquidity risk",
            detail="; ".join(profile.detail["liquidity"].get("reasons", []))
                   or "position may be hard to exit",
            direction=-0.6, weight=0.7, claim_type=ClaimType.OBSERVATION,
            quality=DataQuality.GOOD, sources=("market data",)))
    for unknown in profile.unknowns:
        out.append(Evidence(
            label="Unmeasured risk", detail=unknown, direction=-0.2, weight=0.3,
            claim_type=ClaimType.ASSUMPTION, quality=DataQuality.POOR, sources=()))
    return out


# ------------------------------------------------------ portfolio level ----
def correlation_matrix(series_by_symbol: Mapping[str, PriceSeries], *,
                       min_overlap: int = 60) -> dict[str, dict[str, float | None]]:
    """Pairwise correlations on aligned returns, ``None`` where overlap is thin."""
    from research_engine.core.series import aligned_returns
    symbols = list(series_by_symbol)
    out: dict[str, dict[str, float | None]] = {s: {} for s in symbols}
    for i, a in enumerate(symbols):
        for b in symbols[i:]:
            if a == b:
                out[a][b] = 1.0
                continue
            ra, rb = aligned_returns(series_by_symbol[a], series_by_symbol[b])
            value = None
            if ra.size >= min_overlap and ra.std() > 0 and rb.std() > 0:
                value = float(np.corrcoef(ra, rb)[0, 1])
            out[a][b] = value
            out[b][a] = value
    return out


def concentration(weights: Mapping[str, float]) -> dict[str, Any]:
    """Herfindahl index and largest-position share."""
    values = [w for w in weights.values() if is_finite(w) and w > 0]
    if not values:
        return {"hhi": None, "largest": None, "effective_positions": None}
    total = sum(values)
    shares = [w / total for w in values]
    hhi = sum(s * s for s in shares)
    return {"hhi": round(hhi, 4), "largest": round(max(shares), 4),
            "effective_positions": round(1.0 / hhi, 2) if hhi else None}


def portfolio_risk(weights: Mapping[str, float],
                   series_by_symbol: Mapping[str, PriceSeries], *,
                   periods_per_year: int = 252) -> dict[str, Any]:
    """Portfolio volatility from the covariance of aligned returns.

    Reports how much of the portfolio could actually be measured: computing a
    portfolio volatility from 40% of the holdings and presenting it as the whole
    is a quiet lie.
    """
    usable = {s: w for s, w in weights.items()
              if s in series_by_symbol and len(series_by_symbol[s]) > 60}
    if not usable:
        return {"volatility": None, "coverage": 0.0,
                "note": "no holdings had enough history"}

    symbols = list(usable)
    # Align all series on their common dates.
    common: set = set(series_by_symbol[symbols[0]].dates)
    for s in symbols[1:]:
        common &= set(series_by_symbol[s].dates)
    ordered = sorted(common)
    if len(ordered) < 60:
        return {"volatility": None, "coverage": 0.0,
                "note": f"only {len(ordered)} overlapping sessions across holdings"}

    matrix = []
    for s in symbols:
        series = series_by_symbol[s]
        index = {d: i for i, d in enumerate(series.dates)}
        prices = np.array([series.adj_close[index[d]] for d in ordered])
        matrix.append(prices[1:] / prices[:-1] - 1.0)
    returns = np.vstack(matrix)
    cov = np.cov(returns) * periods_per_year
    total_weight = sum(usable.values())
    w = np.array([usable[s] / total_weight for s in symbols])
    variance = float(w @ np.atleast_2d(cov) @ w)
    portfolio_returns = w @ returns

    return {
        "volatility": round(float(np.sqrt(max(variance, 0.0))), 4),
        "coverage": round(total_weight / sum(weights.values()), 3) if weights else 0.0,
        "holdings_measured": len(symbols),
        "overlapping_sessions": len(ordered),
        "var_95": _r(R.value_at_risk(portfolio_returns, 0.95)),
        "expected_shortfall_975": _r(R.expected_shortfall(portfolio_returns, 0.975)),
        "diversification_ratio": _r(_diversification_ratio(w, cov)),
        **concentration(weights),
    }


def _diversification_ratio(weights: np.ndarray, cov: np.ndarray) -> float | None:
    """Weighted average volatility divided by portfolio volatility (>= 1)."""
    variances = np.diag(np.atleast_2d(cov))
    if np.any(variances < 0):
        return None
    weighted_avg = float(weights @ np.sqrt(variances))
    portfolio_vol = float(np.sqrt(max(weights @ np.atleast_2d(cov) @ weights, 1e-12)))
    return weighted_avg / portfolio_vol if portfolio_vol > 0 else None


def limit_breaches(weights: Mapping[str, float],
                   sectors: Mapping[str, str] | None = None,
                   asset_classes: Mapping[str, str] | None = None, *,
                   max_position: float = 0.12, max_sector: float = 0.30,
                   max_crypto: float = 0.20,
                   concentration_warning_hhi: float = 0.18) -> list[str]:
    """Explicit, quotable limit violations for the portfolio report."""
    breaches: list[str] = []
    total = sum(weights.values()) or 1.0
    for symbol, weight in weights.items():
        share = weight / total
        if share > max_position:
            breaches.append(f"{symbol} is {share:.1%} of the portfolio "
                            f"(limit {max_position:.0%})")
    if sectors:
        by_sector: dict[str, float] = {}
        for symbol, weight in weights.items():
            sector = sectors.get(symbol, "Unknown")
            by_sector[sector] = by_sector.get(sector, 0.0) + weight / total
        for sector, share in by_sector.items():
            if share > max_sector:
                breaches.append(f"{sector} exposure is {share:.1%} "
                                f"(limit {max_sector:.0%})")
    if asset_classes:
        crypto_share = sum(w / total for s, w in weights.items()
                           if asset_classes.get(s) == "crypto")
        if crypto_share > max_crypto:
            breaches.append(f"crypto exposure is {crypto_share:.1%} "
                            f"(limit {max_crypto:.0%})")
    stats = concentration(weights)
    if stats["hhi"] is not None and stats["hhi"] > concentration_warning_hhi:
        breaches.append(
            f"portfolio is concentrated (HHI {stats['hhi']:.2f}, equivalent to about "
            f"{stats['effective_positions']:.0f} equally weighted positions)")
    return breaches
