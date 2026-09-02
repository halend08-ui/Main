"""News sentiment -- treated as weak, potentially manipulated evidence.

Explicit stance: sentiment is *not* truth. A wave of positive coverage tells you
what is being said, not what is happening. This module therefore:

* separates headline sentiment from body sentiment (headlines are written to be
  clicked, not to be accurate);
* weights every item by its source tier, so a regulatory filing outranks a blog;
* detects hype, panic, syndication and clickbait patterns and reports them as
  *warnings* rather than folding them into the score;
* refuses to produce a sentiment reading from too few or too low-quality items.

The lexicon is deliberately small, financial and auditable. A large opaque model
would score better on benchmarks and worse on explainability, and explainability
is the requirement here.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.numeric import clamp, mean
from research_engine.core.types import ClaimType, DataQuality, Evidence, SourceTier

POSITIVE_TERMS: dict[str, float] = {
    "beat": 0.6, "beats": 0.6, "exceeded": 0.6, "record": 0.5, "surge": 0.5,
    "surges": 0.5, "growth": 0.4, "grew": 0.4, "profit": 0.4, "profitable": 0.5,
    "upgrade": 0.7, "upgraded": 0.7, "raises": 0.5, "raised": 0.5, "outperform": 0.6,
    "approval": 0.6, "approved": 0.6, "partnership": 0.4, "expansion": 0.4,
    "buyback": 0.5, "dividend": 0.3, "acquisition": 0.3, "wins": 0.5, "won": 0.4,
    "breakthrough": 0.5, "milestone": 0.4, "strong": 0.4, "accelerating": 0.5,
    "recovery": 0.4, "rebound": 0.4, "optimistic": 0.3, "resilient": 0.3,
}

NEGATIVE_TERMS: dict[str, float] = {
    "miss": -0.6, "missed": -0.6, "misses": -0.6, "decline": -0.5, "declines": -0.5,
    "loss": -0.5, "losses": -0.5, "downgrade": -0.7, "downgraded": -0.7,
    "cuts": -0.5, "cut": -0.4, "warning": -0.6, "warns": -0.6, "lawsuit": -0.6,
    "sued": -0.6, "investigation": -0.7, "probe": -0.6, "fraud": -0.9,
    "bankruptcy": -1.0, "bankrupt": -1.0, "default": -0.9, "delisting": -0.8,
    "restructuring": -0.5, "layoffs": -0.4, "resigns": -0.5, "resignation": -0.5,
    "recall": -0.6, "halt": -0.6, "halted": -0.6, "plunge": -0.7, "plunges": -0.7,
    "slump": -0.5, "weak": -0.4, "weakness": -0.4, "delay": -0.4, "delayed": -0.4,
    "hack": -0.9, "hacked": -0.9, "exploit": -0.9, "breach": -0.7, "rug": -1.0,
    "sec charges": -0.9, "subpoena": -0.7, "impairment": -0.5, "writedown": -0.6,
    "dilution": -0.5, "going concern": -0.9,
}

NEGATORS = {"not", "no", "never", "without", "fails to", "failed to", "denies"}

HYPE_TERMS = {"skyrocket", "explode", "moon", "100x", "guaranteed", "can't lose",
              "next big thing", "millionaire", "urgent", "must buy", "to the moon",
              "life-changing", "insane gains", "don't miss"}

PANIC_TERMS = {"crash", "collapse", "meltdown", "catastrophe", "disaster",
               "wipeout", "carnage", "bloodbath", "panic"}

_WORD = re.compile(r"[a-z0-9']+")


@dataclass
class SentimentReading:
    score: float | None                # -1..+1, None when not assessable
    headline_score: float | None
    body_score: float | None
    items_scored: int
    confidence: float
    hype_score: float
    panic_score: float
    source_mix: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    top_positive: list[str] = field(default_factory=list)
    top_negative: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"score": None if self.score is None else round(self.score, 3),
                "headline_score": None if self.headline_score is None
                else round(self.headline_score, 3),
                "body_score": None if self.body_score is None else round(self.body_score, 3),
                "items_scored": self.items_scored,
                "confidence": round(self.confidence, 3),
                "hype_score": round(self.hype_score, 3),
                "panic_score": round(self.panic_score, 3),
                "source_mix": self.source_mix, "warnings": self.warnings,
                "top_positive": self.top_positive, "top_negative": self.top_negative,
                "claim_type": ClaimType.INTERPRETATION.value,
                "caveat": ("Sentiment describes coverage, not fundamentals. It is "
                           "weighted lowest of all evidence families.")}


def score_text(text: str | None) -> tuple[float | None, list[str]]:
    """Lexicon sentiment in -1..+1 with the matched terms, or None if no signal.

    Negation is handled with a three-word lookback, which is crude but
    transparent -- "did not beat expectations" must not read as positive.
    """
    if not text:
        return None, []
    words = _WORD.findall(text.lower())
    if not words:
        return None, []
    lowered = text.lower()
    hits: list[tuple[str, float]] = []

    for phrase, weight in {**POSITIVE_TERMS, **NEGATIVE_TERMS}.items():
        if " " in phrase and phrase in lowered:
            hits.append((phrase, weight))

    for i, word in enumerate(words):
        weight = POSITIVE_TERMS.get(word) or NEGATIVE_TERMS.get(word)
        if weight is None:
            continue
        window = words[max(0, i - 3):i]
        if any(w in NEGATORS for w in window) or " ".join(window[-2:]) in NEGATORS:
            weight = -weight * 0.8
        hits.append((word, weight))

    if not hits:
        return None, []
    total = sum(w for _, w in hits)
    # Saturating: twenty negative words are not twenty times one negative word.
    score = math.tanh(total / 2.0)
    matched = [term for term, _ in sorted(hits, key=lambda h: -abs(h[1]))][:5]
    return clamp(score, -1.0, 1.0), matched


def detect_hype(text: str | None) -> float:
    if not text:
        return 0.0
    lowered = text.lower()
    hits = sum(1 for term in HYPE_TERMS if term in lowered)
    exclamations = lowered.count("!")
    caps_words = sum(1 for w in (text or "").split()
                     if len(w) > 3 and w.isupper())
    return clamp((hits * 0.4 + exclamations * 0.15 + caps_words * 0.1), 0.0, 1.0)


def detect_panic(text: str | None) -> float:
    if not text:
        return 0.0
    lowered = text.lower()
    return clamp(sum(1 for term in PANIC_TERMS if term in lowered) * 0.35, 0.0, 1.0)


def analyse(items: Sequence[Any], *, as_of: date | None = None,
            half_life_days: float = 5.0, min_items: int = 3) -> SentimentReading:
    """Aggregate sentiment over news items, weighted by source tier and recency."""
    if not items:
        return SentimentReading(None, None, None, 0, 0.0, 0.0, 0.0,
                                warnings=["no news items available"])

    reference = as_of or max((getattr(i, "published_at", None) or datetime.now()).date()
                             for i in items)
    headline_scores: list[tuple[float, float]] = []
    body_scores: list[tuple[float, float]] = []
    hype_values: list[float] = []
    panic_values: list[float] = []
    source_mix: Counter[str] = Counter()
    positives: Counter[str] = Counter()
    negatives: Counter[str] = Counter()
    seen_headlines: set[str] = set()
    duplicates = 0

    for item in items:
        headline = getattr(item, "headline", None)
        summary = getattr(item, "summary", None)
        tier = getattr(item, "source_tier", SourceTier.FINANCIAL_JOURNALISM)
        published = getattr(item, "published_at", None)
        source_mix[tier.value if hasattr(tier, "value") else str(tier)] += 1

        key = (headline or "").strip().lower()
        if key in seen_headlines:
            duplicates += 1
            continue
        seen_headlines.add(key)

        age_days = 0.0
        if published is not None:
            age_days = max(0.0, (reference - published.date()).days)
        recency = 0.5 ** (age_days / half_life_days)
        weight = (tier.weight if hasattr(tier, "weight") else 0.5) * recency

        h_score, h_terms = score_text(headline)
        if h_score is not None:
            headline_scores.append((h_score, weight))
            for term in h_terms:
                (positives if term in POSITIVE_TERMS else negatives)[term] += 1
        b_score, b_terms = score_text(summary)
        if b_score is not None:
            body_scores.append((b_score, weight))
            for term in b_terms:
                (positives if term in POSITIVE_TERMS else negatives)[term] += 1

        hype_values.append(detect_hype(f"{headline or ''} {summary or ''}"))
        panic_values.append(detect_panic(f"{headline or ''} {summary or ''}"))

    def weighted(pairs: Sequence[tuple[float, float]]) -> float | None:
        if not pairs:
            return None
        total = sum(w for _, w in pairs)
        return sum(s * w for s, w in pairs) / total if total > 0 else None

    headline_score = weighted(headline_scores)
    body_score = weighted(body_scores)
    scored = len(headline_scores) + len(body_scores)

    warnings: list[str] = []
    if duplicates:
        warnings.append(f"{duplicates} duplicate headlines ignored (syndication)")
    if scored < min_items:
        warnings.append(f"only {scored} items carried usable sentiment signal")

    # Headline vs body divergence is a clickbait / misleading-headline signal.
    combined: float | None = None
    if headline_score is not None and body_score is not None:
        if abs(headline_score - body_score) > 0.5:
            warnings.append(
                f"headline sentiment ({headline_score:+.2f}) diverges from article "
                f"sentiment ({body_score:+.2f}): headlines may be misleading")
        combined = 0.35 * headline_score + 0.65 * body_score
    else:
        combined = body_score if body_score is not None else headline_score

    hype = float(mean(hype_values) or 0.0)
    panic = float(mean(panic_values) or 0.0)
    if hype > 0.3:
        warnings.append(f"promotional language detected (hype score {hype:.2f}); "
                        f"positive sentiment discounted")
        if combined is not None and combined > 0:
            combined *= (1.0 - clamp(hype, 0.0, 0.7))
    if panic > 0.3:
        warnings.append(f"panic language detected (panic score {panic:.2f})")

    tier_weights = [getattr(getattr(i, "source_tier", None), "weight", 0.5) for i in items]
    avg_tier = float(mean(tier_weights) or 0.5)
    if avg_tier < 0.4:
        warnings.append("coverage is dominated by low-tier sources")

    confidence = clamp(
        0.15 + 0.35 * clamp(scored / 15.0, 0, 1) + 0.3 * avg_tier
        - 0.3 * hype - 0.1 * (duplicates / max(len(items), 1)), 0.05, 0.7)
    if scored < min_items:
        combined = None
        confidence = min(confidence, 0.15)

    return SentimentReading(
        score=combined, headline_score=headline_score, body_score=body_score,
        items_scored=scored, confidence=confidence, hype_score=hype, panic_score=panic,
        source_mix=dict(source_mix), warnings=warnings,
        top_positive=[t for t, _ in positives.most_common(5)],
        top_negative=[t for t, _ in negatives.most_common(5)])


def sentiment_evidence(reading: SentimentReading) -> list[Evidence]:
    out: list[Evidence] = []
    if reading.score is not None and abs(reading.score) > 0.15:
        out.append(Evidence(
            label="News sentiment",
            detail=f"{reading.items_scored} items score {reading.score:+.2f} "
                   f"(source mix: {reading.source_mix})",
            direction=clamp(reading.score, -1, 1),
            weight=clamp(reading.confidence * 0.5, 0.05, 0.35),   # capped low
            claim_type=ClaimType.INTERPRETATION, quality=DataQuality.FAIR,
            sources=tuple(reading.source_mix)))
    for warning in reading.warnings:
        if "hype" in warning or "misleading" in warning or "low-tier" in warning:
            out.append(Evidence(
                label="Coverage quality warning", detail=warning, direction=-0.2,
                weight=0.3, claim_type=ClaimType.OBSERVATION,
                quality=DataQuality.GOOD, sources=("sentiment analysis",)))
    return out


# ------------------------------------------------------- fact checking -----
@dataclass
class Conflict:
    metric: str
    values: list[tuple[str, float, SourceTier, date | None]]
    resolution: float | None
    resolved_by: str | None
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric,
                "values": [{"source": s, "value": v, "tier": t.value,
                            "as_of": d.isoformat() if d else None}
                           for s, v, t, d in self.values],
                "resolution": self.resolution, "resolved_by": self.resolved_by,
                "note": self.note}


def reconcile(metric: str,
              candidates: Sequence[tuple[str, float, SourceTier, date | None]],
              *, tolerance: float = 0.02) -> Conflict:
    """Resolve disagreeing values by source hierarchy, then recency.

    Never averages conflicting numbers: an average of a filing and a blog post is
    worse than either. When the top tier is tied and the values still differ, the
    conflict is left explicitly unresolved.
    """
    usable = [(s, float(v), t, d) for s, v, t, d in candidates if v is not None]
    if not usable:
        return Conflict(metric, [], None, None, "no values available")
    if len(usable) == 1:
        s, v, _, _ = usable[0]
        return Conflict(metric, usable, v, s, "single source")

    values = [v for _, v, _, _ in usable]
    spread = (max(values) - min(values)) / abs(max(values, key=abs)) if max(values, key=abs) else 0
    if spread <= tolerance:
        best = max(usable, key=lambda c: c[2].weight)
        return Conflict(metric, usable, best[1], best[0],
                        f"sources agree within {tolerance:.0%}")

    top_tier = max(t for _, _, t, _ in usable)
    top = [c for c in usable if c[2] == top_tier]
    if len(top) == 1:
        return Conflict(metric, usable, top[0][1], top[0][0],
                        f"resolved by source hierarchy: {top_tier.value} outranks "
                        f"the others (spread was {spread:.1%})")

    dated = [c for c in top if c[3] is not None]
    if dated:
        newest = max(dated, key=lambda c: c[3])
        same_day = [c for c in dated if c[3] == newest[3]]
        if len(same_day) == 1:
            return Conflict(metric, usable, newest[1], newest[0],
                            f"resolved by recency within {top_tier.value} "
                            f"(spread was {spread:.1%})")
    return Conflict(metric, usable, None, None,
                    f"UNRESOLVED: {len(top)} equally authoritative sources disagree by "
                    f"{spread:.1%}; the value is treated as unavailable")
