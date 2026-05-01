from __future__ import annotations

import copy
import unittest

from helpers import assert_invalid, load_golden
from scripts.validator import validate_normalized_report_payload


class ValidatorTest(unittest.TestCase):
    def test_golden_payloads_are_valid(self) -> None:
        validate_normalized_report_payload(load_golden("sample_payload_heartflow.json"))
        validate_normalized_report_payload(load_golden("sample_payload_charm.json"))

    def test_final_conclusion_valid_thematic_sentence_passes(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["final_conclusion"] = "心流不是意志力硬拧出来的，而是任务结构、反馈和挑战共同形成的状态。"
        validate_normalized_report_payload(payload)

    def test_final_conclusion_with_watch_advice_fails(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["final_conclusion"] = "报告已经覆盖核心内容，原视频只建议跳看重点片段。"
        assert_invalid(self, payload, "final_scope")

    def test_final_conclusion_incomplete_suffix_fails(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["final_conclusion"] = "心流的关键不是"
        assert_invalid(self, payload, "final_sentence")
        payload["final_conclusion"] = "心流的关键不是。"
        assert_invalid(self, payload, "final_incomplete")

    def test_tag_must_be_fixed_enum(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["tag"] = "心流进入方法"
        assert_invalid(self, payload, "tag_enum")

    def test_topic_must_not_inherit_legacy_default(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["topic"] = "心理学 / 社交 / 魅力"
        assert_invalid(self, payload, "topic_legacy_default")

    def test_one_line_brief_with_time_range_fails(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["one_line_brief"] = "这期视频主要讲：心流方法，建议看 10:51 | 15:08。"
        assert_invalid(self, payload, "one_line_scope")

    def test_watch_verdict_requires_pipe_time_or_no_watch_decision(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_verdict"] = "看报告基本够，原视频只建议跳看 10:51-15:08。"
        assert_invalid(self, payload, "time_pipe")

    def test_watch_verdict_missing_time_and_no_watch_decision_fails(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_verdict"] = "看报告基本够，原视频只建议跳看首选片段。"
        assert_invalid(self, payload, "watch_verdict_time")

    def test_watch_verdict_pipe_time_passes(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_verdict"] = "看报告基本够，原视频只建议跳看 20:44 | 32:34。"
        validate_normalized_report_payload(payload)

    def test_watch_verdict_no_watch_decision_passes(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_verdict"] = "看报告足够，原视频不必看。"
        validate_normalized_report_payload(payload)

    def test_highest_compression_must_not_be_over_abstract(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["highest_compression"] = "认知升级"
        assert_invalid(self, payload, "highest_abstract")

    def test_legacy_fields_cannot_enter_normalized_payload(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["core_thesis"] = "旧字段不能控制 V5 页面"
        assert_invalid(self, payload, "legacy_field")

    def test_legacy_module_labels_cannot_reappear(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["content_caveat"] = "这里不应该出现完整笔记模块。"
        assert_invalid(self, payload, "legacy_module_label")

    def test_replacement_score_is_not_changed_by_topic(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        changed = copy.deepcopy(payload)
        changed["topic"] = "商业分析 / 竞争策略 / 资源配置"

        original = validate_normalized_report_payload(payload)
        updated = validate_normalized_report_payload(changed)

        self.assertEqual(original["replacement_score"], updated["replacement_score"])
        self.assertEqual(original["score_basis"], updated["score_basis"])


if __name__ == "__main__":
    unittest.main()
