"""Ingestion service: fetch through the provider chain, validate, persist.

This is where the "never fabricate" rule is enforced at the boundary. For each
asset and data kind, the service:

1. asks the registry (which handles failover) for the data;
2. records what was and was not obtained, per provider;
3. validates the result before writing;
4. writes a data-quality report alongside the data itself.

If nothing usable comes back, nothing is written and the gap is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.errors import DataUnavailable, ProviderError
from research_engine.core.logging import get_logger
from research_engine.core.series import PriceSeries
from research_engine.core.timeutil import CRYPTO_CALENDAR, EQUITY_CALENDAR, to_date
from research_engine.core.types import AssetClass, DataQuality, SourceTier
from research_engine.ingestion.base import Capability
from research_engine.ingestion.registry import ProviderRegistry
from research_engine.quality import checks as QC
from research_engine.quality import grading as QG

log = get_logger(__name__)


@dataclass
class IngestionReport:
    started: date
    requested: int = 0
    succeeded: int = 0
    rows_written: int = 0
    failures: list[str] = field(default_factory=list)
    per_kind: dict[str, int] = field(default_factory=dict)
    quality: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.started.isoformat(), "requested": self.requested,
                "succeeded": self.succeeded, "rows_written": self.rows_written,
                "failures": self.failures[:20], "per_kind": self.per_kind,
                "quality": self.quality}


class IngestionService:
    def __init__(self, settings: Any, registry: ProviderRegistry,
                 repositories: Mapping[str, Any]) -> None:
        self.settings = settings
        self.registry = registry
        self.repos = dict(repositories)

    # -- public API --------------------------------------------------------
    def refresh(self, as_of: date, *, limit: int | None = None) -> dict[str, Any]:
        """Update the whole active universe. Used by the daily loop."""
        assets = self.repos["assets"].list(active_only=True, limit=limit)
        symbols = [a.symbol for a in assets]
        result = self.ingest(symbols, kinds=("prices", "fundamentals", "news"),
                             as_of=as_of)
        result["health"] = {"providers": self.registry.stats(),
                            "coverage": self._coverage()}
        return result

    def ingest(self, symbols: Sequence[str], *,
               kinds: Sequence[str] = ("prices", "fundamentals"),
               as_of: date | None = None) -> dict[str, Any]:
        as_of = as_of or date.today()
        report = IngestionReport(started=as_of, requested=len(symbols))
        # Macro series are not per-asset: they are fetched once for the whole
        # run below. Including them in the per-symbol loop would report one
        # failure per symbol for work that actually succeeded.
        per_asset_kinds = [k for k in kinds if k != "macro"]
        for symbol in symbols:
            asset = self.repos["assets"].get(symbol)
            if asset is None:
                report.failures.append(f"{symbol}: not in the asset universe")
                continue
            ok = False
            for kind in per_asset_kinds:
                try:
                    written = self._ingest_one(asset, kind, as_of)
                except (DataUnavailable, ProviderError) as exc:
                    report.failures.append(f"{symbol}/{kind}: {exc}")
                    continue
                except Exception as exc:
                    log.exception("ingestion failed", symbol=symbol, kind=kind)
                    report.failures.append(f"{symbol}/{kind}: unexpected {exc}")
                    continue
                report.rows_written += written
                report.per_kind[kind] = report.per_kind.get(kind, 0) + written
                ok = ok or written > 0
            if ok:
                report.succeeded += 1
                grade = self._grade_prices(asset, as_of)
                if grade is not None:
                    report.quality[symbol] = grade.value
        if "macro" in kinds:
            report.per_kind["macro"] = self.ingest_macro(as_of)
        return report.to_dict()

    def ingest_macro(self, as_of: date | None = None) -> int:
        from research_engine.ingestion.providers.fred import DEFAULT_SERIES
        repo = self.repos.get("macro")
        if repo is None:
            return 0
        written = 0
        for series_id in DEFAULT_SERIES:
            try:
                result, _ = self.registry.require(
                    Capability.MACRO, "fetch_macro", series_id, target=series_id)
            except DataUnavailable as exc:
                log.warning("macro series unavailable", series=series_id,
                            error=str(exc)[:160])
                continue
            written += repo.write(series_id, result.records,
                                  source=result.provider,
                                  source_tier=result.provenance.source_tier)
        return written

    # -- per-kind ingestion ------------------------------------------------
    def _ingest_one(self, asset: Any, kind: str, as_of: date) -> int:
        if kind == "prices":
            return self._ingest_prices(asset, as_of)
        if kind == "fundamentals":
            return self._ingest_fundamentals(asset, as_of)
        if kind == "news":
            return self._ingest_news(asset, as_of)
        raise ValueError(f"unknown ingestion kind {kind!r}")

    def _ingest_prices(self, asset: Any, as_of: date) -> int:
        coverage = self.repos["prices"].coverage(asset.id)
        start = None
        if coverage.get("hi"):
            # incremental: re-fetch a small overlap so corrections are picked up
            start = to_date(coverage["hi"]) - timedelta(days=5)

        capability = (Capability.CRYPTO_HISTORY if asset.is_crypto
                      else Capability.PRICES_EOD)
        method = "fetch_crypto_history" if asset.is_crypto else "fetch_prices"
        kwargs: dict[str, Any] = {}
        args: tuple[Any, ...]
        if asset.is_crypto:
            args = (asset.coingecko_id or asset.symbol.lower(),)
            kwargs["days"] = 3650 if start is None else max(
                7, (as_of - start).days + 5)
        else:
            args = (asset.symbol,)
            kwargs["start"] = start
            kwargs["end"] = as_of

        result, report = self.registry.require(capability, method, *args,
                                               target=asset.symbol, **kwargs)
        quality = (DataQuality.GOOD if not result.partial else DataQuality.FAIR)
        written = self.repos["prices"].write_bars(asset.id, result.records,
                                                  source=result.provider,
                                                  quality=quality)
        if result.missing:
            log.debug("price fields unavailable", symbol=asset.symbol,
                      missing=",".join(result.missing), provider=result.provider)
        return written

    def _ingest_fundamentals(self, asset: Any, as_of: date) -> int:
        if asset.is_crypto:
            return 0            # tokens have no financial statements
        result, _ = self.registry.require(
            Capability.FUNDAMENTALS, "fetch_fundamentals", asset.symbol,
            target=asset.symbol, identifiers={"cik": asset.cik})
        return self.repos["fundamentals"].write(
            asset.id, result.records, source=result.provider,
            source_tier=result.provenance.source_tier)

    def _ingest_news(self, asset: Any, as_of: date) -> int:
        repo = self.repos.get("news")
        if repo is None:
            return 0
        try:
            result, _ = self.registry.require(
                Capability.NEWS, "fetch_news", asset.symbol, target=asset.symbol)
        except DataUnavailable:
            return 0            # no news is a normal state, not an error
        items = [{**record, "asset_id": asset.id, "symbol": asset.symbol}
                 for record in result.records]
        return repo.write(items)

    # -- quality -----------------------------------------------------------
    def _grade_prices(self, asset: Any, as_of: date) -> DataQuality | None:
        try:
            series = self.repos["prices"].series(asset.id, asset.symbol, as_of=as_of)
        except DataUnavailable:
            return None
        is_crypto = asset.is_crypto
        report = QG.finalize(QC.check_price_series(
            series, as_of=as_of,
            calendar=CRYPTO_CALENDAR if is_crypto else EQUITY_CALENDAR,
            max_staleness_days=int(self.settings.get(
                "quality.max_crypto_price_staleness_days" if is_crypto
                else "quality.max_price_staleness_days", 5)),
            max_daily_move_abs=float(self.settings.get(
                "quality.max_crypto_daily_move_abs" if is_crypto
                else "quality.max_daily_move_abs", 0.6)),
            min_history_days=int(self.settings.get(
                "quality.min_history_days_for_analysis", 120)),
            min_coverage_ratio=float(self.settings.get(
                "quality.min_coverage_ratio", 0.9)),
            is_crypto=is_crypto))
        repo = self.repos.get("quality")
        if repo is not None:
            repo.write(asset.id, scope="prices", as_of=as_of, grade=report.grade,
                       score=report.score,
                       issues=[i.to_dict() for i in report.issues])
        if report.worst >= QC.Severity.ERROR:
            log.warning("price data quality issues", symbol=asset.symbol,
                        grade=report.grade.value,
                        issues=",".join(sorted(report.codes())))
        return report.grade

    def _coverage(self) -> dict[str, Any]:
        assets = self.repos["assets"]
        total = assets.count()
        db = getattr(self.repos["prices"], "db", None)
        with_prices = 0
        if db is not None:
            with_prices = int(db.scalar(
                "SELECT COUNT(DISTINCT asset_id) FROM prices_daily") or 0)
        return {"total": total, "with_prices": with_prices}
