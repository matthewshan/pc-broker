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
    GET  /activity   -> 200 user-activity report     (auth required)
    POST /shutdown   -> 202, then `shutdown /s /t 0 /f` (auth required)
    POST /restart    -> 202, then `shutdown /r /t 0 /f` (auth required)

The /activity report feeds the broker's idle auto-shutdown. This agent only
REPORTS raw facts; the broker applies thresholds and decides. Contract: any
null field or ``"ok": false`` MUST be treated by the broker as "user is
active" (never shut down on ambiguous data).

Idle reporter mode (``python agent.py --idle-reporter``):
    WTS reports LastInputTime only for RDP sessions; for the local console it
    is 0, and GetLastInputInfo is session-local (useless from SYSTEM). So
    install.ps1 also registers a logon-triggered task that runs this same
    file with --idle-reporter INSIDE the user session, periodically writing
    idle seconds (from GetLastInputInfo) to AGENT_IDLE_FILE. The SYSTEM agent
    merges that file into /activity; a stale or missing file simply reads as
    "unknown", which the broker must treat as active.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import subprocess
import sys
import time
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
IDLE_FILE = os.environ.get("AGENT_IDLE_FILE", r"C:\ProgramData\pc-broker\last_input.json")
IDLE_REPORT_INTERVAL = float(os.environ.get("AGENT_IDLE_REPORT_INTERVAL", "30"))
IDLE_FILE_MAX_AGE = IDLE_REPORT_INTERVAL * 3


def _run_shutdown(restart: bool = False) -> None:
    """Invoke the Windows shutdown command. Fire-and-forget.

    In dry-run mode the command is logged but never executed, so the machine
    stays on. Used to validate the end-to-end path before arming the agent.
    """
    flag = "/r" if restart else "/s"
    # /f is required: with /t 0 Windows does NOT imply force, so any app in an
    # active session can veto the shutdown — it then hangs pending forever
    # while the broker has already reported success.
    cmd = ["shutdown", flag, "/t", "0", "/f"]
    if DRY_RUN:
        logger.warning("DRY RUN: would execute: %s (machine NOT affected)", " ".join(cmd))
        return
    logger.info("Executing: %s", " ".join(cmd))
    # On non-Windows hosts (e.g. local dev) this will fail harmlessly.
    subprocess.Popen(cmd)


# ── User-activity collection (Windows WTS API via ctypes) ──────────────────
# WTS is queried instead of GetLastInputInfo because the agent runs as SYSTEM
# in session 0: GetLastInputInfo only sees its own session, while
# WTSQuerySessionInformation reports LastInputTime for every session.

_WTS_ACTIVE = 0
_STATE_NAMES = {
    0: "Active", 1: "Connected", 2: "ConnectQuery", 3: "Shadow",
    4: "Disconnected", 5: "Idle", 6: "Listen", 7: "Reset", 8: "Down", 9: "Init",
}
_WTS_SESSION_INFO_EX = 25
_SESSION_FLAG_LOCK = 0  # WTSINFOEX_LEVEL1.SessionFlags on Win8+


def _wts_sessions() -> list[dict]:
    """Enumerate interactive sessions with per-session idle time. Raises on failure."""
    import ctypes
    from ctypes import wintypes

    class WTS_SESSION_INFOW(ctypes.Structure):
        _fields_ = [
            ("SessionId", wintypes.DWORD),
            ("pWinStationName", wintypes.LPWSTR),
            ("State", ctypes.c_int),
        ]

    class WTSINFOEX_LEVEL1W(ctypes.Structure):
        _fields_ = [
            ("SessionId", wintypes.ULONG),
            ("SessionState", ctypes.c_long),
            ("SessionFlags", ctypes.c_long),
            ("WinStationName", ctypes.c_wchar * 33),
            ("UserName", ctypes.c_wchar * 21),
            ("DomainName", ctypes.c_wchar * 18),
            ("LogonTime", ctypes.c_longlong),
            ("ConnectTime", ctypes.c_longlong),
            ("DisconnectTime", ctypes.c_longlong),
            ("LastInputTime", ctypes.c_longlong),
            ("CurrentTime", ctypes.c_longlong),
            ("IncomingBytes", wintypes.DWORD),
            ("OutgoingBytes", wintypes.DWORD),
            ("IncomingFrames", wintypes.DWORD),
            ("OutgoingFrames", wintypes.DWORD),
            ("IncomingCompressedBytes", wintypes.DWORD),
            ("OutgoingCompressedBytes", wintypes.DWORD),
        ]

    class WTSINFOEXW(ctypes.Structure):
        _fields_ = [("Level", wintypes.DWORD), ("Data", WTSINFOEX_LEVEL1W)]

    wtsapi = ctypes.WinDLL("wtsapi32")
    sessions_ptr = ctypes.POINTER(WTS_SESSION_INFOW)()
    count = wintypes.DWORD(0)
    if not wtsapi.WTSEnumerateSessionsW(
        None, 0, 1, ctypes.byref(sessions_ptr), ctypes.byref(count)
    ):
        raise ctypes.WinError()

    results: list[dict] = []
    try:
        for i in range(count.value):
            info = sessions_ptr[i]
            station = info.pWinStationName or ""
            if station in ("Services", "Listen") or station.startswith("RDP-Listener"):
                continue

            buf = wintypes.LPWSTR()
            nbytes = wintypes.DWORD(0)
            entry: dict = {
                "id": info.SessionId,
                "station": station,
                "user": None,
                "state": _STATE_NAMES.get(info.State, str(info.State)),
                "locked": None,
                "idle_seconds": None,
            }
            if wtsapi.WTSQuerySessionInformationW(
                None, info.SessionId, _WTS_SESSION_INFO_EX,
                ctypes.byref(buf), ctypes.byref(nbytes),
            ):
                try:
                    ex = ctypes.cast(buf, ctypes.POINTER(WTSINFOEXW)).contents
                    data = ex.Data
                    entry["user"] = data.UserName or None
                    entry["locked"] = data.SessionFlags == _SESSION_FLAG_LOCK
                    # Both timestamps are 100ns FILETIME ticks from the same
                    # struct, so idle time needs no epoch conversion.
                    # LastInputTime == 0 means "no input recorded" (e.g. right
                    # after logon) -> unknown, reported as null.
                    if data.LastInputTime > 0 and data.CurrentTime >= data.LastInputTime:
                        entry["idle_seconds"] = (data.CurrentTime - data.LastInputTime) / 10_000_000.0
                finally:
                    wtsapi.WTSFreeMemory(buf)
            results.append(entry)
    finally:
        wtsapi.WTSFreeMemory(sessions_ptr)
    return results


def _console_idle_from_file() -> tuple[float | None, float | None]:
    """Read (idle_seconds, report_age_seconds) written by the idle reporter.

    Returns (None, None) if the file is missing, stale, or unparseable —
    which the broker must treat as "user is active".
    """
    try:
        with open(IDLE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        age = time.time() - float(data["written_at"])
        if age < 0 or age > IDLE_FILE_MAX_AGE:
            return None, age
        return float(data["idle_seconds"]), age
    except Exception:
        return None, None


def _run_idle_reporter() -> None:
    """Write console idle time to IDLE_FILE forever. Runs IN the user session."""
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    os.makedirs(os.path.dirname(IDLE_FILE), exist_ok=True)
    logger.info("Idle reporter writing to %s every %ss", IDLE_FILE, IDLE_REPORT_INTERVAL)
    while True:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if user32.GetLastInputInfo(ctypes.byref(lii)):
            # Both tick values are 32-bit ms; modular subtraction handles wrap.
            idle_ms = (kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
            payload = {
                "written_at": time.time(),
                "idle_seconds": idle_ms / 1000.0,
                "user": os.environ.get("USERNAME", ""),
            }
            tmp = IDLE_FILE + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                os.replace(tmp, IDLE_FILE)
            except OSError:
                logger.exception("Failed to write idle file")
        time.sleep(IDLE_REPORT_INTERVAL)


_gpu_cache: dict = {"at": 0.0, "value": None}
_GPU_CACHE_TTL = 10.0


def _gpu_util_percent() -> int | None:
    """Overall GPU utilization via nvidia-smi, cached ~10s. None if unavailable."""
    now = time.monotonic()
    if now - _gpu_cache["at"] < _GPU_CACHE_TTL:
        return _gpu_cache["value"]
    value = None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            value = int(out.stdout.strip().splitlines()[0])
    except Exception:
        value = None
    _gpu_cache["at"] = now
    _gpu_cache["value"] = value
    return value


def _collect_activity() -> dict:
    """Build the /activity report. Never raises: failures land in ok/error."""
    report: dict = {
        "ok": True,
        "sessions": [],
        "min_active_idle_seconds": None,
        "any_session_active": None,
        "console_report_age_seconds": None,
        "gpu_util_percent": None,
        "error": None,
    }
    try:
        sessions = _wts_sessions()

        # WTS LastInputTime is only populated for RDP sessions; fill the local
        # console's idle from the in-session reporter file when it's fresh.
        console_idle, report_age = _console_idle_from_file()
        report["console_report_age_seconds"] = report_age
        for s in sessions:
            if s["station"] == "Console" and s["idle_seconds"] is None:
                s["idle_seconds"] = console_idle

        report["sessions"] = sessions
        active = [s for s in sessions if s["state"] == "Active"]
        report["any_session_active"] = bool(active)
        idles = [s["idle_seconds"] for s in active if s["idle_seconds"] is not None]
        if active and len(idles) == len(active):
            report["min_active_idle_seconds"] = min(idles)
        # else: some active session has unknown idle -> leave null (broker
        # must treat null as "user is active")
    except Exception as exc:
        logger.exception("Activity collection failed")
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"

    try:
        report["gpu_util_percent"] = _gpu_util_percent()
    except Exception:
        report["gpu_util_percent"] = None
    return report


class Handler(BaseHTTPRequestHandler):
    server_version = "pc-broker-agent/0.2"

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
        elif self.path == "/activity":
            if not self._authorized():
                logger.warning("Rejected unauthorized /activity request")
                self._send(401, {"detail": "invalid or missing token"})
                return
            self._send(200, _collect_activity())
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
    if "--idle-reporter" in sys.argv:
        _run_idle_reporter()
    else:
        main()
