from __future__ import annotations

import unittest

from helpers import ROOT


class TemplateSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.video_template = (ROOT / "references" / "video_report_v5.html").read_text(encoding="utf-8")
        self.watch_order_template = (ROOT / "references" / "00watch_order_v5.html").read_text(encoding="utf-8")

    def test_video_and_watch_order_templates_are_distinct_sources(self) -> None:
        self.assertNotEqual(self.video_template, self.watch_order_template)

    def test_video_report_template_has_single_video_markers(self) -> None:
        for marker in [
            "视频到底讲了什么？",
            "如果要看，只看哪里？",
            "one-line brief",
            "final-conclusion",
            "feedback-launcher",
        ]:
            self.assertIn(marker, self.video_template)

    def test_video_report_template_excludes_watch_order_markers(self) -> None:
        for marker in ["categoryConfig", "rank-card", "filter-banner"]:
            self.assertNotIn(marker, self.video_template)

    def test_watch_order_template_has_watch_order_markers(self) -> None:
        for marker in [
            "视频提炼 · 观看顺序",
            "filter-banner",
            "rank-card",
            "categoryConfig",
            "strong",
            "medium",
            "low",
            "skip",
            "failed",
        ]:
            self.assertIn(marker, self.watch_order_template)

    def test_watch_order_template_excludes_single_video_markers(self) -> None:
        for marker in ["视频到底讲了什么？", "如果要看，只看哪里？", "feedback-launcher"]:
            self.assertNotIn(marker, self.watch_order_template)


if __name__ == "__main__":
    unittest.main()
