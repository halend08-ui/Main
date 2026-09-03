"""Command-line interface.

Every operation the system performs is available here, which keeps the daily
loop scriptable (cron, systemd timer, CI) with no web layer required.

    research-engine init                      create the database
    research-engine providers                 show provider health and keys
    research-engine ingest --symbols AAPL     pull data for specific assets
    research-engine universe --refresh        build the asset universe
    research-engine daily --as-of 2026-01-05  run the full research loop
    research-engine analyze NVDA              deep research on one asset
    research-engine report --latest           print the most recent report
    research-engine backtest --name momentum  run a walk-forward backtest
    research-engine evaluate                  grade matured predictions
    research-engine models                    list model versions
    research-engine serve                     start the read-only API
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

from research_engine import DISCLAIMER, __version__
from research_engine.config.settings import Settings, load_settings
from research_engine.core.errors import ConfigError, DataUnavailable, ResearchEngineError
from research_engine.core.logging import configure_logging, get_logger, secrets_from_environ
from research_engine.core.timeutil import to_date

log = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-engine",
        description="Autonomous investment research engine (decision support only)",
        epilog=DISCLAIMER)
    parser.add_argument("--config", help="path to a YAML configuration file")
    parser.add_argument("--log-level", default=None,
                        help="DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--json-logs", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create or migrate the database")

    providers = sub.add_parser("providers", help="show provider configuration/health")
    providers.add_argument("--json", action="store_true")

    universe = sub.add_parser("universe", help="build or inspect the asset universe")
    universe.add_argument("--refresh", action="store_true",
                          help="fetch the universe from providers")
    universe.add_argument("--asset-class", choices=["equity", "crypto"], default=None)
    universe.add_argument("--limit", type=int, default=25)

    ingest = sub.add_parser("ingest", help="download data for assets")
    ingest.add_argument("--symbols", nargs="+", required=True)
    ingest.add_argument("--what", nargs="+",
                        default=["prices", "fundamentals"],
                        choices=["prices", "fundamentals", "news", "macro"])

    daily = sub.add_parser("daily", help="run the full daily research loop")
    daily.add_argument("--as-of", default=None)
    daily.add_argument("--symbols", nargs="+", default=None)
    daily.add_argument("--output", default=None, help="write the report to this path")
    daily.add_argument("--json", action="store_true")

    analyze = sub.add_parser("analyze", help="deep research on a single asset")
    analyze.add_argument("symbol")
    analyze.add_argument("--as-of", default=None)
    analyze.add_argument("--peers", nargs="+", default=())
    analyze.add_argument("--memo", action="store_true", help="print the full memo")
    analyze.add_argument("--json", action="store_true")

    report = sub.add_parser("report", help="print a stored report")
    report.add_argument("--kind", default="daily", choices=["daily", "memo",
                                                            "self_evaluation"])
    report.add_argument("--latest", action="store_true")

    backtest = sub.add_parser("backtest", help="run a walk-forward backtest")
    backtest.add_argument("--name", default="baseline")
    backtest.add_argument("--start", default=None)
    backtest.add_argument("--end", default=None)
    backtest.add_argument("--strategy", default="score",
                          choices=["score", "momentum", "equal_weight"])
    backtest.add_argument("--json", action="store_true")

    sub.add_parser("evaluate", help="grade predictions whose horizon has elapsed")

    models = sub.add_parser("models", help="list model versions")
    models.add_argument("--family", default=None)
    models.add_argument("--promote", default=None, metavar="VERSION")

    serve = sub.add_parser("serve", help="start the read-only HTTP API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    doctor = sub.add_parser("doctor", help="check configuration and data health")
    doctor.add_argument("--json", action="store_true")
    return parser


# ------------------------------------------------------------- services ----
def _settings(args: argparse.Namespace) -> Settings:
    overrides: dict[str, Any] = {}
    if args.log_level:
        overrides.setdefault("app", {})["log_level"] = args.log_level
    if args.json_logs:
        overrides.setdefault("app", {})["log_json"] = True
    return load_settings(args.config, overrides=overrides)


def _services(settings: Settings) -> dict[str, Any]:
    """Wire the repositories and the data adapter."""
    from research_engine.pipeline.data_access import RepositoryDataAccess
    from research_engine.storage.analysis_repos import (AlertRepository,
                                                        BacktestRepository,
                                                        ModelRegistryRepository,
                                                        PortfolioRepository,
                                                        PredictionRepository,
                                                        RecommendationRepository,
                                                        ReportRepository,
                                                        ResearchQueueRepository,
                                                        ScoreRepository,
                                                        SignalRepository)
    from research_engine.storage.db import connect
    from research_engine.storage.reference_repos import (CryptoMetricRepository,
                                                         DataQualityRepository,
                                                         DataSourceRepository,
                                                         EventRepository,
                                                         MacroRepository,
                                                         NewsRepository,
                                                         OwnershipRepository)
    from research_engine.storage.repositories import (AssetRepository,
                                                      FundamentalRepository,
                                                      PriceRepository)

    db = connect(settings)
    repos = {
        "assets": AssetRepository(db), "prices": PriceRepository(db),
        "fundamentals": FundamentalRepository(db), "news": NewsRepository(db),
        "events": EventRepository(db), "macro": MacroRepository(db),
        "crypto": CryptoMetricRepository(db), "ownership": OwnershipRepository(db),
        "sources": DataSourceRepository(db), "quality": DataQualityRepository(db),
        "scores": ScoreRepository(db), "recommendations": RecommendationRepository(db),
        "signals": SignalRepository(db), "predictions": PredictionRepository(db),
        "models": ModelRegistryRepository(db), "backtests": BacktestRepository(db),
        "portfolio": PortfolioRepository(db), "queue": ResearchQueueRepository(db),
        "alerts": AlertRepository(db), "reports": ReportRepository(db),
    }
    ingestor = _ingestor(settings, repos)
    return {"db": db, "repos": repos,
            "data": RepositoryDataAccess(settings, repos, ingestor=ingestor),
            "ingestor": ingestor}


def _ingestor(settings: Settings, repos: dict[str, Any]) -> Any:
    from research_engine.ingestion.factory import build_registry
    from research_engine.ingestion.service import IngestionService

    registry = build_registry(
        settings,
        health_hook=lambda name, ok: (repos["sources"].record_success(name) if ok
                                      else repos["sources"].record_failure(name)))
    return IngestionService(settings, registry, repos)


# ------------------------------------------------------------- commands ----
def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    services = _services(settings)
    counts = services["db"].table_counts()
    print(f"database ready at {settings.database_path}")
    print(f"tables: {len(counts)}")
    print(f"rows: {sum(counts.values())}")
    return 0


def cmd_providers(args: argparse.Namespace, settings: Settings) -> int:
    from research_engine.ingestion.factory import build_registry
    registry = build_registry(settings)
    described = registry.describe()
    if args.json:
        print(json.dumps(described, indent=2))
        return 0
    for provider in described:
        status = "available" if provider["available"] else "UNAVAILABLE"
        print(f"{provider['name']:<12} {status:<12} "
              f"tier={provider['source_tier']:<20} "
              f"caps={','.join(provider['capabilities'])}")
        if provider["unavailable_reason"]:
            print(f"             reason: {provider['unavailable_reason']}")
        if provider["documentation"]:
            print(f"             {provider['documentation']}")
    return 0


def cmd_universe(args: argparse.Namespace, settings: Settings) -> int:
    services = _services(settings)
    repos = services["repos"]
    if args.refresh:
        from research_engine.ingestion.universe import UniverseBuilder
        builder = UniverseBuilder(settings, services["ingestor"].registry,
                                  repos["assets"])
        for asset_class in (settings.get("universe.asset_classes") or ["equity"]):
            stats = (builder.build_crypto() if asset_class == "crypto"
                     else builder.build_equities())
            print(f"{asset_class}: {json.dumps(stats.as_dict())}")
    assets = repos["assets"].list(asset_class=args.asset_class, limit=args.limit)
    print(f"{'symbol':<10} {'class':<8} {'sector':<24} {'market cap':>16}")
    for asset in assets:
        cap = f"{asset.market_cap_usd:,.0f}" if asset.market_cap_usd else "n/a"
        print(f"{asset.symbol:<10} {asset.asset_class.value:<8} "
              f"{(asset.sector or '-'):<24} {cap:>16}")
    print(f"\ntotal assets: {repos['assets'].count()}")
    return 0


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    services = _services(settings)
    result = services["ingestor"].ingest(args.symbols, kinds=args.what)
    print(json.dumps(result, indent=2, default=str))
    return 0 if not result.get("failures") else 1


def cmd_daily(args: argparse.Namespace, settings: Settings) -> int:
    from research_engine.pipeline.daily import DailyPipeline
    services = _services(settings)
    as_of = to_date(args.as_of) if args.as_of else date.today()
    pipeline = DailyPipeline(settings, data=services["data"],
                             repositories=services["repos"])
    run = pipeline.run(as_of, symbols=args.symbols)
    if args.json:
        print(json.dumps(run.to_dict(), indent=2, default=str))
    elif run.report is not None:
        text = run.report.render()
        print(text)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"\nwritten to {args.output}", file=sys.stderr)
    return 0 if run.ok else 1


def cmd_analyze(args: argparse.Namespace, settings: Settings) -> int:
    from research_engine.analysis.agent import ResearchAgent
    services = _services(settings)
    agent = ResearchAgent(settings, services["data"])
    as_of = to_date(args.as_of) if args.as_of else date.today()
    dossier = agent.investigate(args.symbol, as_of=as_of, peer_symbols=args.peers)
    if args.json:
        print(json.dumps(dossier.to_dict(), indent=2, default=str))
    elif args.memo:
        print(dossier.memo)
    elif dossier.recommendation is not None:
        print(dossier.recommendation.render())
        if dossier.reverse_dcf.get("comparison"):
            print(f"\nReverse DCF: {dossier.reverse_dcf['comparison']}")
        if dossier.unanswered:
            print("\nQuestions this system could not answer:")
            for question in dossier.unanswered:
                print(f"  * {question}")
    else:
        print(dossier.memo)
        return 1
    return 0


def cmd_report(args: argparse.Namespace, settings: Settings) -> int:
    services = _services(settings)
    report = services["repos"]["reports"].latest(args.kind)
    if not report:
        print(f"no {args.kind} report stored yet", file=sys.stderr)
        return 1
    print(report["body_markdown"])
    return 0


def cmd_backtest(args: argparse.Namespace, settings: Settings) -> int:
    from research_engine.backtest.engine import (AssetHistory, Backtester,
                                                 BacktestConfig)
    from research_engine.backtest.costs import CostModel

    services = _services(settings)
    repos = services["repos"]
    universe: dict[str, AssetHistory] = {}
    for asset in repos["assets"].list(active_only=False, limit=500):
        try:
            series = repos["prices"].series(asset.id, asset.symbol)
        except DataUnavailable:
            continue
        universe[asset.symbol] = AssetHistory(
            symbol=asset.symbol, series=series, listed_date=asset.listed_date,
            delisted_date=asset.delisted_date,
            asset_class=asset.asset_class.value, sector=asset.sector,
            market_cap=asset.market_cap_usd)
    if len(universe) < 2:
        print("not enough price history stored to backtest", file=sys.stderr)
        return 1

    start = to_date(args.start) if args.start else to_date(
        settings.get("backtest.start", "2015-01-01"))
    end = to_date(args.end) if args.end else max(a.series.end for a in universe.values())
    config = BacktestConfig(
        start=start, end=end,
        initial_capital=float(settings.get("backtest.initial_capital", 100_000)),
        cost_model=CostModel(
            commission_bps=float(settings.get("backtest.commission_bps", 5)),
            spread_bps=float(settings.get("backtest.slippage_bps", 10)),
            crypto_spread_bps=float(settings.get("backtest.crypto_slippage_bps", 30))),
        embargo_days=int(settings.get("backtest.embargo_days", 5)),
        train_window_days=int(settings.get("backtest.train_window_days", 1260)),
        test_window_days=int(settings.get("backtest.test_window_days", 252)),
        step_days=int(settings.get("backtest.step_days", 252)),
        risk_free_rate=settings.risk_free_rate())

    strategy = _strategy(args.strategy)
    engine = Backtester(universe, config)
    result = engine.run(strategy, name=args.name)
    repos["backtests"].write(
        name=args.name, config=result.config, start=start, end=end,
        metrics=result.metrics, benchmark_metrics=result.benchmarks,
        warnings=result.warnings)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(f"backtest '{args.name}' {start} -> {end}")
        for key in ("cagr", "volatility", "sharpe", "sortino", "max_drawdown",
                    "total_return"):
            print(f"  {key:<16} {result.metrics.get(key)}")
        print(f"  trades           {result.trade_metrics.get('trades')}")
        print(f"  win rate         {result.trade_metrics.get('win_rate')}")
        print(f"  turnover         {result.trade_metrics.get('annual_turnover')}")
        for label, metrics in result.benchmarks.items():
            print(f"  benchmark {label:<8} cagr={metrics.get('cagr')}")
        for warning in result.warnings:
            print(f"  ! {warning}")
        print("\nBacktests are simulations. They do not establish that this "
              "strategy will work in future.")
    return 0


def _strategy(name: str):
    """Built-in strategies used for validation, not for recommendation."""
    def equal_weight(as_of, visible):
        symbols = sorted(visible)[:20]
        return {s: 1.0 / max(len(symbols), 1) for s in symbols}

    def momentum(as_of, visible):
        ranked = []
        for symbol, asset in visible.items():
            px = asset.series.adj_close
            if px.size > 130 and px[-126] > 0:
                ranked.append((symbol, float(px[-1] / px[-126] - 1)))
        ranked.sort(key=lambda item: -item[1])
        chosen = [s for s, _ in ranked[:10]]
        return {s: 0.1 for s in chosen}

    return {"equal_weight": equal_weight, "momentum": momentum,
            "score": momentum}[name]


def cmd_evaluate(args: argparse.Namespace, settings: Settings) -> int:
    from research_engine.pipeline.daily import DailyPipeline
    services = _services(settings)
    pipeline = DailyPipeline(settings, data=services["data"],
                             repositories=services["repos"])
    detail = pipeline._evaluate(date.today())
    print(json.dumps(detail, indent=2, default=str))
    return 0


def cmd_models(args: argparse.Namespace, settings: Settings) -> int:
    services = _services(settings)
    repo = services["repos"]["models"]
    if args.promote:
        repo.promote(args.promote)
        print(f"promoted {args.promote}")
        return 0
    for model in repo.list(args.family):
        print(f"{model['version']:<18} {model['family']:<12} {model['status']:<10} "
              f"created={model['created_at']}")
    return 0


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    from research_engine.api.server import serve
    host = args.host or str(settings.get("api.host", "127.0.0.1"))
    port = args.port or int(settings.get("api.port", 8000))
    return serve(settings, host=host, port=port)


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    """Check that the system is configured to produce trustworthy output."""
    from research_engine.ingestion.factory import build_registry

    findings: list[dict[str, Any]] = []
    services = _services(settings)
    repos = services["repos"]

    registry = build_registry(settings)
    for provider in registry.describe():
        if not provider["available"]:
            findings.append({"area": "providers", "severity": "warning",
                             "detail": provider["unavailable_reason"]})

    total = repos["assets"].count()
    if total == 0:
        findings.append({"area": "universe", "severity": "error",
                         "detail": "no assets: run 'universe --refresh' first"})

    with_prices = int(services["db"].scalar(
        "SELECT COUNT(DISTINCT asset_id) FROM prices_daily") or 0)
    if total and with_prices < total * 0.5:
        findings.append({"area": "prices", "severity": "warning",
                         "detail": f"only {with_prices} of {total} assets have "
                                   f"price history"})

    open_predictions = repos["predictions"].open_count()
    evaluated = len(repos["predictions"].evaluated(limit=1))
    if open_predictions and not evaluated:
        findings.append({"area": "learning", "severity": "info",
                         "detail": f"{open_predictions} predictions are awaiting "
                                   f"their horizon; performance statistics will be "
                                   f"empty until then"})

    if settings.allow_trading:
        findings.append({"area": "security", "severity": "critical",
                         "detail": "app.allow_trading is enabled: this system does "
                                   "not implement execution and must not be wired "
                                   "to a broker"})

    summary = {"config_sources": list(settings.source_files),
               "database": str(settings.database_path),
               "assets": total, "assets_with_prices": with_prices,
               "open_predictions": open_predictions, "findings": findings}
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"config:   {', '.join(settings.source_files)}")
        print(f"database: {settings.database_path}")
        print(f"assets:   {total} ({with_prices} with price history)")
        print(f"open predictions: {open_predictions}")
        if not findings:
            print("\nno issues found")
        for finding in findings:
            print(f"\n[{finding['severity'].upper()}] {finding['area']}: "
                  f"{finding['detail']}")
    return 1 if any(f["severity"] in ("error", "critical") for f in findings) else 0


COMMANDS = {
    "init": cmd_init, "providers": cmd_providers, "universe": cmd_universe,
    "ingest": cmd_ingest, "daily": cmd_daily, "analyze": cmd_analyze,
    "report": cmd_report, "backtest": cmd_backtest, "evaluate": cmd_evaluate,
    "models": cmd_models, "serve": cmd_serve, "doctor": cmd_doctor,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = _settings(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(str(args.log_level or settings.get("app.log_level", "INFO")),
                      json_output=bool(args.json_logs
                                       or settings.get("app.log_json", False)),
                      secrets=secrets_from_environ())
    handler = COMMANDS[args.command]
    try:
        return handler(args, settings)
    except ResearchEngineError as exc:
        log.error("command failed", command=args.command, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
