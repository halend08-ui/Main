"""RSS/Atom news provider.

Deliberately generic: the operator configures feed URLs, each tagged with a
source tier, so the source hierarchy stays explicit and auditable. Company IR
feeds and regulatory feeds can be given a higher tier than general journalism.

This provider extracts headline, link, timestamp and summary only. It performs
no sentiment analysis -- that happens in the analysis layer, where the result
can be weighted by source tier and cross-checked against filings.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree

from research_engine.core.errors import DataUnavailable, ProviderError
from research_engine.core.logging import get_logger
from research_engine.core.types import DataQuality, SourceTier
from research_engine.ingestion.base import Capability, DataProvider, ProviderResult

log = get_logger(__name__)

_TAG = re.compile(r"<[^>]+>")



class RssNewsProvider(DataProvider):
    name = "rss"
    capabilities = frozenset({Capability.NEWS})
    source_tier = SourceTier.FINANCIAL_JOURNALISM
    default_quality = DataQuality.FAIR
    requires_key = False
    documentation = ("Operator-configured RSS/Atom feeds. Each feed declares its "
                     "own source tier so filings outrank commentary.")

    def __init__(self, feeds: Sequence[Mapping[str, Any]] | None = None, *,
                 ttl_seconds: float = 1_800, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # feed: {url, name, tier, symbol_template?}
        self.feeds = list(feeds or [])
        self.ttl_seconds = ttl_seconds

    @property
    def available(self) -> bool:
        return bool(self.feeds) and self.transport is not None

    def unavailable_reason(self) -> str | None:
        if not self.feeds:
            return f"{self.name}: no feeds configured"
        return super().unavailable_reason()

    def fetch_news(self, symbol: str | None = None, *, limit: int = 50) -> ProviderResult:
        items: list[dict[str, Any]] = []
        errors: list[str] = []
        for feed in self.feeds:
            url = str(feed.get("url", ""))
            if not url:
                continue
            if symbol and "{symbol}" in url:
                url = url.replace("{symbol}", symbol.upper())
            elif symbol and not feed.get("general", False):
                # A general feed cannot be filtered server-side; we still fetch
                # it and filter by headline mention below.
                pass
            try:
                text = self.request_text(url, cache_key=f"rss:{url}",
                                         ttl_seconds=self.ttl_seconds)
            except (ProviderError, DataUnavailable) as exc:
                errors.append(f"{feed.get('name', url)}: {exc}")
                continue
            tier = feed.get("tier")
            tier = SourceTier(tier) if isinstance(tier, str) else (
                tier or SourceTier.FINANCIAL_JOURNALISM)
            for entry in parse_feed(text):
                if symbol and not _mentions(entry["headline"], entry.get("summary"), symbol):
                    continue
                items.append({**entry, "source": feed.get("name", url),
                              "source_tier": tier, "symbol": symbol})
        if not items:
            raise DataUnavailable(
                f"{self.name}: no news items"
                + (f" ({'; '.join(errors)})" if errors else ""))
        items.sort(key=lambda i: i["published_at"], reverse=True)
        return self.result(Capability.NEWS, items[:limit],
                           notes=tuple(errors), partial=bool(errors))


def _mentions(headline: str, summary: str | None, symbol: str) -> bool:
    pattern = re.compile(rf"\b{re.escape(symbol)}\b", re.IGNORECASE)
    return bool(pattern.search(headline) or (summary and pattern.search(summary)))


def parse_feed(text: str) -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom into normalised entries. Never raises on one bad item."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ProviderError("rss", f"malformed feed: {exc}", retryable=False) from exc

    entries: list[dict[str, Any]] = []
    # RSS 2.0
    for item in root.iter("item"):
        published = _parse_time(_text(item, "pubDate") or _text(item, "date"))
        headline = _clean(_text(item, "title"))
        if not headline or published is None:
            continue
        entries.append({"headline": headline, "url": _text(item, "link"),
                        "summary": _clean(_text(item, "description")),
                        "published_at": published})
    # Atom
    ns = "{http://www.w3.org/2005/Atom}"
    for item in root.iter(f"{ns}entry"):
        published = _parse_time(_text(item, f"{ns}updated") or _text(item, f"{ns}published"))
        headline = _clean(_text(item, f"{ns}title"))
        if not headline or published is None:
            continue
        link_el = item.find(f"{ns}link")
        entries.append({
            "headline": headline,
            "url": link_el.get("href") if link_el is not None else None,
            "summary": _clean(_text(item, f"{ns}summary")),
            "published_at": published})
    return entries


def _text(element: Any, tag: str) -> str | None:
    found = element.find(tag)
    return found.text if found is not None and found.text else None


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return _TAG.sub("", text).strip()


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
