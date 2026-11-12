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
    payload["reading_flow"] = [
        {
            "start": "00:00",
            "end": "24:00",
            "heading": "先把心流从意志力误解中拿开",
            "body": "课程先界定任务设计如何决定启动阻力，再说明目标、反馈与挑战为什么需要一起看。",
        },
        {
            "start": "24:00",
            "end": "44:20",
            "heading": "再把大任务拆成能进入的小入口",
            "body": "中段把任务拆分、限时挑战和反馈节点连起来，说明方法如何在执行中维持推进。",
        },
        {
            "start": "44:20",
            "end": "1:02:03",
            "heading": "最后用现实边界校准这套方法",
            "body": "结尾补充干扰太强或反馈不足时的调整方式，避免把心流当成任何场景都有效的单一技巧。",
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
        payload = long_course_payload()
        payload["target_summary"] = "这期内容先界定心流的误解，再讲目标、反馈、任务拆分和适用边界。"
        html = render_single_video_html(payload)

        self.assertIn("视频到底讲了什么？", html)
        self.assertIn(payload["target_summary"], html)
        self.assertIn("想按时间核对全片，再展开完整索引", html)
        self.assertIn("00:00 - 08:30", html)
        self.assertIn("目标、反馈与挑战", html)
        body = html.split("</style>", 1)[1]
        self.assertLess(body.index("视频到底讲了什么？"), body.index("直接结论"))
        self.assertLess(body.index("直接结论"), body.index("想按时间核对全片，再展开完整索引"))


if __name__ == "__main__":
    unittest.main()
