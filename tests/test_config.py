import os

import pytest

from src.config import get_alpaca_keys, load_config


class TestLoadConfig:
    def test_loads_default_config(self):
        config = load_config()
        assert "alpaca" in config
        assert "watchlist" in config
        assert "trading" in config
        assert "scheduler" in config
        assert "database" in config

    def test_watchlist_has_symbols(self):
        config = load_config()
        symbols = config["watchlist"]["symbols"]
        assert isinstance(symbols, list)
        assert len(symbols) > 0
        assert "NVDA" in symbols

    def test_trading_params_exist(self):
        config = load_config()
        trading = config["trading"]
        assert "max_position_pct" in trading
        assert "max_open_positions" in trading
        assert "sentiment_buy_threshold" in trading


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
