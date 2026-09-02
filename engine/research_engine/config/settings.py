"""Configuration loading: defaults -> file -> environment.

Everything the operator might reasonably want to change lives in
``default.yaml``; nothing here hard-codes a threshold. Secrets are resolved
lazily through :class:`SecretResolver`, which reads environment variables only
and registers what it finds with the logging redactor.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from research_engine.core.errors import ConfigError
from research_engine.core.logging import register_secret

_DEFAULT_FILE = Path(__file__).with_name("default.yaml")
ENV_PREFIX = "RE__"


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], Mapping) and isinstance(value, Mapping):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _coerce(text: str) -> Any:
    """Interpret an env-var string as a scalar (int/float/bool/list/null).

    Numbers are parsed before YAML because YAML 1.1 does not recognise the
    common shell form ``1e9`` as a float.
    """
    raw = text.strip()
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return text


def _env_overrides(environ: Mapping[str, str]) -> dict[str, Any]:
    """``RE__SCORING__THRESHOLDS__STRONG=72`` -> nested dict."""
    out: dict[str, Any] = {}
    for key, raw in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = [part.lower() for part in key[len(ENV_PREFIX):].split("__") if part]
        if not path:
            continue
        cursor = out
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):
                raise ConfigError(f"conflicting environment override at {key}")
        cursor[path[-1]] = _coerce(raw)
    return out


class SecretResolver:
    """Reads secrets from the environment. Never from files, never from code."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else os.environ
        self._seen: set[str] = set()

    def get(self, env_var: str | None, *, required: bool = False,
            purpose: str = "") -> str | None:
        if not env_var:
            return None
        value = self._environ.get(env_var)
        if value:
            value = value.strip()
        if not value:
            if required:
                raise ConfigError(
                    f"missing required secret in environment variable {env_var}"
                    + (f" (needed for {purpose})" if purpose else "")
                )
            return None
        if env_var not in self._seen:
            register_secret(value)
            self._seen.add(env_var)
        return value

    def has(self, env_var: str | None) -> bool:
        return bool(env_var) and bool(self._environ.get(env_var or ""))


@dataclass(frozen=True)
class Section:
    """Read-only dotted access over a nested mapping."""

    _data: Mapping[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def get(self, path: str, default: Any = None) -> Any:
        cursor: Any = self._data
        for part in path.split("."):
            if isinstance(cursor, Mapping) and part in cursor:
                cursor = cursor[part]
            else:
                return default
        if isinstance(cursor, Mapping):
            return Section(cursor)
        return cursor

    def require(self, path: str) -> Any:
        sentinel = object()
        value = self.get(path, sentinel)
        if value is sentinel:
            raise ConfigError(f"missing required configuration key: {path}")
        return value

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data))

    def keys(self) -> Iterable[str]:
        return self._data.keys()

    def items(self) -> Iterable[tuple[str, Any]]:
        return self._data.items()

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Section({sorted(self._data)})"


@dataclass(frozen=True)
class Settings:
    """Whole-application configuration."""

    data: Mapping[str, Any]
    source_files: tuple[str, ...] = ()
    secrets: SecretResolver = field(default_factory=SecretResolver)

    # -- access ------------------------------------------------------------
    def section(self, name: str) -> Section:
        value = self.data.get(name, {})
        if not isinstance(value, Mapping):
            raise ConfigError(f"configuration section {name!r} is not a mapping")
        return Section(value)

    def get(self, path: str, default: Any = None) -> Any:
        return Section(self.data).get(path, default)

    def require(self, path: str) -> Any:
        return Section(self.data).require(path)

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.data))

    # -- derived paths -----------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return Path(str(self.get("app.data_dir", "./data"))).expanduser()

    def path(self, *parts: str) -> Path:
        """Resolve a path relative to ``data_dir`` unless it is absolute."""
        candidate = Path(*parts)
        return candidate if candidate.is_absolute() else self.data_dir / candidate

    @property
    def database_path(self) -> Path:
        return self.path(str(self.get("database.path", "research.db")))

    @property
    def cache_dir(self) -> Path:
        return self.path(str(self.get("ingestion.cache_dir", "cache")))

    @property
    def reports_dir(self) -> Path:
        return self.path(str(self.get("pipeline.report_dir", "reports")))

    @property
    def models_dir(self) -> Path:
        return self.path(str(self.get("learning.registry_dir", "models")))

    # -- convenience -------------------------------------------------------
    @property
    def allow_trading(self) -> bool:
        return bool(self.get("app.allow_trading", False))

    def risk_free_rate(self) -> float:
        env_var = self.get("analysis.risk_free_rate_env")
        raw = os.environ.get(str(env_var)) if env_var else None
        if raw:
            try:
                return float(raw)
            except ValueError as exc:
                raise ConfigError(f"{env_var} must be a decimal fraction") from exc
        return float(self.get("analysis.risk_free_rate_default", 0.04))

    def scoring_weights(self, asset_class: str = "equity") -> dict[str, float]:
        key = "scoring.crypto_weights" if asset_class == "crypto" else "scoring.weights"
        raw = self.get(key, {})
        weights = dict(raw.items()) if isinstance(raw, Section) else dict(raw or {})
        return normalise_weights({k: float(v) for k, v in weights.items()})

    def provider_config(self, name: str) -> Section:
        cfg = self.get(f"providers.{name}")
        if cfg is None:
            raise ConfigError(f"unknown provider {name!r}")
        return cfg if isinstance(cfg, Section) else Section(dict(cfg))

    def provider_chain(self, capability: str) -> list[str]:
        chain = self.get(f"ingestion.providers.{capability}", [])
        return list(chain or [])

    def with_overrides(self, overrides: Mapping[str, Any]) -> "Settings":
        """Return a copy with a nested mapping merged on top (tests, CLI flags)."""
        return Settings(_deep_merge(self.data, overrides), self.source_files, self.secrets)


def normalise_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Scale non-negative weights to sum to 1. Rejects degenerate input."""
    cleaned = {k: float(v) for k, v in weights.items() if v is not None}
    if any(v < 0 for v in cleaned.values()):
        raise ConfigError("scoring weights must be non-negative")
    total = sum(cleaned.values())
    if total <= 0:
        raise ConfigError("scoring weights must sum to a positive number")
    return {k: v / total for k, v in cleaned.items()}


def _validate(data: Mapping[str, Any]) -> None:
    """Fail fast on configurations that would silently corrupt research."""
    required_sections = ("app", "database", "universe", "ingestion", "quality",
                         "analysis", "scoring", "risk", "backtest", "learning")
    for name in required_sections:
        if name not in data:
            raise ConfigError(f"configuration is missing required section {name!r}")

    for key in ("scoring.weights", "scoring.crypto_weights"):
        section = Section(data).get(key)
        weights = dict(section.items()) if isinstance(section, Section) else {}
        normalise_weights({k: float(v) for k, v in weights.items()})

    thresholds = Section(data).get("scoring.thresholds")
    if isinstance(thresholds, Section):
        order = ["watch", "moderate", "strong", "exceptional"]
        values = [float(thresholds.get(k, 0)) for k in order]
        if values != sorted(values):
            raise ConfigError("scoring.thresholds must increase: "
                              "watch < moderate < strong < exceptional")

    bt = Section(data).get("backtest")
    if isinstance(bt, Section) and float(bt.get("embargo_days", 0)) < 0:
        raise ConfigError("backtest.embargo_days must be >= 0")

    if bool(Section(data).get("app.allow_trading", False)):
        raise ConfigError(
            "app.allow_trading must remain false: automated order execution is "
            "not implemented and is out of scope for this system"
        )


def default_settings(overrides: Mapping[str, Any] | None = None) -> Settings:
    """Defaults only -- the baseline used by tests."""
    data = yaml.safe_load(_DEFAULT_FILE.read_text(encoding="utf-8")) or {}
    if overrides:
        data = _deep_merge(data, overrides)
    _validate(data)
    return Settings(data, (str(_DEFAULT_FILE),))


def load_settings(config_path: str | Path | None = None, *,
                  environ: Mapping[str, str] | None = None,
                  overrides: Mapping[str, Any] | None = None) -> Settings:
    """Load defaults, then an optional YAML file, then environment overrides."""
    env = environ if environ is not None else os.environ
    data = yaml.safe_load(_DEFAULT_FILE.read_text(encoding="utf-8")) or {}
    sources = [str(_DEFAULT_FILE)]

    path = config_path or env.get("RESEARCH_ENGINE_CONFIG")
    if path:
        p = Path(path).expanduser()
        if not p.exists():
            raise ConfigError(f"configuration file not found: {p}")
        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, Mapping):
            raise ConfigError(f"configuration file must contain a mapping: {p}")
        data = _deep_merge(data, loaded)
        sources.append(str(p))

    env_over = _env_overrides(env)
    if env_over:
        data = _deep_merge(data, env_over)
        sources.append("environment")

    if overrides:
        data = _deep_merge(data, overrides)
        sources.append("runtime-overrides")

    _validate(data)
    return Settings(data, tuple(sources), SecretResolver(env))
