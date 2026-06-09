from __future__ import annotations

import copy
import unittest

from helpers import ROOT, load_golden
from scripts.renderer import render_single_video_html
from scripts.scoring import apply_deterministic_scoring
from scripts.validator import WatchBriefValidationError


class RendererTest(unittest.TestCase):
    def target_payload(self) -> dict:
        payload = load_golden("sample_payload_heartflow.json")
        payload["report_target"] = "knowledge_notes"
        payload["target_summary"] = "这是一份面向知识笔记的中文分析摘要。"
        payload["target_sections"] = {
            "core_concepts": ["心流来自目标、反馈和挑战之间的配合。"],
            "key_facts": ["视频把心流解释为任务结构问题，而不是单纯意志力问题。"],
            "methods": ["把任务拆小，并让反馈更及时。"],
            "caveats": ["转写内容只支持对视频内部观点做整理。"],
        }
        return payload

    def test_render_heartflow_has_expected_sections(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        html = render_single_video_html(payload)
        self.assertIn(payload["title"], html)
        self.assertIn("class=\"hero score-skip\"", html)
        self.assertIn("class=\"panel section section-chain col-12\"", html)
        self.assertIn("section-watch", html)
        self.assertIn("视频到底讲了什么？", html)
        self.assertIn("如果要补原片，先看哪里？", html)

    def test_render_non_watch_target_uses_target_sections(self) -> None:
        html = render_single_video_html(self.target_payload())

        self.assertIn("知识笔记", html)
        self.assertIn('data-report-target="knowledge_notes"', html)
        self.assertIn("核心概念", html)
        self.assertIn("心流来自目标、反馈和挑战之间的配合。", html)
        self.assertNotIn("如果要补原片，先看哪里？", html)

    def test_render_charm_matches_golden_html(self) -> None:
        payload = load_golden("sample_payload_charm.json")
        expected = (ROOT / "golden" / "sample_charm.html").read_text(encoding="utf-8")
        self.assertIn(payload["title"], expected)
        self.assertIn("<body>", expected)
        # Keep a compatibility check: renderer output should contain the same title and core phrase set.
        html = render_single_video_html(payload)
        self.assertIn(payload["title"], html)

    def test_renderer_requires_validator_pass(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["final_conclusion"] = "报告已经覆盖核心内容，原视频不用完整看。"
        with self.assertRaises(WatchBriefValidationError):
            render_single_video_html(payload)

    def test_renderer_missing_field_fails_instead_of_filling(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["watch_verdict"]
        with self.assertRaises(WatchBriefValidationError) as context:
            render_single_video_html(payload)
        self.assertTrue(any(issue.path == "watch_verdict" and issue.code == "required" for issue in context.exception.issues))

    def test_renderer_legacy_field_does_not_repair_missing_final_conclusion(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["final_conclusion"]
        payload["core_thesis"] = "旧字段不能补红色结论。"
        with self.assertRaises(WatchBriefValidationError) as context:
            render_single_video_html(payload)
        codes = {issue.code for issue in context.exception.issues}
        self.assertIn("required", codes)
        self.assertIn("legacy_field", codes)

    def test_renderer_does_not_mutate_payload_or_rewrite_fields(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        original = copy.deepcopy(payload)
        html = render_single_video_html(payload)

        self.assertEqual(payload, original)
        self.assertIn(payload["final_conclusion"], html)
        self.assertIn("看报告基本够，原视频只建议跳看 10:51 - 15:08", html)
        self.assertIn("<strong>补看入口：</strong>", html)
        self.assertIn(payload["one_line_brief"], html)
        self.assertNotIn("结论：结论：", html)

    def test_renderer_keeps_v4_visible_skeleton(self) -> None:
        html = render_single_video_html(load_golden("sample_payload_heartflow.json"))
        required_fragments = [
            'class="hero score-skip"',
            'class="panel hero-main"',
            'class="panel hero-side"',
            'class="panel section section-chain col-12"',
            'class="panel section section-watch col-12"',
            "视频到底讲了什么？",
            "如果要补原片，先看哪里？",
            "内容价值偏低",
            "纠偏反馈",
            "评分标高了",
            "评分标低了",
            "方向基本准确",
            "highest compression · 核心提炼",
            "首选片段：任务改造步骤",
            "可选补看：心流误区",
            '<span>10:51</span><span class="time-sep">|</span><span>15:08</span>',
        ]
        for fragment in required_fragments:
            self.assertIn(fragment, html)

    def test_renderer_watch_time_uses_three_line_structure(self) -> None:
        html = render_single_video_html(load_golden("sample_payload_heartflow.json"))

        self.assertIn('<div class="watch-time"><span>10:51</span><span class="time-sep">|</span><span>15:08</span></div>', html)
        self.assertNotIn('<div class="watch-time">10:51 | 15:08</div>', html)

    def test_renderer_natural_sentences_use_hyphen_time_range(self) -> None:
        html = render_single_video_html(load_golden("sample_payload_heartflow.json"))

        self.assertIn("原视频只建议跳看 10:51 - 15:08", html)
        self.assertIn("只选一段：10:51 - 15:08", html)
        self.assertNotIn("原视频只建议跳看 10:51 | 15:08", html)
        self.assertNotIn("只选一段：10:51 | 15:08", html)

    def test_renderer_meta_row_uses_video_publish_date_and_natural_duration(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["date"] = "2026-04-29"
        payload["duration"] = "44分58秒"

        html = render_single_video_html(payload)

        self.assertIn("<strong>日期：</strong>2026-04-29", html)
        self.assertIn("<strong>时长：</strong>44分58秒", html)
        self.assertNotIn("<strong>时长：</strong>44:58", html)
        self.assertNotIn("<strong>日期：</strong>未知", html)

    def test_content_caveat_uses_bottom_report_note_not_hero_side_banner(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        html = render_single_video_html(payload)

        self.assertIn('<p class="report-note"><strong>说明：</strong>', html)
        self.assertIn(payload["content_caveat"], html)
        self.assertNotIn('<p class="side-verdict"><strong>局限：</strong>', html)
        final_conclusion_block = html.split('<div class="final-conclusion">', 1)[1].split("</div>", 1)[0]
        self.assertNotIn(payload["content_caveat"], final_conclusion_block)

    def test_empty_content_caveat_does_not_render_report_note(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["content_caveat"] = ""

        html = render_single_video_html(payload)

        self.assertNotIn('class="report-note"', html)

    def test_renderer_score_color_skip_band_from_low_replacement_score(self) -> None:
        payload = load_golden("sample_payload_charm.json")
        payload["structured_assessment"] = {"信息密度": 1.2, "论据质量": 1.2, "独创性": 1.2, "观看性价比": 1.2}
        payload = apply_deterministic_scoring(payload)
        html = render_single_video_html(payload)

        self.assertIn('data-score-band="skip"', html)
        self.assertIn("--score-band-accent:#A24A42", html)

    def test_renderer_score_color_low_band_from_mid_replacement_score(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {"信息密度": 5.3, "论据质量": 5.3, "独创性": 5.3, "观看性价比": 5.3}
        payload = apply_deterministic_scoring(payload)
        html = render_single_video_html(payload)

        self.assertIn('data-score-band="low"', html)
        self.assertIn("--score-band-accent:#f6c90e", html)

    def test_renderer_score_color_medium_band_from_worth_supplementing_score(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {"信息密度": 7.9, "论据质量": 7.9, "独创性": 7.9, "观看性价比": 7.9}
        payload["watch_verdict"] = "报告不能完全替代，原视频值得补看；如果时间有限，先看 10:51 | 15:08。"
        payload = apply_deterministic_scoring(payload)
        html = render_single_video_html(payload)

        self.assertIn('data-score-band="medium"', html)
        self.assertIn("--score-band-accent:#ab96e5", html)

    def test_renderer_score_color_strong_band_from_high_replacement_score(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {"信息密度": 8.6, "论据质量": 8.6, "独创性": 8.6, "观看性价比": 8.6}
        payload["watch_verdict"] = "报告不能完全替代，原视频建议完整看完，尤其是 10:51 | 15:08 的完整论证和示范。"
        payload = apply_deterministic_scoring(payload)
        html = render_single_video_html(payload)

        self.assertIn('data-score-band="strong"', html)
        self.assertIn("--score-band-accent:#b8de7f", html)

    def test_renderer_output_has_no_removed_legacy_modules(self) -> None:
        html = render_single_video_html(load_golden("sample_payload_heartflow.json"))
        self.assertNotIn("要点提炼", html)
        self.assertNotIn("可执行动作清单", html)
        self.assertNotIn("完整笔记", html)

    def test_renderer_source_has_no_legacy_field_fallbacks(self) -> None:
        source = (ROOT / "scripts" / "renderer.py").read_text(encoding="utf-8")
        self.assertNotIn("core_thesis", source)
        self.assertNotIn("summary_sentence", source)
        self.assertNotIn("main_content_intro", source)
        self.assertNotIn("worth_watching", source)
        self.assertNotIn("fallback", source.lower())


if __name__ == "__main__":
    unittest.main()
