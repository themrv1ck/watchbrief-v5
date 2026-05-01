from __future__ import annotations

import unittest

from helpers import ROOT


class PhaseScopeTest(unittest.TestCase):
    def test_no_legacy_live_review_module_exists(self) -> None:
        forbidden_now = [
            "scripts/live_codex_review.py"
        ]
        for relative_path in forbidden_now:
            self.assertFalse((ROOT / relative_path).exists(), f"{relative_path} belongs to a later phase")


if __name__ == "__main__":
    unittest.main()
