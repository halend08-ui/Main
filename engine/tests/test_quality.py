"""Quality-layer tests, including synthetic corruptions that MUST be detected."""

import datetime as dt

import pytest

from research_engine.core.errors import LookAheadError
from research_engine.core.series import PriceSeries
from research_engine.core.timeutil import CRYPTO_CALENDAR, EQUITY_CALENDAR
from research_engine.core.types import DataQuality, Period, SourceTier
from research_engine.quality.bias import (assert_no_future_listings,
                                          assert_point_in_time,
                                          check_label_horizon_embargo,
                                          check_survivorship,
                                          check_train_test_separation,
                                          detect_lookahead_in_features,
                                          overfitting_pressure,
                                          shifted_signal_is_safe)
from research_engine.quality.checks import (Severity, check_fundamentals,
                                            check_news, check_price_series)
from research_engine.quality.grading import (combine, confidence_multiplier,
                                             finalize, grade_from_issues)
from research_engine.storage.repositories import FundamentalPoint


def _series(bars):
    return PriceSeries.from_rows("TEST", bars)


# --------------------------------------------------------------- prices ----
def test_clean_series_grades_well(price_bars):
    bars = price_bars(400, start=dt.date(2024, 1, 1))
    report = finalize(check_price_series(_series(bars), as_of=bars[-1]["date"],
                                         calendar=EQUITY_CALENDAR))
    assert report.usable
    assert report.grade >= DataQuality.GOOD, [i.to_dict() for i in report.issues]


def test_negative_price_is_fatal(price_bars):
    bars = price_bars(200)
    bars[50]["close"] = -5.0
    report = finalize(check_price_series(_series(bars)))
    assert not report.usable
    assert report.grade is DataQuality.INSUFFICIENT
    assert "price.nonpositive" in report.codes()


def test_high_below_low_detected(price_bars):
    bars = price_bars(200)
    bars[10]["high"], bars[10]["low"] = 1.0, 100.0
    report = check_price_series(_series(bars))
    assert "price.ohlc_inconsistent" in report.codes()


def test_stale_prices_detected(price_bars):
    bars = price_bars(200, start=dt.date(2024, 1, 1))
    report = check_price_series(_series(bars),
                                as_of=bars[-1]["date"] + dt.timedelta(days=30))
    assert "price.stale" in report.codes()


def test_unadjusted_split_detected(price_bars):
    bars = price_bars(300)
    for bar in bars[150:]:                      # 2:1 split never applied backwards
        for key in ("open", "high", "low", "close", "adj_close"):
            bar[key] = bar[key] / 2
    report = check_price_series(_series(bars))
    assert "price.suspected_unadjusted_split" in report.codes()


def test_frozen_feed_detected(price_bars):
    bars = price_bars(200)
    for bar in bars[-10:]:
        bar["close"] = bar["adj_close"] = 42.0
    report = check_price_series(_series(bars))
    assert "price.flatline" in report.codes()


def test_calendar_gaps_detected(price_bars):
    bars = price_bars(300, start=dt.date(2024, 1, 1))
    kept = bars[:100] + bars[160:]              # ~3 months missing
    report = check_price_series(_series(kept), as_of=kept[-1]["date"],
                                calendar=EQUITY_CALENDAR)
    assert "price.gaps" in report.codes() or "price.date_gaps" in report.codes()
    assert report.coverage is not None and report.coverage < 0.9


def test_short_history_flagged(price_bars):
    bars = price_bars(30, start=dt.date(2026, 1, 1))
    report = check_price_series(_series(bars), as_of=bars[-1]["date"])
    assert "price.short_history" in report.codes()


def test_zero_volume_run_flagged(price_bars):
    bars = price_bars(200)
    for bar in bars[-20:]:
        bar["volume"] = 0
    report = check_price_series(_series(bars))
    assert "price.zero_volume_run" in report.codes()


# --------------------------------------------------------- fundamentals ----
def _fp(metric, end, value, filed=None, period=Period.ANNUAL, form="10-K"):
    end_d = dt.date.fromisoformat(end)
    return FundamentalPoint(metric=metric, period=period, period_end=end_d,
                            value=value, unit="USD",
                            filed_date=dt.date.fromisoformat(filed) if filed else end_d,
                            source="test", source_tier=SourceTier.REGULATORY_FILING,
                            quality=DataQuality.EXCELLENT, form=form)


def test_missing_core_fundamentals_is_fatal():
    report = check_fundamentals({"revenue": [_fp("revenue", "2025-12-31", 100)]},
                                subject="X", as_of=dt.date(2026, 3, 1))
    assert "fundamentals.missing_core" in report.codes()
    assert not report.usable


def test_impossible_negative_revenue_detected():
    data = {
        "revenue": [_fp("revenue", "2025-12-31", -50)],
        "net_income": [_fp("net_income", "2025-12-31", 10)],
        "total_assets": [_fp("total_assets", "2025-12-31", 500)],
        "total_equity": [_fp("total_equity", "2025-12-31", 200)],
        "operating_cash_flow": [_fp("operating_cash_flow", "2025-12-31", 30)],
    }
    report = check_fundamentals(data, subject="X", as_of=dt.date(2026, 3, 1))
    assert "fundamentals.negative_revenue" in report.codes()


def test_balance_sheet_identity_violation_detected():
    data = {
        "revenue": [_fp("revenue", "2025-12-31", 100)],
        "net_income": [_fp("net_income", "2025-12-31", 10)],
        "total_assets": [_fp("total_assets", "2025-12-31", 1000)],
        "total_liabilities": [_fp("total_liabilities", "2025-12-31", 400)],
        "total_equity": [_fp("total_equity", "2025-12-31", 100)],   # 400+100 != 1000
        "operating_cash_flow": [_fp("operating_cash_flow", "2025-12-31", 30)],
    }
    report = check_fundamentals(data, subject="X", as_of=dt.date(2026, 3, 1))
    assert "fundamentals.balance_sheet_identity" in report.codes()


def test_missing_fiscal_year_detected():
    revenue = [_fp("revenue", "2021-12-31", 80), _fp("revenue", "2022-12-31", 90),
               _fp("revenue", "2025-12-31", 120)]
    data = {"revenue": revenue,
            "net_income": [_fp("net_income", "2025-12-31", 10)],
            "total_assets": [_fp("total_assets", "2025-12-31", 500)],
            "total_equity": [_fp("total_equity", "2025-12-31", 200)],
            "operating_cash_flow": [_fp("operating_cash_flow", "2025-12-31", 30)]}
    report = check_fundamentals(data, subject="X", as_of=dt.date(2026, 3, 1))
    assert "fundamentals.gap_revenue" in report.codes()


# --------------------------------------------------------------- grading ---
def test_thin_data_cannot_be_excellent():
    score, grade = grade_from_issues([], observations=10, min_observations=60)
    assert grade <= DataQuality.FAIR


def test_fatal_issue_zeroes_score(price_bars):
    bars = price_bars(200)
    bars[3]["close"] = 0.0
    report = finalize(check_price_series(_series(bars)))
    assert report.score == 0.0


def test_combine_is_contagious_on_fatal(price_bars):
    good = finalize(check_price_series(_series(price_bars(400, start=dt.date(2024, 1, 1)))))
    bad = check_fundamentals({}, subject="X", as_of=dt.date(2026, 1, 1))
    score, grade = combine([good, bad])
    assert grade is DataQuality.INSUFFICIENT and score == 0.0


def test_confidence_multiplier_is_monotonic():
    grades = [DataQuality.INSUFFICIENT, DataQuality.POOR, DataQuality.FAIR,
              DataQuality.GOOD, DataQuality.EXCELLENT]
    values = [confidence_multiplier(g) for g in grades]
    assert values == sorted(values)
    assert confidence_multiplier(DataQuality.INSUFFICIENT) == 0.0


# ------------------------------------------------------------------ bias ---
def test_point_in_time_violation_raises():
    records = [{"metric": "revenue", "filed_date": dt.date(2026, 5, 1)}]
    assert_point_in_time(records, as_of=dt.date(2026, 6, 1))       # fine
    with pytest.raises(LookAheadError):
        assert_point_in_time(records, as_of=dt.date(2026, 4, 1))


def test_feature_lookahead_detected():
    features = [dt.date(2026, 1, 5), dt.date(2026, 1, 6)]
    sources = [dt.date(2026, 1, 4), dt.date(2026, 1, 9)]
    finding = detect_lookahead_in_features(features, sources)
    assert finding is not None and finding.kind == "look_ahead"


def test_same_bar_execution_is_flagged():
    days = [dt.date(2026, 1, i) for i in (5, 6, 7)]
    assert shifted_signal_is_safe(days, days) is not None
    assert shifted_signal_is_safe(days, [d + dt.timedelta(days=1) for d in days]) is None


def test_survivorship_detected_in_clean_universe():
    universe = [{"symbol": f"S{i}", "listed_date": "2010-01-01"} for i in range(100)]
    finding = check_survivorship(universe, as_of=dt.date(2020, 1, 1))
    assert finding is not None and finding.kind == "survivorship"
    universe[0]["delisted_date"] = "2015-05-05"
    assert check_survivorship(universe, as_of=dt.date(2020, 1, 1)) is None


def test_future_listings_rejected():
    with pytest.raises(LookAheadError):
        assert_no_future_listings([{"symbol": "IPO", "listed_date": "2026-01-01"}],
                                  dt.date(2020, 1, 1))


def test_train_test_embargo_enforced():
    assert check_train_test_separation(dt.date(2015, 1, 1), dt.date(2019, 12, 31),
                                       dt.date(2020, 1, 6), dt.date(2020, 12, 31),
                                       embargo_days=5) is None
    overlap = check_train_test_separation(dt.date(2015, 1, 1), dt.date(2020, 6, 30),
                                          dt.date(2020, 1, 1), dt.date(2020, 12, 31))
    assert overlap is not None and overlap.kind == "leakage"
    touching = check_train_test_separation(dt.date(2015, 1, 1), dt.date(2019, 12, 31),
                                           dt.date(2020, 1, 2), dt.date(2020, 12, 31),
                                           embargo_days=5)
    assert touching is not None


def test_label_horizon_must_fit_in_embargo():
    assert check_label_horizon_embargo(21, 5) is not None
    assert check_label_horizon_embargo(5, 21) is None


def test_overfitting_pressure_penalises_search():
    light = overfitting_pressure(parameters=3, observations=5000, configurations_tried=1)
    heavy = overfitting_pressure(parameters=40, observations=300, configurations_tried=500)
    assert light["deflation_factor"] > heavy["deflation_factor"]
    assert heavy["warnings"]


# ------------------------------------------------------------------ news ---
def test_syndicated_news_detected():
    class Item:
        def __init__(self, headline, tier=SourceTier.FINANCIAL_JOURNALISM):
            self.headline = headline
            self.source_tier = tier
            self.published_at = dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)

    items = [Item("Acme beats estimates")] * 8 + [Item("Something else")]
    report = check_news(items, subject="ACME", as_of=dt.date(2026, 1, 6))
    assert "news.duplicated" in report.codes()
