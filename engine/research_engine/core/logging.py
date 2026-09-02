"""Structured logging with mandatory secret redaction.

Every log record goes through :class:`SecretRedactingFilter`, which scrubs any
value registered as a secret plus anything matching common key patterns. This
is a security control, not a convenience: API keys must never reach disk.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any, Iterable

_SECRET_VALUES: set[str] = set()

# Patterns for key-like material appearing inline in a message.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(api[_-]?key|apikey|token|secret|password|passwd|authorization|bearer)"
               r"\s*[=:]\s*['\"]?([A-Za-z0-9_\-\.]{8,})['\"]?"),
    re.compile(r"(?i)([?&](?:api[_-]?key|token|apikey|key)=)([^&\s]+)"),
)

REDACTED = "***REDACTED***"


def register_secret(value: str | None) -> None:
    """Register a literal secret so it is scrubbed from all future log output."""
    if value and len(value) >= 6:
        _SECRET_VALUES.add(value)


def registered_secret_count() -> int:
    return len(_SECRET_VALUES)


def redact(text: str) -> str:
    """Remove registered secrets and key-like patterns from ``text``."""
    if not text:
        return text
    for secret in _SECRET_VALUES:
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}={REDACTED}"
                           if "=" not in m.group(1) else f"{m.group(1)}{REDACTED}", text)
    return text


class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: redact(str(v)) for k, v in record.args.items()}
                else:
                    record.args = tuple(redact(str(a)) for a in record.args)
            for key, value in list(getattr(record, "context", {}).items()):
                record.context[key] = redact(str(value))  # type: ignore[attr-defined]
        except Exception:  # logging must never break the pipeline
            pass
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line -- greppable and machine-readable."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context:
            payload["context"] = context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class HumanFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)-38s %(message)s",
                         datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        context = getattr(record, "context", None)
        if context:
            base += "  " + " ".join(f"{k}={v}" for k, v in context.items())
        return base


def configure_logging(level: str = "INFO", *, json_output: bool = False,
                      stream: Any = None, secrets: Iterable[str] = ()) -> None:
    """Install the root handler. Safe to call repeatedly (idempotent)."""
    for secret in secrets:
        register_secret(secret)

    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if json_output else HumanFormatter())
    handler.addFilter(SecretRedactingFilter())
    root.addHandler(handler)

    # Third-party chatter is not useful at INFO.
    for noisy in ("urllib3", "requests", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> "ContextLogger":
    return ContextLogger(logging.getLogger(name))


class ContextLogger:
    """Thin wrapper adding structured ``context`` to every call."""

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _log(self, level: int, msg: str, /, **context: Any) -> None:
        exc_info = context.pop("exc_info", None)
        self._logger.log(level, msg, extra={"context": context} if context else {},
                         exc_info=exc_info)

    def debug(self, msg: str, /, **c: Any) -> None:
        self._log(logging.DEBUG, msg, **c)

    def info(self, msg: str, /, **c: Any) -> None:
        self._log(logging.INFO, msg, **c)

    def warning(self, msg: str, /, **c: Any) -> None:
        self._log(logging.WARNING, msg, **c)

    def error(self, msg: str, /, **c: Any) -> None:
        self._log(logging.ERROR, msg, **c)

    def exception(self, msg: str, /, **c: Any) -> None:
        c["exc_info"] = True
        self._log(logging.ERROR, msg, **c)

    @property
    def raw(self) -> logging.Logger:
        return self._logger


def secrets_from_environ(prefix: str = "") -> list[str]:
    """Collect key-like environment values so they can be pre-registered."""
    out: list[str] = []
    for key, value in os.environ.items():
        if prefix and not key.startswith(prefix):
            continue
        if re.search(r"(?i)(key|token|secret|password)", key) and value:
            out.append(value)
    return out
