#!/usr/bin/env python3
"""
Copyright (c) 2026 Joshua Almeida

Nexus AI license server — Stripe Checkout (test mode) + license verification.

Run from this directory after setting environment variables from .env.example:

    python app.py

This process does not deploy itself. Do not point it at live Stripe keys.
"""
from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from config import ConfigError, load_settings  # noqa: E402
import db as store  # noqa: E402
from http_api import Handler  # noqa: E402


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def build_handler(settings, conn, client, webhook_construct):
    api = Handler(
        settings=settings,
        conn=conn,
        client=client,
        webhook_construct=webhook_construct,
    )

    class LicenseHTTPRequestHandler(BaseHTTPRequestHandler):
        server_version = "NexusAILicense/0.2"

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 1_000_000:
                return b""
            return self.rfile.read(length) if length else b""

        def _dispatch(self, method: str) -> None:
            headers = {k: v for k, v in self.headers.items()}
            ip = self.client_address[0] if self.client_address else "unknown"
            resp = api.handle(method, self.path, headers, self._read_body(), client_ip=ip)
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(resp.body)

        def do_GET(self):  # noqa: N802
            self._dispatch("GET")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

        def do_OPTIONS(self):  # noqa: N802
            self._dispatch("OPTIONS")

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    return LicenseHTTPRequestHandler


def make_stripe_client(settings):
    from stripe import StripeClient

    return StripeClient(settings.stripe_secret_key, stripe_version=settings.stripe_api_version)


def make_webhook_construct():
    from stripe import Webhook

    def construct(payload: bytes, sig: str, secret: str):
        if isinstance(payload, str):
            payload_in = payload
        else:
            payload_in = payload.decode("utf-8")
        return Webhook.construct_event(payload_in, sig, secret)

    return construct


def main() -> int:
    _load_dotenv(HERE / ".env")
    try:
        settings = load_settings(require_stripe=True)
    except ConfigError as e:
        sys.stderr.write(str(e) + "\n")
        return 2

    conn = store.connect(settings.db_path)
    try:
        client = make_stripe_client(settings)
    except Exception as e:
        sys.stderr.write(f"Failed to create Stripe client: {type(e).__name__}\n")
        return 2

    handler = build_handler(settings, conn, client, make_webhook_construct())
    server = ThreadingHTTPServer(("127.0.0.1", settings.port), handler)
    sys.stderr.write(
        f"Nexus AI license server (test mode) on {settings.public_base_url}\n"
        f"Webhook endpoint: {settings.public_base_url}/v1/webhook\n"
        "Live Stripe keys are refused. Do not deploy without an explicit OK.\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nStopping.\n")
    finally:
        server.server_close()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
