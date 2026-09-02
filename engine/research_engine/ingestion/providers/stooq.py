"""Stooq EOD price provider.

Upstream: https://stooq.com -- free, no API key, CSV download endpoint.
Coverage: US, Canadian (`.ca`), UK, German and several other venues, plus
indices and FX. Rate limits are undocumented, so we stay deliberately slow.

Limitations (documented rather than papered over):

* Stooq prices are split-adjusted but **not** dividend-adjusted, so
  ``adj_close`` is not supplied; total-return analytics must be told this.
* No fundamentals, no corporate-action feed.
* Symbols are suffixed by venue (``aapl.us``, ``shop.ca``).
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from research_engine.core.errors import DataUnavailable, ProviderError
from research_engine.core.logging import get_logger
from research_engine.core.timeutil import to_date
from research_engine.core.types import DataQuality, SourceTier
from research_engine.ingestion.base import Capability, DataProvider, ProviderResult

log = get_logger(__name__)

_SUFFIX_BY_EXCHANGE = {
    "NYSE": "us", "NASDAQ": "us", "AMEX": "us", "NYSEARCA": "us", "BATS": "us",
    "TSX": "ca", "TSXV": "ca", "LSE": "uk", "XETRA": "de",
}


class StooqProvider(DataProvider):
    name = "stooq"
    capabilities = frozenset({Capability.PRICES_EOD})
    source_tier = SourceTier.DATA_PROVIDER
    default_quality = DataQuality.GOOD
    requires_key = False
    base_url = "https://stooq.com"
    documentation = ("Free EOD CSV. Split-adjusted, NOT dividend-adjusted; "
                     "no fundamentals. Venue suffix required (aapl.us).")

    def __init__(self, *, base_url: str | None = None,
                 default_suffix: str = "us", ttl_seconds: float = 43_200,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = base_url or type(self).base_url
        self.default_suffix = default_suffix
        self.ttl_seconds = ttl_seconds

    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept": "text/csv"}

    def stooq_symbol(self, symbol: str, exchange: str | None = None) -> str:
        s = symbol.strip().lower().replace(".", "-")
        if "." in symbol and symbol.rsplit(".", 1)[-1].lower() in {"us", "ca", "uk", "de"}:
            return symbol.strip().lower()
        suffix = _SUFFIX_BY_EXCHANGE.get((exchange or "").upper(), self.default_suffix)
        return f"{s}.{suffix}"

    def fetch_prices(self, symbol: str, *, start: date | None = None,
                     end: date | None = None, exchange: str | None = None
                     ) -> ProviderResult:
        code = self.stooq_symbol(symbol, exchange)
        url = f"{self.base_url}/q/d/l/"
        params: dict[str, Any] = {"s": code, "i": "d"}
        if start:
            params["d1"] = start.strftime("%Y%m%d")
        if end:
            params["d2"] = end.strftime("%Y%m%d")

        text = self.request_text(url, params=params,
                                 cache_key=f"prices:{code}:{start}:{end}",
                                 ttl_seconds=self.ttl_seconds)
        if not text or text.strip().lower().startswith("no data"):
            raise DataUnavailable(f"{self.name}: no data for {code}")

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "Close" not in reader.fieldnames:
            raise ProviderError(self.name, f"unexpected CSV header for {code}: "
                                           f"{reader.fieldnames}", retryable=False)
        bars: list[dict[str, Any]] = []
        for row in reader:
            close = _f(row.get("Close"))
            day = row.get("Date")
            if close is None or not day:
                continue
            bars.append({
                "date": to_date(day), "open": _f(row.get("Open")),
                "high": _f(row.get("High")), "low": _f(row.get("Low")),
                "close": close, "volume": _f(row.get("Volume")),
                # No dividend adjustment upstream: leave adj_close unset rather
                # than pretending the raw close is total-return adjusted.
                "adj_close": None,
            })
        if not bars:
            raise DataUnavailable(f"{self.name}: empty series for {code}")
        return self.result(
            Capability.PRICES_EOD, bars, as_of=bars[-1]["date"], url=url,
            missing=("adj_close", "dividend", "split_factor"), partial=True,
            notes=("stooq closes are split-adjusted but not dividend-adjusted",))


def _f(value: Any) -> float | None:
    if value is None or str(value).strip() in ("", "-", "N/A"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None
