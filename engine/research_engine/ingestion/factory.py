"""Build a :class:`ProviderRegistry` from configuration.

This is the only place that maps provider *names* in ``default.yaml`` to
classes, which is what makes providers swappable without code changes
elsewhere. Credentials are pulled from the environment through the settings'
:class:`SecretResolver`; a provider whose key is absent is registered but
reports itself unavailable, so the failover chain skips it and the reason is
visible in ``describe()`` rather than being a mystery.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from research_engine.config.settings import Settings
from research_engine.core.logging import get_logger
from research_engine.core.types import SourceTier
from research_engine.ingestion.base import DataProvider
from research_engine.ingestion.cache import ResponseCache
from research_engine.ingestion.http import Transport, build_transport
from research_engine.ingestion.ratelimit import RetryPolicy
from research_engine.ingestion.providers import (CoinGeckoProvider, CsvLocalProvider,
                                                 FredProvider, RssNewsProvider,
                                                 SecEdgarProvider, StooqProvider)
from research_engine.ingestion.registry import ProviderRegistry

log = get_logger(__name__)

ProviderBuilder = Callable[..., DataProvider]

BUILDERS: dict[str, ProviderBuilder] = {
    "csv_local": CsvLocalProvider,
    "stooq": StooqProvider,
    "coingecko": CoinGeckoProvider,
    "sec_edgar": SecEdgarProvider,
    "fred": FredProvider,
    "rss": RssNewsProvider,
}


def build_registry(settings: Settings, *, transport: Transport | None = None,
                   cache: ResponseCache | None = None,
                   health_hook: Callable[[str, bool], None] | None = None,
                   only: Mapping[str, Any] | None = None) -> ProviderRegistry:
    chains = {}
    providers_cfg = settings.get("ingestion.providers")
    if providers_cfg is not None:
        for capability, chain in providers_cfg.items():
            chains[capability] = list(chain or [])

    registry = ProviderRegistry(chains, health_hook=health_hook)
    shared_transport = transport or build_transport(
        "offline" if settings.get("app.offline", False) else "auto")
    shared_cache = cache if cache is not None else ResponseCache(settings.cache_dir)
    retry = RetryPolicy(
        max_retries=int(settings.get("ingestion.max_retries", 4)),
        base_seconds=float(settings.get("ingestion.backoff_base_seconds", 1.0)),
        max_seconds=float(settings.get("ingestion.backoff_max_seconds", 60.0)),
        jitter=float(settings.get("ingestion.backoff_jitter", 0.25)))
    timeout = float(settings.get("ingestion.request_timeout_seconds", 20))
    user_agent = str(settings.get("ingestion.user_agent", "research-engine/0.1"))
    ttls = settings.get("ingestion.cache_ttl_seconds")

    def ttl(name: str, default: float) -> float:
        return float(ttls.get(name, default)) if ttls is not None else default

    section = settings.get("providers")
    names = list(section.keys()) if section is not None else []
    for name in names:
        if only is not None and name not in only:
            continue
        cfg = settings.provider_config(name)
        if not bool(cfg.get("enabled", True)):
            log.info("provider disabled by configuration", provider=name)
            continue
        builder = BUILDERS.get(name)
        if builder is None:
            log.warning("no builder for configured provider", provider=name)
            continue

        api_key = settings.secrets.get(cfg.get("api_key_env"), purpose=f"provider {name}")
        common: dict[str, Any] = {
            "transport": shared_transport, "cache": shared_cache,
            "api_key": api_key, "retry": retry, "timeout": timeout,
            "user_agent": user_agent,
            "requests_per_minute": float(cfg.get("requests_per_minute", 30)),
        }
        try:
            provider = _construct(name, builder, cfg, common, settings, ttl)
        except Exception as exc:  # a broken provider must not kill the pipeline
            log.error("failed to build provider", provider=name, error=str(exc))
            continue

        tier = cfg.get("source_tier")
        if tier:
            try:
                provider.source_tier = SourceTier(str(tier))
            except ValueError:
                log.warning("unknown source_tier for provider", provider=name, tier=tier)
        registry.register(provider)
        log.debug("registered provider", provider=name,
                  available=provider.available,
                  reason=provider.unavailable_reason() or "")
    return registry


def _construct(name: str, builder: ProviderBuilder, cfg: Any,
               common: dict[str, Any], settings: Settings,
               ttl: Callable[[str, float], float]) -> DataProvider:
    if name == "csv_local":
        root = cfg.get("root", "local")
        return builder(settings.path(str(root)), **{k: v for k, v in common.items()
                                                    if k != "transport"})
    if name == "stooq":
        return builder(base_url=cfg.get("base_url"),
                       ttl_seconds=ttl("prices_eod", 43_200), **common)
    if name == "coingecko":
        return builder(base_url=cfg.get("base_url"),
                       ttl_market=ttl("crypto_market", 900),
                       ttl_history=ttl("prices_eod", 43_200), **common)
    if name == "sec_edgar":
        import os
        return builder(base_url=cfg.get("base_url"),
                       contact_email=os.environ.get("INGESTION_CONTACT_EMAIL"),
                       ttl_seconds=ttl("fundamentals", 86_400), **common)
    if name == "fred":
        return builder(base_url=cfg.get("base_url"),
                       ttl_seconds=ttl("macro", 43_200), **common)
    if name == "rss":
        feeds = cfg.get("feeds")
        feed_list = feeds.as_dict() if hasattr(feeds, "as_dict") else (feeds or [])
        if isinstance(feed_list, dict):
            feed_list = list(feed_list.values())
        return builder(feed_list, ttl_seconds=ttl("news", 1_800), **common)
    return builder(**common)
