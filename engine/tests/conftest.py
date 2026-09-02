"""Shared fixtures.

Every test runs fully offline: the only transport available in tests is
:class:`FakeTransport`, so a test that accidentally reaches the network fails
loudly instead of silently depending on a third party.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from pathlib import Path

import pytest

from research_engine.config.settings import default_settings
from research_engine.core.series import PriceSeries
from research_engine.ingestion.http import FakeTransport
from research_engine.storage.db import Database
from research_engine.storage.repositories import (AssetRepository, FundamentalRepository,
                                                  PriceRepository)


@pytest.fixture()
def settings(tmp_path: Path):
    return default_settings({"app": {"data_dir": str(tmp_path)}})


@pytest.fixture()
def db() -> Database:
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


@pytest.fixture()
def repos(db: Database):
    return {
        "assets": AssetRepository(db),
        "prices": PriceRepository(db),
        "fundamentals": FundamentalRepository(db),
    }


@pytest.fixture()
def transport() -> FakeTransport:
    return FakeTransport()


def make_prices(n: int = 500, *, start: dt.date = dt.date(2022, 1, 3),
                initial: float = 100.0, drift: float = 0.0003,
                vol: float = 0.012, seed: int = 7,
                weekdays_only: bool = True,
                volume: float = 1_000_000.0) -> list[dict]:
    """Deterministic synthetic OHLCV.

    Synthetic data is used ONLY to test computations. It never enters the
    research database in production paths -- see tests/test_no_fabrication.py.
    """
    rng = random.Random(seed)
    bars: list[dict] = []
    price = initial
    day = start
    while len(bars) < n:
        if not weekdays_only or day.weekday() < 5:
            shock = rng.gauss(drift, vol)
            price = max(0.01, price * math.exp(shock))
            high = price * (1 + abs(rng.gauss(0, vol / 2)))
            low = price * (1 - abs(rng.gauss(0, vol / 2)))
            bars.append({
                "date": day, "open": round(price * (1 + rng.gauss(0, vol / 4)), 4),
                "high": round(high, 4), "low": round(low, 4),
                "close": round(price, 4), "volume": round(volume * rng.uniform(0.6, 1.6)),
                "adj_close": round(price, 4),
            })
        day += dt.timedelta(days=1)
    return bars


@pytest.fixture()
def price_bars():
    return make_prices


@pytest.fixture()
def sample_series() -> PriceSeries:
    return PriceSeries.from_rows("TEST", make_prices(600))
