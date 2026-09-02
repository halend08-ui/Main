"""Repositories: the only place that knows SQL for a given table.

The important methods here are the *point-in-time* ones. ``fundamentals_as_of``
filters on ``filed_date <= as_of`` and ``prices_as_of`` on ``date <= as_of``,
so any analysis that goes through a repository inherits look-ahead protection
instead of having to remember it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.errors import DataUnavailable, StorageError
from research_engine.core.logging import get_logger
from research_engine.core.series import PriceSeries
from research_engine.core.timeutil import iso, to_date, to_datetime, utcnow
from research_engine.core.types import AssetClass, DataQuality, Period, SourceTier
from research_engine.storage.db import Database, dumps, loads

log = get_logger(__name__)


# --------------------------------------------------------------- assets ----
@dataclass(frozen=True, slots=True)
class AssetRecord:
    id: int
    symbol: str
    asset_class: AssetClass
    name: str | None = None
    exchange: str | None = None
    country: str | None = None
    currency: str | None = None
    sector: str | None = None
    industry: str | None = None
    cik: str | None = None
    coingecko_id: str | None = None
    chain: str | None = None
    is_active: bool = True
    listed_date: date | None = None
    delisted_date: date | None = None
    market_cap_usd: float | None = None
    shares_outstanding: float | None = None
    float_shares: float | None = None
    circulating_supply: float | None = None
    max_supply: float | None = None
    quality_grade: str | None = None
    tags: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "AssetRecord":
        return cls(
            id=int(row["id"]),
            symbol=row["symbol"],
            asset_class=AssetClass(row["asset_class"]),
            name=row.get("name"), exchange=row.get("exchange"),
            country=row.get("country"), currency=row.get("currency"),
            sector=row.get("sector"), industry=row.get("industry"),
            cik=row.get("cik"), coingecko_id=row.get("coingecko_id"),
            chain=row.get("chain"), is_active=bool(row.get("is_active", 1)),
            listed_date=to_date(row["listed_date"]) if row.get("listed_date") else None,
            delisted_date=to_date(row["delisted_date"]) if row.get("delisted_date") else None,
            market_cap_usd=row.get("market_cap_usd"),
            shares_outstanding=row.get("shares_outstanding"),
            float_shares=row.get("float_shares"),
            circulating_supply=row.get("circulating_supply"),
            max_supply=row.get("max_supply"),
            quality_grade=row.get("quality_grade"),
            tags=tuple(loads(row.get("tags"), []) or []),
        )

    @property
    def is_crypto(self) -> bool:
        return self.asset_class is AssetClass.CRYPTO


class AssetRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, *, symbol: str, asset_class: AssetClass | str,
               **fields: Any) -> int:
        ac = AssetClass(asset_class) if isinstance(asset_class, str) else asset_class
        now = iso(utcnow())
        payload: dict[str, Any] = {
            "symbol": symbol.upper().strip(),
            "asset_class": ac.value,
            "created_at": now,
            "updated_at": now,
        }
        for key, value in fields.items():
            if value is None:
                continue
            if key == "tags":
                payload["tags"] = dumps(list(value))
            elif isinstance(value, (date, datetime)):
                payload[key] = iso(value)
            elif isinstance(value, bool):
                payload[key] = int(value)
            else:
                payload[key] = value
        self.db.upsert("assets", payload, conflict_columns=["asset_class", "symbol"],
                       update_columns=[c for c in payload if c != "created_at"])
        row = self.db.query_one(
            "SELECT id FROM assets WHERE asset_class=? AND symbol=?",
            (ac.value, payload["symbol"]))
        if not row:
            raise StorageError(f"failed to persist asset {symbol}")
        return int(row["id"])

    def get(self, symbol: str, asset_class: AssetClass | str | None = None
            ) -> AssetRecord | None:
        if asset_class is not None:
            ac = AssetClass(asset_class) if isinstance(asset_class, str) else asset_class
            row = self.db.query_one(
                "SELECT * FROM assets WHERE asset_class=? AND symbol=?",
                (ac.value, symbol.upper()))
        else:
            row = self.db.query_one(
                "SELECT * FROM assets WHERE symbol=? ORDER BY asset_class LIMIT 1",
                (symbol.upper(),))
        return AssetRecord.from_row(row) if row else None

    def require(self, symbol: str,
                asset_class: AssetClass | str | None = None) -> AssetRecord:
        record = self.get(symbol, asset_class)
        if record is None:
            raise DataUnavailable(f"unknown asset {symbol!r}")
        return record

    def by_id(self, asset_id: int) -> AssetRecord | None:
        row = self.db.query_one("SELECT * FROM assets WHERE id=?", (asset_id,))
        return AssetRecord.from_row(row) if row else None

    def list(self, *, asset_class: AssetClass | str | None = None,
             active_only: bool = True, sector: str | None = None,
             min_market_cap: float | None = None,
             limit: int | None = None) -> list[AssetRecord]:
        clauses, params = ["1=1"], []
        if asset_class is not None:
            ac = AssetClass(asset_class) if isinstance(asset_class, str) else asset_class
            clauses.append("asset_class=?")
            params.append(ac.value)
        if active_only:
            clauses.append("is_active=1")
        if sector:
            clauses.append("sector=?")
            params.append(sector)
        if min_market_cap is not None:
            clauses.append("market_cap_usd >= ?")
            params.append(float(min_market_cap))
        sql = (f"SELECT * FROM assets WHERE {' AND '.join(clauses)} "
               f"ORDER BY COALESCE(market_cap_usd, 0) DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [AssetRecord.from_row(r) for r in self.db.query(sql, params)]

    def mark_delisted(self, asset_id: int, when: date | str, reason: str) -> None:
        """Keep the asset (survivorship-bias control); flag it inactive."""
        self.db.execute(
            "UPDATE assets SET is_active=0, delisted_date=?, delisting_reason=?, "
            "updated_at=? WHERE id=?",
            (iso(to_date(when)), reason, iso(utcnow()), asset_id))

    def count(self, asset_class: AssetClass | str | None = None) -> int:
        if asset_class is None:
            return int(self.db.scalar("SELECT COUNT(*) FROM assets") or 0)
        ac = AssetClass(asset_class) if isinstance(asset_class, str) else asset_class
        return int(self.db.scalar("SELECT COUNT(*) FROM assets WHERE asset_class=?",
                                  (ac.value,)) or 0)


# --------------------------------------------------------------- prices ----
class PriceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write_bars(self, asset_id: int, bars: Iterable[Mapping[str, Any]], *,
                   source: str, quality: DataQuality = DataQuality.GOOD) -> int:
        now = iso(utcnow())
        rows = []
        for bar in bars:
            close = bar.get("close")
            if close is None:
                continue  # never invent a close
            rows.append({
                "asset_id": asset_id,
                "date": iso(to_date(bar["date"])),
                "open": _f(bar.get("open")), "high": _f(bar.get("high")),
                "low": _f(bar.get("low")), "close": float(close),
                "volume": _f(bar.get("volume")),
                "adj_close": _f(bar.get("adj_close")),
                "split_factor": float(bar.get("split_factor", 1.0) or 1.0),
                "dividend": float(bar.get("dividend", 0.0) or 0.0),
                "currency": bar.get("currency"),
                "source": source, "retrieved_at": now,
                "quality": quality.value, "revision": 1,
            })
        if not rows:
            return 0
        written = self.db.upsert_many(
            "prices_daily", rows, conflict_columns=["asset_id", "date"],
            update_columns=["open", "high", "low", "close", "volume", "adj_close",
                            "split_factor", "dividend", "currency", "source",
                            "retrieved_at", "quality"])
        self._refresh_bounds(asset_id)
        return written

    def _refresh_bounds(self, asset_id: int) -> None:
        row = self.db.query_one(
            "SELECT MIN(date) AS lo, MAX(date) AS hi FROM prices_daily WHERE asset_id=?",
            (asset_id,))
        if row and row.get("hi"):
            self.db.execute(
                "UPDATE assets SET first_price_date=?, last_price_date=?, updated_at=? "
                "WHERE id=?", (row["lo"], row["hi"], iso(utcnow()), asset_id))

    def series(self, asset_id: int, symbol: str, *,
               as_of: date | datetime | str | None = None,
               start: date | str | None = None,
               limit: int | None = None) -> PriceSeries:
        clauses, params = ["asset_id=?"], [asset_id]
        if as_of is not None:
            clauses.append("date <= ?")
            params.append(iso(to_date(as_of)))
        if start is not None:
            clauses.append("date >= ?")
            params.append(iso(to_date(start)))
        sql = (f"SELECT date, open, high, low, close, volume, adj_close, source "
               f"FROM prices_daily WHERE {' AND '.join(clauses)} ORDER BY date")
        rows = self.db.query(sql, params)
        if limit:
            rows = rows[-int(limit):]
        if not rows:
            raise DataUnavailable(f"no price history stored for {symbol}")
        return PriceSeries.from_rows(symbol, rows)

    def latest_close(self, asset_id: int,
                     as_of: date | str | None = None) -> tuple[date, float] | None:
        params: list[Any] = [asset_id]
        sql = "SELECT date, close, adj_close FROM prices_daily WHERE asset_id=?"
        if as_of is not None:
            sql += " AND date <= ?"
            params.append(iso(to_date(as_of)))
        sql += " ORDER BY date DESC LIMIT 1"
        row = self.db.query_one(sql, params)
        if not row:
            return None
        px = row["adj_close"] if row.get("adj_close") is not None else row["close"]
        return to_date(row["date"]), float(px)

    def coverage(self, asset_id: int) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n, MIN(date) AS lo, MAX(date) AS hi "
            "FROM prices_daily WHERE asset_id=?", (asset_id,))
        return dict(row or {"n": 0, "lo": None, "hi": None})

    def bulk_latest(self, as_of: date | str | None = None) -> dict[int, float]:
        """Latest close per asset -- one query for the whole universe scan."""
        cutoff = iso(to_date(as_of)) if as_of else None
        sql = """
            SELECT p.asset_id AS asset_id,
                   COALESCE(p.adj_close, p.close) AS px
            FROM prices_daily p
            JOIN (SELECT asset_id, MAX(date) AS mx FROM prices_daily
                  WHERE (? IS NULL OR date <= ?) GROUP BY asset_id) latest
              ON latest.asset_id = p.asset_id AND latest.mx = p.date
        """
        return {int(r["asset_id"]): float(r["px"])
                for r in self.db.query(sql, (cutoff, cutoff))}


# --------------------------------------------------------- fundamentals ----
@dataclass(frozen=True, slots=True)
class FundamentalPoint:
    metric: str
    period: Period
    period_end: date
    value: float | None
    unit: str | None
    filed_date: date
    source: str
    source_tier: SourceTier
    quality: DataQuality
    form: str | None = None
    accession: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "FundamentalPoint":
        return cls(
            metric=row["metric"], period=Period(row["period"]),
            period_end=to_date(row["period_end"]), value=row.get("value"),
            unit=row.get("unit"), filed_date=to_date(row["filed_date"]),
            source=row["source"], source_tier=SourceTier(row["source_tier"]),
            quality=DataQuality(row.get("quality", "fair")),
            form=row.get("form"), accession=row.get("accession"))


class FundamentalRepository:
    """Point-in-time store of financial statement line items."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, asset_id: int, points: Iterable[Mapping[str, Any]], *,
              source: str, source_tier: SourceTier) -> int:
        now = iso(utcnow())
        rows = []
        for p in points:
            if not p.get("metric") or not p.get("period_end") or not p.get("filed_date"):
                continue
            rows.append({
                "asset_id": asset_id,
                "metric": str(p["metric"]),
                "statement": p.get("statement"),
                "period": str(p.get("period", "annual")),
                "period_start": iso(to_date(p["period_start"])) if p.get("period_start") else None,
                "period_end": iso(to_date(p["period_end"])),
                "fiscal_year": p.get("fiscal_year"),
                "fiscal_period": p.get("fiscal_period"),
                "value": _f(p.get("value")),
                "unit": p.get("unit"),
                "filed_date": iso(to_date(p["filed_date"])),
                "accession": p.get("accession") or "",
                "form": p.get("form"),
                "source": source,
                "source_tier": source_tier.value,
                "retrieved_at": now,
                "quality": DataQuality(p.get("quality", DataQuality.GOOD)).value
                if not isinstance(p.get("quality"), DataQuality)
                else p["quality"].value,
                "revision": int(p.get("revision", 1)),
            })
        if not rows:
            return 0
        return self.db.upsert_many(
            "fundamentals", rows,
            conflict_columns=["asset_id", "metric", "period", "period_end",
                              "source", "accession"],
            update_columns=["value", "unit", "filed_date", "form", "retrieved_at",
                            "quality", "revision", "statement", "period_start",
                            "fiscal_year", "fiscal_period"])

    def history(self, asset_id: int, metric: str, *,
                period: Period | str = Period.ANNUAL,
                as_of: date | datetime | str | None = None,
                limit: int = 40) -> list[FundamentalPoint]:
        """Values for ``metric`` known at ``as_of``, oldest period first.

        When several filings report the same period (restatements), the row
        with the latest ``filed_date`` that is still <= as_of wins -- exactly
        what an analyst could have seen at that moment. Selection is done in
        Python rather than in a correlated subquery because the rule is subtle
        and worth reading plainly.
        """
        p = Period(period) if isinstance(period, str) else period
        params: list[Any] = [asset_id, metric, p.value]
        pit = ""
        if as_of is not None:
            pit = "AND filed_date <= ?"
            params.append(iso(to_date(as_of)))
        rows = self.db.query(
            f"SELECT * FROM fundamentals WHERE asset_id=? AND metric=? AND period=? "
            f"{pit} ORDER BY period_end DESC, filed_date DESC, revision DESC",
            params)
        chosen: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            key = str(row["period_end"])
            if key not in chosen:      # rows are already ordered newest-filing-first
                chosen[key] = row
        points = [FundamentalPoint.from_row(r) for r in chosen.values()]
        points.sort(key=lambda pt: pt.period_end)
        return points[-int(limit):] if limit else points

    def latest(self, asset_id: int, metric: str, *,
               period: Period | str = Period.ANNUAL,
               as_of: date | str | None = None) -> FundamentalPoint | None:
        points = self.history(asset_id, metric, period=period, as_of=as_of, limit=1)
        return points[-1] if points else None

    def latest_value(self, asset_id: int, metric: str, *,
                     period: Period | str = Period.ANNUAL,
                     as_of: date | str | None = None) -> float | None:
        point = self.latest(asset_id, metric, period=period, as_of=as_of)
        return point.value if point else None

    def metrics_available(self, asset_id: int) -> list[str]:
        return [r["metric"] for r in self.db.query(
            "SELECT DISTINCT metric FROM fundamentals WHERE asset_id=? ORDER BY metric",
            (asset_id,))]

    def bulk_latest(self, metric: str, asset_ids: Sequence[int], *,
                    period: Period | str = Period.ANNUAL,
                    as_of: date | str | None = None) -> dict[int, float]:
        """Cross-sectional read used by screening (one query, not N)."""
        if not asset_ids:
            return {}
        p = Period(period) if isinstance(period, str) else period
        placeholders = ",".join("?" for _ in asset_ids)
        params: list[Any] = [metric, p.value, *asset_ids]
        pit = ""
        if as_of is not None:
            pit = "AND filed_date <= ?"
            params.append(iso(to_date(as_of)))
        sql = f"""
            SELECT asset_id, value FROM fundamentals f
            WHERE metric=? AND period=? AND asset_id IN ({placeholders}) {pit}
              AND value IS NOT NULL
              AND period_end = (SELECT MAX(period_end) FROM fundamentals g
                                WHERE g.asset_id=f.asset_id AND g.metric=f.metric
                                  AND g.period=f.period)
            GROUP BY asset_id
        """
        return {int(r["asset_id"]): float(r["value"]) for r in self.db.query(sql, params)}


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None
