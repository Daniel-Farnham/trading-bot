from unittest.mock import MagicMock, patch

import pytest

from src.data.market import MarketData


class FakeAccount:
    equity = "100000.00"
    cash = "50000.00"
    buying_power = "100000.00"
    portfolio_value = "100000.00"
    currency = "USD"


class FakePosition:
    symbol = "AAPL"
    qty = "10"
    avg_entry_price = "150.00"
    current_price = "155.00"
    market_value = "1550.00"
    unrealized_pl = "50.00"
    unrealized_plpc = "0.0333"


class FakeClock:
    is_open = True


class FakeBar:
    close = 155.50


@patch("src.data.market.TradingClient")
@patch("src.data.market.StockHistoricalDataClient")
class TestMarketData:
    def _make_market(self, mock_data_client, mock_trading_client) -> MarketData:
        return MarketData(api_key="test_key", secret_key="test_secret")

    def test_get_account(self, mock_data_cls, mock_trading_cls):
        mock_trading_cls.return_value.get_account.return_value = FakeAccount()
        market = self._make_market(mock_data_cls, mock_trading_cls)

        account = market.get_account()
        assert account["equity"] == 100000.0
        assert account["cash"] == 50000.0
        assert account["currency"] == "USD"

    def test_get_positions(self, mock_data_cls, mock_trading_cls):
        mock_trading_cls.return_value.get_all_positions.return_value = [FakePosition()]
        market = self._make_market(mock_data_cls, mock_trading_cls)

        positions = market.get_positions()
        assert len(positions) == 1
        assert positions[0]["ticker"] == "AAPL"
        assert positions[0]["qty"] == 10
        assert positions[0]["avg_entry"] == 150.0

    def test_get_positions_empty(self, mock_data_cls, mock_trading_cls):
        mock_trading_cls.return_value.get_all_positions.return_value = []
        market = self._make_market(mock_data_cls, mock_trading_cls)

        positions = market.get_positions()
        assert positions == []

    def test_get_position_found(self, mock_data_cls, mock_trading_cls):
        mock_trading_cls.return_value.get_open_position.return_value = FakePosition()
        market = self._make_market(mock_data_cls, mock_trading_cls)

        pos = market.get_position("AAPL")
        assert pos is not None
        assert pos["ticker"] == "AAPL"

    def test_get_position_not_found(self, mock_data_cls, mock_trading_cls):
        mock_trading_cls.return_value.get_open_position.side_effect = Exception("Not found")
        market = self._make_market(mock_data_cls, mock_trading_cls)

        pos = market.get_position("AAPL")
        assert pos is None

    def test_is_market_open(self, mock_data_cls, mock_trading_cls):
        mock_trading_cls.return_value.get_clock.return_value = FakeClock()
        market = self._make_market(mock_data_cls, mock_trading_cls)

        assert market.is_market_open() is True

    def test_get_latest_price(self, mock_data_cls, mock_trading_cls):
        mock_data_cls.return_value.get_stock_latest_bar.return_value = {"AAPL": FakeBar()}
        market = self._make_market(mock_data_cls, mock_trading_cls)

        price = market.get_latest_price("AAPL")
        assert price == 155.50

    def test_get_latest_price_failure_returns_none(self, mock_data_cls, mock_trading_cls):
        mock_data_cls.return_value.get_stock_latest_bar.side_effect = Exception("API error")
        market = self._make_market(mock_data_cls, mock_trading_cls)

        price = market.get_latest_price("AAPL")
        assert price is None

    def test_get_latest_prices(self, mock_data_cls, mock_trading_cls):
        mock_data_cls.return_value.get_stock_latest_bar.return_value = {
            "AAPL": FakeBar(),
            "MSFT": FakeBar(),
        }
        market = self._make_market(mock_data_cls, mock_trading_cls)

        prices = market.get_latest_prices(["AAPL", "MSFT"])
        assert len(prices) == 2
        assert prices["AAPL"] == 155.50
