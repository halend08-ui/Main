"""Time handling and point-in-time discipline.

The engine is built around an explicit *as-of* clock. Any component that reads
data takes an ``as_of`` timestamp and may only see records whose
``retrieved_at`` is at or before it. That single rule is what prevents
look-ahead bias from creeping into research, backtests and model training.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterator, Sequence

UTC = timezone.utc

_AS_OF: contextvars.ContextVar[datetime | None] = contextvars.ContextVar(
    "research_engine_as_of", default=None)


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Attach UTC to naive datetimes; convert aware ones. Never guess local."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_datetime(value: date | datetime | str) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0), tzinfo=UTC)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"unparseable timestamp: {value!r}") from exc


def to_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return ensure_utc(value).date()
    if isinstance(value, date):
        return value
    return to_datetime(value).date()


def iso(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return ensure_utc(value).isoformat()
    return value.isoformat()


def current_as_of() -> datetime:
    """The active as-of instant (defaults to now)."""
    return _AS_OF.get() or utcnow()


@contextlib.contextmanager
def as_of_context(moment: date | datetime) -> Iterator[datetime]:
    """Pin the engine's clock, e.g. while replaying a historical decision."""
    value = to_datetime(moment)
    token = _AS_OF.set(value)
    try:
        yield value
    finally:
        _AS_OF.reset(token)


def years_between(start: date | datetime, end: date | datetime) -> float:
    d0, d1 = to_date(start), to_date(end)
    return (d1 - d0).days / 365.25


def days_between(start: date | datetime, end: date | datetime) -> int:
    return (to_date(end) - to_date(start)).days


# --- Trading calendar -------------------------------------------------------
#
# A full exchange calendar (holidays per venue) is a data problem, not a code
# problem, and getting it wrong silently corrupts backtests. We therefore ship
# a weekday calendar plus an optional holiday set that can be loaded from
# configuration, and we mark crypto as 24/7.

_WEEKEND = {5, 6}


@dataclass(frozen=True, slots=True)
class TradingCalendar:
    """Weekday calendar with injectable holidays. ``continuous`` = crypto."""

    name: str = "weekday"
    holidays: frozenset[date] = frozenset()
    continuous: bool = False

    def is_session(self, day: date | datetime) -> bool:
        d = to_date(day)
        if self.continuous:
            return True
        return d.weekday() not in _WEEKEND and d not in self.holidays

    def next_session(self, day: date | datetime) -> date:
        d = to_date(day) + timedelta(days=1)
        for _ in range(30):
            if self.is_session(d):
                return d
            d += timedelta(days=1)
        raise ValueError("no trading session found within 30 days")

    def previous_session(self, day: date | datetime) -> date:
        d = to_date(day) - timedelta(days=1)
        for _ in range(30):
            if self.is_session(d):
                return d
            d -= timedelta(days=1)
        raise ValueError("no trading session found within 30 days")

    def sessions_between(self, start: date | datetime,
                         end: date | datetime) -> list[date]:
        d, last = to_date(start), to_date(end)
        out: list[date] = []
        while d <= last:
            if self.is_session(d):
                out.append(d)
            d += timedelta(days=1)
        return out

    def count_sessions(self, start: date | datetime, end: date | datetime) -> int:
        return len(self.sessions_between(start, end))


CRYPTO_CALENDAR = TradingCalendar(name="crypto-24x7", continuous=True)
EQUITY_CALENDAR = TradingCalendar(name="weekday")


def annualization_factor(periods_per_year: int) -> float:
    return float(periods_per_year) ** 0.5


def infer_periods_per_year(dates: Sequence[date | datetime]) -> int:
    """Infer sampling frequency from observation spacing.

    Returns 252 (daily equity), 365 (daily crypto), 52 (weekly) or 12 (monthly).
    Defaults to 252 when the series is too short to tell.
    """
    ds = [to_date(d) for d in dates]
    if len(ds) < 3:
        return 252
    gaps = [(ds[i] - ds[i - 1]).days for i in range(1, len(ds))]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return 252
    gaps.sort()
    median_gap = gaps[len(gaps) // 2]
    if median_gap <= 1:
        # Weekends present => exchange-traded; absent => continuous market.
        weekend_days = sum(1 for d in ds if d.weekday() in _WEEKEND)
        return 365 if weekend_days > 0.15 * len(ds) else 252
    if median_gap <= 4:
        return 252
    if median_gap <= 10:
        return 52
    if median_gap <= 45:
        return 12
    return 4
