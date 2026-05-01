from __future__ import annotations

import unittest

from helpers import assert_invalid, load_golden


class WatchSegmentsTest(unittest.TestCase):
    def test_multiple_primary_segments_fail(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_segments"][1]["priority"] = "primary"
        assert_invalid(self, payload, "primary_count")

    def test_multiple_optional_segments_fail(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_segments"].append({
            "priority": "optional",
            "start": "15:09",
            "end": "16:10",
            "title": "可选补看：结尾提醒",
            "reason": "这一段补充作者对执行顺序的提醒。"
        })
        assert_invalid(self, payload, "optional_count")

    def test_only_one_segment_must_match_primary_time_range(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["only_one_segment"] = "只选一段：01:59 | 05:12。这一段不是 primary。"
        assert_invalid(self, payload, "only_one_match")

    def test_segment_reason_time_display_must_use_pipe(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_segments"][0]["reason"] = "这一段 10:51-15:08 最集中。"
        assert_invalid(self, payload, "time_pipe")

    def test_segment_title_prefix_must_match_priority(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_segments"][0]["title"] = "重点片段：任务改造步骤"
        assert_invalid(self, payload, "segment_title_prefix")

    def test_segments_must_keep_priority_order(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_segments"] = [payload["watch_segments"][1], payload["watch_segments"][0]]
        assert_invalid(self, payload, "segment_order")


if __name__ == "__main__":
    unittest.main()
