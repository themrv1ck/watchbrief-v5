from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.watchbrief_codex_state import (
    collect_watchbrief_codex_status,
    read_current_watchbrief_codex_account,
    switch_watchbrief_codex_account,
)


class WatchBriefCodexStateTest(unittest.TestCase):
    def test_reads_current_account_and_collects_status_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "accountA"
            home.mkdir()
            (home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
            (home / "config.toml").write_text("model = 'gpt-5.5'\n", encoding="utf-8")
            (home / "sessions").mkdir()
            (home / "sessions" / "one.jsonl").write_text("{}\n", encoding="utf-8")
            (root / "current").write_text("accountA\n", encoding="utf-8")

            status = collect_watchbrief_codex_status(root=root)

        self.assertEqual(status["current_account"], "accountA")
        self.assertTrue(status["ready"])
        self.assertEqual(status["sessions_count"], 1)
        self.assertNotIn("secret", str(status))

    def test_switch_writes_current_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "accountB").mkdir()
            (root / "accountB" / "auth.json").write_text("{}", encoding="utf-8")
            (root / "accountB" / "config.toml").write_text("", encoding="utf-8")

            status = switch_watchbrief_codex_account("accountB", root=root)

            self.assertEqual(read_current_watchbrief_codex_account(root), "accountB")
            self.assertEqual(status["current_account"], "accountB")

    def test_stale_current_account_falls_back_to_available_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "current").write_text("missing-account\n", encoding="utf-8")
            (root / "account2").mkdir()
            (root / "account2" / "auth.json").write_text("{}", encoding="utf-8")
            (root / "account2" / "config.toml").write_text("", encoding="utf-8")

            self.assertEqual(read_current_watchbrief_codex_account(root), "account2")


if __name__ == "__main__":
    unittest.main()
