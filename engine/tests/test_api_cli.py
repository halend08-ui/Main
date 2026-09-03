"""API and CLI tests. The API is exercised through FastAPI's TestClient, with
no network and no live server."""

import datetime as dt
import json

import pytest

from research_engine.api.server import build_app
from research_engine.cli import build_parser, main
from research_engine.pipeline.daily import DailyPipeline
from research_engine.pipeline.data_access import RepositoryDataAccess
from research_engine.storage.analysis_repos import (AlertRepository,
                                                    ModelRegistryRepository,
                                                    PortfolioRepository,
                                                    PredictionRepository,
                                                    RecommendationRepository,
                                                    ReportRepository,
                                                    ResearchQueueRepository,
                                                    ScoreRepository)
from research_engine.storage.reference_repos import (CryptoMetricRepository,
                                                     DataQualityRepository,
                                                     DataSourceRepository,
                                                     MacroRepository, NewsRepository)
from research_engine.storage.repositories import (AssetRepository,
                                                  FundamentalRepository,
                                                  PriceRepository)
from research_engine.core.types import SourceTier
from tests.conftest import make_prices
from tests.test_features import COMPOUNDER

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture()
def api(db, settings):
    repos = {
        "assets": AssetRepository(db), "prices": PriceRepository(db),
        "fundamentals": FundamentalRepository(db), "news": NewsRepository(db),
        "macro": MacroRepository(db), "crypto": CryptoMetricRepository(db),
        "sources": DataSourceRepository(db), "quality": DataQualityRepository(db),
        "scores": ScoreRepository(db), "recommendations": RecommendationRepository(db),
        "predictions": PredictionRepository(db), "models": ModelRegistryRepository(db),
        "queue": ResearchQueueRepository(db), "alerts": AlertRepository(db),
        "reports": ReportRepository(db), "portfolio": PortfolioRepository(db),
    }
    start = dt.date(2023, 1, 2)
    as_of = None
    for i, symbol in enumerate(["SPY", "ACME", "BETA"]):
        asset_id = repos["assets"].upsert(symbol=symbol, asset_class="equity",
                                          name=f"{symbol} Inc", sector="Technology",
                                          market_cap_usd=5e9, listed_date=start)
        bars = make_prices(700, seed=i + 1, start=start)
        repos["prices"].write_bars(asset_id, bars, source="fixture")
        as_of = bars[-1]["date"]
        if symbol != "SPY":
            repos["fundamentals"].write(
                asset_id,
                [{"metric": m, "period": "annual", "period_end": p.period_end,
                  "value": p.value, "unit": "USD", "filed_date": p.filed_date,
                  "accession": f"{symbol}-{p.period_end.year}", "form": "10-K"}
                 for m, hist in COMPOUNDER.items() for p in hist],
                source="fixture", source_tier=SourceTier.REGULATORY_FILING)

    data = RepositoryDataAccess(settings, repos)
    DailyPipeline(settings, data=data, repositories=repos).run(as_of)
    app = build_app(settings, {"db": db, "repos": repos, "data": data})
    return fastapi_testclient.TestClient(app), repos, as_of


# ------------------------------------------------------------------ api ----
def test_health_reports_state_and_disclaimer(api):
    client, _, _ = api
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["assets"] == 3
    assert "not investment advice" in payload["disclaimer"].lower()


def test_opportunities_are_ranked_and_filtered(api):
    client, _, _ = api
    payload = client.get("/api/opportunities?limit=5").json()
    assert payload["items"]
    scores = [i["score"] for i in payload["items"] if i["score"] is not None]
    assert scores == sorted(scores, reverse=True)
    filtered = client.get("/api/opportunities?recommendation=SELL").json()
    assert all(i["recommendation"] == "SELL" for i in filtered["items"])


def test_screener_filters_by_score_and_risk(api):
    client, _, _ = api
    strict = client.get("/api/screen?min_score=99").json()
    assert strict["items"] == []
    loose = client.get("/api/screen?min_score=0").json()
    assert loose["items"]


def test_asset_detail_includes_history_and_thesis(api):
    client, _, _ = api
    payload = client.get("/api/asset/ACME").json()
    assert payload["asset"]["symbol"] == "ACME"
    assert payload["recommendation"] is not None
    assert "sell_conditions" in payload["recommendation"]
    assert payload["score_history"]
    assert "disclaimer" in payload


def test_unknown_asset_is_404(api):
    client, _, _ = api
    assert client.get("/api/asset/NOPE").status_code == 404


def test_price_endpoint_returns_bars_and_indicators(api):
    client, _, _ = api
    payload = client.get("/api/asset/ACME/prices?days=400").json()
    assert len(payload["bars"]) == 400
    assert "sma_200" in payload["indicators"]


def test_providers_endpoint_never_leaks_credentials(api, monkeypatch):
    monkeypatch.setenv("COINGECKO_API_KEY", "super-secret-value")
    client, _, _ = api
    body = client.get("/api/providers").text
    assert "super-secret-value" not in body
    assert "api_key" not in body.lower() or "api_key_env" in body.lower()


def test_api_exposes_no_mutating_routes(api):
    client, _, _ = api
    app = client.app
    for route in app.routes:
        for method in getattr(route, "methods", set()):
            assert method in {"GET", "HEAD", "OPTIONS"}, f"{route.path} {method}"


def test_performance_endpoint_carries_its_caveat(api):
    client, _, _ = api
    payload = client.get("/api/performance").json()
    assert "past results do not guarantee" in payload["caveat"]


def test_portfolio_states_it_never_trades(api):
    client, _, _ = api
    payload = client.get("/api/portfolio").json()
    assert "never places orders" in payload["note"]


# ------------------------------------------------------------------ cli ----
def test_cli_parser_covers_every_command():
    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices
    assert {"init", "providers", "universe", "ingest", "daily", "analyze",
            "report", "backtest", "evaluate", "models", "serve",
            "doctor"} <= set(commands)


def test_cli_init_and_doctor(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RE__APP__DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RE__APP__OFFLINE", "true")
    assert main(["init"]) == 0
    assert (tmp_path / "research.db").exists()
    # doctor exits non-zero because the universe is empty, and says why
    assert main(["doctor"]) == 1
    output = capsys.readouterr().out
    assert "no assets" in output


def test_cli_rejects_trading_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RE__APP__DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RE__APP__ALLOW_TRADING", "true")
    assert main(["init"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_cli_offline_run_is_honest_about_missing_data(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RE__APP__DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RE__APP__OFFLINE", "true")
    main(["init"])
    code = main(["ingest", "--symbols", "AAPL"])
    output = capsys.readouterr().out
    assert code == 1
    assert "not in the asset universe" in output


def test_cli_portfolio_requires_a_price_it_does_not_guess(tmp_path, monkeypatch,
                                                          capsys):
    monkeypatch.setenv("RE__APP__DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RE__APP__OFFLINE", "true")
    main(["init"])
    from research_engine.config.settings import load_settings
    from research_engine.storage.db import connect
    from research_engine.storage.repositories import AssetRepository

    settings = load_settings(environ={"RE__APP__DATA_DIR": str(tmp_path)})
    AssetRepository(connect(settings)).upsert(symbol="ACME", asset_class="equity")

    # no stored price and none supplied: refuse rather than invent one
    assert main(["portfolio", "open", "--symbol", "ACME", "--quantity", "10"]) == 1
    assert "rather than letting the system guess" in capsys.readouterr().err

    assert main(["portfolio", "open", "--symbol", "ACME", "--quantity", "10",
                 "--price", "42.50", "--thesis", "test thesis"]) == 0
    assert main(["portfolio", "show"]) == 0
    output = capsys.readouterr().out
    assert "ACME" in output and "test thesis" in output
    assert "places no orders" in output


def test_cli_portfolio_warns_when_no_thesis_is_recorded(tmp_path, monkeypatch,
                                                        capsys):
    monkeypatch.setenv("RE__APP__DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RE__APP__OFFLINE", "true")
    main(["init"])
    from research_engine.config.settings import load_settings
    from research_engine.storage.db import connect
    from research_engine.storage.repositories import AssetRepository

    settings = load_settings(environ={"RE__APP__DATA_DIR": str(tmp_path)})
    AssetRepository(connect(settings)).upsert(symbol="BETA", asset_class="equity")
    main(["portfolio", "open", "--symbol", "BETA", "--quantity", "5",
          "--price", "10"])
    assert "cannot be monitored for thesis deterioration" in capsys.readouterr().out


def test_api_does_not_emit_false_precision(api):
    """A fair value quoted to 13 decimals implies precision the model lacks."""
    client, _, _ = api
    items = client.get("/api/opportunities?limit=5").json()["items"]
    items += client.get("/api/screen?min_score=0").json()["items"]
    assert items
    for item in items:
        for value in item["fair_value"].values():
            if value is not None:
                assert len(str(value).split(".")[-1]) <= 2, value
        for value in item["expected_return"].values():
            if value is not None:
                assert len(str(value).split(".")[-1]) <= 3, value
        if item["confidence"] is not None:
            assert len(str(item["confidence"]).split(".")[-1]) <= 3


def test_portfolio_breaches_are_computed_live_not_from_a_stale_report(api):
    """Positions opened between daily runs must still be risk-checked."""
    client, repos, as_of = api
    portfolio_id = repos["portfolio"].ensure("research")
    asset = repos["assets"].get("ACME")
    repos["portfolio"].open_position(portfolio_id, asset.id, opened_at=as_of,
                                     entry_price=100.0, quantity=1000.0,
                                     thesis="deliberately oversized")

    payload = client.get("/api/portfolio").json()
    assert len(payload["positions"]) == 1
    # the daily run happened before this position existed; the API must not
    # report "no breaches" from that stale report
    assert payload["breaches"]
    assert any("ACME is 100.0%" in b for b in payload["breaches"])
    assert payload["risk"].get("hhi") is not None
