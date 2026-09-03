"""Analysis-layer tests: scoring behaviour, risk classification, ensemble
disagreement, calibration, sell logic and end-to-end recommendation gates."""

import datetime as dt

import pytest

from research_engine.analysis import anomaly as AN
from research_engine.analysis import ensemble as E
from research_engine.analysis import events as EV
from research_engine.analysis import probability as P
from research_engine.analysis import recommendation as REC
from research_engine.analysis import risk as RK
from research_engine.analysis import scoring as SC
from research_engine.analysis import sell as SELL
from research_engine.analysis import sentiment as SENT
from research_engine.analysis.pipeline import AnalysisInput, analyze
from research_engine.core.series import PriceSeries
from research_engine.core.types import (AssetClass, DataQuality, EventImpact, Horizon,
                                        OpportunityTier, Period, Recommendation,
                                        RiskLevel, SourceTier)
from research_engine.storage.repositories import FundamentalPoint
from tests.test_features import COMPOUNDER


# -------------------------------------------------------------- scoring ----
def test_missing_factors_are_excluded_not_zeroed():
    factors = [
        SC.FactorScore("growth", 80.0, 0.5, DataQuality.GOOD),
        SC.FactorScore("valuation", None, 0.5, DataQuality.INSUFFICIENT),
    ]
    composite = SC.compose(factors, thresholds={"watch": 45, "moderate": 58,
                                                "strong": 70, "exceptional": 82},
                           max_missing_weight=0.6)
    assert composite.coverage == pytest.approx(0.5)
    # 80 minus the coverage haircut, NOT (80 + 0) / 2 = 40
    assert composite.total > 65
    assert "valuation" in composite.missing_factors()


def test_score_withheld_when_too_much_evidence_is_missing():
    factors = [
        SC.FactorScore("growth", 80.0, 0.3, DataQuality.GOOD),
        SC.FactorScore("valuation", None, 0.4, DataQuality.INSUFFICIENT),
        SC.FactorScore("health", None, 0.3, DataQuality.INSUFFICIENT),
    ]
    composite = SC.compose(factors, thresholds={"watch": 45, "moderate": 58,
                                                "strong": 70, "exceptional": 82},
                           max_missing_weight=0.4)
    assert composite.total is None
    assert composite.quality is DataQuality.INSUFFICIENT


def test_implausible_cheapness_is_penalised():
    fair, _ = SC.score_valuation(fcf_yield=0.10, earnings_yield=None, pe=None,
                                 ev_ebitda=None, peg=None)
    absurd, _ = SC.score_valuation(fcf_yield=0.60, earnings_yield=None, pe=None,
                                   ev_ebitda=None, peg=None)
    assert absurd < fair          # a 60% FCF yield signals distress, not value


def test_growth_score_caps_implausible_rates():
    high, _ = SC.score_growth(0.40, 1.0, None)
    absurd, _ = SC.score_growth(2.50, 1.0, None)
    assert absurd - high < 12     # diminishing credit above ~45%


def test_top_tier_requires_data_quality():
    factors = [SC.FactorScore("growth", 95.0, 1.0, DataQuality.FAIR)]
    composite = SC.compose(factors,
                           thresholds={"watch": 45, "moderate": 58, "strong": 70,
                                       "exceptional": 82},
                           data_quality=DataQuality.FAIR,
                           min_quality_for_top_tier=DataQuality.GOOD)
    assert composite.tier is OpportunityTier.MODERATE
    assert any("capped" in n for n in composite.notes)


def test_cross_sectional_percentiles_need_a_sample():
    small = SC.cross_sectional_percentiles({"A": 1.0, "B": 2.0})
    assert all(v is None for v in small.values())
    big = SC.cross_sectional_percentiles({f"S{i}": float(i) for i in range(30)})
    assert big["S29"] == pytest.approx(1.0)


# ----------------------------------------------------------------- risk ----
def test_permanent_loss_dominates_risk_level():
    level, drivers = RK.classify_risk_level(volatility=0.15, max_drawdown=-0.10,
                                            permanent_loss=0.85, liquidity=0.1)
    assert level is RiskLevel.EXTREME
    assert any("permanent" in d for d in drivers)


def test_unknown_solvency_raises_risk_rather_than_lowering_it():
    level, drivers = RK.classify_risk_level(volatility=0.15, max_drawdown=-0.08,
                                            permanent_loss=None, liquidity=0.1)
    assert level >= RiskLevel.ELEVATED
    assert any("could not be assessed" in d or "unavailable" in d for d in drivers)


def test_cash_burn_drives_permanent_loss_risk():
    result = RK.permanent_loss_risk(cash_runway_years=0.8, fcf_positive=False,
                                    interest_coverage=0.5)
    assert result["score"] > 0.7
    assert any("runway" in r for r in result["reasons"]), result["reasons"]


def test_liquidity_risk_reports_days_to_exit():
    result = RK.liquidity_risk(avg_dollar_volume=200_000, position_size_usd=500_000)
    assert result["days_to_exit"] == pytest.approx(50.0)
    assert result["score"] >= 0.8


def test_crypto_volatility_thresholds_are_wider(price_bars):
    equity_level, _ = RK.classify_risk_level(volatility=0.7, max_drawdown=-0.3,
                                             permanent_loss=0.2, liquidity=0.2)
    crypto_level, _ = RK.classify_risk_level(volatility=0.7, max_drawdown=-0.3,
                                             permanent_loss=0.2, liquidity=0.2,
                                             is_crypto=True)
    assert equity_level > crypto_level


def test_portfolio_risk_reports_coverage(price_bars):
    series = {s: PriceSeries.from_rows(s, price_bars(300, seed=i))
              for i, s in enumerate(["A", "B", "C"])}
    result = RK.portfolio_risk({"A": 0.5, "B": 0.3, "C": 0.2}, series)
    assert result["volatility"] > 0
    assert result["coverage"] == pytest.approx(1.0)
    assert result["effective_positions"] > 1


def test_limit_breaches_are_explicit():
    breaches = RK.limit_breaches({"A": 0.5, "B": 0.25, "C": 0.25},
                                 sectors={"A": "Tech", "B": "Tech", "C": "Energy"},
                                 asset_classes={"C": "crypto"})
    assert any("A is 50.0%" in b for b in breaches)
    assert any("Tech" in b for b in breaches)
    assert any("crypto" in b for b in breaches)


# ------------------------------------------------------------- ensemble ----
def test_ensemble_surfaces_disagreement():
    views = [
        E.ModelView("fundamental", E.Stance.STRONGLY_BULLISH, 0.8, 90),
        E.ModelView("valuation", E.Stance.STRONGLY_BEARISH, 0.8, 15),
        E.ModelView("risk", E.Stance.BEARISH, 0.7, 30),
    ]
    result = E.combine(views)
    assert result.agreement < 0.5
    assert result.conflicts
    assert any("value trap" in c or "bullish vs" in c for c in result.conflicts)


def test_no_single_model_can_dominate():
    views = [
        E.ModelView("sentiment", E.Stance.STRONGLY_BULLISH, 1.0, 100),
        E.ModelView("fundamental", E.Stance.BEARISH, 0.5, 30),
        E.ModelView("valuation", E.Stance.BEARISH, 0.5, 30),
    ]
    result = E.combine(views, max_single_weight=0.35)
    assert result.consensus in (E.Stance.NEUTRAL, E.Stance.BEARISH)


def test_abstaining_models_are_reported():
    views = [E.ModelView("event", E.Stance.NO_VIEW, 0.0),
             E.ModelView("fundamental", E.Stance.BULLISH, 0.7, 70)]
    result = E.combine(views)
    assert result.abstained == ["event"]


# ---------------------------------------------------------- probability ----
def test_probability_starts_from_base_rate_without_evidence():
    forecast = P.forecast(asset_class="equity", horizon=Horizon.Y1, score=None)
    assert forecast.prob_positive == pytest.approx(forecast.base_rate, abs=0.06)
    assert any("base rate" in c for c in forecast.caveats)


def test_poor_data_pulls_probability_toward_half():
    strong = P.forecast(asset_class="equity", horizon=Horizon.Y1, score=95,
                        data_quality=DataQuality.EXCELLENT)
    weak = P.forecast(asset_class="equity", horizon=Horizon.Y1, score=95,
                      data_quality=DataQuality.POOR)
    assert abs(weak.prob_positive - 0.5) < abs(strong.prob_positive - 0.5)
    assert weak.confidence < strong.confidence


def test_disagreement_shrinks_probability():
    agree = P.forecast(asset_class="equity", horizon=Horizon.Y1, score=90,
                       ensemble_agreement=1.0)
    disagree = P.forecast(asset_class="equity", horizon=Horizon.Y1, score=90,
                          ensemble_agreement=0.1)
    assert disagree.prob_positive < agree.prob_positive


def test_deviation_from_base_rate_is_capped():
    extreme = P.forecast(asset_class="crypto", horizon=Horizon.Y1, score=100,
                         regime_adjustment=0.5, macro_adjustment=0.5,
                         data_quality=DataQuality.EXCELLENT)
    assert abs(extreme.prob_positive - extreme.base_rate) <= P.MAX_TOTAL_DEVIATION + 1e-9


def test_scenario_probabilities_sum_to_one():
    forecast = P.forecast(asset_class="equity", horizon=Horizon.Y1, score=75,
                          scenario_returns={"bear": -0.3, "base": 0.15, "bull": 0.5})
    assert sum(forecast.scenario_probabilities.values()) == pytest.approx(1.0)
    assert forecast.expected_return["probability_weighted"] is not None


def test_calibrator_corrects_overconfidence():
    import random
    rng = random.Random(11)
    predictions, outcomes = [], []
    for _ in range(600):
        p = rng.uniform(0.55, 0.95)
        predictions.append(p)
        outcomes.append(rng.random() < p * 0.6)     # systematically overconfident
    calibrator = P.Calibrator.fit(predictions, outcomes)
    assert calibrator.is_fitted
    assert calibrator.apply(0.9) < 0.9
    assert calibrator.calibration_error() > 0.1
    assert calibrator.overconfidence_penalty() > 0


def test_calibrator_is_monotone():
    import random
    rng = random.Random(3)
    predictions = [rng.uniform(0.05, 0.95) for _ in range(800)]
    outcomes = [rng.random() < p for p in predictions]
    calibrator = P.Calibrator.fit(predictions, outcomes)
    values = [calibrator.apply(p) for p in [0.1, 0.3, 0.5, 0.7, 0.9]]
    assert values == sorted(values)


def test_calibrator_refuses_tiny_samples():
    assert not P.Calibrator.fit([0.6] * 5, [True] * 5).is_fitted


def test_brier_skill_detects_a_useless_model():
    import random
    rng = random.Random(5)
    outcomes = [rng.random() < 0.6 for _ in range(500)]
    useless = [0.6] * 500                       # always the base rate
    skill = P.brier_skill_score(useless, outcomes)
    assert skill is not None and abs(skill) < 0.05


# ------------------------------------------------------------ sentiment ----
def test_negation_flips_sentiment():
    positive, _ = SENT.score_text("Company beats estimates")
    negated, _ = SENT.score_text("Company did not beat estimates")
    assert positive > 0 > negated


def test_hype_discounts_positive_sentiment():
    class Item:
        def __init__(self, headline, summary=None,
                     tier=SourceTier.SOCIAL_MEDIA):
            self.headline, self.summary, self.source_tier = headline, summary, tier
            self.published_at = dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)

    hyped = SENT.analyse([Item("MOON!!! 100x guaranteed, must buy now", "record growth"),
                          Item("Stock will skyrocket", "record profit surge"),
                          Item("Insane gains ahead", "strong growth")])
    assert hyped.hype_score > 0.3
    assert any("hype" in w for w in hyped.warnings)


def test_sentiment_withheld_when_too_thin():
    class Item:
        headline = "Company announces something"
        summary = None
        source_tier = SourceTier.FINANCIAL_JOURNALISM
        published_at = dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)

    reading = SENT.analyse([Item()])
    assert reading.score is None
    assert reading.confidence <= 0.15


def test_source_hierarchy_resolves_conflicts():
    conflict = SENT.reconcile("revenue", [
        ("sec_edgar", 1000.0, SourceTier.REGULATORY_FILING, dt.date(2025, 2, 1)),
        ("blogger", 1500.0, SourceTier.SOCIAL_MEDIA, dt.date(2025, 3, 1)),
    ])
    assert conflict.resolution == 1000.0
    assert "hierarchy" in conflict.note


def test_equal_authority_conflict_left_unresolved():
    conflict = SENT.reconcile("revenue", [
        ("vendor_a", 1000.0, SourceTier.DATA_PROVIDER, None),
        ("vendor_b", 1400.0, SourceTier.DATA_PROVIDER, None),
    ])
    assert conflict.resolution is None
    assert "UNRESOLVED" in conflict.note


# --------------------------------------------------------------- events ----
def test_event_classification_and_thesis_relevance():
    fraud = EV.classify("Regulator opens fraud investigation into ACME",
                        source_tier=SourceTier.REGULATORY_FILING)
    assert fraud.impact is EventImpact.EXTREMELY_NEGATIVE
    assert fraud.changes_thesis

    launch = EV.classify("ACME launches new product line")
    assert launch.impact is EventImpact.POSITIVE
    assert not launch.changes_thesis

    assert EV.classify("ACME appears in a magazine feature") is None


def test_event_confidence_scales_with_source():
    filing = EV.classify("Company cuts guidance", source_tier=SourceTier.COMPANY_FILING)
    tweet = EV.classify("Company cuts guidance", source_tier=SourceTier.SOCIAL_MEDIA)
    assert filing.confidence > tweet.confidence


def test_event_aggregation_decays_with_age():
    old = EV.DetectedEvent("guidance_cut", EventImpact.EXTREMELY_NEGATIVE,
                           dt.datetime(2025, 1, 1), "cut", "wire",
                           SourceTier.FINANCIAL_JOURNALISM, 0.15, 120, True, 0.7)
    recent = EV.DetectedEvent("guidance_cut", EventImpact.EXTREMELY_NEGATIVE,
                              dt.datetime(2026, 1, 1), "cut", "wire",
                              SourceTier.FINANCIAL_JOURNALISM, 0.15, 120, True, 0.7)
    as_of = dt.date(2026, 1, 15)
    assert (EV.aggregate_event_risk([recent], as_of=as_of)["score"]
            <= EV.aggregate_event_risk([old], as_of=as_of)["score"])


# -------------------------------------------------------------- anomaly ----
def test_volume_spike_detected(price_bars):
    bars = price_bars(200)
    bars[-1]["volume"] = bars[-1]["volume"] * 20
    found = AN.volume_spike(PriceSeries.from_rows("X", bars))
    assert found is not None and found.kind == "volume_spike"
    assert "not a trading signal" in found.to_dict()["action"]


def test_price_anomaly_uses_robust_statistics(price_bars):
    bars = price_bars(300)
    bars[-1]["close"] = bars[-1]["adj_close"] = bars[-2]["close"] * 1.9
    found = AN.price_anomaly(PriceSeries.from_rows("X", bars))
    assert found is not None and found.severity > 0


def test_insider_cluster_requires_multiple_insiders():
    single = [{"holder": "A", "change_shares": 1000}] * 5
    assert AN.insider_cluster(single) is None
    multiple = [{"holder": h, "change_shares": 1000} for h in ("A", "B", "C")]
    assert AN.insider_cluster(multiple) is not None


# ----------------------------------------------------------------- sell ----
def test_sell_conditions_are_measurable():
    conditions = SELL.build_conditions(price=100.0, fair_value_base=120.0,
                                       fair_value_bear=70.0, revenue_growth=0.20,
                                       operating_margin=0.25, interest_coverage=8.0,
                                       fcf=500.0, atr=3.0)
    assert conditions
    assert all(c.metric and c.operator and c.threshold is not None for c in conditions)
    assert any(c.trigger is SELL.SellTrigger.THESIS_DETERIORATION for c in conditions)
    assert any(c.trigger is SELL.SellTrigger.VALUATION for c in conditions)


def test_price_decline_alone_only_triggers_review():
    conditions = SELL.build_conditions(price=100.0, fair_value_base=120.0,
                                       atr=2.0, max_drawdown_tolerance=0.25)
    evaluation = SELL.evaluate(conditions, {"price": 70.0})
    assert not evaluation.should_sell          # a price fall is not a sell reason
    assert evaluation.review


def test_thesis_deterioration_triggers_sell():
    conditions = SELL.build_conditions(price=100.0, fair_value_base=120.0,
                                       revenue_growth=0.20, interest_coverage=8.0,
                                       fcf=500.0)
    evaluation = SELL.evaluate(conditions, {"revenue_growth_ttm": -0.10,
                                            "interest_coverage": 1.2})
    assert evaluation.should_sell
    assert len(evaluation.breached) >= 2


def test_unevaluable_conditions_are_reported():
    conditions = SELL.build_conditions(price=100.0, fair_value_base=120.0,
                                       revenue_growth=0.2)
    evaluation = SELL.evaluate(conditions, {})
    assert evaluation.unevaluable
    assert not evaluation.should_sell          # unknown is not a breach


def test_opportunity_cost_requires_a_material_margin():
    marginal = SELL.opportunity_cost_check(current_expected_return=0.10, current_risk=0.2,
                                           alternative_expected_return=0.12,
                                           alternative_risk=0.2)
    assert not marginal["switch"]
    clear = SELL.opportunity_cost_check(current_expected_return=0.05, current_risk=0.3,
                                        alternative_expected_return=0.30,
                                        alternative_risk=0.2)
    assert clear["switch"]


def test_invalidation_is_about_assumptions_not_price():
    items = SELL.build_invalidation(evidence_labels=[], growth_assumption=0.2,
                                    margin_assumption=0.3, moat_verdict="wide",
                                    key_risks=["customer concentration"])
    assert any("growth" in i.lower() for i in items)
    assert not any("price falls" in i.lower() for i in items)


# ------------------------------------------------------ recommendations ----
def _make_input(price_bars, **overrides):
    bars = price_bars(700, start=dt.date(2023, 1, 2))
    series = PriceSeries.from_rows("ACME", bars)
    defaults = dict(
        symbol="ACME", as_of=series.end, asset_class=AssetClass.EQUITY,
        series=series, benchmark=PriceSeries.from_rows("SPY", price_bars(700, seed=99,
                                                                        start=dt.date(2023, 1, 2))),
        annual=COMPOUNDER, market={"market_cap_usd": 5e9}, sector="Technology")
    defaults.update(overrides)
    return AnalysisInput(**defaults)


def test_end_to_end_recommendation(price_bars, settings):
    result = analyze(_make_input(price_bars, settings=settings))
    assert result.recommendation in set(Recommendation)
    assert result.data_quality >= DataQuality.FAIR
    assert result.sell_conditions
    assert result.invalidation
    assert result.bear_case          # a bear case is always constructed
    rendered = result.render()
    for section in ("ASSET:", "Recommendation:", "Estimated Fair Value:",
                    "SELL / EXIT IF:", "Thesis Invalidation:", "Data Quality:",
                    "Model Version:"):
        assert section in rendered


def test_insufficient_data_never_becomes_a_hold(price_bars):
    tiny = PriceSeries.from_rows("THIN", price_bars(20, start=dt.date(2026, 1, 1)))
    result = analyze(AnalysisInput(symbol="THIN", as_of=tiny.end,
                                   asset_class=AssetClass.EQUITY, series=tiny))
    assert result.recommendation is Recommendation.INSUFFICIENT_DATA
    assert result.score is None
    assert "Insufficient reliable data." in result.risks


def test_buy_requires_quality_gate(price_bars, settings):
    result = analyze(_make_input(price_bars, settings=settings, annual={}))
    # with no fundamentals the coverage gate must prevent a BUY
    assert result.recommendation is not Recommendation.BUY


def test_changes_are_described_against_previous(price_bars, settings):
    previous = {"recommendation": "WATCH", "score": 55.0, "price": 50.0,
                "risk_level": "moderate", "data_quality": "good",
                "fair_value": {"base": 60.0}}
    result = analyze(_make_input(price_bars, settings=settings, previous=previous))
    assert result.changes
    assert result.changes != ["first analysis of this asset"]


def test_crypto_path_uses_crypto_analysis(price_bars, settings):
    from research_engine.features import crypto as C
    series = PriceSeries.from_rows("TOK", price_bars(500, weekdays_only=False, vol=0.05))
    snapshot = C.build_snapshot("TOK", series.end,
                                market={"market_cap_usd": 2e9, "volume_24h_usd": 5e7,
                                        "circulating_supply": 5e8, "max_supply": 1e9,
                                        "fully_diluted_valuation_usd": 4e9},
                                series=series, quality_grade="established")
    result = analyze(AnalysisInput(symbol="TOK", as_of=series.end,
                                   asset_class=AssetClass.CRYPTO, series=series,
                                   crypto_snapshot=snapshot,
                                   market={"market_cap_usd": 2e9},
                                   settings=settings))
    # crypto must not receive an equity DCF fair value
    assert result.fair_value["base"] is None
    assert result.recommendation in set(Recommendation)


def test_bear_scenario_applies_margin_and_discount_deltas():
    """A bear case that only trims growth is not a bear case."""
    from research_engine.features import valuation as V
    scenarios = V.scenario_valuation(
        base_fcf=1000, shares=100, net_debt=0, growth=0.15, discount_rate=0.09,
        terminal_growth=0.025, price=200,
        scenario_config={"bear": {"revenue_growth_delta": -0.06, "margin_delta": -0.04,
                                  "discount_delta": 0.025},
                         "base": {}, "bull": {}})
    # margin and discount deltas together must bite much harder than growth alone
    growth_only = V.scenario_valuation(
        base_fcf=1000, shares=100, net_debt=0, growth=0.15, discount_rate=0.09,
        terminal_growth=0.025, price=200,
        scenario_config={"bear": {"revenue_growth_delta": -0.06}, "base": {}, "bull": {}})
    assert scenarios.bear < growth_only.bear * 0.75


def test_rendered_output_contains_bear_and_bull_cases(price_bars, settings):
    result = analyze(_make_input(price_bars, settings=settings))
    rendered = result.render()
    assert "Bear Case:" in rendered and "Bull Case:" in rendered
    assert result.risks         # never empty


def test_bear_case_never_calls_an_increase_a_decline():
    """A bear value above the current price is not a 'decline'."""
    from research_engine.analysis.risk import RiskProfile

    profile = RiskProfile(symbol="X", as_of=dt.date(2026, 1, 5),
                          level=RiskLevel.MODERATE, volatility=0.2,
                          max_drawdown=-0.3, var_95=None, expected_shortfall=None,
                          permanent_loss_score=0.2, liquidity_score=0.2,
                          gap_risk=None)
    above = REC.build_bear_case(evidence=[], risk=profile, bear_value=120.0,
                                price=100.0, ensemble=None)
    # the fair-value sentence must not describe a higher number as a decline
    # (the separate sentence about historical drawdown legitimately says "decline")
    assert "38% decline" not in above
    assert "+20% versus" in above
    assert "not pessimistic enough" in above

    below = REC.build_bear_case(evidence=[], risk=profile, bear_value=70.0,
                                price=100.0, ensemble=None)
    assert "30% decline" in below
