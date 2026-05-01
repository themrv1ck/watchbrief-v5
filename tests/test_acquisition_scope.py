from __future__ import annotations

import unittest

from helpers import ROOT


class AcquisitionScopeTest(unittest.TestCase):
    def test_no_legacy_live_review_module_exists(self) -> None:
        self.assertFalse((ROOT / "scripts" / "live_codex_review.py").exists())

    def test_acquisition_modules_do_not_import_renderer_or_analyzer_execution(self) -> None:
        modules = [
            ROOT / "scripts" / "resolver.py",
            ROOT / "scripts" / "subtitle_fetcher.py",
            ROOT / "scripts" / "bilibili_content_provider.py",
            ROOT / "scripts" / "audio_downloader.py",
            ROOT / "scripts" / "transcriber.py",
        ]
        forbidden = [
            "from .renderer",
            "import renderer",
            "from .validator",
            "import validator",
            "codex_review",
            "prompts",
        ]
        for path in modules:
            source = path.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, source, f"{path.name} must not cross into rendering or analysis")


if __name__ == "__main__":
    unittest.main()
