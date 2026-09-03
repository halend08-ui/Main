"""The data adapter the daily pipeline runs against.

Everything the pipeline needs is behind this one interface, for two reasons:

* the pipeline can be tested end to end with a fixture adapter and no database;
* point-in-time filtering happens in exactly one place, so a new pipeline step
  cannot accidentally bypass it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from research_engine.analysis import anomaly as AN
from research_engine.analysis import sell as SELL
from research_engine.analysis.pipeline import AnalysisInput
from research_engine.analysis.probability import Calibrator, CalibrationBin
from research_engine.core.errors import DataUnavailable
from research_engine.core.logging import get_logger
from research_engine.core.series import PriceSeries
from research_engine.core.timeutil import to_date
from research_engine.core.types import AssetClass, DataQuality, Period
from research_engine.features import crypto as CRYPTO
from research_engine.features import macro as MACRO
from research_engine.pipeline import discovery as DISC

log = get_logger(__name__)

#: Fundamental metrics loaded for analysis. Kept explicit so a provider change
#: cannot silently shrink the evidence base.
ANALYSIS_METRICS = (
    "revenue", "gross_profit", "operating_income", "net_income", "total_assets",
    "total_liabilities", "total_equity", "current_assets", "current_liabilities",
    "cash_and_equivalents", "long_term_debt", "short_term_debt", "total_debt",
    "operating_cash_flow", "capex", "shares_diluted", "shares_outstanding",
    "interest_expense", "income_tax", "pretax_income", "buybacks",
    "dividends_paid", "stock_compensation", "depreciation_amortization",
)


class RepositoryDataAccess:
    """Concrete adapter over the storage repositories."""

    def __init__(self, settings: Any, repositories: Mapping[str, Any], *,
                 ingestor: Any = None) -> None:
        self.settings = settings
        self.repos = dict(repositories)
        self.ingestor = ingestor
        self._asset_cache: dict[str, Any] = {}
        self._macro_state: dict[date, Any] = {}
        self._regime: str = "unknown"

    # -- ingestion ---------------------------------------------------------
    def refresh(self, as_of: date) -> dict[str, Any]:
        """Pull new data. Without an ingestor this is a no-op, reported as such."""
        if self.ingestor is None:
            return {"ingested": 0,
                    "note": "no ingestor configured: running on stored data only",
                    "health": {}}
        return self.ingestor.refresh(as_of)

    # -- universe ----------------------------------------------------------
    def universe(self, as_of: date) -> list[str]:
        assets = self.repos["assets"].list(active_only=True)
        return [a.symbol for a in assets]

    def asset(self, symbol: str) -> Any:
        if symbol not in self._asset_cache:
            record = self.repos["assets"].get(symbol)
            if record is None:
                raise DataUnavailable(f"unknown asset {symbol}")
            self._asset_cache[symbol] = record
        return self._asset_cache[symbol]

    def asset_id(self, symbol: str) -> int | None:
        try:
            return self.asset(symbol).id
        except DataUnavailable:
            return None

    def asset_class(self, symbol: str) -> str:
        try:
            return self.asset(symbol).asset_class.value
        except DataUnavailable:
            return "equity"

    def sector(self, symbol: str) -> str | None:
        try:
            return self.asset(symbol).sector
        except DataUnavailable:
            return None

    def crypto_symbols(self) -> set[str]:
        return {a.symbol for a in self.repos["assets"].list(
            asset_class=AssetClass.CRYPTO, active_only=False)}

    # -- market data -------------------------------------------------------
    def series(self, symbol: str, *, as_of: date | None = None) -> PriceSeries:
        asset = self.asset(symbol)
        return self.repos["prices"].series(asset.id, symbol, as_of=as_of)

    def price_move(self, symbol: str, as_of: date) -> float | None:
        try:
            series = self.series(symbol, as_of=as_of)
        except DataUnavailable:
            return None
        if len(series) < 2:
            return None
        return float(series.adj_close[-1] / series.adj_close[-2] - 1.0)

    def macro_series(self, as_of: date) -> dict[str, list[tuple[date, float]]]:
        repo = self.repos.get("macro")
        if repo is None:
            return {}
        from research_engine.ingestion.providers.fred import DEFAULT_SERIES
        out: dict[str, list[tuple[date, float]]] = {}
        for series_id in DEFAULT_SERIES:
            points = repo.series(series_id, as_of=as_of, limit=120)
            if points:
                out[series_id] = points
        return out

    def macro_value(self, series_id: str, as_of: date) -> float | None:
        repo = self.repos.get("macro")
        if repo is None:
            return None
        latest = repo.latest(series_id, as_of=as_of)
        return latest[1] if latest else None

    def macro_state(self, as_of: date) -> Any:
        if as_of not in self._macro_state:
            series = self.macro_series(as_of)
            self._macro_state[as_of] = (MACRO.build_state(as_of, series)
                                        if series else None)
        return self._macro_state[as_of]

    def benchmarks(self, as_of: date) -> dict[str, PriceSeries]:
        out: dict[str, PriceSeries] = {}
        for asset_class, key in (("equity", "analysis.benchmark_equity"),
                                 ("crypto", "analysis.benchmark_crypto")):
            symbol = self.settings.get(key)
            if not symbol:
                continue
            try:
                out[asset_class] = self.series(str(symbol), as_of=as_of)
            except DataUnavailable:
                continue
        return out

    # -- analysis inputs ---------------------------------------------------
    def analysis_input(self, symbol: str, as_of: date) -> AnalysisInput:
        asset = self.asset(symbol)
        series = None
        try:
            series = self.series(symbol, as_of=as_of)
        except DataUnavailable:
            pass

        annual: dict[str, list[Any]] = {}
        quarterly: dict[str, list[Any]] = {}
        funds = self.repos.get("fundamentals")
        if funds is not None and asset.asset_class is not AssetClass.CRYPTO:
            for metric in ANALYSIS_METRICS:
                annual_points = funds.history(asset.id, metric, period=Period.ANNUAL,
                                              as_of=as_of)
                if annual_points:
                    annual[metric] = annual_points
                quarterly_points = funds.history(asset.id, metric,
                                                 period=Period.QUARTERLY, as_of=as_of,
                                                 limit=8)
                if quarterly_points:
                    quarterly[metric] = quarterly_points

        news = []
        news_repo = self.repos.get("news")
        if news_repo is not None:
            news = news_repo.recent(asset.id, since=as_of - timedelta(days=45),
                                    as_of=as_of, limit=60)

        benchmarks = self.benchmarks(as_of)
        benchmark = benchmarks.get(asset.asset_class.value)

        market: dict[str, Any] = {"market_cap_usd": asset.market_cap_usd,
                                  "quality_grade": asset.quality_grade}
        crypto_snapshot = None
        unlocks: list[Mapping[str, Any]] = []
        if asset.asset_class is AssetClass.CRYPTO:
            crypto_repo = self.repos.get("crypto")
            onchain: dict[str, list[tuple[date, float]]] = {}
            if crypto_repo is not None:
                for metric in ("active_addresses", "transactions", "fees", "tvl"):
                    points = crypto_repo.series(asset.id, metric, as_of=as_of)
                    if points:
                        onchain[metric] = points
                unlocks = crypto_repo.upcoming_unlocks(asset.id, as_of=as_of)
            market.update({
                "circulating_supply": asset.circulating_supply,
                "max_supply": asset.max_supply,
                "price_usd": series.last_close if series else None,
            })
            crypto_snapshot = CRYPTO.build_snapshot(
                symbol, as_of, market=market, series=series,
                btc_series=benchmarks.get("crypto"), unlocks=unlocks,
                onchain=onchain, quality_grade=asset.quality_grade,
                category_tags=asset.tags)

        return AnalysisInput(
            symbol=symbol, as_of=as_of, asset_class=asset.asset_class, series=series,
            benchmark=benchmark, annual=annual, quarterly=quarterly, news=news,
            market=market, crypto_snapshot=crypto_snapshot, sector=asset.sector,
            previous=self.previous_analysis(symbol, as_of),
            held=symbol in self._held_symbols(as_of), unlocks=unlocks)

    def previous_analysis(self, symbol: str, as_of: date) -> dict[str, Any] | None:
        repo = self.repos.get("recommendations")
        asset_id = self.asset_id(symbol)
        if repo is None or asset_id is None:
            return None
        return repo.latest(asset_id, before=as_of)

    def asset_state(self, symbol: str, as_of: date) -> dict[str, Any]:
        """The inputs the prioritiser needs, gathered cheaply."""
        state: dict[str, Any] = {}
        asset_id = self.asset_id(symbol)
        if asset_id is None:
            return state
        scores = self.repos.get("scores")
        if scores is not None:
            latest = scores.latest(asset_id)
            if latest:
                state["score"] = latest.get("total_score")
                state["days_since_analysis"] = (as_of - to_date(latest["as_of"])).days
                history = scores.history(asset_id, limit=2)
                if len(history) >= 2:
                    state["previous_score"] = history[-2].get("total_score")
                state["data_quality"] = _quality(latest.get("data_quality"))
        state["is_held"] = symbol in self._held_symbols(as_of)
        try:
            series = self.series(symbol, as_of=as_of)
            state["anomaly_priority"] = AN.research_priority(AN.scan(series))
        except DataUnavailable:
            state["anomaly_priority"] = 0.0
        state["market_cap"] = getattr(self.asset(symbol), "market_cap_usd", None)
        return state

    # -- portfolio ---------------------------------------------------------
    def _held_symbols(self, as_of: date) -> set[str]:
        return {p["symbol"] for p in self.open_positions(as_of)}

    def open_positions(self, as_of: date) -> list[dict[str, Any]]:
        repo = self.repos.get("portfolio")
        if repo is None:
            return []
        portfolio_id = repo.ensure(str(self.settings.get("portfolio.name", "research")))
        positions = repo.positions(portfolio_id, open_only=True)
        for position in positions:
            try:
                position["price"] = self.series(position["symbol"],
                                                as_of=as_of).last_close
            except DataUnavailable:
                position["price"] = None
        return positions

    # -- events / conditions -----------------------------------------------
    def breached_conditions(self, symbol: str, as_of: date,
                            result: Any) -> list[dict[str, Any]]:
        metrics = {"price": result.price}
        evaluation = SELL.evaluate(result.sell_conditions, metrics)
        return [c.to_dict() for c in evaluation.breached]

    def next_unlock(self, symbol: str, as_of: date) -> dict[str, Any] | None:
        repo = self.repos.get("crypto")
        asset_id = self.asset_id(symbol)
        if repo is None or asset_id is None:
            return None
        unlocks = repo.upcoming_unlocks(asset_id, as_of=as_of, days_ahead=60)
        return unlocks[0] if unlocks else None

    # -- discovery ---------------------------------------------------------
    def discovery_candidates(self, as_of: date) -> list[DISC.Discovery]:
        assets = self.repos["assets"].list(active_only=True, limit=1000)
        rows = [{"symbol": a.symbol, "asset_class": a.asset_class.value,
                 "listed_date": a.listed_date} for a in assets]
        found: list[DISC.Discovery] = list(DISC.recent_listings(rows, as_of=as_of))

        series_by_symbol: dict[str, PriceSeries] = {}
        for asset in assets[:300]:            # cheap screen over the largest names
            try:
                series_by_symbol[asset.symbol] = self.repos["prices"].series(
                    asset.id, asset.symbol, as_of=as_of, limit=260)
            except DataUnavailable:
                continue
        found.extend(DISC.unusual_activity(series_by_symbol))
        return found

    # -- learning ----------------------------------------------------------
    def calibrator(self, model_version: str) -> Calibrator | None:
        repo = self.repos.get("models")
        if repo is None:
            return None
        rows = repo.latest_calibration(model_version)
        if len(rows) < 2:
            return None
        return Calibrator([CalibrationBin(
            low=float(r["bin_low"]), high=float(r["bin_high"]),
            predicted_mean=float(r.get("predicted_mean") or r["bin_low"]),
            observed_rate=float(r.get("observed_rate") or 0.5),
            samples=int(r.get("samples") or 0)) for r in rows])

    def learned_base_rates(self) -> dict[str, dict[str, float]]:
        repo = self.repos.get("predictions")
        if repo is None:
            return {}
        from research_engine.learning.performance import measured_base_rates
        return measured_base_rates(repo.evaluated())

    def current_regime(self) -> str:
        return self._regime

    def set_regime(self, regime: str) -> None:
        self._regime = regime


def _quality(value: Any) -> DataQuality | None:
    if not value:
        return None
    try:
        return DataQuality(str(value))
    except ValueError:
        return None
