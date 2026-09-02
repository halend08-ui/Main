"""Walk-forward backtest engine.

Structural guarantees, each enforced in code rather than by convention:

* **Point-in-time universe.** Only assets listed at the rebalance date are
  eligible, and delisted assets remain in the universe up to their delisting --
  so failures are experienced, not airbrushed out.
* **Next-bar execution.** A signal computed from the close of day T is filled at
  day T+1, and the engine asserts this rather than trusting the caller.
* **Delisting handling.** A position in an asset that stops trading is
  liquidated at its last price with a configurable recovery haircut, not
  silently dropped (dropping it is the mechanism of survivorship bias).
* **Costs and liquidity.** Every fill pays commission, spread and impact, and
  orders that exceed the participation cap are rejected and logged.
* **Walk-forward folds.** Train and test windows are separated by an embargo at
  least as long as the label horizon.

The engine takes a ``strategy`` callable so the same harness can test the full
recommendation pipeline or a single-factor sanity check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from research_engine.core.errors import LookAheadError
from research_engine.core.logging import get_logger
from research_engine.core.numeric import clamp, is_finite
from research_engine.core.series import PriceSeries
from research_engine.core.timeutil import to_date
from research_engine.backtest.costs import CostModel
from research_engine.backtest.metrics import (summarize_curve, summarize_trades,
                                              turnover)
from research_engine.quality.bias import (check_label_horizon_embargo,
                                          check_survivorship,
                                          check_train_test_separation,
                                          shifted_signal_is_safe)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AssetHistory:
    """Everything the engine may know about one asset, with listing dates."""

    symbol: str
    series: PriceSeries
    listed_date: date | None = None
    delisted_date: date | None = None
    asset_class: str = "equity"
    sector: str | None = None
    market_cap: float | None = None

    def tradable_on(self, day: date) -> bool:
        if self.listed_date and day < self.listed_date:
            return False
        if self.delisted_date and day > self.delisted_date:
            return False
        return True

    def price_on(self, day: date) -> float | None:
        return self.series.price_on(day, tolerance_days=5)

    def adv_on(self, day: date, window: int = 20) -> float | None:
        """Average daily dollar volume over the window ending at ``day``."""
        idx = [i for i, d in enumerate(self.series.dates) if d <= day]
        if len(idx) < window:
            return None
        tail = idx[-window:]
        values = [self.series.close[i] * self.series.volume[i] for i in tail]
        finite = [v for v in values if is_finite(v)]
        if len(finite) < window // 2:
            return None
        return float(np.median(finite))


@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    entry_date: date
    costs: float = 0.0
    reason: str = ""


@dataclass
class BacktestConfig:
    start: date
    end: date
    initial_capital: float = 100_000.0
    rebalance_days: int = 21
    max_positions: int = 20
    max_position_weight: float = 0.10
    cost_model: CostModel = field(default_factory=CostModel)
    allow_shorts: bool = False
    delisting_recovery: float = 0.30      # fraction of last price recovered
    label_horizon_days: int = 21
    embargo_days: int = 21
    train_window_days: int = 1260
    test_window_days: int = 252
    step_days: int = 252
    risk_free_rate: float = 0.0
    warn_on_survivorship: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(),
                "initial_capital": self.initial_capital,
                "rebalance_days": self.rebalance_days,
                "max_positions": self.max_positions,
                "max_position_weight": self.max_position_weight,
                "allow_shorts": self.allow_shorts,
                "delisting_recovery": self.delisting_recovery,
                "label_horizon_days": self.label_horizon_days,
                "embargo_days": self.embargo_days,
                "train_window_days": self.train_window_days,
                "test_window_days": self.test_window_days,
                "step_days": self.step_days,
                "cost_model": {"commission_bps": self.cost_model.commission_bps,
                               "spread_bps": self.cost_model.spread_bps,
                               "crypto_spread_bps": self.cost_model.crypto_spread_bps,
                               "max_participation": self.cost_model.max_participation}}


#: A strategy sees only what was knowable at ``as_of`` and returns target
#: weights by symbol. The engine never passes it future data.
Strategy = Callable[[date, Mapping[str, AssetHistory]], Mapping[str, float]]


@dataclass
class BacktestResult:
    dates: list[date]
    equity: list[float]
    trades: list[dict[str, Any]]
    metrics: dict[str, Any]
    trade_metrics: dict[str, Any]
    warnings: list[str]
    rejected_orders: list[dict[str, Any]]
    benchmarks: dict[str, dict[str, Any]] = field(default_factory=dict)
    folds: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"metrics": self.metrics, "trade_metrics": self.trade_metrics,
                "warnings": self.warnings, "benchmarks": self.benchmarks,
                "folds": self.folds, "config": self.config,
                "rejected_orders": self.rejected_orders[:50],
                "equity_curve": [{"date": d.isoformat(), "value": round(v, 2)}
                                 for d, v in zip(self.dates, self.equity)]}


class Backtester:
    def __init__(self, universe: Mapping[str, AssetHistory], config: BacktestConfig,
                 *, benchmark: PriceSeries | None = None,
                 benchmarks: Mapping[str, PriceSeries] | None = None) -> None:
        self.universe = dict(universe)
        self.config = config
        self.benchmark = benchmark
        self.benchmarks = dict(benchmarks or {})
        if benchmark is not None and "benchmark" not in self.benchmarks:
            self.benchmarks["benchmark"] = benchmark

    # -- universe ----------------------------------------------------------
    def universe_as_of(self, day: date) -> dict[str, AssetHistory]:
        """Assets tradable on ``day`` with history truncated at ``day``.

        Truncation is the load-bearing part: a strategy cannot look ahead if it
        is never handed a series that extends past the decision date.
        """
        out: dict[str, AssetHistory] = {}
        for symbol, asset in self.universe.items():
            if not asset.tradable_on(day):
                continue
            if asset.series.start > day:
                continue
            try:
                truncated = asset.series.as_of(day)
            except Exception:
                continue
            if len(truncated) < 30:
                continue
            out[symbol] = AssetHistory(
                symbol=symbol, series=truncated, listed_date=asset.listed_date,
                delisted_date=asset.delisted_date, asset_class=asset.asset_class,
                sector=asset.sector, market_cap=asset.market_cap)
        return out

    # -- main loop ---------------------------------------------------------
    def run(self, strategy: Strategy, *, name: str = "backtest") -> BacktestResult:
        cfg = self.config
        warnings: list[str] = []

        if cfg.warn_on_survivorship:
            snapshot = [{"symbol": s, "listed_date": a.listed_date,
                         "delisted_date": a.delisted_date}
                        for s, a in self.universe.items()]
            finding = check_survivorship(snapshot, as_of=cfg.start)
            if finding is not None:
                warnings.append(str(finding))

        embargo_finding = check_label_horizon_embargo(cfg.label_horizon_days,
                                                      cfg.embargo_days)
        if embargo_finding is not None:
            warnings.append(str(embargo_finding))

        calendar = self._calendar()
        if len(calendar) < 30:
            raise ValueError("not enough trading days in the backtest window")

        cash = cfg.initial_capital
        positions: dict[str, Position] = {}
        trades: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        equity_dates: list[date] = []
        equity_values: list[float] = []
        signal_dates: list[date] = []
        execution_dates: list[date] = []
        last_rebalance: date | None = None

        for i, day in enumerate(calendar):
            # 1. mark to market, handling delistings first
            cash, closed = self._handle_delistings(day, positions, cash, trades)
            equity = cash + self._positions_value(day, positions)
            equity_dates.append(day)
            equity_values.append(equity)

            # 2. rebalance on schedule, acting on the PREVIOUS close
            if last_rebalance is None or (day - last_rebalance).days >= cfg.rebalance_days:
                if i == 0:
                    last_rebalance = day
                    continue
                decision_day = calendar[i - 1]         # signals from yesterday's close
                visible = self.universe_as_of(decision_day)
                for asset in visible.values():
                    asset.series.require_no_future(decision_day)   # hard assertion

                try:
                    targets = strategy(decision_day, visible)
                except Exception as exc:                 # a broken strategy must not
                    log.exception("strategy raised", day=str(decision_day))
                    warnings.append(f"strategy error on {decision_day}: {exc}")
                    targets = {}

                signal_dates.append(decision_day)
                execution_dates.append(day)             # filled today: T+1
                cash = self._rebalance(day, targets, positions, cash, equity,
                                       trades, rejected)
                last_rebalance = day

        # liquidate at the end so the final equity is realisable
        final_day = calendar[-1]
        for symbol in list(positions):
            price = self._price(symbol, final_day)
            if price is not None:
                cash = self._close(symbol, final_day, price, positions, cash, trades,
                                   reason="backtest end")
        equity_values[-1] = cash

        leak = shifted_signal_is_safe(signal_dates, execution_dates)
        if leak is not None:
            warnings.append(str(leak))    # should be unreachable; kept as a tripwire

        metrics = summarize_curve(
            equity_dates, equity_values,
            benchmark_values=self._benchmark_curve(equity_dates, cfg.initial_capital),
            risk_free_rate=cfg.risk_free_rate)
        trade_metrics = summarize_trades(trades)
        years = max((equity_dates[-1] - equity_dates[0]).days / 365.25, 1e-9)
        trade_metrics["annual_turnover"] = turnover(
            trades, average_equity=float(np.mean(equity_values)), years=years)

        benchmark_metrics = {
            label: summarize_curve(equity_dates,
                                   self._series_curve(series, equity_dates,
                                                      cfg.initial_capital),
                                   risk_free_rate=cfg.risk_free_rate)
            for label, series in self.benchmarks.items()}
        benchmark_metrics["cash"] = self._cash_benchmark(equity_dates,
                                                         cfg.initial_capital,
                                                         cfg.risk_free_rate)
        if rejected:
            warnings.append(f"{len(rejected)} orders were rejected as unexecutable "
                            f"at the modelled size")

        return BacktestResult(
            dates=equity_dates, equity=equity_values, trades=trades, metrics=metrics,
            trade_metrics=trade_metrics, warnings=warnings, rejected_orders=rejected,
            benchmarks=benchmark_metrics, config={**cfg.to_dict(), "name": name})

    # -- walk-forward ------------------------------------------------------
    def walk_forward(self, strategy_factory: Callable[[date, date], Strategy], *,
                     name: str = "walk_forward") -> BacktestResult:
        """Run sequential train/test folds and stitch the out-of-sample results.

        ``strategy_factory(train_start, train_end)`` returns a strategy fitted on
        the training window only; it is then evaluated on the following test
        window after an embargo.
        """
        cfg = self.config
        folds: list[dict[str, Any]] = []
        combined_dates: list[date] = []
        combined_equity: list[float] = []
        all_trades: list[dict[str, Any]] = []
        warnings: list[str] = []
        capital = cfg.initial_capital

        train_start = cfg.start
        while True:
            train_end = train_start + timedelta(days=cfg.train_window_days)
            test_start = train_end + timedelta(days=cfg.embargo_days)
            test_end = min(test_start + timedelta(days=cfg.test_window_days), cfg.end)
            if test_start >= cfg.end or (test_end - test_start).days < 30:
                break

            finding = check_train_test_separation(train_start, train_end, test_start,
                                                  test_end, embargo_days=cfg.embargo_days)
            if finding is not None:
                warnings.append(str(finding))

            fold_config = BacktestConfig(**{**_config_kwargs(cfg),
                                            "start": test_start, "end": test_end,
                                            "initial_capital": capital,
                                            "warn_on_survivorship": False})
            fold_engine = Backtester(self.universe, fold_config,
                                     benchmarks=self.benchmarks)
            strategy = strategy_factory(train_start, train_end)
            result = fold_engine.run(strategy, name=f"{name}-fold{len(folds) + 1}")

            folds.append({
                "fold": len(folds) + 1,
                "train": [train_start.isoformat(), train_end.isoformat()],
                "test": [test_start.isoformat(), test_end.isoformat()],
                "metrics": result.metrics, "trades": result.trade_metrics.get("trades", 0),
                "warnings": result.warnings})
            combined_dates.extend(result.dates)
            combined_equity.extend(result.equity)
            all_trades.extend(result.trades)
            warnings.extend(result.warnings)
            capital = result.equity[-1] if result.equity else capital
            train_start = train_start + timedelta(days=cfg.step_days)

        if not folds:
            raise ValueError("no walk-forward folds fit inside the configured window; "
                             "shorten train_window_days or widen start/end")

        metrics = summarize_curve(combined_dates, combined_equity,
                                  risk_free_rate=cfg.risk_free_rate)
        metrics["folds"] = len(folds)
        metrics["fold_cagrs"] = [f["metrics"].get("cagr") for f in folds]
        consistent = [c for c in metrics["fold_cagrs"] if c is not None]
        if consistent:
            metrics["fold_win_rate"] = round(
                sum(1 for c in consistent if c > 0) / len(consistent), 3)
            metrics["worst_fold_cagr"] = round(min(consistent), 4)
        return BacktestResult(
            dates=combined_dates, equity=combined_equity, trades=all_trades,
            metrics=metrics, trade_metrics=summarize_trades(all_trades),
            warnings=sorted(set(warnings)), rejected_orders=[], folds=folds,
            config={**cfg.to_dict(), "name": name, "mode": "walk_forward"})

    # -- internals ---------------------------------------------------------
    def _calendar(self) -> list[date]:
        days: set[date] = set()
        for asset in self.universe.values():
            for day in asset.series.dates:
                if self.config.start <= day <= self.config.end:
                    days.add(day)
        return sorted(days)

    def _price(self, symbol: str, day: date) -> float | None:
        asset = self.universe.get(symbol)
        return asset.price_on(day) if asset else None

    def _positions_value(self, day: date, positions: Mapping[str, Position]) -> float:
        total = 0.0
        for symbol, position in positions.items():
            price = self._price(symbol, day)
            total += position.quantity * (price if price is not None
                                          else position.entry_price)
        return total

    def _handle_delistings(self, day: date, positions: dict[str, Position],
                           cash: float, trades: list[dict[str, Any]]
                           ) -> tuple[float, list[str]]:
        """Liquidate positions in assets that have stopped trading.

        The recovery haircut matters: pretending a delisted holding is sold at
        its last quoted price is exactly the assumption that makes backtests
        look better than reality.
        """
        closed: list[str] = []
        for symbol in list(positions):
            asset = self.universe.get(symbol)
            if asset is None:
                continue
            if asset.delisted_date and day > asset.delisted_date:
                last_price = asset.price_on(asset.delisted_date) or positions[symbol].entry_price
                recovery = last_price * self.config.delisting_recovery
                cash = self._close(symbol, day, recovery, positions, cash, trades,
                                   reason=f"delisted {asset.delisted_date} "
                                          f"({self.config.delisting_recovery:.0%} recovery)")
                closed.append(symbol)
        return cash, closed

    def _rebalance(self, day: date, targets: Mapping[str, float],
                   positions: dict[str, Position], cash: float, equity: float,
                   trades: list[dict[str, Any]],
                   rejected: list[dict[str, Any]]) -> float:
        cfg = self.config
        cleaned: dict[str, float] = {}
        for symbol, weight in targets.items():
            if symbol not in self.universe or not is_finite(weight) or weight <= 0:
                continue
            if not self.universe[symbol].tradable_on(day):
                continue
            cleaned[symbol] = min(float(weight), cfg.max_position_weight)
        if len(cleaned) > cfg.max_positions:
            cleaned = dict(sorted(cleaned.items(), key=lambda kv: -kv[1])[:cfg.max_positions])
        total = sum(cleaned.values())
        if total > 1.0:
            cleaned = {s: w / total for s, w in cleaned.items()}

        # exits first, so the cash is available for entries
        for symbol in list(positions):
            if symbol not in cleaned:
                price = self._price(symbol, day)
                if price is None:
                    continue
                cash = self._close(symbol, day, price, positions, cash, trades,
                                   reason="dropped from target portfolio")

        for symbol, weight in cleaned.items():
            price = self._price(symbol, day)
            if price is None or price <= 0:
                continue
            asset = self.universe[symbol]
            target_value = equity * weight
            current_value = positions[symbol].quantity * price if symbol in positions else 0.0
            delta_value = target_value - current_value
            if abs(delta_value) < equity * 0.005:      # ignore trivial adjustments
                continue

            adv = asset.adv_on(day)
            cost = cfg.cost_model.estimate(
                notional=abs(delta_value), adv=adv,
                volatility=self._recent_volatility(asset, day),
                is_crypto=asset.asset_class == "crypto",
                market_cap=asset.market_cap)
            if not cost["executable"]:
                rejected.append({"date": day.isoformat(), "symbol": symbol,
                                 "notional": round(abs(delta_value), 2),
                                 "reason": cost["reason"]})
                continue
            if delta_value > 0 and delta_value + cost["total"] > cash:
                delta_value = max(0.0, cash - cost["total"])
                if delta_value <= 0:
                    continue

            quantity = delta_value / price
            cash -= delta_value + cost["total"]
            if symbol in positions:
                position = positions[symbol]
                new_qty = position.quantity + quantity
                if new_qty <= 0:
                    cash = self._close(symbol, day, price, positions, cash, trades,
                                       reason="position closed by rebalance")
                    continue
                blended = ((position.entry_price * position.quantity
                            + price * quantity) / new_qty)
                positions[symbol] = Position(symbol, new_qty, blended,
                                             position.entry_date,
                                             position.costs + cost["total"])
            else:
                positions[symbol] = Position(symbol, quantity, price, day,
                                             cost["total"], reason="entry")
        return cash

    def _close(self, symbol: str, day: date, price: float,
               positions: dict[str, Position], cash: float,
               trades: list[dict[str, Any]], *, reason: str) -> float:
        position = positions.pop(symbol, None)
        if position is None:
            return cash
        asset = self.universe.get(symbol)
        notional = position.quantity * price
        cost = self.config.cost_model.estimate(
            notional=notional, adv=asset.adv_on(day) if asset else None,
            volatility=self._recent_volatility(asset, day) if asset else None,
            is_crypto=bool(asset and asset.asset_class == "crypto"),
            market_cap=asset.market_cap if asset else None)
        proceeds = notional - cost["total"]
        total_costs = position.costs + cost["total"]
        pnl = proceeds - position.entry_price * position.quantity
        trades.append({
            "symbol": symbol, "side": "long", "entry_date": position.entry_date,
            "exit_date": day, "entry_price": position.entry_price,
            "exit_price": price, "quantity": position.quantity,
            "costs": total_costs, "pnl": pnl,
            "return_pct": (price / position.entry_price - 1.0
                           if position.entry_price > 0 else None),
            "holding_days": (day - position.entry_date).days, "reason": reason})
        return cash + proceeds

    def _recent_volatility(self, asset: AssetHistory | None, day: date) -> float | None:
        if asset is None:
            return None
        try:
            window = asset.series.as_of(day).tail(60)
        except Exception:
            return None
        returns = window.returns()
        finite = returns[np.isfinite(returns)]
        if finite.size < 30:
            return None
        return float(np.std(finite, ddof=1) * np.sqrt(252))

    def _benchmark_curve(self, dates: Sequence[date],
                         capital: float) -> list[float] | None:
        if self.benchmark is None:
            return None
        return self._series_curve(self.benchmark, dates, capital)

    def _series_curve(self, series: PriceSeries, dates: Sequence[date],
                      capital: float) -> list[float]:
        """Buy-and-hold curve on the strategy's own date grid."""
        base = series.price_on(dates[0], tolerance_days=10)
        out: list[float] = []
        last = capital
        for day in dates:
            price = series.price_on(day, tolerance_days=10)
            if base and price:
                last = capital * price / base
            out.append(last)
        return out

    def _cash_benchmark(self, dates: Sequence[date], capital: float,
                        rate: float) -> dict[str, Any]:
        values = [capital * (1 + rate) ** ((d - dates[0]).days / 365.25) for d in dates]
        return summarize_curve(dates, values, risk_free_rate=rate)


def _config_kwargs(cfg: BacktestConfig) -> dict[str, Any]:
    return {"start": cfg.start, "end": cfg.end, "initial_capital": cfg.initial_capital,
            "rebalance_days": cfg.rebalance_days, "max_positions": cfg.max_positions,
            "max_position_weight": cfg.max_position_weight,
            "cost_model": cfg.cost_model, "allow_shorts": cfg.allow_shorts,
            "delisting_recovery": cfg.delisting_recovery,
            "label_horizon_days": cfg.label_horizon_days,
            "embargo_days": cfg.embargo_days,
            "train_window_days": cfg.train_window_days,
            "test_window_days": cfg.test_window_days, "step_days": cfg.step_days,
            "risk_free_rate": cfg.risk_free_rate,
            "warn_on_survivorship": cfg.warn_on_survivorship}
