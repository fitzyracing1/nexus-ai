"""Copyright (c) 2026 Joshua Almeida"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import billing  # noqa: E402
import db as store  # noqa: E402
from config import Settings, STRIPE_API_VERSION  # noqa: E402
from http_api import Handler, RateLimiter  # noqa: E402
from keys import TRIAL_DAYS  # noqa: E402


class FakeSessions:
    def __init__(self):
        self.calls = []
        self.n = 0

    def create(self, params=None, **kwargs):
        payload = params or kwargs
        self.calls.append(payload)
        self.n += 1
        return {"id": f"cs_test_{self.n}", "url": f"https://checkout.stripe.com/c/pay/cs_test_{self.n}"}


class FakePortal:
    def __init__(self):
        self.calls = []

    def create(self, params=None, **kwargs):
        payload = params or kwargs
        self.calls.append(payload)
        return {"url": "https://billing.stripe.com/p/session/test"}


class FakeClient:
    def __init__(self):
        self.v1 = SimpleNamespace(
            checkout=SimpleNamespace(sessions=FakeSessions()),
            billing_portal=SimpleNamespace(sessions=FakePortal()),
            subscriptions=SimpleNamespace(retrieve=self._retrieve),
        )
        self._subs = {}

    def _retrieve(self, sub_id):
        return self._subs.get(sub_id)


def settings(tmp: Path) -> Settings:
    return Settings(
        stripe_secret_key="rk_test_fake",
        stripe_webhook_secret="whsec_fake",
        stripe_price_id="price_test123",
        public_base_url="http://127.0.0.1:8787",
        db_path=tmp / "t.sqlite",
        port=8787,
        activate_redirect="https://fitzyracing1.github.io/nexus-ai/?nexus_license={key}",
        stripe_api_version=STRIPE_API_VERSION,
    )


class BillingHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.id().replace(".", "_"))
        # Use a real temp file under the test module dir? unittest cwd is fine with Named temp
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.settings = settings(self.dir)
        self.conn = store.connect(self.settings.db_path)
        self.client = FakeClient()
        self.now = 1_700_000_000_000
        self.handler = Handler(
            settings=self.settings,
            conn=self.conn,
            client=self.client,
            webhook_construct=None,
            now_ms=lambda: self.now,
        )

    def tearDown(self):
        self.conn.close()
        self._td.cleanup()

    def call(self, method, path, payload=None, headers=None, ip="127.0.0.1"):
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Origin": "chrome-extension://abcdef"}
        if headers:
            hdrs.update(headers)
        return self.handler.handle(method, path, hdrs, body, client_ip=ip)

    def test_health(self):
        resp = self.call("GET", "/health")
        self.assertEqual(resp.status, 200)
        self.assertTrue(json.loads(resp.body)["ok"])

    def test_checkout_uses_remaining_trial_and_omits_payment_method_types(self):
        # 3 days already used → 4 days of Stripe trial
        trial_started = self.now - 3 * 24 * 60 * 60 * 1000
        resp = self.call("POST", "/v1/checkout", {"install_id": "inst-1", "trial_started_at": trial_started})
        self.assertEqual(resp.status, 200, resp.body)
        data = json.loads(resp.body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["trial_days"], 4)
        self.assertTrue(data["url"].startswith("https://checkout.stripe.com/"))
        params = self.client.v1.checkout.sessions.calls[0]
        self.assertEqual(params["mode"], "subscription")
        self.assertNotIn("payment_method_types", params)
        self.assertEqual(params["payment_method_collection"], "always")
        self.assertEqual(params["subscription_data"]["trial_period_days"], 4)
        self.assertEqual(params["line_items"][0]["price"], "price_test123")
        self.assertIn("integration_identifier", params)
        # License key is not returned before payment/trial confirmation.
        self.assertNotIn("license_key", data)

    def test_checkout_after_trial_has_no_stripe_trial(self):
        trial_started = self.now - 10 * 24 * 60 * 60 * 1000
        resp = self.call("POST", "/v1/checkout", {"install_id": "inst-2", "trial_started_at": trial_started})
        self.assertEqual(resp.status, 200, resp.body)
        self.assertEqual(json.loads(resp.body)["trial_days"], 0)
        params = self.client.v1.checkout.sessions.calls[0]
        self.assertNotIn("trial_period_days", params["subscription_data"])

    def test_checkout_without_local_start_gets_full_trial(self):
        resp = self.call("POST", "/v1/checkout", {})
        self.assertEqual(json.loads(resp.body)["trial_days"], TRIAL_DAYS)

    def test_webhook_checkout_completed_activates_license_and_is_idempotent(self):
        start = self.call("POST", "/v1/checkout", {"install_id": "inst-3", "trial_started_at": self.now})
        session_id = json.loads(start.body)["session_id"]
        pending = store.get_by_session(self.conn, session_id)
        key = pending["license_key"]

        event = {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": session_id,
                    "payment_status": "no_payment_required",
                    "customer": "cus_123",
                    "customer_details": {"email": "buyer@example.com"},
                    "subscription": "sub_123",
                    "metadata": {"license_key": key, "install_id": "inst-3"},
                }
            },
        }
        self.client._subs["sub_123"] = {
            "id": "sub_123",
            "status": "trialing",
            "customer": "cus_123",
            "trial_end": 1_800_000_000,
            "current_period_end": 1_800_000_000,
            "metadata": {"license_key": key},
        }

        first = billing.handle_event(self.conn, event, client=self.client)
        self.assertTrue(first["ok"])
        rec = store.get_by_key(self.conn, key)
        self.assertEqual(rec["status"], "trialing")
        self.assertEqual(rec["stripe_customer_id"], "cus_123")
        self.assertEqual(rec["email"], "buyer@example.com")

        second = billing.handle_event(self.conn, event, client=self.client)
        self.assertTrue(second["duplicate"])

        status = self.call("GET", f"/v1/checkout/{session_id}")
        body = json.loads(status.body)
        self.assertEqual(body["license_key"], key)
        self.assertEqual(body["status"], "trialing")

        verify = self.call("POST", "/v1/license/verify", {"license_key": key})
        v = json.loads(verify.body)
        self.assertTrue(v["ok"])
        self.assertTrue(v["unlocked"])
        self.assertEqual(v["status"], "trialing")

    def test_subscription_updated_and_payment_failed(self):
        store.insert_pending(self.conn, license_key="NEXUS-AAAA-BBBB-CCCC", install_id="x", checkout_session_id="cs_x")
        store.update_license(
            self.conn,
            "NEXUS-AAAA-BBBB-CCCC",
            stripe_subscription_id="sub_x",
            stripe_customer_id="cus_x",
            status="active",
        )
        billing.handle_event(
            self.conn,
            {
                "id": "evt_sub_upd",
                "type": "customer.subscription.updated",
                "data": {
                    "object": {
                        "id": "sub_x",
                        "status": "active",
                        "customer": "cus_x",
                        "current_period_end": 1_900_000_000,
                        "metadata": {"license_key": "NEXUS-AAAA-BBBB-CCCC"},
                    }
                },
            },
        )
        rec = store.get_by_key(self.conn, "NEXUS-AAAA-BBBB-CCCC")
        self.assertEqual(rec["status"], "active")
        self.assertEqual(rec["current_period_end"], 1_900_000_000)

        billing.handle_event(
            self.conn,
            {
                "id": "evt_fail",
                "type": "invoice.payment_failed",
                "data": {
                    "object": {
                        "customer": "cus_x",
                        "subscription": "sub_x",
                    }
                },
            },
            client=self.client,
        )
        rec = store.get_by_key(self.conn, "NEXUS-AAAA-BBBB-CCCC")
        self.assertEqual(rec["status"], "past_due")

    def test_refund_and_dispute_resolve_through_customer(self):
        store.insert_pending(self.conn, license_key="NEXUS-DDDD-EEEE-FFFF", install_id="y", checkout_session_id="cs_y")
        store.update_license(
            self.conn,
            "NEXUS-DDDD-EEEE-FFFF",
            stripe_subscription_id="sub_y",
            stripe_customer_id="cus_y",
            status="active",
        )
        billing.handle_event(
            self.conn,
            {
                "id": "evt_refund",
                "type": "charge.refunded",
                "data": {"object": {"id": "ch_1", "customer": "cus_y", "invoice": {"id": "in_1", "subscription": "sub_y"}}},
            },
        )
        self.assertEqual(store.get_by_key(self.conn, "NEXUS-DDDD-EEEE-FFFF")["status"], "refunded")

        # reset and dispute
        store.update_license(self.conn, "NEXUS-DDDD-EEEE-FFFF", status="active")
        billing.handle_event(
            self.conn,
            {
                "id": "evt_disp",
                "type": "charge.dispute.created",
                "data": {"object": {"id": "dp_1", "customer": "cus_y", "invoice": "in_1"}},
            },
        )
        self.assertEqual(store.get_by_key(self.conn, "NEXUS-DDDD-EEEE-FFFF")["status"], "disputed")

    def test_webhook_rejects_without_constructor_or_bad_signature(self):
        resp = self.call("POST", "/v1/webhook", {})
        self.assertEqual(resp.status, 500)

        def boom(_payload, _sig, _secret):
            raise ValueError("bad sig")

        self.handler.webhook_construct = boom
        resp = self.call("POST", "/v1/webhook", {}, headers={"Stripe-Signature": "nope"})
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(resp.body)["error"], "invalid_signature")

    def test_cors_and_rate_limit(self):
        resp = self.call("OPTIONS", "/v1/checkout")
        self.assertEqual(resp.status, 204)
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "chrome-extension://abcdef")

        self.handler.limiter = RateLimiter(max_per_minute=2)
        self.call("POST", "/v1/license/verify", {"license_key": "NEXUS-AAAA-BBBB-CCCC"}, ip="1.2.3.4")
        self.call("POST", "/v1/license/verify", {"license_key": "NEXUS-AAAA-BBBB-CCCC"}, ip="1.2.3.4")
        third = self.call("POST", "/v1/license/verify", {"license_key": "NEXUS-AAAA-BBBB-CCCC"}, ip="1.2.3.4")
        self.assertEqual(third.status, 429)

    def test_portal_needs_customer(self):
        store.insert_pending(self.conn, license_key="NEXUS-GGGG-HHHH-IIII", install_id="z", checkout_session_id="cs_z")
        missing = self.call("POST", "/v1/portal", {"license_key": "NEXUS-GGGG-HHHH-IIII"})
        self.assertEqual(missing.status, 404)
        store.update_license(self.conn, "NEXUS-GGGG-HHHH-IIII", stripe_customer_id="cus_z", status="active")
        ok = self.call("POST", "/v1/portal", {"license_key": "NEXUS-GGGG-HHHH-IIII"})
        self.assertEqual(ok.status, 200)
        self.assertIn("billing.stripe.com", json.loads(ok.body)["url"])

    def test_unknown_and_invalid_license(self):
        bad = self.call("POST", "/v1/license/verify", {"license_key": "nope"})
        self.assertEqual(bad.status, 400)
        missing = self.call("POST", "/v1/license/verify", {"license_key": "NEXUS-ZZZZ-YYYY-XXXX"})
        self.assertEqual(missing.status, 404)


if __name__ == "__main__":
    unittest.main()
