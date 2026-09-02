"""Time-series containers used by every analytical module.

``PriceSeries`` is deliberately a small, immutable, numpy-backed structure
rather than a DataFrame:

* it can assert its own invariants (sorted, unique, positive closes);
* ``as_of`` slicing is a first-class operation, so look-ahead bias is hard to
  write by accident;
* it has no pandas version coupling in the numerical core.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Sequence

import numpy as np

from research_engine.core.errors import InsufficientData, LookAheadError
from research_engine.core.numeric import as_array
from research_engine.core.timeutil import infer_periods_per_year, to_date


@dataclass(frozen=True, slots=True)
class PriceBar:
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None
    adj_close: float | None = None
    source: str = ""

    @property
    def effective_close(self) -> float:
        """Split/dividend-adjusted close when available, else raw close."""
        return float(self.adj_close) if self.adj_close is not None else float(self.close)


class PriceSeries:
    """Ordered OHLCV history for a single asset.

    All analytics read ``adj_close`` (total-return adjusted) unless they
    explicitly need raw prices, because unadjusted series create fake gaps at
    every split and dividend.
    """

    __slots__ = ("symbol", "dates", "open", "high", "low", "close", "volume",
                 "adj_close", "_periods_per_year")

    def __init__(self, symbol: str, bars: Sequence[PriceBar]) -> None:
        if not bars:
            raise InsufficientData(f"price series for {symbol}", 1, 0)
        ordered = sorted(bars, key=lambda b: b.date)
        seen: set[date] = set()
        for bar in ordered:
            if bar.date in seen:
                raise ValueError(f"duplicate price date {bar.date} for {symbol}")
            seen.add(bar.date)
        self.symbol = symbol
        self.dates: tuple[date, ...] = tuple(b.date for b in ordered)
        self.open = as_array([b.open for b in ordered])
        self.high = as_array([b.high for b in ordered])
        self.low = as_array([b.low for b in ordered])
        self.close = as_array([b.close for b in ordered])
        self.volume = as_array([b.volume for b in ordered])
        self.adj_close = as_array([b.effective_close for b in ordered])
        self._periods_per_year: int | None = None

    # -- construction ------------------------------------------------------
    @classmethod
    def from_rows(cls, symbol: str, rows: Iterable[dict]) -> "PriceSeries":
        bars = [
            PriceBar(
                date=to_date(r["date"]),
                open=r.get("open"), high=r.get("high"), low=r.get("low"),
                close=float(r["close"]), volume=r.get("volume"),
                adj_close=r.get("adj_close"), source=str(r.get("source", "")),
            )
            for r in rows
        ]
        return cls(symbol, bars)

    @classmethod
    def from_closes(cls, symbol: str, dates: Sequence[date | str],
                    closes: Sequence[float],
                    volumes: Sequence[float] | None = None) -> "PriceSeries":
        bars = [
            PriceBar(date=to_date(d), open=None, high=None, low=None,
                     close=float(c), volume=(float(volumes[i]) if volumes else None),
                     adj_close=float(c))
            for i, (d, c) in enumerate(zip(dates, closes))
        ]
        return cls(symbol, bars)

    # -- basics ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.dates)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (f"PriceSeries({self.symbol}, n={len(self)}, "
                f"{self.dates[0]}..{self.dates[-1]})")

    @property
    def start(self) -> date:
        return self.dates[0]

    @property
    def end(self) -> date:
        return self.dates[-1]

    @property
    def last_close(self) -> float:
        return float(self.adj_close[-1])

    @property
    def last_raw_close(self) -> float:
        return float(self.close[-1])

    @property
    def periods_per_year(self) -> int:
        if self._periods_per_year is None:
            self._periods_per_year = infer_periods_per_year(self.dates)
        return self._periods_per_year

    # -- slicing -----------------------------------------------------------
    def as_of(self, moment: date | datetime | str) -> "PriceSeries":
        """History up to and including ``moment``. The look-ahead guard."""
        cutoff = to_date(moment)
        keep = [i for i, d in enumerate(self.dates) if d <= cutoff]
        if not keep:
            raise InsufficientData(f"{self.symbol} history as of {cutoff}", 1, 0)
        return self._subset(keep)

    def between(self, start: date | str, end: date | str) -> "PriceSeries":
        lo, hi = to_date(start), to_date(end)
        keep = [i for i, d in enumerate(self.dates) if lo <= d <= hi]
        if not keep:
            raise InsufficientData(f"{self.symbol} history {lo}..{hi}", 1, 0)
        return self._subset(keep)

    def tail(self, n: int) -> "PriceSeries":
        if n <= 0:
            raise ValueError("n must be positive")
        keep = list(range(max(0, len(self) - n), len(self)))
        return self._subset(keep)

    def _subset(self, idx: Sequence[int]) -> "PriceSeries":
        bars = [
            PriceBar(date=self.dates[i],
                     open=_nan_to_none(self.open[i]),
                     high=_nan_to_none(self.high[i]),
                     low=_nan_to_none(self.low[i]),
                     close=float(self.close[i]),
                     volume=_nan_to_none(self.volume[i]),
                     adj_close=float(self.adj_close[i]))
            for i in idx
        ]
        return PriceSeries(self.symbol, bars)

    def price_on(self, day: date | str, *, tolerance_days: int = 5) -> float | None:
        """Last close at or before ``day`` within ``tolerance_days``."""
        target = to_date(day)
        for i in range(len(self) - 1, -1, -1):
            d = self.dates[i]
            if d <= target:
                return float(self.adj_close[i]) if (target - d).days <= tolerance_days else None
        return None

    def require_no_future(self, as_of: date | datetime) -> None:
        """Raise if the series extends past ``as_of`` (defensive assertion)."""
        cutoff = to_date(as_of)
        if self.dates[-1] > cutoff:
            raise LookAheadError(
                f"{self.symbol}: series ends {self.dates[-1]} which is after as-of {cutoff}")

    # -- derived series ----------------------------------------------------
    def returns(self, *, log: bool = False) -> np.ndarray:
        """Simple (or log) period returns of the adjusted close, length n-1."""
        px = self.adj_close
        if len(px) < 2:
            return np.array([])
        with np.errstate(divide="ignore", invalid="ignore"):
            if log:
                out = np.log(px[1:] / px[:-1])
            else:
                out = px[1:] / px[:-1] - 1.0
        out[~np.isfinite(out)] = np.nan
        return out

    def dollar_volume(self) -> np.ndarray:
        return self.close * self.volume

    def to_rows(self) -> list[dict]:
        return [
            {"date": self.dates[i].isoformat(),
             "open": _nan_to_none(self.open[i]),
             "high": _nan_to_none(self.high[i]),
             "low": _nan_to_none(self.low[i]),
             "close": float(self.close[i]),
             "volume": _nan_to_none(self.volume[i]),
             "adj_close": float(self.adj_close[i])}
            for i in range(len(self))
        ]


def _nan_to_none(x: float) -> float | None:
    return None if (x is None or not np.isfinite(x)) else float(x)


def align(a: PriceSeries, b: PriceSeries) -> tuple[np.ndarray, np.ndarray, list[date]]:
    """Intersect two series on date, returning aligned adjusted closes."""
    index = {d: i for i, d in enumerate(b.dates)}
    dates, xs, ys = [], [], []
    for i, d in enumerate(a.dates):
        j = index.get(d)
        if j is not None:
            dates.append(d)
            xs.append(a.adj_close[i])
            ys.append(b.adj_close[j])
    return np.array(xs), np.array(ys), dates


def aligned_returns(a: PriceSeries, b: PriceSeries) -> tuple[np.ndarray, np.ndarray]:
    """Aligned simple returns for beta/correlation work."""
    xs, ys, _ = align(a, b)
    if len(xs) < 3:
        return np.array([]), np.array([])
    with np.errstate(divide="ignore", invalid="ignore"):
        ra = xs[1:] / xs[:-1] - 1.0
        rb = ys[1:] / ys[:-1] - 1.0
    mask = np.isfinite(ra) & np.isfinite(rb)
    return ra[mask], rb[mask]
