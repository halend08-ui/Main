import pytest

from research_engine.config.settings import (default_settings, load_settings,
                                             normalise_weights)
from research_engine.core.errors import ConfigError


def test_defaults_load_and_validate():
    s = default_settings()
    assert s.get("app.name") == "research-engine"
    assert s.provider_chain("prices_eod")
    assert abs(sum(s.scoring_weights().values()) - 1.0) < 1e-9
    assert abs(sum(s.scoring_weights("crypto").values()) - 1.0) < 1e-9


def test_environment_overrides_are_typed():
    s = load_settings(environ={
        "RE__SCORING__THRESHOLDS__STRONG": "72",
        "RE__UNIVERSE__MIN_MARKET_CAP_USD": "1e9",
        "RE__APP__LOG_JSON": "true",
    })
    assert s.get("scoring.thresholds.strong") == 72
    assert s.get("universe.min_market_cap_usd") == pytest.approx(1e9)
    assert s.get("app.log_json") is True


def test_trading_is_gated_off():
    with pytest.raises(ConfigError):
        default_settings({"app": {"allow_trading": True}})


def test_threshold_monotonicity_enforced():
    with pytest.raises(ConfigError):
        default_settings({"scoring": {"thresholds": {"strong": 10}}})


def test_weights_must_be_positive():
    with pytest.raises(ConfigError):
        normalise_weights({"a": 0.0, "b": 0.0})
    with pytest.raises(ConfigError):
        normalise_weights({"a": -1.0})


def test_secrets_only_come_from_environment(tmp_path):
    s = load_settings(environ={"COINGECKO_API_KEY": "super-secret-value"})
    assert s.secrets.get("COINGECKO_API_KEY") == "super-secret-value"
    assert s.secrets.get("DOES_NOT_EXIST") is None
    with pytest.raises(ConfigError):
        s.secrets.get("DOES_NOT_EXIST", required=True)
    # the default config file must never contain a literal key
    text = (tmp_path / "x").parent  # noqa: F841 - path unused, readability
    from research_engine.config import settings as settings_module
    raw = settings_module._DEFAULT_FILE.read_text()
    assert "api_key:" not in raw


def test_default_embargo_covers_the_default_label_horizon():
    """A default run must not be the thing the leakage check warns about."""
    from research_engine.quality.bias import check_label_horizon_embargo

    s = default_settings()
    horizon = int(s.get("backtest.label_horizon_days"))
    embargo = int(s.get("backtest.embargo_days"))
    assert check_label_horizon_embargo(horizon, embargo) is None
