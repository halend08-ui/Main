"""HTTP transport abstraction.

The engine never calls ``requests`` directly. Everything goes through a
:class:`Transport`, which means:

* tests run fully offline against a recorded/fake transport;
* rate limiting, retries and caching live in one place;
* credentials are injected per-request and never logged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from research_engine.core.errors import ProviderError
from research_engine.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Response:
    status_code: int
    text: str
    headers: Mapping[str, str] = field(default_factory=dict)
    url: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError as exc:
            raise ProviderError("http", f"invalid JSON from {self.url}: {exc}",
                                retryable=False) from exc

    def retry_after(self) -> float | None:
        raw = self.headers.get("Retry-After") or self.headers.get("retry-after")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None


class Transport(Protocol):
    def get(self, url: str, *, params: Mapping[str, Any] | None = None,
            headers: Mapping[str, str] | None = None,
            timeout: float = 20.0) -> Response:
        ...


class RequestsTransport:
    """Real network transport. ``requests`` is imported lazily so the core
    package stays installable without it."""

    def __init__(self, session: Any | None = None) -> None:
        self._session = session

    def _get_session(self) -> Any:
        if self._session is None:
            try:
                import requests  # noqa: PLC0415 - optional dependency
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise ProviderError(
                    "http", "the 'requests' package is required for live ingestion "
                    "(pip install research-engine[providers])", retryable=False) from exc
            self._session = requests.Session()
        return self._session

    def get(self, url: str, *, params: Mapping[str, Any] | None = None,
            headers: Mapping[str, str] | None = None,
            timeout: float = 20.0) -> Response:
        session = self._get_session()
        try:
            resp = session.get(url, params=dict(params or {}),
                               headers=dict(headers or {}), timeout=timeout)
        except Exception as exc:  # network errors are retryable by definition
            raise ProviderError("http", f"request to {url} failed: {exc}",
                                retryable=True) from exc
        return Response(status_code=resp.status_code, text=resp.text,
                        headers=dict(resp.headers), url=str(resp.url))


class FakeTransport:
    """Deterministic transport for tests and offline development.

    Routes are matched by substring of the request URL, in insertion order.
    A route may be a static :class:`Response` or a callable taking
    ``(url, params)``.
    """

    def __init__(self, routes: Mapping[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = dict(routes or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def add(self, match: str, response: Any) -> "FakeTransport":
        self.routes[match] = response
        return self

    def add_json(self, match: str, payload: Any, status: int = 200) -> "FakeTransport":
        return self.add(match, Response(status, json.dumps(payload), {}, match))

    def add_text(self, match: str, text: str, status: int = 200) -> "FakeTransport":
        return self.add(match, Response(status, text, {}, match))

    def get(self, url: str, *, params: Mapping[str, Any] | None = None,
            headers: Mapping[str, str] | None = None,
            timeout: float = 20.0) -> Response:
        self.calls.append((url, dict(params or {})))
        for match, route in self.routes.items():
            if match in url:
                if callable(route):
                    return route(url, dict(params or {}))
                return route
        return Response(404, "no route", {}, url)


class OfflineTransport:
    """Refuses every request with a clear, non-retryable error.

    Used when the operator has disabled network access. It exists so that an
    offline run fails loudly and is recorded as *unavailable data*, rather than
    quietly producing analysis from nothing.
    """

    def get(self, url: str, *, params: Mapping[str, Any] | None = None,
            headers: Mapping[str, str] | None = None,
            timeout: float = 20.0) -> Response:
        raise ProviderError("offline",
                            f"network access is disabled; cannot fetch {url}",
                            retryable=False)


def build_transport(mode: str = "auto") -> Transport:
    if mode == "offline":
        return OfflineTransport()
    return RequestsTransport()
