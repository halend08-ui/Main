"""Tests for the research agent and the ingestion service — the two layers that
sit at the system's boundaries and therefore matter most for honesty."""

import datetime as dt

import pytest

from research_engine.analysis.agent import ResearchAgent
from research_engine.core.errors import DataUnavailable
from research_engine.core.series import PriceSeries
from research_engine.core.types import AssetClass, DataQuality, SourceTier
from research_engine.ingestion.cache import ResponseCache
from research_engine.ingestion.factory import build_registry
from research_engine.ingestion.http import FakeTransport, Response
from research_engine.ingestion.service import IngestionService
from research_engine.pipeline.data_access import RepositoryDataAccess
from research_engine.storage.analysis_repos import (ModelRegistryRepository,
                                                    PortfolioRepository,
                                                    PredictionRepository,
                                                    RecommendationRepository,
                                                    ReportRepository, ScoreRepository)
from research_engine.storage.reference_repos import (CryptoMetricRepository,
                                                     DataQualityRepository,
                                                     DataSourceRepository,
                                                     MacroRepository, NewsRepository)
from research_engine.storage.repositories import (AssetRepository,
                                                  FundamentalRepository,
                                                  PriceRepository)
from tests.conftest import make_prices
from tests.test_features import COMPOUNDER


def _repos(db):
    return {
        "assets": AssetRepository(db), "prices": PriceRepository(db),
        "fundamentals": FundamentalRepository(db), "news": NewsRepository(db),
        "macro": MacroRepository(db), "crypto": CryptoMetricRepository(db),
        "sources": DataSourceRepository(db), "quality": DataQualityRepository(db),
        "scores": ScoreRepository(db), "recommendations": RecommendationRepository(db),
        "predictions": PredictionRepository(db), "models": ModelRegistryRepository(db),
        "reports": ReportRepository(db), "portfolio": PortfolioRepository(db),
    }


def _seed(repos, symbols=("ACME", "PEER1", "PEER2", "PEER3"), n=800):
    as_of = None
    for i, symbol in enumerate(symbols):
        asset_id = repos["assets"].upsert(symbol=symbol, asset_class="equity",
                                          name=f"{symbol} Inc", sector="Technology",
                                          market_cap_usd=5e9,
                                          listed_date=dt.date(2022, 1, 3))
        bars = make_prices(n, seed=i + 1, start=dt.date(2022, 1, 3))
        repos["prices"].write_bars(asset_id, bars, source="fixture")
        as_of = bars[-1]["date"]
        repos["fundamentals"].write(
            asset_id,
            [{"metric": m, "period": "annual", "period_end": p.period_end,
              "value": p.value, "unit": "USD", "filed_date": p.filed_date,
              "accession": f"{symbol}-{p.period_end.year}", "form": "10-K"}
             for m, hist in COMPOUNDER.items() for p in hist],
            source="fixture", source_tier=SourceTier.REGULATORY_FILING)
    return as_of


# ------------------------------------------------------------------ agent --
@pytest.fixture()
def agent_env(db, settings):
    repos = _repos(db)
    as_of = _seed(repos)
    data = RepositoryDataAccess(settings, repos)
    return ResearchAgent(settings, data), repos, as_of


def test_agent_produces_a_full_dossier(agent_env):
    agent, _, as_of = agent_env
    dossier = agent.investigate("ACME", as_of=as_of, peer_symbols=["PEER1", "PEER2"])
    assert dossier.recommendation is not None
    assert dossier.memo.startswith("# ACME")
    assert dossier.horizons["1y"] != "insufficient history"
    assert dossier.horizons["risk_summary"]["volatility"] is not None
    assert dossier.sources


def test_agent_reports_what_it_could_not_answer(agent_env):
    agent, _, as_of = agent_env
    dossier = agent.investigate("ACME", as_of=as_of)
    joined = " ".join(dossier.unanswered).lower()
    assert "management quality" in joined
    assert "customer concentration" in joined
    assert "no news" in joined


def test_agent_on_an_unknown_asset_says_insufficient(agent_env):
    agent, _, as_of = agent_env
    dossier = agent.investigate("NOSUCH", as_of=as_of)
    assert dossier.recommendation is None
    assert "Insufficient reliable data" in dossier.memo
    assert dossier.unanswered


def test_agent_reverse_dcf_compares_to_delivered_growth(agent_env):
    agent, _, as_of = agent_env
    dossier = agent.investigate("ACME", as_of=as_of)
    reverse = dossier.reverse_dcf
    assert reverse.get("applicable") is True
    if reverse.get("implied_growth") is not None:
        assert "the market is" in reverse["comparison"]


def test_agent_historical_analogues_carry_their_caveat(agent_env):
    agent, _, as_of = agent_env
    dossier = agent.investigate("ACME", as_of=as_of)
    if dossier.historical_analogues:
        summary = dossier.historical_analogues[0]
        assert "overlapping observations" in summary["caveat"]
        assert summary["claim_type"] == "historical_observation"


def test_agent_is_point_in_time(agent_env):
    agent, _, as_of = agent_env
    earlier = as_of - dt.timedelta(days=300)
    dossier = agent.investigate("ACME", as_of=earlier)
    assert dossier.as_of == earlier
    assert dossier.recommendation.as_of == earlier


def test_agent_notes_a_missing_peer_group(db, settings):
    repos = _repos(db)
    as_of = _seed(repos, symbols=("SOLO",))
    agent = ResearchAgent(settings, RepositoryDataAccess(settings, repos))
    dossier = agent.investigate("SOLO", as_of=as_of)
    assert any("peer group" in q for q in dossier.unanswered)


# ---------------------------------------------------------------- service --
STOOQ_CSV = ("Date,Open,High,Low,Close,Volume\n"
             + "\n".join(f"2026-01-{day:02d},10.0,10.5,9.8,{10 + day * 0.1},1000000"
                         for day in range(1, 29)))


@pytest.fixture()
def service_env(db, settings, tmp_path):
    repos = _repos(db)
    transport = FakeTransport().add_text("stooq.com", STOOQ_CSV)
    registry = build_registry(settings, transport=transport,
                              cache=ResponseCache(tmp_path / "cache"))
    return IngestionService(settings, registry, repos), repos, transport


def test_service_writes_prices_and_a_quality_report(service_env):
    service, repos, _ = service_env
    repos["assets"].upsert(symbol="AAPL", asset_class="equity")
    result = service.ingest(["AAPL"], kinds=("prices",), as_of=dt.date(2026, 1, 28))
    assert result["rows_written"] == 28
    assert result["succeeded"] == 1
    # a data-quality report is written alongside the data itself
    asset_id = repos["assets"].get("AAPL").id
    quality = repos["quality"].latest(asset_id, "prices")
    assert quality is not None
    assert quality["grade"] in {g.value for g in DataQuality}
    # a 28-day series must be graded down for short history, not called excellent
    assert any(issue["code"] == "price.short_history" for issue in quality["issues"])


def test_service_reports_unknown_assets_rather_than_creating_them(service_env):
    service, repos, _ = service_env
    result = service.ingest(["GHOST"], kinds=("prices",))
    assert result["succeeded"] == 0
    assert any("not in the asset universe" in f for f in result["failures"])
    assert repos["assets"].get("GHOST") is None


def test_service_records_provider_failure_without_writing_data(db, settings, tmp_path):
    repos = _repos(db)
    repos["assets"].upsert(symbol="AAPL", asset_class="equity")
    transport = FakeTransport().add("stooq.com", Response(500, "boom", {}, "u"))
    registry = build_registry(settings, transport=transport,
                              cache=ResponseCache(tmp_path / "cache"))
    stooq = registry.get("stooq")
    stooq._sleep = lambda seconds: None
    service = IngestionService(settings, registry, repos)
    result = service.ingest(["AAPL"], kinds=("prices",))
    assert result["rows_written"] == 0
    assert result["failures"]
    with pytest.raises(DataUnavailable):
        repos["prices"].series(repos["assets"].get("AAPL").id, "AAPL")


def test_service_fetches_incrementally(service_env):
    service, repos, transport = service_env
    repos["assets"].upsert(symbol="AAPL", asset_class="equity")
    service.ingest(["AAPL"], kinds=("prices",), as_of=dt.date(2026, 1, 28))
    calls_before = len(transport.calls)
    service.ingest(["AAPL"], kinds=("prices",), as_of=dt.date(2026, 1, 28))
    # the second call requests only an overlap window, not the full history
    params = transport.calls[calls_before][1]
    assert "d1" in params


def test_service_skips_fundamentals_for_crypto(service_env):
    service, repos, _ = service_env
    repos["assets"].upsert(symbol="BTC", asset_class="crypto")
    result = service.ingest(["BTC"], kinds=("fundamentals",))
    assert result["per_kind"].get("fundamentals", 0) == 0
    assert not any("BTC/fundamentals" in f for f in result["failures"])


def test_refresh_reports_provider_health_and_coverage(service_env):
    service, repos, _ = service_env
    repos["assets"].upsert(symbol="AAPL", asset_class="equity")
    result = service.refresh(dt.date(2026, 1, 28))
    assert "health" in result
    assert "providers" in result["health"]
    assert result["health"]["coverage"]["total"] == 1
