"""Client-side rate limiting and retry policy.

Providers are shared infrastructure that we do not own. The rules here are
deliberately conservative: stay under the published limit, back off
exponentially with jitter on failure, and give up rather than hammer.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass


class TokenBucket:
    """Classic token bucket. ``capacity`` tokens refill at ``rate`` per second."""

    def __init__(self, rate_per_minute: float, *, capacity: int | None = None,
                 clock=time.monotonic, sleeper=time.sleep) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self.rate = float(rate_per_minute) / 60.0
        self.capacity = float(capacity if capacity is not None
                              else max(1.0, rate_per_minute / 4.0))
        self._tokens = self.capacity
        self._clock = clock
        self._sleep = sleeper
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    def try_acquire(self, tokens: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def wait_time(self, tokens: float = 1.0) -> float:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                return 0.0
            return (tokens - self._tokens) / self.rate

    def acquire(self, tokens: float = 1.0, *, timeout: float | None = None) -> bool:
        """Block until tokens are available. Returns False on timeout."""
        deadline = None if timeout is None else self._clock() + timeout
        while True:
            wait = self.wait_time(tokens)
            if wait <= 0 and self.try_acquire(tokens):
                return True
            if deadline is not None and self._clock() + wait > deadline:
                return False
            self._sleep(min(max(wait, 0.001), 5.0))


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff with full jitter, capped."""

    max_retries: int = 4
    base_seconds: float = 1.0
    max_seconds: float = 60.0
    jitter: float = 0.25

    def delay(self, attempt: int, *, retry_after: float | None = None,
              rng: random.Random | None = None) -> float:
        """Delay before attempt ``attempt`` (1-based). Honours ``Retry-After``."""
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        if retry_after is not None and retry_after >= 0:
            return min(float(retry_after), self.max_seconds)
        raw = min(self.base_seconds * (2 ** (attempt - 1)), self.max_seconds)
        if self.jitter <= 0:
            return raw
        r = rng or random
        return max(0.0, raw * (1.0 + r.uniform(-self.jitter, self.jitter)))

    def should_retry(self, attempt: int) -> bool:
        return attempt <= self.max_retries
