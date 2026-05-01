from __future__ import annotations

import unittest

from helpers import ROOT


class TranscriptSourceScopeTest(unittest.TestCase):
    def test_transcript_adapter_does_not_import_download_or_transcription_clients(self) -> None:
        source = (ROOT / "scripts" / "transcript_source_adapter.py").read_text(encoding="utf-8").lower()
        forbidden_imports = [
            "import yt_dlp",
            "from yt_dlp",
            "import openai",
            "from openai",
            "import requests",
            "from requests",
            "import urllib",
            "from urllib",
            "import httpx",
            "from httpx",
            "import subprocess",
            "from subprocess",
            "import whisper",
            "from whisper",
        ]
        for forbidden in forbidden_imports:
            self.assertNotIn(forbidden, source)

    def test_no_legacy_live_review_module_exists(self) -> None:
        forbidden_now = [
            "scripts/live_codex_review.py"
        ]
        for relative_path in forbidden_now:
            self.assertFalse((ROOT / relative_path).exists(), f"{relative_path} belongs to a later phase")


if __name__ == "__main__":
    unittest.main()
