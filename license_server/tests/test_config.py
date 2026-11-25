"""Copyright (c) 2026 Joshua Almeida"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from config import ConfigError, load_settings  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_refuses_live_secret_key(self):
        env = {
            "STRIPE_SECRET_KEY": "sk_live_this_is_not_a_real_key_but_looks_live",
            "STRIPE_WEBHOOK_SECRET": "whsec_test",
            "STRIPE_PRICE_ID": "price_test",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigError) as ctx:
                load_settings(require_stripe=True)
        self.assertIn("live", str(ctx.exception).lower())

    def test_refuses_live_restricted_key(self):
        env = {
            "STRIPE_SECRET_KEY": "rk_live_this_is_not_a_real_key_but_looks_live",
            "STRIPE_WEBHOOK_SECRET": "whsec_test",
            "STRIPE_PRICE_ID": "price_test",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigError):
                load_settings(require_stripe=True)

    def test_accepts_test_restricted_key(self):
        env = {
            "STRIPE_SECRET_KEY": "rk_test_this_is_not_a_real_key",
            "STRIPE_WEBHOOK_SECRET": "whsec_test",
            "STRIPE_PRICE_ID": "price_test123",
            "PUBLIC_BASE_URL": "http://127.0.0.1:8787",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings(require_stripe=True)
        self.assertEqual(settings.stripe_price_id, "price_test123")
        self.assertTrue(settings.public_base_url.startswith("http://127.0.0.1"))

    def test_requires_webhook_and_price(self):
        env = {"STRIPE_SECRET_KEY": "sk_test_abc", "STRIPE_WEBHOOK_SECRET": "", "STRIPE_PRICE_ID": ""}
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigError):
                load_settings(require_stripe=True)


if __name__ == "__main__":
    unittest.main()
