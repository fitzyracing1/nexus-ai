"""
Copyright (c) 2026 Joshua Almeida

License-key generation and Stripe trial-day calculations.
No I/O, no Stripe calls — safe to unit test.
"""
from __future__ import annotations

import re
import secrets

TRIAL_DAYS = 7
DAY_MS = 24 * 60 * 60 * 1000
TRIAL_MS = TRIAL_DAYS * DAY_MS
KEY_RE = re.compile(r"^NEXUS(?:-[A-Z0-9]{4}){3}$")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1


def generate_license_key() -> str:
    parts = ["".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3)]
    return "NEXUS-" + "-".join(parts)


def normalize_license_key(key: str | None) -> str:
    return (key or "").strip().upper()


def is_valid_license_key(key: str | None) -> bool:
    return bool(KEY_RE.fullmatch(normalize_license_key(key)))


def stripe_trial_days(trial_started_at_ms: int | None, now_ms: int) -> int:
    """
    Number of days to put on a Stripe Checkout subscription trial.

    The product trial is 7 days from first extension install. Checkout
    uses the remaining time so a user cannot stack a second full week
    after the local trial ends. If no local start is supplied (checkout
    from a website without the extension), a full 7-day Stripe trial is
    granted.
    """
    if trial_started_at_ms is None:
        return TRIAL_DAYS
    try:
        start = int(trial_started_at_ms)
    except (TypeError, ValueError):
        return TRIAL_DAYS
    remaining = start + TRIAL_MS - int(now_ms)
    if remaining <= 0:
        return 0
    days = (remaining + DAY_MS - 1) // DAY_MS
    return int(min(TRIAL_DAYS, max(0, days)))


def is_test_mode_stripe_key(key: str) -> bool:
    key = (key or "").strip()
    return key.startswith("sk_test_") or key.startswith("rk_test_")


def is_live_mode_stripe_key(key: str) -> bool:
    key = (key or "").strip()
    return key.startswith("sk_live_") or key.startswith("rk_live_")
