"""Minimal HTTP health check server for Railway.

Runs in a background thread. Responds to GET /health with bot status.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger(__name__)

_status: dict = {
    "status": "starting",
    "started_at": datetime.now().isoformat(),
    "last_call1": None,
    "last_call3": None,
    "last_trigger_check": None,
}


def update_status(key: str, value: str) -> None:
    """Update a status field."""
    _status[key] = value


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_status).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress request logging
        pass


def start_health_server(port: int = 8080) -> threading.Thread:
    """Start the health check server in a background thread."""
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health check server started on port %d", port)
    update_status("status", "running")
    return thread
