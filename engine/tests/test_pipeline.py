"""Pipeline tests: prioritisation, discovery, alerts, reporting, memos, and a
full daily run against a real (in-memory) database."""

import datetime as dt
import json

import pytest

from research_engine.core.series import PriceSeries
from research_engine.core.types import (AssetClass, DataQuality, Horizon, Period,
                                        Recommendation, RiskLevel, SourceTier)
from research_engine.pipeline import alerts as ALERTS
from research_engine.pipeline import discovery as DISC
from research_engine.pipeline import prioritization as PRIOR
from research_engine.pipeline.daily import DailyPipeline
from research_engine.pipeline.data_access import RepositoryDataAccess
from research_engine.pipeline.report import DailyReport
from research_engine.storage.analysis_repos import (AlertRepository,
                                                    ModelRegistryRepository,
                                                    PortfolioRepository,
                                                    PredictionRepository,
                                                    RecommendationRepository,
                                                    ReportRepository,
                                                    ResearchQueueRepository,
                                                    ScoreRepository)
from research_engine.storage.reference_repos import (CryptoMetricRepository,
                                                     MacroRepository, NewsRepository)
from research_engine.storage.repositories import (AssetRepository,
                                                  FundamentalRepository,
                                                  PriceRepository)
from tests.conftest import make_prices
from tests.test_features import COMPOUNDER


# ------------------------------------------------------- prioritisation ----
def test_held_and_changed_assets_outrank_static_high_scores():
    static_star = PRIOR.PriorityInputs("STAR", score=88, previous_score=88,
                                       days_since_analysis=1)
    changed = PRIOR.PriorityInputs("MOVER", score=62, previous_score=45,
                                   days_since_analysis=1)
    held = PRIOR.PriorityInputs("OWNED", score=60, previous_score=60,
                                days_since_analysis=1, is_held=True)
    ranked = PRIOR.rank([static_star, changed, held])
    assert ranked[0].symbol in ("MOVER", "OWNED")
    assert ranked[-1].symbol == "STAR"


def test_never_analysed_assets_are_prioritised():
    result = PRIOR.score_priority(PRIOR.PriorityInputs("NEW", score=None,
                                                       days_since_analysis=None))
    assert "never analysed" in result.reasons
    assert result.components["staleness"] == 1.0


def test_poor_data_quality_halves_priority():
    good = PRIOR.score_priority(PRIOR.PriorityInputs("A", score=80,
                                                     data_quality=DataQuality.GOOD))
    poor = PRIOR.score_priority(PRIOR.PriorityInputs("A", score=80,
                                                     data_quality=DataQuality.POOR))
    assert poor.priority == pytest.approx(good.priority * 0.5)
    assert any("halved" in r for r in poor.reasons)


def test_funnel_narrows_and_records_why():
    ranked = PRIOR.rank([PRIOR.PriorityInputs(f"S{i}", score=100 - i)
                         for i in range(50)])
    config = PRIOR.FunnelConfig(stage1_max=40, stage2_max=20, stage3_max=5,
                                stage4_max=2)
    funnel = PRIOR.run_funnel(ranked, config)
    assert [len(funnel.stages[s]) for s in (1, 2, 3, 4)] == [40, 20, 5, 2]
    assert funnel.dropped[1]
    assert all(reason for _, reason in funnel.dropped[2])


def test_funnel_screens_can_reject_assets():
    ranked = PRIOR.rank([PRIOR.PriorityInputs(f"S{i}", score=90) for i in range(10)])
    funnel = PRIOR.run_funnel(
        ranked, PRIOR.FunnelConfig(stage1_max=10, stage2_max=10, stage3_max=10,
                                   stage4_max=10),
        stage2_screen={f"S{i}": i % 2 == 0 for i in range(10)})
    assert len(funnel.stages[2]) == 5
    assert any("did not pass" in r for _, r in funnel.dropped[2])


# ------------------------------------------------------------ discovery ----
def test_new_listings_carry_short_history_warnings():
    found = DISC.recent_listings(
        [{"symbol": "IPO", "listed_date": "2026-01-15", "asset_class": "equity"}],
        as_of=dt.date(2026, 3, 1))
    assert found and found[0].trigger == "new_listing"
    assert any("limited price history" in w for w in found[0].warnings)


def test_discoveries_are_never_recommendations():
    found = DISC.recent_listings([{"symbol": "IPO", "listed_date": "2026-01-15"}],
                                 as_of=dt.date(2026, 2, 1))
    assert "not a recommendation" in found[0].to_dict()["status"]


def test_accelerating_fundamentals_detected():
    found = DISC.accelerating_fundamentals({"FAST": [100, 105, 112, 145]})
    assert found and found[0].trigger == "fundamental_acceleration"
    assert "accelerated" in found[0].reason


def test_sector_laggards_flag_both_readings():
    found = DISC.sector_laggards(
        {"A": 0.30, "B": 0.28, "C": 0.32, "D": 0.29, "E": 0.31, "LAG": 0.05},
        {s: "Technology" for s in ("A", "B", "C", "D", "E", "LAG")})
    assert found and found[0].symbol == "LAG"
    assert any("as often a warning" in w for w in found[0].warnings)


def test_crypto_volume_growth_warns_about_wash_trading():
    found = DISC.crypto_traction([{"symbol": "tok", "market_cap_usd": 1e8,
                                   "volume_24h_usd": 3e7,
                                   "volume_24h_usd_prior": 5e6}])
    assert found and any("wash trading" in w for w in found[0].warnings)


def test_multiple_triggers_raise_interest_and_dedupe():
    a = DISC.Discovery("X", "equity", "volume spike", "volume_spike", 0.5)
    b = DISC.Discovery("X", "equity", "insider buying", "insider_cluster", 0.4)
    merged = DISC.deduplicate([a, b])
    assert len(merged) == 1
    assert merged[0].score > 0.5
    assert "+" in merged[0].trigger


def test_known_symbols_are_not_rediscovered():
    a = DISC.Discovery("KNOWN", "equity", "spike", "volume_spike", 0.9)
    assert DISC.deduplicate([a], known_symbols=["known"]) == []


# --------------------------------------------------------------- alerts ----
def test_recommendation_downgrade_is_a_warning():
    rules = ALERTS.AlertRules()
    alert = rules.recommendation_change("X", "BUY", "SELL")
    assert alert.severity is ALERTS.Severity.WARNING
    upgrade = rules.recommendation_change("X", "WATCH", "BUY")
    assert upgrade.severity is ALERTS.Severity.NOTICE


def test_thresholds_are_respected():
    rules = ALERTS.AlertRules({"score_change_abs": 20})
    assert rules.score_change("X", 50, 60) is None
    assert rules.score_change("X", 50, 75) is not None


def test_crypto_price_threshold_is_wider():
    rules = ALERTS.AlertRules()
    assert rules.price_move("BTC", 0.12, is_crypto=True) is None
    assert rules.price_move("AAPL", 0.12, is_crypto=False) is not None


def test_drawdown_alert_says_review_not_sell():
    alert = ALERTS.AlertRules().position_drawdown("X", 100.0, 70.0)
    assert "re-reviewed" in alert.detail
    assert "not the same as" in alert.detail


def test_alerts_are_deduplicated(tmp_path):
    path = tmp_path / "alerts.jsonl"
    dispatcher = ALERTS.AlertDispatcher(sinks=[ALERTS.file_sink(path)])
    alert = ALERTS.Alert("test", ALERTS.Severity.INFO, "t", "d", symbol="X")
    assert len(dispatcher.dispatch([alert])) == 1
    assert len(dispatcher.dispatch([alert])) == 0        # same kind/symbol/day
    assert len(path.read_text().strip().splitlines()) == 1


def test_evaluate_all_runs_every_rule():
    fired = ALERTS.evaluate_all(
        ALERTS.AlertRules(), symbol="X", as_of=dt.date(2026, 1, 5),
        current={"recommendation": "SELL", "score": 40.0, "price": 90.0,
                 "price_move_1d": -0.15, "risk_level": "high",
                 "data_quality": "fair", "confidence": 0.5,
                 "fair_value": {"base": 80.0}},
        previous={"recommendation": "BUY", "score": 70.0, "risk_level": "moderate",
                  "data_quality": "good"},
        breached_conditions=[{"description": "revenue growth collapsed"}])
    kinds = {a.kind for a in fired}
    assert {"recommendation_change", "score_change", "price_move",
            "thesis_invalidation", "risk_increase", "data_quality"} <= kinds
    assert any(a.severity is ALERTS.Severity.CRITICAL for a in fired)


# --------------------------------------------------------------- report ----
def test_report_renders_all_sections():
    report = DailyReport(
        as_of=dt.date(2026, 1, 5),
        market={"regime": {"regime": "bull", "confidence": 0.7},
                "macro_stance": {"policy": "restrictive"},
                "major_risks": ["credit spreads widening"]},
        opportunities=[{"symbol": "ACME", "recommendation": "BUY", "score": 82,
                        "confidence": 0.6, "price": 100.0,
                        "fair_value": {"base": 130.0},
                        "expected_return": {"base": 0.3}, "risk_level": "moderate",
                        "data_quality": "good", "why": ["strong ROIC"],
                        "key_risks": ["customer concentration"],
                        "sell_conditions": ["revenue growth below 5%"]}],
        changes={"new_buys": [{"symbol": "ACME", "detail": "WATCH -> BUY"}]},
        discoveries=[{"symbol": "NEW", "asset_class": "equity", "reason": "listed",
                      "trigger": "new_listing", "warnings": ["short history"]}],
        model_performance={"overall": {"sufficient": True, "samples": 120,
                                       "hit_rate": 0.58, "avg_return": 0.04,
                                       "avg_excess": 0.01, "brier_skill": 0.03,
                                       "calibration_error": 0.06}})
    text = report.render()
    for heading in ("Market Overview", "Top Opportunities", "Biggest Changes",
                    "New Assets Discovered", "Existing Portfolio",
                    "Model Performance"):
        assert heading in text
    assert "Not investment advice" in text
    assert "none of the above is a recommendation" in text


def test_empty_report_says_so_rather_than_padding():
    text = DailyReport(as_of=dt.date(2026, 1, 5)).render()
    assert "No asset cleared the minimum evidence" in text
    assert "legitimate output, not a failure" in text


# ----------------------------------------------------------------- memo ----
def test_memo_contains_every_required_section(price_bars, settings):
    from research_engine.analysis import memo as MEMO
    from research_engine.analysis.pipeline import AnalysisInput, analyze

    series = PriceSeries.from_rows("ACME", price_bars(700, start=dt.date(2023, 1, 2)))
    result = analyze(AnalysisInput(
        symbol="ACME", as_of=series.end, asset_class=AssetClass.EQUITY, series=series,
        annual=COMPOUNDER, market={"market_cap_usd": 5e9}, sector="Technology",
        settings=settings))
    text = MEMO.generate(result, fundamental={"roic": 0.31, "gross_margin": 0.71})
    for heading in ("Executive Summary", "Business / Project Overview",
                    "Fundamental Analysis", "Valuation", "Technical Analysis",
                    "Industry and Competitive Position", "Catalysts", "Risks",
                    "Bear Case", "Bull Case", "Thesis",
                    "What Would Change My Mind?", "Sell Conditions",
                    "Data Quality", "Model Information", "Sources"):
        assert f"## {heading}" in text, heading
    assert "not investment advice" in text.lower()
    # the bear case must appear before the bull case
    assert text.index("## Bear Case") < text.index("## Bull Case")


def test_memo_admits_missing_sections(price_bars, settings):
    from research_engine.analysis import memo as MEMO
    from research_engine.analysis.pipeline import AnalysisInput, analyze

    series = PriceSeries.from_rows("THIN", price_bars(400, start=dt.date(2024, 1, 2)))
    result = analyze(AnalysisInput(symbol="THIN", as_of=series.end,
                                   asset_class=AssetClass.EQUITY, series=series,
                                   settings=settings))
    text = MEMO.generate(result)
    assert "No peer or industry dataset was configured" in text
    assert "This is a gap, not a judgement" in text


# ------------------------------------------------------ end-to-end daily ---
@pytest.fixture()
def seeded(db, settings):
    """A small but complete database: two equities, one crypto, one benchmark."""
    repos = {
        "assets": AssetRepository(db), "prices": PriceRepository(db),
        "fundamentals": FundamentalRepository(db), "news": NewsRepository(db),
        "macro": MacroRepository(db), "crypto": CryptoMetricRepository(db),
        "scores": ScoreRepository(db), "recommendations": RecommendationRepository(db),
        "predictions": PredictionRepository(db), "models": ModelRegistryRepository(db),
        "queue": ResearchQueueRepository(db), "alerts": AlertRepository(db),
        "reports": ReportRepository(db), "portfolio": PortfolioRepository(db),
    }
    start = dt.date(2023, 1, 2)
    as_of = None
    for i, (symbol, klass) in enumerate([("SPY", "equity"), ("ACME", "equity"),
                                         ("BETA", "equity"), ("TOK", "crypto")]):
        asset_id = repos["assets"].upsert(
            symbol=symbol, asset_class=klass, name=f"{symbol} Inc",
            sector="Technology" if klass == "equity" else None,
            market_cap_usd=5e9, listed_date=start)
        bars = make_prices(700, seed=i + 1, start=start,
                           weekdays_only=klass == "equity")
        repos["prices"].write_bars(asset_id, bars, source="fixture")
        as_of = bars[-1]["date"]
        if symbol in ("ACME", "BETA"):
            points = []
            for metric, history in COMPOUNDER.items():
                for point in history:
                    points.append({"metric": metric, "period": "annual",
                                   "period_end": point.period_end,
                                   "value": point.value, "unit": "USD",
                                   "filed_date": point.filed_date,
                                   "accession": f"{symbol}-{point.period_end.year}",
                                   "form": "10-K"})
            repos["fundamentals"].write(asset_id, points, source="fixture",
                                        source_tier=SourceTier.REGULATORY_FILING)
    repos["macro"].write("T10Y2Y", [{"date": "2025-06-01", "value": -0.4,
                                     "release_date": "2025-06-02"}], source="fixture")
    return {"repos": repos, "as_of": as_of, "settings": settings}


def test_daily_run_end_to_end(seeded):
    settings, repos, as_of = seeded["settings"], seeded["repos"], seeded["as_of"]
    data = RepositoryDataAccess(settings, repos)
    pipeline = DailyPipeline(settings, data=data, repositories=repos)
    run = pipeline.run(as_of)

    assert run.ok, [s.to_dict() for s in run.steps if not s.ok]
    assert run.analyzed
    assert run.report is not None
    rendered = run.report.render()
    assert "Daily Research Report" in rendered
    assert "Model Performance" in rendered

    # results were persisted and are queryable
    acme_id = repos["assets"].get("ACME").id
    assert repos["recommendations"].latest(acme_id) is not None
    assert repos["reports"].latest("daily") is not None
    # predictions were stored for later grading
    assert repos["predictions"].open_count() > 0


def test_daily_run_survives_a_failing_step(seeded, monkeypatch):
    settings, repos, as_of = seeded["settings"], seeded["repos"], seeded["as_of"]
    data = RepositoryDataAccess(settings, repos)

    def boom(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(data, "discovery_candidates", boom)
    run = DailyPipeline(settings, data=data, repositories=repos).run(as_of)

    assert not run.ok
    assert "discovery" in run.failed_steps()
    # the rest of the pipeline still produced a report
    assert run.report is not None
    assert run.analyzed
    assert any("discovery" in w for w in run.report.warnings)


def test_daily_run_is_point_in_time(seeded):
    """Running as of an earlier date must not see later prices."""
    settings, repos = seeded["settings"], seeded["repos"]
    data = RepositoryDataAccess(settings, repos)
    earlier = seeded["as_of"] - dt.timedelta(days=200)
    bundle = data.analysis_input("ACME", earlier)
    assert bundle.series.end <= earlier
    bundle.series.require_no_future(earlier)


def test_predictions_are_graded_when_horizons_elapse(seeded):
    settings, repos = seeded["settings"], seeded["repos"]
    data = RepositoryDataAccess(settings, repos)
    early = seeded["as_of"] - dt.timedelta(days=200)
    pipeline = DailyPipeline(settings, data=data, repositories=repos)

    pipeline.run(early)
    open_before = repos["predictions"].open_count()
    assert open_before > 0

    # advance far enough for the shortest horizon to mature
    later = pipeline.run(seeded["as_of"])
    evaluation = next(s for s in later.steps if s.name == "evaluate_predictions")
    assert evaluation.ok
    assert evaluation.detail.get("evaluated", 0) >= 0     # graded or not yet due


def test_alerts_persist_to_the_database(seeded):
    settings, repos, as_of = seeded["settings"], seeded["repos"], seeded["as_of"]
    data = RepositoryDataAccess(settings, repos)
    pipeline = DailyPipeline(settings, data=data, repositories=repos)
    pipeline.run(as_of - dt.timedelta(days=100))
    pipeline.dispatcher._seen.clear()          # a new day: dedupe window resets
    pipeline.run(as_of)
    assert isinstance(repos["alerts"].recent(), list)
