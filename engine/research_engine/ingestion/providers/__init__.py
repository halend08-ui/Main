"""Concrete data providers.

Each module documents the upstream API, its licensing/free-tier status and the
fields it can and cannot supply. Adding a provider means implementing
:class:`~research_engine.ingestion.base.DataProvider` and registering it in
``build_registry`` -- no other code changes.
"""
from research_engine.ingestion.providers.csv_local import CsvLocalProvider  # noqa: F401
from research_engine.ingestion.providers.stooq import StooqProvider  # noqa: F401
from research_engine.ingestion.providers.coingecko import CoinGeckoProvider  # noqa: F401
from research_engine.ingestion.providers.sec_edgar import SecEdgarProvider  # noqa: F401
from research_engine.ingestion.providers.fred import FredProvider  # noqa: F401
from research_engine.ingestion.providers.rss_news import RssNewsProvider  # noqa: F401
