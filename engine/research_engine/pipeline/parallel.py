"""Parallel universe scanning.

Analysis is CPU-bound numpy work, so threads do not help: measured on this
codebase, four threads were 2.4x SLOWER than sequential (GIL contention plus
SQLite lock contention). Processes do help, because each gets its own
interpreter and its own database connection.

The design keeps workers stateless and picklable:

* the parent sends each worker a list of symbols and an as-of date;
* each worker opens its own read-only database handle and analyses its slice;
* results come back as flat :class:`Candidate` records plus the rendered
  recommendation payload -- never live objects holding a connection.

A worker that fails takes down its own slice and nothing else: the parent
records which symbols were lost and continues, because losing 200 of 5,000
assets should not lose the other 4,800.
"""

from __future__ import annotations

import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.logging import get_logger

log = get_logger(__name__)

#: Module-level handles, initialised once per worker process. Building the
#: repositories per asset would cost more than the analysis itself.
_WORKER: dict[str, Any] = {}


@dataclass
class ScanResult:
    as_of: date
    candidates: list[Any] = field(default_factory=list)
    payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    failures: list[dict[str, str]] = field(default_factory=list)
    seconds: float = 0.0
    workers: int = 1
    analysed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(), "analysed": self.analysed,
                "failures": self.failures[:50], "seconds": round(self.seconds, 2),
                "workers": self.workers,
                "assets_per_second": (round(self.analysed / self.seconds, 1)
                                      if self.seconds > 0 else None)}


def _init_worker(config_overrides: Mapping[str, Any] | None,
                 config_path: str | None) -> None:
    """Build one settings object, database handle and adapter per process."""
    from research_engine.config.settings import load_settings
    from research_engine.pipeline.data_access import RepositoryDataAccess
    from research_engine.storage.analysis_repos import (ModelRegistryRepository,
                                                        PortfolioRepository,
                                                        PredictionRepository,
                                                        RecommendationRepository,
                                                        ScoreRepository)
    from research_engine.storage.db import Database
    from research_engine.storage.reference_repos import (CryptoMetricRepository,
                                                         MacroRepository,
                                                         NewsRepository)
    from research_engine.storage.repositories import (AssetRepository,
                                                      FundamentalRepository,
                                                      PriceRepository)

    settings = load_settings(config_path, overrides=config_overrides or {})
    db = Database(settings.database_path)
    repos = {
        "assets": AssetRepository(db), "prices": PriceRepository(db),
        "fundamentals": FundamentalRepository(db), "news": NewsRepository(db),
        "macro": MacroRepository(db), "crypto": CryptoMetricRepository(db),
        "scores": ScoreRepository(db),
        "recommendations": RecommendationRepository(db),
        "predictions": PredictionRepository(db),
        "models": ModelRegistryRepository(db),
        "portfolio": PortfolioRepository(db),
    }
    _WORKER["settings"] = settings
    _WORKER["data"] = RepositoryDataAccess(settings, repos)
    _WORKER["pid"] = os.getpid()


def _analyse_slice(symbols: Sequence[str], as_of: date, model_version: str
                   ) -> tuple[list[Any], dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Analyse one slice inside a worker process."""
    from research_engine.analysis.comparison import candidate_from_result
    from research_engine.analysis.pipeline import analyze
    from research_engine.core.errors import DataUnavailable

    settings = _WORKER["settings"]
    data = _WORKER["data"]
    candidates: list[Any] = []
    payloads: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []

    for symbol in symbols:
        try:
            bundle = data.analysis_input(symbol, as_of)
            bundle.settings = settings
            bundle.model_version = model_version
            result = analyze(bundle)
        except (DataUnavailable, KeyError) as exc:
            failures.append({"symbol": symbol, "error": str(exc)[:200]})
            continue
        except Exception as exc:                      # one bad asset, not the slice
            failures.append({"symbol": symbol,
                             "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        asset = data.asset(symbol)
        candidates.append(candidate_from_result(
            result, sector=asset.sector, market_cap=asset.market_cap_usd,
            asset_class=asset.asset_class.value))
        payloads[symbol] = result.to_dict()
    return candidates, payloads, failures


def scan(symbols: Sequence[str], as_of: date, *, settings: Any,
         model_version: str = "scoring_v1", workers: int | None = None,
         chunk_size: int | None = None, config_path: str | None = None
         ) -> ScanResult:
    """Analyse many assets across processes.

    Falls back to in-process execution for small universes, where the cost of
    spawning interpreters exceeds the work being distributed.
    """
    symbols = [s for s in symbols]
    result = ScanResult(as_of=as_of)
    if not symbols:
        return result

    workers = workers or max(1, min(os.cpu_count() or 1,
                                    int(settings.get("pipeline.parallel_workers", 1))))
    started = time.monotonic()

    # Spawning costs ~0.3s per worker; below this the pool is a net loss.
    if workers <= 1 or len(symbols) < 60:
        _init_worker(settings.as_dict(), config_path)
        candidates, payloads, failures = _analyse_slice(symbols, as_of, model_version)
        result.candidates = candidates
        result.payloads = payloads
        result.failures = failures
        result.workers = 1
    else:
        size = chunk_size or max(10, math.ceil(len(symbols) / (workers * 4)))
        slices = [symbols[i:i + size] for i in range(0, len(symbols), size)]
        overrides = settings.as_dict()
        result.workers = workers
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(overrides, config_path)) as pool:
            futures = {pool.submit(_analyse_slice, chunk, as_of, model_version): chunk
                       for chunk in slices}
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    candidates, payloads, failures = future.result()
                except Exception as exc:
                    log.exception("scan worker died", assets=len(chunk))
                    result.failures.extend(
                        {"symbol": s, "error": f"worker died: {exc}"[:200]}
                        for s in chunk)
                    continue
                result.candidates.extend(candidates)
                result.payloads.update(payloads)
                result.failures.extend(failures)

    result.seconds = time.monotonic() - started
    result.analysed = len(result.candidates)
    log.info("universe scan complete", analysed=result.analysed,
             failed=len(result.failures), workers=result.workers,
             seconds=round(result.seconds, 2))
    return result
