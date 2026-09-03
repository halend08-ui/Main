"""Read-only research API.

Security posture, deliberately narrow:

* **Read-only.** There are no mutating endpoints. The API cannot trigger
  ingestion, change configuration, promote a model or place an order.
* **No secrets.** Provider credentials are never returned; the providers
  endpoint reports availability and the *name* of the environment variable a
  provider expects, never its value.
* **Local by default.** Binds to 127.0.0.1 and allows CORS only for the
  configured dashboard origin.

FastAPI is imported lazily so the core package installs without it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Mapping

from research_engine import DISCLAIMER, __version__
from research_engine.core.errors import DataUnavailable
from research_engine.core.logging import get_logger
from research_engine.core.timeutil import to_date

log = get_logger(__name__)


def build_app(settings: Any, services: Mapping[str, Any] | None = None):
    """Construct the FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.middleware.cors import CORSMiddleware
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("the API requires fastapi: pip install "
                           "'research-engine[api]'") from exc

    if services is None:
        from research_engine.cli import _services
        services = _services(settings)
    repos = services["repos"]
    data = services["data"]

    app = FastAPI(title="Investment Research Engine",
                  version=__version__,
                  description=("Read-only research API. " + DISCLAIMER))
    origins = list(settings.get("api.cors_origins") or [])
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_credentials=False, allow_methods=["GET"],
                       allow_headers=["*"])

    def _latest_as_of() -> date | None:
        return repos["recommendations"].latest_as_of()

    # -- meta --------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        counts = services["db"].table_counts()
        return {"status": "ok", "version": __version__,
                "assets": counts.get("assets", 0),
                "prices": counts.get("prices_daily", 0),
                "recommendations": counts.get("recommendations", 0),
                "latest_analysis": (_latest_as_of().isoformat()
                                    if _latest_as_of() else None),
                "disclaimer": DISCLAIMER}

    @app.get("/api/providers")
    def providers() -> list[dict[str, Any]]:
        """Provider availability. Never returns credential values."""
        from research_engine.ingestion.factory import build_registry
        described = build_registry(settings).describe()
        health_rows = {r["name"]: r for r in repos["sources"].health()}
        for provider in described:
            provider.pop("api_key", None)
            provider["health"] = health_rows.get(provider["name"], {})
        return described

    # -- market ------------------------------------------------------------
    @app.get("/api/market")
    def market() -> dict[str, Any]:
        as_of = _latest_as_of() or date.today()
        report = repos["reports"].latest("daily")
        payload = (report or {}).get("payload") or {}
        return {"as_of": as_of.isoformat(),
                "market": payload.get("market", {}),
                "warnings": payload.get("warnings", []),
                "disclaimer": DISCLAIMER}

    @app.get("/api/opportunities")
    def opportunities(limit: int = Query(25, ge=1, le=200),
                      asset_class: str | None = None,
                      min_score: float | None = None,
                      recommendation: str | None = None) -> dict[str, Any]:
        as_of = _latest_as_of()
        if as_of is None:
            return {"as_of": None, "items": [],
                    "note": "no analysis has been run yet"}
        rows = repos["recommendations"].on_date(as_of, limit=500)
        items = []
        for row in rows:
            if asset_class and row.get("asset_class") != asset_class:
                continue
            if min_score is not None and (row.get("score") or 0) < min_score:
                continue
            if recommendation and row.get("recommendation") != recommendation:
                continue
            items.append(_recommendation_summary(row))
        return {"as_of": as_of.isoformat(), "items": items[:limit],
                "total_matching": len(items), "disclaimer": DISCLAIMER}

    @app.get("/api/screen")
    def screen(min_score: float = 0, max_risk: str | None = None,
               sector: str | None = None, asset_class: str | None = None,
               min_market_cap: float | None = None,
               min_confidence: float = 0.0,
               limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        """Filter the analysed universe -- the dashboard screener."""
        as_of = _latest_as_of()
        if as_of is None:
            return {"items": [], "note": "no analysis has been run yet"}
        from research_engine.core.types import RiskLevel
        order = [r.value for r in RiskLevel]
        max_index = order.index(max_risk) if max_risk in order else len(order)

        # One lookup for the whole universe rather than one query per row:
        # the screener runs over the full analysed set on every keystroke.
        market_caps = {a.symbol: a.market_cap_usd
                       for a in repos["assets"].list(active_only=False, limit=10_000)}

        items = []
        for row in repos["recommendations"].on_date(as_of, limit=1000):
            if (row.get("score") or 0) < min_score:
                continue
            if (row.get("confidence") or 0) < min_confidence:
                continue
            if sector and row.get("sector") != sector:
                continue
            if asset_class and row.get("asset_class") != asset_class:
                continue
            risk = row.get("risk_level")
            if risk in order and order.index(risk) > max_index:
                continue
            if min_market_cap:
                cap = market_caps.get(row["symbol"])
                if cap is None or cap < min_market_cap:
                    continue
            items.append(_recommendation_summary(row))
        items.sort(key=lambda i: i.get("score") or 0, reverse=True)
        return {"as_of": as_of.isoformat(), "items": items[:limit],
                "total_matching": len(items)}

    # -- assets ------------------------------------------------------------
    @app.get("/api/assets")
    def assets(asset_class: str | None = None,
               limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return [{"symbol": a.symbol, "name": a.name,
                 "asset_class": a.asset_class.value, "sector": a.sector,
                 "market_cap_usd": a.market_cap_usd, "is_active": a.is_active,
                 "quality_grade": a.quality_grade}
                for a in repos["assets"].list(asset_class=asset_class, limit=limit)]

    @app.get("/api/asset/{symbol}")
    def asset_detail(symbol: str) -> dict[str, Any]:
        record = repos["assets"].get(symbol.upper())
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown asset {symbol}")
        latest = repos["recommendations"].latest(record.id)
        history = repos["scores"].history(record.id, limit=180)
        recommendations = repos["recommendations"].history(record.id, limit=30)
        news = repos["news"].recent(record.id, limit=20)
        quality = repos["quality"].latest(record.id, "prices")
        return {
            "asset": {"symbol": record.symbol, "name": record.name,
                      "asset_class": record.asset_class.value,
                      "sector": record.sector, "industry": record.industry,
                      "exchange": record.exchange, "country": record.country,
                      "market_cap_usd": record.market_cap_usd,
                      "quality_grade": record.quality_grade,
                      "is_active": record.is_active,
                      "delisted_date": record.delisted_date.isoformat()
                      if record.delisted_date else None},
            "recommendation": _recommendation_detail(latest) if latest else None,
            "score_history": history,
            "recommendation_history": [
                {"as_of": r["as_of"], "recommendation": r["recommendation"],
                 "score": r["score"], "confidence": r["confidence"],
                 "price": r["price"], "model_version": r["model_version"]}
                for r in recommendations],
            "news": [{"headline": n.headline, "url": n.url,
                      "published_at": n.published_at.isoformat(),
                      "source": n.source, "source_tier": n.source_tier.value}
                     for n in news],
            "data_quality": quality,
            "disclaimer": DISCLAIMER,
        }

    @app.get("/api/asset/{symbol}/prices")
    def asset_prices(symbol: str, days: int = Query(500, ge=10, le=5000)
                     ) -> dict[str, Any]:
        record = repos["assets"].get(symbol.upper())
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown asset {symbol}")
        try:
            series = repos["prices"].series(record.id, record.symbol, limit=days)
        except DataUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        rows = series.to_rows()
        indicators: dict[str, list[float | None]] = {}
        if len(series) >= 200:
            from research_engine.features import technical as TECH
            computed = TECH.compute_all(series)
            for key in ("sma_50", "sma_200", "rsi", "bb_upper", "bb_lower"):
                values = computed.get(key)
                if values is not None:
                    indicators[key] = [None if v != v else round(float(v), 4)
                                       for v in values]
        return {"symbol": record.symbol, "bars": rows, "indicators": indicators}

    @app.get("/api/asset/{symbol}/memo")
    def asset_memo(symbol: str) -> dict[str, Any]:
        record = repos["assets"].get(symbol.upper())
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown asset {symbol}")
        memo = repos["reports"].latest("memo", asset_id=record.id)
        if not memo:
            raise HTTPException(status_code=404,
                                detail=f"no memo has been generated for {symbol}")
        return {"symbol": record.symbol, "as_of": memo["as_of"],
                "markdown": memo["body_markdown"],
                "model_version": memo.get("model_version")}

    # -- portfolio / performance -------------------------------------------
    @app.get("/api/portfolio")
    def portfolio() -> dict[str, Any]:
        """Portfolio risk computed live, not read from the last daily report.

        Positions can be opened between runs, and a stale report showing "no
        limit breaches" for a portfolio that now has three is worse than showing
        nothing at all.
        """
        from research_engine.analysis import risk as RK
        from research_engine.core.errors import DataUnavailable as _Unavailable

        as_of = _latest_as_of() or date.today()
        positions = data.open_positions(as_of)
        if not positions:
            return {"as_of": as_of.isoformat(), "positions": [], "risk": {},
                    "breaches": [],
                    "note": "hypothetical portfolio: this system never places orders"}

        weights: dict[str, float] = {}
        series_by_symbol: dict[str, Any] = {}
        for position in positions:
            price = position.get("price") or position.get("entry_price") or 0.0
            weights[position["symbol"]] = price * float(position.get("quantity", 0))
            try:
                series_by_symbol[position["symbol"]] = data.series(
                    position["symbol"], as_of=as_of)
            except (_Unavailable, KeyError):
                continue

        risk = RK.portfolio_risk(weights, series_by_symbol)
        breaches = RK.limit_breaches(
            weights,
            sectors={p["symbol"]: p.get("sector", "Unknown") for p in positions},
            asset_classes={p["symbol"]: p.get("asset_class", "equity")
                           for p in positions},
            max_position=float(settings.get("risk.max_position_weight", 0.12)),
            max_sector=float(settings.get("risk.max_sector_weight", 0.30)),
            max_crypto=float(settings.get("risk.max_crypto_weight", 0.20)),
            concentration_warning_hhi=float(
                settings.get("risk.concentration_warning_hhi", 0.18)))
        return {"as_of": as_of.isoformat(), "positions": positions, "risk": risk,
                "breaches": breaches,
                "note": "hypothetical portfolio: this system never places orders"}

    @app.get("/api/performance")
    def performance(model_version: str | None = None) -> dict[str, Any]:
        report = repos["reports"].latest("daily")
        payload = (report or {}).get("payload") or {}
        version = model_version or "scoring_v1"
        return {"model_version": version,
                "summary": payload.get("model_performance", {}),
                "buckets": repos["models"].performance(version),
                "calibration": repos["models"].latest_calibration(version),
                "open_predictions": repos["predictions"].open_count(),
                "caveat": ("Performance is measured on the engine's own stored "
                           "predictions. Backtests are simulations and past "
                           "results do not guarantee future ones.")}

    @app.get("/api/queue")
    def research_queue(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        return repos["queue"].pending(limit=limit)

    @app.get("/api/alerts")
    def alerts(limit: int = Query(50, ge=1, le=500),
               days: int = Query(7, ge=1, le=90)) -> list[dict[str, Any]]:
        from research_engine.core.timeutil import utcnow
        return repos["alerts"].recent(limit=limit,
                                      since=utcnow() - timedelta(days=days))

    @app.get("/api/report")
    def report(kind: str = "daily") -> dict[str, Any]:
        stored = repos["reports"].latest(kind)
        if not stored:
            raise HTTPException(status_code=404, detail=f"no {kind} report stored")
        return {"kind": kind, "as_of": stored["as_of"],
                "title": stored["title"], "markdown": stored["body_markdown"],
                "payload": stored.get("payload", {})}

    return app


def _round(value: Any, digits: int) -> Any:
    """Round for presentation.

    A fair value quoted to thirteen decimal places implies precision the model
    does not have; false precision is a documented failure mode, so the API
    rounds at the boundary rather than shipping raw floats to the client.
    """
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


def _recommendation_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": row.get("symbol"), "name": row.get("name"),
        "asset_class": row.get("asset_class"), "sector": row.get("sector"),
        "recommendation": row.get("recommendation"),
        "score": _round(row.get("score"), 1),
        "confidence": _round(row.get("confidence"), 3),
        "price": _round(row.get("price"), 4),
        "risk_level": row.get("risk_level"), "data_quality": row.get("data_quality"),
        "horizon": row.get("horizon"),
        "fair_value": {"bear": _round(row.get("fair_value_bear"), 2),
                       "base": _round(row.get("fair_value_base"), 2),
                       "bull": _round(row.get("fair_value_bull"), 2)},
        "expected_return": {"bear": _round(row.get("expected_return_bear"), 3),
                            "base": _round(row.get("expected_return_base"), 3),
                            "bull": _round(row.get("expected_return_bull"), 3)},
        "probability_positive": _round(row.get("prob_positive"), 3),
        "model_version": row.get("model_version"), "as_of": row.get("as_of"),
    }


def _recommendation_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    summary = _recommendation_summary(row)
    rationale = row.get("rationale") or {}
    summary.update({
        "evidence": rationale.get("evidence", []),
        "bear_case": rationale.get("bear_case", ""),
        "bull_case": rationale.get("bull_case", ""),
        "gates_failed": rationale.get("gates_failed", []),
        "sell_conditions": row.get("sell_conditions", []),
        "invalidation": row.get("invalidation", []),
    })
    return summary


def serve(settings: Any, *, host: str = "127.0.0.1", port: int = 8000) -> int:
    try:
        import uvicorn
    except ImportError:  # pragma: no cover - optional dependency
        print("the API requires uvicorn: pip install 'research-engine[api]'")
        return 1
    app = build_app(settings)
    log.info("starting read-only API", host=host, port=port)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
