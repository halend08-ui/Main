"""System-wide invariants.

These tests exist to protect the two properties the whole system rests on:

1. Nothing is ever fabricated. Missing data stays missing, and every layer
   degrades confidence rather than inventing a value.
2. Nothing ever uses future information.

They are deliberately broad — several inspect the source tree — because these
are properties of the *system*, not of any one function, and a regression
anywhere would be invisible to a unit test elsewhere.
"""

from __future__ import annotations

import ast
import datetime as dt
import io
import json
import re
from pathlib import Path

import pytest

from research_engine.analysis.pipeline import AnalysisInput, analyze
from research_engine.analysis.probability import forecast
from research_engine.analysis.scoring import CompositeScore, FactorScore, compose
from research_engine.core.errors import DataUnavailable, LookAheadError
from research_engine.core.logging import configure_logging, get_logger
from research_engine.core.series import PriceSeries
from research_engine.core.types import (INSUFFICIENT_DATA_MESSAGE, AssetClass,
                                        DataQuality, Horizon, Recommendation)
from research_engine.ingestion.base import Capability, DataProvider
from research_engine.ingestion.http import FakeTransport, OfflineTransport, Response
from research_engine.ingestion.registry import ProviderRegistry

PACKAGE = Path(__file__).resolve().parent.parent / "research_engine"


def python_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


# ------------------------------------------------- 1. never fabricate ------
def test_failed_providers_raise_rather_than_return_placeholders():
    class Broken(DataProvider):
        name = "broken"
        capabilities = frozenset({Capability.PRICES_EOD})

        def fetch_prices(self, symbol, **kwargs):
            raise DataUnavailable("upstream has no data for this symbol")

    registry = ProviderRegistry({"prices_eod": ["broken"]})
    registry.register(Broken(transport=FakeTransport()))
    with pytest.raises(DataUnavailable):
        registry.require(Capability.PRICES_EOD, "fetch_prices", "X", target="X")


def test_offline_transport_never_returns_a_body():
    provider = DataProvider(transport=OfflineTransport(), requests_per_minute=1000)
    with pytest.raises(Exception) as exc:
        provider.request_json("https://example.com/anything")
    assert "network access is disabled" in str(exc.value)


def test_analysis_of_an_unknown_asset_says_insufficient(price_bars, settings):
    tiny = PriceSeries.from_rows("THIN", price_bars(15, start=dt.date(2026, 1, 1)))
    result = analyze(AnalysisInput(symbol="THIN", as_of=tiny.end,
                                   asset_class=AssetClass.EQUITY, series=tiny,
                                   settings=settings))
    assert result.recommendation is Recommendation.INSUFFICIENT_DATA
    assert result.score is None
    assert result.fair_value["base"] is None
    assert INSUFFICIENT_DATA_MESSAGE in result.risks
    rendered = result.render()
    assert "n/a" in rendered            # missing values are shown as missing


def test_missing_factors_never_become_zero():
    present_only = compose([FactorScore("growth", 90.0, 1.0, DataQuality.GOOD)],
                           thresholds={"watch": 45, "moderate": 58, "strong": 70,
                                       "exceptional": 82})
    with_missing = compose(
        [FactorScore("growth", 90.0, 1.0, DataQuality.GOOD),
         FactorScore("valuation", None, 0.2, DataQuality.INSUFFICIENT)],
        thresholds={"watch": 45, "moderate": 58, "strong": 70, "exceptional": 82})
    # the missing factor lowers the score through the coverage haircut,
    # but nowhere near the 45 that averaging in a zero would produce
    assert with_missing.total < present_only.total
    assert with_missing.total > 80


def test_probability_without_evidence_is_the_base_rate():
    result = forecast(asset_class="equity", horizon=Horizon.Y1, score=None)
    assert result.prob_positive == pytest.approx(result.base_rate, abs=0.06)
    assert result.adjustments[0]["source"] == "base_rate"


def test_no_module_silently_defaults_a_price_or_return():
    """Guard against `or 0` on a price/return/value expression.

    ``value or 0`` is the idiom that turns "unknown" into "zero" without anyone
    noticing, so it is banned on these names.
    """
    banned = re.compile(
        r"\b(price|close|value|return|revenue|earnings|fcf|market_cap)\w*\s+or\s+0(?![.\w])")
    offenders: list[str] = []
    for path in python_files():
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if banned.search(line):
                offenders.append(f"{path.relative_to(PACKAGE)}:{number}: {line.strip()}")
    assert not offenders, "unknown values must not default to zero:\n" + "\n".join(offenders)


def test_synthetic_data_generation_lives_only_in_tests_and_scripts():
    """The engine itself must contain no data-generating code path."""
    suspicious = re.compile(r"\b(random\.gauss|np\.random|random\.uniform)\b")
    offenders = [str(p.relative_to(PACKAGE)) for p in python_files()
                 if suspicious.search(p.read_text())]
    assert not offenders, ("random data generation found inside the engine: "
                           + ", ".join(offenders))


def test_every_provider_declares_what_it_cannot_supply():
    from research_engine.ingestion.providers import (CoinGeckoProvider,
                                                     SecEdgarProvider,
                                                     StooqProvider)
    transport = FakeTransport().add_text(
        "stooq.com", "Date,Open,High,Low,Close,Volume\n2026-01-02,1,2,0.5,1.5,100\n")
    stooq = StooqProvider(transport=transport, requests_per_minute=10_000)
    result = stooq.fetch_prices("AAPL")
    assert result.missing and result.partial
    assert result.records[0]["adj_close"] is None      # not silently set to close


# ------------------------------------------------ 2. never look ahead ------
def test_price_series_cannot_be_asked_for_the_future(sample_series):
    cut = sample_series.as_of(sample_series.dates[100])
    assert cut.end == sample_series.dates[100]
    with pytest.raises(LookAheadError):
        cut.require_no_future(sample_series.dates[50])


def test_point_in_time_reads_are_the_repository_default(repos):
    """Fundamental history filtered by as-of must never include later filings."""
    from research_engine.core.types import SourceTier
    asset_id = repos["assets"].upsert(symbol="PIT", asset_class="equity")
    repos["fundamentals"].write(asset_id, [
        {"metric": "revenue", "period": "annual", "period_end": "2025-12-31",
         "value": 100, "filed_date": "2026-02-20", "accession": "a1"},
    ], source="test", source_tier=SourceTier.REGULATORY_FILING)
    assert repos["fundamentals"].history(asset_id, "revenue", as_of="2026-01-01") == []
    assert repos["fundamentals"].history(asset_id, "revenue", as_of="2026-03-01")


def test_backtest_strategies_receive_no_future_data(price_bars):
    from research_engine.backtest.engine import (AssetHistory, Backtester,
                                                 BacktestConfig)
    universe = {
        s: AssetHistory(symbol=s,
                        series=PriceSeries.from_rows(s, price_bars(400, seed=i + 1,
                                                                   start=dt.date(2024, 1, 2))),
                        listed_date=dt.date(2020, 1, 1), market_cap=5e9)
        for i, s in enumerate(("A", "B"))}
    dates = universe["A"].series.dates
    config = BacktestConfig(start=dates[60], end=dates[-1], rebalance_days=21,
                            warn_on_survivorship=False)
    seen: list[tuple[dt.date, dt.date]] = []

    def strategy(as_of, visible):
        for asset in visible.values():
            seen.append((as_of, asset.series.end))
        return {"A": 0.3}

    Backtester(universe, config).run(strategy)
    assert seen
    assert all(end <= as_of for as_of, end in seen)


def test_no_analysis_module_reads_the_wall_clock():
    """Analysis must be driven by an explicit as-of date, not by `today`.

    A module that reads the clock produces a different answer when replayed,
    which silently breaks historical evaluation.
    """
    offenders: list[str] = []
    for path in sorted((PACKAGE / "analysis").rglob("*.py")) + \
                sorted((PACKAGE / "features").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("today", "now", "utcnow"):
                    offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert not offenders, ("analysis/features must take an as-of date rather than "
                           "reading the clock: " + ", ".join(offenders))


# --------------------------------------------------- 3. never trade -------
def test_no_execution_code_exists():
    """No order-placing calls and no broker SDK imports anywhere in the engine.

    Matches code, not prose: the CLI legitimately *warns* about brokers, and a
    warning is the opposite of an integration.
    """
    call_pattern = re.compile(
        r"\b(place_order|submit_order|create_order|execute_trade|cancel_order)\s*\(")
    broker_sdks = {"alpaca", "alpaca_trade_api", "ib_insync", "ibapi", "ccxt",
                   "robin_stocks", "tda", "schwab", "oandapyV20"}
    offenders: list[str] = []
    for path in python_files():
        text = path.read_text()
        for number, line in enumerate(text.splitlines(), start=1):
            if call_pattern.search(line):
                offenders.append(f"{path.relative_to(PACKAGE)}:{number} call")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in broker_sdks:
                    offenders.append(
                        f"{path.relative_to(PACKAGE)}:{node.lineno} imports {name}")
    assert not offenders, "order-execution code found: " + ", ".join(offenders)


def test_trading_flag_cannot_be_enabled():
    from research_engine.config.settings import default_settings
    from research_engine.core.errors import ConfigError
    with pytest.raises(ConfigError):
        default_settings({"app": {"allow_trading": True}})


# ------------------------------------------------- 4. never leak keys ------
def test_no_credential_literals_in_the_source():
    patterns = (re.compile(r"(?i)api[_-]?key\s*=\s*['\"][A-Za-z0-9_\-]{12,}['\"]"),
                re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
                re.compile(r"(?i)(secret|password|token)\s*=\s*['\"][^'\"]{10,}['\"]"))
    offenders: list[str] = []
    for path in python_files() + [PACKAGE / "config" / "default.yaml"]:
        text = path.read_text()
        for pattern in patterns:
            for match in pattern.finditer(text):
                snippet = match.group(0)
                if any(token in snippet for token in ("env", "ENV", "getenv", "None")):
                    continue
                offenders.append(f"{path.name}: {snippet[:60]}")
    assert not offenders, "possible credential literals: " + "; ".join(offenders)


def test_secrets_are_scrubbed_from_every_log_record():
    buffer = io.StringIO()
    configure_logging("DEBUG", json_output=True, stream=buffer,
                      secrets=["SUPER-SECRET-KEY-123"])
    log = get_logger("audit")
    log.info("calling https://api/v1?api_key=SUPER-SECRET-KEY-123",
             key="SUPER-SECRET-KEY-123", nested="Bearer SUPER-SECRET-KEY-123")
    log.warning("retrying", url="https://x?token=SUPER-SECRET-KEY-123")
    output = buffer.getvalue()
    assert "SUPER-SECRET-KEY-123" not in output
    assert output.count("REDACTED") >= 3
    import logging
    logging.getLogger().handlers.clear()


# --------------------------------------- 5. uncertainty is always stated ---
def test_every_public_output_carries_its_uncertainty(price_bars, settings):
    from research_engine.analysis import memo as MEMO
    from research_engine.pipeline.report import DailyReport
    from tests.test_features import COMPOUNDER

    series = PriceSeries.from_rows("ACME", price_bars(700, start=dt.date(2023, 1, 2)))
    result = analyze(AnalysisInput(symbol="ACME", as_of=series.end,
                                   asset_class=AssetClass.EQUITY, series=series,
                                   annual=COMPOUNDER, market={"market_cap_usd": 5e9},
                                   sector="Technology", settings=settings))
    rendered = result.render()
    assert "not investment advice" in rendered.lower()
    assert "can be wrong" in rendered.lower()

    memo = MEMO.generate(result)
    assert "not investment advice" in memo.lower()
    assert "material uncertainty" in memo.lower()

    report = DailyReport(as_of=series.end).render()
    assert "Not investment advice" in report
    assert "past performance does not guarantee" in report


def test_probabilities_are_labelled_as_model_output():
    result = forecast(asset_class="equity", horizon=Horizon.Y1, score=80)
    payload = result.to_dict()
    assert payload["claim_type"] == "model_prediction"
    assert "not a guarantee" in payload["disclaimer"]
