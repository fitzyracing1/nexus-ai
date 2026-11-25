"""
Copyright (c) 2026 Joshua Almeida

Environment configuration. Live Stripe keys are refused.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from keys import is_live_mode_stripe_key, is_test_mode_stripe_key

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "licenses.sqlite"
DEFAULT_PORT = 8787
# Latest Stripe API version as of this change. See stripe-best-practices.
STRIPE_API_VERSION = "2026-11-18.endive"


class ConfigError(SystemExit):
    pass


@dataclass
class Settings:
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_price_id: str
    public_base_url: str
    db_path: Path
    port: int
    activate_redirect: str
    stripe_api_version: str = STRIPE_API_VERSION


def load_settings(*, require_stripe: bool = True) -> Settings:
    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    webhook = (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()
    price = (os.environ.get("STRIPE_PRICE_ID") or "").strip()
    base = (os.environ.get("PUBLIC_BASE_URL") or "http://127.0.0.1:8787").rstrip("/")
    db = Path(os.environ.get("LICENSE_DB_PATH") or DEFAULT_DB)
    port = int(os.environ.get("PORT") or DEFAULT_PORT)
    activate = os.environ.get(
        "LICENSE_ACTIVATE_REDIRECT",
        "https://fitzyracing1.github.io/nexus-ai/?nexus_license={key}",
    )

    if require_stripe:
        if not key:
            raise ConfigError(
                "STRIPE_SECRET_KEY is required. Use a test-mode restricted key "
                "(rk_test_...) or a test secret key (sk_test_...). Live keys are refused."
            )
        if is_live_mode_stripe_key(key):
            raise ConfigError(
                "Refusing to start: live Stripe keys (sk_live_ / rk_live_) are not "
                "allowed in this project. Use test mode only until Joshua approves a live launch."
            )
        if not is_test_mode_stripe_key(key):
            raise ConfigError(
                "STRIPE_SECRET_KEY must be a Stripe test-mode key (rk_test_ or sk_test_)."
            )
        if not webhook:
            raise ConfigError(
                "STRIPE_WEBHOOK_SECRET is required (from `stripe listen` or a Dashboard "
                "test-mode webhook endpoint)."
            )
        if not price:
            raise ConfigError(
                "STRIPE_PRICE_ID is required. Create a $5/month test-mode price and set it here."
            )
        if price.startswith("price_live_") or (price.startswith("price_") and "_live_" in price):
            # price ids are typically price_xxxxxxxxxxxxx in both modes; cannot always detect.
            # The live-key check above is the real safeguard.
            pass

    return Settings(
        stripe_secret_key=key,
        stripe_webhook_secret=webhook,
        stripe_price_id=price,
        public_base_url=base,
        db_path=db,
        port=port,
        activate_redirect=activate,
    )
