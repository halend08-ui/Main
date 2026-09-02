"""Exception hierarchy.

The distinction that matters most in this system is between *"we could not get
the data"* and *"the data we got is wrong"*.  Both must surface; neither may be
silently replaced with a fabricated value.
"""

from __future__ import annotations


class ResearchEngineError(Exception):
    """Base class for every error raised by this package."""


class ConfigError(ResearchEngineError):
    """Configuration is missing, malformed or internally inconsistent."""


class ProviderError(ResearchEngineError):
    """A data provider failed. Carries enough context to log and fail over."""

    def __init__(self, provider: str, message: str, *, retryable: bool = True,
                 status_code: int | None = None) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


class RateLimitError(ProviderError):
    """Provider signalled (or the local limiter predicted) a rate-limit breach."""

    def __init__(self, provider: str, message: str = "rate limited",
                 *, retry_after: float | None = None) -> None:
        super().__init__(provider, message, retryable=True, status_code=429)
        self.retry_after = retry_after


class AuthenticationError(ProviderError):
    """Missing or rejected credentials. Never retried, never logged with the key."""

    def __init__(self, provider: str, message: str = "authentication failed") -> None:
        super().__init__(provider, message, retryable=False, status_code=401)


class DataUnavailable(ResearchEngineError):
    """The requested data does not exist / could not be obtained.

    Callers must handle this by reporting "insufficient reliable data" rather
    than substituting a default value.
    """


class DataQualityError(ResearchEngineError):
    """Data was obtained but failed validation hard enough to be unusable."""


class LookAheadError(ResearchEngineError):
    """A computation attempted to read information dated after its as-of time."""


class StorageError(ResearchEngineError):
    """Database-level failure."""


class ModelError(ResearchEngineError):
    """A model could not produce a usable output."""


class InsufficientData(ModelError):
    """Not enough observations to compute a statistic responsibly."""

    def __init__(self, what: str, needed: int, got: int) -> None:
        super().__init__(f"{what}: need >= {needed} observations, got {got}")
        self.what = what
        self.needed = needed
        self.got = got
