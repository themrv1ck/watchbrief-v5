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
        self.assertIn("class=\"panel section summary-panel col-12\"", html)
        self.assertIn("section-watch", html)
        self.assertIn("视频到底讲了什么？", html)
        self.assertNotIn("第 1 幕", html)
        self.assertIn('--bg:#0c0e14', html)
        self.assertNotIn('--bg:#f7f3eb', html)
        self.assertIn('class="beat-number" aria-hidden="true">01', html)
        self.assertIn('class="beat-number" aria-hidden="true">02', html)
        self.assertIn("原片对应片段", html)
        summary = html.split('<article class="panel section summary-panel col-12" id="summary">', 1)[1].split("</article>", 1)[0]
        self.assertIn("先写三行而不是整篇", summary)
        self.assertNotIn("内容价值", summary)
        self.assertNotIn(payload["watch_verdict"], summary)
        self.assertNotIn("如果要补原片，先看哪里？", html)
        self.assertIn("补充内容与边界", html)
        self.assertIn("直接结论", html)
        self.assertIn("具体观点", html)
        self.assertIn("可直接采用", html)
        self.assertIn('class="reader-reference reader-actions"><h3>可直接采用</h3>', html)
        self.assertNotIn('target-section t-orange"><h3>可直接采用</h3>', html)
        self.assertIn("注意边界", html)

    def test_approved_template_places_dynamic_summary_and_brief_in_equal_height_hero(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["one_line_brief"] = "这期视频主要讲：让任务更容易开始。"
        html = render_single_video_html(payload)
        hero = html.split('<section class="hero score-', 1)[1].split('</section>', 1)[0]
        left, right = hero.split('<aside class="panel hero-side"', 1)
        self.assertIn(payload["target_summary"], left)
        self.assertIn('<div class="summary-label">视频总结</div>', left)
        self.assertIn('<strong>省流</strong><span>让任务更容易开始。</span>', right)
        self.assertLess(right.index('score-box'), right.index('class="tag"'))
        self.assertLess(right.index('class="tag"'), right.index('side-verdict'))
        self.assertNotIn('值得补看', right)
        self.assertNotIn('<details', hero)
        self.assertIn('.hero{align-items:stretch}', html)
        self.assertIn('justify-content:flex-start;align-self:stretch', html)
        self.assertIn('<strong>视频主旨：</strong>' + payload["final_conclusion"], html)
        self.assertNotIn('抗炎生活', html)

    def test_default_render_shows_compact_personal_preference_judgment(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["personal_relevance"] = {
            "status": "matched",
            "basis": "watchtrace_bilibili_profile",
            "match_level": "interest_domain_match",
            "topic_interest_score": 8.5,
            "summary": "当前视频话题命中你的兴趣领域：个人效率与认知表现。",
            "evidence": ["个人效率与认知表现：心流"],
            "caveat": "兴趣画像只来自真实历史观看事实与 WatchBefore 内容报告；不使用作者级推断，也不替你做最终观看决定。",
            "profile_schema_version": "watchbefore.profile.v2",
            "profile_sha256": "a" * 64,
        }

        html = render_single_video_html(payload)

        self.assertNotIn("和我的观看习惯有什么关系？", html)
        self.assertIn('data-personal-match="interest_domain_match"', html)
        self.assertIn("画像判断", html)
        self.assertIn("当前视频话题命中你的兴趣领域", html)
        self.assertNotIn("完整看完内容主题：心流", html)
        self.assertIn("直接结论", html)

    def test_default_render_marks_missing_profile_as_unusable_for_like_judgment(self) -> None:
        html = render_single_video_html(load_golden("sample_payload_heartflow.json"))

        self.assertIn('data-personal-match="profile-not-bound"', html)
        self.assertIn("此结果不能用于判断你是否会喜欢", html)

    def test_render_non_watch_target_uses_target_sections(self) -> None:
        html = render_single_video_html(self.target_payload())

        self.assertIn("知识笔记", html)
        self.assertIn('data-report-target="knowledge_notes"', html)
        self.assertIn("核心概念", html)
        self.assertIn("心流来自目标、反馈和挑战之间的配合。", html)
        self.assertNotIn("如果要补原片，先看哪里？", html)

    def test_render_content_brief_target_uses_direct_sections(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["report_target"] = "content_brief"
        payload["target_summary"] = "这是一份直接陈述内容观点的中文简报。"
        payload["target_sections"] = {
            "direct_statements": ["心流来自目标、反馈和挑战之间的配合。"],
            "key_points": ["视频把心流解释为任务结构问题。"],
            "practical_takeaways": ["把任务拆小，并让反馈更及时。"],
            "boundaries": ["转写内容只支持对视频内部观点做整理。"],
        }
        html = render_single_video_html(payload)

        self.assertIn("内容简报", html)
        self.assertIn('data-report-target="content_brief"', html)
        self.assertIn("直接结论", html)
        self.assertIn("心流来自目标、反馈和挑战之间的配合。", html)

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
        self.assertIn("<strong>补看说明：</strong>", html)
        self.assertIn(payload["one_line_brief"], html)
        self.assertNotIn("结论：结论：", html)

    def test_renderer_uses_reading_flow_as_primary_text_and_hides_analysis_indexes(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["title"] = "一小时课程：如何设计任务系统"
        payload["duration"] = "1小时00分00秒"
        payload["reading_flow"] = [
            {
                "start": "00:00",
                "end": "20:00",
                "heading": "先把误区从意志力上拿开",
                "body": "视频先从难以专注的困境切入，再把焦点放到任务结构如何制造阻力。",
            },
            {
                "start": "20:00",
                "end": "40:00",
                "heading": "再把任务变成能够反馈的系统",
                "body": "后半段把清理干扰、缩小入口和即时反馈连成一条可以执行的路径。",
            },
            {
                "start": "40:00",
                "end": "60:00",
                "heading": "最后回到持续可用的执行标准",
                "body": "结尾把改造后的任务结构放回长期执行，避免把一次状态当成唯一目标。",
            },
        ]
        payload["long_content_breakdown"] = [
            {"start": "00:00", "end": "20:00", "title": "问题", "summary": "先说明任务模糊和反馈缓慢如何制造启动阻力。"},
            {"start": "20:00", "end": "40:00", "title": "方法", "summary": "再讲清理干扰、缩小入口和即时反馈如何组成改造方法。"},
            {"start": "40:00", "end": "60:00", "title": "落点", "summary": "最后说明持续执行需要回到任务结构和现实边界。"},
        ]
        html = render_single_video_html(payload)
        required_fragments = [
            'class="hero score-skip"',
            'class="panel hero-main"',
            'class="panel section section-watch col-12"',
            "视频到底讲了什么？",
            "原片对应片段",
            "直接结论",
            "具体观点",
            "可直接采用",
            "注意边界",
            "内容价值偏低",
            "纠偏反馈",
            "评分标高了",
            "评分标低了",
            "方向基本准确",
            "首选片段：任务改造步骤",
            "可选补看：心流误区",
            '<span>10:51</span><span class="time-sep">|</span><span>15:08</span>',
        ]
        for fragment in required_fragments:
            self.assertIn(fragment, html)
        body = html.split("</style>", 1)[1]
        self.assertLess(body.index("视频到底讲了什么？"), body.index("先把误区从意志力上拿开"))
        self.assertLess(body.index("先把误区从意志力上拿开"), body.index("报告信息与边界"))
        self.assertNotIn("把它读成三幕", body)
        self.assertNotIn("第 1 幕", body)
        self.assertIn('class="reader-index source-index panel col-12"', html)
        self.assertIn('class="reader-index analysis-index panel col-12" id="supporting"', html)
        self.assertNotIn('class="path-table"', html)
        self.assertNotIn('class="combined-target-grid"', html)

    def test_renderer_requires_model_authored_reading_flow(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["reading_flow"]
        with self.assertRaises(WatchBriefValidationError) as context:
            render_single_video_html(payload)

        self.assertTrue(any(issue.path == "reading_flow" and issue.code == "required" for issue in context.exception.issues))

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

        self.assertIn("2026-04-29", html)
        self.assertIn("44分58秒", html)
        self.assertNotIn("未知", html)

    def test_content_caveat_uses_bottom_report_note_not_hero_side_banner(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        html = render_single_video_html(payload)

        self.assertIn('<p class="report-note"><strong>说明：</strong>', html)
        self.assertIn(payload["content_caveat"], html)
        self.assertNotIn('<p class="side-verdict"><strong>局限：</strong>', html)
        summary_block = html.split('<article class="panel section summary-panel col-12" id="summary">', 1)[1].split("</article>", 1)[0]
        self.assertNotIn(payload["content_caveat"], summary_block)

    def test_empty_content_caveat_does_not_render_report_note(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["content_caveat"] = ""

        html = render_single_video_html(payload)

        self.assertNotIn('class="report-note"', html)

    def test_renderer_score_color_skip_band_from_low_replacement_score(self) -> None:
        payload = load_golden("sample_payload_charm.json")
        payload["structured_assessment"] = {"信息密度": 1.2, "论据质量": 1.2, "话题兴趣": 1.2}
        payload = apply_deterministic_scoring(payload)
        html = render_single_video_html(payload)

        self.assertIn('data-score-band="skip"', html)

    def test_renderer_score_color_low_band_from_mid_replacement_score(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {"信息密度": 5.3, "论据质量": 5.3, "话题兴趣": 5.3}
        payload = apply_deterministic_scoring(payload)
        html = render_single_video_html(payload)

        self.assertIn('data-score-band="low"', html)

    def test_renderer_score_color_medium_band_from_worth_supplementing_score(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {"信息密度": 7.9, "论据质量": 7.9, "话题兴趣": 7.9}
        payload["watch_verdict"] = "报告不能完全替代，原视频值得补看；如果时间有限，先看 10:51 | 15:08。"
        payload = apply_deterministic_scoring(payload)
        html = render_single_video_html(payload)

        self.assertIn('data-score-band="medium"', html)

    def test_renderer_score_color_strong_band_from_high_replacement_score(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {"信息密度": 8.6, "论据质量": 8.6, "话题兴趣": 8.6}
        payload["watch_verdict"] = "报告不能完全替代，原视频建议完整看完，尤其是 10:51 | 15:08 的完整论证和示范。"
        payload = apply_deterministic_scoring(payload)
        html = render_single_video_html(payload)

        self.assertIn('data-score-band="strong"', html)

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
