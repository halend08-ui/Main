"""Provider registry and failover chain.

Given a capability, the registry walks the configured provider chain in order:

1. skip providers that are disabled or missing credentials (recorded, not silent);
2. call the provider;
3. on failure, log it, mark provider health, and try the next one;
4. if every provider fails, raise :class:`DataUnavailable` -- never synthesise.

The chain and its order come from configuration, so providers can be swapped
without touching code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from research_engine.core.errors import DataUnavailable, ProviderError
from research_engine.core.logging import get_logger
from research_engine.ingestion.base import Capability, DataProvider, ProviderResult

log = get_logger(__name__)


@dataclass
class FetchAttempt:
    provider: str
    ok: bool
    error: str | None = None
    skipped_reason: str | None = None
    records: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "ok": self.ok, "error": self.error,
                "skipped_reason": self.skipped_reason, "records": self.records}


@dataclass
class FetchReport:
    """What happened across the whole chain -- surfaced in data-quality output."""

    capability: Capability
    target: str
    attempts: list[FetchAttempt] = field(default_factory=list)
    result: ProviderResult | None = None

    @property
    def succeeded(self) -> bool:
        return self.result is not None

    @property
    def failover_used(self) -> bool:
        return sum(1 for a in self.attempts if a.ok or a.error) > 1

    def as_dict(self) -> dict[str, Any]:
        return {"capability": self.capability.value, "target": self.target,
                "succeeded": self.succeeded,
                "provider": self.result.provider if self.result else None,
                "attempts": [a.as_dict() for a in self.attempts]}


class ProviderRegistry:
    """Holds provider instances and resolves capability -> ordered chain."""

    def __init__(self, chains: Mapping[str, Sequence[str]] | None = None,
                 *, health_hook: Callable[[str, bool], None] | None = None) -> None:
        self._providers: dict[str, DataProvider] = {}
        self._chains: dict[str, list[str]] = {k: list(v) for k, v in (chains or {}).items()}
        self._health_hook = health_hook

    # -- registration ------------------------------------------------------
    def register(self, provider: DataProvider) -> "ProviderRegistry":
        self._providers[provider.name] = provider
        return self

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def get(self, name: str) -> DataProvider | None:
        return self._providers.get(name)

    def names(self) -> list[str]:
        return sorted(self._providers)

    def set_chain(self, capability: Capability | str, chain: Sequence[str]) -> None:
        key = capability.value if isinstance(capability, Capability) else capability
        self._chains[key] = list(chain)

    def chain(self, capability: Capability) -> list[DataProvider]:
        """Configured chain, falling back to any provider declaring the capability."""
        configured = self._chains.get(capability.value)
        if configured:
            chain = [self._providers[name] for name in configured
                     if name in self._providers]
            missing = [n for n in configured if n not in self._providers]
            if missing:
                log.debug("configured providers not registered",
                          capability=capability.value, missing=",".join(missing))
            if chain:
                return chain
        return [p for p in self._providers.values() if p.supports(capability)]

    # -- fetching ----------------------------------------------------------
    def fetch(self, capability: Capability, method: str, *args: Any,
              target: str = "", **kwargs: Any) -> FetchReport:
        """Call ``method`` on each provider in the chain until one succeeds."""
        report = FetchReport(capability=capability, target=target or str(args[:1]))
        chain = self.chain(capability)
        if not chain:
            log.error("no providers configured", capability=capability.value)
            raise DataUnavailable(
                f"no provider registered for capability {capability.value}")

        for provider in chain:
            if not provider.supports(capability):
                report.attempts.append(FetchAttempt(
                    provider.name, ok=False,
                    skipped_reason=f"does not support {capability.value}"))
                continue
            reason = provider.unavailable_reason()
            if reason:
                report.attempts.append(FetchAttempt(provider.name, ok=False,
                                                    skipped_reason=reason))
                log.debug("skipping provider", provider=provider.name, reason=reason)
                continue
            try:
                result = getattr(provider, method)(*args, **kwargs)
            except NotImplementedError as exc:
                report.attempts.append(FetchAttempt(provider.name, ok=False,
                                                    skipped_reason=str(exc)))
                continue
            except (ProviderError, DataUnavailable) as exc:
                self._record_health(provider.name, False)
                report.attempts.append(FetchAttempt(provider.name, ok=False,
                                                    error=str(exc)[:300]))
                log.warning("provider failed, trying next in chain",
                            provider=provider.name, capability=capability.value,
                            target=target, error=str(exc)[:200])
                continue
            except Exception as exc:  # unexpected: log loudly, keep the chain alive
                self._record_health(provider.name, False)
                report.attempts.append(FetchAttempt(
                    provider.name, ok=False, error=f"unexpected: {exc}"[:300]))
                log.exception("unexpected provider error", provider=provider.name,
                              capability=capability.value)
                continue

            if result is None or result.empty:
                report.attempts.append(FetchAttempt(provider.name, ok=False,
                                                    error="empty result", records=0))
                continue
            self._record_health(provider.name, True)
            report.attempts.append(FetchAttempt(provider.name, ok=True,
                                                records=len(result)))
            report.result = result
            return report

        log.error("all providers failed", capability=capability.value, target=target,
                  attempts=len(report.attempts))
        return report

    def require(self, capability: Capability, method: str, *args: Any,
                target: str = "", **kwargs: Any) -> tuple[ProviderResult, FetchReport]:
        report = self.fetch(capability, method, *args, target=target, **kwargs)
        if report.result is None:
            detail = "; ".join(
                f"{a.provider}: {a.error or a.skipped_reason or 'no data'}"
                for a in report.attempts) or "no providers attempted"
            raise DataUnavailable(
                f"{capability.value} unavailable for {target or args}: {detail}")
        return report.result, report

    def _record_health(self, provider: str, ok: bool) -> None:
        if self._health_hook is not None:
            try:
                self._health_hook(provider, ok)
            except Exception:  # health tracking must never break ingestion
                log.debug("health hook failed", provider=provider)

    # -- diagnostics -------------------------------------------------------
    def stats(self) -> dict[str, dict[str, Any]]:
        return {name: p.stats.as_dict() for name, p in self._providers.items()}

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for name, provider in sorted(self._providers.items()):
            out.append({
                "name": name,
                "capabilities": sorted(c.value for c in provider.capabilities),
                "source_tier": provider.source_tier.value,
                "requires_key": provider.requires_key,
                "available": provider.available,
                "unavailable_reason": provider.unavailable_reason(),
                "base_url": provider.base_url,
                "documentation": provider.documentation,
            })
        return out
