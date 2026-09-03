"""Data-quality checks.

Each check returns :class:`Issue` objects rather than raising, because the right
response to bad data depends on the caller: a scanner may skip the asset, a
research report may footnote it, and a backtest must refuse it outright.

The checks are deliberately explicit about *what kind* of problem was found:
"stale" is not the same as "missing", and neither is the same as "impossible".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from research_engine.core.logging import get_logger
from research_engine.core.numeric import median as med
from research_engine.core.series import PriceSeries
from research_engine.core.timeutil import TradingCalendar, to_date
from research_engine.core.types import DataQuality

log = get_logger(__name__)


class Severity(enum.IntEnum):
    INFO = 10
    WARNING = 20
    ERROR = 30
    FATAL = 40      # data must not be used at all


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    severity: Severity
    message: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity.name,
                "message": self.message, "detail": dict(self.detail)}


@dataclass
class QualityReport:
    scope: str
    subject: str
    as_of: date
    issues: list[Issue] = field(default_factory=list)
    observations: int = 0
    coverage: float | None = None
    score: float = 1.0
    grade: DataQuality = DataQuality.EXCELLENT

    def add(self, code: str, severity: Severity, message: str, **detail: Any) -> None:
        self.issues.append(Issue(code, severity, message, detail))

    @property
    def usable(self) -> bool:
        return not any(i.severity >= Severity.FATAL for i in self.issues)

    @property
    def worst(self) -> Severity:
        return max((i.severity for i in self.issues), default=Severity.INFO)

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def to_dict(self) -> dict[str, Any]:
        return {"scope": self.scope, "subject": self.subject,
                "as_of": self.as_of.isoformat(), "observations": self.observations,
                "coverage": self.coverage, "score": round(self.score, 4),
                "grade": self.grade.value, "usable": self.usable,
                "issues": [i.to_dict() for i in self.issues]}


# ------------------------------------------------------------- prices ------
def check_price_series(series: PriceSeries, *, as_of: date | None = None,
                       calendar: TradingCalendar | None = None,
                       max_staleness_days: int = 5,
                       max_daily_move_abs: float = 0.6,
                       min_history_days: int = 120,
                       min_coverage_ratio: float = 0.9,
                       is_crypto: bool = False) -> QualityReport:
    """Validate an OHLCV series.

    Detects: impossible prices, OHLC inconsistency, duplicate/unsorted dates,
    calendar gaps, stale quotes, zero-volume runs, suspected unadjusted splits,
    and extreme moves that need human eyes before they drive a recommendation.
    """
    ref = as_of or series.end
    report = QualityReport(scope="prices", subject=series.symbol, as_of=ref,
                           observations=len(series))

    close = series.adj_close
    raw_close = series.close

    # -- impossible values -------------------------------------------------
    nonpositive = int(np.sum(~(raw_close > 0)))
    if nonpositive:
        report.add("price.nonpositive", Severity.FATAL,
                   f"{nonpositive} non-positive or missing closes",
                   count=nonpositive)

    for name, arr in (("open", series.open), ("high", series.high), ("low", series.low)):
        neg = int(np.sum(arr < 0))
        if neg:
            report.add(f"price.negative_{name}", Severity.ERROR,
                       f"{neg} negative {name} values", count=neg)

    with np.errstate(invalid="ignore"):
        bad_hl = np.isfinite(series.high) & np.isfinite(series.low) & (series.high < series.low)
        inconsistent = int(np.sum(bad_hl))
        if inconsistent:
            report.add("price.ohlc_inconsistent", Severity.ERROR,
                       f"{inconsistent} bars where high < low", count=inconsistent)

        for name, arr in (("open", series.open), ("close", raw_close)):
            outside = np.isfinite(arr) & np.isfinite(series.high) & np.isfinite(series.low) & (
                (arr > series.high * 1.0001) | (arr < series.low * 0.9999))
            n_outside = int(np.sum(outside))
            if n_outside:
                report.add(f"price.{name}_outside_range", Severity.WARNING,
                           f"{n_outside} bars where {name} sits outside high/low",
                           count=n_outside)

    # -- volume ------------------------------------------------------------
    if np.all(~np.isfinite(series.volume)):
        report.add("price.volume_missing", Severity.WARNING,
                   "no volume data: liquidity analysis unavailable")
    else:
        neg_vol = int(np.sum(series.volume < 0))
        if neg_vol:
            report.add("price.negative_volume", Severity.ERROR,
                       f"{neg_vol} negative volume values", count=neg_vol)
        recent = series.volume[-20:]
        zero_days = int(np.sum(recent == 0))
        if zero_days >= 5:
            report.add("price.zero_volume_run", Severity.WARNING,
                       f"{zero_days} of the last 20 sessions had zero volume",
                       count=zero_days)

    # -- history depth -----------------------------------------------------
    span_days = (series.end - series.start).days
    if span_days < min_history_days:
        report.add("price.short_history", Severity.ERROR,
                   f"only {span_days} days of history (need {min_history_days})",
                   days=span_days, required=min_history_days)

    # -- staleness ---------------------------------------------------------
    staleness = (ref - series.end).days
    if staleness > max_staleness_days:
        report.add("price.stale", Severity.ERROR,
                   f"last price is {staleness} days old", days=staleness)
    elif staleness > max(1, max_staleness_days // 2):
        report.add("price.slightly_stale", Severity.WARNING,
                   f"last price is {staleness} days old", days=staleness)

    # -- calendar coverage -------------------------------------------------
    #
    # Coverage must be judged at the series' OWN sampling frequency. Vendors
    # legitimately supply weekly and monthly bars, and measuring a monthly
    # series against a daily session calendar reports "5% coverage" for data
    # that is in fact complete.
    periods_per_year = series.periods_per_year
    is_daily = periods_per_year >= 250
    if len(series) > 5:
        if is_daily and calendar is not None:
            expected = calendar.count_sessions(series.start, series.end)
        else:
            span_years = max((series.end - series.start).days / 365.25, 1e-9)
            expected = int(round(span_years * periods_per_year))
        if expected > 0:
            coverage = len(series) / expected
            report.coverage = round(min(coverage, 1.0), 4)
            if coverage < min_coverage_ratio:
                report.add("price.gaps", Severity.WARNING,
                           f"only {coverage:.0%} of expected observations present "
                           f"at this series' {_frequency_label(periods_per_year)} "
                           f"frequency",
                           observed=len(series), expected=expected,
                           periods_per_year=periods_per_year)
            if coverage > 1.05:
                report.add("price.unexpected_sessions", Severity.WARNING,
                           "more bars than the calendar has sessions "
                           "(duplicate dates or wrong calendar)",
                           observed=len(series), expected=expected)

    if not is_daily:
        report.add("price.low_frequency", Severity.INFO,
                   f"{_frequency_label(periods_per_year)} data: technical "
                   f"indicators, drawdown depth and volatility estimates are "
                   f"coarser than the daily equivalents they are named after",
                   periods_per_year=periods_per_year)

    # -- gaps within the series -------------------------------------------
    # The gap threshold scales with the sampling interval: a 30-day step is a
    # gap in a daily series and the normal spacing in a monthly one.
    expected_step = 365.25 / max(periods_per_year, 1)
    gap_threshold = max(3 if is_crypto else 6, int(expected_step * 2.5))
    gaps = [(series.dates[i - 1], series.dates[i])
            for i in range(1, len(series))
            if (series.dates[i] - series.dates[i - 1]).days > gap_threshold]
    if gaps:
        report.add("price.date_gaps", Severity.WARNING,
                   f"{len(gaps)} gaps longer than {gap_threshold} days in a "
                   f"{_frequency_label(periods_per_year)} series",
                   count=len(gaps),
                   largest=max((b - a).days for a, b in gaps),
                   example=f"{gaps[0][0]} -> {gaps[0][1]}")

    # -- extreme moves / split artefacts ----------------------------------
    returns = series.returns()
    if returns.size:
        finite = returns[np.isfinite(returns)]
        extreme_idx = np.where(np.abs(finite) > max_daily_move_abs)[0]
        if extreme_idx.size:
            worst = float(np.max(np.abs(finite)))
            report.add("price.extreme_move", Severity.WARNING,
                       f"{extreme_idx.size} daily moves beyond "
                       f"{max_daily_move_abs:.0%} (largest {worst:.0%})",
                       count=int(extreme_idx.size), largest=round(worst, 4))
        # A near-exact ratio move (2:1, 3:1, 1:10 ...) that reverses nothing is
        # the signature of an unadjusted corporate action.
        suspected = _suspected_split_artifacts(series)
        if suspected:
            report.add("price.suspected_unadjusted_split", Severity.ERROR,
                       f"{len(suspected)} moves look like unadjusted splits",
                       dates=[d.isoformat() for d in suspected[:5]])

    # -- flatlines ---------------------------------------------------------
    if len(close) >= 10:
        tail = close[-10:]
        if np.all(np.isfinite(tail)) and float(np.std(tail)) == 0.0:
            report.add("price.flatline", Severity.ERROR,
                       "last 10 closes are identical: feed likely frozen")

    return report


def _frequency_label(periods_per_year: int) -> str:
    if periods_per_year >= 300:
        return "continuous daily"
    if periods_per_year >= 250:
        return "daily"
    if periods_per_year >= 40:
        return "weekly"
    if periods_per_year >= 10:
        return "monthly"
    return "quarterly or coarser"


_SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 20.0)


def _suspected_split_artifacts(series: PriceSeries, tolerance: float = 0.02) -> list[date]:
    """Find single-day jumps at (near) a whole-number split ratio."""
    out: list[date] = []
    px = series.adj_close
    for i in range(1, len(px)):
        prev, cur = px[i - 1], px[i]
        if not (np.isfinite(prev) and np.isfinite(cur)) or prev <= 0:
            continue
        ratio = cur / prev
        for r in _SPLIT_RATIOS:
            if abs(ratio - r) / r < tolerance or abs(ratio - 1.0 / r) * r < tolerance:
                out.append(series.dates[i])
                break
    return out


# --------------------------------------------------------- fundamentals ----
_NON_NEGATIVE_METRICS = {
    "revenue", "total_assets", "current_assets", "cash_and_equivalents",
    "inventory", "shares_diluted", "shares_outstanding", "gross_profit",
    "total_liabilities", "current_liabilities", "goodwill",
}
_IDENTITY_TOLERANCE = 0.02      # 2% -- rounding and minority interests differ


def check_fundamentals(points_by_metric: Mapping[str, Sequence[Any]], *,
                       subject: str, as_of: date,
                       max_staleness_days: int = 200) -> QualityReport:
    """Validate a company's fundamental record.

    ``points_by_metric`` maps metric name -> ordered ``FundamentalPoint`` list
    (oldest first), exactly as the repository returns it.
    """
    report = QualityReport(scope="fundamentals", subject=subject, as_of=as_of)
    total_points = sum(len(v) for v in points_by_metric.values())
    report.observations = total_points

    if not points_by_metric or total_points == 0:
        report.add("fundamentals.absent", Severity.FATAL,
                   "no fundamental data available")
        return report

    # -- coverage of the metrics analysis actually needs -------------------
    required = ("revenue", "net_income", "total_assets", "total_equity",
                "operating_cash_flow")
    missing = [m for m in required if not points_by_metric.get(m)]
    if missing:
        severity = Severity.FATAL if len(missing) >= 4 else Severity.ERROR
        report.add("fundamentals.missing_core", severity,
                   f"missing core metrics: {', '.join(missing)}", metrics=missing)

    # -- freshness ---------------------------------------------------------
    latest_end = max((p[-1].period_end for p in points_by_metric.values() if p),
                     default=None)
    if latest_end is not None:
        age = (as_of - latest_end).days
        if age > max_staleness_days:
            report.add("fundamentals.stale", Severity.ERROR,
                       f"latest reported period ended {age} days ago", days=age)
        elif age > max_staleness_days * 0.7:
            report.add("fundamentals.aging", Severity.WARNING,
                       f"latest reported period ended {age} days ago", days=age)

    # -- sign sanity -------------------------------------------------------
    for metric, points in points_by_metric.items():
        if metric not in _NON_NEGATIVE_METRICS:
            continue
        negatives = [p for p in points if p.value is not None and p.value < 0]
        if negatives:
            report.add(f"fundamentals.negative_{metric}", Severity.ERROR,
                       f"{len(negatives)} negative values for {metric}, "
                       f"which cannot be negative",
                       count=len(negatives),
                       example=str(negatives[-1].period_end))

    # -- accounting identities --------------------------------------------
    assets = _latest_value(points_by_metric, "total_assets")
    liabilities = _latest_value(points_by_metric, "total_liabilities")
    equity = _latest_value(points_by_metric, "total_equity")
    if None not in (assets, liabilities, equity) and assets:
        implied = liabilities + equity
        drift = abs(implied - assets) / abs(assets)
        if drift > _IDENTITY_TOLERANCE:
            report.add("fundamentals.balance_sheet_identity", Severity.WARNING,
                       f"assets != liabilities + equity (off by {drift:.1%})",
                       drift=round(drift, 4))

    revenue = _latest_value(points_by_metric, "revenue")
    gross = _latest_value(points_by_metric, "gross_profit")
    if revenue and gross is not None and gross > revenue * 1.01:
        report.add("fundamentals.gross_exceeds_revenue", Severity.ERROR,
                   "gross profit exceeds revenue")

    # -- restatement churn -------------------------------------------------
    for metric, points in points_by_metric.items():
        forms = {getattr(p, "form", None) for p in points}
        if any(f and f.endswith("/A") for f in forms):
            report.add("fundamentals.restated", Severity.INFO,
                       f"{metric} includes amended filings", metric=metric)

    # -- period continuity -------------------------------------------------
    for metric in ("revenue", "net_income"):
        points = points_by_metric.get(metric) or []
        annual = [p for p in points if getattr(p.period, "value", p.period) == "annual"]
        if len(annual) >= 3:
            years = [p.period_end.year for p in annual]
            expected = set(range(min(years), max(years) + 1))
            gaps = sorted(expected - set(years))
            if gaps:
                report.add(f"fundamentals.gap_{metric}", Severity.WARNING,
                           f"missing fiscal years for {metric}: "
                           f"{', '.join(str(g) for g in gaps[:5])}",
                           years=gaps[:5])
    return report


def _latest_value(points_by_metric: Mapping[str, Sequence[Any]],
                  metric: str) -> float | None:
    points = points_by_metric.get(metric) or []
    for point in reversed(points):
        if point.value is not None:
            return float(point.value)
    return None


# ----------------------------------------------------------------- news ----
def check_news(items: Sequence[Any], *, subject: str, as_of: date,
               min_items: int = 3) -> QualityReport:
    """Assess whether a news set is usable evidence or just noise."""
    report = QualityReport(scope="news", subject=subject, as_of=as_of,
                           observations=len(items))
    if not items:
        report.add("news.absent", Severity.WARNING,
                   "no news coverage found: event analysis unavailable")
        return report
    if len(items) < min_items:
        report.add("news.thin", Severity.INFO,
                   f"only {len(items)} items: sentiment will be low confidence")

    headlines = [str(getattr(i, "headline", "")).strip().lower() for i in items]
    unique = len(set(headlines))
    if unique < len(headlines) * 0.6:
        report.add("news.duplicated", Severity.WARNING,
                   f"{len(headlines) - unique} near-duplicate headlines "
                   f"(syndication inflates apparent coverage)",
                   duplicates=len(headlines) - unique)

    tiers = [getattr(i, "source_tier", None) for i in items]
    low_quality = sum(1 for t in tiers if t is not None and t.weight <= 0.35)
    if low_quality > len(items) * 0.5:
        report.add("news.low_source_quality", Severity.WARNING,
                   f"{low_quality}/{len(items)} items come from low-tier sources")

    latest = max((getattr(i, "published_at", None) for i in items), default=None)
    if latest is not None:
        age = (as_of - latest.date()).days
        if age > 30:
            report.add("news.stale", Severity.WARNING,
                       f"most recent item is {age} days old", days=age)
    return report
