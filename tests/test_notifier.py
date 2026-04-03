"""Tests for email notifier."""
from __future__ import annotations

import smtplib
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.live.notifier import EmailNotifier, _esc


def _get_html_body(msg) -> str:
    """Extract HTML body from a MIMEMultipart message, handling encoding."""
    payload = msg.get_payload()[0]
    body = payload.get_payload(decode=True)
    if isinstance(body, bytes):
        return body.decode("utf-8")
    return payload.get_payload()


@pytest.fixture
def notifier():
    """Notifier with SMTP mocked out."""
    return EmailNotifier(
        sender="test@gmail.com",
        app_password="test-password",
        recipient="user@gmail.com",
        enabled=True,
    )


@pytest.fixture
def disabled_notifier():
    return EmailNotifier(
        sender="test@gmail.com",
        app_password="test-password",
        recipient="user@gmail.com",
        enabled=False,
    )


SAMPLE_CALL1_OUTPUT = {
    "macro_assessment": "Fed held rates, market relief rally expected",
    "theme_impacts": [
        {"theme": "AI Infrastructure", "direction": "strengthening", "evidence": "MSFT capex +20%"},
    ],
    "flagged_tickers_universe": [
        {"ticker": "NVDA", "reason": "Blackwell shipments ahead of schedule"},
    ],
    "new_universe_additions": [
        {"ticker": "VRT", "reason": "Data center cooling leader"},
    ],
    "holdings_alerts": [
        {"ticker": "AVGO", "alert": "Earnings beat, raised guidance"},
    ],
    "watchlist_alerts": [
        {"ticker": "CEG", "alert": "Nuclear restart deal confirmed"},
    ],
    "emerging_signals": [
        {"signal": "Defense backlogs rising", "potential_theme": "Defense Supercycle"},
    ],
    "world_view_observation": "Fed pivot delayed but market shrugging it off",
}

SAMPLE_CALL3_OUTPUT = {
    "world_assessment": "AI capex cycle intact despite macro uncertainty",
    "weekly_summary": "Added to NVDA, closed NKE on thesis break",
    "new_positions": [
        {
            "ticker": "CEG", "action": "BUY", "allocation_pct": 8,
            "confidence": "high", "thesis": "Nuclear renaissance",
        },
    ],
    "close_positions": [
        {"ticker": "NKE", "reason": "Tariff thesis broken"},
    ],
    "decision_reasoning": [
        {"ticker": "CEG", "action": "BUY", "reasoning": "Nuclear demand confirmed by AWS deal"},
        {"ticker": "NKE", "action": "SELL", "reasoning": "Supply chain exposed to tariffs"},
    ],
    "theme_updates": [
        {"name": "Nuclear Renaissance", "delta": 1, "reason": "CEG deal confirms"},
    ],
    "lessons": ["Don't fight structural trends"],
}

SAMPLE_TRADES = [
    {"ticker": "CEG", "action": "BUY", "quantity": 50, "details": "Core entry at $245"},
    {"ticker": "NKE", "action": "SELL", "quantity": 100, "details": "Thesis break exit"},
]


class TestCall1Summary:
    @patch("src.live.notifier.smtplib.SMTP")
    def test_sends_call1_email(self, mock_smtp, notifier):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        result = notifier.send_call1_summary(SAMPLE_CALL1_OUTPUT)

        assert result is True
        mock_server.send_message.assert_called_once()
        msg = mock_server.send_message.call_args[0][0]
        assert "Call 1 Discovery" in msg["Subject"]
        assert date.today().isoformat() in msg["Subject"]

    @patch("src.live.notifier.smtplib.SMTP")
    def test_call1_email_contains_all_sections(self, mock_smtp, notifier):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        notifier.send_call1_summary(SAMPLE_CALL1_OUTPUT)

        msg = mock_server.send_message.call_args[0][0]
        body = _get_html_body(msg)
        assert "Fed held rates" in body
        assert "AI Infrastructure" in body
        assert "NVDA" in body
        assert "VRT" in body
        assert "AVGO" in body
        assert "CEG" in body
        assert "Defense Supercycle" in body

    def test_disabled_notifier_returns_false(self, disabled_notifier):
        result = disabled_notifier.send_call1_summary(SAMPLE_CALL1_OUTPUT)
        assert result is False

    @patch("src.live.notifier.smtplib.SMTP")
    def test_handles_empty_output(self, mock_smtp, notifier):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        result = notifier.send_call1_summary({})
        assert result is True


class TestCall3Summary:
    @patch("src.live.notifier.smtplib.SMTP")
    def test_sends_call3_email(self, mock_smtp, notifier):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        result = notifier.send_call3_summary(
            SAMPLE_CALL3_OUTPUT, SAMPLE_TRADES, review_type="weekly",
        )

        assert result is True
        msg = mock_server.send_message.call_args[0][0]
        assert "Call 3 Weekly" in msg["Subject"]

    @patch("src.live.notifier.smtplib.SMTP")
    def test_call3_includes_trigger_reason(self, mock_smtp, notifier):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        notifier.send_call3_summary(
            SAMPLE_CALL3_OUTPUT, [], review_type="volatility",
            trigger_reason="NVDA down 12%",
        )

        msg = mock_server.send_message.call_args[0][0]
        assert "NVDA down 12%" in msg["Subject"]

    @patch("src.live.notifier.smtplib.SMTP")
    def test_call3_email_contains_trades(self, mock_smtp, notifier):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        notifier.send_call3_summary(SAMPLE_CALL3_OUTPUT, SAMPLE_TRADES)

        msg = mock_server.send_message.call_args[0][0]
        body = _get_html_body(msg)
        assert "CEG" in body
        assert "NKE" in body
        assert "Core entry" in body

    @patch("src.live.notifier.smtplib.SMTP")
    def test_call3_no_trades_message(self, mock_smtp, notifier):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        notifier.send_call3_summary(SAMPLE_CALL3_OUTPUT, [])

        msg = mock_server.send_message.call_args[0][0]
        body = _get_html_body(msg)
        assert "No trades executed" in body


class TestEODPortfolio:
    @patch("src.live.notifier.smtplib.SMTP")
    def test_sends_eod_email(self, mock_smtp, notifier, tmp_path):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        account = {"equity": "105000", "cash": "30000", "buying_power": "60000"}
        positions = [
            {
                "symbol": "NVDA", "qty": "80", "avg_entry_price": "125.00",
                "current_price": "155.00", "market_value": "12400.00",
                "unrealized_pl": "2400.00", "unrealized_plpc": "0.24",
            },
        ]

        result = notifier.send_eod_portfolio(account, positions, tmp_path)

        assert result is True
        msg = mock_server.send_message.call_args[0][0]
        assert "EOD Portfolio" in msg["Subject"]

    @patch("src.live.notifier.smtplib.SMTP")
    def test_eod_contains_account_and_positions(self, mock_smtp, notifier, tmp_path):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        account = {"equity": "105000", "cash": "30000", "buying_power": "60000"}
        positions = [
            {
                "symbol": "NVDA", "qty": "80", "avg_entry_price": "125.00",
                "current_price": "155.00", "market_value": "12400.00",
                "unrealized_pl": "2400.00", "unrealized_plpc": "0.24",
            },
        ]

        notifier.send_eod_portfolio(account, positions, tmp_path)

        msg = mock_server.send_message.call_args[0][0]
        body = _get_html_body(msg)
        assert "$105,000" in body
        assert "$30,000" in body
        assert "NVDA" in body
        assert "$2,400" in body

    @patch("src.live.notifier.smtplib.SMTP")
    def test_eod_attaches_md_files(self, mock_smtp, notifier, tmp_path):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        # Create some memory files
        (tmp_path / "active_theses.md").write_text("# Theses\n## NVDA")
        (tmp_path / "themes.md").write_text("# Themes\n## AI Infra [4]")
        (tmp_path / "world_view.md").write_text("# World View\nRisk-on")

        account = {"equity": "100000", "cash": "50000", "buying_power": "100000"}
        notifier.send_eod_portfolio(account, [], tmp_path)

        msg = mock_server.send_message.call_args[0][0]
        # Payload: [html_body, attachment1, attachment2, ...]
        attachments = msg.get_payload()[1:]
        attachment_names = [a.get_filename() for a in attachments]
        assert "active_theses.md" in attachment_names
        assert "themes.md" in attachment_names
        assert "world_view.md" in attachment_names

    @patch("src.live.notifier.smtplib.SMTP")
    def test_eod_skips_missing_md_files(self, mock_smtp, notifier, tmp_path):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        # Only one file exists
        (tmp_path / "themes.md").write_text("# Themes")

        account = {"equity": "100000", "cash": "50000", "buying_power": "100000"}
        notifier.send_eod_portfolio(account, [], tmp_path)

        msg = mock_server.send_message.call_args[0][0]
        attachments = msg.get_payload()[1:]
        assert len(attachments) == 1
        assert attachments[0].get_filename() == "themes.md"

    @patch("src.live.notifier.smtplib.SMTP")
    def test_eod_no_positions_message(self, mock_smtp, notifier, tmp_path):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        account = {"equity": "100000", "cash": "100000", "buying_power": "200000"}
        notifier.send_eod_portfolio(account, [], tmp_path)

        msg = mock_server.send_message.call_args[0][0]
        body = _get_html_body(msg)
        assert "No open positions" in body


class TestAlertAndError:
    @patch("src.live.notifier.smtplib.SMTP")
    def test_sends_alert(self, mock_smtp, notifier):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        result = notifier.send_alert("Intraday Shock", "NVDA down 15% in 30 min")

        assert result is True
        msg = mock_server.send_message.call_args[0][0]
        assert "ALERT" in msg["Subject"]
        assert "Intraday Shock" in msg["Subject"]

    @patch("src.live.notifier.smtplib.SMTP")
    def test_sends_error(self, mock_smtp, notifier):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        result = notifier.send_error("BudgetExceeded", "Daily cap hit at $2.00")

        assert result is True
        msg = mock_server.send_message.call_args[0][0]
        assert "ERROR" in msg["Subject"]


class TestSMTPFailure:
    @patch("src.live.notifier.smtplib.SMTP")
    def test_smtp_error_returns_false(self, mock_smtp, notifier):
        mock_smtp.return_value.__enter__ = MagicMock(
            side_effect=smtplib.SMTPAuthenticationError(535, b"Auth failed"),
        )
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        result = notifier.send_call1_summary(SAMPLE_CALL1_OUTPUT)
        assert result is False


class TestHtmlEscaping:
    def test_escapes_html(self):
        assert _esc("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" or \
               "&lt;script&gt;" in _esc("<script>alert('xss')</script>")

    def test_escapes_ampersand(self):
        assert _esc("AT&T") == "AT&amp;T"

    def test_escapes_quotes(self):
        assert _esc('"hello"') == "&quot;hello&quot;"
