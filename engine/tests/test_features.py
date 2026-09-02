"""Feature-engine tests: causality, correctness against known cases, and
graceful degradation when inputs are missing."""

import datetime as dt

import numpy as np
import pytest

from research_engine.core.series import PriceSeries
from research_engine.core.types import DataQuality, MarketRegime, Period, SourceTier
from research_engine.features import crypto as C
from research_engine.features import fundamental as F
from research_engine.features import macro as M
from research_engine.features import regime as G
from research_engine.features import returns as R
from research_engine.features import technical as T
from research_engine.features import valuation as V
from research_engine.storage.repositories import FundamentalPoint


# ------------------------------------------------------------ technicals ---
def test_indicators_are_causal(sample_series):
    """The value at bar i must not change when later bars are added."""
    full = T.compute_all(sample_series)
    truncated = T.compute_all(sample_series.as_of(sample_series.dates[300]))
    for name in ("sma_50", "ema_26", "rsi", "macd", "atr", "bb_upper", "adx", "mfi"):
        a, b = full[name][:301], truncated[name][:301]
        both = np.isfinite(a) & np.isfinite(b)
        assert both.sum() > 50, name
        assert np.allclose(a[both], b[both], atol=1e-9), name


def test_warmup_is_nan_not_backfilled():
    values = list(range(1, 60))
    assert np.all(np.isnan(T.sma(values, 20)[:19]))
    assert not np.isnan(T.sma(values, 20)[19])
    assert np.all(np.isnan(T.rsi(values, 14)[:14]))


def test_sma_matches_manual_average():
    values = [1, 2, 3, 4, 5, 6]
    assert T.sma(values, 3)[2] == pytest.approx(2.0)
    assert T.sma(values, 3)[-1] == pytest.approx(5.0)


def test_rsi_bounds_and_extremes():
    rising = [100 * 1.01 ** i for i in range(60)]
    falling = [100 * 0.99 ** i for i in range(60)]
    assert T.rsi(rising)[-1] > 95
    assert T.rsi(falling)[-1] < 5
    mixed = T.rsi([100 + (i % 5) for i in range(80)])
    finite = mixed[np.isfinite(mixed)]
    assert np.all((finite >= 0) & (finite <= 100))


def test_atr_positive_and_needs_window():
    high = [10 + i for i in range(40)]
    low = [9 + i for i in range(40)]
    close = [9.5 + i for i in range(40)]
    result = T.atr(high, low, close, 14)
    assert np.isnan(result[10])
    assert result[-1] > 0


def test_donchian_excludes_current_bar():
    high = [1, 2, 3, 4, 5, 100]
    low = [1, 1, 1, 1, 1, 1]
    channel = T.donchian(high, low, window=5)
    assert channel["upper"][5] == 5      # the 100 must not be in its own channel


def test_macd_crossover_shape():
    values = [100 + i for i in range(200)]
    result = T.macd(values)
    assert result.macd[-1] > 0            # rising series: fast above slow
    assert np.isfinite(result.signal[-1])
    assert result.histogram[-1] == pytest.approx(result.macd[-1] - result.signal[-1])


def test_drawdown_series_is_non_positive(sample_series):
    dd = T.drawdown_series(sample_series.adj_close)
    finite = dd[np.isfinite(dd)]
    assert np.all(finite <= 1e-12)
    assert finite.min() < 0


# --------------------------------------------------------------- returns ---
def test_statistics_refuse_small_samples():
    tiny = [0.01, -0.01, 0.02]
    assert R.sharpe_ratio(tiny) is None
    assert R.volatility(tiny) is None
    assert R.value_at_risk(tiny) is None


def test_known_return_maths():
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(300)]
    doubling = PriceSeries.from_closes("D", days, [100 * 1.01 ** i for i in range(300)])
    assert R.total_return(doubling) == pytest.approx(1.01 ** 299 - 1)
    assert R.max_drawdown(doubling.adj_close) == pytest.approx(0.0, abs=1e-9)


def test_drawdown_episodes_found():
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(30)]
    path = [100, 110, 120, 130, 100, 90, 95, 105, 135] + [140] * 21
    series = PriceSeries.from_closes("E", days, path)
    episodes = R.drawdown_episodes(series, min_depth=0.10)
    assert len(episodes) == 1
    assert episodes[0].depth == pytest.approx(90 / 130 - 1, abs=1e-6)
    assert episodes[0].recovery is not None


def test_beta_against_self_is_one(sample_series):
    beta, alpha = R.beta_alpha(sample_series, sample_series)
    assert beta == pytest.approx(1.0)
    assert alpha == pytest.approx(0.0, abs=1e-9)


# ----------------------------------------------------------- fundamentals --
def _hist(metric, values, start_year=2019, period=Period.ANNUAL):
    return [FundamentalPoint(metric=metric, period=period,
                             period_end=dt.date(start_year + i, 12, 31),
                             value=v, unit="USD",
                             filed_date=dt.date(start_year + i + 1, 2, 15),
                             source="test", source_tier=SourceTier.REGULATORY_FILING,
                             quality=DataQuality.EXCELLENT, form="10-K")
            for i, v in enumerate(values)]


COMPOUNDER = {
    "revenue": _hist("revenue", [1000, 1150, 1330, 1530, 1760, 2020]),
    "gross_profit": _hist("gross_profit", [700, 810, 940, 1080, 1250, 1440]),
    "operating_income": _hist("operating_income", [250, 300, 360, 430, 520, 620]),
    "net_income": _hist("net_income", [190, 230, 280, 340, 410, 490]),
    "total_assets": _hist("total_assets", [2000, 2200, 2400, 2700, 3000, 3300]),
    "total_liabilities": _hist("total_liabilities", [800, 850, 900, 950, 1000, 1050]),
    "total_equity": _hist("total_equity", [1200, 1350, 1500, 1750, 2000, 2250]),
    "current_assets": _hist("current_assets", [900, 1000, 1100, 1250, 1400, 1550]),
    "current_liabilities": _hist("current_liabilities", [400, 420, 450, 470, 500, 520]),
    "cash_and_equivalents": _hist("cash_and_equivalents", [500, 560, 620, 700, 800, 900]),
    "long_term_debt": _hist("long_term_debt", [300, 300, 280, 260, 240, 220]),
    "operating_cash_flow": _hist("operating_cash_flow", [230, 280, 340, 410, 500, 600]),
    "capex": _hist("capex", [50, 55, 60, 70, 80, 90]),
    "shares_diluted": _hist("shares_diluted", [100, 99, 98, 97, 96, 95]),
    "interest_expense": _hist("interest_expense", [12, 12, 11, 10, 9, 8]),
    "income_tax": _hist("income_tax", [50, 60, 74, 90, 108, 130]),
    "pretax_income": _hist("pretax_income", [240, 290, 354, 430, 518, 620]),
}


def test_growth_profile_and_durability():
    profile = F.growth_profile(COMPOUNDER, "revenue")
    assert profile["cagr_5y"] == pytest.approx((2020 / 1000) ** (1 / 5) - 1, abs=1e-6)
    assert profile["consistency"] == 1.0
    assert profile["stability"] > 0.8


def test_free_cash_flow_subtracts_capex():
    assert F.free_cash_flow(COMPOUNDER) == 600 - 90


def test_roic_uses_effective_tax_rate():
    roic = F.return_on_invested_capital(COMPOUNDER)
    nopat = 620 * (1 - 130 / 620)
    capital = 2250 + 220 - 900
    assert roic == pytest.approx(nopat / capital, rel=1e-6)


def test_negative_equity_roe_is_none():
    broken = dict(COMPOUNDER)
    broken["total_equity"] = _hist("total_equity", [-100, -120])
    assert F.return_on_equity(broken) is None


def test_moat_requires_evidence():
    strong = F.assess_moat(COMPOUNDER)
    assert strong.verdict in ("narrow", "wide")
    assert strong.supporting

    weak = {
        "revenue": _hist("revenue", [1000, 900, 950, 800]),
        "gross_profit": _hist("gross_profit", [200, 150, 170, 120]),
        "operating_income": _hist("operating_income", [50, 10, 20, -30]),
        "net_income": _hist("net_income", [30, 5, 10, -40]),
        "total_equity": _hist("total_equity", [500, 480, 450, 400]),
        "long_term_debt": _hist("long_term_debt", [400, 420, 450, 500]),
        "cash_and_equivalents": _hist("cash_and_equivalents", [50, 40, 30, 20]),
        "operating_cash_flow": _hist("operating_cash_flow", [40, 10, 15, -20]),
        "capex": _hist("capex", [30, 30, 30, 30]),
        "shares_diluted": _hist("shares_diluted", [100, 110, 125, 145]),
    }
    assert F.assess_moat(weak).verdict == "none"
    assert F.assess_moat(weak).contradicting


def test_snapshot_reports_missing_metrics():
    snap = F.build_snapshot("TEST", dt.date(2025, 6, 30), COMPOUNDER, market_cap=10_000)
    assert snap.value("roic") is not None
    assert snap.coverage() > 0.6
    sparse = F.build_snapshot("SPARSE", dt.date(2025, 6, 30),
                              {"revenue": _hist("revenue", [100, 110])})
    assert "roic" in sparse.missing
    assert sparse.value("free_cash_flow") is None


def test_altman_z_not_applied_to_financials():
    snap = F.build_snapshot("BANK", dt.date(2025, 6, 30), COMPOUNDER,
                            market_cap=10_000, sector="Financials")
    assert snap.value("altman_z") is None
    assert any("not applicable" in n for n in snap.notes)


# ------------------------------------------------------------- valuation ---
def test_dcf_discounting_is_correct():
    a = V.DcfAssumptions(base_fcf=100, revenue_growth=0.0, growth_fade_to=0.0,
                         fcf_margin=None, discount_rate=0.10, terminal_growth=0.0,
                         years=1, tax_rate=0.0, shares=1, net_debt=0)
    result = V.discounted_cash_flow(a)
    # one year of 100 discounted at 10%, plus a zero-growth perpetuity of 100
    assert result.equity_value == pytest.approx(100 / 1.1 + (100 / 0.10) / 1.1)


def test_dcf_rejects_impossible_assumptions():
    with pytest.raises(ValueError):
        V.discounted_cash_flow(V.DcfAssumptions(
            base_fcf=100, revenue_growth=0.05, growth_fade_to=0.03, fcf_margin=None,
            discount_rate=0.03, terminal_growth=0.05, years=10, tax_rate=0.2,
            shares=10, net_debt=0))


def test_dcf_flags_terminal_value_dominance():
    a = V.DcfAssumptions(base_fcf=10, revenue_growth=0.02, growth_fade_to=0.02,
                         fcf_margin=None, discount_rate=0.07, terminal_growth=0.05,
                         years=5, tax_rate=0.2, shares=10, net_debt=0)
    assert any("terminal value" in w for w in V.discounted_cash_flow(a).warnings)


def test_reverse_dcf_recovers_the_input_growth():
    a = V.DcfAssumptions(base_fcf=1000, revenue_growth=0.12, growth_fade_to=0.025,
                         fcf_margin=None, discount_rate=0.09, terminal_growth=0.025,
                         years=10, tax_rate=0.21, shares=100, net_debt=500)
    price = V.discounted_cash_flow(a).value_per_share
    implied = V.reverse_dcf(price=price, shares=100, base_fcf=1000, net_debt=500,
                            discount_rate=0.09, terminal_growth=0.025)
    assert implied["implied_growth"] == pytest.approx(0.12, abs=0.01)


def test_reverse_dcf_explains_impossible_prices():
    result = V.reverse_dcf(price=1e9, shares=100, base_fcf=1000, net_debt=0,
                           discount_rate=0.09, terminal_growth=0.025)
    assert result["implied_growth"] is None and "does not justify" in result["reason"]


def test_multiples_refuse_negative_denominators():
    m = V.compute_multiples(price=10, shares=100, earnings=-50, revenue=1000,
                            net_debt=0, ebitda=-10)
    assert m["pe"] is None and m["ev_ebitda"] is None
    assert m["ps"] == pytest.approx(1.0)


def test_scenarios_are_ordered_and_explained():
    scenarios = V.scenario_valuation(base_fcf=1000, shares=100, net_debt=500,
                                     growth=0.12, discount_rate=0.09,
                                     terminal_growth=0.025, price=200)
    assert scenarios.bear < scenarios.base < scenarios.bull
    assert scenarios.assumptions["discount_rate"] == 0.09
    returns = scenarios.expected_returns()
    assert returns["bear"] < returns["base"] < returns["bull"]


def test_blend_flags_method_disagreement():
    dcf = V.ValuationScenarios(80, 100, 120, "dcf", 100)
    multiples = V.ValuationScenarios(15, 20, 25, "multiples:ebitda", 100)
    blended = V.blend([dcf, multiples])
    assert any("disagree" in w for w in blended.warnings)


def test_sensitivity_grid_spreads():
    a = V.DcfAssumptions(base_fcf=1000, revenue_growth=0.10, growth_fade_to=0.025,
                         fcf_margin=None, discount_rate=0.09, terminal_growth=0.025,
                         years=10, tax_rate=0.21, shares=100, net_debt=0)
    grid = V.sensitivity_grid(a)
    assert grid["max"] > grid["min"] > 0
    assert grid["spread_ratio"] > 1.5      # honest uncertainty, not a point estimate


def test_cost_of_equity_clamped_and_explained():
    rate, note = V.cost_of_equity(risk_free_rate=0.04, equity_risk_premium=0.05,
                                  beta=8.0)
    assert rate <= 0.20 and "clamped" in note
    rate2, note2 = V.cost_of_equity(risk_free_rate=0.04, equity_risk_premium=0.05,
                                    beta=None)
    assert "beta unavailable" in note2


# ---------------------------------------------------------------- crypto ---
def test_supply_metrics_expose_dilution():
    metrics = C.supply_metrics(circulating=100, total=1000, max_supply=1000,
                               market_cap=1e9, fdv=1e10)
    assert metrics["float_ratio"] == pytest.approx(0.1)
    assert metrics["mcap_to_fdv"] == pytest.approx(0.1)
    assert metrics["implied_dilution"] == pytest.approx(0.9)


def test_unknown_unlocks_are_not_treated_as_zero():
    result = C.unlock_overhang([], as_of=dt.date(2026, 1, 1), circulating=1000)
    assert result["known"] is False
    assert result["pct_of_circulating"] is None
    assert "unknown, not as zero" in result["note"]


def test_unlock_overhang_measured_in_days_of_volume():
    unlocks = [{"unlock_date": "2026-02-01", "tokens": 1_000_000}]
    result = C.unlock_overhang(unlocks, as_of=dt.date(2026, 1, 1), circulating=10_000_000,
                               daily_volume_usd=5_000_000, price=10.0)
    assert result["pct_of_circulating"] == pytest.approx(0.1)
    assert result["days_of_volume"] == pytest.approx(2.0)


def test_crypto_risk_reports_unknown_factors(price_bars):
    series = PriceSeries.from_rows("TOK", price_bars(400, weekdays_only=False, vol=0.05))
    snap = C.build_snapshot("TOK", dt.date(2026, 1, 1),
                            market={"market_cap_usd": 5e8, "volume_24h_usd": 1e6,
                                    "circulating_supply": 1e8, "max_supply": 1e9,
                                    "fully_diluted_valuation_usd": 5e9},
                            series=series, quality_grade="emerging")
    risk = snap.metrics["risk_overall"].detail
    assert risk["coverage"] < 1.0
    assert risk["unknown_factors"]
    assert any("unlock schedule unavailable" in r for r in snap.risks)
    assert snap.value("mcap_to_fdv") == pytest.approx(0.1)


def test_equity_valuation_not_applied_to_crypto():
    # the crypto module exposes no P/E-style helpers at all
    assert not hasattr(C, "compute_multiples")
    assert not hasattr(C, "discounted_cash_flow")


# ----------------------------------------------------------------- macro ---
def _monthly(start_year, values):
    return [(dt.date(start_year + i // 12, i % 12 + 1, 1), v) for i, v in enumerate(values)]


def test_macro_stance_classification():
    state = M.build_state(dt.date(2026, 1, 1), {
        "CPIAUCSL": _monthly(2024, [100 + i * 0.5 for i in range(26)]),
        "FEDFUNDS": _monthly(2024, [5.0] * 26),
        "T10Y2Y": _monthly(2024, [-0.4] * 26),
        "BAMLH0A0HYM2": _monthly(2024, [7.0] * 26),
        "INDPRO": _monthly(2024, [100 - i * 0.2 for i in range(26)]),
    })
    assert state.stance["yield_curve"] == "inverted"
    assert state.stance["credit"] == "stressed"
    assert state.stance["growth"] == "contracting"
    assert state.stance["policy"] in ("restrictive", "accommodative", "neutral")
    assert any("Inverted yield curve" in e.label for e in state.evidence)


def test_macro_missing_series_reported():
    state = M.build_state(dt.date(2026, 1, 1), {"CPIAUCSL": []})
    assert "CPIAUCSL" in state.missing
    assert state.stance["yield_curve"] == "unknown"


def test_sector_adjustment_is_bounded_and_explained():
    state = M.build_state(dt.date(2026, 1, 1), {
        "CPIAUCSL": _monthly(2024, [100 + i * 0.6 for i in range(26)]),
        "FEDFUNDS": _monthly(2024, [6.0] * 26),
        "INDPRO": _monthly(2024, [100 - i * 0.3 for i in range(26)]),
    })
    adj = M.sector_adjustment("Real Estate", state)
    assert -0.15 <= adj["adjustment"] <= 0.15
    assert adj["reasons"]
    assert M.sector_adjustment("Nonexistent Sector", state)["applied"] is False


# ---------------------------------------------------------------- regime ---
def test_regime_needs_enough_history(price_bars):
    short = PriceSeries.from_rows("SPY", price_bars(100))
    assert G.classify(short).regime is MarketRegime.UNKNOWN


def test_bull_and_bear_regimes_detected():
    days = [dt.date(2022, 1, 3) + dt.timedelta(days=i) for i in range(600)]
    up = PriceSeries.from_closes("UP", days, [100 * 1.0015 ** i for i in range(600)])
    down = PriceSeries.from_closes("DOWN", days, [300 * 0.9985 ** i for i in range(600)])
    assert G.classify(up).regime is MarketRegime.BULL
    assert G.classify(down).regime is MarketRegime.BEAR


def test_regime_is_point_in_time(price_bars):
    series = PriceSeries.from_rows("SPY", price_bars(800))
    state = G.classify(series, as_of=series.dates[500])
    assert state.as_of == series.dates[500]


def test_breadth_calculation():
    days = [dt.date(2023, 1, 2) + dt.timedelta(days=i) for i in range(300)]
    rising = {f"UP{i}": PriceSeries.from_closes(f"UP{i}", days,
                                                [100 * 1.001 ** j for j in range(300)])
              for i in range(8)}
    falling = {f"DN{i}": PriceSeries.from_closes(f"DN{i}", days,
                                                 [100 * 0.999 ** j for j in range(300)])
               for i in range(4)}
    breadth = G.breadth_from_universe({**rising, **falling})
    assert breadth == pytest.approx(8 / 12, abs=0.01)
