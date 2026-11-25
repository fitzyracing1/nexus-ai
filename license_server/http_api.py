"""
Copyright (c) 2026 Joshua Almeida

Pure request handler for the license server. The stdlib HTTP wrapper is in app.py.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from keys import is_valid_license_key, normalize_license_key
import billing
import db as store

ALLOWED_ORIGIN_PREFIXES = (
    "chrome-extension://",
    "https://fitzyracing1.github.io",
    "http://127.0.0.1:8787",
    "http://localhost:8787",
    "http://127.0.0.1:17777",
)


class Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}


def json_response(status: int, payload: dict[str, Any], *, origin: str | None = None) -> Response:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
    }
    _apply_cors(headers, origin)
    return Response(status, body, headers)


def html_response(status: int, html: str, *, origin: str | None = None) -> Response:
    headers = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}
    _apply_cors(headers, origin)
    return Response(status, html.encode("utf-8"), headers)


def empty_response(status: int, *, origin: str | None = None) -> Response:
    headers = {}
    _apply_cors(headers, origin)
    return Response(status, b"", headers)


def _apply_cors(headers: dict[str, str], origin: str | None) -> None:
    if origin and origin_allowed(origin):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
        headers["Access-Control-Allow-Headers"] = "Content-Type, Stripe-Signature"
        headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        headers["Access-Control-Max-Age"] = "86400"


def origin_allowed(origin: str) -> bool:
    origin = origin or ""
    return any(origin.startswith(p) for p in ALLOWED_ORIGIN_PREFIXES)


class RateLimiter:
    def __init__(self, max_per_minute: int = 30):
        self.max_per_minute = max_per_minute
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        window = self._hits.setdefault(key, [])
        self._hits[key] = [t for t in window if now - t < 60]
        if len(self._hits[key]) >= self.max_per_minute:
            return False
        self._hits[key].append(now)
        return True


class Handler:
    def __init__(
        self,
        *,
        settings,
        conn,
        client=None,
        webhook_construct: Callable | None = None,
        limiter: RateLimiter | None = None,
        now_ms: Callable[[], int] | None = None,
    ):
        self.settings = settings
        self.conn = conn
        self.client = client
        self.webhook_construct = webhook_construct
        self.limiter = limiter or RateLimiter()
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))

    def handle(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        client_ip: str = "unknown",
    ) -> Response:
        method = method.upper()
        parsed = urlparse(path)
        route = parsed.path
        query = {k: v[-1] if v else "" for k, v in parse_qs(parsed.query).items()}
        origin = headers.get("Origin") or headers.get("origin")

        if method == "OPTIONS":
            return empty_response(204, origin=origin)

        try:
            if method == "GET" and route == "/health":
                return json_response(200, {"ok": True, "service": "nexus-ai-license", "mode": "test"}, origin=origin)

            if method == "POST" and route == "/v1/checkout":
                return self._checkout(headers, body, origin, client_ip)

            if method == "GET" and route.startswith("/v1/checkout/"):
                session_id = route[len("/v1/checkout/") :]
                return self._checkout_status(session_id, origin, client_ip)

            if method == "POST" and route == "/v1/license/verify":
                return self._verify(headers, body, origin, client_ip)

            if method == "POST" and route == "/v1/portal":
                return self._portal(headers, body, origin, client_ip)

            if method == "POST" and route == "/v1/webhook":
                return self._webhook(headers, body)

            if method == "GET" and route == "/success":
                return self._success_page(query.get("session_id") or "")

            if method == "GET" and route in ("/canceled", "/cancelled"):
                return html_response(200, _PAGE_CANCELED)

            if method == "GET" and route == "/portal-done":
                return html_response(200, _PAGE_PORTAL_DONE)

            return json_response(404, {"ok": False, "error": "not_found"}, origin=origin)
        except ValueError as e:
            return json_response(400, {"ok": False, "error": str(e)}, origin=origin)
        except Exception as e:
            # Do not include secrets or environment dumps.
            return json_response(500, {"ok": False, "error": "server_error", "detail": type(e).__name__}, origin=origin)

    def _parse_json(self, body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError("invalid_json") from e
        if not isinstance(data, dict):
            raise ValueError("invalid_json")
        return data

    def _checkout(self, headers, body, origin, client_ip) -> Response:
        if not self.limiter.allow(f"checkout:{client_ip}"):
            return json_response(429, {"ok": False, "error": "rate_limited"}, origin=origin)
        payload = self._parse_json(body)
        install_id = (payload.get("install_id") or "").strip() or None
        trial_started = payload.get("trial_started_at")
        if trial_started is not None:
            try:
                trial_started = int(trial_started)
            except (TypeError, ValueError) as e:
                raise ValueError("invalid_trial_started_at") from e
        if self.client is None:
            raise RuntimeError("stripe_client_missing")
        result = billing.create_checkout(
            client=self.client,
            settings=self.settings,
            conn=self.conn,
            install_id=install_id,
            trial_started_at_ms=trial_started,
            now_ms=self.now_ms(),
        )
        return json_response(200, {"ok": True, **result}, origin=origin)

    def _checkout_status(self, session_id: str, origin, client_ip) -> Response:
        if not self.limiter.allow(f"status:{client_ip}"):
            return json_response(429, {"ok": False, "error": "rate_limited"}, origin=origin)
        session_id = (session_id or "").strip()
        if not session_id.startswith("cs_"):
            raise ValueError("invalid_session_id")
        rec = store.get_by_session(self.conn, session_id)
        if rec is None:
            return json_response(404, {"ok": False, "error": "not_found"}, origin=origin)
        status = rec.get("status") or "pending"
        payload: dict[str, Any] = {"ok": True, "status": status}
        if status in ("active", "trialing"):
            payload["license_key"] = rec["license_key"]
            payload["current_period_end"] = rec.get("current_period_end")
            payload["trial_end"] = rec.get("trial_end")
        return json_response(200, payload, origin=origin)

    def _verify(self, headers, body, origin, client_ip) -> Response:
        if not self.limiter.allow(f"verify:{client_ip}"):
            return json_response(429, {"ok": False, "error": "rate_limited"}, origin=origin)
        payload = self._parse_json(body)
        key = normalize_license_key(payload.get("license_key"))
        if not is_valid_license_key(key):
            return json_response(400, {"ok": False, "error": "invalid_license_key"}, origin=origin)
        rec = store.get_by_key(self.conn, key)
        if rec is None:
            return json_response(404, {"ok": False, "error": "unknown_license"}, origin=origin)
        # Optionally refresh from Stripe when we have a subscription id and a client.
        if self.client and rec.get("stripe_subscription_id"):
            sub = billing.retrieve_subscription(self.client, rec["stripe_subscription_id"])
            if sub:
                rec = billing.apply_subscription(self.conn, license_key=key, subscription=sub) or rec
        return json_response(
            200,
            {
                "ok": True,
                "status": rec.get("status"),
                "trial_end": rec.get("trial_end"),
                "current_period_end": rec.get("current_period_end"),
                "unlocked": rec.get("status") in billing.UNLOCK_STATUSES,
            },
            origin=origin,
        )

    def _portal(self, headers, body, origin, client_ip) -> Response:
        if not self.limiter.allow(f"portal:{client_ip}"):
            return json_response(429, {"ok": False, "error": "rate_limited"}, origin=origin)
        payload = self._parse_json(body)
        key = normalize_license_key(payload.get("license_key"))
        if not is_valid_license_key(key):
            return json_response(400, {"ok": False, "error": "invalid_license_key"}, origin=origin)
        rec = store.get_by_key(self.conn, key)
        if rec is None or not rec.get("stripe_customer_id"):
            return json_response(404, {"ok": False, "error": "unknown_license"}, origin=origin)
        if self.client is None:
            raise RuntimeError("stripe_client_missing")
        result = billing.create_portal(
            client=self.client,
            settings=self.settings,
            customer_id=rec["stripe_customer_id"],
        )
        return json_response(200, {"ok": True, **result}, origin=origin)

    def _webhook(self, headers, body) -> Response:
        sig = headers.get("Stripe-Signature") or headers.get("stripe-signature") or ""
        if self.webhook_construct is None:
            return json_response(500, {"ok": False, "error": "webhook_not_configured"})
        try:
            event = self.webhook_construct(body, sig, self.settings.stripe_webhook_secret)
        except Exception:
            return json_response(400, {"ok": False, "error": "invalid_signature"})
        if hasattr(event, "to_dict"):
            event = event.to_dict()
        elif not isinstance(event, dict):
            event = {
                "id": getattr(event, "id", None),
                "type": getattr(event, "type", None),
                "data": {"object": getattr(getattr(event, "data", None), "object", {})},
            }
        result = billing.handle_event(self.conn, event, client=self.client)
        return json_response(200, {"ok": True, "duplicate": result.get("duplicate", False)})

    def _success_page(self, session_id: str) -> Response:
        rec = store.get_by_session(self.conn, session_id) if session_id else None
        if rec is None or rec.get("status") not in ("active", "trialing"):
            html = _PAGE_SUCCESS_PENDING.format(session_id=session_id or "")
            return html_response(200, html)
        key = rec["license_key"]
        redirect = self.settings.activate_redirect.replace("{key}", key)
        html = _PAGE_SUCCESS.format(license_key=key, redirect=redirect, status=rec.get("status"))
        return html_response(200, html)


def _layout(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Nexus AI</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ margin: 0; background: #09090b; color: #e4e4e7; font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
    main {{ max-width: 640px; margin: 0 auto; padding: 48px 24px 80px; }}
    h1 {{ font-size: 1.75rem; letter-spacing: -0.04em; margin: 0 0 12px; }}
    p, li {{ color: #d4d4d8; }}
    a {{ color: #34d399; }}
    code, .key {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.95em; background: #18181b; border: 1px solid #27272a; padding: 4px 8px; border-radius: 6px; }}
    .key {{ display: inline-block; margin: 8px 0 16px; font-size: 1.05em; color: #6ee7b7; }}
    .btn {{ display: inline-block; margin-top: 8px; padding: 10px 16px; border-radius: 8px; background: #34d399; color: #09090b; font-weight: 600; text-decoration: none; }}
  </style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


_PAGE_CANCELED = _layout(
    "Checkout canceled",
    "<h1>Checkout canceled</h1><p>No charge was made. You can close this tab and try again from the Nexus AI extension popup.</p>",
)

_PAGE_PORTAL_DONE = _layout(
    "Billing updated",
    "<h1>Billing portal</h1><p>You can close this tab and return to the Nexus AI extension. Subscription changes can take a few seconds to appear.</p>",
)

_PAGE_SUCCESS_PENDING = _layout(
    "Finishing checkout",
    """<h1>Finishing your subscription</h1>
    <p>Stripe has not confirmed this checkout yet. If you just paid (or started a trial), wait a few seconds and refresh this page. Session: <code>{session_id}</code></p>
    <p>If this page never shows a license key, open the extension popup and try Subscribe again, or email Fitzyracing1@gmail.com.</p>""",
)

_PAGE_SUCCESS = _layout(
    "You are subscribed",
    """<h1>You are subscribed</h1>
    <p>Status: <strong>{status}</strong>. Paste this license key into the Nexus AI extension popup, or click the button to activate it on this computer.</p>
    <p class="key">{license_key}</p>
    <p><a class="btn" href="{redirect}">Activate in Nexus AI</a></p>
    <p>This is a Stripe test- or live-mode subscription at $5/month after the 7-day trial. Manage or cancel it later from the extension’s “Manage billing” button.</p>""",
)
