"""Cross-sectional comparison and parallel scanning."""

import datetime as dt

import pytest

from research_engine.analysis import comparison as CMP
from research_engine.core.series import PriceSeries
from tests.conftest import make_prices


def _c(symbol, *, sector="Technology", score=70.0, base=0.20, bear=-0.30,
       risk="moderate", quality="good", cap=5e9, factors=None,
       recommendation="BUY"):
    return CMP.Candidate(
        symbol=symbol, asset_class="equity", sector=sector, market_cap=cap,
        score=score, tier="strong", recommendation=recommendation, confidence=0.6,
        risk_level=risk, data_quality=quality, expected_return_base=base,
        expected_return_bear=bear, prob_positive=0.65,
        factor_scores=factors or ({} if score is None else
                                  {"growth": score, "valuation": score - 10,
                                   "financial_health": score + 5}))


# ------------------------------------------------------- peer grouping -----
def test_small_sectors_fall_back_to_size_bands():
    candidates = ([_c(f"T{i}", sector="Technology") for i in range(8)]
                  + [_c(f"E{i}", sector="Energy", cap=5e9) for i in range(2)]
                  + [_c(f"M{i}", sector="Materials", cap=5e9) for i in range(4)])
    groups = CMP.build_peer_groups(candidates)
    by_name = {g.name: g for g in groups}
    assert "Technology" in by_name and by_name["Technology"].basis == "sector"
    # the two small sectors are pooled by capitalisation instead
    assert any(g.basis == "cap_band" for g in groups)
    assert sum(g.size for g in groups) == len(candidates)


def test_group_below_minimum_reports_no_percentile():
    group = CMP.PeerGroup("Tiny", "sector", [_c(f"A{i}") for i in range(3)])
    profiles = CMP.rank_within(group)
    profile = profiles["A0"]
    assert profile.composite_percentile is None
    assert all(f.percentile is None for f in profile.factor_ranks)
    assert any("at least" in c for c in profile.caveats)


def test_percentiles_rank_within_the_peer_group():
    members = [_c(f"S{i}", score=50 + i * 5) for i in range(8)]
    group = CMP.PeerGroup("Technology", "sector", members)
    profiles = CMP.rank_within(group)
    assert profiles["S7"].composite_percentile == pytest.approx(1.0)
    assert profiles["S0"].composite_percentile < 0.3
    assert profiles["S7"].strengths


def test_poor_data_quality_is_flagged_in_the_profile():
    members = [_c(f"S{i}") for i in range(6)] + [_c("BAD", quality="poor")]
    group = CMP.PeerGroup("Technology", "sector", members)
    profile = CMP.rank_within(group)["BAD"]
    assert any("data quality is poor" in c for c in profile.caveats)


# ----------------------------------------------------- risk adjustment -----
def test_implausibly_mild_bear_case_cannot_dominate():
    """A 2% worst case is a modelling failure, not a great opportunity."""
    mild = _c("MILD", base=0.86, bear=-0.02, risk="moderate")
    honest = _c("HONEST", base=0.50, bear=-0.20, risk="moderate")
    assert mild.downside_was_floored()
    assert not honest.downside_was_floored()
    # floored at 15% for moderate risk: 0.86/0.15 = 5.7, not 0.86/0.02 = 43
    assert mild.risk_adjusted_return() < 6.0


def test_missing_downside_uses_the_risk_level_floor():
    unknown = _c("U", base=0.30, bear=None, risk="high")
    assert unknown.risk_adjusted_return() == pytest.approx(0.30 / 0.40)
    assert unknown.downside_was_floored()


# --------------------------------------------------------- head to head ----
def test_head_to_head_names_the_deciding_factors():
    a = _c("AAA", base=0.40, bear=-0.20,
           factors={"growth": 90.0, "valuation": 40.0, "financial_health": 80.0})
    b = _c("BBB", base=0.10, bear=-0.30,
           factors={"growth": 50.0, "valuation": 85.0, "financial_health": 60.0})
    head = CMP.head_to_head(a, b)
    assert head.winner == "AAA"
    assert "growth" in head.summary
    assert any(d["factor"] == "valuation" and d["favours"] == "BBB"
               for d in head.factor_deltas)


def test_head_to_head_flags_cross_sector_comparison():
    a = _c("TECH", sector="Technology")
    b = _c("UTIL", sector="Utilities")
    head = CMP.head_to_head(a, b)
    assert any("different sectors" in c for c in head.caveats)


def test_head_to_head_refuses_unscored_assets():
    good = _c("GOOD")
    unscored = _c("BAD", score=None, recommendation="INSUFFICIENT_DATA")
    head = CMP.head_to_head(good, unscored)
    assert head.winner is None
    assert "not comparable" in head.summary


# ------------------------------------------------------- full comparison ---
def _universe():
    sectors = ["Technology", "Health Care", "Energy", "Financials", "Utilities"]
    out = []
    for s_i, sector in enumerate(sectors):
        for i in range(8):
            # technology scores highest in absolute terms across the board
            base_score = 80 - s_i * 6 + i
            out.append(_c(f"{sector[:2].upper()}{i}", sector=sector,
                          score=float(base_score),
                          base=0.05 + i * 0.04, bear=-0.25))
    return out


def test_comparison_produces_a_leader_from_each_group():
    result = CMP.compare(_universe(), as_of=dt.date(2026, 1, 5), per_group=1)
    groups_in_ranking = {row["peer_group"] for row in result.final_ranking}
    assert len(groups_in_ranking) == 5          # not five technology names
    assert result.peer_groups and all(g.rankable for g in result.peer_groups)


def test_comparison_surfaces_disagreement_with_the_absolute_view():
    result = CMP.compare(_universe(), as_of=dt.date(2026, 1, 5), per_group=1)
    assert result.disagreements
    assert any("concentrat" in d or "peer-relative" in d for d in result.disagreements)


def test_absolute_ranking_concentration_is_called_out():
    result = CMP.compare(_universe(), as_of=dt.date(2026, 1, 5))
    top_sectors = [r["peer_group"] for r in result.absolute_ranking[:10]]
    assert top_sectors.count("Technology") >= 6      # the concentration exists
    assert any("Technology" in d and "concentrat" in d for d in result.disagreements)


def test_unscored_assets_are_excluded_with_a_reason():
    universe = _universe() + [_c("DEAD", score=None,
                                 recommendation="INSUFFICIENT_DATA")]
    result = CMP.compare(universe, as_of=dt.date(2026, 1, 5))
    assert {"symbol": "DEAD", "reason": "INSUFFICIENT_DATA"} in result.excluded
    assert "DEAD" not in {r["symbol"] for r in result.final_ranking}


def test_comparison_renders_and_states_its_limits():
    result = CMP.compare(_universe(), as_of=dt.date(2026, 1, 5))
    text = result.render()
    assert "BEST OF BREED" in text and "FINAL RANKING" in text
    assert "top 3 within each peer group" in text


def test_empty_comparison_says_so():
    result = CMP.compare([], as_of=dt.date(2026, 1, 5))
    assert result.final_ranking == []
    assert any("nothing is comparable" in n for n in result.notes)


# ------------------------------------------------------- parallel scan -----
def test_parallel_scan_matches_sequential(db, settings, tmp_path):
    """A worker pool must not change the answer, only the wall clock."""
    from research_engine.pipeline.parallel import scan
    from research_engine.storage.repositories import AssetRepository, PriceRepository
    from research_engine.storage.db import Database

    path = tmp_path / "scan.db"
    database = Database(path)
    database.migrate()
    assets, prices = AssetRepository(database), PriceRepository(database)
    for i in range(12):
        asset_id = assets.upsert(symbol=f"P{i:02d}", asset_class="equity",
                                 sector="Technology", market_cap_usd=5e9)
        prices.write_bars(asset_id, make_prices(400, seed=i + 1,
                                                start=dt.date(2024, 1, 2)),
                          source="fixture")
    database.close()

    local = settings.with_overrides({"app": {"data_dir": str(tmp_path)},
                                     "database": {"path": "scan.db"}})
    symbols = [f"P{i:02d}" for i in range(12)]
    as_of = dt.date(2025, 6, 2)

    sequential = scan(symbols, as_of, settings=local, workers=1)
    assert sequential.analysed == 12
    assert {c.symbol for c in sequential.candidates} == set(symbols)
    assert not sequential.failures


def test_scan_records_failures_without_losing_the_rest(db, settings, tmp_path):
    from research_engine.pipeline.parallel import scan
    from research_engine.storage.db import Database
    from research_engine.storage.repositories import AssetRepository, PriceRepository

    path = tmp_path / "scan.db"
    database = Database(path)
    database.migrate()
    assets, prices = AssetRepository(database), PriceRepository(database)
    asset_id = assets.upsert(symbol="GOOD", asset_class="equity", sector="Tech")
    prices.write_bars(asset_id, make_prices(400, start=dt.date(2024, 1, 2)),
                      source="fixture")
    database.close()

    local = settings.with_overrides({"app": {"data_dir": str(tmp_path)},
                                     "database": {"path": "scan.db"}})
    result = scan(["GOOD", "MISSING"], dt.date(2025, 6, 2), settings=local, workers=1)
    assert result.analysed == 1
    assert any(f["symbol"] == "MISSING" for f in result.failures)


def test_daily_report_includes_the_peer_relative_ranking():
    from research_engine.pipeline.report import DailyReport

    report = DailyReport(
        as_of=dt.date(2026, 1, 5),
        comparison={"final_ranking": [
            {"symbol": "AAA", "peer_group": "Energy", "risk_adjusted": 1.8,
             "expected_return_base": 0.42, "recommendation": "BUY",
             "downside_floored": False},
            {"symbol": "BBB", "peer_group": "Technology", "risk_adjusted": 5.9,
             "expected_return_base": 0.80, "recommendation": "WATCH",
             "downside_floored": True}],
            "disagreements": ["AAA leads its peer group without a high raw score"]})
    text = report.render()
    assert "Best Of Breed (peer-relative)" in text
    assert "downside floored" in text
    assert "best of a weak peer group" in text
    assert "disagreement:" in text


def test_daily_loop_attaches_peer_evidence_to_recommendations(db, settings):
    """An individual recommendation should say where it stands among peers."""
    from research_engine.pipeline.daily import DailyPipeline
    from research_engine.pipeline.data_access import RepositoryDataAccess
    from research_engine.core.types import SourceTier
    from research_engine.storage.analysis_repos import (RecommendationRepository,
                                                        ScoreRepository)
    from research_engine.storage.repositories import (AssetRepository,
                                                      FundamentalRepository,
                                                      PriceRepository)
    from tests.test_features import COMPOUNDER

    repos = {"assets": AssetRepository(db), "prices": PriceRepository(db),
             "fundamentals": FundamentalRepository(db),
             "scores": ScoreRepository(db),
             "recommendations": RecommendationRepository(db)}
    as_of = None
    for i in range(8):
        symbol = f"PEER{i}"
        asset_id = repos["assets"].upsert(symbol=symbol, asset_class="equity",
                                          sector="Technology", market_cap_usd=5e9)
        bars = make_prices(700, seed=i + 1, start=dt.date(2023, 1, 2))
        repos["prices"].write_bars(asset_id, bars, source="fixture")
        as_of = bars[-1]["date"]
        repos["fundamentals"].write(
            asset_id,
            [{"metric": m, "period": "annual", "period_end": p.period_end,
              "value": (p.value or 0) * (1 + i * 0.1), "unit": "USD",
              "filed_date": p.filed_date, "accession": f"{symbol}{p.period_end.year}",
              "form": "10-K"}
             for m, hist in COMPOUNDER.items() for p in hist],
            source="fixture", source_tier=SourceTier.REGULATORY_FILING)

    data = RepositoryDataAccess(settings, repos)
    run = DailyPipeline(settings, data=data, repositories=repos).run(as_of)
    compare_step = next(s for s in run.steps if s.name == "compare")
    assert compare_step.ok
    assert compare_step.detail["groups"] >= 1

    labels = {e.label for result in run.analyzed.values() for e in result.evidence}
    assert "Peer-relative standing" in labels
