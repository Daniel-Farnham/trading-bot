"""Email notifications for live trading via Gmail SMTP.

Three email types:
1. Call summary — sent after Call 1 and Call 3 with full logs
2. EOD portfolio update — daily, no Claude, raw Alpaca state + memory files attached
3. Error/alert — system errors, budget exceeded, etc.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import date
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

logger = logging.getLogger(__name__)


class EmailNotifier:
    def __init__(
        self,
        sender: str,
        app_password: str,
        recipient: str,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
        enabled: bool = True,
    ):
        self._sender = sender
        self._app_password = app_password
        self._recipient = recipient
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._enabled = enabled

    def send_call1_summary(self, call1_output: dict) -> bool:
        """Send Call 1 discovery summary."""
        today = date.today().isoformat()
        subject = f"[Trading Bot] Call 1 Discovery — {today}"

        lines = [f"<h2>Call 1 Discovery — {today}</h2>"]

        macro = call1_output.get("macro_assessment", "")
        if macro:
            lines.append(f"<h3>Macro Assessment</h3><p>{_esc(macro)}</p>")

        themes = call1_output.get("theme_impacts", [])
        if themes:
            lines.append("<h3>Theme Impacts</h3><ul>")
            for t in themes:
                lines.append(
                    f"<li><b>{_esc(t.get('theme', ''))}</b>: "
                    f"{_esc(t.get('direction', ''))} — {_esc(t.get('evidence', ''))}</li>"
                )
            lines.append("</ul>")

        flagged = call1_output.get("flagged_tickers_universe", [])
        if flagged:
            lines.append("<h3>Flagged Tickers (In Universe)</h3><ul>")
            for f in flagged:
                lines.append(f"<li><b>{_esc(f.get('ticker', ''))}</b>: {_esc(f.get('reason', ''))}</li>")
            lines.append("</ul>")

        new_adds = call1_output.get("new_universe_additions", [])
        if new_adds:
            lines.append("<h3>New Universe Additions</h3><ul>")
            for a in new_adds:
                lines.append(f"<li><b>{_esc(a.get('ticker', ''))}</b>: {_esc(a.get('reason', ''))}</li>")
            lines.append("</ul>")

        holdings_alerts = call1_output.get("holdings_alerts", [])
        if holdings_alerts:
            lines.append("<h3>Holdings Alerts</h3><ul>")
            for h in holdings_alerts:
                lines.append(f"<li><b>{_esc(h.get('ticker', ''))}</b>: {_esc(h.get('alert', ''))}</li>")
            lines.append("</ul>")

        watchlist_alerts = call1_output.get("watchlist_alerts", [])
        if watchlist_alerts:
            lines.append("<h3>Watchlist Alerts</h3><ul>")
            for w in watchlist_alerts:
                lines.append(f"<li><b>{_esc(w.get('ticker', ''))}</b>: {_esc(w.get('alert', ''))}</li>")
            lines.append("</ul>")

        emerging = call1_output.get("emerging_signals", [])
        if emerging:
            lines.append("<h3>Emerging Signals</h3><ul>")
            for e in emerging:
                lines.append(
                    f"<li>{_esc(e.get('signal', ''))} "
                    f"(potential theme: {_esc(e.get('potential_theme', ''))})</li>"
                )
            lines.append("</ul>")

        observation = call1_output.get("world_view_observation", "")
        if observation:
            lines.append(f"<h3>World View Observation</h3><p>{_esc(observation)}</p>")

        return self._send(subject, "\n".join(lines))

    def send_call3_summary(
        self,
        call3_output: dict,
        trades_executed: list[dict],
        review_type: str = "weekly",
        trigger_reason: str | None = None,
    ) -> bool:
        """Send Call 3 decision summary with trades."""
        today = date.today().isoformat()
        trigger_label = f" ({trigger_reason})" if trigger_reason else ""
        subject = f"[Trading Bot] Call 3 {review_type.title()}{trigger_label} — {today}"

        lines = [f"<h2>Call 3 {review_type.title()} Review{trigger_label} — {today}</h2>"]

        assessment = call3_output.get("world_assessment", "")
        if assessment:
            lines.append(f"<h3>World Assessment</h3><p>{_esc(assessment)}</p>")

        summary = call3_output.get("weekly_summary", "")
        if summary:
            lines.append(f"<h3>Summary</h3><p>{_esc(summary)}</p>")

        # Trades executed
        if trades_executed:
            lines.append("<h3>Trades Executed</h3>")
            lines.append("<table border='1' cellpadding='5' cellspacing='0'>")
            lines.append("<tr><th>Ticker</th><th>Action</th><th>Quantity</th><th>Details</th></tr>")
            for t in trades_executed:
                lines.append(
                    f"<tr><td>{_esc(t.get('ticker', ''))}</td>"
                    f"<td>{_esc(t.get('action', ''))}</td>"
                    f"<td>{t.get('quantity', '')}</td>"
                    f"<td>{_esc(t.get('details', ''))}</td></tr>"
                )
            lines.append("</table>")
        else:
            lines.append("<p><i>No trades executed.</i></p>")

        # New positions proposed
        new_pos = call3_output.get("new_positions", [])
        if new_pos:
            lines.append("<h3>New Positions</h3><ul>")
            for p in new_pos:
                lines.append(
                    f"<li><b>{_esc(p.get('ticker', ''))}</b> — "
                    f"{_esc(p.get('action', ''))} {p.get('allocation_pct', '')}% "
                    f"({_esc(p.get('confidence', ''))}): {_esc(p.get('thesis', ''))}</li>"
                )
            lines.append("</ul>")

        # Closes
        closes = call3_output.get("close_positions", [])
        if closes:
            lines.append("<h3>Positions Closed</h3><ul>")
            for c in closes:
                lines.append(f"<li><b>{_esc(c.get('ticker', ''))}</b>: {_esc(c.get('reason', ''))}</li>")
            lines.append("</ul>")

        # Decision reasoning
        reasoning = call3_output.get("decision_reasoning", [])
        if reasoning:
            lines.append("<h3>Decision Reasoning</h3><ul>")
            for r in reasoning:
                lines.append(
                    f"<li><b>{_esc(r.get('ticker', ''))}</b> ({_esc(r.get('action', ''))}): "
                    f"{_esc(r.get('reasoning', ''))}</li>"
                )
            lines.append("</ul>")

        # Theme updates
        theme_updates = call3_output.get("theme_updates", [])
        if theme_updates:
            lines.append("<h3>Theme Updates</h3><ul>")
            for t in theme_updates:
                action = t.get("action", f"delta {t.get('delta', '')}")
                lines.append(
                    f"<li><b>{_esc(t.get('name', ''))}</b>: {action} — {_esc(t.get('reason', ''))}</li>"
                )
            lines.append("</ul>")

        # Lessons
        lessons = call3_output.get("lessons", [])
        if lessons:
            lines.append("<h3>New Lessons</h3><ul>")
            for lesson in lessons:
                lines.append(f"<li>{_esc(str(lesson))}</li>")
            lines.append("</ul>")

        return self._send(subject, "\n".join(lines))

    def send_eod_portfolio(
        self,
        account: dict,
        positions: list[dict],
        memory_dir: str | Path,
    ) -> bool:
        """Send EOD portfolio update with Alpaca state and memory files attached."""
        today = date.today().isoformat()
        subject = f"[Trading Bot] EOD Portfolio — {today}"

        lines = [f"<h2>EOD Portfolio Update — {today}</h2>"]

        # Account summary
        equity = account.get("equity", 0)
        cash = account.get("cash", 0)
        buying_power = account.get("buying_power", 0)
        lines.append("<h3>Account</h3>")
        lines.append(f"<p>Equity: <b>${float(equity):,.2f}</b><br>")
        lines.append(f"Cash: ${float(cash):,.2f}<br>")
        lines.append(f"Buying Power: ${float(buying_power):,.2f}</p>")

        # Positions table
        if positions:
            lines.append("<h3>Positions</h3>")
            lines.append("<table border='1' cellpadding='5' cellspacing='0'>")
            lines.append(
                "<tr><th>Ticker</th><th>Qty</th><th>Avg Entry</th>"
                "<th>Current</th><th>Market Value</th><th>P&L</th><th>P&L %</th></tr>"
            )
            for p in positions:
                ticker = p.get("symbol", "")
                qty = p.get("qty", 0)
                avg_entry = float(p.get("avg_entry_price", 0))
                current = float(p.get("current_price", 0))
                market_val = float(p.get("market_value", 0))
                pnl = float(p.get("unrealized_pl", 0))
                pnl_pct = float(p.get("unrealized_plpc", 0)) * 100
                pnl_color = "green" if pnl >= 0 else "red"
                lines.append(
                    f"<tr><td>{_esc(ticker)}</td><td>{qty}</td>"
                    f"<td>${avg_entry:,.2f}</td><td>${current:,.2f}</td>"
                    f"<td>${market_val:,.2f}</td>"
                    f"<td style='color:{pnl_color}'>${pnl:,.2f}</td>"
                    f"<td style='color:{pnl_color}'>{pnl_pct:+.1f}%</td></tr>"
                )
            lines.append("</table>")
        else:
            lines.append("<p><i>No open positions.</i></p>")

        # Attach memory files
        memory_dir = Path(memory_dir)
        attachments = []
        md_files = [
            "active_theses.md", "portfolio_ledger.md", "themes.md",
            "world_view.md", "beliefs.md", "lessons_learned.md",
            "quarterly_summaries.md",
        ]
        for filename in md_files:
            filepath = memory_dir / filename
            if filepath.exists():
                attachments.append(filepath)

        return self._send(subject, "\n".join(lines), attachments=attachments)

    def send_alert(self, alert_type: str, details: str) -> bool:
        """Send an alert email (trigger fired, budget exceeded, etc.)."""
        today = date.today().isoformat()
        subject = f"[Trading Bot] ALERT: {alert_type} — {today}"
        body = f"<h2>Alert: {_esc(alert_type)}</h2><p>{_esc(details)}</p>"
        return self._send(subject, body)

    def send_error(self, error_type: str, traceback_str: str) -> bool:
        """Send an error notification."""
        today = date.today().isoformat()
        subject = f"[Trading Bot] ERROR: {error_type} — {today}"
        body = (
            f"<h2>Error: {_esc(error_type)}</h2>"
            f"<pre>{_esc(traceback_str)}</pre>"
        )
        return self._send(subject, body)

    def _send(
        self,
        subject: str,
        html_body: str,
        attachments: list[Path] | None = None,
    ) -> bool:
        """Send an email via Gmail SMTP."""
        if not self._enabled:
            logger.info("Email disabled, would have sent: %s", subject)
            return False

        msg = MIMEMultipart()
        msg["From"] = self._sender
        msg["To"] = self._recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))

        for filepath in (attachments or []):
            try:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(filepath.read_bytes())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename={filepath.name}",
                )
                msg.attach(part)
            except Exception as e:
                logger.warning("Failed to attach %s: %s", filepath, e)

        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
                server.starttls()
                server.login(self._sender, self._app_password)
                server.send_message(msg)
            logger.info("Email sent: %s", subject)
            return True
        except Exception as e:
            logger.error("Failed to send email '%s': %s", subject, e)
            return False


def _esc(text: str) -> str:
    """Basic HTML escaping."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
