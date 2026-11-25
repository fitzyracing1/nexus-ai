#!/usr/bin/env python3
"""
Copyright (c) 2026 Joshua Almeida

Create a test-mode Stripe Product + $5/month Price and print the price id.
Refuses live keys. Does not print the secret key.

    STRIPE_SECRET_KEY=rk_test_... python create_test_price.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from keys import is_live_mode_stripe_key, is_test_mode_stripe_key  # noqa: E402
from config import STRIPE_API_VERSION  # noqa: E402


def main() -> int:
    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        sys.stderr.write("Set STRIPE_SECRET_KEY to a test-mode key first.\n")
        return 2
    if is_live_mode_stripe_key(key):
        sys.stderr.write("Refusing live Stripe keys. Use rk_test_ or sk_test_ only.\n")
        return 2
    if not is_test_mode_stripe_key(key):
        sys.stderr.write("STRIPE_SECRET_KEY must be rk_test_ or sk_test_.\n")
        return 2

    from stripe import StripeClient

    client = StripeClient(key, stripe_version=STRIPE_API_VERSION)
    products = getattr(getattr(client, "v1", client), "products")
    prices = getattr(getattr(client, "v1", client), "prices")
    try:
        product = products.create(
            params={
                "name": "Nexus AI",
                "description": "Nexus AI Chrome extension — 7-day trial, then $5/month.",
                "tax_code": "txcd_10103000",  # Software as a service, business use (optional until Tax is on)
            }
        )
    except TypeError:
        product = products.create(
            name="Nexus AI",
            description="Nexus AI Chrome extension — 7-day trial, then $5/month.",
        )
    product_id = product["id"] if isinstance(product, dict) else product.id
    try:
        price = prices.create(
            params={
                "product": product_id,
                "currency": "usd",
                "unit_amount": 500,
                "recurring": {"interval": "month"},
                "tax_behavior": "exclusive",
            }
        )
    except TypeError:
        price = prices.create(
            product=product_id,
            currency="usd",
            unit_amount=500,
            recurring={"interval": "month"},
        )
    price_id = price["id"] if isinstance(price, dict) else price.id
    sys.stdout.write(
        f"Created test-mode product {product_id}\n"
        f"Set this in your environment:\n"
        f"STRIPE_PRICE_ID={price_id}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
