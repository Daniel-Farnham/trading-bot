import os

import pytest

from src.config import get_alpaca_keys, load_config


class TestLoadConfig:
    def test_loads_default_config(self):
        config = load_config()
        assert "alpaca" in config
        assert "universe" in config
        assert "trading" in config

    def test_universe_has_themed_stocks(self):
        config = load_config()
        universe = config["universe"]
        assert "ai_technology" in universe
        assert "healthcare_aging" in universe
        assert "energy_climate" in universe
        assert "finance" in universe
        assert "consumer_inequality" in universe
        assert "discovery_pool" in universe
        # Check a few key tickers
        assert "NVDA" in universe["ai_technology"]
        assert "LLY" in universe["healthcare_aging"]
        assert "JPM" in universe["finance"]
        assert "BA" in universe["discovery_pool"]

    def test_universe_total_size(self):
        config = load_config()
        all_tickers = set()
        for theme_tickers in config["universe"].values():
            all_tickers.update(theme_tickers)
        assert len(all_tickers) >= 60

    def test_trading_params_exist(self):
        config = load_config()
        trading = config["trading"]
        assert "max_position_pct" in trading
        assert "max_open_positions" in trading


class TestGetAlpacaKeys:
    def test_returns_keys_when_set(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY", "my_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "my_secret")
        key, secret = get_alpaca_keys()
        assert key == "my_key"
        assert secret == "my_secret"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(EnvironmentError):
            get_alpaca_keys()
