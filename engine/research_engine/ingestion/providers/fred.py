"""FRED macroeconomic provider (Federal Reserve Bank of St. Louis).

Upstream: https://api.stlouisfed.org/fred -- free, requires a key
(``FRED_API_KEY``). Series are official statistics, so the source tier is
regulatory.

Point-in-time note: FRED revises series. The plain observations endpoint returns
the *current* vintage, which would leak future information into a historical
backtest. We therefore request ``realtime_start``/``realtime_end`` when an
as-of date is supplied, and record the release date on every observation.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from research_engine.core.errors import DataUnavailable
from research_engine.core.logging import get_logger
from research_engine.core.timeutil import to_date
from research_engine.core.types import DataQuality, SourceTier
from research_engine.ingestion.base import Capability, DataProvider, ProviderResult

log = get_logger(__name__)

#: Canonical macro series used by the macro engine.
DEFAULT_SERIES: dict[str, str] = {
    "CPIAUCSL": "cpi_all_urban",
    "PCEPILFE": "core_pce",
    "UNRATE": "unemployment_rate",
    "FEDFUNDS": "fed_funds_rate",
    "DGS2": "treasury_2y",
    "DGS10": "treasury_10y",
    "T10Y2Y": "yield_curve_10y_2y",
    "GDPC1": "real_gdp",
    "INDPRO": "industrial_production",
    "UMCSENT": "consumer_sentiment",
    "BAMLH0A0HYM2": "high_yield_spread",
    "DCOILWTICO": "wti_oil",
    "DTWEXBGS": "dollar_index",
    "M2SL": "money_supply_m2",
    "RSAFS": "retail_sales",
}


class FredProvider(DataProvider):
    name = "fred"
    capabilities = frozenset({Capability.MACRO})
    source_tier = SourceTier.REGULATORY_FILING
    default_quality = DataQuality.EXCELLENT
    requires_key = True
    base_url = "https://api.stlouisfed.org/fred"
    documentation = ("Official US macro series. Free API key required. "
                     "Supports vintage (point-in-time) queries via realtime_*.")

    def __init__(self, *, base_url: str | None = None,
                 ttl_seconds: float = 43_200, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = base_url or type(self).base_url
        self.ttl_seconds = ttl_seconds

    def fetch_macro(self, series_id: str, *, start: date | None = None,
                    as_of: date | None = None) -> ProviderResult:
        url = f"{self.base_url}/series/observations"
        params: dict[str, Any] = {
            "series_id": series_id, "api_key": self._api_key, "file_type": "json",
        }
        if start:
            params["observation_start"] = start.isoformat()
        if as_of:
            # Vintage as it stood on `as_of` -- prevents revised data leaking
            # backwards into historical evaluation.
            params["realtime_start"] = as_of.isoformat()
            params["realtime_end"] = as_of.isoformat()
        payload = self.request_json(
            url, params=params, cache_key=f"fred:{series_id}:{start}:{as_of}",
            ttl_seconds=self.ttl_seconds)
        observations = payload.get("observations") or []
        records = []
        for obs in observations:
            raw = obs.get("value")
            if raw in (None, ".", ""):
                continue          # FRED marks missing values with "."; keep the gap
            try:
                value = float(raw)
            except ValueError:
                continue
            records.append({
                "date": to_date(obs["date"]), "value": value,
                "release_date": to_date(obs.get("realtime_start", obs["date"])),
            })
        if not records:
            raise DataUnavailable(f"{self.name}: no observations for {series_id}")
        return self.result(Capability.MACRO, records, as_of=records[-1]["date"],
                           url=url, notes=(f"series={series_id}",))
