from __future__ import annotations

import os
import unittest

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts.analyzer.local_extract import choose_qwen_model


class RealQwenHealthTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("WATCHBRIEF_RUN_REAL_QWEN_TESTS") == "1", "real Qwen health check is opt-in")
    def test_lm_studio_has_qwen_family_model(self) -> None:
        model = choose_qwen_model()
        self.assertIn("qwen", model.lower())


if __name__ == "__main__":
    unittest.main()
