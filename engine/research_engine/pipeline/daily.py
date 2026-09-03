"""The daily research loop.

One entry point runs the whole system unattended:

    1  ingest new data            8   analyse valuation
    2  validate it                9   analyse technicals
    3  update the database        10  analyse news and events
    4  scan the universe          11  analyse macro and regime
    5  detect unusual changes     12  run risk models
    6  discover new assets        13  run probability models
    7  analyse fundamentals       14  compare against yesterday
    15 recalculate scores         19  store predictions
    16 identify upgrades          20  evaluate matured predictions
    17 track existing positions   21  update performance statistics
    18 generate the daily report  22  retrain only when validated

Each step records its own timing and outcome, and a failure in one step is
recorded and skipped rather than aborting the run -- a news outage must not stop
the fundamentals from being analysed.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Mapping, Sequence

from research_engine.analysis import memo as MEMO
from research_engine.analysis.pipeline import AnalysisInput, analyze
from research_engine.analysis.probability import Calibrator
from research_engine.config.settings import Settings
from research_engine.core.errors import DataUnavailable, ResearchEngineError
from research_engine.core.logging import get_logger
from research_engine.core.series import PriceSeries
from research_engine.core.timeutil import iso, to_date, utcnow
from research_engine.core.types import (AssetClass, DataQuality, Horizon,
                                        MarketRegime, Recommendation)
from research_engine.features import macro as MACRO
from research_engine.features import regime as REGIME
from research_engine.learning import evaluation as EVAL
from research_engine.learning import performance as PERF
from research_engine.learning import retrain as RETRAIN
from research_engine.pipeline import alerts as ALERTS
from research_engine.pipeline import discovery as DISC
from research_engine.pipeline import prioritization as PRIOR
from research_engine.pipeline.report import DailyReport, build_opportunity_row

log = get_logger(__name__)


@dataclass
class StepResult:
    name: str
    ok: bool
    seconds: float
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.name, "ok": self.ok, "seconds": round(self.seconds, 2),
                "detail": self.detail, "error": self.error}


@dataclass
class DailyRunResult:
    as_of: date
    steps: list[StepResult] = field(default_factory=list)
    report: DailyReport | None = None
    analyzed: dict[str, Any] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    memos: dict[str, str] = field(default_factory=dict)
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(), "ok": self.ok,
                "steps": [s.to_dict() for s in self.steps],
                "alerts": self.alerts, "analyzed": len(self.analyzed),
                "memos": list(self.memos)}

    def failed_steps(self) -> list[str]:
        return [s.name for s in self.steps if not s.ok]


class DailyPipeline:
    """Runs the loop against injected services, so it is testable end to end.

    ``data_provider`` is any object exposing ``series(symbol)``,
    ``fundamentals(symbol)``, ``news(symbol)`` and ``market(symbol)``; in
    production that is the repository-backed adapter, and in tests it is a
    fixture. The pipeline never performs I/O itself.
    """

    def __init__(self, settings: Settings, *, data, repositories: Mapping[str, Any],
                 model_version: str = "scoring_v1",
                 alert_rules: ALERTS.AlertRules | None = None,
                 dispatcher: ALERTS.AlertDispatcher | None = None) -> None:
        self.settings = settings
        self.data = data
        self.repos = dict(repositories)
        self.model_version = model_version
        self.alert_rules = alert_rules or ALERTS.AlertRules(
            _as_dict(settings.get("alerts.thresholds")))
        self.dispatcher = dispatcher or ALERTS.AlertDispatcher(
            repository=self.repos.get("alerts"))

    # -- helpers -----------------------------------------------------------
    def _step(self, result: DailyRunResult, name: str,
              fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            detail = fn() or {}
            result.steps.append(StepResult(name, True, time.monotonic() - started,
                                           detail))
            return detail
        except Exception as exc:
            elapsed = time.monotonic() - started
            log.exception("pipeline step failed", step=name)
            result.steps.append(StepResult(
                name, False, elapsed, {},
                f"{type(exc).__name__}: {exc}"))
            result.ok = False
            return {}

    # -- the loop ----------------------------------------------------------
    def run(self, as_of: date | None = None, *,
            symbols: Sequence[str] | None = None) -> DailyRunResult:
        as_of = as_of or date.today()
        run = DailyRunResult(as_of=as_of)
        log.info("daily run starting", as_of=str(as_of))

        ingest = self._step(run, "ingest", lambda: self.data.refresh(as_of))
        universe = self._step(run, "universe",
                              lambda: {"symbols": list(symbols) if symbols
                                       else self.data.universe(as_of)})
        candidates: list[str] = list(universe.get("symbols") or [])

        market = self._step(run, "market_context", lambda: self._market_context(as_of))
        discoveries = self._step(run, "discovery",
                                 lambda: self._discover(as_of, candidates))
        priorities = self._step(run, "prioritise",
                                lambda: self._prioritise(as_of, candidates,
                                                         discoveries.get("new", [])))
        analysis = self._step(run, "analyse",
                              lambda: self._analyse(as_of, priorities.get("funnel"),
                                                    market))
        run.analyzed = analysis.get("results", {})

        comparison = self._step(run, "compare",
                                lambda: self._compare(as_of, run.analyzed))
        changes = self._step(run, "detect_changes",
                             lambda: self._changes(as_of, run.analyzed))
        positions = self._step(run, "portfolio", lambda: self._portfolio(as_of,
                                                                        run.analyzed))
        alert_detail = self._step(run, "alerts",
                                  lambda: self._alerts(as_of, run.analyzed, positions))
        run.alerts = alert_detail.get("alerts", [])

        self._step(run, "store_predictions",
                   lambda: self._store_predictions(as_of, run.analyzed))
        evaluation = self._step(run, "evaluate_predictions",
                                lambda: self._evaluate(as_of))
        performance = self._step(run, "performance", lambda: self._performance())
        learning = self._step(run, "learning",
                              lambda: self._learning(performance, evaluation))
        memos = self._step(run, "memos", lambda: self._memos(run.analyzed))
        run.memos = memos.get("memos", {})

        report = self._step(run, "report", lambda: self._report(
            as_of, run, market, discoveries, changes, positions, performance,
            learning, ingest, comparison))
        run.report = report.get("report")

        log.info("daily run complete", as_of=str(as_of), ok=run.ok,
                 analyzed=len(run.analyzed), alerts=len(run.alerts),
                 failed_steps=",".join(run.failed_steps()))
        return run

    # -- steps -------------------------------------------------------------
    def _market_context(self, as_of: date) -> dict[str, Any]:
        benchmark_symbol = str(self.settings.get("analysis.benchmark_equity", "SPY"))
        context: dict[str, Any] = {"regime": {}, "macro_stance": {}, "major_risks": []}
        try:
            benchmark = self.data.series(benchmark_symbol, as_of=as_of)
        except (DataUnavailable, KeyError):
            benchmark = None
            context["major_risks"].append(
                f"benchmark {benchmark_symbol} unavailable: regime detection and "
                f"relative strength are degraded")
        if benchmark is not None:
            state = REGIME.classify(benchmark, as_of=as_of,
                                    credit_spread=self.data.macro_value("BAMLH0A0HYM2",
                                                                        as_of))
            context["regime"] = state.to_dict()
            context["benchmark"] = benchmark_symbol

        macro_series = self.data.macro_series(as_of)
        if macro_series:
            macro_state = MACRO.build_state(as_of, macro_series)
            context["macro_stance"] = macro_state.stance
            context["macro"] = macro_state.to_dict()
            for evidence in macro_state.evidence:
                if evidence.direction < -0.2:
                    context["major_risks"].append(f"{evidence.label}: {evidence.detail}")
        return context

    def _discover(self, as_of: date, known: Sequence[str]) -> dict[str, Any]:
        found = self.data.discovery_candidates(as_of)
        merged = DISC.deduplicate(
            found, known_symbols=known,
            max_results=int(self.settings.get("pipeline.discovery_max_new_per_day", 50)))
        queue = self.repos.get("queue")
        for discovery in merged:
            asset_id = self.data.asset_id(discovery.symbol)
            if asset_id and queue is not None:
                queue.enqueue(asset_id, priority=discovery.score,
                              reason=discovery.reason, trigger=discovery.trigger)
        return {"new": [d.to_dict() for d in merged], "count": len(merged)}

    def _prioritise(self, as_of: date, candidates: Sequence[str],
                    discoveries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        new_symbols = {d["symbol"] for d in discoveries}
        inputs: list[PRIOR.PriorityInputs] = []
        for symbol in list(candidates) + sorted(new_symbols - set(candidates)):
            state = self.data.asset_state(symbol, as_of)
            inputs.append(PRIOR.PriorityInputs(
                symbol=symbol, score=state.get("score"),
                previous_score=state.get("previous_score"),
                anomaly_priority=state.get("anomaly_priority", 0.0),
                days_since_analysis=state.get("days_since_analysis"),
                data_quality=state.get("data_quality"),
                is_new=symbol in new_symbols, is_held=state.get("is_held", False),
                market_cap=state.get("market_cap"),
                upcoming_catalyst_days=state.get("catalyst_days")))
        ranked = PRIOR.rank(inputs)
        config = PRIOR.FunnelConfig(
            stage1_max=int(self.settings.get("pipeline.stage1_max_assets", 5000)),
            stage2_max=int(self.settings.get("pipeline.stage2_max_assets", 600)),
            stage3_max=int(self.settings.get("pipeline.stage3_max_assets", 120)),
            stage4_max=int(self.settings.get("pipeline.stage4_max_assets", 25)))
        funnel = PRIOR.run_funnel(ranked, config)
        return {"ranked": [r.to_dict() for r in ranked[:100]], "funnel": funnel,
                "counts": {str(k): len(v) for k, v in funnel.stages.items()}}

    def _analyse(self, as_of: date, funnel: PRIOR.FunnelResult | None,
                 market: Mapping[str, Any]) -> dict[str, Any]:
        if funnel is None:
            return {"results": {}, "analysed": 0}
        symbols = funnel.stages.get(2) or funnel.stages.get(1) or []
        regime_value = (market.get("regime") or {}).get("regime", "unknown")
        try:
            regime = MarketRegime(regime_value)
        except ValueError:
            regime = MarketRegime.UNKNOWN

        results: dict[str, Any] = {}
        failures: list[str] = []
        calibrator = self.data.calibrator(self.model_version)
        base_rates = self.data.learned_base_rates()

        for symbol in symbols:
            try:
                bundle = self.data.analysis_input(symbol, as_of)
            except (DataUnavailable, KeyError) as exc:
                failures.append(f"{symbol}: {exc}")
                continue
            macro_adjustment = 0.0
            if market.get("macro") and bundle.sector:
                macro_state_obj = self.data.macro_state(as_of)
                if macro_state_obj is not None:
                    macro_adjustment = float(MACRO.sector_adjustment(
                        bundle.sector, macro_state_obj).get("adjustment", 0.0))
            bundle.regime = regime
            bundle.macro_adjustment = macro_adjustment
            bundle.settings = self.settings
            bundle.model_version = self.model_version
            bundle.calibrator = calibrator
            bundle.learned_base_rates = base_rates
            try:
                results[symbol] = analyze(bundle)
            except Exception as exc:
                log.exception("analysis failed", symbol=symbol)
                failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
        self._persist(as_of, results)
        return {"results": results, "analysed": len(results), "failures": failures}

    def _persist(self, as_of: date, results: Mapping[str, Any]) -> None:
        scores = self.repos.get("scores")
        recs = self.repos.get("recommendations")
        for symbol, result in results.items():
            asset_id = self.data.asset_id(symbol)
            if asset_id is None:
                continue
            if scores is not None and result.score is not None:
                scores.write(asset_id, as_of=as_of, total_score=result.score,
                             tier=result.tier,
                             components={f.name: f.to_dict()
                                         for f in (result.ensemble.views if
                                                   result.ensemble else [])} or {},
                             data_quality=result.data_quality,
                             coverage=1.0, model_version=self.model_version)
            if recs is not None:
                recs.write(asset_id, as_of=as_of, recommendation=result.recommendation,
                           confidence=result.confidence, horizon=result.horizon,
                           risk_level=result.risk_level,
                           data_quality=result.data_quality,
                           model_version=self.model_version, price=result.price,
                           score=result.score, tier=result.tier,
                           previous=result.previous,
                           fair_value=result.fair_value,
                           expected_return=result.expected_return,
                           prob_positive=result.prob_positive,
                           rationale={"evidence": [e.to_dict() for e in result.evidence],
                                      "bear_case": result.bear_case,
                                      "bull_case": result.bull_case,
                                      "gates_failed": result.gates_failed},
                           sell_conditions=[c.to_dict() for c in result.sell_conditions],
                           invalidation=result.invalidation)

    def _compare(self, as_of: date, results: Mapping[str, Any]) -> dict[str, Any]:
        """Rank the analysed assets against each other, not just against thresholds.

        An absolute score answers "is this good?"; choosing among thousands needs
        "is this better than the alternatives, and why?". Peer-relative evidence
        is attached back onto each recommendation so an individual report says
        where the asset stands among its own peers.
        """
        from research_engine.analysis import comparison as CMP

        if not results:
            return {"comparison": None, "note": "nothing was analysed"}

        candidates = []
        for symbol, result in results.items():
            try:
                asset = self.data.asset(symbol)
                sector, cap, klass = (asset.sector, asset.market_cap_usd,
                                      asset.asset_class.value)
            except Exception:
                sector, cap, klass = None, None, "equity"
            candidates.append(CMP.candidate_from_result(
                result, sector=sector, market_cap=cap, asset_class=klass))

        outcome = CMP.compare(candidates, as_of=as_of,
                              per_group=int(self.settings.get(
                                  "pipeline.leaders_per_peer_group", 3)))
        for symbol, profile in outcome.profiles.items():
            result = results.get(symbol)
            if result is not None:
                result.evidence.extend(CMP.comparison_evidence(profile))
        return {"comparison": outcome, "groups": len(outcome.peer_groups),
                "ranked": len(outcome.final_ranking),
                "excluded": len(outcome.excluded)}

    def _changes(self, as_of: date, results: Mapping[str, Any]) -> dict[str, Any]:
        buckets: dict[str, list[dict[str, Any]]] = {
            "new_buys": [], "upgrades": [], "downgrades": [], "new_sells": [],
            "score_moves": []}
        for symbol, result in results.items():
            previous = self.data.previous_analysis(symbol, as_of)
            if not previous:
                if result.recommendation is Recommendation.BUY:
                    buckets["new_buys"].append(
                        {"symbol": symbol, "detail": "first analysis: BUY"})
                continue
            old = str(previous.get("recommendation", ""))
            new = result.recommendation.value
            if old != new:
                entry = {"symbol": symbol, "detail": f"{old} -> {new}"}
                if new == "BUY":
                    buckets["new_buys"].append(entry)
                elif new in ("SELL", "AVOID"):
                    buckets["new_sells"].append(entry)
                elif _rank(new) > _rank(old):
                    buckets["upgrades"].append(entry)
                else:
                    buckets["downgrades"].append(entry)
            old_score = previous.get("score")
            if old_score is not None and result.score is not None:
                delta = result.score - float(old_score)
                if abs(delta) >= float(self.settings.get(
                        "alerts.thresholds.score_change_abs", 8)):
                    buckets["score_moves"].append(
                        {"symbol": symbol,
                         "detail": f"score {delta:+.0f} to {result.score:.0f}"})
        return buckets

    def _portfolio(self, as_of: date, results: Mapping[str, Any]) -> dict[str, Any]:
        positions = self.data.open_positions(as_of)
        if not positions:
            return {"positions": [], "risk": {}, "breaches": []}
        enriched: list[dict[str, Any]] = []
        weights: dict[str, float] = {}
        series_by_symbol: dict[str, PriceSeries] = {}
        for position in positions:
            symbol = position["symbol"]
            result = results.get(symbol)
            price = position.get("price")
            entry = position.get("entry_price")
            enriched.append({
                **position,
                "unrealized_return": (price / entry - 1.0) if price and entry else None,
                "recommendation": result.recommendation.value if result else "not analysed",
                "risk_level": result.risk_level.value if result else "unknown",
                "thesis_health": _thesis_health(result),
            })
            value = (price or entry or 0) * position.get("quantity", 0)
            weights[symbol] = value
            try:
                series_by_symbol[symbol] = self.data.series(symbol, as_of=as_of)
            except (DataUnavailable, KeyError):
                continue

        from research_engine.analysis import risk as RK
        risk = RK.portfolio_risk(weights, series_by_symbol) if weights else {}
        breaches = RK.limit_breaches(
            weights,
            sectors={p["symbol"]: p.get("sector", "Unknown") for p in positions},
            asset_classes={p["symbol"]: p.get("asset_class", "equity")
                           for p in positions},
            max_position=float(self.settings.get("risk.max_position_weight", 0.12)),
            max_sector=float(self.settings.get("risk.max_sector_weight", 0.30)),
            max_crypto=float(self.settings.get("risk.max_crypto_weight", 0.20)),
            concentration_warning_hhi=float(
                self.settings.get("risk.concentration_warning_hhi", 0.18)))
        return {"positions": enriched, "risk": risk, "breaches": breaches}

    def _alerts(self, as_of: date, results: Mapping[str, Any],
                portfolio: Mapping[str, Any]) -> dict[str, Any]:
        positions = {p["symbol"]: p for p in portfolio.get("positions", [])}
        fired: list[ALERTS.Alert] = []
        for symbol, result in results.items():
            previous = self.data.previous_analysis(symbol, as_of) or {}
            current = {**result.to_dict(),
                       "price_move_1d": self.data.price_move(symbol, as_of)}
            breached = self.data.breached_conditions(symbol, as_of, result)
            fired.extend(ALERTS.evaluate_all(
                self.alert_rules, symbol=symbol, as_of=as_of, current=current,
                previous=previous, position=positions.get(symbol),
                breached_conditions=breached,
                unlock=self.data.next_unlock(symbol, as_of),
                is_crypto=result.symbol in self.data.crypto_symbols()))
        delivered = self.dispatcher.dispatch(fired)
        return {"alerts": [a.to_dict() for a in delivered], "count": len(delivered)}

    def _store_predictions(self, as_of: date, results: Mapping[str, Any]) -> dict[str, Any]:
        repo = self.repos.get("predictions")
        if repo is None:
            return {"stored": 0}
        stored = 0
        for symbol, result in results.items():
            if result.recommendation is Recommendation.INSUFFICIENT_DATA:
                continue
            if result.price is None:
                continue
            asset_id = self.data.asset_id(symbol)
            if asset_id is None:
                continue
            horizon = result.horizon
            repo.write(asset_id, as_of=as_of, horizon=horizon,
                       due_at=as_of + timedelta(days=horizon.days),
                       price_at_prediction=result.price,
                       recommendation=result.recommendation,
                       confidence=result.confidence,
                       asset_class=self.data.asset_class(symbol),
                       model_version=self.model_version,
                       data_quality=result.data_quality,
                       prob_positive=result.prob_positive,
                       expected_return=result.expected_return.get("base"),
                       expected_downside=result.expected_return.get("bear"),
                       factors={f.name: (f.score or 0.0) / 100.0
                                for f in (result.ensemble.views if result.ensemble
                                          else []) if getattr(f, "score", None)},
                       regime=self.data.current_regime(),
                       sector=self.data.sector(symbol))
            stored += 1
        return {"stored": stored}

    def _evaluate(self, as_of: date) -> dict[str, Any]:
        repo = self.repos.get("predictions")
        if repo is None:
            return {"evaluated": 0}
        due = repo.due(as_of)
        if not due:
            return {"evaluated": 0, "note": "no predictions have matured"}
        series_by_symbol: dict[str, PriceSeries] = {}
        for prediction in due:
            symbol = str(prediction.get("symbol"))
            if symbol in series_by_symbol:
                continue
            try:
                series_by_symbol[symbol] = self.data.series(symbol, as_of=as_of)
            except (DataUnavailable, KeyError):
                continue
        outcomes = EVAL.evaluate_batch(
            due, series_by_symbol,
            benchmark_by_class=self.data.benchmarks(as_of), as_of=as_of)
        for outcome in outcomes:
            repo.record_outcome(
                outcome.prediction_id, price_at_due=outcome.price_at_due,
                actual_return=outcome.actual_return,
                benchmark_return=outcome.benchmark_return,
                max_drawdown=outcome.max_drawdown,
                realized_vol=outcome.realized_vol, hit=outcome.hit,
                thesis_outcome=outcome.thesis_outcome,
                failure_reason=outcome.failure_reason)
        return {"evaluated": len(outcomes), "due": len(due),
                "failures": sum(1 for o in outcomes if o.thesis_outcome == "failed"),
                "luck": sum(1 for o in outcomes if o.thesis_outcome == "luck")}

    def _performance(self) -> dict[str, Any]:
        repo = self.repos.get("predictions")
        if repo is None:
            return {}
        records = repo.evaluated(model_version=self.model_version)
        if not records:
            return {"overall": {"sufficient": False,
                                "note": "no evaluated predictions yet"}}
        buckets = PERF.compute(records)
        overall = next((b for b in buckets if b.kind == "overall"), None)
        weak = PERF.systematic_errors(buckets)
        confidence_check = PERF.confidence_is_informative(buckets)
        registry = self.repos.get("models")
        if registry is not None:
            for bucket in buckets:
                if bucket.sufficient:
                    registry.record_performance(
                        self.model_version, bucket_kind=bucket.kind,
                        bucket_value=bucket.value, samples=bucket.samples,
                        hit_rate=bucket.hit_rate, avg_return=bucket.avg_return,
                        avg_excess=bucket.avg_excess, sharpe=bucket.sharpe,
                        sortino=bucket.sortino, brier=bucket.brier,
                        calibration_error=bucket.calibration_error,
                        profit_factor=bucket.profit_factor,
                        avg_winner=bucket.avg_winner, avg_loser=bucket.avg_loser)
        return {"overall": overall.to_dict() if overall else {},
                "buckets": [b.to_dict() for b in buckets],
                "weak_buckets": weak, "confidence_check": confidence_check,
                "records": len(records)}

    def _learning(self, performance: Mapping[str, Any],
                  evaluation: Mapping[str, Any]) -> dict[str, Any]:
        repo = self.repos.get("predictions")
        if repo is None or not performance.get("records"):
            return {"note": "learning skipped: no evaluated predictions"}
        records = repo.evaluated(model_version=self.model_version)
        min_samples = int(self.settings.get("learning.min_samples_for_retrain", 200))
        effectiveness = RETRAIN.factor_effectiveness(
            records, min_samples=int(self.settings.get(
                "learning.min_samples_per_bucket", 30)))
        proposal = RETRAIN.propose_weights(
            self.settings.scoring_weights(), effectiveness,
            max_change=float(self.settings.get(
                "learning.max_weight_change_per_update", 0.25)),
            min_samples=min_samples, total_samples=len(records))
        report = RETRAIN.learning_report(
            effectiveness=effectiveness, proposal=proposal,
            systematic=performance.get("weak_buckets", []),
            confidence_check=performance.get("confidence_check", {}))
        # A proposal is a *proposal*: applying it requires registering a new
        # model version, which the operator promotes explicitly.
        report["applied"] = False
        report["note"] = ("weight changes are proposed, versioned and validated; "
                          "they are never applied silently in production")
        return report

    def _memos(self, results: Mapping[str, Any]) -> dict[str, Any]:
        limit = int(self.settings.get("pipeline.stage4_max_assets", 25))
        ranked = sorted((r for r in results.values() if r.score is not None),
                        key=lambda r: r.score, reverse=True)[:limit]
        memos: dict[str, str] = {}
        repo = self.repos.get("reports")
        for result in ranked:
            if result.recommendation not in (Recommendation.BUY, Recommendation.SELL):
                continue
            text = MEMO.generate(result)
            memos[result.symbol] = text
            if repo is not None:
                asset_id = self.data.asset_id(result.symbol)
                repo.write(kind="memo", as_of=result.as_of,
                           title=f"{result.symbol} investment memo",
                           body_markdown=text, asset_id=asset_id,
                           model_version=self.model_version)
        return {"memos": memos, "count": len(memos)}

    def _report(self, as_of: date, run: DailyRunResult, market: Mapping[str, Any],
                discoveries: Mapping[str, Any], changes: Mapping[str, Any],
                portfolio: Mapping[str, Any], performance: Mapping[str, Any],
                learning: Mapping[str, Any], ingest: Mapping[str, Any],
                comparison: Mapping[str, Any] | None = None) -> dict[str, Any]:
        ranked = sorted((r for r in run.analyzed.values() if r.score is not None),
                        key=lambda r: r.score, reverse=True)
        report = DailyReport(
            as_of=as_of, market=dict(market),
            opportunities=[build_opportunity_row(r) for r in ranked[:25]],
            changes={k: list(v) for k, v in changes.items()},
            discoveries=list(discoveries.get("new", [])),
            portfolio=dict(portfolio),
            model_performance=dict(performance),
            self_evaluation=_self_evaluation(performance, learning, run),
            data_health=dict(ingest.get("health", {})),
            comparison=((comparison or {}).get("comparison").to_dict()
                        if (comparison or {}).get("comparison") else {}),
            warnings=[f"step '{s.name}' failed: {s.error}"
                      for s in run.steps if not s.ok])
        repo = self.repos.get("reports")
        if repo is not None:
            repo.write(kind="daily", as_of=as_of,
                       title=f"Daily research report {as_of.isoformat()}",
                       body_markdown=report.render(), payload=report.to_dict(),
                       model_version=self.model_version)
        return {"report": report}


def _self_evaluation(performance: Mapping[str, Any], learning: Mapping[str, Any],
                     run: DailyRunResult) -> dict[str, Any]:
    """The daily "what did I get wrong?" record."""
    overall = performance.get("overall") or {}
    what_worked: list[str] = []
    what_failed: list[str] = []
    why: list[str] = []
    actions: list[str] = []

    if overall.get("sufficient", False):
        hit_rate = overall.get("hit_rate")
        if hit_rate is not None:
            (what_worked if hit_rate >= 0.5 else what_failed).append(
                f"directional hit rate of {hit_rate:.0%} over "
                f"{overall.get('samples')} evaluated predictions")
        skill = overall.get("brier_skill")
        if skill is not None:
            (what_worked if skill > 0 else what_failed).append(
                f"Brier skill of {skill:+.3f} versus the base rate")
        luck = overall.get("luck_rate")
        if luck is not None and luck > 0.2:
            why.append(f"{luck:.0%} of positive outcomes produced no excess return: "
                       f"results are partly market exposure rather than selection")
    else:
        why.append(overall.get("note", "not enough evaluated predictions yet"))

    for finding in performance.get("weak_buckets", [])[:5]:
        what_failed.append(finding.get("detail", ""))

    proposal = (learning or {}).get("weight_proposal") or {}
    if proposal.get("accepted"):
        actions.append("a weight update was proposed and requires explicit "
                       "promotion as a new model version before taking effect")
    elif proposal.get("rejection_reason"):
        actions.append(f"no weight change: {proposal['rejection_reason']}")

    if run.failed_steps():
        what_failed.append(f"pipeline steps failed: {', '.join(run.failed_steps())}")

    return {"what_worked": what_worked, "what_failed": what_failed, "why": why,
            "systematic_errors": [f.get("detail") for f in
                                  performance.get("weak_buckets", [])[:5]],
            "actions_taken": actions}


def _thesis_health(result: Any) -> str:
    if result is None:
        return "not analysed today"
    if result.recommendation is Recommendation.SELL:
        return "broken: exit conditions breached"
    if result.recommendation is Recommendation.INSUFFICIENT_DATA:
        return "unassessable: insufficient data"
    if result.gates_failed:
        return f"weakening: {result.gates_failed[0]}"
    return "intact"


def _rank(recommendation: str) -> int:
    return {"SELL": 0, "AVOID": 0, "INSUFFICIENT_DATA": 1, "WATCH": 2,
            "HOLD": 3, "BUY": 4}.get(recommendation, 2)


def _as_dict(section: Any) -> dict[str, Any]:
    if section is None:
        return {}
    if hasattr(section, "as_dict"):
        return section.as_dict()
    return dict(section)
