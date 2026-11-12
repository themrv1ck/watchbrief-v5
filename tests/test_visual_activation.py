from __future__ import annotations

import unittest

import helpers  # noqa: F401 - project import path
from scripts.visual_activation import decide_visual_analysis


class VisualActivationTest(unittest.TestCase):
    def test_auto_enables_for_multiple_screen_dependent_segments(self) -> None:
        segments = [
            {"text": "看屏幕上的界面，点击右侧按钮后出现设置菜单。"},
            {"text": "在图表里可以看到前后对比，拖动滑块查看效果。"},
        ]
        decision = decide_visual_analysis("auto", segments, duration_seconds=600)
        self.assertTrue(decision["enabled"])
        self.assertEqual(decision["reason_code"], "transcript_visual_dependency")
        self.assertGreaterEqual(len(decision["cue_groups"]), 2)

    def test_auto_ignores_one_visual_mention_and_plain_speech(self) -> None:
        segments = [
            {"text": "这里有一张图表。"},
            {"text": "我们主要讨论受访者的工作经历和观点。"},
        ]
        self.assertFalse(decide_visual_analysis("auto", segments, duration_seconds=600)["enabled"])

    def test_auto_catches_color_grading_demonstration(self) -> None:
        segments = [
            {"text": "我们把饱和度调一下，拖到左边看看效果。"},
            {"text": "现在画面中是一层黄色，再做一次仿色并观察镜头。"},
        ]
        decision = decide_visual_analysis("auto", segments, duration_seconds=760)
        self.assertTrue(decision["enabled"])
        self.assertIn("visual_form", decision["cue_groups"])

    def test_auto_respects_frame_budget(self) -> None:
        segments = [{"text": "屏幕上的界面需要点击按钮。"}, {"text": "看图表的前后对比。"}]
        decision = decide_visual_analysis("auto", segments, duration_seconds=1801)
        self.assertFalse(decision["enabled"])
        self.assertEqual(decision["reason_code"], "auto_frame_budget_exceeded")
        tighter_gap = decide_visual_analysis("auto", segments, duration_seconds=1200, max_gap_seconds=5.0)
        self.assertFalse(tighter_gap["enabled"])
        self.assertGreater(tighter_gap["estimated_frame_count"], tighter_gap["max_auto_frame_count"])

    def test_auto_requires_known_duration(self) -> None:
        decision = decide_visual_analysis("auto", [{"text": "屏幕上点击按钮"}], duration_seconds=None)
        self.assertFalse(decision["enabled"])
        self.assertEqual(decision["reason_code"], "duration_unavailable")

    def test_explicit_modes_override_heuristic(self) -> None:
        self.assertTrue(decide_visual_analysis("on", [], duration_seconds=3600)["enabled"])
        self.assertFalse(decide_visual_analysis("off", [{"text": "屏幕上点击按钮"}], duration_seconds=600)["enabled"])


if __name__ == "__main__":
    unittest.main()
