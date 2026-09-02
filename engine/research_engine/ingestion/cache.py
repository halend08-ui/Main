"""On-disk response cache.

Purpose is twofold: respect provider rate limits, and make research
reproducible -- a cached payload can be replayed to reconstruct exactly what a
past run saw. Entries are content-addressed by (provider, key) and carry an
explicit TTL plus the time they were fetched.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_engine.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CacheEntry:
    key: str
    provider: str
    fetched_at: float
    ttl_seconds: float
    payload: Any
    meta: dict[str, Any]

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.fetched_at)

    def is_fresh(self, now: float | None = None) -> bool:
        if self.ttl_seconds <= 0:
            return False
        return ((now if now is not None else time.time()) - self.fetched_at) < self.ttl_seconds


class ResponseCache:
    """JSON file cache. Small, dependency-free, easy to inspect and to purge."""

    def __init__(self, root: str | Path, *, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self.stores = 0

    def _path(self, provider: str, key: str) -> Path:
        digest = hashlib.sha256(f"{provider}|{key}".encode("utf-8")).hexdigest()
        return self.root / provider / digest[:2] / f"{digest}.json"

    def get(self, provider: str, key: str, *,
            allow_stale: bool = False) -> CacheEntry | None:
        if not self.enabled:
            return None
        path = self._path(provider, key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            self.misses += 1
            return None
        entry = CacheEntry(key=raw.get("key", key), provider=provider,
                           fetched_at=float(raw.get("fetched_at", 0.0)),
                           ttl_seconds=float(raw.get("ttl_seconds", 0.0)),
                           payload=raw.get("payload"), meta=raw.get("meta", {}))
        if entry.is_fresh() or allow_stale:
            self.hits += 1
            return entry
        self.misses += 1
        return None

    def set(self, provider: str, key: str, payload: Any, *, ttl_seconds: float,
            meta: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        path = self._path(provider, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"key": key, "provider": provider, "fetched_at": time.time(),
                  "ttl_seconds": float(ttl_seconds), "payload": payload,
                  "meta": meta or {}}
        # Atomic write: a truncated cache file must never look like valid data.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(record, fh)
            os.replace(tmp, path)
            self.stores += 1
        except OSError:
            log.warning("cache write failed", provider=provider, key=key[:60])
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def purge(self, provider: str | None = None) -> int:
        target = self.root / provider if provider else self.root
        removed = 0
        if not target.exists():
            return 0
        for path in target.rglob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "stores": self.stores}
