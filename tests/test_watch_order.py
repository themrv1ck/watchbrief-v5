from __future__ import annotations

import copy
import re
import unittest
from pathlib import Path

from helpers import load_golden
from scripts.watch_order import build_watch_order_payload, render_watch_order_html
from scripts.validator import validate_watch_order_payload, watch_order_density_percent
from scripts.score_bands import band_config


class WatchOrderTest(unittest.TestCase):
    def test_watch_order_density_bar_uses_structured_assessment_information_density(self) -> None:
        video = load_golden("sample_payload_heartflow.json")
        video["replacement_score"] = 9.9
        video["structured_assessment"]["信息密度"] = 4.2

        self.assertEqual(watch_order_density_percent(video), 42)

    def test_watch_order_payload_keeps_failure_records(self) -> None:
        video = load_golden("sample_payload_charm.json")
        video["page_file"] = "sample_charm.html"
        payload = {
            "job_name": "mock-watch-order",
            "generated_at": "2026-04-26 10:00 CST",
            "requested_count": 2,
            "completed_count": 1,
            "failed_count": 1,
            "failures": [
                {
                    "title": "failed video",
                    "url": "https://example.com/watch/fail",
                    "stage": "mock-stage",
                    "error": "mock failure reason"
                }
            ],
            "videos": [video]
        }

        validated = validate_watch_order_payload(copy.deepcopy(payload))
        self.assertEqual(validated["failures"][0]["error"], "mock failure reason")

    def test_render_watch_order_html_shows_xiaohongshu_board_skipped_notes(self) -> None:
        video = load_golden("sample_payload_heartflow.json")
        video["page_file"] = "01-video.html"
        payload = {
            "job_name": "mock-xhs-board",
            "playlist_title": "小红书测试专辑",
            "source_url": "https://www.xiaohongshu.com/board/mock",
            "source_kind": "list",
            "source_subkind": "xiaohongshu_board",
            "source_platform": "小红书",
            "generated_at": "2026-04-30 10:00",
            "requested_count": 2,
            "completed_count": 1,
            "failed_count": 0,
            "skipped_count": 1,
            "video_note_count": 1,
            "non_video_count": 1,
            "failures": [],
            "skipped": [
                {
                    "title": "图文笔记",
                    "url": "https://www.xiaohongshu.com/explore/image",
                    "stage": "resolver",
                    "reason_code": "non_video_note",
                    "note_media_kind": "image_text_note",
                }
            ],
            "videos": [video],
        }

        html = render_watch_order_html(payload)

        self.assertIn("总 note 数", html)
        self.assertIn("视频 note 1", html)
        self.assertIn("图文跳过 1", html)
        self.assertIn('"type": "skipped"', html)
        self.assertIn('"filterKey": "skipped"', html)
        self.assertIn("non_video_note", html)

    def test_render_watch_order_html_keeps_v4_structure(self) -> None:
        video = load_golden("sample_payload_heartflow.json")
        video["page_file"] = "sample_heartflow.html"
        video["structured_assessment"]["信息密度"] = 4.2
        payload = {
            "job_name": "mock-watch-order",
            "generated_at": "2026-04-26 10:00",
            "requested_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "failures": [],
            "videos": [video],
        }

        html = render_watch_order_html(payload)

        self.assertIn("视频平台 播放列表 · 观看顺序", html)
        self.assertNotIn("<title>视频提炼 · 观看顺序</title>", html)
        self.assertIn("总视频数", html)
        self.assertIn("推荐观看片段", html)
        self.assertIn('"pageFile": "sample_heartflow.html"', html)
        self.assertIn('"density": 42', html)

    def test_render_watch_order_html_keeps_playlist_order_not_score_order(self) -> None:
        first = load_golden("sample_payload_heartflow.json")
        first["page_file"] = "01-heartflow.html"
        first["replacement_score"] = 5.0
        second = load_golden("sample_payload_charm.json")
        second["page_file"] = "02-charm.html"
        second["replacement_score"] = 6.0
        payload = {
            "job_name": "mock-watch-order",
            "generated_at": "2026-04-26 10:00",
            "requested_count": 2,
            "completed_count": 2,
            "failed_count": 0,
            "failures": [],
            "videos": [first, second],
        }

        html = render_watch_order_html(payload)

        self.assertLess(
            html.index('"pageFile": "01-heartflow.html"'),
            html.index('"pageFile": "02-charm.html"'),
        )
        self.assertIn("播放列表顺序 · 原始顺序", html)

    def test_score_band_mapping_in_cards_from_low_score(self) -> None:
        video = load_golden("sample_payload_charm.json")
        video["page_file"] = "sample_charm.html"
        video["replacement_score"] = 1.2
        video["structured_assessment"]["信息密度"] = 8.8
        payload = {
            "job_name": "mock-watch-order",
            "generated_at": "2026-04-26 10:00",
            "requested_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "failures": [],
            "videos": [video],
        }

        html = render_watch_order_html(payload)
        cfg = band_config("skip")
        html_lower = html.lower()

        self.assertIn('"accent": "#a24a42"', html_lower)
        self.assertIn(cfg.accent.lower(), html_lower)
        self.assertIn(cfg.bg.lower(), html_lower)
        self.assertIn('"filterkey": "skip"', html_lower)
        self.assertIn("--group-accent:${config.accent}", html)

    def test_basic_replaceable_tag_with_mid_score_uses_low_band(self) -> None:
        video = load_golden("sample_payload_heartflow.json")
        video["page_file"] = "sample_heartflow.html"
        video["replacement_score"] = 5.9
        video["tag"] = "报告基本可替代"
        payload = {
            "job_name": "mock-watch-order",
            "generated_at": "2026-04-26 10:00",
            "requested_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "failures": [],
            "videos": [video],
        }

        html = render_watch_order_html(payload)
        html_lower = html.lower()

        self.assertIn('"filterkey": "low"', html_lower)
        self.assertIn('"label": "只建议跳看"', html)
        self.assertNotIn('"filterKey": "skip"', html)

    def test_worth_supplementing_score_uses_medium_band(self) -> None:
        video = load_golden("sample_payload_heartflow.json")
        video["page_file"] = "sample_heartflow.html"
        video["replacement_score"] = 7.9
        video["tag"] = "值得补看"
        payload = {
            "job_name": "mock-watch-order",
            "generated_at": "2026-04-26 10:00",
            "requested_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "failures": [],
            "videos": [video],
        }

        html = render_watch_order_html(payload)
        html_lower = html.lower()

        self.assertIn('"filterkey": "medium"', html_lower)
        self.assertIn('"label": "值得补看"', html)
        self.assertIn(band_config("medium").accent.lower(), html_lower)

    def test_filter_banner_color_mapping_for_skip_and_failed(self) -> None:
        html = render_watch_order_html(
            {
                "job_name": "mock-watch-order",
                "generated_at": "2026-04-26 10:00",
                "requested_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "failures": [],
                "videos": [],
            }
        )

        skip_cfg = band_config("skip")
        failed_cfg = band_config("failed")
        html_lower = html.lower()

        self.assertIn('"bg": "rgba(240,112,112,.12)"', html_lower)
        self.assertIn(skip_cfg.accent.lower(), html_lower)
        self.assertIn('"bg": "rgba(156,163,175,.09)"', html_lower)
        self.assertIn(failed_cfg.accent, html_lower)
        self.assertIn("--filter-bg:${config.bg}", html)

    def test_render_watch_order_score_bar_width_from_structured_information_density(self) -> None:
        video = load_golden("sample_payload_heartflow.json")
        video["page_file"] = "sample_heartflow.html"
        video["replacement_score"] = 9.9
        video["structured_assessment"]["信息密度"] = 6.3
        payload = {
            "job_name": "mock-watch-order",
            "generated_at": "2026-04-26 10:00",
            "requested_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "failures": [],
            "videos": [video],
        }

        html = render_watch_order_html(payload)
        self.assertIn('"density": 63', html)
        self.assertNotIn('"density": 99', html)

    def test_render_watch_order_failed_uses_gray(self) -> None:
        payload = {
            "job_name": "mock-watch-order",
            "generated_at": "2026-04-26 10:00",
            "requested_count": 1,
            "completed_count": 0,
            "failed_count": 1,
            "videos": [],
            "failures": [
                {
                    "title": "Failed Video",
                    "url": "https://example.com/watch/fail",
                    "stage": "resolver",
                    "error": "timeout",
                }
            ],
        }

        html = render_watch_order_html(payload)
        self.assertIn('"accent": "#9ca3af"', html.lower())
        self.assertIn("timeout", html)
        self.assertIn('"type": "failed"', html)
        self.assertIn('"filterKey": "failed"', html)
        self.assertIn("--group-accent:${config.accent}", html)

    def test_watch_order_filter_banner_is_script_rendered_without_prefix_text(self) -> None:
        html = render_watch_order_html(
            {
                "job_name": "mock-watch-order",
                "generated_at": "2026-04-26 10:00 CST",
                "requested_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "failures": [],
                "videos": [],
            }
        )

        self.assertIn('<div class="filter-banner" id="filter-banner"></div>', html)
        self.assertNotIn(">筛选<", html)
        self.assertIn("function renderFilterBanner()", html)
        self.assertIn("function renderList()", html)
        self.assertIn("activeFilter = chip.dataset.filter", html)
        self.assertIn("addEventListener('click'", html)
        self.assertIn("emptyState.hidden = false", html)

    def test_watch_order_failed_items_are_filterable(self) -> None:
        html = render_watch_order_html(
            {
                "job_name": "mock-watch-order",
                "generated_at": "2026-04-26 10:00 CST",
                "requested_count": 1,
                "completed_count": 0,
                "failed_count": 1,
                "videos": [],
                "failures": [
                    {
                        "title": "Failed Video",
                        "url": "https://example.com/watch/fail",
                        "stage": "local_extract",
                        "error": "timeout",
                    }
                ],
            }
        )

        self.assertIn('"type": "failed"', html)
        self.assertIn('"filterKey": "failed"', html)
        self.assertIn("item.filterKey === activeFilter || item.type === activeFilter", html)

    def test_watch_order_title_uses_playlist_title(self) -> None:
        html = render_watch_order_html(
            {
                "job_name": "https://www.youtube.com/watch?v=one&list=abc",
                "playlist_title": "Creator Lessons",
                "source_url": "https://www.youtube.com/watch?v=one&list=abc",
                "source_kind": "list",
                "generated_at": "2026-04-26 10:00",
                "requested_count": 5,
                "completed_count": 0,
                "failed_count": 0,
                "failures": [],
                "videos": [],
            }
        )

        self.assertIn("<title>Creator Lessons · 观看顺序</title>", html)
        self.assertIn('<div class="page-heading">Creator Lessons · 观看顺序</div>', html)
        self.assertNotIn("<title>视频提炼 · 观看顺序</title>", html)

    def test_watch_order_title_falls_back_to_youtube_playlist(self) -> None:
        html = render_watch_order_html(
            {
                "job_name": "https://www.youtube.com/watch?v=one&list=abc",
                "source_url": "https://www.youtube.com/watch?v=one&list=abc",
                "source_kind": "list",
                "generated_at": "2026-04-26 10:00",
                "requested_count": 5,
                "completed_count": 0,
                "failed_count": 0,
                "failures": [],
                "videos": [],
            }
        )

        self.assertIn("<title>YouTube 播放列表 · 观看顺序</title>", html)
        self.assertIn('<div class="page-heading">YouTube 播放列表 · 观看顺序</div>', html)

    def test_watch_order_title_can_infer_source_url_from_job_name(self) -> None:
        source_url = "https://www.youtube.com/watch?v=one&list=abc"
        html = render_watch_order_html(
            {
                "job_name": source_url,
                "generated_at": "2026-04-26 10:00",
                "requested_count": 5,
                "completed_count": 0,
                "failed_count": 0,
                "failures": [],
                "videos": [],
            }
        )
        topbar_right = re.search(r'<div class="topbar-right">(.*?)</div>', html, re.S)
        self.assertIsNotNone(topbar_right)
        visible_text = re.sub(r"<[^>]+>", "", topbar_right.group(1))

        self.assertIn("<title>YouTube 播放列表 · 观看顺序</title>", html)
        self.assertIn("YouTube · 播放列表 · 5 条视频", visible_text)
        self.assertIn("打开原播放列表", visible_text)
        self.assertNotIn(source_url, visible_text)

    def test_watch_order_topbar_metadata_hides_bare_source_url(self) -> None:
        source_url = "https://www.youtube.com/watch?v=one&list=abc"
        html = render_watch_order_html(
            {
                "job_name": source_url,
                "playlist_title": "Creator Lessons",
                "source_url": source_url,
                "source_kind": "list",
                "generated_at": "2026-04-26 10:00",
                "requested_count": 5,
                "completed_count": 0,
                "failed_count": 0,
                "failures": [],
                "videos": [],
            }
        )
        topbar_right = re.search(r'<div class="topbar-right">(.*?)</div>', html, re.S)
        self.assertIsNotNone(topbar_right)
        visible_text = re.sub(r"<[^>]+>", "", topbar_right.group(1))

        self.assertIn("2026-04-26 10:00 CST", visible_text)
        self.assertIn("YouTube · 播放列表 · 5 条视频", visible_text)
        self.assertIn("打开原播放列表", visible_text)
        self.assertNotIn(source_url, visible_text)

    def test_watch_order_footer_is_v5_branded(self) -> None:
        html = render_watch_order_html(
            {
                "job_name": "mock-watch-order",
                "generated_at": "2026-04-26 10:00 CST",
                "requested_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "failures": [],
                "videos": [],
            }
        )

        self.assertIn("WATCHBRIEF · WATCH ORDER", html)
        self.assertNotIn("WATCH ORDER · V4", html)

    def test_build_watch_order_payload_keeps_playlist_title_from_manifest(self) -> None:
        payload = build_watch_order_payload(
            {
                "source_url": "https://www.youtube.com/watch?v=one&list=abc",
                "source_kind": "list",
                "playlist_title": "Creator Lessons",
                "total_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "items": [],
            },
            output_dir=Path("/tmp/watch-order"),
        )

        self.assertEqual(payload["playlist_title"], "Creator Lessons")
        self.assertEqual(payload["source_url"], "https://www.youtube.com/watch?v=one&list=abc")
        self.assertEqual(payload["source_kind"], "list")
        self.assertTrue(payload["generated_at"].endswith("CST"))



if __name__ == "__main__":
    unittest.main()
