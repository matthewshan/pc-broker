"""pc-broker shutdown agent.

A tiny, dependency-free HTTP service that runs ON the Windows gaming PC. The
broker (running in k3s) calls it to shut the PC down gracefully, since
Wake-on-LAN can only power a machine *on*.

Dependency-light on purpose: uses only the Python standard library so the PC
needs nothing but a Python install (or a bundled exe). Run it as an always-on
Windows service via ``install.ps1`` so it is reachable after WoL, before login.

Configuration (environment variables):
    SHUTDOWN_AGENT_TOKEN   Shared secret. Required: requests must send
                           ``Authorization: Bearer <token>``. If unset the
                           agent refuses to start.
    AGENT_PORT             TCP port to listen on (default: 8001).
    AGENT_BIND             Address to bind (default: 0.0.0.0).
    AGENT_DRY_RUN          If truthy (1/true/yes/on), authorized shutdown and
                           restart requests are logged but the machine is NOT
                           powered off. Use this to verify the full
                           phone -> broker -> agent path safely.

Endpoints:
    GET  /health     -> 200 {"status": "ok"}        (no auth)
    POST /shutdown   -> 202, then `shutdown /s /t 0` (auth required)
    POST /restart    -> 202, then `shutdown /r /t 0` (auth required)
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s pc-broker-agent: %(message)s",
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("SHUTDOWN_AGENT_TOKEN", "")
PORT = int(os.environ.get("AGENT_PORT", "8001"))
BIND = os.environ.get("AGENT_BIND", "0.0.0.0")
DRY_RUN = os.environ.get("AGENT_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")


def _run_shutdown(restart: bool = False) -> None:
    """Invoke the Windows shutdown command. Fire-and-forget.

    In dry-run mode the command is logged but never executed, so the machine
    stays on. Used to validate the end-to-end path before arming the agent.
    """
    flag = "/r" if restart else "/s"
    cmd = ["shutdown", flag, "/t", "0"]
    if DRY_RUN:
        logger.warning("DRY RUN: would execute: %s (machine NOT affected)", " ".join(cmd))
        return
    logger.info("Executing: %s", " ".join(cmd))
    # On non-Windows hosts (e.g. local dev) this will fail harmlessly.
    subprocess.Popen(cmd)


class Handler(BaseHTTPRequestHandler):
    server_version = "pc-broker-agent/0.1"

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        provided = self.headers.get("Authorization", "")
        expected = f"Bearer {TOKEN}"
        return hmac.compare_digest(provided, expected)

    def log_message(self, fmt: str, *args) -> None:  # route to logging
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.path not in ("/shutdown", "/restart"):
            self._send(404, {"detail": "not found"})
            return
        if not self._authorized():
            logger.warning("Rejected unauthorized %s request", self.path)
            self._send(401, {"detail": "invalid or missing token"})
            return

        restart = self.path == "/restart"
        action = "restart" if restart else "shutdown"
        self._send(202, {"accepted": True, "action": action, "dry_run": DRY_RUN})
        # Respond first, then trigger the action so the broker gets a clean 202.
        try:
            _run_shutdown(restart=restart)
        except Exception:
            logger.exception("Failed to run %s", action)


def main() -> None:
    if not TOKEN:
        logger.error("SHUTDOWN_AGENT_TOKEN is not set; refusing to start.")
        sys.exit(1)
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    if DRY_RUN:
        logger.warning("DRY RUN enabled: shutdown/restart requests will be logged, not executed.")
    logger.info("Listening on %s:%d (no action taken on startup)", BIND, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
