from __future__ import annotations

import unittest

from helpers import ROOT


class AnalyzerScopeTest(unittest.TestCase):
    def test_analyzer_modules_except_live_adapters_do_not_import_live_clients(self) -> None:
        forbidden_imports = [
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
            "import yt_dlp",
            "from yt_dlp",
            "import playwright",
            "from playwright",
        ]
        for path in (ROOT / "scripts" / "analyzer").glob("*.py"):
            if path.name in {"codex_review.py", "local_extract.py"}:
                continue
            source = path.read_text(encoding="utf-8").lower()
            for forbidden in forbidden_imports:
                self.assertNotIn(forbidden, source, f"{path.name} must not import {forbidden}")

    def test_local_extract_uses_only_local_qwen_endpoint(self) -> None:
        source = (ROOT / "scripts" / "analyzer" / "local_extract.py").read_text(encoding="utf-8").lower()
        self.assertIn("watchbrief_qwen_base_url", source)
        self.assertIn("watchbrief_qwen_api_base", source)
        self.assertIn("127.0.0.1:1234/v1", source)
        self.assertNotIn("import openai", source)
        self.assertNotIn("from openai", source)
        self.assertNotIn("import requests", source)
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("yt-dlp", source)

    def test_codex_review_uses_codex_cli_only_for_explicit_adapter(self) -> None:
        source = (ROOT / "scripts" / "analyzer" / "codex_review.py").read_text(encoding="utf-8").lower()
        self.assertIn("import subprocess", source)
        self.assertIn("codex", source)
        self.assertNotIn("openai_api_key", source)
        for forbidden in (
            "import openai",
            "from openai",
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "urllib.request",
        ):
            self.assertNotIn(forbidden, source)

    def test_phase8_contract_keeps_live_review_explicit(self) -> None:
        text = (ROOT / "CONTRACT_V5.md").read_text(encoding="utf-8")
        self.assertIn("Phase 8 Boundary", text)
        self.assertIn("explicitly enabled", text)
        self.assertIn("mock/manual review path", text)
        self.assertIn("schema, then validator", text)


if __name__ == "__main__":
    unittest.main()
