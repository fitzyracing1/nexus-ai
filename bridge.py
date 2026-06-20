"""
ai-browser bridge v0.2 — local HTTP relay between an AI client and the
Chrome extension that drives the user's active tab.

  Auth:        every command/result/poll requires the auth token from config.json
               (header: 'X-Auth-Token: ...' or 'Authorization: Bearer ...').
  Free tier:   25 commands/day without a license; resets at UTC midnight.
  Licensed:    unlimited. Activate via POST /activate with {license_key, auth_token}.

Endpoints on 127.0.0.1:17777:
  POST /command       protected  AI client → bridge
  GET  /next-command  protected  extension → bridge (long-poll)
  POST /result        protected  extension → bridge
  POST /activate      protected  activate a LemonSqueezy license key
  GET  /license       public     status, free-tier counter, license info (masked)
  GET  /health        public     liveness
  GET  /status        public     queue depth, last result
"""
import json
import os
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import license as licmod  # noqa: this is our license.py, not the stdlib
from setup_page import SETUP_HTML


HOST = "127.0.0.1"
PORT = int(os.environ.get("AI_BROWSER_PORT", "17777"))
POLL_TIMEOUT_S = 5  # short long-poll so MV3 service workers can complete a poll within their lifecycle
DEFAULT_CMD_TIMEOUT_S = 30
VERSION = "ai-browser-bridge/0.2"

state_lock = threading.Lock()
pending_commands = deque()
command_available = threading.Condition(state_lock)
results = {}
last_result = None
last_poll_at = 0.0


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def extract_token(headers) -> str | None:
    tok = headers.get("X-Auth-Token") or ""
    if tok:
        return tok.strip()
    auth = headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = VERSION

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Auth-Token, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _auth_ok(self) -> bool:
        cfg = licmod.load_config()
        tok = extract_token(self.headers)
        return bool(tok) and tok == cfg["auth_token"]

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Auth-Token, Authorization")
        self.end_headers()

    # ---------------- routes ----------------
    def _send_html(self, status, body):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/setup"):
            cfg = licmod.load_config()
            html = SETUP_HTML.replace("__TOKEN__", cfg["auth_token"])
            self._send_html(200, html)
            return
        if self.path.startswith("/health"):
            self._send_json(200, {"ok": True, "version": VERSION})
        elif self.path.startswith("/license"):
            cfg = licmod.load_config()
            licmod.revalidate_if_stale(cfg)
            self._send_json(200, {"ok": True, **licmod.license_status(cfg)})
        elif self.path.startswith("/status"):
            with state_lock:
                self._send_json(200, {
                    "ok": True,
                    "pending": len(pending_commands),
                    "last_result": last_result,
                    "extension_last_poll_seconds_ago":
                        round(time.time() - last_poll_at, 2) if last_poll_at else None,
                })
        elif self.path.startswith("/next-command"):
            if not self._auth_ok():
                self._send_json(401, {"ok": False, "error": "missing or invalid auth token"})
                return
            self._handle_next_command()
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path.startswith("/command"):
            if not self._auth_ok():
                self._send_json(401, {"ok": False, "error": "missing or invalid auth token"})
                return
            self._handle_command()
        elif self.path.startswith("/result"):
            if not self._auth_ok():
                self._send_json(401, {"ok": False, "error": "missing or invalid auth token"})
                return
            self._handle_result()
        elif self.path.startswith("/activate"):
            if not self._auth_ok():
                self._send_json(401, {"ok": False, "error": "missing or invalid auth token"})
                return
            self._handle_activate()
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    # ---------------- handlers ----------------
    def _handle_command(self):
        body = self._read_json()
        action = body.get("action")
        args = body.get("args", {}) or {}
        timeout_s = float(body.get("timeout_ms", DEFAULT_CMD_TIMEOUT_S * 1000)) / 1000.0
        if not action:
            self._send_json(400, {"ok": False, "error": "missing 'action'"})
            return

        # license / rate-limit gate
        cfg = licmod.load_config()
        licmod.revalidate_if_stale(cfg)
        allowed, info = licmod.check_and_increment(cfg)
        if not allowed:
            self._send_json(402, {
                "ok": False,
                "error": (f"free tier limit reached ({info['limit']} commands/day). "
                          "Activate a license to remove this limit."),
                "tier": info,
            })
            return

        request_id = uuid.uuid4().hex
        evt = threading.Event()
        with state_lock:
            results[request_id] = {"event": evt, "payload": None}
            pending_commands.append({"request_id": request_id, "action": action, "args": args})
            command_available.notify()

        log(f"-> queued {action} {request_id[:8]} tier={info}")

        if evt.wait(timeout_s):
            with state_lock:
                rec = results.pop(request_id, None)
                payload = rec["payload"] if rec else None
            if payload is None:
                payload = {"ok": False, "error": "no payload"}
            payload["tier"] = info
            self._send_json(200, payload)
        else:
            with state_lock:
                results.pop(request_id, None)
                for i, c in enumerate(pending_commands):
                    if c["request_id"] == request_id:
                        del pending_commands[i]
                        break
            self._send_json(504, {
                "ok": False,
                "error": f"timeout after {timeout_s:.1f}s waiting for extension. "
                         f"Is the extension installed, connected, and is a tab open?",
                "tier": info,
            })

    def _handle_next_command(self):
        global last_poll_at
        with state_lock:
            last_poll_at = time.time()
            deadline = time.time() + POLL_TIMEOUT_S
            while not pending_commands:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self.send_response(204)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    return
                command_available.wait(timeout=remaining)
            cmd = pending_commands.popleft()
        log(f"<- ext picked up {cmd['action']} {cmd['request_id'][:8]}")
        self._send_json(200, cmd)

    def _handle_result(self):
        global last_result
        body = self._read_json()
        request_id = body.get("request_id")
        if not request_id:
            self._send_json(400, {"ok": False, "error": "missing request_id"})
            return
        with state_lock:
            rec = results.get(request_id)
            if rec is None:
                self._send_json(200, {"ok": True, "note": "no waiter (timed out?)"})
                return
            rec["payload"] = body
            rec["event"].set()
            last_result = {
                "request_id": request_id,
                "ok": body.get("ok"),
                "error": body.get("error"),
                "ts": time.time(),
            }
        log(f"== result {request_id[:8]} ok={body.get('ok')} err={body.get('error')}")
        self._send_json(200, {"ok": True})

    def _handle_activate(self):
        body = self._read_json()
        key = (body.get("license_key") or "").strip()
        if not key:
            self._send_json(400, {"ok": False, "error": "missing license_key"})
            return
        cfg = licmod.load_config()
        res = licmod.activate_license(cfg, key)
        status_code = 200 if res["ok"] else 400
        log(f"== activate ok={res['ok']} msg={res['message']}")
        self._send_json(status_code, {
            "ok": res["ok"],
            "message": res["message"],
            "license_status": licmod.license_status(cfg),
        })


# ---------------- bootstrap ----------------
def banner(cfg: dict):
    cd = licmod.config_dir()
    status = licmod.license_status(cfg)
    setup_url = f"http://{HOST}:{PORT}/setup"
    print("=" * 66)
    print(f"  {VERSION}")
    print()
    print(f"  >>>  Open this in your browser to finish setup:")
    print(f"  >>>      {setup_url}")
    print()
    print(f"  listening on http://{HOST}:{PORT}")
    print(f"  config:      {cd}")
    if status["licensed"]:
        print(f"  license:     ACTIVE  key={status['license_key_masked']}")
    else:
        used = status["used_today"] or 0
        rem = status["remaining_today"] if status["remaining_today"] is not None else "?"
        print(f"  license:     FREE TIER  ({used}/{status['free_tier_limit']} used today, {rem} remaining)")
    if not status["ls_configured"]:
        print(f"  (LemonSqueezy not configured — any non-empty key activates in dev mode)")
    print("=" * 66, flush=True)


def maybe_open_setup():
    """If AI_BROWSER_OPEN_SETUP=1 (default on first install), open the setup
    page in the user's default browser."""
    if os.environ.get("AI_BROWSER_OPEN_SETUP") == "0":
        return
    # Only open if this looks like a first run: no license_checked_at yet
    cfg = licmod.load_config()
    marker = licmod.config_dir() / ".setup_shown"
    if marker.exists() and cfg.get("license_checked_at") is not None:
        return
    try:
        import webbrowser
        webbrowser.open(f"http://{HOST}:{PORT}/setup")
        marker.write_text(str(int(time.time())), encoding="utf-8")
    except Exception:
        pass


def cli_activate(key: str):
    cfg = licmod.load_config()
    res = licmod.activate_license(cfg, key)
    print(f"activate ok={res['ok']}: {res['message']}")
    sys.exit(0 if res["ok"] else 1)


def main():
    # CLI: ai-browser-bridge activate <key>
    if len(sys.argv) >= 3 and sys.argv[1] == "activate":
        cli_activate(sys.argv[2])
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "-V"):
        print(VERSION)
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("--help", "-h"):
        print(f"{VERSION}")
        print("usage: ai-browser-bridge [--version] [activate <license_key>]")
        print("Starts the local HTTP bridge on http://127.0.0.1:17777")
        print("Open http://127.0.0.1:17777/setup in your browser to finish setup.")
        return

    cfg = licmod.load_config()
    banner(cfg)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    # Spawn-open the setup page in a thread so it doesn't block startup
    threading.Thread(target=maybe_open_setup, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutdown")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
