"""Repositories for reference and contextual data: news, events, macro,
crypto on-chain metrics, ownership, provider health and data-quality reports."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.logging import get_logger
from research_engine.core.timeutil import iso, to_date, to_datetime, utcnow
from research_engine.core.types import (DataQuality, EventImpact, SourceTier)
from research_engine.storage.db import Database, dumps, loads

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NewsItem:
    id: int
    headline: str
    summary: str | None
    url: str | None
    published_at: datetime
    source: str
    source_tier: SourceTier
    symbol: str | None = None
    asset_id: int | None = None
    headline_sentiment: float | None = None
    body_sentiment: float | None = None
    hype_score: float | None = None
    duplicate_of: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "NewsItem":
        return cls(
            id=int(row["id"]), headline=row["headline"], summary=row.get("summary"),
            url=row.get("url"), published_at=to_datetime(row["published_at"]),
            source=row["source"], source_tier=SourceTier(row["source_tier"]),
            symbol=row.get("symbol"), asset_id=row.get("asset_id"),
            headline_sentiment=row.get("headline_sentiment"),
            body_sentiment=row.get("body_sentiment"),
            hype_score=row.get("hype_score"), duplicate_of=row.get("duplicate_of"))


def news_key(url: str | None, headline: str, published_at: datetime) -> str:
    """Stable dedupe key: URL when present, else headline+day."""
    basis = url.strip().lower() if url else f"{headline.strip().lower()}|{published_at.date()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


class NewsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, items: Iterable[Mapping[str, Any]]) -> int:
        now = iso(utcnow())
        written = 0
        for item in items:
            published = to_datetime(item["published_at"])
            key = item.get("external_id") or news_key(item.get("url"),
                                                      item["headline"], published)
            payload = {
                "external_id": key,
                "asset_id": item.get("asset_id"),
                "symbol": item.get("symbol"),
                "headline": item["headline"],
                "summary": item.get("summary"),
                "url": item.get("url"),
                "published_at": iso(published),
                "source": item["source"],
                "source_tier": SourceTier(item.get("source_tier",
                                                   SourceTier.FINANCIAL_JOURNALISM)).value
                if not isinstance(item.get("source_tier"), SourceTier)
                else item["source_tier"].value,
                "language": item.get("language", "en"),
                "headline_sentiment": item.get("headline_sentiment"),
                "body_sentiment": item.get("body_sentiment"),
                "hype_score": item.get("hype_score"),
                "duplicate_of": item.get("duplicate_of"),
                "retrieved_at": now,
            }
            self.db.upsert("news", payload, conflict_columns=["external_id"],
                           update_columns=["headline_sentiment", "body_sentiment",
                                           "hype_score", "duplicate_of", "summary",
                                           "asset_id"])
            written += 1
        return written

    def recent(self, asset_id: int | None = None, *, since: datetime | str | None = None,
               as_of: datetime | str | None = None, limit: int = 100) -> list[NewsItem]:
        clauses, params = ["1=1"], []
        if asset_id is not None:
            clauses.append("asset_id=?")
            params.append(asset_id)
        if since is not None:
            clauses.append("published_at >= ?")
            params.append(iso(to_datetime(since)))
        if as_of is not None:
            clauses.append("published_at <= ?")
            params.append(iso(to_datetime(as_of)))
        rows = self.db.query(
            f"SELECT * FROM news WHERE {' AND '.join(clauses)} "
            f"ORDER BY published_at DESC LIMIT {int(limit)}", params)
        return [NewsItem.from_row(r) for r in rows]

    def update_scores(self, news_id: int, *, headline_sentiment: float | None = None,
                      body_sentiment: float | None = None,
                      hype_score: float | None = None,
                      duplicate_of: int | None = None) -> None:
        self.db.execute(
            "UPDATE news SET headline_sentiment=COALESCE(?, headline_sentiment), "
            "body_sentiment=COALESCE(?, body_sentiment), "
            "hype_score=COALESCE(?, hype_score), duplicate_of=COALESCE(?, duplicate_of) "
            "WHERE id=?",
            (headline_sentiment, body_sentiment, hype_score, duplicate_of, news_id))


class EventRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, asset_id: int | None, *, event_type: str,
              occurred_at: datetime | str, impact: EventImpact,
              source: str, source_tier: SourceTier,
              headline: str | None = None, detail: Mapping[str, Any] | None = None,
              expected_impact_pct: float | None = None,
              confidence: float | None = None, duration_days: int | None = None,
              changes_thesis: bool = False, news_id: int | None = None) -> int:
        return self.db.insert("events", {
            "asset_id": asset_id,
            "event_type": event_type,
            "occurred_at": iso(to_datetime(occurred_at)),
            "detected_at": iso(utcnow()),
            "impact": impact.value,
            "expected_impact_pct": expected_impact_pct,
            "confidence": confidence,
            "duration_days": duration_days,
            "changes_thesis": int(changes_thesis),
            "headline": headline,
            "detail": dumps(dict(detail or {})),
            "news_id": news_id,
            "source": source,
            "source_tier": source_tier.value,
        })

    def for_asset(self, asset_id: int, *, since: datetime | str | None = None,
                  as_of: datetime | str | None = None,
                  limit: int = 50) -> list[dict[str, Any]]:
        clauses, params = ["asset_id=?"], [asset_id]
        if since is not None:
            clauses.append("occurred_at >= ?")
            params.append(iso(to_datetime(since)))
        if as_of is not None:
            clauses.append("detected_at <= ?")   # point-in-time: when we knew
            params.append(iso(to_datetime(as_of)))
        rows = self.db.query(
            f"SELECT * FROM events WHERE {' AND '.join(clauses)} "
            f"ORDER BY occurred_at DESC LIMIT {int(limit)}", params)
        for row in rows:
            row["detail"] = loads(row.get("detail"), {})
        return rows


class MacroRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, series_id: str, observations: Iterable[Mapping[str, Any]], *,
              source: str, source_tier: SourceTier = SourceTier.REGULATORY_FILING,
              unit: str | None = None) -> int:
        now = iso(utcnow())
        rows = [{
            "series_id": series_id,
            "date": iso(to_date(o["date"])),
            "value": None if o.get("value") is None else float(o["value"]),
            "unit": o.get("unit", unit),
            # Release date matters: GDP for Q1 is not knowable in Q1.
            "release_date": iso(to_date(o["release_date"])) if o.get("release_date")
            else iso(to_date(o["date"])),
            "source": source,
            "source_tier": source_tier.value,
            "retrieved_at": now,
        } for o in observations if o.get("date")]
        if not rows:
            return 0
        return self.db.upsert_many(
            "macro_series", rows, conflict_columns=["series_id", "date", "source"],
            update_columns=["value", "unit", "release_date", "retrieved_at"])

    def series(self, series_id: str, *, as_of: date | str | None = None,
               start: date | str | None = None,
               limit: int = 500) -> list[tuple[date, float]]:
        """Observations *released* on or before ``as_of`` (point-in-time)."""
        clauses, params = ["series_id=?", "value IS NOT NULL"], [series_id]
        if as_of is not None:
            clauses.append("release_date <= ?")
            params.append(iso(to_date(as_of)))
        if start is not None:
            clauses.append("date >= ?")
            params.append(iso(to_date(start)))
        rows = self.db.query(
            f"SELECT date, value FROM macro_series WHERE {' AND '.join(clauses)} "
            f"ORDER BY date DESC LIMIT {int(limit)}", params)
        return [(to_date(r["date"]), float(r["value"])) for r in reversed(rows)]

    def latest(self, series_id: str,
               as_of: date | str | None = None) -> tuple[date, float] | None:
        points = self.series(series_id, as_of=as_of, limit=1)
        return points[-1] if points else None


class CryptoMetricRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, asset_id: int, metric: str,
              observations: Iterable[Mapping[str, Any]], *, source: str,
              source_tier: SourceTier = SourceTier.DATA_PROVIDER,
              quality: DataQuality = DataQuality.FAIR, unit: str | None = None) -> int:
        now = iso(utcnow())
        rows = [{
            "asset_id": asset_id, "metric": metric,
            "date": iso(to_date(o["date"])),
            "value": None if o.get("value") is None else float(o["value"]),
            "unit": o.get("unit", unit), "source": source,
            "source_tier": source_tier.value, "retrieved_at": now,
            "quality": quality.value,
        } for o in observations if o.get("date")]
        if not rows:
            return 0
        return self.db.upsert_many(
            "crypto_metrics", rows,
            conflict_columns=["asset_id", "metric", "date", "source"],
            update_columns=["value", "unit", "retrieved_at", "quality"])

    def series(self, asset_id: int, metric: str, *, as_of: date | str | None = None,
               limit: int = 400) -> list[tuple[date, float]]:
        clauses, params = ["asset_id=?", "metric=?", "value IS NOT NULL"], [asset_id, metric]
        if as_of is not None:
            clauses.append("date <= ?")
            params.append(iso(to_date(as_of)))
        rows = self.db.query(
            f"SELECT date, value FROM crypto_metrics WHERE {' AND '.join(clauses)} "
            f"ORDER BY date DESC LIMIT {int(limit)}", params)
        return [(to_date(r["date"]), float(r["value"])) for r in reversed(rows)]

    def write_unlocks(self, asset_id: int,
                      unlocks: Iterable[Mapping[str, Any]], *, source: str) -> int:
        now = iso(utcnow())
        rows = [{
            "asset_id": asset_id,
            "unlock_date": iso(to_date(u["unlock_date"])),
            "tokens": u.get("tokens"), "pct_of_supply": u.get("pct_of_supply"),
            "recipient": u.get("recipient", ""), "source": source, "retrieved_at": now,
        } for u in unlocks if u.get("unlock_date")]
        if not rows:
            return 0
        return self.db.upsert_many(
            "token_unlocks", rows,
            conflict_columns=["asset_id", "unlock_date", "recipient"],
            update_columns=["tokens", "pct_of_supply", "retrieved_at"])

    def upcoming_unlocks(self, asset_id: int, *, as_of: date | str,
                         days_ahead: int = 90) -> list[dict[str, Any]]:
        start = to_date(as_of)
        end = date.fromordinal(start.toordinal() + int(days_ahead))
        return self.db.query(
            "SELECT * FROM token_unlocks WHERE asset_id=? AND unlock_date BETWEEN ? AND ? "
            "ORDER BY unlock_date", (asset_id, iso(start), iso(end)))


class OwnershipRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, asset_id: int, records: Iterable[Mapping[str, Any]], *,
              kind: str, source: str, source_tier: SourceTier) -> int:
        now = iso(utcnow())
        rows = [{
            "asset_id": asset_id, "kind": kind,
            "as_of_date": iso(to_date(r["as_of_date"])),
            "holder": r.get("holder", ""), "shares": r.get("shares"),
            "change_shares": r.get("change_shares"), "value_usd": r.get("value_usd"),
            "pct_of_float": r.get("pct_of_float"),
            "filed_date": iso(to_date(r.get("filed_date", r["as_of_date"]))),
            "source": source, "source_tier": source_tier.value, "retrieved_at": now,
        } for r in records if r.get("as_of_date")]
        if not rows:
            return 0
        return self.db.upsert_many(
            "ownership", rows,
            conflict_columns=["asset_id", "kind", "as_of_date", "holder", "filed_date"],
            update_columns=["shares", "change_shares", "value_usd", "pct_of_float",
                            "retrieved_at"])

    def recent(self, asset_id: int, kind: str, *, as_of: date | str | None = None,
               limit: int = 50) -> list[dict[str, Any]]:
        clauses, params = ["asset_id=?", "kind=?"], [asset_id, kind]
        if as_of is not None:
            clauses.append("filed_date <= ?")
            params.append(iso(to_date(as_of)))
        return self.db.query(
            f"SELECT * FROM ownership WHERE {' AND '.join(clauses)} "
            f"ORDER BY as_of_date DESC LIMIT {int(limit)}", params)


class DataSourceRepository:
    """Provider health: what works, what is failing, and how often."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def register(self, name: str, *, kind: str, source_tier: SourceTier,
                 base_url: str | None = None, requires_key: bool = False,
                 enabled: bool = True, notes: str | None = None) -> None:
        self.db.upsert("data_sources", {
            "name": name, "kind": kind, "source_tier": source_tier.value,
            "base_url": base_url, "requires_key": int(requires_key),
            "enabled": int(enabled), "notes": notes,
        }, conflict_columns=["name"],
            update_columns=["kind", "source_tier", "base_url", "requires_key",
                            "enabled", "notes"])

    def record_success(self, name: str) -> None:
        self.db.execute(
            "UPDATE data_sources SET last_success_at=?, success_count=success_count+1 "
            "WHERE name=?", (iso(utcnow()), name))

    def record_failure(self, name: str) -> None:
        self.db.execute(
            "UPDATE data_sources SET last_failure_at=?, failure_count=failure_count+1 "
            "WHERE name=?", (iso(utcnow()), name))

    def health(self) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM data_sources ORDER BY name")


class DataQualityRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, asset_id: int | None, *, scope: str, as_of: date | str,
              grade: DataQuality, score: float,
              issues: Sequence[Mapping[str, Any]]) -> int:
        return self.db.upsert("data_quality_reports", {
            "asset_id": asset_id, "scope": scope, "as_of": iso(to_date(as_of)),
            "grade": grade.value, "score": round(float(score), 4),
            "issues": dumps(list(issues)), "checked_at": iso(utcnow()),
        }, conflict_columns=["asset_id", "scope", "as_of"],
            update_columns=["grade", "score", "issues", "checked_at"])

    def latest(self, asset_id: int, scope: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM data_quality_reports WHERE asset_id=? AND scope=? "
            "ORDER BY as_of DESC LIMIT 1", (asset_id, scope))
        if row:
            row["issues"] = loads(row.get("issues"), [])
        return row
