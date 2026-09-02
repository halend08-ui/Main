import datetime as dt
import io
import logging

import pytest

from research_engine.core.errors import InsufficientData, LookAheadError
from research_engine.core.logging import (configure_logging, get_logger, redact,
                                          register_secret)
from research_engine.core.numeric import (cagr, correlation, fmt_pct, geometric_mean,
                                          median, ols_beta_alpha, pct_change,
                                          percentile, percentile_rank, robust_zscore,
                                          round_sig, safe_div, stdev, winsorize,
                                          zscore)
from research_engine.core.series import PriceSeries, aligned_returns
from research_engine.core.timeutil import (EQUITY_CALENDAR, CRYPTO_CALENDAR,
                                           as_of_context, current_as_of,
                                           infer_periods_per_year, years_between)
from research_engine.core.types import (DataQuality, EventImpact, Horizon,
                                        OpportunityTier, SourceTier)


# ------------------------------------------------------------- numerics ----
@pytest.mark.parametrize("fn,args", [
    (safe_div, (1, 0)), (safe_div, (None, 2)), (pct_change, (1, 0)),
    (pct_change, (1, None)), (cagr, (0, 10, 1)), (cagr, (10, 10, 0)),
    (zscore, ([1, 1, 1], 1)), (geometric_mean, ([1, -1],)),
])
def test_undefined_maths_returns_none_not_zero(fn, args):
    assert fn(*args) is None


def test_pct_change_off_negative_base_is_refused():
    # +50 from -100 is not "+150%"; the metric is meaningless, so we return None
    assert pct_change(50, -100) is None


def test_basic_statistics():
    assert median([3, 1, 2]) == 2
    assert stdev([1, 1, 1]) == 0
    assert stdev([1]) is None
    assert percentile([0, 10], 0.5) == 5
    assert percentile_rank([1, 2, 3, 4], 3) == 0.75
    assert cagr(100, 121, 2) == pytest.approx(0.1)


def test_robust_zscore_survives_outliers():
    data = [10, 11, 10, 12, 11, 10, 900]
    assert abs(robust_zscore(data, 900)) > abs(zscore(data, 900))


def test_winsorize_clips_extremes():
    data = list(range(100)) + [10_000]
    assert max(winsorize(data)) < 10_000


def test_no_false_precision():
    assert round_sig(1234.5678, 3) == 1230.0
    assert round_sig(0.000123456, 2) == 0.00012
    assert fmt_pct(0.12345) == "12.3%"
    assert fmt_pct(None) == "n/a"


def test_regression_needs_enough_overlap():
    assert ols_beta_alpha([0.1] * 5, [0.1] * 5) is None
    beta, alpha = ols_beta_alpha([2 * x for x in range(30)], list(range(30)))
    assert beta == pytest.approx(2.0)
    assert correlation([1, 2], [1, 2]) is None      # too few points


# --------------------------------------------------------------- series ----
def test_series_rejects_duplicates_and_sorts(price_bars):
    bars = price_bars(5)
    with pytest.raises(ValueError):
        PriceSeries.from_rows("D", [*bars, dict(bars[0])])


def test_series_as_of_is_the_lookahead_guard(sample_series):
    cut = sample_series.as_of(sample_series.dates[100])
    assert len(cut) == 101
    cut.require_no_future(cut.end)
    with pytest.raises(LookAheadError):
        cut.require_no_future(cut.dates[50])


def test_series_requires_data():
    with pytest.raises(InsufficientData):
        PriceSeries("EMPTY", [])


def test_aligned_returns_intersect_dates():
    days = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    a = PriceSeries.from_closes("A", days, [100 + i for i in range(10)])
    b = PriceSeries.from_closes("B", days[3:], [50 + i for i in range(7)])
    ra, rb = aligned_returns(a, b)
    assert len(ra) == len(rb) == 6


# ----------------------------------------------------------------- time ----
def test_calendars():
    assert not EQUITY_CALENDAR.is_session(dt.date(2026, 1, 3))
    assert CRYPTO_CALENDAR.is_session(dt.date(2026, 1, 3))
    assert EQUITY_CALENDAR.count_sessions(dt.date(2026, 1, 5), dt.date(2026, 1, 9)) == 5
    assert years_between("2020-01-01", "2025-01-01") == pytest.approx(5.0, abs=0.01)


def test_as_of_context_pins_the_clock():
    with as_of_context("2019-05-04"):
        assert current_as_of().date() == dt.date(2019, 5, 4)
    assert current_as_of().year >= 2024


def test_periods_per_year_inference():
    daily = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(90)]
    assert infer_periods_per_year(daily) == 365
    assert infer_periods_per_year([d for d in daily if d.weekday() < 5]) == 252
    monthly = [dt.date(2020, m, 1) for m in range(1, 13)]
    assert infer_periods_per_year(monthly) == 12


# ---------------------------------------------------------------- types ----
def test_ordered_enums_compare():
    assert DataQuality.EXCELLENT > DataQuality.POOR
    assert SourceTier.REGULATORY_FILING > SourceTier.SOCIAL_MEDIA
    assert OpportunityTier.EXCEPTIONAL > OpportunityTier.WATCH
    assert EventImpact.EXTREMELY_NEGATIVE.polarity == -1.0
    assert DataQuality.from_score(0.2) is DataQuality.INSUFFICIENT
    assert Horizon.Y1.trading_days == 252


# -------------------------------------------------------------- logging ----
def test_secrets_never_reach_the_log():
    buf = io.StringIO()
    configure_logging("DEBUG", json_output=True, stream=buf, secrets=["TOPSECRET123"])
    log = get_logger("test")
    log.info("fetch https://api/v1?api_key=TOPSECRET123", token="TOPSECRET123")
    output = buf.getvalue()
    assert "TOPSECRET123" not in output
    assert "REDACTED" in output
    logging.getLogger().handlers.clear()


def test_redaction_catches_key_patterns():
    assert "abcdef123456" not in redact("Authorization: abcdef123456")
    assert "sk-livekey99" not in redact("?api_key=sk-livekey99&x=1")
