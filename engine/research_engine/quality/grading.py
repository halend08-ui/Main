"""Turn quality issues into a grade that gates downstream confidence.

The grade is not cosmetic. Elsewhere in the system:

* a BUY requires at least the configured minimum grade;
* confidence is multiplied by a quality factor;
* FATAL issues remove the asset from the scan entirely.

Scoring model: start at 1.0 and subtract a penalty per issue, weighted by
severity and capped per code so that ten instances of one warning cannot sink a
series the way one genuine error should.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from research_engine.core.types import DataQuality
from research_engine.quality.checks import Issue, QualityReport, Severity

#: Base penalty per severity level.
_PENALTY = {
    Severity.INFO: 0.01,
    Severity.WARNING: 0.06,
    Severity.ERROR: 0.20,
    Severity.FATAL: 1.00,
}

#: Issues whose importance differs from their severity default.
_CODE_WEIGHT: Mapping[str, float] = {
    "price.stale": 1.5,
    "price.suspected_unadjusted_split": 1.6,
    "price.flatline": 1.5,
    "price.short_history": 1.2,
    "fundamentals.missing_core": 1.4,
    "fundamentals.balance_sheet_identity": 1.3,
    "news.duplicated": 0.5,
    "news.thin": 0.5,
}

#: A single code can never cost more than this, however many times it fires.
_MAX_PENALTY_PER_CODE = 0.35


def score_issues(issues: Iterable[Issue]) -> float:
    """Return a 0..1 quality score."""
    per_code: dict[str, float] = {}
    for issue in issues:
        if issue.severity >= Severity.FATAL:
            return 0.0
        penalty = _PENALTY[issue.severity] * _CODE_WEIGHT.get(issue.code, 1.0)
        per_code[issue.code] = min(_MAX_PENALTY_PER_CODE,
                                   per_code.get(issue.code, 0.0) + penalty)
    return max(0.0, 1.0 - sum(per_code.values()))


def grade_from_issues(issues: Iterable[Issue], *,
                      observations: int | None = None,
                      min_observations: int = 60) -> tuple[float, DataQuality]:
    """Score and grade. Thin data is capped at FAIR however clean it looks."""
    issue_list = list(issues)
    score = score_issues(issue_list)
    grade = DataQuality.from_score(score)
    if observations is not None and observations < min_observations:
        # Clean-but-tiny samples are the classic overconfidence trap.
        grade = min(grade, DataQuality.FAIR, key=lambda g: g.score_floor)
        score = min(score, DataQuality.FAIR.score_floor + 0.05)
    return score, grade


def finalize(report: QualityReport, *, min_observations: int = 60) -> QualityReport:
    """Populate ``score``/``grade`` on a report in place and return it."""
    report.score, report.grade = grade_from_issues(
        report.issues, observations=report.observations,
        min_observations=min_observations)
    return report


def combine(reports: Sequence[QualityReport],
            weights: Mapping[str, float] | None = None) -> tuple[float, DataQuality]:
    """Blend several scopes (prices, fundamentals, news) into one grade.

    The blend is weighted, but a FATAL anywhere is contagious: if prices are
    unusable, nothing built on them is trustworthy either.
    """
    if not reports:
        return 0.0, DataQuality.INSUFFICIENT
    default_weights = {"prices": 0.45, "fundamentals": 0.35, "news": 0.10,
                       "crypto": 0.35, "macro": 0.10}
    w = dict(default_weights)
    w.update(weights or {})

    if any(not r.usable for r in reports):
        return 0.0, DataQuality.INSUFFICIENT

    total_weight = 0.0
    weighted = 0.0
    for report in reports:
        weight = w.get(report.scope, 0.1)
        weighted += report.score * weight
        total_weight += weight
    score = weighted / total_weight if total_weight else 0.0
    return score, DataQuality.from_score(score)


def confidence_multiplier(grade: DataQuality) -> float:
    """How much a given data grade is allowed to support confidence.

    Rationale: with poor data the model may still have a view, but it must not
    be allowed to express high confidence in it.
    """
    return {
        DataQuality.EXCELLENT: 1.00,
        DataQuality.GOOD: 0.90,
        DataQuality.FAIR: 0.70,
        DataQuality.POOR: 0.45,
        DataQuality.INSUFFICIENT: 0.0,
    }[grade]
