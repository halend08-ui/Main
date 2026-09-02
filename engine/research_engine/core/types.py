"""Domain enumerations and value objects shared by every layer.

Design notes
------------
* Every enum that expresses a *judgement* (quality, conviction, risk) is
  ordered, so comparisons like ``quality >= DataQuality.GOOD`` are meaningful.
* Anything that can be "unknown" is modelled explicitly. There is no implicit
  zero/NaN standing in for a missing observation anywhere in this package.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping


class OrderedEnum(enum.Enum):
    """Enum whose members compare by declaration order."""

    def __init__(self, *args: Any) -> None:
        self._order_ = len(type(self).__members__)

    def _cmp_key(self) -> int:
        return self._order_

    def __lt__(self, other: object) -> bool:
        if isinstance(other, type(self)):
            return self._cmp_key() < other._cmp_key()
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, type(self)):
            return self._cmp_key() <= other._cmp_key()
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, type(self)):
            return self._cmp_key() > other._cmp_key()
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, type(self)):
            return self._cmp_key() >= other._cmp_key()
        return NotImplemented


class AssetClass(str, enum.Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    ETF = "etf"
    INDEX = "index"
    FX = "fx"
    COMMODITY = "commodity"
    MACRO = "macro"          # economic series, not investable


class DataQuality(OrderedEnum):
    """Ordered from worst to best so that ``>=`` reads naturally."""

    INSUFFICIENT = "insufficient"
    POOR = "poor"
    FAIR = "fair"
    GOOD = "good"
    EXCELLENT = "excellent"

    @classmethod
    def from_score(cls, score: float) -> "DataQuality":
        """Map a 0..1 quality score onto the ordered grades."""
        if score < 0.35:
            return cls.INSUFFICIENT
        if score < 0.55:
            return cls.POOR
        if score < 0.72:
            return cls.FAIR
        if score < 0.88:
            return cls.GOOD
        return cls.EXCELLENT

    @property
    def score_floor(self) -> float:
        return {"insufficient": 0.0, "poor": 0.35, "fair": 0.55,
                "good": 0.72, "excellent": 0.88}[self.value]


class Recommendation(str, enum.Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    WATCH = "WATCH"
    SELL = "SELL"
    AVOID = "AVOID"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class OpportunityTier(OrderedEnum):
    """Ranking buckets described in the product spec (worst -> best)."""

    AVOID = "avoid"
    HIGH_RISK = "high_risk"
    WATCH = "watch"
    MODERATE = "moderate"
    STRONG = "strong"
    EXCEPTIONAL = "exceptional"


class RiskLevel(OrderedEnum):
    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"
    EXTREME = "extreme"


class MarketRegime(str, enum.Enum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    HIGH_VOL = "high_volatility"
    LOW_VOL = "low_volatility"
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    RECESSIONARY = "recessionary"
    RECOVERY = "recovery"
    UNKNOWN = "unknown"


class Horizon(str, enum.Enum):
    """Analysis / prediction horizons. ``days`` is calendar days."""

    W1 = "1w"
    M1 = "1m"
    M3 = "3m"
    M6 = "6m"
    Y1 = "1y"
    Y3 = "3y"
    Y5 = "5y"
    Y10 = "10y"

    @property
    def days(self) -> int:
        return {"1w": 7, "1m": 30, "3m": 91, "6m": 182,
                "1y": 365, "3y": 1095, "5y": 1826, "10y": 3652}[self.value]

    @property
    def trading_days(self) -> int:
        return {"1w": 5, "1m": 21, "3m": 63, "6m": 126,
                "1y": 252, "3y": 756, "5y": 1260, "10y": 2520}[self.value]


class EventImpact(OrderedEnum):
    EXTREMELY_NEGATIVE = "extremely_negative"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    EXTREMELY_POSITIVE = "extremely_positive"

    @property
    def polarity(self) -> float:
        return {"extremely_negative": -1.0, "negative": -0.5, "neutral": 0.0,
                "positive": 0.5, "extremely_positive": 1.0}[self.value]


class SourceTier(OrderedEnum):
    """Source hierarchy (worst -> best). Used to weight conflicting evidence."""

    SOCIAL_MEDIA = "social_media"
    SECONDARY_RESEARCH = "secondary_research"
    FINANCIAL_JOURNALISM = "financial_journalism"
    DATA_PROVIDER = "data_provider"
    EARNINGS_MATERIAL = "earnings_material"
    COMPANY_DOCUMENTATION = "company_documentation"
    COMPANY_FILING = "company_filing"
    REGULATORY_FILING = "regulatory_filing"

    @property
    def weight(self) -> float:
        """Relative trust weight used by the fact-checking layer."""
        return {
            "social_media": 0.10,
            "secondary_research": 0.35,
            "financial_journalism": 0.50,
            "data_provider": 0.75,
            "earnings_material": 0.90,
            "company_documentation": 0.90,
            "company_filing": 0.97,
            "regulatory_filing": 1.00,
        }[self.value]


class StatementKind(str, enum.Enum):
    INCOME = "income"
    BALANCE = "balance"
    CASHFLOW = "cashflow"


class Period(str, enum.Enum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    TTM = "ttm"


class ClaimType(str, enum.Enum):
    """Epistemic status of a statement produced by the engine.

    Mixing these up is the single most common failure mode of automated
    research writing, so the type is explicit and carried into the output.
    """

    OBSERVATION = "historical_observation"
    MODEL_PREDICTION = "model_prediction"
    INTERPRETATION = "analyst_interpretation"
    ASSUMPTION = "assumption"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a value came from, and when we learned it.

    ``as_of`` is the moment the fact became *true in the world*; ``retrieved_at``
    is when we learned it. Point-in-time queries filter on ``retrieved_at`` so
    the engine can never use a value before it was knowable.
    """

    source: str
    source_tier: SourceTier
    as_of: datetime
    retrieved_at: datetime
    url: str | None = None
    quality: DataQuality = DataQuality.FAIR
    expires_at: datetime | None = None
    notes: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or utcnow()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_tier": self.source_tier.value,
            "as_of": self.as_of.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "url": self.url,
            "quality": self.quality.value,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    """A single provenance-carrying data point.

    ``value is None`` means *known to be unavailable* -- an explicit gap, never
    a zero.
    """

    asset: str
    metric: str
    value: float | None
    unit: str
    provenance: Provenance
    period: Period | None = None
    period_end: date | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class AssetRef:
    """Minimal identity of a tradable asset."""

    symbol: str
    asset_class: AssetClass
    exchange: str | None = None
    name: str | None = None

    @property
    def key(self) -> str:
        return f"{self.asset_class.value}:{self.symbol.upper()}"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One piece of support for (or against) a conclusion."""

    label: str
    detail: str
    direction: float           # -1 bearish .. +1 bullish
    weight: float              # 0..1 importance
    claim_type: ClaimType
    quality: DataQuality
    sources: tuple[str, ...] = ()

    def signed_weight(self) -> float:
        return self.direction * self.weight

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "detail": self.detail,
            "direction": round(self.direction, 3),
            "weight": round(self.weight, 3),
            "claim_type": self.claim_type.value,
            "quality": self.quality.value,
            "sources": list(self.sources),
        }


@dataclass(frozen=True, slots=True)
class Metric:
    """A computed number with the uncertainty and quality attached to it."""

    name: str
    value: float | None
    unit: str = ""
    quality: DataQuality = DataQuality.FAIR
    low: float | None = None
    high: float | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "quality": self.quality.value,
            "low": self.low,
            "high": self.high,
            "detail": dict(self.detail),
        }


INSUFFICIENT_DATA_MESSAGE = "Insufficient reliable data."
