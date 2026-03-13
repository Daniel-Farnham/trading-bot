from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.execution.broker import Broker, OrderResult
from src.strategy.risk_v3 import PositionPlan


def _make_plan(**overrides) -> PositionPlan:
    defaults = {
        "ticker": "AAPL",
        "quantity": 10,
        "entry_price": 150.0,
        "stop_loss": 144.0,
        "take_profit": 159.0,
        "risk_amount": 60.0,
        "position_value": 1500.0,
        "risk_pct": 0.006,
    }
    defaults.update(overrides)
    return PositionPlan(**defaults)


@patch("src.execution.broker.TradingClient")
class TestBroker:
    def _make_broker(self, mock_client_cls) -> Broker:
        return Broker(api_key="test_key", secret_key="test_secret")

    def test_place_bracket_order_success(self, mock_client_cls):
        mock_order = MagicMock()
        mock_order.id = "order_123"
        mock_order.filled_avg_price = "150.50"
        mock_client_cls.return_value.submit_order.return_value = mock_order

        broker = self._make_broker(mock_client_cls)
        plan = _make_plan()
        result = broker.place_bracket_order(plan)

        assert result.success is True
        assert result.order_id == "order_123"
        assert result.filled_price == 150.50
        mock_client_cls.return_value.submit_order.assert_called_once()

    def test_place_bracket_order_failure(self, mock_client_cls):
        mock_client_cls.return_value.submit_order.side_effect = Exception("Insufficient funds")

        broker = self._make_broker(mock_client_cls)
        result = broker.place_bracket_order(_make_plan())

        assert result.success is False
        assert "Insufficient funds" in result.error

    def test_place_bracket_order_no_fill_price(self, mock_client_cls):
        mock_order = MagicMock()
        mock_order.id = "order_456"
        mock_order.filled_avg_price = None
        mock_client_cls.return_value.submit_order.return_value = mock_order

        broker = self._make_broker(mock_client_cls)
        result = broker.place_bracket_order(_make_plan())

        assert result.success is True
        assert result.filled_price is None

    def test_place_market_sell_success(self, mock_client_cls):
        mock_order = MagicMock()
        mock_order.id = "sell_789"
        mock_order.filled_avg_price = "152.00"
        mock_client_cls.return_value.submit_order.return_value = mock_order

        broker = self._make_broker(mock_client_cls)
        result = broker.place_market_sell("AAPL", 10)

        assert result.success is True
        assert result.order_id == "sell_789"

    def test_place_market_sell_failure(self, mock_client_cls):
        mock_client_cls.return_value.submit_order.side_effect = Exception("No position")

        broker = self._make_broker(mock_client_cls)
        result = broker.place_market_sell("AAPL", 10)

        assert result.success is False

    def test_close_position_success(self, mock_client_cls):
        broker = self._make_broker(mock_client_cls)
        result = broker.close_position("AAPL")

        assert result.success is True
        mock_client_cls.return_value.close_position.assert_called_once_with("AAPL")

    def test_close_position_failure(self, mock_client_cls):
        mock_client_cls.return_value.close_position.side_effect = Exception("No position")

        broker = self._make_broker(mock_client_cls)
        result = broker.close_position("AAPL")

        assert result.success is False

    def test_cancel_all_orders(self, mock_client_cls):
        broker = self._make_broker(mock_client_cls)
        assert broker.cancel_all_orders() is True
        mock_client_cls.return_value.cancel_orders.assert_called_once()

    def test_cancel_all_orders_failure(self, mock_client_cls):
        mock_client_cls.return_value.cancel_orders.side_effect = Exception("API error")

        broker = self._make_broker(mock_client_cls)
        assert broker.cancel_all_orders() is False

    def test_get_order(self, mock_client_cls):
        mock_order = MagicMock()
        mock_order.id = "order_123"
        mock_order.status = "filled"
        mock_order.symbol = "AAPL"
        mock_order.qty = "10"
        mock_order.filled_qty = "10"
        mock_order.filled_avg_price = "150.50"
        mock_order.side = "buy"
        mock_order.type = "limit"
        mock_client_cls.return_value.get_order_by_id.return_value = mock_order

        broker = self._make_broker(mock_client_cls)
        order = broker.get_order("order_123")

        assert order is not None
        assert order["id"] == "order_123"
        assert order["status"] == "filled"

    def test_get_order_not_found(self, mock_client_cls):
        mock_client_cls.return_value.get_order_by_id.side_effect = Exception("Not found")

        broker = self._make_broker(mock_client_cls)
        assert broker.get_order("bad_id") is None
