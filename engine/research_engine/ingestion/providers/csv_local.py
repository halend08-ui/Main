"""Local CSV/JSON provider.

Purpose:

* run the engine fully offline (air-gapped research, CI, reproducible demos);
* import a licensed vendor extract without writing a new provider;
* provide a deterministic fixture source for tests.

Layout under ``<root>``::

    prices/<SYMBOL>.csv        date,open,high,low,close,volume[,adj_close]
    fundamentals/<SYMBOL>.csv  metric,period,period_end,value,unit,filed_date[,form]
    macro/<SERIES_ID>.csv      date,value[,release_date]
    news/<SYMBOL>.csv          published_at,headline,source[,url,summary,tier]
    universe/equity.csv        symbol,name,exchange,sector,industry,country[,cik]
    universe/crypto.csv        symbol,name,coingecko_id[,chain]

Nothing is inferred: a missing column becomes a missing value, not a zero.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.errors import DataUnavailable
from research_engine.core.logging import get_logger
from research_engine.core.timeutil import to_date, to_datetime
from research_engine.core.types import DataQuality, SourceTier
from research_engine.ingestion.base import Capability, DataProvider, ProviderResult

log = get_logger(__name__)


def _num(row: Mapping[str, Any], key: str) -> float | None:
    raw = row.get(key)
    if raw is None or str(raw).strip() in ("", "-", "n/a", "N/A", "null", "None"):
        return None
    try:
        return float(str(raw).replace(",", ""))
    except ValueError:
        return None


class CsvLocalProvider(DataProvider):
    name = "csv_local"
    capabilities = frozenset({
        Capability.PRICES_EOD, Capability.FUNDAMENTALS, Capability.MACRO,
        Capability.NEWS, Capability.UNIVERSE_EQUITY, Capability.UNIVERSE_CRYPTO,
    })
    source_tier = SourceTier.DATA_PROVIDER
    default_quality = DataQuality.GOOD
    documentation = "Local files; quality depends entirely on what the operator supplies."

    def __init__(self, root: str | Path, **kwargs: Any) -> None:
        kwargs.setdefault("transport", None)
        super().__init__(**kwargs)
        self.root = Path(root)

    @property
    def available(self) -> bool:
        return self.root.exists()

    def unavailable_reason(self) -> str | None:
        if not self.root.exists():
            return f"{self.name}: directory not found: {self.root}"
        return None

    # -- helpers -----------------------------------------------------------
    def _read(self, relative: str) -> list[dict[str, str]]:
        path = self.root / relative
        if not path.exists():
            raise DataUnavailable(f"{self.name}: no file {path}")
        with path.open(newline="", encoding="utf-8") as fh:
            return [ {(k or "").strip().lower(): (v.strip() if isinstance(v, str) else v)
                      for k, v in row.items()}
                     for row in csv.DictReader(fh) ]

    # -- capabilities ------------------------------------------------------
    def fetch_prices(self, symbol: str, *, start: date | None = None,
                     end: date | None = None) -> ProviderResult:
        rows = self._read(f"prices/{symbol.upper()}.csv")
        bars: list[dict[str, Any]] = []
        for row in rows:
            if not row.get("date"):
                continue
            day = to_date(row["date"])
            if start and day < start:
                continue
            if end and day > end:
                continue
            close = _num(row, "close")
            if close is None:
                continue      # a bar without a close is not a bar
            bars.append({
                "date": day, "open": _num(row, "open"), "high": _num(row, "high"),
                "low": _num(row, "low"), "close": close, "volume": _num(row, "volume"),
                "adj_close": _num(row, "adj_close"),
                "split_factor": _num(row, "split_factor") or 1.0,
                "dividend": _num(row, "dividend") or 0.0,
            })
        missing = [c for c in ("open", "high", "low", "volume", "adj_close")
                   if rows and c not in rows[0]]
        return self.result(Capability.PRICES_EOD, bars,
                           as_of=bars[-1]["date"] if bars else None,
                           url=str(self.root / f"prices/{symbol.upper()}.csv"),
                           missing=missing, partial=bool(missing))

    def fetch_fundamentals(self, symbol: str, *,
                           identifiers: Mapping[str, Any] | None = None) -> ProviderResult:
        rows = self._read(f"fundamentals/{symbol.upper()}.csv")
        points = []
        for row in rows:
            if not row.get("metric") or not row.get("period_end"):
                continue
            points.append({
                "metric": row["metric"],
                "statement": row.get("statement"),
                "period": row.get("period", "annual"),
                "period_end": to_date(row["period_end"]),
                "period_start": to_date(row["period_start"]) if row.get("period_start") else None,
                "value": _num(row, "value"),
                "unit": row.get("unit", "USD"),
                # Absent a filing date we must assume the value was only knowable
                # at period end; assuming earlier would create look-ahead bias.
                "filed_date": to_date(row["filed_date"]) if row.get("filed_date")
                else to_date(row["period_end"]),
                "form": row.get("form"),
                "accession": row.get("accession", "local"),
            })
        return self.result(Capability.FUNDAMENTALS, points,
                           url=str(self.root / f"fundamentals/{symbol.upper()}.csv"),
                           notes=("filed_date defaulted to period_end where absent",)
                           if any(not r.get("filed_date") for r in rows) else ())

    def fetch_macro(self, series_id: str, *,
                    start: date | None = None) -> ProviderResult:
        rows = self._read(f"macro/{series_id.upper()}.csv")
        obs = [{"date": to_date(r["date"]), "value": _num(r, "value"),
                "release_date": to_date(r["release_date"]) if r.get("release_date") else None}
               for r in rows if r.get("date")
               and (start is None or to_date(r["date"]) >= start)]
        return self.result(Capability.MACRO, obs,
                           url=str(self.root / f"macro/{series_id.upper()}.csv"))

    def fetch_news(self, symbol: str | None = None, *, limit: int = 50) -> ProviderResult:
        rel = f"news/{symbol.upper()}.csv" if symbol else "news/general.csv"
        rows = self._read(rel)
        items = []
        for row in rows[:limit]:
            if not row.get("headline") or not row.get("published_at"):
                continue
            items.append({
                "headline": row["headline"], "summary": row.get("summary"),
                "url": row.get("url"),
                "published_at": to_datetime(row["published_at"]),
                "source": row.get("source", "local"),
                "source_tier": SourceTier(row["tier"]) if row.get("tier")
                else SourceTier.FINANCIAL_JOURNALISM,
                "symbol": symbol,
            })
        return self.result(Capability.NEWS, items, url=str(self.root / rel))

    def fetch_universe(self, asset_class: str = "equity") -> ProviderResult:
        rel = f"universe/{asset_class}.csv"
        rows = self._read(rel)
        cap = (Capability.UNIVERSE_CRYPTO if asset_class == "crypto"
               else Capability.UNIVERSE_EQUITY)
        records = [{k: v for k, v in row.items() if v not in ("", None)} for row in rows]
        return self.result(cap, records, url=str(self.root / rel))
