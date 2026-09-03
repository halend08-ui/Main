"""The research agent: deep investigation of a single asset on demand.

``analyze NVDA`` should do what a competent analyst does in an afternoon --
gather everything available, cross-check it, produce a structured thesis, and be
explicit about what could not be established.

The agent is a *composition* of the existing engines, not a separate model. That
matters: an on-demand answer and the daily scan must never disagree because they
used different code paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from research_engine.analysis import memo as MEMO
from research_engine.analysis import sentiment as SENT
from research_engine.analysis.pipeline import analyze
from research_engine.analysis.recommendation import RecommendationResult
from research_engine.core.errors import DataUnavailable
from research_engine.core.logging import get_logger
from research_engine.core.timeutil import current_as_of
from research_engine.core.numeric import median, percentile
from research_engine.core.types import AssetClass, ClaimType, DataQuality, SourceTier
from research_engine.features import returns as RET
from research_engine.features import valuation as VAL

log = get_logger(__name__)


@dataclass
class ResearchDossier:
    symbol: str
    as_of: date
    recommendation: RecommendationResult | None
    memo: str
    horizons: dict[str, Any] = field(default_factory=dict)
    peers: list[dict[str, Any]] = field(default_factory=list)
    reverse_dcf: dict[str, Any] = field(default_factory=dict)
    historical_analogues: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "as_of": self.as_of.isoformat(),
            "recommendation": self.recommendation.to_dict() if self.recommendation else None,
            "horizons": self.horizons, "peers": self.peers,
            "reverse_dcf": self.reverse_dcf,
            "historical_analogues": self.historical_analogues,
            "conflicts": self.conflicts, "unanswered_questions": self.unanswered,
            "sources": self.sources, "memo": self.memo,
        }


class ResearchAgent:
    """Runs a deep investigation using the same engines as the daily loop."""

    def __init__(self, settings: Any, data: Any, *,
                 model_version: str = "scoring_v1") -> None:
        self.settings = settings
        self.data = data
        self.model_version = model_version

    def investigate(self, symbol: str, *, as_of: date | None = None,
                    peer_symbols: Sequence[str] = ()) -> ResearchDossier:
        # current_as_of() respects an active as_of_context, so replaying a past
        # date produces the answer that date's data supported.
        as_of = as_of or current_as_of().date()
        symbol = symbol.upper()
        unanswered: list[str] = []

        try:
            bundle = self.data.analysis_input(symbol, as_of)
        except (DataUnavailable, KeyError) as exc:
            return ResearchDossier(
                symbol=symbol, as_of=as_of, recommendation=None,
                memo=f"# {symbol}\n\nInsufficient reliable data: {exc}\n",
                unanswered=[str(exc)])

        bundle.settings = self.settings
        bundle.model_version = self.model_version
        bundle.calibrator = self.data.calibrator(self.model_version)
        bundle.learned_base_rates = self.data.learned_base_rates()

        peers = self._peer_analysis(symbol, peer_symbols or self._infer_peers(symbol),
                                    as_of)
        if peers:
            multiples = [p["ev_ebitda"] for p in peers if p.get("ev_ebitda")]
            if len(multiples) >= 3:
                bundle.peer_multiples = {"ev_ebitda": multiples}
        else:
            unanswered.append(
                "No peer group could be assembled, so relative valuation is "
                "unassessed. Peer comparison is one of the strongest checks on a "
                "standalone DCF, and its absence widens the uncertainty.")

        result = analyze(bundle)

        horizons = self._horizon_analysis(bundle)
        analogues = self._historical_analogues(bundle)
        reverse = self._reverse_dcf(bundle, result)
        conflicts = self._cross_check(symbol, as_of, bundle)

        for question, answered in (
                ("Management quality and incentives could not be assessed: no "
                 "proxy-statement or compensation data is ingested.",
                 False),
                ("Customer concentration and contract structure are not in any "
                 "configured data source.", False),
                ("Insider transactions were not available for this asset.",
                 bool(getattr(bundle, "insider_transactions", None)))):
            if not answered:
                unanswered.append(question)

        if not bundle.news:
            unanswered.append("No news coverage was retrieved, so event and "
                              "sentiment analysis are absent rather than neutral.")

        memo = MEMO.generate(
            result,
            fundamental=self._fundamental_summary(bundle),
            valuation={"assumptions": (result.to_dict().get("ensemble") or {}) and
                       self._valuation_assumptions(result),
                       "warnings": []},
            technical=self._technical_summary(bundle),
            peers=peers,
            sources=self._sources(bundle))

        return ResearchDossier(
            symbol=symbol, as_of=as_of, recommendation=result, memo=memo,
            horizons=horizons, peers=peers, reverse_dcf=reverse,
            historical_analogues=analogues, conflicts=conflicts,
            unanswered=unanswered, sources=self._sources(bundle))

    # -- components --------------------------------------------------------
    def _horizon_analysis(self, bundle: Any) -> dict[str, Any]:
        """Behaviour over every configured horizon, including drawdowns."""
        if bundle.series is None:
            return {"note": "no price history"}
        out: dict[str, Any] = {}
        # calendar days per label; horizon_returns converts using the
        # series' own frequency
        windows = {"1w": 7, "1m": 30, "3m": 91, "6m": 182, "1y": 365,
                   "3y": 1095, "5y": 1826, "10y": 3652}
        trailing = RET.horizon_returns(bundle.series, list(windows.values()))
        for label, window in windows.items():
            value = trailing.get(window)
            out[label] = (round(value, 4) if value is not None
                          else "insufficient history")
        episodes = RET.drawdown_episodes(bundle.series, min_depth=0.15)
        out["drawdowns"] = [{"start": e.start.isoformat(),
                             "trough": e.trough.isoformat(),
                             "recovered": e.recovery.isoformat() if e.recovery else None,
                             "depth": round(e.depth, 4),
                             "recovery_days": e.recovery_days}
                            for e in episodes[-5:]]
        summary = RET.summarize(bundle.series, benchmark=bundle.benchmark)
        out["risk_summary"] = {k: (round(v, 4) if isinstance(v, float) else v)
                               for k, v in summary.items()}
        return out

    def _peer_analysis(self, symbol: str, peers: Sequence[str],
                       as_of: date) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for peer in peers:
            if peer.upper() == symbol:
                continue
            try:
                bundle = self.data.analysis_input(peer.upper(), as_of)
            except (DataUnavailable, KeyError):
                continue
            row: dict[str, Any] = {"symbol": peer.upper()}
            market_cap = bundle.market.get("market_cap_usd")
            operating = (bundle.annual.get("operating_income") or [None])[-1]
            da = (bundle.annual.get("depreciation_amortization") or [None])[-1]
            if market_cap and operating is not None and getattr(operating, "value", None):
                ebitda = float(operating.value) + float(getattr(da, "value", 0) or 0)
                if ebitda > 0:
                    row["ev_ebitda"] = round(market_cap / ebitda, 2)
            revenue = (bundle.annual.get("revenue") or [None])[-1]
            if revenue is not None and getattr(revenue, "value", None):
                row["revenue"] = float(revenue.value)
            row["note"] = "same sector" if bundle.sector else "sector unknown"
            out.append(row)
        return out

    def _infer_peers(self, symbol: str) -> list[str]:
        try:
            return self.data.sector_peers(symbol)
        except AttributeError:
            return []

    def _reverse_dcf(self, bundle: Any, result: RecommendationResult) -> dict[str, Any]:
        """What growth does today's price already require?"""
        if bundle.asset_class is AssetClass.CRYPTO or result.price is None:
            return {"applicable": False,
                    "reason": "reverse DCF does not apply to this asset class"}
        from research_engine.features import fundamental as FUND
        fcf = FUND.free_cash_flow(bundle.annual)
        shares = None
        for metric in ("shares_diluted", "shares_outstanding"):
            points = bundle.annual.get(metric) or []
            if points and points[-1].value:
                shares = float(points[-1].value)
                break
        if not fcf or not shares:
            return {"applicable": False,
                    "reason": "free cash flow or share count unavailable"}
        outcome = VAL.reverse_dcf(
            price=result.price, shares=shares, base_fcf=fcf,
            net_debt=FUND.net_debt(bundle.annual) or 0.0,
            discount_rate=float(self.settings.get("analysis.risk_free_rate_default", 0.04))
            + float(self.settings.get("analysis.equity_risk_premium", 0.05)),
            terminal_growth=float(self.settings.get(
                "analysis.valuation.terminal_growth", 0.025)))
        implied = outcome.get("implied_growth")
        profile = FUND.growth_profile(bundle.annual, "revenue")
        # Prefer the longest window actually available at this as-of date: a
        # point-in-time run early in a fiscal year may not yet have five filed
        # years, and saying nothing would be worse than saying "over 3 years".
        historical, window = next(
            ((profile.get(f"cagr_{years}y"), years) for years in (10, 5, 3, 1)
             if profile.get(f"cagr_{years}y") is not None), (None, None))
        if implied is not None and historical is not None:
            outcome["delivered_growth"] = round(historical, 4)
            outcome["delivered_growth_window_years"] = window
            outcome["comparison"] = (
                f"the price implies {implied:.1%} annual cash-flow growth; the "
                f"company has actually delivered {historical:.1%} over {window} "
                f"year(s)"
                + (" -- the market is asking for an acceleration"
                   if implied > historical else
                   " -- the market is pricing a slowdown"))
        elif implied is not None:
            outcome["comparison"] = (
                f"the price implies {implied:.1%} annual cash-flow growth; no "
                f"multi-year revenue history was filed as of this date, so there "
                f"is nothing to compare it against")
        outcome["applicable"] = True
        return outcome

    def _historical_analogues(self, bundle: Any) -> list[dict[str, Any]]:
        """Past periods in this asset's own history that resemble today.

        Deliberately limited to the asset's own history: cross-asset "analogues"
        are almost always story-telling, and the sample sizes do not support the
        confidence people place in them.
        """
        if bundle.series is None or len(bundle.series) < 500:
            return []
        import numpy as np
        from research_engine.features import technical as TECH

        px = bundle.series.adj_close
        ma = TECH.sma(px, 200)
        rsi = TECH.rsi(px, 14)
        current_ratio = px[-1] / ma[-1] if np.isfinite(ma[-1]) and ma[-1] > 0 else None
        current_rsi = rsi[-1] if np.isfinite(rsi[-1]) else None
        if current_ratio is None or current_rsi is None:
            return []

        out: list[dict[str, Any]] = []
        forward = 126        # six months
        for i in range(220, len(px) - forward):
            if not (np.isfinite(ma[i]) and ma[i] > 0 and np.isfinite(rsi[i])):
                continue
            ratio = px[i] / ma[i]
            if abs(ratio - current_ratio) < 0.03 and abs(rsi[i] - current_rsi) < 6:
                out.append({
                    "date": bundle.series.dates[i].isoformat(),
                    "price_vs_200dma": round(float(ratio - 1), 4),
                    "rsi": round(float(rsi[i]), 1),
                    "forward_6m_return": round(float(px[i + forward] / px[i] - 1), 4)})
        if not out:
            return []
        returns = [o["forward_6m_return"] for o in out]
        summary = {
            "matches": len(out),
            "median_forward_6m": round(median(returns) or 0.0, 4),
            "worst": round(min(returns), 4), "best": round(max(returns), 4),
            "caveat": ("these are overlapping observations from a single asset's "
                       "history; they describe the past, they do not forecast, "
                       "and the effective sample size is far smaller than the "
                       "match count suggests"),
            "claim_type": ClaimType.OBSERVATION.value,
        }
        return [summary, *out[-5:]]

    def _cross_check(self, symbol: str, as_of: date,
                     bundle: Any) -> list[dict[str, Any]]:
        """Reconcile any conflicting values across sources."""
        try:
            candidates = self.data.conflicting_values(symbol, as_of)
        except AttributeError:
            return []
        out: list[dict[str, Any]] = []
        for metric, values in (candidates or {}).items():
            conflict = SENT.reconcile(metric, values)
            if conflict.resolution is None or "hierarchy" in conflict.note:
                out.append(conflict.to_dict())
        return out

    def _fundamental_summary(self, bundle: Any) -> dict[str, Any]:
        if not bundle.annual:
            return {}
        from research_engine.features import fundamental as FUND
        snapshot = FUND.build_snapshot(
            bundle.symbol, bundle.as_of, bundle.annual, quarterly=bundle.quarterly,
            market_cap=bundle.market.get("market_cap_usd"), sector=bundle.sector)
        return {k: v.value for k, v in snapshot.metrics.items() if v.available}

    def _valuation_assumptions(self, result: RecommendationResult) -> dict[str, Any]:
        rationale = result.to_dict().get("ensemble") or {}
        return {"model_version": result.model_version,
                "horizon": result.horizon.value,
                "note": "see the valuation section of the recommendation payload"}

    def _technical_summary(self, bundle: Any) -> dict[str, Any]:
        if bundle.series is None:
            return {}
        import numpy as np
        from research_engine.features import technical as TECH
        indicators = TECH.compute_all(bundle.series)
        out: dict[str, Any] = {}
        for key in ("sma_200", "rsi", "adx", "atr", "bb_percent_b", "drawdown"):
            values = indicators.get(key)
            if values is None or len(values) == 0 or not np.isfinite(values[-1]):
                continue
            out[key] = round(float(values[-1]), 4)
        if "sma_200" in out and bundle.series.last_close:
            out["price_vs_200dma"] = round(
                bundle.series.last_close / out["sma_200"] - 1, 4)
        return out

    def _sources(self, bundle: Any) -> list[str]:
        sources: set[str] = set()
        for points in bundle.annual.values():
            for point in points:
                sources.add(f"{point.source} ({point.source_tier.value})")
        for item in bundle.news:
            tier = getattr(item, "source_tier", None)
            sources.add(f"{getattr(item, 'source', 'news')} "
                        f"({tier.value if tier else 'unknown'})")
        if bundle.series is not None:
            sources.add("price history (data provider)")
        return sorted(sources)
