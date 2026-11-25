"""Copyright (c) 2026 Joshua Almeida"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE_KEY = re.compile(r"""['\"]?[sr]k_live_[A-Za-z0-9]{16,}""")
# Allow documentation that only mentions the prefix, not a fake-looking long key.


class NoLiveSecretsTests(unittest.TestCase):
    def test_repo_has_no_live_stripe_keys(self):
        hits = []
        skip_parts = {".git", "node_modules", ".venv", "venv", "data", "__pycache__"}
        for path in ROOT.rglob("*"):
            if any(part in skip_parts for part in path.parts):
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() in {".png", ".zip", ".pyc", ".sqlite"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if LIVE_KEY.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{i}")
        self.assertEqual(hits, [], msg="Live Stripe keys must not be committed:\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
