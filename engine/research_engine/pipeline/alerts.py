"""Alerting.

Alerts exist to surface things a person would want to know today. The design
constraint is *not* completeness but signal: an alert stream nobody reads is
worse than none, so every rule has a configurable threshold and repeated
identical alerts are suppressed.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.logging import get_logger
from research_engine.core.numeric import is_finite
from research_engine.core.timeutil import iso, utcnow
from research_engine.core.types import Recommendation, RiskLevel

log = get_logger(__name__)


class Severity(str, enum.Enum):
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Alert:
    kind: str
    severity: Severity
    title: str
    detail: str
    symbol: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    def key(self) -> str:
        """Dedupe key: same kind, same asset, same day."""
        return f"{self.kind}|{self.symbol or ''}|{self.created_at.date()}"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "severity": self.severity.value,
                "title": self.title, "detail": self.detail, "symbol": self.symbol,
                "payload": dict(self.payload), "created_at": iso(self.created_at)}


class AlertRules:
    """Threshold-driven rule set. All thresholds come from configuration."""

    def __init__(self, thresholds: Mapping[str, Any] | None = None) -> None:
        defaults = {
            "recommendation_change": True,
            "score_change_abs": 8.0,
            "price_move_abs_1d": 0.10,
            "crypto_price_move_abs_1d": 0.18,
            "drawdown_from_entry": 0.20,
            "valuation_target_reached": True,
            "thesis_invalidation": True,
            "risk_increase_levels": 1,
            "token_unlock_days_ahead": 14,
            "data_quality_degraded": True,
            "new_high_conviction": True,
        }
        self.thresholds = {**defaults, **dict(thresholds or {})}

    # -- individual rules --------------------------------------------------
    def recommendation_change(self, symbol: str, previous: str | None,
                              current: str) -> Alert | None:
        if not self.thresholds.get("recommendation_change") or not previous:
            return None
        if previous == current:
            return None
        downgrade = _rank(current) < _rank(previous)
        return Alert(
            kind="recommendation_change",
            severity=Severity.WARNING if downgrade else Severity.NOTICE,
            title=f"{symbol}: {previous} -> {current}",
            detail=f"the recommendation changed from {previous} to {current}",
            symbol=symbol, payload={"previous": previous, "current": current})

    def score_change(self, symbol: str, previous: float | None,
                     current: float | None) -> Alert | None:
        threshold = float(self.thresholds.get("score_change_abs", 8))
        if previous is None or current is None:
            return None
        delta = current - previous
        if abs(delta) < threshold:
            return None
        return Alert(
            kind="score_change",
            severity=Severity.NOTICE if delta > 0 else Severity.WARNING,
            title=f"{symbol}: score moved {delta:+.0f} to {current:.0f}",
            detail=f"the composite score moved {delta:+.1f} points since the last run",
            symbol=symbol, payload={"previous": previous, "current": current})

    def price_move(self, symbol: str, move: float | None, *,
                   is_crypto: bool = False) -> Alert | None:
        key = "crypto_price_move_abs_1d" if is_crypto else "price_move_abs_1d"
        threshold = float(self.thresholds.get(key, 0.1))
        if move is None or abs(move) < threshold:
            return None
        return Alert(
            kind="price_move",
            severity=Severity.WARNING if move < 0 else Severity.NOTICE,
            title=f"{symbol}: {move:+.1%} in one session",
            detail=f"a single-session move of {move:+.1%} exceeds the "
                   f"{threshold:.0%} alert threshold; check for news before acting",
            symbol=symbol, payload={"move": move})

    def position_drawdown(self, symbol: str, entry: float | None,
                          current: float | None) -> Alert | None:
        threshold = float(self.thresholds.get("drawdown_from_entry", 0.2))
        if not entry or not current or entry <= 0:
            return None
        drawdown = current / entry - 1.0
        if drawdown > -threshold:
            return None
        return Alert(
            kind="position_drawdown", severity=Severity.WARNING,
            title=f"{symbol}: down {abs(drawdown):.0%} from entry",
            detail=f"the position is {abs(drawdown):.0%} below its entry price; "
                   f"the thesis should be re-reviewed, which is not the same as "
                   f"selling",
            symbol=symbol, payload={"entry": entry, "current": current,
                                    "drawdown": drawdown})

    def valuation_target(self, symbol: str, price: float | None,
                         fair_value_base: float | None) -> Alert | None:
        if not self.thresholds.get("valuation_target_reached"):
            return None
        if not price or not fair_value_base or fair_value_base <= 0:
            return None
        if price < fair_value_base:
            return None
        return Alert(
            kind="valuation_target", severity=Severity.NOTICE,
            title=f"{symbol}: price reached the base-case fair value",
            detail=f"price {price:,.2f} is at or above the base-case fair value "
                   f"{fair_value_base:,.2f}; expected return from here is reduced",
            symbol=symbol, payload={"price": price, "fair_value": fair_value_base})

    def thesis_invalidation(self, symbol: str,
                            breached: Sequence[Mapping[str, Any]]) -> Alert | None:
        if not self.thresholds.get("thesis_invalidation") or not breached:
            return None
        return Alert(
            kind="thesis_invalidation", severity=Severity.CRITICAL,
            title=f"{symbol}: {len(breached)} exit condition(s) breached",
            detail="; ".join(str(c.get("description")) for c in breached[:3]),
            symbol=symbol, payload={"breached": list(breached)})

    def risk_increase(self, symbol: str, previous: str | None,
                      current: str | None) -> Alert | None:
        levels = int(self.thresholds.get("risk_increase_levels", 1))
        if not previous or not current:
            return None
        order = [r.value for r in RiskLevel]
        try:
            jump = order.index(current) - order.index(previous)
        except ValueError:
            return None
        if jump < levels:
            return None
        return Alert(
            kind="risk_increase", severity=Severity.WARNING,
            title=f"{symbol}: risk raised {previous} -> {current}",
            detail=f"the assessed risk level rose by {jump} level(s)",
            symbol=symbol, payload={"previous": previous, "current": current})

    def token_unlock(self, symbol: str, unlock_date: date | None,
                     pct_of_supply: float | None, *, as_of: date) -> Alert | None:
        days_ahead = int(self.thresholds.get("token_unlock_days_ahead", 14))
        if not unlock_date:
            return None
        days = (unlock_date - as_of).days
        if not 0 <= days <= days_ahead:
            return None
        share = f"{pct_of_supply:.1%} of supply" if pct_of_supply else "an unlock"
        return Alert(
            kind="token_unlock", severity=Severity.WARNING,
            title=f"{symbol}: {share} unlocks in {days} days",
            detail=f"scheduled supply increase on {unlock_date.isoformat()}",
            symbol=symbol, payload={"unlock_date": unlock_date.isoformat(),
                                    "pct_of_supply": pct_of_supply})

    def data_quality_degraded(self, symbol: str, previous: str | None,
                              current: str | None) -> Alert | None:
        if not self.thresholds.get("data_quality_degraded"):
            return None
        order = ["insufficient", "poor", "fair", "good", "excellent"]
        if not previous or not current or previous not in order or current not in order:
            return None
        if order.index(current) >= order.index(previous):
            return None
        return Alert(
            kind="data_quality", severity=Severity.WARNING,
            title=f"{symbol}: data quality fell from {previous} to {current}",
            detail="analysis confidence has been reduced accordingly; check "
                   "provider health before trusting today's output for this asset",
            symbol=symbol, payload={"previous": previous, "current": current})

    def high_conviction(self, symbol: str, recommendation: str, score: float | None,
                        confidence: float | None) -> Alert | None:
        if not self.thresholds.get("new_high_conviction"):
            return None
        if recommendation != Recommendation.BUY.value:
            return None
        if score is None or confidence is None or score < 80 or confidence < 0.6:
            return None
        return Alert(
            kind="high_conviction", severity=Severity.NOTICE,
            title=f"{symbol}: high-conviction BUY ({score:.0f}/100, "
                  f"{confidence:.0%} confidence)",
            detail="a new opportunity cleared every gate at high conviction; "
                   "read the memo before acting",
            symbol=symbol, payload={"score": score, "confidence": confidence})


def _rank(recommendation: str) -> int:
    order = {"SELL": 0, "AVOID": 0, "INSUFFICIENT_DATA": 1, "WATCH": 2,
             "HOLD": 3, "BUY": 4}
    return order.get(recommendation, 2)


class AlertDispatcher:
    """Deduplicates and delivers alerts. File sink by default; extensible."""

    def __init__(self, *, sinks: Sequence[Any] = (), repository: Any = None) -> None:
        self.sinks = list(sinks)
        self.repository = repository
        self._seen: set[str] = set()

    def dispatch(self, alerts: Iterable[Alert]) -> list[Alert]:
        delivered: list[Alert] = []
        for alert in alerts:
            if alert is None:
                continue
            key = alert.key()
            if key in self._seen:
                continue
            self._seen.add(key)
            delivered.append(alert)
            for sink in self.sinks:
                try:
                    sink(alert)
                except Exception:
                    log.exception("alert sink failed", kind=alert.kind)
            if self.repository is not None:
                try:
                    self.repository.write(
                        kind=alert.kind, severity=alert.severity.value,
                        title=alert.title, detail=alert.detail,
                        payload=dict(alert.payload))
                except Exception:
                    log.exception("failed to persist alert", kind=alert.kind)
        if delivered:
            log.info("alerts dispatched", count=len(delivered),
                     critical=sum(1 for a in delivered
                                  if a.severity is Severity.CRITICAL))
        return delivered


def file_sink(path: str | Path):
    """Append alerts as JSON lines."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    def _write(alert: Alert) -> None:
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(alert.to_dict()) + "\n")
    return _write


def evaluate_all(rules: AlertRules, *, symbol: str, as_of: date,
                 current: Mapping[str, Any],
                 previous: Mapping[str, Any] | None = None,
                 position: Mapping[str, Any] | None = None,
                 breached_conditions: Sequence[Mapping[str, Any]] = (),
                 unlock: Mapping[str, Any] | None = None,
                 is_crypto: bool = False) -> list[Alert]:
    """Run every rule for one asset and return the alerts that fired."""
    previous = previous or {}
    candidates = [
        rules.recommendation_change(symbol, previous.get("recommendation"),
                                    str(current.get("recommendation", ""))),
        rules.score_change(symbol, previous.get("score"), current.get("score")),
        rules.price_move(symbol, current.get("price_move_1d"), is_crypto=is_crypto),
        rules.valuation_target(symbol, current.get("price"),
                               (current.get("fair_value") or {}).get("base")),
        rules.thesis_invalidation(symbol, breached_conditions),
        rules.risk_increase(symbol, previous.get("risk_level"),
                            current.get("risk_level")),
        rules.data_quality_degraded(symbol, previous.get("data_quality"),
                                    current.get("data_quality")),
        rules.high_conviction(symbol, str(current.get("recommendation", "")),
                              current.get("score"), current.get("confidence")),
    ]
    if position:
        candidates.append(rules.position_drawdown(symbol, position.get("entry_price"),
                                                  current.get("price")))
    if unlock:
        unlock_date = unlock.get("unlock_date")
        parsed = (unlock_date if isinstance(unlock_date, date)
                  else date.fromisoformat(str(unlock_date)[:10]) if unlock_date else None)
        candidates.append(rules.token_unlock(symbol, parsed,
                                             unlock.get("pct_of_supply"), as_of=as_of))
    return [a for a in candidates if a is not None]
