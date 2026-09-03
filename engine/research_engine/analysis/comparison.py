"""Cross-sectional comparison: ranking assets against each other.

Scoring an asset in isolation answers "is this good?". Choosing between
thousands of them needs a different question: "is this better than the
alternatives, and why?". This module answers that one.

Design decisions that keep the comparison honest:

* **Peer-relative first.** A 22% ROIC is unremarkable in software and
  exceptional in grocery retail. Every factor is ranked within a peer group
  before anything is compared across groups.
* **Best-of-breed, then cross-sector.** Taking the global top-N on a composite
  score reliably returns whichever sector is currently in favour. The engine
  picks the leaders within each peer group first, then compares those winners
  on risk-adjusted expected return.
* **A percentile needs a sample.** Groups below the minimum size report "not
  comparable" rather than a rank out of four.
* **Comparisons state their own validity.** Two assets with different data
  quality, different sectors or different coverage are still comparable, but
  the output says what makes the comparison weak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar, Iterable, Mapping, Sequence

from research_engine.core.numeric import (clamp, is_finite, median, percentile,
                                          percentile_rank, safe_div)
from research_engine.core.types import (ClaimType, DataQuality, Evidence,
                                        OpportunityTier, Recommendation, RiskLevel)

#: Below this many members a peer group cannot support a percentile.
MIN_PEER_GROUP = 5

#: Factors where a HIGHER score is better. Every factor in the scoring engine is
#: normalised this way, but the mapping is stated explicitly rather than assumed.
HIGHER_IS_BETTER = True

#: Market-capitalisation bands used when a sector group is too small to rank in.
CAP_BANDS: tuple[tuple[str, float, float], ...] = (
    ("mega", 200e9, float("inf")),
    ("large", 10e9, 200e9),
    ("mid", 2e9, 10e9),
    ("small", 300e6, 2e9),
    ("micro", 0.0, 300e6),
)


def cap_band(market_cap: float | None) -> str:
    if not is_finite(market_cap):
        return "unknown"
    for name, low, high in CAP_BANDS:
        if low <= float(market_cap) < high:
            return name
    return "unknown"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One asset's comparable summary. Deliberately flat and picklable."""

    symbol: str
    asset_class: str
    sector: str | None
    market_cap: float | None
    score: float | None
    tier: str
    recommendation: str
    confidence: float
    risk_level: str
    data_quality: str
    expected_return_base: float | None
    expected_return_bear: float | None
    prob_positive: float | None
    factor_scores: Mapping[str, float | None] = field(default_factory=dict)

    @property
    def comparable(self) -> bool:
        """Assets the engine refused to score cannot be ranked against others."""
        return self.score is not None and self.recommendation != "INSUFFICIENT_DATA"

    #: Minimum downside assumed when ranking, by reported risk level. No equity
    #: has a 2% worst case; a bear case that mild means the model failed to
    #: imagine one, and dividing by it produces an explosive ratio that lets a
    #: single optimistic scenario dominate the whole ranking.
    DOWNSIDE_FLOOR: ClassVar[dict[str, float]] = {"low": 0.10, "moderate": 0.15, "elevated": 0.25,
                      "high": 0.40, "extreme": 0.60}

    def risk_adjusted_return(self) -> float | None:
        """Base-case expected return per unit of downside.

        Uses the bear case as the downside measure rather than volatility: what
        matters when choosing between candidates is how much is at stake if the
        thesis is wrong, not how much the price wobbles. The denominator is
        floored by risk level so an implausibly mild bear case cannot win the
        ranking by arithmetic.
        """
        if self.expected_return_base is None:
            return None
        floor = Candidate.DOWNSIDE_FLOOR.get(self.risk_level, 0.30)
        downside = self.expected_return_bear
        if downside is None or downside >= 0:
            return self.expected_return_base / floor
        return self.expected_return_base / max(abs(downside), floor)

    def downside_was_floored(self) -> bool:
        """True when the ranking used the floor rather than the modelled bear case."""
        floor = Candidate.DOWNSIDE_FLOOR.get(self.risk_level, 0.30)
        downside = self.expected_return_bear
        return downside is None or downside >= 0 or abs(downside) < floor


@dataclass
class PeerGroup:
    name: str
    basis: str                       # "sector" | "cap_band" | "asset_class"
    members: list[Candidate]
    note: str = ""

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def rankable(self) -> bool:
        return self.size >= MIN_PEER_GROUP

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "basis": self.basis, "size": self.size,
                "rankable": self.rankable, "note": self.note,
                "members": [m.symbol for m in self.members]}


@dataclass(frozen=True, slots=True)
class FactorRank:
    factor: str
    value: float | None
    percentile: float | None
    rank: int | None
    of: int

    def to_dict(self) -> dict[str, Any]:
        return {"factor": self.factor,
                "value": None if self.value is None else round(self.value, 1),
                "percentile": None if self.percentile is None else round(self.percentile, 3),
                "rank": self.rank, "of": self.of}


@dataclass
class RelativeProfile:
    symbol: str
    peer_group: str
    peer_group_size: int
    composite_percentile: float | None
    factor_ranks: list[FactorRank]
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "peer_group": self.peer_group,
                "peer_group_size": self.peer_group_size,
                "composite_percentile": (None if self.composite_percentile is None
                                         else round(self.composite_percentile, 3)),
                "factor_ranks": [f.to_dict() for f in self.factor_ranks],
                "strengths": self.strengths, "weaknesses": self.weaknesses,
                "caveats": self.caveats}


# ------------------------------------------------------- peer grouping -----
def build_peer_groups(candidates: Sequence[Candidate], *,
                      min_size: int = MIN_PEER_GROUP) -> list[PeerGroup]:
    """Group by sector, falling back to capitalisation band, then asset class.

    The fallback matters: a sector with three members cannot support percentile
    ranking, and silently ranking within it would produce confident nonsense.
    """
    by_sector: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        key = candidate.sector or "Unclassified"
        by_sector.setdefault(key, []).append(candidate)

    groups: list[PeerGroup] = []
    orphans: list[Candidate] = []
    for sector, members in sorted(by_sector.items()):
        if sector != "Unclassified" and len(members) >= min_size:
            groups.append(PeerGroup(sector, "sector", members))
        else:
            orphans.extend(members)

    if orphans:
        by_band: dict[str, list[Candidate]] = {}
        for candidate in orphans:
            by_band.setdefault(cap_band(candidate.market_cap), []).append(candidate)
        leftovers: list[Candidate] = []
        for band, members in sorted(by_band.items()):
            if len(members) >= min_size:
                groups.append(PeerGroup(
                    f"{band}-cap", "cap_band", members,
                    note="grouped by size because the sector had too few members "
                         "to rank within"))
            else:
                leftovers.extend(members)
        if leftovers:
            by_class: dict[str, list[Candidate]] = {}
            for candidate in leftovers:
                by_class.setdefault(candidate.asset_class, []).append(candidate)
            for asset_class, members in sorted(by_class.items()):
                groups.append(PeerGroup(
                    asset_class, "asset_class", members,
                    note="no sector or size peer group reached the minimum size; "
                         "relative ranks for these assets are weak evidence"))
    return groups


# ------------------------------------------------------- factor ranking ----
def rank_within(group: PeerGroup, factors: Sequence[str] | None = None
                ) -> dict[str, RelativeProfile]:
    """Percentile-rank every member of a peer group on every factor."""
    members = [m for m in group.members if m.comparable]
    if not members:
        return {}

    names = list(factors) if factors else sorted(
        {f for m in members for f in m.factor_scores})

    profiles: dict[str, RelativeProfile] = {}
    composites = [m.score for m in members if m.score is not None]

    for member in members:
        ranks: list[FactorRank] = []
        strengths: list[str] = []
        weaknesses: list[str] = []
        for name in names:
            population = [m.factor_scores.get(name) for m in members]
            population = [v for v in population if is_finite(v)]
            value = member.factor_scores.get(name)
            if not group.rankable or len(population) < MIN_PEER_GROUP or value is None:
                ranks.append(FactorRank(name, value, None, None, len(population)))
                continue
            pct = percentile_rank(population, value)
            ordered = sorted(population, reverse=True)
            position = ordered.index(value) + 1 if value in ordered else None
            ranks.append(FactorRank(name, value, pct, position, len(population)))
            if pct is not None and pct >= 0.80:
                strengths.append(f"{name.replace('_', ' ')} in the top "
                                 f"{(1 - pct) * 100:.0f}% of {group.name} peers")
            elif pct is not None and pct <= 0.20:
                weaknesses.append(f"{name.replace('_', ' ')} in the bottom "
                                  f"{pct * 100:.0f}% of {group.name} peers")

        composite_pct = (percentile_rank(composites, member.score)
                         if group.rankable and member.score is not None
                         and len(composites) >= MIN_PEER_GROUP else None)

        caveats: list[str] = []
        if not group.rankable:
            caveats.append(
                f"peer group '{group.name}' has only {group.size} members; "
                f"at least {MIN_PEER_GROUP} are needed for a percentile to mean "
                f"anything, so no relative rank is reported")
        if group.note:
            caveats.append(group.note)
        if member.data_quality in ("poor", "insufficient"):
            caveats.append(f"this asset's data quality is {member.data_quality}: "
                           f"its position in the ranking is unreliable")

        profiles[member.symbol] = RelativeProfile(
            symbol=member.symbol, peer_group=group.name,
            peer_group_size=group.size, composite_percentile=composite_pct,
            factor_ranks=ranks, strengths=strengths[:5], weaknesses=weaknesses[:5],
            caveats=caveats)
    return profiles


# --------------------------------------------------------- head to head ----
@dataclass
class HeadToHead:
    a: str
    b: str
    winner: str | None
    margin: float | None
    factor_deltas: list[dict[str, Any]]
    summary: str
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"a": self.a, "b": self.b, "winner": self.winner,
                "margin": None if self.margin is None else round(self.margin, 2),
                "factor_deltas": self.factor_deltas, "summary": self.summary,
                "caveats": self.caveats,
                "claim_type": ClaimType.INTERPRETATION.value}


def head_to_head(a: Candidate, b: Candidate, *,
                 profiles: Mapping[str, RelativeProfile] | None = None
                 ) -> HeadToHead:
    """Compare two assets factor by factor and say which is better, and why."""
    caveats: list[str] = []
    if not a.comparable or not b.comparable:
        unusable = a.symbol if not a.comparable else b.symbol
        return HeadToHead(a.symbol, b.symbol, None, None, [],
                          f"{unusable} could not be scored, so the two are not "
                          f"comparable.",
                          [f"{unusable}: {a.recommendation if unusable == a.symbol else b.recommendation}"])

    if a.sector != b.sector:
        caveats.append(
            f"different sectors ({a.sector or 'unknown'} vs {b.sector or 'unknown'}): "
            f"factor levels are not directly comparable, so peer percentiles carry "
            f"more weight than raw scores here")
    if a.data_quality != b.data_quality:
        caveats.append(f"different data quality ({a.data_quality} vs "
                       f"{b.data_quality}): the weaker side's position is less certain")

    deltas: list[dict[str, Any]] = []
    shared = sorted(set(a.factor_scores) & set(b.factor_scores))
    for factor in shared:
        va, vb = a.factor_scores.get(factor), b.factor_scores.get(factor)
        if not is_finite(va) or not is_finite(vb):
            deltas.append({"factor": factor, "a": va, "b": vb, "delta": None,
                           "note": "not computable for both"})
            continue
        entry: dict[str, Any] = {"factor": factor, "a": round(float(va), 1),
                                 "b": round(float(vb), 1),
                                 "delta": round(float(va) - float(vb), 1),
                                 "favours": a.symbol if va > vb else
                                 (b.symbol if vb > va else "neither")}
        if profiles:
            pa = profiles.get(a.symbol)
            pb = profiles.get(b.symbol)
            for label, profile in (("a_percentile", pa), ("b_percentile", pb)):
                if profile is None:
                    continue
                match = next((f for f in profile.factor_ranks if f.factor == factor),
                             None)
                if match and match.percentile is not None:
                    entry[label] = round(match.percentile, 2)
        deltas.append(entry)

    deltas.sort(key=lambda d: abs(d.get("delta") or 0), reverse=True)

    ra, rb = a.risk_adjusted_return(), b.risk_adjusted_return()
    winner: str | None = None
    margin: float | None = None
    if ra is not None and rb is not None:
        winner = a.symbol if ra > rb else (b.symbol if rb > ra else None)
        margin = abs(ra - rb)
        basis = "risk-adjusted expected return"
    elif a.score is not None and b.score is not None:
        winner = a.symbol if a.score > b.score else (b.symbol if b.score > a.score else None)
        margin = abs(a.score - b.score)
        basis = "composite score"
        caveats.append("expected returns were unavailable for at least one side, "
                       "so the comparison falls back on the composite score")
    else:
        basis = "nothing comparable"

    if winner is None:
        summary = f"{a.symbol} and {b.symbol} are too close to separate on {basis}."
    else:
        loser = b.symbol if winner == a.symbol else a.symbol
        strongest = [d for d in deltas if d.get("favours") == winner][:2]
        against = [d for d in deltas if d.get("favours") == loser][:2]
        summary = (
            f"{winner} ranks ahead of {loser} on {basis} "
            f"(margin {margin:.2f}). "
            + (f"It leads on "
               + ", ".join(f"{d['factor'].replace('_', ' ')} "
                           f"({d['a'] if winner == a.symbol else d['b']:.0f} vs "
                           f"{d['b'] if winner == a.symbol else d['a']:.0f})"
                           for d in strongest) + ". " if strongest else "")
            + (f"{loser} is better on "
               + ", ".join(d["factor"].replace("_", " ") for d in against) + "."
               if against else ""))

    return HeadToHead(a.symbol, b.symbol, winner, margin, deltas, summary, caveats)


# ------------------------------------------------- the full comparison -----
@dataclass
class ComparisonResult:
    as_of: date
    peer_groups: list[PeerGroup]
    profiles: dict[str, RelativeProfile]
    best_of_breed: list[dict[str, Any]]
    final_ranking: list[dict[str, Any]]
    absolute_ranking: list[dict[str, Any]]
    disagreements: list[str]
    excluded: list[dict[str, str]]
    per_group: int = 3
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(),
                "peer_groups": [g.to_dict() for g in self.peer_groups],
                "profiles": {k: v.to_dict() for k, v in self.profiles.items()},
                "best_of_breed": self.best_of_breed,
                "final_ranking": self.final_ranking,
                "absolute_ranking": self.absolute_ranking,
                "disagreements": self.disagreements, "excluded": self.excluded,
                "notes": self.notes}

    def render(self, limit: int = 20) -> str:
        lines: list[str] = []
        add = lines.append
        add(f"CROSS-SECTIONAL COMPARISON  {self.as_of.isoformat()}")
        add("")
        add(f"Peer groups: {len(self.peer_groups)} "
            f"({sum(1 for g in self.peer_groups if g.rankable)} large enough to rank in)")
        add(f"Comparable assets: {len(self.profiles)}  "
            f"excluded: {len(self.excluded)}")
        add("")
        add(f"BEST OF BREED (top {self.per_group} within each peer group)")
        add(f"{'group':<24} {'symbol':<10} {'score':>6} {'pctile':>7} "
            f"{'base ret':>9} {'risk-adj':>9}")
        for row in self.best_of_breed[:limit]:
            add(f"{row['peer_group']:<24} {row['symbol']:<10} "
                f"{_fmt(row['score'], 0):>6} {_fmt_pct(row['percentile']):>7} "
                f"{_fmt_pct(row['expected_return_base']):>9} "
                f"{_fmt(row['risk_adjusted'], 2):>9}")
        add("")
        add("FINAL RANKING (best of breed, compared across groups on "
            "risk-adjusted expected return)")
        for i, row in enumerate(self.final_ranking[:limit], start=1):
            floored = " [downside floored]" if row.get("downside_floored") else ""
            add(f"{i:>2}. {row['symbol']:<10} {row['peer_group']:<22} "
                f"risk-adj {_fmt(row['risk_adjusted'], 2)}  "
                f"base {_fmt_pct(row['expected_return_base'])}  "
                f"{row['recommendation']}{floored}")
        if self.disagreements:
            add("")
            add("WHERE THE TWO VIEWS DISAGREE")
            for note in self.disagreements[:8]:
                add(f"  * {note}")
        if self.notes:
            add("")
            for note in self.notes:
                add(f"note: {note}")
        return "\n".join(lines)


def _fmt(value: Any, digits: int) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}"


def _fmt_pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.0f}%"


def compare(candidates: Sequence[Candidate], *, as_of: date,
            per_group: int = 3, min_group_size: int = MIN_PEER_GROUP
            ) -> ComparisonResult:
    """Peer-relative ranking, then cross-group comparison of the leaders."""
    excluded = [{"symbol": c.symbol, "reason": c.recommendation}
                for c in candidates if not c.comparable]
    comparable = [c for c in candidates if c.comparable]
    notes: list[str] = []
    if not comparable:
        return ComparisonResult(
            as_of, [], {}, [], [], [], [], excluded, per_group,
            ["no asset could be scored, so nothing is comparable"])

    groups = build_peer_groups(comparable, min_size=min_group_size)
    profiles: dict[str, RelativeProfile] = {}
    for group in groups:
        profiles.update(rank_within(group))

    group_by_symbol = {m.symbol: g for g in groups for m in g.members}
    candidate_by_symbol = {c.symbol: c for c in comparable}

    # -- best of breed ----------------------------------------------------
    best_of_breed: list[dict[str, Any]] = []
    for group in groups:
        members = sorted((m for m in group.members if m.comparable),
                         key=lambda m: (m.score if m.score is not None else -1),
                         reverse=True)
        for member in members[:per_group]:
            profile = profiles.get(member.symbol)
            best_of_breed.append({
                "symbol": member.symbol, "peer_group": group.name,
                "peer_group_basis": group.basis, "score": member.score,
                "percentile": profile.composite_percentile if profile else None,
                "expected_return_base": member.expected_return_base,
                "risk_adjusted": member.risk_adjusted_return(),
                "recommendation": member.recommendation,
                "risk_level": member.risk_level,
                "data_quality": member.data_quality,
                "strengths": profile.strengths if profile else [],
                "downside_floored": member.downside_was_floored(),
            })
    best_of_breed.sort(key=lambda r: (r["score"] or 0), reverse=True)

    # -- cross-group final ranking ----------------------------------------
    ranked = [r for r in best_of_breed if r["risk_adjusted"] is not None]
    unranked = [r for r in best_of_breed if r["risk_adjusted"] is None]
    ranked.sort(key=lambda r: r["risk_adjusted"], reverse=True)
    if unranked:
        notes.append(
            f"{len(unranked)} group leader(s) had no expected-return estimate and "
            f"are listed after the ranked ones rather than being dropped: "
            f"{', '.join(r['symbol'] for r in unranked[:5])}")
    final_ranking = ranked + unranked

    # -- absolute ranking, for the disagreement check ---------------------
    absolute = sorted(comparable, key=lambda c: (c.score if c.score is not None else -1),
                      reverse=True)
    absolute_ranking = [{"symbol": c.symbol, "score": c.score,
                         "peer_group": group_by_symbol[c.symbol].name
                         if c.symbol in group_by_symbol else "unknown",
                         "recommendation": c.recommendation}
                        for c in absolute]

    disagreements = _describe_disagreements(final_ranking, absolute_ranking,
                                            profiles, candidate_by_symbol)

    unrankable = [g for g in groups if not g.rankable]
    if unrankable:
        notes.append(
            f"{len(unrankable)} peer group(s) were below the minimum size, so their "
            f"members carry no percentile: "
            f"{', '.join(g.name for g in unrankable[:5])}")

    return ComparisonResult(as_of=as_of, peer_groups=groups, profiles=profiles,
                            best_of_breed=best_of_breed, final_ranking=final_ranking,
                            absolute_ranking=absolute_ranking,
                            disagreements=disagreements, excluded=excluded,
                            per_group=per_group, notes=notes)


def _describe_disagreements(final: Sequence[Mapping[str, Any]],
                            absolute: Sequence[Mapping[str, Any]],
                            profiles: Mapping[str, RelativeProfile],
                            candidates: Mapping[str, Candidate]) -> list[str]:
    """Name the cases where absolute score and peer-relative view diverge."""
    out: list[str] = []
    absolute_positions = {row["symbol"]: i + 1 for i, row in enumerate(absolute)}
    final_positions = {row["symbol"]: i + 1 for i, row in enumerate(final)}

    for symbol, final_rank in list(final_positions.items())[:15]:
        absolute_rank = absolute_positions.get(symbol)
        if absolute_rank is None:
            continue
        if absolute_rank - final_rank >= 5:
            profile = profiles.get(symbol)
            reason = (f"top of its {profile.peer_group} peer group"
                      if profile else "a peer-group leader")
            out.append(
                f"{symbol} ranks {final_rank} on the peer-relative view but "
                f"{absolute_rank} on raw score: it is {reason} without being "
                f"a high scorer in absolute terms")
        elif final_rank - absolute_rank >= 5:
            out.append(
                f"{symbol} scores well in absolute terms (rank {absolute_rank}) "
                f"but only {final_rank} once compared with its own peers")

    # concentration check on the absolute view
    top_absolute = absolute[:10]
    sectors = [row.get("peer_group") for row in top_absolute]
    if sectors:
        dominant = max(set(sectors), key=sectors.count)
        share = sectors.count(dominant) / len(sectors)
        if share >= 0.6:
            out.append(
                f"the absolute top 10 is {share:.0%} '{dominant}': a raw score "
                f"ranking concentrates in whichever group is currently favoured, "
                f"which is the reason for the peer-relative view")
    return out


def comparison_evidence(profile: RelativeProfile) -> list[Evidence]:
    """Turn a relative profile into evidence for an individual recommendation."""
    out: list[Evidence] = []
    if profile.composite_percentile is not None:
        pct = profile.composite_percentile
        out.append(Evidence(
            label="Peer-relative standing",
            detail=f"ranks in the {pct * 100:.0f}th percentile of "
                   f"{profile.peer_group_size} {profile.peer_group} peers",
            direction=clamp((pct - 0.5) * 2, -1, 1), weight=0.6,
            claim_type=ClaimType.OBSERVATION, quality=DataQuality.GOOD,
            sources=("cross-sectional comparison",)))
    for strength in profile.strengths[:3]:
        out.append(Evidence(
            label="Peer-group strength", detail=strength, direction=0.5, weight=0.4,
            claim_type=ClaimType.OBSERVATION, quality=DataQuality.GOOD,
            sources=("cross-sectional comparison",)))
    for weakness in profile.weaknesses[:3]:
        out.append(Evidence(
            label="Peer-group weakness", detail=weakness, direction=-0.5, weight=0.4,
            claim_type=ClaimType.OBSERVATION, quality=DataQuality.GOOD,
            sources=("cross-sectional comparison",)))
    for caveat in profile.caveats:
        out.append(Evidence(
            label="Comparison caveat", detail=caveat, direction=0.0, weight=0.2,
            claim_type=ClaimType.ASSUMPTION, quality=DataQuality.FAIR, sources=()))
    return out


def candidate_from_result(result: Any, *, sector: str | None = None,
                          market_cap: float | None = None,
                          asset_class: str = "equity") -> Candidate:
    """Adapt a RecommendationResult into a comparison Candidate."""
    return Candidate(
        symbol=result.symbol, asset_class=asset_class, sector=sector,
        market_cap=market_cap, score=result.score, tier=result.tier.value,
        recommendation=result.recommendation.value, confidence=result.confidence,
        risk_level=result.risk_level.value, data_quality=result.data_quality.value,
        expected_return_base=result.expected_return.get("base"),
        expected_return_bear=result.expected_return.get("bear"),
        prob_positive=result.prob_positive,
        factor_scores=dict(getattr(result, "factor_scores", {}) or {}))
