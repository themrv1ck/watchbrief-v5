from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts.acquisition_errors import AcquisitionError
from scripts.resolver import resolve_url
from scripts.subtitle_fetcher import fetch_platform_subtitles


RUN_REAL = os.environ.get("WATCHBRIEF_V5_RUN_REAL_SMOKE") == "1"
REAL_URL = os.environ.get("WATCHBRIEF_V5_REAL_SMOKE_URL", "https://www.youtube.com/watch?v=BaW_jenozKc")


@unittest.skipUnless(RUN_REAL, "set WATCHBRIEF_V5_RUN_REAL_SMOKE=1 to run real acquisition smoke tests")
class RealAcquisitionSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("yt-dlp") is None:
            self.skipTest("yt-dlp is not installed")

    def test_real_resolver_smoke_or_classified_failure(self) -> None:
        try:
            resolved = resolve_url(REAL_URL, timeout=90)
        except AcquisitionError as exc:
            self.skipTest(f"classified resolver failure: {exc.reason_code}: {exc.message}")
        self.assertIn(resolved["source_kind"], {"single", "list"})
        self.assertTrue(resolved["videos"])

    def test_real_subtitle_smoke_or_classified_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                result = fetch_platform_subtitles(REAL_URL, Path(temp_dir), timeout=120)
            except AcquisitionError as exc:
                self.skipTest(f"classified subtitle failure: {exc.reason_code}: {exc.message}")
            self.assertEqual(result.material["material_version"], "watchbrief_v5.transcript_material.v1")
            self.assertTrue(result.material["segments"])


if __name__ == "__main__":
    unittest.main()
