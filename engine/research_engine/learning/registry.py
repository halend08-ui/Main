"""Model versioning and reproducibility.

Rules:

* A model version is immutable once results have been attributed to it.
* Every version records its parameters, features, training window, data sources
  and a fingerprint of the code that produced it.
* Promotion never deletes the previous version; it retires it, so the question
  "which model produced this 2024 recommendation?" always has an answer.
* Any change to scoring weights, thresholds or calibration produces a NEW
  version. Silent parameter edits are the thing this module exists to prevent.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from research_engine.core.logging import get_logger
from research_engine.core.timeutil import iso, utcnow

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Everything needed to reproduce a model's behaviour."""

    family: str
    version: str
    parameters: Mapping[str, Any]
    features: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    train_start: date | None = None
    train_end: date | None = None
    notes: str = ""

    def fingerprint(self) -> str:
        """Stable hash of the parameters and feature set."""
        payload = json.dumps({"family": self.family,
                              "parameters": _normalise(self.parameters),
                              "features": sorted(self.features)},
                             sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {"family": self.family, "version": self.version,
                "parameters": dict(self.parameters), "features": list(self.features),
                "data_sources": list(self.data_sources),
                "train_start": self.train_start.isoformat() if self.train_start else None,
                "train_end": self.train_end.isoformat() if self.train_end else None,
                "fingerprint": self.fingerprint(), "notes": self.notes}


def _normalise(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _normalise(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, float):
        return round(value, 8)
    return value


def code_fingerprint(*objects: Any) -> str:
    """Hash the source of the functions/classes that implement a model.

    Combined with the parameter fingerprint this answers "would this version
    behave identically today?" -- if the code changed, the answer is no, and the
    fingerprint says so instead of the system pretending otherwise.
    """
    parts: list[str] = []
    for obj in objects:
        try:
            parts.append(inspect.getsource(obj))
        except (TypeError, OSError):
            parts.append(repr(obj))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def next_version(existing: Iterable[str], family: str) -> str:
    """``scoring_v1`` -> ``scoring_v2``. Versions are never reused."""
    highest = 0
    prefix = f"{family}_v"
    for version in existing:
        if version.startswith(prefix):
            try:
                highest = max(highest, int(version[len(prefix):].split("_")[0]))
            except ValueError:
                continue
    return f"{prefix}{highest + 1}"


class ModelRegistry:
    """Thin service over the model repositories, adding promotion policy."""

    def __init__(self, repository: Any, *, artifacts_dir: str | Path | None = None) -> None:
        self.repo = repository
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        if self.artifacts_dir:
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def register(self, spec: ModelSpec, *, code_hash: str | None = None,
                 validation_metrics: Mapping[str, Any] | None = None,
                 parent_version: str | None = None,
                 status: str = "candidate") -> str:
        self.repo.register(
            spec.version, family=spec.family, parameters=dict(spec.parameters),
            features=list(spec.features), train_start=spec.train_start,
            train_end=spec.train_end, validation_metrics=dict(validation_metrics or {}),
            data_sources=list(spec.data_sources),
            code_fingerprint=code_hash or spec.fingerprint(),
            parent_version=parent_version, status=status, notes=spec.notes)
        if self.artifacts_dir:
            path = self.artifacts_dir / f"{spec.version}.json"
            path.write_text(json.dumps(spec.to_dict(), indent=2), encoding="utf-8")
        log.info("model registered", version=spec.version, family=spec.family,
                 fingerprint=spec.fingerprint(), status=status)
        return spec.version

    def active(self, family: str) -> dict[str, Any] | None:
        return self.repo.active(family)

    def active_version(self, family: str, default: str | None = None) -> str | None:
        model = self.repo.active(family)
        return model["version"] if model else default

    def promote(self, version: str, *, test_metrics: Mapping[str, Any] | None = None,
                reason: str = "") -> None:
        if test_metrics:
            self.repo.register(version, family=self.repo.get(version)["family"],
                               parameters=self.repo.get(version)["parameters"],
                               test_metrics=dict(test_metrics), status="candidate")
        self.repo.promote(version)
        log.info("model promoted", version=version, reason=reason)

    def history(self, family: str | None = None) -> list[dict[str, Any]]:
        return self.repo.list(family)

    def reproducibility_report(self, version: str,
                               current_code_hash: str | None = None) -> dict[str, Any]:
        """Can this version's output be reproduced with today's code?"""
        model = self.repo.get(version)
        if not model:
            return {"version": version, "known": False,
                    "note": "no such model version"}
        stored_hash = model.get("code_fingerprint")
        reproducible = (current_code_hash is None or stored_hash == current_code_hash)
        return {
            "version": version, "known": True, "family": model.get("family"),
            "status": model.get("status"), "created_at": model.get("created_at"),
            "parameters": model.get("parameters"),
            "train_window": [model.get("train_start"), model.get("train_end")],
            "stored_code_fingerprint": stored_hash,
            "current_code_fingerprint": current_code_hash,
            "reproducible": reproducible,
            "note": ("parameters and code match: output is reproducible"
                     if reproducible else
                     "the implementing code has changed since this version ran; "
                     "historical output cannot be reproduced exactly and must be "
                     "read as a record, not re-derived"),
        }
