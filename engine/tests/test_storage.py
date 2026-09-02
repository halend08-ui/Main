import datetime as dt

import pytest

from research_engine.core.errors import DataUnavailable
from research_engine.core.types import AssetClass, DataQuality, Period, SourceTier
from research_engine.storage.analysis_repos import (ModelRegistryRepository,
                                                    PredictionRepository,
                                                    RecommendationRepository,
                                                    ResearchQueueRepository,
                                                    ScoreRepository)
from research_engine.storage.reference_repos import MacroRepository, NewsRepository


def test_schema_has_indexes(db):
    idx = [r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")]
    assert "idx_prices_asset_date" in idx
    assert "idx_fund_pit" in idx
    assert len(idx) > 20


def test_asset_upsert_is_idempotent(repos):
    a = repos["assets"]
    first = a.upsert(symbol="aapl", asset_class="equity", name="Apple")
    second = a.upsert(symbol="AAPL", asset_class=AssetClass.EQUITY, sector="Tech")
    assert first == second
    record = a.require("AAPL")
    assert record.sector == "Tech" and record.name == "Apple"


def test_delisted_assets_are_kept_for_survivorship_control(repos):
    a = repos["assets"]
    aid = a.upsert(symbol="DEAD", asset_class="equity")
    a.mark_delisted(aid, "2020-06-01", "bankruptcy")
    assert a.get("DEAD").is_active is False
    assert a.list(active_only=True) == []
    assert len(a.list(active_only=False)) == 1


def test_price_round_trip_and_as_of(repos, price_bars):
    aid = repos["assets"].upsert(symbol="X", asset_class="equity")
    bars = price_bars(60, start=dt.date(2024, 1, 1))
    assert repos["prices"].write_bars(aid, bars, source="test") == 60
    series = repos["prices"].series(aid, "X")
    assert len(series) == 60
    cut = repos["prices"].series(aid, "X", as_of=bars[20]["date"])
    assert len(cut) == 21 and cut.end == bars[20]["date"]


def test_price_bars_without_close_are_dropped_not_zeroed(repos):
    aid = repos["assets"].upsert(symbol="Y", asset_class="equity")
    written = repos["prices"].write_bars(aid, [
        {"date": "2024-01-02", "close": 10.0},
        {"date": "2024-01-03", "close": None},
    ], source="test")
    assert written == 1
    assert len(repos["prices"].series(aid, "Y")) == 1


def test_missing_history_raises_rather_than_returning_empty(repos):
    aid = repos["assets"].upsert(symbol="Z", asset_class="equity")
    with pytest.raises(DataUnavailable):
        repos["prices"].series(aid, "Z")


def test_fundamentals_point_in_time_restatement(repos):
    aid = repos["assets"].upsert(symbol="F", asset_class="equity")
    repos["fundamentals"].write(aid, [
        {"metric": "revenue", "period": "annual", "period_end": "2024-12-31",
         "value": 1000, "filed_date": "2025-02-15", "accession": "a1", "form": "10-K"},
        {"metric": "revenue", "period": "annual", "period_end": "2024-12-31",
         "value": 950, "filed_date": "2025-08-01", "accession": "a2", "form": "10-K/A"},
    ], source="sec_edgar", source_tier=SourceTier.REGULATORY_FILING)

    before = repos["fundamentals"].latest_value(aid, "revenue", as_of="2025-03-01")
    after = repos["fundamentals"].latest_value(aid, "revenue", as_of="2025-09-01")
    unknown = repos["fundamentals"].history(aid, "revenue", as_of="2025-01-01")
    assert before == 1000        # what was knowable in March
    assert after == 950          # restated figure, only after it was filed
    assert unknown == []         # nothing was filed yet


def test_recommendation_history_is_append_only_per_model(db, repos):
    aid = repos["assets"].upsert(symbol="R", asset_class="equity")
    recs = RecommendationRepository(db)
    from research_engine.core.types import Horizon, Recommendation, RiskLevel
    for version in ("scoring_v1", "scoring_v2"):
        recs.write(aid, as_of="2026-01-05", recommendation=Recommendation.BUY,
                   confidence=0.6, horizon=Horizon.Y1, risk_level=RiskLevel.MODERATE,
                   data_quality=DataQuality.GOOD, model_version=version, score=80)
    rows = recs.history(aid)
    assert {r["model_version"] for r in rows} == {"scoring_v1", "scoring_v2"}


def test_prediction_lifecycle(db, repos):
    from research_engine.core.types import Horizon, Recommendation
    aid = repos["assets"].upsert(symbol="P", asset_class="equity")
    preds = PredictionRepository(db)
    pid = preds.write(aid, as_of="2026-01-05", horizon=Horizon.M3,
                      due_at="2026-04-05", price_at_prediction=100.0,
                      recommendation=Recommendation.BUY, confidence=0.7,
                      asset_class="equity", model_version="scoring_v1",
                      data_quality=DataQuality.GOOD, prob_positive=0.62)
    assert preds.open_count() == 1
    assert not preds.due("2026-02-01")
    assert len(preds.due("2026-05-01")) == 1
    preds.record_outcome(pid, price_at_due=112.0, actual_return=0.12,
                         benchmark_return=0.03, hit=True, thesis_outcome="succeeded")
    assert preds.open_count() == 0
    done = preds.evaluated()
    assert done[0]["excess_return"] == pytest.approx(0.09)


def test_model_promotion_retires_previous(db):
    reg = ModelRegistryRepository(db)
    reg.register("scoring_v1", family="scoring", parameters={"w": 1}, status="active")
    reg.register("scoring_v2", family="scoring", parameters={"w": 2})
    reg.promote("scoring_v2")
    assert reg.active("scoring")["version"] == "scoring_v2"
    assert reg.get("scoring_v1")["status"] == "retired"   # kept, never deleted


def test_macro_is_point_in_time_by_release_date(db):
    macro = MacroRepository(db)
    macro.write("GDPC1", [
        {"date": "2026-01-01", "value": 100.0, "release_date": "2026-04-28"},
    ], source="fred")
    assert macro.latest("GDPC1", as_of="2026-02-01") is None   # not released yet
    assert macro.latest("GDPC1", as_of="2026-05-01")[1] == 100.0


def test_news_deduplicates_on_url(db):
    news = NewsRepository(db)
    item = {"headline": "Same story", "published_at": "2026-01-05T10:00:00Z",
            "source": "wire", "url": "https://example.com/a", "source_tier": SourceTier.FINANCIAL_JOURNALISM}
    news.write([item, dict(item)])
    assert len(news.recent()) == 1


def test_research_queue_orders_by_priority(db, repos):
    q = ResearchQueueRepository(db)
    low = repos["assets"].upsert(symbol="LOW", asset_class="equity")
    high = repos["assets"].upsert(symbol="HIGH", asset_class="equity")
    q.enqueue(low, priority=0.2, reason="routine")
    q.enqueue(high, priority=0.9, reason="fundamental acceleration")
    assert [r["symbol"] for r in q.pending()] == ["HIGH", "LOW"]


def test_scores_top_ranking(db, repos):
    from research_engine.core.types import OpportunityTier
    scores = ScoreRepository(db)
    for sym, val in (("A", 90.0), ("B", 40.0)):
        aid = repos["assets"].upsert(symbol=sym, asset_class="equity")
        scores.write(aid, as_of="2026-01-05", total_score=val,
                     tier=OpportunityTier.STRONG if val > 50 else OpportunityTier.WATCH,
                     components={"growth": {"score": val}},
                     data_quality=DataQuality.GOOD, coverage=0.9,
                     model_version="scoring_v1")
    top = scores.top("2026-01-05", limit=1)
    assert top[0]["symbol"] == "A" and top[0]["components"]["growth"]["score"] == 90.0
