"""Tests for live options: data client, contract selector, options broker, and executor routing."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.data.options_data import OptionsDataClient, OptionContract
from src.execution.options_broker import OptionsBroker
from src.execution.broker import OrderResult
from src.strategy.contract_selector import ContractSelector, SelectedContract


# === OCC Symbol Parsing ===


class TestOCCParsing:
    def test_parse_call(self):
        result = OptionsDataClient._parse_occ_symbol("NVDA250620C00140000")
        assert result["underlying"] == "NVDA"
        assert result["expiry"] == "2025-06-20"
        assert result["option_type"] == "call"
        assert result["strike"] == 140.0

    def test_parse_put(self):
        result = OptionsDataClient._parse_occ_symbol("AAPL250718P00200000")
        assert result["underlying"] == "AAPL"
        assert result["expiry"] == "2025-07-18"
        assert result["option_type"] == "put"
        assert result["strike"] == 200.0

    def test_parse_fractional_strike(self):
        result = OptionsDataClient._parse_occ_symbol("SPY250620C00550500")
        assert result["strike"] == 550.5

    def test_parse_short_symbol(self):
        result = OptionsDataClient._parse_occ_symbol("X")
        assert result["underlying"] == "X"


# === Contract Selector ===


def _make_chain():
    """Create a mock options chain."""
    return [
        OptionContract(
            symbol="NVDA250620C00130000", underlying="NVDA",
            option_type="call", strike=130.0, expiry="2025-06-20",
            bid=25.0, ask=26.0, mid=25.5, last=25.5,
            volume=500, open_interest=2000,
            implied_volatility=0.45, delta=0.72, gamma=0.01,
            theta=-0.15, vega=0.30,
        ),
        OptionContract(
            symbol="NVDA250620C00140000", underlying="NVDA",
            option_type="call", strike=140.0, expiry="2025-06-20",
            bid=18.0, ask=19.0, mid=18.5, last=18.5,
            volume=800, open_interest=3500,
            implied_volatility=0.42, delta=0.58, gamma=0.015,
            theta=-0.18, vega=0.28,
        ),
        OptionContract(
            symbol="NVDA250620C00150000", underlying="NVDA",
            option_type="call", strike=150.0, expiry="2025-06-20",
            bid=12.0, ask=13.0, mid=12.5, last=12.5,
            volume=1200, open_interest=5000,
            implied_volatility=0.40, delta=0.45, gamma=0.018,
            theta=-0.20, vega=0.25,
        ),
    ]


class TestContractSelector:
    def test_selects_atm_contract(self):
        data = MagicMock()
        data.get_chain_for_entry.return_value = _make_chain()

        selector = ContractSelector(options_data=data)
        result = selector.select_contract(
            ticker="NVDA", action="BUY_CALL",
            current_price=140.0, allocation_usd=5000,
        )

        assert result is not None
        assert result.strike == 140.0  # Closest to ATM
        assert result.quantity > 0
        assert result.total_cost > 0

    def test_selects_otm_contract(self):
        data = MagicMock()
        data.get_chain_for_entry.return_value = _make_chain()

        selector = ContractSelector(options_data=data)
        result = selector.select_contract(
            ticker="NVDA", action="BUY_CALL",
            current_price=140.0, allocation_usd=5000,
            strike_selection="10_OTM",
        )

        assert result is not None
        # 10% OTM on a call = strike above price = ~154 → closest is 150
        assert result.strike == 150.0

    def test_calculates_quantity(self):
        assert ContractSelector._calculate_quantity(18.5, 5000) == 2  # 5000 / (18.5 * 100) = 2.7 → 2
        assert ContractSelector._calculate_quantity(18.5, 1000) == 0  # 1000 / 1850 = 0.54 → 0
        assert ContractSelector._calculate_quantity(0, 5000) == 0

    def test_empty_chain_returns_none(self):
        data = MagicMock()
        data.get_chain_for_entry.return_value = []

        selector = ContractSelector(options_data=data)
        result = selector.select_contract(
            ticker="NVDA", action="BUY_CALL",
            current_price=140.0, allocation_usd=5000,
        )

        assert result is None

    def test_put_selection(self):
        data = MagicMock()
        put_chain = [
            OptionContract(
                symbol="NVDA250620P00130000", underlying="NVDA",
                option_type="put", strike=130.0, expiry="2025-06-20",
                bid=8.0, ask=9.0, mid=8.5, last=8.5,
                volume=300, open_interest=1500,
                implied_volatility=0.48, delta=-0.35, gamma=0.012,
                theta=-0.12, vega=0.22,
            ),
        ]
        data.get_chain_for_entry.return_value = put_chain

        selector = ContractSelector(options_data=data)
        result = selector.select_contract(
            ticker="NVDA", action="BUY_PUT",
            current_price=140.0, allocation_usd=5000,
        )

        assert result is not None
        assert result.option_type == "put"

    def test_target_strike_atm(self):
        assert ContractSelector._target_strike(140.0, "ATM", "call") == 140.0

    def test_target_strike_otm_call(self):
        result = ContractSelector._target_strike(100.0, "10_OTM", "call")
        assert result == pytest.approx(110.0)  # 10% above for OTM call

    def test_target_strike_otm_put(self):
        result = ContractSelector._target_strike(100.0, "10_OTM", "put")
        assert result == pytest.approx(90.0)  # 10% below for OTM put

    def test_allocation_too_small(self):
        data = MagicMock()
        data.get_chain_for_entry.return_value = _make_chain()

        selector = ContractSelector(options_data=data)
        result = selector.select_contract(
            ticker="NVDA", action="BUY_CALL",
            current_price=140.0, allocation_usd=100,  # Too small for any contract
        )

        assert result is None


# === Options Executor Routing ===


class TestOptionsExecutorRouting:
    def test_routes_buy_call_to_options_broker(self):
        from src.live.executor import LiveExecutor

        broker = MagicMock()
        risk = MagicMock()
        risk.is_core_position.return_value = True
        tm = MagicMock()
        options_broker = MagicMock()
        options_broker.buy_to_open.return_value = OrderResult(success=True, order_id="opt123")

        contract_selector = MagicMock()
        contract_selector.select_contract.return_value = SelectedContract(
            symbol="NVDA250620C00140000", underlying="NVDA",
            option_type="call", strike=140.0, expiry="2025-06-20",
            premium=18.5, quantity=2, total_cost=3700.0,
            delta=0.58, theta=-0.18, implied_volatility=0.42,
        )

        executor = LiveExecutor(
            broker=broker, risk_manager=risk, thesis_manager=tm,
            options_broker=options_broker, contract_selector=contract_selector,
        )

        # Mock getting current price
        broker._client = MagicMock()
        pos_mock = MagicMock()
        pos_mock.current_price = 140.0
        broker._client.get_open_position.return_value = pos_mock

        response = {
            "new_positions": [{
                "ticker": "NVDA", "action": "BUY_CALL", "allocation_pct": 5,
                "direction": "LONG", "confidence": "high",
                "thesis": "AI capex catalyst", "strike_selection": "ATM",
                "expiry_months": 6,
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, [])

        contract_selector.select_contract.assert_called_once()
        options_broker.buy_to_open.assert_called_once_with("NVDA250620C00140000", 2)
        assert len(trades) == 1
        assert trades[0]["action"] == "BUY_CALL"
        assert "CALL" in trades[0]["details"]

    def test_routes_sell_put_to_options_broker(self):
        from src.live.executor import LiveExecutor

        broker = MagicMock()
        risk = MagicMock()
        tm = MagicMock()
        options_broker = MagicMock()
        options_broker.sell_to_open.return_value = OrderResult(success=True)

        contract_selector = MagicMock()
        contract_selector.select_contract.return_value = SelectedContract(
            symbol="AMZN250620P00180000", underlying="AMZN",
            option_type="put", strike=180.0, expiry="2025-06-20",
            premium=5.0, quantity=3, total_cost=1500.0,
            delta=-0.25, theta=-0.10, implied_volatility=0.35,
        )

        executor = LiveExecutor(
            broker=broker, risk_manager=risk, thesis_manager=tm,
            options_broker=options_broker, contract_selector=contract_selector,
        )

        broker._client = MagicMock()
        pos_mock = MagicMock()
        pos_mock.current_price = 195.0
        broker._client.get_open_position.return_value = pos_mock

        response = {
            "new_positions": [{
                "ticker": "AMZN", "action": "SELL_PUT", "allocation_pct": 8,
                "direction": "LONG", "confidence": "high",
                "thesis": "Want to own at discount",
                "strike_selection": "10_OTM", "expiry_months": 3,
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, [])

        options_broker.sell_to_open.assert_called_once()
        assert len(trades) == 1
        assert trades[0]["action"] == "SELL_PUT"

    def test_skips_options_when_no_broker_configured(self):
        from src.live.executor import LiveExecutor

        broker = MagicMock()
        risk = MagicMock()
        tm = MagicMock()

        executor = LiveExecutor(
            broker=broker, risk_manager=risk, thesis_manager=tm,
            # No options_broker or contract_selector
        )

        response = {
            "new_positions": [{
                "ticker": "NVDA", "action": "BUY_CALL", "allocation_pct": 5,
                "direction": "LONG", "confidence": "high", "thesis": "test",
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, [])

        assert len(trades) == 0

    def test_no_contract_found_skips(self):
        from src.live.executor import LiveExecutor

        broker = MagicMock()
        risk = MagicMock()
        tm = MagicMock()
        options_broker = MagicMock()
        contract_selector = MagicMock()
        contract_selector.select_contract.return_value = None

        executor = LiveExecutor(
            broker=broker, risk_manager=risk, thesis_manager=tm,
            options_broker=options_broker, contract_selector=contract_selector,
        )

        broker._client = MagicMock()
        pos_mock = MagicMock()
        pos_mock.current_price = 140.0
        broker._client.get_open_position.return_value = pos_mock

        response = {
            "new_positions": [{
                "ticker": "NVDA", "action": "BUY_CALL", "allocation_pct": 5,
                "direction": "LONG", "confidence": "high", "thesis": "test",
            }],
        }
        trades = executor.execute_decisions(response, 100000, 30000, [])

        options_broker.buy_to_open.assert_not_called()
        assert len(trades) == 0


# === Options Data Client ===


class TestOptionsDataClient:
    def test_get_chain_for_entry_filters(self):
        data = MagicMock(spec=OptionsDataClient)
        data.get_chain_for_entry = OptionsDataClient.get_chain_for_entry.__get__(data)
        data.get_chain.return_value = [
            OptionContract(
                symbol="NVDA250620C00140000", underlying="NVDA",
                option_type="call", strike=140.0, expiry="2025-06-20",
                bid=18.0, ask=19.0, mid=18.5, last=18.5,
                volume=800, open_interest=3500,
                implied_volatility=0.42, delta=0.58, gamma=0.015,
                theta=-0.18, vega=0.28,
            ),
            # Low OI — should be filtered
            OptionContract(
                symbol="NVDA250620C00145000", underlying="NVDA",
                option_type="call", strike=145.0, expiry="2025-06-20",
                bid=15.0, ask=16.0, mid=15.5, last=15.5,
                volume=10, open_interest=50,  # Below threshold
                implied_volatility=0.43, delta=0.52, gamma=0.016,
                theta=-0.19, vega=0.27,
            ),
            # Wide spread — should be filtered
            OptionContract(
                symbol="NVDA250620C00155000", underlying="NVDA",
                option_type="call", strike=155.0, expiry="2025-06-20",
                bid=8.0, ask=12.0, mid=10.0, last=10.0,  # 40% spread
                volume=100, open_interest=500,
                implied_volatility=0.45, delta=0.38, gamma=0.017,
                theta=-0.21, vega=0.24,
            ),
        ]

        result = data.get_chain_for_entry(
            underlying="NVDA", current_price=140.0,
            option_type="call", min_dte=45, max_dte=90,
        )

        assert len(result) == 1  # Only the liquid one survives
        assert result[0].strike == 140.0
