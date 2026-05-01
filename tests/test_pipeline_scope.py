from __future__ import annotations

import unittest

from helpers import ROOT


class PipelineScopeTest(unittest.TestCase):
    def test_pipeline_does_not_import_live_model_clients(self) -> None:
        source = (ROOT / "scripts" / "video_pipeline.py").read_text(encoding="utf-8").lower()
        forbidden = [
            "import openai",
            "from openai",
            "import requests",
            "from requests",
            "live_codex_review",
        ]
        for marker in forbidden:
            self.assertNotIn(marker, source)

    def test_pipeline_contract_documents_no_prefetch_and_mock_review_only(self) -> None:
        text = (ROOT / "CONTRACT_V5.md").read_text(encoding="utf-8")
        self.assertIn("mock/manual review response provider", text)
        self.assertIn("prefetching subtitles, audio, or transcripts", text)
        self.assertIn("merging list transcripts into one analysis blob", text)


if __name__ == "__main__":
    unittest.main()
