"""Provider framework: capabilities, results with provenance, and the retry /
cache / rate-limit machinery every provider inherits.

A provider is a thin adapter that turns one external API into normalised
records. Providers must:

* declare their capabilities and source tier;
* never invent a value -- missing data is reported as missing;
* attach provenance to everything they return.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.errors import (AuthenticationError, DataUnavailable,
                                         ProviderError, RateLimitError)
from research_engine.core.logging import get_logger
from research_engine.core.timeutil import utcnow
from research_engine.core.types import DataQuality, Provenance, SourceTier
from research_engine.ingestion.cache import ResponseCache
from research_engine.ingestion.http import Response, Transport
from research_engine.ingestion.ratelimit import RetryPolicy, TokenBucket

log = get_logger(__name__)


class Capability(str, enum.Enum):
    PRICES_EOD = "prices_eod"
    PRICES_INTRADAY = "prices_intraday"
    FUNDAMENTALS = "fundamentals"
    CORPORATE_ACTIONS = "corporate_actions"
    CRYPTO_MARKET = "crypto_market"
    CRYPTO_HISTORY = "crypto_history"
    CRYPTO_ONCHAIN = "crypto_onchain"
    NEWS = "news"
    MACRO = "macro"
    UNIVERSE_EQUITY = "universe_equity"
    UNIVERSE_CRYPTO = "universe_crypto"
    OWNERSHIP = "ownership"
    SHORT_INTEREST = "short_interest"


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Records plus the provenance and any explicitly-missing fields.

    ``missing`` is part of the contract: a provider that cannot supply a field
    says so, and downstream code degrades confidence instead of assuming zero.
    """

    provider: str
    capability: Capability
    records: tuple[Mapping[str, Any], ...]
    provenance: Provenance
    missing: tuple[str, ...] = ()
    partial: bool = False
    notes: tuple[str, ...] = ()
    from_cache: bool = False

    def __len__(self) -> int:
        return len(self.records)

    @property
    def empty(self) -> bool:
        return not self.records


@dataclass
class ProviderStats:
    requests: int = 0
    cache_hits: int = 0
    failures: int = 0
    rate_limited: int = 0
    retries: int = 0
    last_error: str | None = None
    last_success_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests, "cache_hits": self.cache_hits,
            "failures": self.failures, "rate_limited": self.rate_limited,
            "retries": self.retries, "last_error": self.last_error,
            "last_success_at": self.last_success_at.isoformat()
            if self.last_success_at else None,
        }


class DataProvider:
    """Base class. Subclasses implement ``fetch_*`` per declared capability."""

    name: str = "provider"
    capabilities: frozenset[Capability] = frozenset()
    source_tier: SourceTier = SourceTier.DATA_PROVIDER
    default_quality: DataQuality = DataQuality.GOOD
    requires_key: bool = False
    base_url: str = ""
    documentation: str = ""

    def __init__(self, *, transport: Transport | None = None,
                 cache: ResponseCache | None = None,
                 api_key: str | None = None,
                 requests_per_minute: float = 30.0,
                 retry: RetryPolicy | None = None,
                 timeout: float = 20.0,
                 user_agent: str = "research-engine/0.1",
                 sleeper=time.sleep) -> None:
        self.transport = transport
        self.cache = cache
        self._api_key = api_key
        self.bucket = TokenBucket(requests_per_minute)
        self.retry = retry or RetryPolicy()
        self.timeout = timeout
        self.user_agent = user_agent
        self.stats = ProviderStats()
        self._sleep = sleeper

    # -- introspection -----------------------------------------------------
    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @property
    def available(self) -> bool:
        """False when a required credential is missing -- checked before use so
        the failover chain can skip this provider cleanly."""
        if self.requires_key and not self._api_key:
            return False
        return self.transport is not None

    def unavailable_reason(self) -> str | None:
        if self.requires_key and not self._api_key:
            return f"{self.name}: API key not configured"
        if self.transport is None:
            return f"{self.name}: no transport configured"
        return None

    # -- request plumbing --------------------------------------------------
    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept": "application/json"}

    def request_json(self, url: str, *, params: Mapping[str, Any] | None = None,
                     cache_key: str | None = None, ttl_seconds: float = 0.0,
                     headers: Mapping[str, str] | None = None) -> Any:
        text, _ = self._request(url, params=params, cache_key=cache_key,
                                ttl_seconds=ttl_seconds, headers=headers)
        try:
            import json
            return json.loads(text)
        except ValueError as exc:
            raise ProviderError(self.name, f"invalid JSON from {url}: {exc}",
                                retryable=False) from exc

    def request_text(self, url: str, *, params: Mapping[str, Any] | None = None,
                     cache_key: str | None = None, ttl_seconds: float = 0.0,
                     headers: Mapping[str, str] | None = None) -> str:
        text, _ = self._request(url, params=params, cache_key=cache_key,
                                ttl_seconds=ttl_seconds, headers=headers)
        return text

    def _request(self, url: str, *, params: Mapping[str, Any] | None,
                 cache_key: str | None, ttl_seconds: float,
                 headers: Mapping[str, str] | None) -> tuple[str, bool]:
        """Cache -> rate limit -> request -> classify -> retry with backoff."""
        key = cache_key or f"{url}?{sorted((params or {}).items())}"
        if self.cache is not None and ttl_seconds > 0:
            hit = self.cache.get(self.name, key)
            if hit is not None:
                self.stats.cache_hits += 1
                return str(hit.payload), True

        if self.transport is None:
            raise ProviderError(self.name, "no transport configured", retryable=False)

        merged = {**self.headers(), **dict(headers or {})}
        attempt = 0
        last_error: Exception | None = None
        while True:
            attempt += 1
            self.bucket.acquire(timeout=60.0)
            self.stats.requests += 1
            try:
                resp = self.transport.get(url, params=params, headers=merged,
                                          timeout=self.timeout)
                self._raise_for_status(resp, url)
                if self.cache is not None and ttl_seconds > 0:
                    self.cache.set(self.name, key, resp.text, ttl_seconds=ttl_seconds,
                                   meta={"url": url})
                self.stats.last_success_at = utcnow()
                return resp.text, False
            except AuthenticationError:
                self.stats.failures += 1
                raise
            except ProviderError as exc:
                last_error = exc
                self.stats.last_error = str(exc)
                if isinstance(exc, RateLimitError):
                    self.stats.rate_limited += 1
                if not exc.retryable or not self.retry.should_retry(attempt):
                    self.stats.failures += 1
                    break
                retry_after = getattr(exc, "retry_after", None)
                delay = self.retry.delay(attempt, retry_after=retry_after)
                self.stats.retries += 1
                log.warning("provider request failed; backing off",
                            provider=self.name, attempt=attempt,
                            delay_s=round(delay, 2), error=str(exc)[:200])
                self._sleep(delay)

        # Exhausted retries. Serve a stale cache entry if one exists, clearly
        # marked -- stale-but-labelled beats nothing, and beats fabrication.
        if self.cache is not None:
            stale = self.cache.get(self.name, key, allow_stale=True)
            if stale is not None:
                log.warning("serving stale cache after provider failure",
                            provider=self.name, age_s=round(stale.age_seconds))
                return str(stale.payload), True
        assert last_error is not None
        raise last_error

    def _raise_for_status(self, resp: Response, url: str) -> None:
        if resp.ok:
            return
        if resp.status_code in (401, 403):
            raise AuthenticationError(self.name,
                                      f"{resp.status_code} for {url}")
        if resp.status_code == 404:
            raise DataUnavailable(f"{self.name}: not found: {url}")
        if resp.status_code == 429:
            raise RateLimitError(self.name, f"429 for {url}",
                                 retry_after=resp.retry_after())
        if 500 <= resp.status_code < 600:
            raise ProviderError(self.name, f"{resp.status_code} for {url}",
                                retryable=True, status_code=resp.status_code)
        raise ProviderError(self.name, f"{resp.status_code} for {url}",
                            retryable=False, status_code=resp.status_code)

    # -- provenance --------------------------------------------------------
    def provenance(self, *, as_of: datetime | date | None = None,
                   url: str | None = None,
                   quality: DataQuality | None = None,
                   ttl_seconds: float | None = None,
                   notes: str | None = None) -> Provenance:
        now = utcnow()
        moment = as_of if isinstance(as_of, datetime) else (
            datetime.combine(as_of, datetime.min.time()).replace(tzinfo=now.tzinfo)
            if as_of else now)
        expires = None
        if ttl_seconds:
            from datetime import timedelta
            expires = now + timedelta(seconds=float(ttl_seconds))
        return Provenance(source=self.name, source_tier=self.source_tier,
                          as_of=moment, retrieved_at=now, url=url,
                          quality=quality or self.default_quality,
                          expires_at=expires, notes=notes)

    def result(self, capability: Capability, records: Sequence[Mapping[str, Any]], *,
               as_of: datetime | date | None = None, url: str | None = None,
               quality: DataQuality | None = None,
               missing: Iterable[str] = (), partial: bool = False,
               notes: Iterable[str] = (), from_cache: bool = False) -> ProviderResult:
        return ProviderResult(
            provider=self.name, capability=capability, records=tuple(records),
            provenance=self.provenance(as_of=as_of, url=url, quality=quality),
            missing=tuple(missing), partial=partial, notes=tuple(notes),
            from_cache=from_cache)

    # -- capability entry points (override as supported) -------------------
    def fetch_prices(self, symbol: str, *, start: date | None = None,
                     end: date | None = None) -> ProviderResult:
        raise NotImplementedError(f"{self.name} does not provide EOD prices")

    def fetch_fundamentals(self, symbol: str, *,
                           identifiers: Mapping[str, Any] | None = None) -> ProviderResult:
        raise NotImplementedError(f"{self.name} does not provide fundamentals")

    def fetch_crypto_market(self, symbols: Sequence[str] | None = None) -> ProviderResult:
        raise NotImplementedError(f"{self.name} does not provide crypto market data")

    def fetch_crypto_history(self, coin_id: str, *, days: int = 365) -> ProviderResult:
        raise NotImplementedError(f"{self.name} does not provide crypto history")

    def fetch_news(self, symbol: str | None = None, *,
                   limit: int = 50) -> ProviderResult:
        raise NotImplementedError(f"{self.name} does not provide news")

    def fetch_macro(self, series_id: str, *,
                    start: date | None = None) -> ProviderResult:
        raise NotImplementedError(f"{self.name} does not provide macro series")

    def fetch_universe(self) -> ProviderResult:
        raise NotImplementedError(f"{self.name} does not provide a universe")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{type(self).__name__} name={self.name} caps={sorted(c.value for c in self.capabilities)}>"
