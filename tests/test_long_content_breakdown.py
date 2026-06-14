from __future__ import annotations

import unittest

from helpers import assert_invalid, load_golden
from scripts.renderer import render_single_video_html
from scripts.validator import validate_normalized_report_payload


def long_course_payload() -> dict:
    payload = load_golden("sample_payload_heartflow.json")
    payload["title"] = "一小时课程：如何把任务设计成更容易进入心流"
    payload["duration"] = "1小时02分03秒"
    payload["topic"] = "课程 / 效率 / 心流 / 任务设计"
    payload["long_content_breakdown"] = [
        {
            "start": "00:00",
            "end": "08:30",
            "title": "开场与问题界定",
            "summary": "先解释为什么很多人把心流误解成意志力问题，并交代课程要解决的任务设计入口。",
        },
        {
            "start": "08:30",
            "end": "24:00",
            "title": "目标、反馈与挑战",
            "summary": "集中讲清楚目标、即时反馈和适度挑战如何共同降低进入心流的阻力。",
        },
        {
            "start": "24:00",
            "end": "44:20",
            "title": "任务拆分方法",
            "summary": "展开如何把大任务拆成小入口，并用限时挑战和反馈节点维持推进。",
        },
        {
            "start": "44:20",
            "end": "1:02:03",
            "title": "适用边界与收束",
            "summary": "补充方法不适用的场景，以及缺少反馈、干扰太强时该如何重新调整任务结构。",
        },
    ]
    return payload


class LongContentBreakdownTest(unittest.TestCase):
    def test_long_course_payload_accepts_phase_breakdown(self) -> None:
        validated = validate_normalized_report_payload(long_course_payload())

        self.assertEqual(len(validated["long_content_breakdown"]), 4)
        self.assertEqual(validated["long_content_breakdown"][0]["start"], "00:00")

    def test_long_course_payload_requires_phase_breakdown(self) -> None:
        payload = long_course_payload()
        del payload["long_content_breakdown"]

        assert_invalid(self, payload, "long_breakdown_required")

    def test_short_video_rejects_phase_breakdown(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["long_content_breakdown"] = long_course_payload()["long_content_breakdown"]

        assert_invalid(self, payload, "long_breakdown_scope")

    def test_phase_breakdown_must_be_ordered_and_non_overlapping(self) -> None:
        payload = long_course_payload()
        payload["long_content_breakdown"][1]["start"] = "07:00"

        assert_invalid(self, payload, "long_breakdown_overlap")

    def test_renderer_outputs_phase_breakdown_section(self) -> None:
        html = render_single_video_html(long_course_payload())

        self.assertIn("长内容阶段拆分", html)
        self.assertIn("00:00 - 08:30", html)
        self.assertIn("目标、反馈与挑战", html)


if __name__ == "__main__":
    unittest.main()
