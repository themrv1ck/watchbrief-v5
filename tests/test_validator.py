from __future__ import annotations

import copy
import unittest

from helpers import assert_invalid, load_golden
from scripts.scoring import apply_deterministic_scoring
from scripts.validator import validate_normalized_report_payload


class ValidatorTest(unittest.TestCase):
    def target_payload(self, target: str = "knowledge_notes") -> dict:
        payload = load_golden("sample_payload_heartflow.json")
        payload["report_target"] = target
        payload["target_summary"] = "这是一份面向目标模式的中文分析摘要。"
        payload["target_sections"] = {
            "core_concepts": ["心流来自目标、反馈和挑战之间的配合。"],
            "key_facts": ["视频把心流解释为任务结构问题，而不是单纯意志力问题。"],
            "methods": ["把任务拆小，并让反馈更及时。"],
            "caveats": ["转写内容只支持对视频内部观点做整理。"],
        }
        return payload

    def test_golden_payloads_are_valid(self) -> None:
        validate_normalized_report_payload(load_golden("sample_payload_heartflow.json"))
        validate_normalized_report_payload(load_golden("sample_payload_charm.json"))

    def test_non_watch_report_target_payload_passes(self) -> None:
        validated = validate_normalized_report_payload(self.target_payload())

        self.assertEqual(validated["report_target"], "knowledge_notes")
        self.assertIn("core_concepts", validated["target_sections"])

    def test_personal_relevance_payload_passes(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["personal_relevance"] = {
            "status": "matched",
            "basis": "watchtrace_bilibili_profile",
            "match_level": "interest_domain_match",
            "topic_interest_score": 7.5,
            "summary": "当前视频话题命中你的兴趣领域：个人效率与认知表现。",
            "evidence": ["个人效率与认知表现：心流"],
            "caveat": "兴趣画像只来自真实历史观看事实与 WatchBefore 内容报告；不使用作者级推断，也不替你做最终观看决定。",
            "profile_schema_version": "watchbefore.profile.v2",
            "profile_sha256": "a" * 64,
        }

        validated = validate_normalized_report_payload(payload)

        self.assertEqual(validated["personal_relevance"]["match_level"], "interest_domain_match")

    def test_personal_relevance_requires_profile_hash(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["personal_relevance"] = {
            "status": "matched",
            "basis": "watchtrace_bilibili_profile",
            "match_level": "interest_domain_match",
            "topic_interest_score": 7.5,
            "summary": "这个视频命中行为样本。",
            "evidence": ["样本"],
            "caveat": "这是观看行为信号。",
            "profile_schema_version": "watchbefore.profile.v2",
            "profile_sha256": "not-a-hash",
        }

        assert_invalid(self, payload, "personal_relevance_profile_hash")

    def test_content_brief_report_target_payload_passes(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["report_target"] = "content_brief"
        payload["target_summary"] = "这是一份直接陈述内容观点的中文简报。"
        payload["target_sections"] = {
            "direct_statements": ["心流来自目标、反馈和挑战之间的配合。"],
            "key_points": ["视频把心流解释为任务结构问题，而不是单纯意志力问题。"],
            "practical_takeaways": ["把任务拆小，并让反馈更及时。"],
            "boundaries": ["转写内容只支持对视频内部观点做整理。"],
        }

        validated = validate_normalized_report_payload(payload)

        self.assertEqual(validated["report_target"], "content_brief")
        self.assertIn("direct_statements", validated["target_sections"])

    def test_watch_decision_accepts_default_template_content_sections(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["report_target"] = "watch_decision"

        validated = validate_normalized_report_payload(payload)

        self.assertEqual(validated["target_sections"]["direct_statements"][0], payload["target_sections"]["direct_statements"][0])

    def test_watch_decision_rejects_wrong_default_template_section_keys(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["report_target"] = "watch_decision"
        payload["target_sections"] = {"core_concepts": ["多余字段"]}

        assert_invalid(self, payload, "target_sections_keys")

    def test_default_report_requires_content_range_and_content_sections(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["target_summary"]
        del payload["target_sections"]

        assert_invalid(self, payload, "required")

    def test_reading_flow_is_required_and_must_progress_in_time_order(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["reading_flow"] = [
            {
                "start": "08:20",
                "end": "16:42",
                "heading": "后半段",
                "body": "这一段把任务结构和执行动作放在一起说明。",
            },
            {
                "start": "00:00",
                "end": "08:20",
                "heading": "开场",
                "body": "这一段从专注困难切入，说明为什么不该只靠意志力。",
            },
        ]

        assert_invalid(self, payload, "reading_flow_order")

        del payload["reading_flow"]
        assert_invalid(self, payload, "required")

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

    def test_final_conclusion_ending_with_important_passes(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["final_conclusion"] = "长期安宁、诚实相待、彼此独立又共同变好的人，比任何短期吸引条件都更重要。"
        validate_normalized_report_payload(payload)

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

    def test_one_line_allows_domain_scoring_terms(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["one_line_brief"] = "这期视频主要讲：睡眠软件评分体系如何影响用户对休息质量的判断。"
        validate_normalized_report_payload(payload)

    def test_one_line_still_rejects_watchbrief_score_advice(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["one_line_brief"] = "这期视频主要讲：这支视频评分 8 分，建议补看。"
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

    def test_watch_segment_end_must_be_after_start(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_segments"][0]["start"] = "12:00"
        payload["watch_segments"][0]["end"] = "11:00"
        payload["only_one_segment"] = "只选一段：12:00 | 11:00。这一段已经覆盖全片核心。"
        payload["watch_verdict"] = "看报告基本够，原视频只建议跳看 12:00 | 11:00。"

        assert_invalid(self, payload, "segment_time_order")

    def test_watch_segment_must_not_exceed_video_duration(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["duration"] = "10:00"
        payload["watch_segments"][0]["start"] = "09:00"
        payload["watch_segments"][0]["end"] = "11:00"
        payload["only_one_segment"] = "只选一段：09:00 | 11:00。这一段已经覆盖全片核心。"
        payload["watch_verdict"] = "看报告基本够，原视频只建议跳看 09:00 | 11:00。"

        assert_invalid(self, payload, "segment_out_of_duration")

    def test_watch_verdict_no_watch_decision_passes(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_verdict"] = "看报告足够，原视频不必看。"
        validate_normalized_report_payload(payload)

    def test_high_score_verdict_can_say_full_video_not_needed_when_precise_segments_are_given(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {
            "信息密度": 8.5,
            "论据质量": 8.0,
            "话题兴趣": 8.2,
        }
        payload = apply_deterministic_scoring(payload)
        payload["watch_verdict"] = "看报告基本够；如果想直接抄关键动作，先看 01:52 | 05:35。只想知道核心原则的话，原视频不必完整看。"
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
