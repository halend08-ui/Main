"""Learning-layer tests: outcome grading, bucketed performance, guarded
weight updates, promotion policy and model reproducibility."""

import datetime as dt

import pytest

from research_engine.analysis.probability import Calibrator
from research_engine.core.series import PriceSeries
from research_engine.learning import evaluation as EVAL
from research_engine.learning import performance as PERF
from research_engine.learning import retrain as RT
from research_engine.learning.registry import (ModelRegistry, ModelSpec,
                                               code_fingerprint, next_version)
from research_engine.storage.analysis_repos import ModelRegistryRepository


def _series(start_price=100.0, end_price=120.0, days=200,
            start=dt.date(2025, 1, 1)):
    step = (end_price / start_price) ** (1 / (days - 1))
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    return PriceSeries.from_closes("X", dates, [start_price * step ** i
                                                for i in range(days)])


def _prediction(**overrides):
    base = {"id": 1, "symbol": "X", "as_of": "2025-01-01", "due_at": "2025-04-01",
            "price_at_prediction": 100.0, "recommendation": "BUY",
            "confidence": 0.6, "prob_positive": 0.65, "expected_return": 0.15,
            "expected_downside": -0.15, "horizon": "3m", "asset_class": "equity",
            "factors": {"growth": 0.4, "valuation": 0.3}}
    base.update(overrides)
    return base


# ------------------------------------------------------------ evaluation ---
def test_prediction_not_graded_before_its_horizon():
    outcome = EVAL.evaluate_prediction(_prediction(), _series(),
                                       as_of=dt.date(2025, 2, 1))
    assert outcome is None


def test_successful_buy_is_graded_succeeded():
    outcome = EVAL.evaluate_prediction(_prediction(), _series(100, 140),
                                       as_of=dt.date(2025, 12, 1))
    assert outcome.hit is True
    assert outcome.actual_return > 0
    assert outcome.thesis_outcome == "succeeded"


def test_being_right_for_the_wrong_reason_is_recorded_as_luck():
    market = _series(100, 130)          # the whole market rose
    outcome = EVAL.evaluate_prediction(
        _prediction(expected_return=0.40), _series(100, 105), benchmark=market,
        as_of=dt.date(2025, 12, 1))
    assert outcome.actual_return > 0
    assert outcome.excess_return < 0
    assert outcome.thesis_outcome == "luck"
    assert "market" in outcome.failure_reason


def test_hold_predictions_make_no_directional_claim():
    outcome = EVAL.evaluate_prediction(_prediction(recommendation="HOLD"),
                                       _series(100, 103), as_of=dt.date(2025, 12, 1))
    assert outcome.hit is None          # not counted as a hit or a miss
    assert outcome.thesis_outcome == "succeeded"


def test_drawdown_during_the_holding_period_is_measured():
    dates = [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(120)]
    path = ([100 - i for i in range(60)] + [40 + i * 2 for i in range(60)])
    series = PriceSeries.from_closes("X", dates, path)
    outcome = EVAL.evaluate_prediction(
        _prediction(due_at="2025-04-20"), series, as_of=dt.date(2025, 12, 1))
    assert outcome.max_drawdown < -0.5
    assert outcome.thesis_outcome in ("partial", "succeeded", "failed")


def test_missing_price_at_due_date_is_flagged_not_dropped():
    short = _series(100, 110, days=30)          # history stops long before due
    outcome = EVAL.evaluate_prediction(_prediction(due_at="2026-04-01"), short,
                                       as_of=dt.date(2026, 6, 1))
    assert outcome is not None
    assert outcome.notes


def test_factor_attribution_sums_to_direction():
    outcome = EVAL.evaluate_prediction(_prediction(), _series(100, 130),
                                       as_of=dt.date(2025, 12, 1))
    attribution = EVAL.factor_attribution(_prediction(), outcome)
    assert set(attribution) == {"growth", "valuation"}
    assert all(v > 0 for v in attribution.values())     # positive outcome
    assert sum(attribution.values()) == pytest.approx(1.0)


# ----------------------------------------------------------- performance ---
def _records(n=200, hit_rate=0.6, asset_class="equity", regime="bull",
             confidence=0.7, seed=1):
    import random
    rng = random.Random(seed)
    out = []
    for i in range(n):
        win = rng.random() < hit_rate
        ret = rng.uniform(0.02, 0.3) if win else rng.uniform(-0.3, -0.02)
        out.append({"id": i, "actual_return": ret, "excess_return": ret - 0.02,
                    "hit": win, "prob_positive": confidence,
                    "confidence": confidence, "asset_class": asset_class,
                    "regime": regime, "horizon": "1y", "recommendation": "BUY",
                    "sector": "Technology", "data_quality": "good",
                    "thesis_outcome": "succeeded" if win else "failed",
                    "factors": {"growth": 0.5 if win else -0.2}})
    return out


def test_small_buckets_report_insufficient_rather_than_a_number():
    buckets = PERF.compute(_records(10))
    assert all(not b.sufficient for b in buckets)
    assert all(b.hit_rate is None for b in buckets)
    assert all("needed before performance means anything" in b.note for b in buckets)


def test_performance_is_bucketed_by_regime_and_class():
    records = _records(150, hit_rate=0.7, regime="bull") + \
              _records(150, hit_rate=0.3, regime="bear", seed=2)
    buckets = {(b.kind, b.value): b for b in PERF.compute(records)}
    assert buckets[("regime", "bull")].hit_rate > 0.6
    assert buckets[("regime", "bear")].hit_rate < 0.4


def test_systematic_errors_identified():
    records = _records(200, hit_rate=0.3, regime="bear")
    findings = PERF.systematic_errors(PERF.compute(records))
    assert any(f["issue"] == "low_hit_rate" for f in findings)
    assert any(f["severity"] == "high" for f in findings)


def test_returns_from_beta_are_called_out():
    records = _records(200, hit_rate=0.8)
    for record in records:
        record["thesis_outcome"] = "luck"
    findings = PERF.systematic_errors(PERF.compute(records))
    assert any(f["issue"] == "returns_from_beta" for f in findings)


def test_confidence_must_track_accuracy():
    low = _records(120, hit_rate=0.75, confidence=0.45, seed=3)
    high = _records(120, hit_rate=0.35, confidence=0.85, seed=4)
    check = PERF.confidence_is_informative(PERF.compute(low + high))
    assert check["assessable"]
    assert not check["monotone"]
    assert "does NOT track accuracy" in check["verdict"]


def test_measured_base_rates_replace_priors():
    records = _records(300, hit_rate=0.72)
    rates = PERF.measured_base_rates(records, min_samples=100)
    assert rates["equity"]["1y"] > 0.6
    assert PERF.measured_base_rates(_records(20)) == {}


# -------------------------------------------------------------- retrain ----
def test_weights_are_not_touched_without_enough_evidence():
    proposal = RT.propose_weights({"growth": 0.5, "valuation": 0.5}, {},
                                  min_samples=100, total_samples=20)
    assert not proposal.accepted
    assert "required before touching weights" in proposal.rejection_reason
    assert proposal.proposed == proposal.current


def test_effective_factors_gain_weight_within_the_cap():
    records = _records(400, hit_rate=0.65)
    effectiveness = RT.factor_effectiveness(records, min_samples=100)
    assert effectiveness["growth"]["sufficient"]
    proposal = RT.propose_weights({"growth": 0.5, "valuation": 0.5}, effectiveness,
                                  max_change=0.25, min_samples=100, total_samples=400)
    assert proposal.accepted
    assert proposal.proposed["growth"] > proposal.current["growth"]
    assert max(abs(v) for v in proposal.changes.values()) <= 0.25
    assert proposal.rationale


def test_factor_effectiveness_is_labelled_association_not_causation():
    records = _records(300)
    effectiveness = RT.factor_effectiveness(records, min_samples=100)
    assert "not evidence that the factor caused" in \
           effectiveness["growth"]["interpretation"]


def test_promotion_requires_samples_and_a_margin():
    candidate = {"samples": 50, "avg_excess": 0.10}
    assert not RT.should_promote(candidate, {"samples": 500, "avg_excess": 0.01},
                                 candidate_version="v2", incumbent_version="v1").promote

    marginal = {"samples": 400, "avg_excess": 0.011}
    decision = RT.should_promote(marginal, {"samples": 500, "avg_excess": 0.01},
                                 candidate_version="v2", incumbent_version="v1")
    assert not decision.promote and "hurdle" in decision.reason

    clear = {"samples": 400, "avg_excess": 0.05}
    assert RT.should_promote(clear, {"samples": 500, "avg_excess": 0.01},
                             candidate_version="v2", incumbent_version="v1").promote


def test_better_returns_do_not_beat_worse_calibration():
    candidate = {"samples": 400, "avg_excess": 0.08, "calibration_error": 0.20}
    incumbent = {"samples": 500, "avg_excess": 0.02, "calibration_error": 0.04}
    decision = RT.should_promote(candidate, incumbent, candidate_version="v2",
                                 incumbent_version="v1")
    assert not decision.promote
    assert "calibrated" in decision.reason


# ------------------------------------------------------------- registry ----
def test_version_numbering_never_reuses(db):
    repo = ModelRegistryRepository(db)
    assert next_version([], "scoring") == "scoring_v1"
    assert next_version(["scoring_v1", "scoring_v3"], "scoring") == "scoring_v4"


def test_registry_records_and_promotes(db, tmp_path):
    registry = ModelRegistry(ModelRegistryRepository(db), artifacts_dir=tmp_path)
    spec1 = ModelSpec("scoring", "scoring_v1", {"growth": 0.5}, ("growth",))
    spec2 = ModelSpec("scoring", "scoring_v2", {"growth": 0.6}, ("growth",))
    registry.register(spec1, status="active")
    registry.register(spec2)
    assert registry.active_version("scoring") == "scoring_v1"
    registry.promote("scoring_v2", reason="better out of sample")
    assert registry.active_version("scoring") == "scoring_v2"
    # the retired version is kept, never deleted
    assert any(m["version"] == "scoring_v1" and m["status"] == "retired"
               for m in registry.history("scoring"))
    assert (tmp_path / "scoring_v1.json").exists()


def test_fingerprint_changes_with_parameters():
    a = ModelSpec("scoring", "v1", {"growth": 0.5})
    b = ModelSpec("scoring", "v1", {"growth": 0.6})
    assert a.fingerprint() != b.fingerprint()
    assert a.fingerprint() == ModelSpec("scoring", "v9", {"growth": 0.5}).fingerprint()


def test_reproducibility_report_detects_code_drift(db):
    registry = ModelRegistry(ModelRegistryRepository(db))
    registry.register(ModelSpec("scoring", "scoring_v1", {"a": 1}),
                      code_hash="abc123")
    same = registry.reproducibility_report("scoring_v1", current_code_hash="abc123")
    drifted = registry.reproducibility_report("scoring_v1", current_code_hash="zzz999")
    assert same["reproducible"] and drifted["reproducible"] is False
    assert "cannot be reproduced exactly" in drifted["note"]


def test_code_fingerprint_is_stable():
    from research_engine.analysis import scoring
    assert code_fingerprint(scoring.compose) == code_fingerprint(scoring.compose)
    assert code_fingerprint(scoring.compose) != code_fingerprint(scoring.tier_for)
