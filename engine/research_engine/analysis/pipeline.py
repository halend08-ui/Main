"""Per-asset analysis orchestration.

This is the function that turns raw stored data into a full recommendation:

    load (point-in-time) -> validate -> features -> factor scores -> ensemble
    -> risk -> valuation -> probability -> gates -> recommendation

It is deliberately a plain function over an explicit input bundle rather than a
class with hidden state, so the same code path runs live, in a backtest replay
and in a unit test with synthetic inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from research_engine.core.logging import get_logger
from research_engine.core.numeric import clamp, safe_div
from research_engine.core.series import PriceSeries
from research_engine.core.types import (AssetClass, ClaimType, DataQuality, Evidence,
                                        Horizon, MarketRegime, OpportunityTier,
                                        Recommendation, RiskLevel)
from research_engine.analysis import ensemble as E
from research_engine.analysis import events as EV
from research_engine.analysis import probability as P
from research_engine.analysis import recommendation as REC
from research_engine.analysis import risk as RK
from research_engine.analysis import scoring as SC
from research_engine.analysis import sell as SELL
from research_engine.analysis import sentiment as SENT
from research_engine.features import fundamental as FUND
from research_engine.features import returns as RET
from research_engine.features import technical as TECH
from research_engine.features import valuation as VAL
from research_engine.quality import checks as QC
from research_engine.quality import grading as QG

log = get_logger(__name__)

MODEL_VERSION_DEFAULT = "scoring_v1"


@dataclass
class AnalysisInput:
    """Everything the analyser needs, already filtered to the as-of date."""

    symbol: str
    as_of: date
    asset_class: AssetClass
    series: PriceSeries | None = None
    benchmark: PriceSeries | None = None
    annual: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    quarterly: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    news: Sequence[Any] = ()
    market: Mapping[str, Any] = field(default_factory=dict)
    crypto_snapshot: Any = None
    macro_adjustment: float = 0.0
    regime: MarketRegime = MarketRegime.UNKNOWN
    regime_adjustment: float = 0.0
    sector: str | None = None
    peer_multiples: Mapping[str, Sequence[float]] = field(default_factory=dict)
    own_multiple_history: Mapping[str, Sequence[float]] = field(default_factory=dict)
    previous: Mapping[str, Any] | None = None
    held: bool = False
    unlocks: Sequence[Mapping[str, Any]] = ()
    earnings_date: date | None = None
    calibrator: P.Calibrator | None = None
    learned_base_rates: Mapping[str, Mapping[str, float]] | None = None
    settings: Any = None
    model_version: str = MODEL_VERSION_DEFAULT
    data_version: str | None = None


def analyze(inp: AnalysisInput) -> REC.RecommendationResult:
    """Run the full per-asset analysis. Never raises on missing data."""
    is_crypto = inp.asset_class is AssetClass.CRYPTO
    horizon = _horizon(inp)
    evidence: list[Evidence] = []
    catalysts: list[str] = []
    risks: list[str] = []

    # ---------------------------------------------------- data quality ----
    reports: list[QC.QualityReport] = []
    if inp.series is not None:
        price_report = QG.finalize(QC.check_price_series(
            inp.series, as_of=inp.as_of,
            max_staleness_days=_setting(inp, "quality.max_crypto_price_staleness_days"
                                        if is_crypto else "quality.max_price_staleness_days", 5),
            max_daily_move_abs=_setting(inp, "quality.max_crypto_daily_move_abs"
                                        if is_crypto else "quality.max_daily_move_abs", 0.6),
            min_history_days=_setting(inp, "quality.min_history_days_for_analysis", 120),
            is_crypto=is_crypto))
        reports.append(price_report)
    if inp.annual:
        reports.append(QG.finalize(QC.check_fundamentals(
            inp.annual, subject=inp.symbol, as_of=inp.as_of)))
    if inp.news:
        reports.append(QG.finalize(QC.check_news(inp.news, subject=inp.symbol,
                                                 as_of=inp.as_of)))
    quality_score, data_quality = QG.combine(reports) if reports else (
        0.0, DataQuality.INSUFFICIENT)

    if not reports or data_quality is DataQuality.INSUFFICIENT:
        return _insufficient(inp, horizon, data_quality,
                             [r.to_dict() for r in reports])

    # --------------------------------------------------------- features ---
    price = inp.series.last_close if inp.series is not None else inp.market.get("price_usd")
    indicators = TECH.compute_all(inp.series,
                                  config=_technical_config(inp)) if inp.series else {}
    stats = (RET.summarize(inp.series, risk_free_rate=_risk_free(inp),
                           benchmark=inp.benchmark)
             if inp.series is not None and len(inp.series) >= 60 else {})

    snapshot = None
    if inp.annual and not is_crypto:
        snapshot = FUND.build_snapshot(inp.symbol, inp.as_of, inp.annual,
                                       quarterly=inp.quarterly,
                                       market_cap=inp.market.get("market_cap_usd"),
                                       sector=inp.sector)
        evidence.extend(snapshot.evidence)

    # ------------------------------------------------------- valuation ----
    valuation = _valuation(inp, snapshot, price, stats)
    if valuation is not None:
        evidence.extend(VAL.valuation_evidence(valuation))
    scenario_returns = valuation.expected_returns() if valuation else {}

    # ------------------------------------------------------------ risk ----
    risk_profile = RK.build_profile(
        inp.symbol, inp.as_of, inp.series, benchmark=inp.benchmark,
        solvency=_solvency_inputs(snapshot, inp, is_crypto),
        liquidity_inputs=_liquidity_inputs(inp, indicators),
        is_crypto=is_crypto, risk_free_rate=_risk_free(inp))
    evidence.extend(RK.risk_evidence(risk_profile))
    risks.extend(risk_profile.drivers[:5])
    risks.extend(risk_profile.unknowns[:2])

    # ---------------------------------------------- news, events, moods ----
    sentiment = SENT.analyse(inp.news, as_of=inp.as_of) if inp.news else None
    if sentiment is not None:
        evidence.extend(SENT.sentiment_evidence(sentiment))
    detected = EV.detect(inp.news) if inp.news else []
    event_risk = EV.aggregate_event_risk(detected, as_of=inp.as_of)
    evidence.extend(EV.event_evidence(detected, as_of=inp.as_of))
    for item in event_risk.get("thesis_changing", []):
        risks.append(f"thesis-relevant event: {item}")
    catalysts.extend(
        f"{c['type'].replace('_', ' ')} on {c['date']} ({c['days_away']}d away)"
        for c in EV.upcoming_catalysts(earnings_date=inp.earnings_date,
                                       unlocks=inp.unlocks, as_of=inp.as_of))

    if inp.crypto_snapshot is not None:
        evidence.extend(inp.crypto_snapshot.evidence)
        risks.extend(inp.crypto_snapshot.risks[:5])

    # ---------------------------------------------------- factor scores ----
    factors = _factor_scores(inp, snapshot, indicators, stats, valuation, sentiment,
                             event_risk, risk_profile, is_crypto)
    thresholds = _thresholds(inp)
    composite = SC.compose(
        factors, thresholds=thresholds,
        max_missing_weight=_setting(inp, "scoring.max_missing_factor_ratio", 0.4),
        data_quality=data_quality,
        min_quality_for_top_tier=_min_quality(inp))

    # -------------------------------------------------------- ensemble ----
    views = E.build_views(
        factor_scores={f.name: f.score for f in factors},
        rationales=_view_rationales(snapshot, valuation, risk_profile, sentiment,
                                    stats=stats, indicators=indicators,
                                    returns_by_window=(
                                        RET.horizon_returns(inp.series,
                                                            (30, 91, 182, 365))
                                        if inp.series is not None else {})))
    ensemble = E.combine(views)
    evidence.extend(E.ensemble_evidence(ensemble))

    # ----------------------------------------------------- probability ----
    forecast = P.forecast(
        asset_class=inp.asset_class.value, horizon=horizon, score=composite.total,
        ensemble_agreement=ensemble.agreement, scenario_returns=scenario_returns,
        data_quality=data_quality, regime_adjustment=inp.regime_adjustment,
        macro_adjustment=inp.macro_adjustment,
        risk_penalty=(risk_profile.permanent_loss_score or 0.0) * 0.2,
        learned_base_rates=inp.learned_base_rates, calibrator=inp.calibrator,
        observations=len(inp.series) if inp.series is not None else None)

    # ---------------------------------------------------------- exits -----
    sell_conditions = SELL.build_conditions(
        price=price,
        fair_value_base=valuation.base if valuation else None,
        fair_value_bear=valuation.bear if valuation else None,
        revenue_growth=snapshot.value("revenue_cagr_1y") if snapshot else None,
        operating_margin=snapshot.value("operating_margin") if snapshot else None,
        interest_coverage=snapshot.value("interest_coverage") if snapshot else None,
        fcf=snapshot.value("free_cash_flow") if snapshot else None,
        atr=_last(indicators.get("atr")),
        stop_atr_multiple=_setting(inp, "risk.stop_atr_multiple", 3.0),
        is_crypto=is_crypto,
        unlock_pct=(inp.crypto_snapshot.value("unlock_pct_180d")
                    if inp.crypto_snapshot else None),
        horizon_months=max(1, horizon.days // 30))

    sell_evaluation = SELL.evaluate(sell_conditions, _live_metrics(
        price, snapshot, risk_profile, indicators, inp))
    evidence.extend(SELL.sell_evidence(sell_evaluation))

    # --------------------------------------------------------- decision ---
    recommendation, gate_failures = REC.decide(
        score=composite, risk=risk_profile, probability=forecast, ensemble=ensemble,
        data_quality=data_quality,
        expected_return_base=scenario_returns.get("base"),
        bear_case_return=scenario_returns.get("bear"),
        previous=_previous_recommendation(inp),
        sell_triggered=sell_evaluation.should_sell and inp.held,
        min_quality_for_buy=_min_quality(inp),
        min_confidence=_setting(inp, "analysis.min_confidence_to_recommend", 0.35),
        held=inp.held)

    invalidation = SELL.build_invalidation(
        evidence_labels=[e.label for e in evidence],
        moat_verdict=(snapshot.metrics["moat_score"].detail.get("verdict")
                      if snapshot and "moat_score" in snapshot.metrics else None),
        growth_assumption=snapshot.value("revenue_cagr_3y") if snapshot else None,
        margin_assumption=snapshot.value("operating_margin") if snapshot else None,
        key_risks=risks)

    bear_case = REC.build_bear_case(
        evidence=evidence, risk=risk_profile,
        bear_value=valuation.bear if valuation else None, price=price,
        ensemble=ensemble,
        fragile_assumption=_fragile_assumption(valuation))
    bull_case = REC.build_bull_case(
        evidence=evidence, bull_value=valuation.bull if valuation else None,
        price=price, catalysts=catalysts)

    result = REC.RecommendationResult(
        symbol=inp.symbol, as_of=inp.as_of, recommendation=recommendation,
        previous=_previous_recommendation(inp), tier=composite.tier,
        score=composite.total, confidence=forecast.confidence, horizon=horizon,
        price=price,
        fair_value={"bear": valuation.bear if valuation else None,
                    "base": valuation.base if valuation else None,
                    "bull": valuation.bull if valuation else None},
        expected_return={k: scenario_returns.get(k) for k in ("bear", "base", "bull")},
        prob_positive=forecast.prob_positive, risk_level=risk_profile.level,
        data_quality=data_quality, evidence=evidence, catalysts=catalysts,
        risks=risks, invalidation=invalidation, sell_conditions=sell_conditions,
        bear_case=bear_case, bull_case=bull_case, gates_failed=gate_failures,
        model_version=inp.model_version, data_version=inp.data_version,
        factor_scores={f.name: f.score for f in factors},
        ensemble=ensemble)
    if recommendation is Recommendation.INSUFFICIENT_DATA:
        # The phrase is contractual: callers and readers look for exactly this.
        result.risks.insert(0, "Insufficient reliable data.")
    if not result.risks:
        # "No risks" is never an honest answer; say what was checked instead.
        result.risks.append(
            "No specific risk driver crossed a threshold; residual market, "
            "execution and valuation risk still apply")
    result.changes = REC.describe_changes(result.to_dict(), inp.previous)
    return result


# ----------------------------------------------------------- internals -----
def _insufficient(inp: AnalysisInput, horizon: Horizon, quality: DataQuality,
                  reports: Sequence[Mapping[str, Any]]) -> REC.RecommendationResult:
    reasons = []
    for report in reports:
        for issue in report.get("issues", []):
            if issue.get("severity") in ("FATAL", "ERROR"):
                reasons.append(f"{report.get('scope')}: {issue.get('message')}")
    if not reasons:
        reasons.append("no usable data for this asset")
    return REC.RecommendationResult(
        symbol=inp.symbol, as_of=inp.as_of,
        recommendation=Recommendation.INSUFFICIENT_DATA, previous=None,
        tier=OpportunityTier.WATCH, score=None, confidence=0.0, horizon=horizon,
        price=inp.series.last_close if inp.series is not None else None,
        fair_value={"bear": None, "base": None, "bull": None},
        expected_return={"bear": None, "base": None, "bull": None},
        prob_positive=None, risk_level=RiskLevel.EXTREME, data_quality=quality,
        gates_failed=reasons[:6], model_version=inp.model_version,
        risks=["Insufficient reliable data."])


def _setting(inp: AnalysisInput, path: str, default: Any) -> Any:
    if inp.settings is None:
        return default
    value = inp.settings.get(path, default)
    return default if value is None else value


def _risk_free(inp: AnalysisInput) -> float:
    if inp.settings is None:
        return 0.04
    return float(inp.settings.risk_free_rate())


def _horizon(inp: AnalysisInput) -> Horizon:
    raw = _setting(inp, "analysis.default_horizon", "1y")
    try:
        return Horizon(str(raw))
    except ValueError:
        return Horizon.Y1


def _thresholds(inp: AnalysisInput) -> dict[str, float]:
    section = _setting(inp, "scoring.thresholds", None)
    if section is None:
        return {"exceptional": 82, "strong": 70, "moderate": 58, "watch": 45}
    return {k: float(v) for k, v in section.items()}


def _min_quality(inp: AnalysisInput) -> DataQuality:
    raw = _setting(inp, "scoring.min_quality_for_buy", "good")
    try:
        return DataQuality(str(raw))
    except ValueError:
        return DataQuality.GOOD


def _technical_config(inp: AnalysisInput) -> dict[str, Any]:
    section = _setting(inp, "analysis.technical", None)
    if section is None:
        return {}
    return {k: v for k, v in section.items()}


def _last(array: Any) -> float | None:
    if array is None or len(array) == 0:
        return None
    import numpy as np
    value = array[-1]
    return float(value) if np.isfinite(value) else None


def _valuation(inp: AnalysisInput, snapshot: Any, price: float | None,
               stats: Mapping[str, Any]) -> VAL.ValuationScenarios | None:
    """Build a valuation view where the inputs support one."""
    if inp.asset_class is AssetClass.CRYPTO:
        return None      # equity DCF/multiples do not apply to tokens
    if snapshot is None or price is None:
        return None
    fcf = snapshot.value("free_cash_flow")
    shares = None
    for metric in ("shares_diluted", "shares_outstanding"):
        points = inp.annual.get(metric) or []
        if points and points[-1].value:
            shares = float(points[-1].value)
            break
    if not shares and inp.market.get("market_cap_usd") and price:
        shares = float(inp.market["market_cap_usd"]) / price
    if not fcf or not shares or fcf <= 0:
        return None

    beta = stats.get("beta")
    settings = inp.settings
    risk_free = _risk_free(inp)
    erp = float(_setting(inp, "analysis.equity_risk_premium", 0.05))
    size_premium = 0.0
    market_cap = inp.market.get("market_cap_usd")
    if market_cap and market_cap < 2e9:
        size_premium = 0.02      # documented small-cap adjustment
    discount_rate, note = VAL.cost_of_equity(
        risk_free_rate=risk_free, equity_risk_premium=erp, beta=beta,
        size_premium=size_premium,
        min_rate=float(_setting(inp, "analysis.valuation.min_discount_rate", 0.06)),
        max_rate=float(_setting(inp, "analysis.valuation.max_discount_rate", 0.20)))

    growth = snapshot.value("revenue_cagr_3y")
    if growth is None:
        growth = snapshot.value("revenue_cagr_5y") or 0.03
    growth = clamp(growth, -0.10, 0.35)      # cap fantasy growth in the base case

    scenarios = VAL.scenario_valuation(
        base_fcf=fcf, shares=shares,
        net_debt=snapshot.value("net_debt") or 0.0, growth=growth,
        discount_rate=discount_rate,
        terminal_growth=float(_setting(inp, "analysis.valuation.terminal_growth", 0.025)),
        price=price, years=int(_setting(inp, "analysis.valuation.dcf_years", 10)),
        scenario_config=_scenario_config(inp))
    scenarios = VAL.ValuationScenarios(
        bear=scenarios.bear, base=scenarios.base, bull=scenarios.bull,
        method=scenarios.method, price=price,
        assumptions={**scenarios.assumptions, "discount_rate_note": note},
        warnings=scenarios.warnings)

    # Cross-check with a multiples view when peers exist.
    ebitda = None
    if snapshot.value("operating_margin") is not None:
        operating = (inp.annual.get("operating_income") or [None])[-1]
        da = (inp.annual.get("depreciation_amortization") or [None])[-1]
        if operating is not None and getattr(operating, "value", None):
            ebitda = float(operating.value) + float(getattr(da, "value", 0) or 0)
    peers = inp.peer_multiples.get("ev_ebitda") or []
    if ebitda and len(peers) >= 3:
        from research_engine.core.numeric import median, percentile
        multiples_view = VAL.multiples_valuation(
            metric_value=ebitda, multiple_bear=percentile(peers, 0.25),
            multiple_base=median(peers), multiple_bull=percentile(peers, 0.75),
            shares=shares, net_debt=snapshot.value("net_debt") or 0.0, price=price,
            metric_name="ev_ebitda")
        return VAL.blend([scenarios, multiples_view], weights=[1.3, 1.0])
    return scenarios


def _scenario_config(inp: AnalysisInput) -> dict[str, dict[str, float]] | None:
    section = _setting(inp, "analysis.valuation.scenarios", None)
    if section is None:
        return None
    out: dict[str, dict[str, float]] = {}
    for name in ("bear", "base", "bull"):
        block = section.get(name)
        if block is None:
            continue
        out[name] = {k: float(v) for k, v in block.items()}
    return out or None


def _solvency_inputs(snapshot: Any, inp: AnalysisInput,
                     is_crypto: bool) -> dict[str, Any]:
    if is_crypto:
        crypto = inp.crypto_snapshot
        return {"crypto_risk": crypto.value("risk_overall") if crypto else None,
                "quality_grade": inp.market.get("quality_grade"),
                "market_cap": inp.market.get("market_cap_usd")}
    if snapshot is None:
        return {"market_cap": inp.market.get("market_cap_usd")}
    fcf = snapshot.value("free_cash_flow")
    return {
        "interest_coverage": snapshot.value("interest_coverage"),
        "net_debt_to_ebitda": snapshot.value("debt_to_ebitda"),
        "cash_runway_years": snapshot.value("cash_runway_years"),
        "altman_z": snapshot.value("altman_z"),
        "dilution_rate": snapshot.value("share_cagr_5y"),
        "fcf_positive": None if fcf is None else fcf > 0,
        "market_cap": inp.market.get("market_cap_usd"),
    }


def _liquidity_inputs(inp: AnalysisInput, indicators: Mapping[str, Any]) -> dict[str, Any]:
    dollar_volume = None
    if inp.series is not None and len(inp.series) >= 20:
        import numpy as np
        window = (inp.series.close * inp.series.volume)[-20:]
        finite = window[np.isfinite(window)]
        if finite.size >= 10:
            dollar_volume = float(np.median(finite))
    return {"avg_dollar_volume": dollar_volume,
            "turnover": (inp.crypto_snapshot.value("turnover")
                         if inp.crypto_snapshot else None),
            "exchange_count": inp.market.get("exchange_count"),
            "participation_cap": _setting(inp, "risk.liquidity_participation_cap", 0.05)}


def _factor_scores(inp: AnalysisInput, snapshot: Any, indicators: Mapping[str, Any],
                   stats: Mapping[str, Any], valuation: Any, sentiment: Any,
                   event_risk: Mapping[str, Any], risk_profile: RK.RiskProfile,
                   is_crypto: bool) -> list[SC.FactorScore]:
    weights = (inp.settings.scoring_weights("crypto" if is_crypto else "equity")
               if inp.settings is not None
               else ({"tokenomics": 0.2, "network_activity": 0.15, "liquidity": 0.15,
                      "momentum": 0.15, "valuation": 0.1, "developer_activity": 0.05,
                      "concentration_risk": 0.1, "event_risk": 0.05, "sentiment": 0.03,
                      "downside_risk": 0.02} if is_crypto
                     else {"fundamental_quality": 0.2, "growth": 0.15, "valuation": 0.2,
                           "momentum": 0.12, "technical_structure": 0.06,
                           "financial_health": 0.12, "competitive_advantage": 0.05,
                           "capital_allocation": 0.04, "macro_environment": 0.02,
                           "event_risk": 0.02, "sentiment": 0.01, "liquidity": 0.01,
                           "downside_risk": 0.0}))
    factors: list[SC.FactorScore] = []

    def add(name: str, score: float | None, detail: Mapping[str, Any],
            quality: DataQuality = DataQuality.GOOD) -> None:
        weight = float(weights.get(name, 0.0))
        if weight <= 0:
            return
        factors.append(SC.FactorScore(
            name=name, score=score, weight=weight, quality=quality,
            detail=dict(detail), reason=str(detail.get("reason", ""))))

    returns_by_window = {}
    if inp.series is not None:
        returns_by_window = RET.horizon_returns(inp.series, (30, 91, 182, 365))
    momentum_score, momentum_detail = SC.score_momentum(
        returns_by_window=returns_by_window,
        relative_strength=_relative_strength(inp),
        reversal_1m=returns_by_window.get(30))
    add("momentum", momentum_score, momentum_detail)

    technical_score, technical_detail = SC.score_technical(
        price_vs_200dma=_price_vs_ma(inp, indicators),
        rsi=_last(indicators.get("rsi")),
        macd_hist=_last(indicators.get("macd_hist")),
        adx=_last(indicators.get("adx")),
        above_donchian=_above_donchian(inp, indicators))
    add("technical_structure", technical_score, technical_detail)

    liquidity_score, liquidity_detail = SC.score_liquidity(
        dollar_volume=_liquidity_inputs(inp, indicators)["avg_dollar_volume"],
        turnover=(inp.crypto_snapshot.value("turnover") if inp.crypto_snapshot else None),
        market_cap=inp.market.get("market_cap_usd"))
    add("liquidity", liquidity_score, liquidity_detail)

    downside_score, downside_detail = SC.score_downside_risk(
        max_drawdown=stats.get("max_drawdown"), volatility=stats.get("volatility"),
        var_95=stats.get("var_95"),
        bear_case_return=(valuation.expected_returns().get("bear")
                          if valuation else None))
    add("downside_risk", downside_score, downside_detail)

    add("event_risk", event_risk.get("score"),
        {"events": event_risk.get("events_considered", 0),
         "reason": event_risk.get("note", "")})
    add("sentiment",
        None if sentiment is None or sentiment.score is None
        else clamp(50.0 + sentiment.score * 45.0, 0.0, 100.0),
        {"items": getattr(sentiment, "items_scored", 0),
         "reason": "sentiment is weak evidence and weighted accordingly"},
        DataQuality.FAIR)
    add("macro_environment", 50.0 + inp.macro_adjustment * 200.0,
        {"regime": inp.regime.value, "adjustment": inp.macro_adjustment},
        DataQuality.FAIR)

    if is_crypto and inp.crypto_snapshot is not None:
        crypto = inp.crypto_snapshot
        risk_detail = crypto.metrics.get("risk_overall")
        factors_detail = (risk_detail.detail.get("factors", {}) if risk_detail else {})
        add("tokenomics", _invert(factors_detail.get("tokenomics")),
            {"mcap_to_fdv": crypto.value("mcap_to_fdv"),
             "emission_rate": crypto.value("emission_rate")})
        add("network_activity", _network_score(crypto),
            {"fees_annualised": crypto.value("fees_annualised"),
             "tvl": crypto.value("tvl")})
        add("developer_activity", _developer_score(inp.market),
            {"commits_4w": inp.market.get("developer_commits_4w")})
        add("concentration_risk", _invert(factors_detail.get("centralization")),
            {"reason": "holder concentration proxy"})
        add("valuation", _crypto_valuation_score(crypto),
            {"mcap_to_fees": crypto.value("mcap_to_fees"),
             "mcap_to_tvl": crypto.value("mcap_to_tvl")})
    elif snapshot is not None:
        quality_score, quality_detail = SC.score_quality(
            roic=snapshot.value("roic"), roe=snapshot.value("roe"),
            gross_margin=snapshot.value("gross_margin"),
            fcf_conversion=snapshot.value("fcf_conversion"),
            margin_trend=snapshot.value("operating_margin_trend_3y"))
        add("fundamental_quality", quality_score, quality_detail)

        growth_score, growth_detail = SC.score_growth(
            snapshot.value("revenue_cagr_3y"), snapshot.value("revenue_consistency"),
            snapshot.value("revenue_acceleration"))
        add("growth", growth_score, growth_detail)

        health_score, health_detail = SC.score_financial_health(
            debt_to_equity=snapshot.value("debt_to_equity"),
            interest_coverage=snapshot.value("interest_coverage"),
            current_ratio=snapshot.value("current_ratio"),
            net_debt_to_ebitda=snapshot.value("debt_to_ebitda"),
            cash_runway_years=snapshot.value("cash_runway_years"),
            altman_z=snapshot.value("altman_z"))
        add("financial_health", health_score, health_detail)

        moat = snapshot.metrics.get("moat_score")
        add("competitive_advantage",
            None if moat is None or moat.value is None else moat.value * 100.0,
            {"verdict": moat.detail.get("verdict") if moat else None}, DataQuality.FAIR)

        add("capital_allocation", _capital_allocation_score(snapshot),
            {"share_cagr_5y": snapshot.value("share_cagr_5y"),
             "fcf_conversion": snapshot.value("fcf_conversion")})

        valuation_score, valuation_detail = SC.score_valuation(
            fcf_yield=safe_div(snapshot.value("free_cash_flow"),
                               inp.market.get("market_cap_usd")),
            earnings_yield=None, pe=None, ev_ebitda=None, peg=None,
            history_percentile=None, peer_percentile=None,
            dcf_upside=(valuation.expected_returns().get("base") if valuation else None))
        add("valuation", valuation_score, valuation_detail)
    else:
        add("fundamental_quality", None, {"reason": "no fundamental data"})
        add("growth", None, {"reason": "no fundamental data"})
        add("financial_health", None, {"reason": "no fundamental data"})
        add("valuation", None, {"reason": "no fundamental data"})
        add("competitive_advantage", None, {"reason": "no fundamental data"})
        add("capital_allocation", None, {"reason": "no fundamental data"})
    return factors


def _invert(value: float | None) -> float | None:
    """Risk factor (0..1, higher worse) -> score (0..100, higher better)."""
    return None if value is None else clamp((1.0 - value) * 100.0, 0.0, 100.0)


def _network_score(crypto: Any) -> float | None:
    fee_multiple = crypto.value("mcap_to_fees")
    tvl_multiple = crypto.value("mcap_to_tvl")
    trend = crypto.value("active_addresses_trend_30d")
    parts: list[float] = []
    if fee_multiple is not None and fee_multiple > 0:
        parts.append(clamp(100.0 - fee_multiple, 0.0, 100.0))
    if tvl_multiple is not None and tvl_multiple > 0:
        parts.append(clamp(100.0 - tvl_multiple * 20.0, 0.0, 100.0))
    if trend is not None:
        parts.append(clamp(50.0 + trend * 120.0, 0.0, 100.0))
    if not parts:
        return None
    return float(sum(parts) / len(parts))


def _developer_score(market: Mapping[str, Any]) -> float | None:
    commits = market.get("developer_commits_4w")
    contributors = market.get("developer_contributors")
    if commits is None and contributors is None:
        return None
    parts = []
    if commits is not None:
        parts.append(clamp(float(commits) * 1.2, 0.0, 100.0))
    if contributors is not None:
        parts.append(clamp(float(contributors) * 3.0, 0.0, 100.0))
    return float(sum(parts) / len(parts))


def _crypto_valuation_score(crypto: Any) -> float | None:
    fee_multiple = crypto.value("mcap_to_fees")
    if fee_multiple is None or fee_multiple <= 0:
        return None
    return clamp(100.0 - fee_multiple * 0.8, 0.0, 100.0)


def _capital_allocation_score(snapshot: Any) -> float | None:
    dilution = snapshot.value("share_cagr_5y")
    conversion = snapshot.value("fcf_conversion")
    sbc = snapshot.value("sbc_pct_revenue")
    parts: list[float] = []
    if dilution is not None:
        parts.append(clamp(60.0 - dilution * 400.0, 0.0, 100.0))
    if conversion is not None:
        parts.append(clamp(30.0 + conversion * 55.0, 0.0, 100.0))
    if sbc is not None:
        parts.append(clamp(90.0 - sbc * 400.0, 0.0, 100.0))
    if not parts:
        return None
    return float(sum(parts) / len(parts))


def _price_vs_ma(inp: AnalysisInput, indicators: Mapping[str, Any]) -> float | None:
    ma = _last(indicators.get("sma_200"))
    if ma is None or inp.series is None or ma <= 0:
        return None
    return inp.series.last_close / ma - 1.0


def _above_donchian(inp: AnalysisInput, indicators: Mapping[str, Any]) -> bool | None:
    upper = _last(indicators.get("donchian_upper"))
    if upper is None or inp.series is None:
        return None
    return inp.series.last_close > upper


def _relative_strength(inp: AnalysisInput) -> float | None:
    if inp.series is None or inp.benchmark is None:
        return None
    from research_engine.core.series import align
    a, b, _ = align(inp.series, inp.benchmark)
    if len(a) < 130:
        return None
    return float((a[-1] / a[-126]) - (b[-1] / b[-126]))


def _view_rationales(snapshot: Any, valuation: Any, risk_profile: RK.RiskProfile,
                     sentiment: Any, *, stats: Mapping[str, Any] | None = None,
                     indicators: Mapping[str, Any] | None = None,
                     returns_by_window: Mapping[int, float | None] | None = None
                     ) -> dict[str, str]:
    """One plain sentence per model view, phrased from that model's perspective.

    The risk view's stance is about *how favourable the risk picture is*, so its
    rationale must be written that way. Printing "bullish: prior drawdown of
    48%" reads as though a large drawdown were good news.
    """
    out: dict[str, str] = {}
    if snapshot is not None:
        roic = snapshot.value("roic")
        growth = snapshot.value("revenue_cagr_3y")
        out["fundamental"] = (
            f"ROIC {roic:.0%}, revenue CAGR {growth:.0%}" if roic is not None
            and growth is not None else "partial fundamental data")
    if valuation is not None and valuation.base is not None:
        base = valuation.expected_returns().get("base")
        out["valuation"] = (f"{valuation.method} implies {base:+.0%} to fair value"
                            if base is not None else valuation.method)

    stats = stats or {}
    risk_parts: list[str] = []
    volatility = stats.get("volatility")
    drawdown = stats.get("max_drawdown")
    if volatility is not None:
        risk_parts.append(f"annualised volatility {volatility:.0%}")
    if drawdown is not None:
        risk_parts.append(f"worst historical drawdown {abs(drawdown):.0%}")
    if risk_profile.permanent_loss_score is not None:
        risk_parts.append(
            f"permanent-loss risk {risk_profile.permanent_loss_score:.2f}")
    out["risk"] = (f"assessed {risk_profile.level.value} risk: "
                   + ", ".join(risk_parts)) if risk_parts else \
                  f"assessed {risk_profile.level.value} risk"

    returns_by_window = returns_by_window or {}
    momentum_parts = [f"{label}: {returns_by_window[window]:+.0%}"
                      for label, window in (("3m", 91), ("6m", 182), ("12m", 365))
                      if returns_by_window.get(window) is not None]
    if momentum_parts:
        out["momentum"] = "trailing return " + ", ".join(momentum_parts)

    indicators = indicators or {}
    technical_parts: list[str] = []
    rsi = _last(indicators.get("rsi"))
    if rsi is not None:
        technical_parts.append(f"RSI {rsi:.0f}")
    adx = _last(indicators.get("adx"))
    if adx is not None:
        technical_parts.append(f"ADX {adx:.0f} "
                               f"({'trending' if adx > 25 else 'no clear trend'})")
    if technical_parts:
        out["technical"] = ", ".join(technical_parts)

    if sentiment is not None and sentiment.score is not None:
        out["sentiment"] = (f"{sentiment.items_scored} items, net "
                            f"{sentiment.score:+.2f}")
    return out


def _live_metrics(price: float | None, snapshot: Any, risk_profile: RK.RiskProfile,
                  indicators: Mapping[str, Any], inp: AnalysisInput
                  ) -> dict[str, float | None]:
    return {
        "price": price,
        "revenue_growth_ttm": snapshot.value("revenue_cagr_1y") if snapshot else None,
        "operating_margin": snapshot.value("operating_margin") if snapshot else None,
        "free_cash_flow_ttm": snapshot.value("free_cash_flow") if snapshot else None,
        "interest_coverage": snapshot.value("interest_coverage") if snapshot else None,
        "permanent_loss_score": risk_profile.permanent_loss_score,
        "fair_value_base": None,
        "avg_dollar_volume": _liquidity_inputs(inp, indicators)["avg_dollar_volume"],
        "unlock_pct_60d": (inp.crypto_snapshot.value("unlock_pct_180d")
                           if inp.crypto_snapshot else None),
        "months_held_without_progress": None,
    }


def _fragile_assumption(valuation: Any) -> str | None:
    if valuation is None:
        return None
    assumptions = valuation.assumptions or {}
    growth = assumptions.get("revenue_growth_year1")
    if growth is not None:
        return (f"the {float(growth):.0%} first-year cash-flow growth the base case "
                f"requires")
    return None


def _previous_recommendation(inp: AnalysisInput) -> Recommendation | None:
    if not inp.previous:
        return None
    raw = inp.previous.get("recommendation")
    if not raw:
        return None
    try:
        return Recommendation(str(raw))
    except ValueError:
        return None
