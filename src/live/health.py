"""HTTP status server for Railway — health check + web dashboard.

Runs in a background thread. Serves:
- GET /health     — JSON health check (for Railway)
- GET /           — HTML dashboard (overview)
- GET /state      — JSON daily state (Call 1/3 outputs, triggers, trades)
- GET /watchlist  — JSON watchlist
- GET /universe   — JSON universe
- GET /portfolio  — JSON Alpaca positions + account
- GET /spend      — JSON API spend log
- GET /memory     — JSON memory files content
- GET /logs       — Recent log entries
"""
from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_status: dict = {
    "status": "starting",
    "started_at": datetime.now().isoformat(),
    "last_call1": None,
    "last_call3": None,
    "last_trigger_check": None,
}

# Ring buffer for recent logs (viewable via /logs)
_recent_logs: deque = deque(maxlen=500)

# Set by main.py after initialization
_data_dir: str = "data/live"
_market_data = None


def update_status(key: str, value: str) -> None:
    """Update a status field."""
    _status[key] = value


def set_data_dir(path: str) -> None:
    """Set the live data directory for file reads."""
    global _data_dir
    _data_dir = path


def set_market_data(market_data) -> None:
    """Set the MarketData client for portfolio queries."""
    global _market_data
    _market_data = market_data


class _LogCaptureHandler(logging.Handler):
    """Captures log records into the ring buffer."""
    def emit(self, record):
        try:
            msg = self.format(record)
            _recent_logs.append(msg)
        except Exception:
            pass


class _DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        routes = {
            "/health": self._health,
            "/": self._dashboard,
            "/state": self._state,
            "/watchlist": self._watchlist,
            "/universe": self._universe,
            "/portfolio": self._portfolio,
            "/spend": self._spend,
            "/memory": self._memory,
            "/logs": self._logs,
        }

        handler = routes.get(path)
        if handler:
            handler()
        else:
            self.send_response(404)
            self.end_headers()

    def _health(self):
        self._json_response(_status)

    def _state(self):
        state_path = Path(_data_dir) / "daily_state.json"
        self._serve_json_file(state_path)

    def _watchlist(self):
        wl_path = Path(_data_dir) / "watchlist.json"
        self._serve_json_file(wl_path)

    def _universe(self):
        uni_path = Path(_data_dir) / "universe.json"
        self._serve_json_file(uni_path)

    def _portfolio(self):
        if not _market_data:
            self._json_response({"error": "Market data not configured"})
            return
        try:
            account = _market_data.get_account()
            positions = _market_data.get_positions()
            self._json_response({"account": account, "positions": positions})
        except Exception as e:
            self._json_response({"error": str(e)})

    def _spend(self):
        spend_path = Path(_data_dir) / "api_spend.jsonl"
        if not spend_path.exists():
            self._json_response([])
            return
        entries = []
        for line in spend_path.read_text().splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        self._json_response(entries)

    def _memory(self):
        memory_dir = Path(_data_dir)
        md_files = [
            "active_theses.md", "portfolio_ledger.md", "themes.md",
            "world_view.md", "beliefs.md", "lessons_learned.md",
            "decision_journal.md",
        ]
        result = {}
        for filename in md_files:
            filepath = memory_dir / filename
            if filepath.exists():
                result[filename] = filepath.read_text()
            else:
                result[filename] = "(not found)"
        self._json_response(result)

    def _logs(self):
        self._json_response(list(_recent_logs))

    def _dashboard(self):
        """Serve the HTML dashboard."""
        html = _build_dashboard_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2, default=str).encode())

    def _serve_json_file(self, path: Path):
        if not path.exists():
            self._json_response([])
            return
        try:
            data = json.loads(path.read_text())
            self._json_response(data)
        except Exception:
            self._json_response({"error": f"Failed to read {path.name}"})

    def log_message(self, format, *args):
        pass


def _build_dashboard_html() -> str:
    """Build the HTML dashboard page."""
    return """<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, system-ui, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }
        h1 { color: #58a6ff; margin-bottom: 20px; }
        h2 { color: #8b949e; margin: 20px 0 10px; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
        .card h3 { color: #58a6ff; margin-bottom: 8px; font-size: 16px; }
        .stat { font-size: 24px; font-weight: bold; color: #f0f6fc; }
        .stat.green { color: #3fb950; }
        .stat.red { color: #f85149; }
        .label { color: #8b949e; font-size: 12px; margin-top: 4px; }
        table { width: 100%; border-collapse: collapse; margin-top: 8px; }
        th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; font-size: 13px; }
        th { color: #8b949e; font-weight: normal; }
        .ticker { color: #58a6ff; font-weight: bold; }
        pre { background: #0d1117; border: 1px solid #30363d; border-radius: 4px; padding: 12px;
              font-size: 12px; max-height: 400px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }
        .refresh { color: #8b949e; font-size: 12px; margin-top: 20px; }
        #error { color: #f85149; margin: 10px 0; }
        .section { margin-bottom: 24px; }
        .pill { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin-left: 4px; }
        .pill.running { background: #238636; }
        .pill.stopped { background: #da3633; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
        .tab { padding: 6px 12px; border-radius: 6px; cursor: pointer; background: #21262d; color: #8b949e; border: none; font-size: 13px; }
        .tab.active { background: #30363d; color: #f0f6fc; }
    </style>
</head>
<body>
    <h1>Trading Bot <span class="pill running" id="status-pill">loading</span></h1>
    <div id="error"></div>

    <div class="tabs">
        <button class="tab active" onclick="showSection('overview')">Overview</button>
        <button class="tab" onclick="showSection('portfolio')">Portfolio</button>
        <button class="tab" onclick="showSection('watchlist')">Watchlist</button>
        <button class="tab" onclick="showSection('claude')">Claude Output</button>
        <button class="tab" onclick="showSection('memory')">Memory</button>
        <button class="tab" onclick="showSection('spend')">API Spend</button>
        <button class="tab" onclick="showSection('logs')">Logs</button>
    </div>

    <div id="overview" class="section">
        <div class="grid" id="status-grid"></div>
    </div>

    <div id="portfolio" class="section" style="display:none">
        <div class="card"><h3>Account</h3><div id="account-info"></div></div>
        <div class="card" style="margin-top:16px"><h3>Positions</h3><div id="positions-table"></div></div>
    </div>

    <div id="watchlist" class="section" style="display:none">
        <div class="grid">
            <div class="card"><h3>Watchlist</h3><div id="watchlist-data"></div></div>
            <div class="card"><h3>Universe</h3><div id="universe-data"></div></div>
        </div>
    </div>

    <div id="claude" class="section" style="display:none">
        <div class="card"><h3>Call 1 Output</h3><pre id="call1-output">Loading...</pre></div>
        <div class="card" style="margin-top:16px"><h3>Call 3 Output</h3><pre id="call3-output">Loading...</pre></div>
        <div class="card" style="margin-top:16px"><h3>Triggers Today</h3><pre id="triggers-output">Loading...</pre></div>
    </div>

    <div id="memory" class="section" style="display:none">
        <div id="memory-cards"></div>
    </div>

    <div id="spend" class="section" style="display:none">
        <div class="card"><h3>API Spend</h3><div id="spend-data"></div></div>
    </div>

    <div id="logs" class="section" style="display:none">
        <div class="card"><h3>Recent Logs</h3><pre id="logs-data">Loading...</pre></div>
    </div>

    <p class="refresh">Auto-refreshes every 30s. <a href="/health" style="color:#58a6ff">Health JSON</a></p>

<script>
let currentSection = 'overview';

function showSection(name) {
    document.querySelectorAll('.section').forEach(s => s.style.display = 'none');
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById(name).style.display = 'block';
    event.target.classList.add('active');
    currentSection = name;
    refresh();
}

async function fetchJSON(url) {
    try {
        const r = await fetch(url);
        return await r.json();
    } catch(e) {
        document.getElementById('error').textContent = 'Failed to fetch ' + url;
        return null;
    }
}

async function refresh() {
    const health = await fetchJSON('/health');
    if (health) {
        const pill = document.getElementById('status-pill');
        pill.textContent = health.status;
        pill.className = 'pill ' + (health.status === 'running' ? 'running' : 'stopped');

        document.getElementById('status-grid').innerHTML = `
            <div class="card"><h3>Status</h3><div class="stat green">${health.status}</div>
                <div class="label">Started: ${health.started_at || 'N/A'}</div></div>
            <div class="card"><h3>Last Call 1</h3><div class="stat">${health.last_call1 ? new Date(health.last_call1).toLocaleTimeString() : 'Never'}</div></div>
            <div class="card"><h3>Last Call 3</h3><div class="stat">${health.last_call3 ? new Date(health.last_call3).toLocaleTimeString() : 'Never'}</div></div>
            <div class="card"><h3>Last Trigger Check</h3><div class="stat">${health.last_trigger_check ? new Date(health.last_trigger_check).toLocaleTimeString() : 'Never'}</div></div>
        `;
    }

    if (currentSection === 'portfolio') {
        const p = await fetchJSON('/portfolio');
        if (p && p.account) {
            const a = p.account;
            document.getElementById('account-info').innerHTML = `
                <div class="stat">$${Number(a.equity || a.portfolio_value || 0).toLocaleString()}</div>
                <div class="label">Cash: $${Number(a.cash || 0).toLocaleString()}</div>
            `;
            if (p.positions && p.positions.length) {
                let rows = p.positions.map(pos => {
                    const pnl = Number(pos.unrealized_pnl || 0);
                    const cls = pnl >= 0 ? 'green' : 'red';
                    return `<tr><td class="ticker">${pos.ticker}</td><td>${pos.qty}</td>
                        <td>$${Number(pos.avg_entry || 0).toFixed(2)}</td>
                        <td>$${Number(pos.current_price || 0).toFixed(2)}</td>
                        <td class="${cls}">$${pnl.toFixed(0)}</td></tr>`;
                }).join('');
                document.getElementById('positions-table').innerHTML =
                    `<table><tr><th>Ticker</th><th>Qty</th><th>Entry</th><th>Current</th><th>P&L</th></tr>${rows}</table>`;
            } else {
                document.getElementById('positions-table').innerHTML = '<p>No positions</p>';
            }
        }
    }

    if (currentSection === 'watchlist') {
        const wl = await fetchJSON('/watchlist');
        if (wl && wl.length) {
            let rows = wl.map(w => `<tr><td class="ticker">${w.ticker}</td><td>${w.reason || ''}</td><td>${w.added_date}</td></tr>`).join('');
            document.getElementById('watchlist-data').innerHTML =
                `<table><tr><th>Ticker</th><th>Reason</th><th>Added</th></tr>${rows}</table>`;
        } else {
            document.getElementById('watchlist-data').innerHTML = '<p>Empty</p>';
        }
        const uni = await fetchJSON('/universe');
        if (uni) {
            document.getElementById('universe-data').innerHTML = `<p>${uni.length} stocks</p>
                <p style="font-size:12px;color:#8b949e;margin-top:8px">${uni.map(u=>u.ticker).join(', ')}</p>`;
        }
    }

    if (currentSection === 'claude') {
        const state = await fetchJSON('/state');
        if (state) {
            document.getElementById('call1-output').textContent = state.call1_output ? JSON.stringify(state.call1_output, null, 2) : 'No Call 1 output today';
            document.getElementById('call3-output').textContent = state.call3_output ? JSON.stringify(state.call3_output, null, 2) : 'No Call 3 output today';
            document.getElementById('triggers-output').textContent = state.triggers_fired && state.triggers_fired.length
                ? JSON.stringify(state.triggers_fired, null, 2) : 'No triggers today';
        }
    }

    if (currentSection === 'memory') {
        const mem = await fetchJSON('/memory');
        if (mem) {
            document.getElementById('memory-cards').innerHTML = Object.entries(mem).map(([name, content]) =>
                `<div class="card" style="margin-bottom:16px"><h3>${name}</h3><pre>${content.substring(0, 3000)}</pre></div>`
            ).join('');
        }
    }

    if (currentSection === 'spend') {
        const spend = await fetchJSON('/spend');
        if (spend && spend.length) {
            const today = spend.filter(s => s.date === new Date().toISOString().split('T')[0]);
            const todayTotal = today.reduce((sum, s) => sum + (s.cost_usd || 0), 0);
            const monthTotal = spend.reduce((sum, s) => sum + (s.cost_usd || 0), 0);
            let rows = spend.slice(-20).reverse().map(s =>
                `<tr><td>${s.timestamp || s.date}</td><td>${s.model}</td>
                <td>${s.input_tokens}</td><td>${s.output_tokens}</td>
                <td>$${s.cost_usd.toFixed(4)}</td></tr>`
            ).join('');
            document.getElementById('spend-data').innerHTML = `
                <div class="stat">$${todayTotal.toFixed(4)} today / $${monthTotal.toFixed(2)} total</div>
                <table style="margin-top:12px"><tr><th>Time</th><th>Model</th><th>In</th><th>Out</th><th>Cost</th></tr>${rows}</table>`;
        } else {
            document.getElementById('spend-data').innerHTML = '<p>No spend data</p>';
        }
    }

    if (currentSection === 'logs') {
        const logs = await fetchJSON('/logs');
        if (logs) {
            document.getElementById('logs-data').textContent = logs.slice(-200).join('\\n');
        }
    }
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


def start_health_server(port: int = 8080) -> Thread:
    """Start the health/dashboard server in a background thread."""
    # Install log capture handler
    capture = _LogCaptureHandler()
    capture.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(capture)

    server = HTTPServer(("0.0.0.0", port), _DashboardHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Dashboard server started on port %d", port)
    update_status("status", "running")
    return thread
