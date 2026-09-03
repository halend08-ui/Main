"""Backtest tests, including deliberate attempts to cheat that must fail."""

import datetime as dt

import pytest

from research_engine.backtest.costs import CostModel
from research_engine.backtest.engine import (AssetHistory, Backtester, BacktestConfig)
from research_engine.backtest.metrics import (compare_to_benchmarks, deflated_metrics,
                                              summarize_curve, summarize_trades)
from research_engine.core.errors import LookAheadError
from research_engine.core.series import PriceSeries
from tests.conftest import make_prices


def _universe(price_bars, symbols=("A", "B", "C", "D"), n=900, **kwargs):
    return {s: AssetHistory(symbol=s,
                            series=PriceSeries.from_rows(s, price_bars(
                                n, seed=i + 1, start=dt.date(2022, 1, 3), **kwargs)),
                            listed_date=dt.date(2020, 1, 1), market_cap=5e9)
            for i, s in enumerate(symbols)}


def _config(universe, **overrides):
    dates = next(iter(universe.values())).series.dates
    defaults = dict(start=dates[60], end=dates[-1], initial_capital=100_000.0,
                    rebalance_days=21, max_positions=3, max_position_weight=0.34,
                    warn_on_survivorship=False)
    defaults.update(overrides)
    return BacktestConfig(**defaults)


# ----------------------------------------------------------------- costs ---
def test_costs_scale_with_participation():
    model = CostModel()
    small = model.estimate(notional=10_000, adv=10_000_000, volatility=0.3)
    large = model.estimate(notional=500_000, adv=10_000_000, volatility=0.3)
    assert large["bps"] > small["bps"]
    assert large["impact"] > small["impact"]


def test_oversized_orders_are_rejected_not_filled():
    model = CostModel(max_participation=0.10)
    result = model.estimate(notional=5_000_000, adv=1_000_000)
    assert not result["executable"]
    assert "average daily volume" in result["reason"]


def test_missing_volume_makes_execution_unverifiable():
    result = CostModel().estimate(notional=1000, adv=None)
    assert not result["executable"]


def test_crypto_and_microcap_spreads_are_wider():
    model = CostModel()
    assert model.spread_for(is_crypto=True, market_cap=None) > \
           model.spread_for(is_crypto=False, market_cap=5e10)
    assert model.spread_for(is_crypto=False, market_cap=1e8) > \
           model.spread_for(is_crypto=False, market_cap=5e10)


# --------------------------------------------------------------- engine ----
def test_universe_as_of_truncates_history(price_bars):
    universe = _universe(price_bars)
    engine = Backtester(universe, _config(universe))
    cutoff = universe["A"].series.dates[200]
    visible = engine.universe_as_of(cutoff)
    for asset in visible.values():
        assert asset.series.end <= cutoff
        asset.series.require_no_future(cutoff)      # must not raise


def test_strategy_cannot_see_the_future(price_bars):
    """A strategy that tries to read tomorrow's price gets no such data."""
    universe = _universe(price_bars)
    engine = Backtester(universe, _config(universe))
    seen_ends: list[dt.date] = []

    def peeking_strategy(as_of, visible):
        for asset in visible.values():
            seen_ends.append(asset.series.end)
            with pytest.raises(LookAheadError):
                asset.series.require_no_future(as_of - dt.timedelta(days=1))
        return {"A": 0.3}

    engine.run(peeking_strategy)
    assert seen_ends and all(end <= universe["A"].series.dates[-1] for end in seen_ends)


def test_execution_happens_after_the_signal(price_bars):
    universe = _universe(price_bars)
    engine = Backtester(universe, _config(universe))
    decision_days: list[dt.date] = []

    def strategy(as_of, visible):
        decision_days.append(as_of)
        return {"A": 0.3, "B": 0.3}

    result = engine.run(strategy)
    assert result.trades
    for trade in result.trades:
        # every entry must be strictly after the decision date that preceded it
        assert any(d < trade["entry_date"] for d in decision_days)
    assert not any("execute on or before" in w for w in result.warnings)


def test_delisted_positions_are_liquidated_with_a_haircut(price_bars):
    universe = _universe(price_bars, symbols=("A", "B"))
    delist_date = universe["B"].series.dates[400]
    universe["B"] = AssetHistory(symbol="B", series=universe["B"].series,
                                 listed_date=dt.date(2020, 1, 1),
                                 delisted_date=delist_date, market_cap=5e9)
    engine = Backtester(universe, _config(universe, delisting_recovery=0.2))
    result = engine.run(lambda as_of, visible: {s: 0.3 for s in visible})
    delisting_trades = [t for t in result.trades if "delisted" in (t.get("reason") or "")]
    assert delisting_trades
    assert delisting_trades[0]["return_pct"] < -0.5      # the haircut is felt


def test_assets_not_yet_listed_are_excluded(price_bars):
    universe = _universe(price_bars, symbols=("A", "B"))
    late = universe["B"].series.dates[500]
    universe["B"] = AssetHistory(symbol="B", series=universe["B"].series,
                                 listed_date=late, market_cap=5e9)
    engine = Backtester(universe, _config(universe))
    early = universe["A"].series.dates[100]
    assert "B" not in engine.universe_as_of(early)
    assert "B" in engine.universe_as_of(universe["A"].series.dates[600])


def test_survivorship_warning_when_no_asset_ever_dies(price_bars):
    universe = _universe(price_bars, symbols=tuple(f"S{i}" for i in range(60)), n=400)
    engine = Backtester(universe, _config(universe, warn_on_survivorship=True))
    result = engine.run(lambda as_of, visible: {})
    assert any("delisted" in w for w in result.warnings)


def test_illiquid_orders_are_rejected_and_recorded(price_bars):
    universe = {"TINY": AssetHistory(
        symbol="TINY",
        series=PriceSeries.from_rows("TINY", price_bars(400, volume=500,
                                                        start=dt.date(2023, 1, 2))),
        listed_date=dt.date(2020, 1, 1), market_cap=5e7)}
    engine = Backtester(universe, _config(universe, max_position_weight=0.5))
    result = engine.run(lambda as_of, visible: {"TINY": 0.5})
    assert result.rejected_orders
    assert any("average daily volume" in r["reason"] for r in result.rejected_orders)


def test_costs_reduce_returns(price_bars):
    universe = _universe(price_bars, symbols=("A", "B"))
    strategy = lambda as_of, visible: {s: 0.4 for s in visible}

    free = Backtester(universe, _config(universe, rebalance_days=5,
                                        cost_model=CostModel(commission_bps=0,
                                                             spread_bps=0,
                                                             impact_coefficient=0.0)))
    costly = Backtester(universe, _config(universe, rebalance_days=5,
                                          cost_model=CostModel(commission_bps=20,
                                                               spread_bps=40)))
    free_result = free.run(strategy)
    costly_result = costly.run(strategy)
    assert costly_result.equity[-1] < free_result.equity[-1]
    assert costly_result.trade_metrics["total_costs"] > 0


def test_benchmarks_include_cash(price_bars):
    universe = _universe(price_bars, symbols=("A", "B"))
    benchmark = PriceSeries.from_rows("SPY", make_prices(900, seed=42,
                                                         start=dt.date(2022, 1, 3)))
    engine = Backtester(universe, _config(universe, risk_free_rate=0.04),
                        benchmark=benchmark)
    result = engine.run(lambda as_of, visible: {"A": 0.3})
    assert "cash" in result.benchmarks
    assert "benchmark" in result.benchmarks
    assert result.benchmarks["cash"]["cagr"] == pytest.approx(0.04, abs=0.005)


def test_equity_curve_is_realisable_at_the_end(price_bars):
    universe = _universe(price_bars, symbols=("A", "B"))
    engine = Backtester(universe, _config(universe))
    result = engine.run(lambda as_of, visible: {s: 0.4 for s in visible})
    # final equity is cash only: positions are liquidated at real prices
    assert result.equity[-1] > 0
    assert all(t["exit_date"] is not None for t in result.trades)


# --------------------------------------------------------- walk-forward ----
def test_walk_forward_folds_are_separated_by_an_embargo(price_bars):
    universe = _universe(price_bars, symbols=("A", "B", "C"), n=1800)
    config = _config(universe, train_window_days=400, test_window_days=200,
                     step_days=200, embargo_days=30, label_horizon_days=21)
    engine = Backtester(universe, config)
    result = engine.walk_forward(lambda ts, te: (lambda as_of, visible:
                                                 {s: 0.3 for s in list(visible)[:2]}))
    assert len(result.folds) >= 2
    for fold in result.folds:
        train_end = dt.date.fromisoformat(fold["train"][1])
        test_start = dt.date.fromisoformat(fold["test"][0])
        assert (test_start - train_end).days >= 30
    assert "fold_win_rate" in result.metrics


def test_embargo_shorter_than_label_horizon_is_flagged(price_bars):
    universe = _universe(price_bars, symbols=("A", "B"), n=1200)
    config = _config(universe, label_horizon_days=60, embargo_days=5,
                     train_window_days=300, test_window_days=150, step_days=150)
    engine = Backtester(universe, config)
    result = engine.run(lambda as_of, visible: {"A": 0.3})
    assert any("embargo" in w for w in result.warnings)


# -------------------------------------------------------------- metrics ----
def test_curve_metrics_are_correct():
    dates = [dt.date(2020, 1, 1) + dt.timedelta(days=i) for i in range(730)]
    values = [100 * 1.001 ** i for i in range(730)]
    summary = summarize_curve(dates, values, periods_per_year=365)
    assert summary["cagr"] > 0.3
    assert summary["max_drawdown"] == pytest.approx(0.0, abs=1e-9)


def test_trade_metrics_profit_factor():
    trades = [
        {"return_pct": 0.2, "pnl": 200, "entry_price": 10, "quantity": 100,
         "costs": 5, "holding_days": 30},
        {"return_pct": -0.1, "pnl": -100, "entry_price": 10, "quantity": 100,
         "costs": 5, "holding_days": 20},
    ]
    metrics = summarize_trades(trades)
    assert metrics["win_rate"] == 0.5
    assert metrics["profit_factor"] == pytest.approx(2.0)
    assert metrics["cost_drag_bps"] > 0


def test_benchmark_comparison_says_when_the_strategy_loses():
    verdict = compare_to_benchmarks({"cagr": 0.05, "sharpe": 0.3},
                                    {"SPY": {"cagr": 0.10, "sharpe": 0.7}})
    assert any("trailed SPY" in v for v in verdict["verdict"])
    assert any("risk-adjusted" in v for v in verdict["verdict"])


def test_heavily_searched_results_are_deflated():
    metrics = {"cagr": 0.30, "sharpe": 2.5}
    honest = deflated_metrics(metrics, configurations_tried=1, parameters=3,
                              observations=3000)
    tortured = deflated_metrics(metrics, configurations_tried=400, parameters=30,
                                observations=400)
    # an honest single-configuration test on a long sample is not deflated at all
    assert honest["sharpe_deflated"] == pytest.approx(2.5)
    assert tortured["sharpe_deflated"] < 1.5
    assert tortured["overfitting"]["warnings"]


def test_unknown_liquidity_is_opt_in_and_loud(price_bars):
    """Volume-less data must not silently become tradable."""
    strict = CostModel()
    permissive = CostModel(allow_unknown_liquidity=True)
    assert not strict.estimate(notional=10_000, adv=None)["executable"]
    lenient = permissive.estimate(notional=10_000, adv=None)
    assert lenient["executable"]
    assert lenient["bps"] > strict.estimate(notional=10_000, adv=1e9)["bps"]
    assert "overstates tradability" in lenient["reason"]

    universe = {"A": AssetHistory(
        symbol="A",
        series=PriceSeries.from_closes(
            "A", [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(400)],
            [100 + i for i in range(400)]),
        listed_date=dt.date(2020, 1, 1), market_cap=5e9)}
    result = Backtester(universe, _config(universe, cost_model=permissive)).run(
        lambda as_of, visible: {"A": 0.5})
    assert any("allow_unknown_liquidity is enabled" in w for w in result.warnings)


def test_curve_metrics_infer_the_sampling_frequency():
    """Annualising a monthly curve as daily inflates Sharpe by sqrt(21)."""
    import random
    rng = random.Random(4)
    monthly_dates = [dt.date(2015 + i // 12, i % 12 + 1, 1) for i in range(120)]
    values, level = [], 100.0
    for _ in range(120):
        level *= 1 + rng.gauss(0.006, 0.03)      # real month-to-month variation
        values.append(level)
    inferred = summarize_curve(monthly_dates, values)
    forced_daily = summarize_curve(monthly_dates, values, periods_per_year=252)
    assert inferred["periods_per_year"] == 12
    # the wrong annualisation inflates volatility by about sqrt(252/12) = 4.6x
    assert forced_daily["volatility"] > inferred["volatility"] * 4
    assert inferred["cagr"] == pytest.approx(forced_daily["cagr"])   # CAGR is time-based
