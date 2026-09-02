"""Bias and leakage detectors.

These are guardrails, not statistics. Each one answers a yes/no question that
has burned quantitative researchers repeatedly:

* **Look-ahead** -- did this feature row use information published after its
  own timestamp?
* **Survivorship** -- does this universe silently exclude assets that died?
* **Leakage** -- do the training and evaluation windows overlap or touch?
* **Overfitting pressure** -- how many parameters were fitted per observation,
  and how many configurations were tried?

They are called by the backtest engine and the learning loop, and they raise
rather than warn when the violation would invalidate a result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.errors import LookAheadError
from research_engine.core.logging import get_logger
from research_engine.core.timeutil import to_date, to_datetime

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BiasFinding:
    kind: str
    detail: str
    severe: bool = True

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"[{self.kind}] {self.detail}"


# ------------------------------------------------------------ look-ahead ---
def assert_point_in_time(records: Iterable[Mapping[str, Any]], *, as_of: date | datetime,
                         knowledge_field: str = "filed_date",
                         label: str = "records") -> None:
    """Raise if any record became knowable after ``as_of``.

    This is the single check that keeps historical analysis honest, so it raises
    instead of returning a finding: a violated point-in-time contract makes the
    surrounding result meaningless.
    """
    cutoff = to_date(as_of)
    offenders = []
    for record in records:
        raw = record.get(knowledge_field)
        if raw is None:
            offenders.append((record.get("metric") or record.get("id"), "missing"))
            continue
        if to_date(raw) > cutoff:
            offenders.append((record.get("metric") or record.get("id"), str(raw)))
    if offenders:
        sample = ", ".join(f"{m}@{d}" for m, d in offenders[:5])
        raise LookAheadError(
            f"{label}: {len(offenders)} record(s) not knowable at {cutoff} ({sample})")


def detect_lookahead_in_features(feature_dates: Sequence[date],
                                 source_dates: Sequence[date]) -> BiasFinding | None:
    """Check that every feature row is derived from data dated at or before it."""
    violations = [(f, s) for f, s in zip(feature_dates, source_dates)
                  if to_date(s) > to_date(f)]
    if not violations:
        return None
    return BiasFinding(
        "look_ahead",
        f"{len(violations)} feature rows use data dated after the feature date "
        f"(first: feature {violations[0][0]} <- source {violations[0][1]})")


def shifted_signal_is_safe(signal_dates: Sequence[date], trade_dates: Sequence[date]
                           ) -> BiasFinding | None:
    """Signals must be acted on strictly *after* they are observed."""
    same_bar = sum(1 for s, t in zip(signal_dates, trade_dates)
                   if to_date(t) <= to_date(s))
    if same_bar:
        return BiasFinding(
            "look_ahead",
            f"{same_bar} trades execute on or before the bar that generated the "
            f"signal; execution must occur on the next session")
    return None


# ---------------------------------------------------------- survivorship ---
def check_survivorship(universe: Sequence[Mapping[str, Any]], *,
                       as_of: date) -> BiasFinding | None:
    """Warn when a historical universe contains no dead assets.

    A real 2015 universe examined in 2026 must contain companies that were later
    delisted. If none are present, the sample has been cleaned of its failures
    and every backtest run on it will be flattered.
    """
    if not universe:
        return BiasFinding("survivorship", "empty universe", severe=True)
    dead = [a for a in universe if a.get("delisted_date")]
    listed_before = [a for a in universe
                     if a.get("listed_date") and to_date(a["listed_date"]) <= as_of]
    if not dead and len(universe) > 50:
        return BiasFinding(
            "survivorship",
            f"universe of {len(universe)} assets as of {as_of} contains no "
            f"delisted names; historical results will be biased upward")
    if listed_before and len(dead) / max(len(universe), 1) < 0.01 and len(universe) > 500:
        return BiasFinding(
            "survivorship",
            f"only {len(dead)} delisted assets in {len(universe)}: below the "
            f"historical base rate for a broad equity universe", severe=False)
    return None


def assert_no_future_listings(universe: Sequence[Mapping[str, Any]],
                              as_of: date) -> None:
    """Assets that had not listed yet must not appear in a historical universe."""
    cutoff = to_date(as_of)
    future = [a.get("symbol") for a in universe
              if a.get("listed_date") and to_date(a["listed_date"]) > cutoff]
    if future:
        raise LookAheadError(
            f"{len(future)} assets in the {cutoff} universe had not listed yet "
            f"({', '.join(str(s) for s in future[:5])})")


# --------------------------------------------------------------- leakage ---
def check_train_test_separation(train_start: date, train_end: date,
                                test_start: date, test_end: date, *,
                                embargo_days: int = 5) -> BiasFinding | None:
    """Train and test windows must be ordered and separated by an embargo.

    The embargo exists because a label observed at time T is computed from
    returns *after* T; without a gap, the tail of training overlaps the head of
    testing through the label horizon.
    """
    ts, te = to_date(train_start), to_date(train_end)
    vs, ve = to_date(test_start), to_date(test_end)
    if te < ts or ve < vs:
        return BiasFinding("leakage", "window start is after its end")
    if vs <= te:
        return BiasFinding(
            "leakage",
            f"test window starts {vs} before training ends {te}")
    gap = (vs - te).days
    if gap < embargo_days:
        return BiasFinding(
            "leakage",
            f"only {gap} days between train and test; {embargo_days} required "
            f"to cover the label horizon")
    return None


def check_label_horizon_embargo(label_horizon_days: int, embargo_days: int
                                ) -> BiasFinding | None:
    if embargo_days < label_horizon_days:
        return BiasFinding(
            "leakage",
            f"embargo of {embargo_days} days is shorter than the {label_horizon_days}-day "
            f"label horizon: the last training labels peek into the test period",
            severe=True)
    return None


# ---------------------------------------------------------- overfitting ----
def overfitting_pressure(*, parameters: int, observations: int,
                         configurations_tried: int = 1) -> dict[str, Any]:
    """Cheap diagnostics for how much a result should be discounted.

    ``deflation_factor`` is a blunt multiplier applied to backtested edge: with
    many configurations tried on few observations, the best result is mostly
    selection noise.
    """
    obs_per_param = observations / parameters if parameters else float("inf")
    # Effective number of independent trials inflates the best observed result.
    trial_penalty = 1.0 / (1.0 + 0.15 * max(0.0, (configurations_tried - 1) ** 0.5))
    sample_penalty = min(1.0, obs_per_param / 30.0) if parameters else 1.0
    warnings: list[str] = []
    if obs_per_param < 10:
        warnings.append(f"only {obs_per_param:.1f} observations per parameter")
    if configurations_tried > 20:
        warnings.append(f"{configurations_tried} configurations tried: "
                        f"the best one is partly selection noise")
    return {
        "observations_per_parameter": round(obs_per_param, 2),
        "configurations_tried": configurations_tried,
        "deflation_factor": round(trial_penalty * sample_penalty, 3),
        "warnings": warnings,
    }
