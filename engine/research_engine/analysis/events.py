"""Event detection and classification.

An "event" is a dated, discrete occurrence that can reprice an asset. The engine
classifies each one on four axes:

* **impact direction** (extremely negative .. extremely positive);
* **expected magnitude** -- a rough percentage, honestly labelled as a prior;
* **duration** -- days over which the effect typically persists;
* **thesis relevance** -- whether it changes the reason for owning the asset,
  which is the only axis that should move a long-horizon recommendation.

Classification is rule-based and auditable. Rules are the right tool here: the
categories are stable, the vocabulary is narrow, and every judgement must be
explainable to someone reading a research memo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.numeric import clamp
from research_engine.core.types import (ClaimType, DataQuality, EventImpact,
                                        Evidence, SourceTier)


@dataclass(frozen=True, slots=True)
class EventRule:
    event_type: str
    patterns: tuple[str, ...]
    impact: EventImpact
    expected_move: float          # prior magnitude, |fraction|
    duration_days: int
    thesis_relevant: bool
    note: str = ""


#: Ordered: the first matching rule wins, so specific rules precede generic ones.
EVENT_RULES: tuple[EventRule, ...] = (
    EventRule("bankruptcy", ("chapter 11", "chapter 7", "bankruptcy", "insolvency",
                             "going concern"),
              EventImpact.EXTREMELY_NEGATIVE, 0.60, 365, True,
              "permanent capital loss is the central risk"),
    EventRule("fraud_investigation", ("fraud", "sec charges", "accounting irregularit",
                                      "restatement", "subpoena", "criminal"),
              EventImpact.EXTREMELY_NEGATIVE, 0.30, 180, True,
              "reported numbers can no longer be relied on"),
    EventRule("exploit", ("hack", "exploit", "drained", "rug pull", "bridge attack"),
              EventImpact.EXTREMELY_NEGATIVE, 0.35, 120, True,
              "protocol security failure"),
    EventRule("regulatory_action", ("lawsuit", "sued", "investigation", "probe",
                                    "regulatory action", "fine", "penalty",
                                    "cease and desist", "enforcement"),
              EventImpact.NEGATIVE, 0.08, 90, False),
    EventRule("guidance_cut", ("cuts guidance", "lowers guidance", "reduces outlook",
                               "guidance cut", "warns on", "profit warning"),
              EventImpact.EXTREMELY_NEGATIVE, 0.15, 120, True,
              "management's own forecast has deteriorated"),
    EventRule("guidance_raise", ("raises guidance", "lifts outlook", "raises outlook",
                                 "guidance raise", "upgrades forecast"),
              EventImpact.EXTREMELY_POSITIVE, 0.10, 120, True),
    EventRule("earnings_beat", ("beats estimates", "tops estimates", "earnings beat",
                                "exceeded expectations"),
              EventImpact.POSITIVE, 0.05, 30, False),
    EventRule("earnings_miss", ("misses estimates", "earnings miss", "falls short of",
                                "missed expectations"),
              EventImpact.NEGATIVE, 0.06, 30, False),
    EventRule("earnings", ("quarterly results", "reports q", "earnings report",
                           "full-year results", "annual results"),
              EventImpact.NEUTRAL, 0.04, 20, False),
    EventRule("leadership_change", ("ceo steps down", "cfo resigns", "resignation",
                                    "appoints ceo", "names ceo", "departs"),
              EventImpact.NEGATIVE, 0.05, 90, True,
              "unplanned senior departures often precede disclosed problems"),
    EventRule("acquisition_target", ("to be acquired", "takeover bid", "acquisition of",
                                     "agrees to be acquired", "merger agreement"),
              EventImpact.EXTREMELY_POSITIVE, 0.20, 60, True),
    EventRule("acquisition_made", ("acquires", "to acquire", "buys stake"),
              EventImpact.NEUTRAL, 0.03, 60, True,
              "acquirer outcomes are mixed; integration risk cuts both ways"),
    EventRule("dilution", ("offering", "share issuance", "capital raise",
                           "secondary offering", "convertible notes"),
              EventImpact.NEGATIVE, 0.08, 60, True),
    EventRule("buyback", ("buyback", "share repurchase", "repurchase program"),
              EventImpact.POSITIVE, 0.03, 90, False),
    EventRule("dividend_cut", ("cuts dividend", "suspends dividend", "dividend cut"),
              EventImpact.EXTREMELY_NEGATIVE, 0.12, 180, True),
    EventRule("dividend_raise", ("raises dividend", "increases dividend"),
              EventImpact.POSITIVE, 0.02, 90, False),
    EventRule("product_launch", ("launches", "unveils", "introduces", "general availability"),
              EventImpact.POSITIVE, 0.03, 45, False),
    EventRule("major_contract", ("wins contract", "awarded contract", "signs agreement",
                                 "multi-year deal"),
              EventImpact.POSITIVE, 0.05, 60, False),
    EventRule("token_unlock", ("token unlock", "vesting unlock", "cliff unlock"),
              EventImpact.NEGATIVE, 0.08, 30, True,
              "scheduled supply increase"),
    EventRule("listing", ("listed on", "lists on", "exchange listing"),
              EventImpact.POSITIVE, 0.06, 21, False),
    EventRule("delisting", ("delisting", "delisted", "removed from"),
              EventImpact.EXTREMELY_NEGATIVE, 0.25, 180, True),
    EventRule("partnership", ("partnership", "partners with", "collaboration"),
              EventImpact.POSITIVE, 0.03, 45, False,
              "partnership announcements are frequently immaterial"),
)


@dataclass
class DetectedEvent:
    event_type: str
    impact: EventImpact
    occurred_at: datetime
    headline: str
    source: str
    source_tier: SourceTier
    expected_move: float
    duration_days: int
    changes_thesis: bool
    confidence: float
    matched_pattern: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"event_type": self.event_type, "impact": self.impact.value,
                "occurred_at": self.occurred_at.isoformat(), "headline": self.headline,
                "source": self.source, "source_tier": self.source_tier.value,
                "expected_move": round(self.expected_move, 4),
                "duration_days": self.duration_days,
                "changes_thesis": self.changes_thesis,
                "confidence": round(self.confidence, 3), "note": self.note,
                "claim_type": ClaimType.INTERPRETATION.value}


def classify(headline: str, *, summary: str | None = None,
             occurred_at: datetime | None = None, source: str = "",
             source_tier: SourceTier = SourceTier.FINANCIAL_JOURNALISM
             ) -> DetectedEvent | None:
    """Match a headline against the rule set. Returns None when nothing matches."""
    text = f"{headline} {summary or ''}".lower()
    for rule in EVENT_RULES:
        for pattern in rule.patterns:
            if pattern in text:
                # Confidence rises with source authority and pattern specificity.
                confidence = clamp(
                    0.35 + 0.45 * source_tier.weight + 0.02 * len(pattern.split()),
                    0.2, 0.92)
                return DetectedEvent(
                    event_type=rule.event_type, impact=rule.impact,
                    occurred_at=occurred_at or datetime.now(),
                    headline=headline.strip(), source=source, source_tier=source_tier,
                    expected_move=rule.expected_move, duration_days=rule.duration_days,
                    changes_thesis=rule.thesis_relevant, confidence=confidence,
                    matched_pattern=pattern, note=rule.note)
    return None


def detect(items: Sequence[Any]) -> list[DetectedEvent]:
    """Classify a batch of news items, de-duplicating repeated stories."""
    events: list[DetectedEvent] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        headline = getattr(item, "headline", None)
        if not headline:
            continue
        event = classify(headline, summary=getattr(item, "summary", None),
                         occurred_at=getattr(item, "published_at", None),
                         source=getattr(item, "source", ""),
                         source_tier=getattr(item, "source_tier",
                                             SourceTier.FINANCIAL_JOURNALISM))
        if event is None:
            continue
        key = (event.event_type, event.occurred_at.date().isoformat())
        if key in seen:
            continue          # one story, many outlets: count the event once
        seen.add(key)
        events.append(event)
    return events


def aggregate_event_risk(events: Sequence[DetectedEvent], *,
                         as_of: date, decay_days: float = 45.0) -> dict[str, Any]:
    """Net event pressure, decayed by age, plus a thesis-change flag."""
    if not events:
        return {"score": None, "net_impact": None, "thesis_changing": [],
                "note": "no classified events in the window"}
    weighted = 0.0
    total_weight = 0.0
    thesis_changing: list[str] = []
    for event in events:
        age = max(0.0, (as_of - event.occurred_at.date()).days)
        decay = 0.5 ** (age / decay_days)
        weight = event.confidence * event.source_tier.weight * decay
        weighted += event.impact.polarity * event.expected_move * weight * 10
        total_weight += weight
        if event.changes_thesis and age <= event.duration_days:
            thesis_changing.append(f"{event.event_type}: {event.headline[:90]}")
    net = weighted / total_weight if total_weight else 0.0
    # 0..100 where 50 is neutral; used directly as the event_risk factor score.
    score = clamp(50.0 + net * 50.0, 0.0, 100.0)
    return {"score": score, "net_impact": round(net, 4),
            "thesis_changing": thesis_changing, "events_considered": len(events)}


def event_evidence(events: Sequence[DetectedEvent], *, as_of: date,
                   limit: int = 5) -> list[Evidence]:
    out: list[Evidence] = []
    ranked = sorted(events, key=lambda e: (abs(e.impact.polarity) * e.expected_move
                                           * e.confidence), reverse=True)
    for event in ranked[:limit]:
        age = (as_of - event.occurred_at.date()).days
        out.append(Evidence(
            label=f"Event: {event.event_type.replace('_', ' ')}",
            detail=(f"{event.headline[:140]} ({age}d ago, {event.source or 'unknown source'}"
                    f"); typical move around {event.expected_move:.0%}"
                    + (f"; {event.note}" if event.note else "")),
            direction=event.impact.polarity,
            weight=clamp(event.confidence * event.source_tier.weight, 0.1, 0.9),
            claim_type=ClaimType.INTERPRETATION,
            quality=DataQuality.GOOD if event.source_tier >= SourceTier.DATA_PROVIDER
            else DataQuality.FAIR,
            sources=(event.source or "news",)))
    return out


def upcoming_catalysts(*, earnings_date: date | None = None,
                       unlocks: Sequence[Mapping[str, Any]] = (),
                       as_of: date, horizon_days: int = 90) -> list[dict[str, Any]]:
    """Known dated events ahead -- the catalysts section of a memo."""
    out: list[dict[str, Any]] = []
    if earnings_date and as_of <= earnings_date <= as_of + timedelta(days=horizon_days):
        out.append({"type": "earnings", "date": earnings_date.isoformat(),
                    "days_away": (earnings_date - as_of).days,
                    "note": "reported results can confirm or break the thesis"})
    for unlock in unlocks:
        raw = unlock.get("unlock_date")
        if not raw:
            continue
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        if as_of <= day <= as_of + timedelta(days=horizon_days):
            out.append({"type": "token_unlock", "date": day.isoformat(),
                        "days_away": (day - as_of).days,
                        "tokens": unlock.get("tokens"),
                        "pct_of_supply": unlock.get("pct_of_supply"),
                        "note": "scheduled supply increase"})
    return sorted(out, key=lambda c: c["days_away"])
