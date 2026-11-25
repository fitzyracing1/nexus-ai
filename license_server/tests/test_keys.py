"""Copyright (c) 2026 Joshua Almeida"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from keys import (  # noqa: E402
    DAY_MS,
    TRIAL_DAYS,
    generate_license_key,
    is_live_mode_stripe_key,
    is_test_mode_stripe_key,
    is_valid_license_key,
    normalize_license_key,
    stripe_trial_days,
)


class KeyTests(unittest.TestCase):
    def test_generated_keys_are_valid_and_unique(self):
        keys = {generate_license_key() for _ in range(50)}
        self.assertEqual(len(keys), 50)
        for k in keys:
            self.assertTrue(is_valid_license_key(k))
            self.assertTrue(k.startswith("NEXUS-"))

    def test_normalize_and_reject_garbage(self):
        self.assertEqual(normalize_license_key(" nexus-abcd-efgh-ijkl "), "NEXUS-ABCD-EFGH-IJKL")
        self.assertTrue(is_valid_license_key("NEXUS-ABCD-EFGH-IJKL"))
        self.assertFalse(is_valid_license_key("NEXUS-AB"))
        self.assertFalse(is_valid_license_key(""))
        self.assertFalse(is_valid_license_key(None))

    def test_stripe_trial_days_aligns_with_remaining_local_trial(self):
        start = 1_000_000_000_000
        self.assertEqual(stripe_trial_days(start, start), TRIAL_DAYS)
        self.assertEqual(stripe_trial_days(start, start + 3 * DAY_MS), 4)
        self.assertEqual(stripe_trial_days(start, start + 7 * DAY_MS), 0)
        self.assertEqual(stripe_trial_days(start, start + 8 * DAY_MS), 0)
        # 1 ms remaining still rounds up to 1 day of Stripe trial.
        self.assertEqual(stripe_trial_days(start, start + 7 * DAY_MS - 1), 1)
        # No local start → full 7-day Stripe trial.
        self.assertEqual(stripe_trial_days(None, start), TRIAL_DAYS)

    def test_key_mode_detection(self):
        self.assertTrue(is_test_mode_stripe_key("rk_test_abc"))
        self.assertTrue(is_test_mode_stripe_key("sk_test_abc"))
        self.assertFalse(is_test_mode_stripe_key("sk_live_abc"))
        self.assertTrue(is_live_mode_stripe_key("sk_live_abc"))
        self.assertTrue(is_live_mode_stripe_key("rk_live_abc"))
        self.assertFalse(is_live_mode_stripe_key("rk_test_abc"))


if __name__ == "__main__":
    unittest.main()
