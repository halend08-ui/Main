"""Repositories for engine output: scores, recommendations, predictions,
model versions, performance, calibration, alerts, the research queue,
portfolios and backtests.

Immutability rule: recommendation and prediction history is append-only.
Recomputing a day under a *new* model version writes a new row; it never
overwrites the row an earlier model produced, so "which model said this?"
always has an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.logging import get_logger
from research_engine.core.timeutil import iso, to_date, to_datetime, utcnow
from research_engine.core.types import (DataQuality, Horizon, OpportunityTier,
                                        Recommendation, RiskLevel)
from research_engine.storage.db import Database, dumps, loads

log = get_logger(__name__)


class ScoreRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, asset_id: int, *, as_of: date | str, total_score: float,
              tier: OpportunityTier, components: Mapping[str, Any],
              data_quality: DataQuality, coverage: float,
              model_version: str) -> int:
        return self.db.upsert("scores", {
            "asset_id": asset_id, "as_of": iso(to_date(as_of)),
            "total_score": round(float(total_score), 2), "tier": tier.value,
            "components": dumps(dict(components)), "data_quality": data_quality.value,
            "coverage": round(float(coverage), 3), "model_version": model_version,
            "computed_at": iso(utcnow()),
        }, conflict_columns=["asset_id", "as_of", "model_version"],
            update_columns=["total_score", "tier", "components", "data_quality",
                            "coverage", "computed_at"])

    def latest(self, asset_id: int,
               model_version: str | None = None) -> dict[str, Any] | None:
        params: list[Any] = [asset_id]
        sql = "SELECT * FROM scores WHERE asset_id=?"
        if model_version:
            sql += " AND model_version=?"
            params.append(model_version)
        sql += " ORDER BY as_of DESC LIMIT 1"
        row = self.db.query_one(sql, params)
        if row:
            row["components"] = loads(row.get("components"), {})
        return row

    def history(self, asset_id: int, limit: int = 120) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT as_of, total_score, tier, data_quality, model_version "
            "FROM scores WHERE asset_id=? ORDER BY as_of DESC LIMIT ?",
            (asset_id, int(limit)))
        return list(reversed(rows))

    def top(self, as_of: date | str, *, limit: int = 25,
            asset_class: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [iso(to_date(as_of))]
        extra = ""
        if asset_class:
            extra = "AND a.asset_class=?"
            params.append(asset_class)
        rows = self.db.query(
            f"SELECT s.*, a.symbol, a.name, a.asset_class, a.sector "
            f"FROM scores s JOIN assets a ON a.id=s.asset_id "
            f"WHERE s.as_of=? {extra} ORDER BY s.total_score DESC LIMIT {int(limit)}",
            params)
        for row in rows:
            row["components"] = loads(row.get("components"), {})
        return rows

    def as_of_map(self, as_of: date | str,
                  model_version: str | None = None) -> dict[int, float]:
        params: list[Any] = [iso(to_date(as_of))]
        sql = "SELECT asset_id, total_score FROM scores WHERE as_of=?"
        if model_version:
            sql += " AND model_version=?"
            params.append(model_version)
        return {int(r["asset_id"]): float(r["total_score"])
                for r in self.db.query(sql, params)}


class RecommendationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, asset_id: int, *, as_of: date | str,
              recommendation: Recommendation, confidence: float,
              horizon: Horizon, risk_level: RiskLevel,
              data_quality: DataQuality, model_version: str,
              price: float | None = None, score: float | None = None,
              tier: OpportunityTier | None = None,
              previous: Recommendation | None = None,
              fair_value: Mapping[str, float | None] | None = None,
              expected_return: Mapping[str, float | None] | None = None,
              prob_positive: float | None = None,
              rationale: Mapping[str, Any] | None = None,
              sell_conditions: Sequence[Any] = (),
              invalidation: Sequence[Any] = (),
              data_version: str | None = None) -> int:
        fv = dict(fair_value or {})
        er = dict(expected_return or {})
        return self.db.upsert("recommendations", {
            "asset_id": asset_id, "as_of": iso(to_date(as_of)),
            "recommendation": recommendation.value,
            "previous_recommendation": previous.value if previous else None,
            "tier": tier.value if tier else None,
            "score": None if score is None else round(float(score), 2),
            "confidence": round(float(confidence), 4),
            "horizon": horizon.value, "price": price,
            "fair_value_bear": fv.get("bear"), "fair_value_base": fv.get("base"),
            "fair_value_bull": fv.get("bull"),
            "expected_return_bear": er.get("bear"), "expected_return_base": er.get("base"),
            "expected_return_bull": er.get("bull"),
            "prob_positive": prob_positive,
            "risk_level": risk_level.value, "data_quality": data_quality.value,
            "rationale": dumps(dict(rationale or {})),
            "sell_conditions": dumps(list(sell_conditions)),
            "invalidation": dumps(list(invalidation)),
            "model_version": model_version, "data_version": data_version,
            "computed_at": iso(utcnow()),
        }, conflict_columns=["asset_id", "as_of", "model_version"],
            update_columns=["recommendation", "previous_recommendation", "tier",
                            "score", "confidence", "horizon", "price",
                            "fair_value_bear", "fair_value_base", "fair_value_bull",
                            "expected_return_bear", "expected_return_base",
                            "expected_return_bull", "prob_positive", "risk_level",
                            "data_quality", "rationale", "sell_conditions",
                            "invalidation", "data_version", "computed_at"])

    def latest(self, asset_id: int, *, before: date | str | None = None,
               model_version: str | None = None) -> dict[str, Any] | None:
        clauses, params = ["asset_id=?"], [asset_id]
        if before is not None:
            clauses.append("as_of < ?")
            params.append(iso(to_date(before)))
        if model_version:
            clauses.append("model_version=?")
            params.append(model_version)
        row = self.db.query_one(
            f"SELECT * FROM recommendations WHERE {' AND '.join(clauses)} "
            f"ORDER BY as_of DESC LIMIT 1", params)
        return _decode_rec(row) if row else None

    def history(self, asset_id: int, limit: int = 60) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM recommendations WHERE asset_id=? ORDER BY as_of DESC LIMIT ?",
            (asset_id, int(limit)))
        return [_decode_rec(r) for r in reversed(rows)]

    def on_date(self, as_of: date | str, *, limit: int = 500,
                recommendation: Recommendation | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [iso(to_date(as_of))]
        extra = ""
        if recommendation:
            extra = "AND r.recommendation=?"
            params.append(recommendation.value)
        rows = self.db.query(
            f"SELECT r.*, a.symbol, a.name, a.asset_class, a.sector "
            f"FROM recommendations r JOIN assets a ON a.id=r.asset_id "
            f"WHERE r.as_of=? {extra} ORDER BY r.score DESC LIMIT {int(limit)}", params)
        return [_decode_rec(r) for r in rows]

    def changes(self, as_of: date | str, previous: date | str) -> list[dict[str, Any]]:
        """Assets whose recommendation differs between two dates."""
        rows = self.db.query(
            "SELECT cur.asset_id, a.symbol, a.name, a.asset_class, "
            "       prev.recommendation AS old_rec, cur.recommendation AS new_rec, "
            "       prev.score AS old_score, cur.score AS new_score, "
            "       cur.confidence, cur.price "
            "FROM recommendations cur "
            "JOIN recommendations prev ON prev.asset_id=cur.asset_id AND prev.as_of=? "
            "JOIN assets a ON a.id=cur.asset_id "
            "WHERE cur.as_of=? AND cur.recommendation <> prev.recommendation",
            (iso(to_date(previous)), iso(to_date(as_of))))
        return rows


def _decode_rec(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["rationale"] = loads(out.get("rationale"), {})
    out["sell_conditions"] = loads(out.get("sell_conditions"), [])
    out["invalidation"] = loads(out.get("invalidation"), [])
    return out


class SignalRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write_many(self, asset_id: int, as_of: date | str,
                   signals: Iterable[Mapping[str, Any]],
                   model_version: str | None = None) -> int:
        rows = [{
            "asset_id": asset_id, "as_of": iso(to_date(as_of)),
            "family": s["family"], "name": s["name"], "value": s.get("value"),
            "direction": s.get("direction"), "strength": s.get("strength"),
            "quality": DataQuality(s.get("quality", DataQuality.FAIR)).value
            if not isinstance(s.get("quality"), DataQuality) else s["quality"].value,
            "model_version": model_version,
        } for s in signals]
        if not rows:
            return 0
        return self.db.upsert_many(
            "signals", rows, conflict_columns=["asset_id", "as_of", "family", "name"],
            update_columns=["value", "direction", "strength", "quality", "model_version"])

    def for_asset(self, asset_id: int, as_of: date | str) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM signals WHERE asset_id=? AND as_of=? ORDER BY family, name",
            (asset_id, iso(to_date(as_of))))


class PredictionRepository:
    """Every prediction the engine makes is stored here before it can be judged."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, asset_id: int, *, as_of: date | str, horizon: Horizon,
              due_at: date | str, price_at_prediction: float,
              recommendation: Recommendation, confidence: float,
              asset_class: str, model_version: str,
              data_quality: DataQuality,
              prob_positive: float | None = None,
              expected_return: float | None = None,
              expected_downside: float | None = None,
              factors: Mapping[str, float] | None = None,
              regime: str | None = None, sector: str | None = None,
              recommendation_id: int | None = None,
              data_version: str | None = None) -> int:
        return self.db.insert("predictions", {
            "asset_id": asset_id, "recommendation_id": recommendation_id,
            "created_at": iso(utcnow()), "as_of": iso(to_date(as_of)),
            "horizon": horizon.value, "due_at": iso(to_date(due_at)),
            "price_at_prediction": float(price_at_prediction),
            "recommendation": recommendation.value,
            "confidence": round(float(confidence), 4),
            "prob_positive": prob_positive, "expected_return": expected_return,
            "expected_downside": expected_downside,
            "factors": dumps(dict(factors or {})), "regime": regime, "sector": sector,
            "asset_class": asset_class, "model_version": model_version,
            "data_version": data_version, "data_quality": data_quality.value,
            "evaluated": 0,
        })

    def due(self, as_of: date | str, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT p.*, a.symbol FROM predictions p JOIN assets a ON a.id=p.asset_id "
            "WHERE p.evaluated=0 AND p.due_at <= ? ORDER BY p.due_at LIMIT ?",
            (iso(to_date(as_of)), int(limit)))
        for row in rows:
            row["factors"] = loads(row.get("factors"), {})
        return rows

    def record_outcome(self, prediction_id: int, *, price_at_due: float | None,
                       actual_return: float | None,
                       benchmark_return: float | None = None,
                       max_drawdown: float | None = None,
                       realized_vol: float | None = None,
                       hit: bool | None = None,
                       thesis_outcome: str = "open",
                       failure_reason: str | None = None,
                       factor_attribution: Mapping[str, float] | None = None,
                       data_quality: DataQuality | None = None) -> None:
        excess = None
        if actual_return is not None and benchmark_return is not None:
            excess = actual_return - benchmark_return
        self.db.upsert("prediction_outcomes", {
            "prediction_id": prediction_id, "evaluated_at": iso(utcnow()),
            "price_at_due": price_at_due, "actual_return": actual_return,
            "benchmark_return": benchmark_return, "excess_return": excess,
            "max_drawdown": max_drawdown, "realized_vol": realized_vol,
            "hit": None if hit is None else int(hit),
            "thesis_outcome": thesis_outcome, "failure_reason": failure_reason,
            "factor_attribution": dumps(dict(factor_attribution or {})),
            "data_quality": data_quality.value if data_quality else None,
        }, conflict_columns=["prediction_id"],
            update_columns=["evaluated_at", "price_at_due", "actual_return",
                            "benchmark_return", "excess_return", "max_drawdown",
                            "realized_vol", "hit", "thesis_outcome", "failure_reason",
                            "factor_attribution", "data_quality"])
        self.db.execute("UPDATE predictions SET evaluated=1 WHERE id=?", (prediction_id,))

    def evaluated(self, *, model_version: str | None = None,
                  horizon: Horizon | str | None = None,
                  since: date | str | None = None,
                  limit: int = 10_000) -> list[dict[str, Any]]:
        clauses, params = ["p.evaluated=1"], []
        if model_version:
            clauses.append("p.model_version=?")
            params.append(model_version)
        if horizon:
            clauses.append("p.horizon=?")
            params.append(horizon.value if isinstance(horizon, Horizon) else horizon)
        if since:
            clauses.append("p.as_of >= ?")
            params.append(iso(to_date(since)))
        rows = self.db.query(
            f"SELECT p.*, o.actual_return, o.benchmark_return, o.excess_return, "
            f"o.max_drawdown, o.hit, o.thesis_outcome, o.failure_reason "
            f"FROM predictions p JOIN prediction_outcomes o ON o.prediction_id=p.id "
            f"WHERE {' AND '.join(clauses)} ORDER BY p.as_of DESC LIMIT {int(limit)}",
            params)
        for row in rows:
            row["factors"] = loads(row.get("factors"), {})
        return rows

    def open_count(self) -> int:
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM predictions WHERE evaluated=0") or 0)


class ModelRegistryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def register(self, version: str, *, family: str,
                 parameters: Mapping[str, Any],
                 features: Sequence[str] = (),
                 train_start: date | str | None = None,
                 train_end: date | str | None = None,
                 test_start: date | str | None = None,
                 test_end: date | str | None = None,
                 validation_metrics: Mapping[str, Any] | None = None,
                 test_metrics: Mapping[str, Any] | None = None,
                 data_sources: Sequence[str] = (),
                 code_fingerprint: str | None = None,
                 parent_version: str | None = None,
                 status: str = "candidate", notes: str | None = None) -> str:
        self.db.upsert("model_versions", {
            "version": version, "family": family, "created_at": iso(utcnow()),
            "parent_version": parent_version,
            "train_start": iso(to_date(train_start)) if train_start else None,
            "train_end": iso(to_date(train_end)) if train_end else None,
            "test_start": iso(to_date(test_start)) if test_start else None,
            "test_end": iso(to_date(test_end)) if test_end else None,
            "features": dumps(list(features)), "parameters": dumps(dict(parameters)),
            "validation_metrics": dumps(dict(validation_metrics or {})),
            "test_metrics": dumps(dict(test_metrics or {})),
            "data_sources": dumps(list(data_sources)),
            "code_fingerprint": code_fingerprint, "status": status, "notes": notes,
        }, conflict_columns=["version"],
            update_columns=["validation_metrics", "test_metrics", "status", "notes",
                            "test_start", "test_end"])
        return version

    def get(self, version: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM model_versions WHERE version=?", (version,))
        return _decode_model(row) if row else None

    def active(self, family: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM model_versions WHERE family=? AND status='active' "
            "ORDER BY created_at DESC LIMIT 1", (family,))
        return _decode_model(row) if row else None

    def list(self, family: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM model_versions"
        params: list[Any] = []
        if family:
            sql += " WHERE family=?"
            params.append(family)
        sql += " ORDER BY created_at DESC"
        return [_decode_model(r) for r in self.db.query(sql, params)]

    def promote(self, version: str) -> None:
        """Activate ``version`` and retire the previous active model.

        Retired rows are kept forever: historical recommendations must remain
        attributable to the exact model that produced them.
        """
        model = self.get(version)
        if not model:
            raise ValueError(f"unknown model version {version}")
        with self.db.transaction() as conn:
            conn.execute("UPDATE model_versions SET status='retired' "
                         "WHERE family=? AND status='active'", (model["family"],))
            conn.execute("UPDATE model_versions SET status='active' WHERE version=?",
                         (version,))

    def record_performance(self, model_version: str, *, bucket_kind: str,
                           bucket_value: str, samples: int,
                           horizon: str | None = None,
                           **metrics: Any) -> int:
        allowed = {"hit_rate", "avg_return", "avg_excess", "sharpe", "sortino",
                   "max_drawdown", "brier", "calibration_error", "profit_factor",
                   "avg_winner", "avg_loser"}
        payload = {k: metrics.get(k) for k in allowed}
        extra = {k: v for k, v in metrics.items() if k not in allowed}
        return self.db.upsert("model_performance", {
            "model_version": model_version, "computed_at": iso(utcnow()),
            "bucket_kind": bucket_kind, "bucket_value": bucket_value,
            "horizon": horizon, "samples": int(samples),
            **payload, "detail": dumps(extra),
        }, conflict_columns=["model_version", "computed_at", "bucket_kind",
                             "bucket_value", "horizon"])

    def performance(self, model_version: str,
                    bucket_kind: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [model_version]
        sql = "SELECT * FROM model_performance WHERE model_version=?"
        if bucket_kind:
            sql += " AND bucket_kind=?"
            params.append(bucket_kind)
        sql += " ORDER BY computed_at DESC"
        return self.db.query(sql, params)

    def write_calibration(self, model_version: str, bins: Sequence[Mapping[str, Any]],
                          horizon: str | None = None) -> int:
        now = iso(utcnow())
        rows = [{
            "model_version": model_version, "computed_at": now, "horizon": horizon,
            "bin_low": float(b["bin_low"]), "bin_high": float(b["bin_high"]),
            "predicted_mean": b.get("predicted_mean"),
            "observed_rate": b.get("observed_rate"), "samples": int(b.get("samples", 0)),
        } for b in bins]
        if not rows:
            return 0
        cols = list(rows[0].keys())
        return self.db.execute_many(
            f"INSERT INTO calibration ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            [[r[c] for c in cols] for r in rows])

    def latest_calibration(self, model_version: str,
                           horizon: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [model_version]
        sql = ("SELECT * FROM calibration WHERE model_version=? AND computed_at="
               "(SELECT MAX(computed_at) FROM calibration WHERE model_version=?")
        params.append(model_version)
        if horizon:
            sql += " AND horizon=?"
            params.append(horizon)
        sql += ")"
        if horizon:
            sql += " AND horizon=?"
            params.append(horizon)
        sql += " ORDER BY bin_low"
        return self.db.query(sql, params)


def _decode_model(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("features", "parameters", "validation_metrics", "test_metrics",
                "data_sources"):
        out[key] = loads(out.get(key), {} if "metrics" in key or key == "parameters" else [])
    return out


class ResearchQueueRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(self, asset_id: int, *, priority: float, reason: str,
                stage: int = 1, trigger: str | None = None) -> int:
        return self.db.upsert("research_queue", {
            "asset_id": asset_id, "queued_at": iso(utcnow()),
            "priority": round(float(priority), 4), "stage": int(stage),
            "reason": reason, "trigger": trigger, "status": "pending",
        }, conflict_columns=["asset_id", "stage", "status"],
            update_columns=["priority", "reason", "trigger", "queued_at"])

    def pending(self, *, stage: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params: list[Any] = []
        sql = ("SELECT q.*, a.symbol, a.name, a.asset_class FROM research_queue q "
               "JOIN assets a ON a.id=q.asset_id WHERE q.status='pending'")
        if stage is not None:
            sql += " AND q.stage=?"
            params.append(int(stage))
        sql += f" ORDER BY q.priority DESC LIMIT {int(limit)}"
        return self.db.query(sql, params)

    def complete(self, queue_id: int, *, promoted_to_stage: int | None = None) -> None:
        self.db.execute(
            "UPDATE research_queue SET status='done', last_analyzed_at=? WHERE id=?",
            (iso(utcnow()), queue_id))
        if promoted_to_stage:
            row = self.db.query_one("SELECT asset_id, priority, reason FROM research_queue "
                                    "WHERE id=?", (queue_id,))
            if row:
                self.enqueue(int(row["asset_id"]), priority=float(row["priority"]),
                             reason=f"promoted from stage {promoted_to_stage - 1}",
                             stage=promoted_to_stage, trigger="funnel")


class AlertRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, *, kind: str, severity: str, title: str,
              asset_id: int | None = None, detail: str | None = None,
              payload: Mapping[str, Any] | None = None) -> int:
        return self.db.insert("alerts", {
            "created_at": iso(utcnow()), "asset_id": asset_id, "kind": kind,
            "severity": severity, "title": title, "detail": detail,
            "payload": dumps(dict(payload or {})), "acknowledged": 0,
        })

    def recent(self, *, limit: int = 100,
               since: datetime | str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        sql = ("SELECT al.*, a.symbol FROM alerts al "
               "LEFT JOIN assets a ON a.id=al.asset_id WHERE 1=1")
        if since:
            sql += " AND al.created_at >= ?"
            params.append(iso(to_datetime(since)))
        sql += f" ORDER BY al.created_at DESC LIMIT {int(limit)}"
        rows = self.db.query(sql, params)
        for row in rows:
            row["payload"] = loads(row.get("payload"), {})
        return rows


class ReportRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, *, kind: str, as_of: date | str, title: str,
              body_markdown: str, asset_id: int | None = None,
              payload: Mapping[str, Any] | None = None,
              model_version: str | None = None) -> int:
        return self.db.insert("research_reports", {
            "asset_id": asset_id, "kind": kind, "as_of": iso(to_date(as_of)),
            "title": title, "body_markdown": body_markdown,
            "payload": dumps(dict(payload or {})), "model_version": model_version,
            "created_at": iso(utcnow()),
        })

    def latest(self, kind: str, asset_id: int | None = None) -> dict[str, Any] | None:
        params: list[Any] = [kind]
        sql = "SELECT * FROM research_reports WHERE kind=?"
        if asset_id is not None:
            sql += " AND asset_id=?"
            params.append(asset_id)
        sql += " ORDER BY as_of DESC, id DESC LIMIT 1"
        row = self.db.query_one(sql, params)
        if row:
            row["payload"] = loads(row.get("payload"), {})
        return row

    def list(self, kind: str, limit: int = 30) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT id, kind, as_of, title, created_at, asset_id FROM research_reports "
            "WHERE kind=? ORDER BY as_of DESC LIMIT ?", (kind, int(limit)))


class BacktestRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def write(self, *, name: str, config: Mapping[str, Any], start: date | str,
              end: date | str, metrics: Mapping[str, Any],
              benchmark_metrics: Mapping[str, Any] | None = None,
              folds: Sequence[Mapping[str, Any]] = (),
              warnings: Sequence[str] = (), model_version: str | None = None,
              code_fingerprint: str | None = None) -> int:
        return self.db.insert("backtests", {
            "name": name, "created_at": iso(utcnow()), "model_version": model_version,
            "config": dumps(dict(config)), "start_date": iso(to_date(start)),
            "end_date": iso(to_date(end)), "metrics": dumps(dict(metrics)),
            "benchmark_metrics": dumps(dict(benchmark_metrics or {})),
            "folds": dumps(list(folds)), "warnings": dumps(list(warnings)),
            "code_fingerprint": code_fingerprint,
        })

    def write_trades(self, backtest_id: int, trades: Iterable[Mapping[str, Any]]) -> int:
        rows = [{
            "backtest_id": backtest_id, "asset_id": t.get("asset_id"),
            "symbol": t["symbol"], "side": t.get("side", "long"),
            "entry_date": iso(to_date(t["entry_date"])),
            "exit_date": iso(to_date(t["exit_date"])) if t.get("exit_date") else None,
            "entry_price": float(t["entry_price"]),
            "exit_price": t.get("exit_price"), "quantity": float(t["quantity"]),
            "costs": float(t.get("costs", 0.0)), "pnl": t.get("pnl"),
            "return_pct": t.get("return_pct"), "reason": t.get("reason"),
        } for t in trades]
        if not rows:
            return 0
        cols = list(rows[0].keys())
        return self.db.execute_many(
            f"INSERT INTO backtest_trades ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            [[r[c] for c in cols] for r in rows])

    def latest(self, name: str | None = None) -> dict[str, Any] | None:
        params: list[Any] = []
        sql = "SELECT * FROM backtests"
        if name:
            sql += " WHERE name=?"
            params.append(name)
        sql += " ORDER BY created_at DESC LIMIT 1"
        row = self.db.query_one(sql, params)
        if row:
            for key in ("config", "metrics", "benchmark_metrics", "folds", "warnings"):
                row[key] = loads(row.get(key), {})
        return row


class PortfolioRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure(self, name: str, *, cash: float = 0.0,
               base_currency: str = "USD", notes: str | None = None) -> int:
        existing = self.db.query_one("SELECT id FROM portfolios WHERE name=?", (name,))
        if existing:
            return int(existing["id"])
        return self.db.insert("portfolios", {
            "name": name, "created_at": iso(utcnow()), "base_currency": base_currency,
            "cash": float(cash), "notes": notes, "is_hypothetical": 1,
        })

    def open_position(self, portfolio_id: int, asset_id: int, *,
                      opened_at: date | str, entry_price: float, quantity: float,
                      thesis: str | None = None, target_price: float | None = None,
                      stop_price: float | None = None,
                      max_loss_pct: float | None = None,
                      horizon: str | None = None) -> int:
        return self.db.insert("positions", {
            "portfolio_id": portfolio_id, "asset_id": asset_id,
            "opened_at": iso(to_date(opened_at)), "entry_price": float(entry_price),
            "quantity": float(quantity), "thesis": thesis, "target_price": target_price,
            "stop_price": stop_price, "max_loss_pct": max_loss_pct, "horizon": horizon,
            "status": "open",
        })

    def close_position(self, position_id: int, *, closed_at: date | str,
                       exit_price: float, reason: str) -> None:
        self.db.execute(
            "UPDATE positions SET status='closed', closed_at=?, exit_price=?, "
            "exit_reason=? WHERE id=?",
            (iso(to_date(closed_at)), float(exit_price), reason, position_id))

    def positions(self, portfolio_id: int, *, open_only: bool = True) -> list[dict[str, Any]]:
        sql = ("SELECT p.*, a.symbol, a.name, a.asset_class, a.sector "
               "FROM positions p JOIN assets a ON a.id=p.asset_id WHERE p.portfolio_id=?")
        if open_only:
            sql += " AND p.status='open'"
        return self.db.query(sql + " ORDER BY p.opened_at", (portfolio_id,))

    def cash(self, portfolio_id: int) -> float:
        return float(self.db.scalar("SELECT cash FROM portfolios WHERE id=?",
                                    (portfolio_id,)) or 0.0)

    def set_cash(self, portfolio_id: int, cash: float) -> None:
        self.db.execute("UPDATE portfolios SET cash=? WHERE id=?",
                        (float(cash), portfolio_id))
