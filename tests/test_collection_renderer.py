from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from helpers import load_golden
from scripts.collection_renderer import segment_url, validate_collection_summary, is_same_bvid_multipart
from scripts.watch_order import build_watch_order_payload, render_watch_order_html, successful_card_item, failure_card_item


def sample() -> dict:
    videos = []
    for index, score in [(1, 5.0), (2, 8.0), (3, 6.0)]:
        video = load_golden("sample_payload_charm.json")
        video.update(title=f"分集{index}", url=f"https://www.bilibili.com/video/BVexample?p={index}",
                     page_file=f"{index:02d}-分集.html", replacement_score=score,
                     target_summary=f"原始内容总结{index}")
        videos.append(video)
    return dict(job_name="test", playlist_title="测试课程", source_url="https://www.bilibili.com/video/BVexample",
                source_kind="list", source_subkind="bilibili_multipart", generated_at="2026-11-03",
                requested_count=3, completed_count=3, failed_count=0, failures=[], videos=videos,
                watch_order_items=[successful_card_item(v) for v in videos])


def summary() -> dict:
    return dict(overview="整套内容总结", brief="一句话省流。", conclusion="合集主旨内容",
                groups=[dict(start=1, end=2, title="第一阶段", summary="跨集的承接说明"),
                        dict(start=3, end=3, title="第二阶段", summary="后续主题的说明")])


class CollectionRendererTest(unittest.TestCase):
    def test_legacy_manifest_identified_by_episode_urls_not_titles(self):
        entries = [dict(url=f"https://www.bilibili.com/video/BVexample?p={i}") for i in (1, 2, 3)]
        source = "https://www.bilibili.com/video/BVexample/"
        self.assertTrue(is_same_bvid_multipart(source, entries))
        for alternate in ("https://www.bilibili.com/list/ml123", source + "?p=2"):
            self.assertFalse(is_same_bvid_multipart(alternate, entries))
        for changes in ([entries[0], entries[0]], [entries[0], dict(url="https://www.bilibili.com/video/BVdifferent?p=2")]):
            self.assertFalse(is_same_bvid_multipart(source, changes))

    def test_legacy_manifest_builds_collection_view_without_type_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            videos = sample()["videos"]
            items = []
            for index, video in enumerate(videos, 1):
                path = root / f"{index}.json"
                path.write_text(json.dumps(video))
                items.append(dict(status="completed", payload_path=str(path), html_path=f"{index}.html", url=video["url"]))
            manifest = dict(source_url="https://www.bilibili.com/video/BVexample/", total_count=3,
                            completed_count=3, items=items)
            payload = build_watch_order_payload(manifest, root)
            self.assertEqual(payload["source_subkind"], "bilibili_multipart")
            self.assertIn('id="chapter-3"', render_watch_order_html(payload))

    def test_multipart_has_continuous_reading_and_keeps_original_order(self):
        data = sample()
        data["collection_summary"] = summary()
        html = render_watch_order_html(data)
        self.assertIn("整套内容总结", html)
        self.assertIn("跨集的承接说明", html)
        self.assertIn("合集主旨内容", html)
        self.assertLess(html.index('id="chapter-1"'), html.index('id="chapter-2"'))
        self.assertLess(html.index('id="chapter-2"'), html.index('id="chapter-3"'))
        self.assertEqual(html.count('class="chapter-summary"'), 3)
        self.assertIn('01-%E5%88%86%E9%9B%86.html', html)
        self.assertNotIn('id="rank-list"', html)
        self.assertNotIn('activeSort', html)
        self.assertNotIn('class="hero-side"', html)
        for score in ("5.0", "8.0", "6.0"):
            self.assertIn(f'class="score">{score}', html)

    def test_missing_synthesis_is_explicit_and_does_not_invent_text(self):
        html = render_watch_order_html(sample())
        self.assertIn("尚未提供跨集主线总结", html)
        self.assertNotIn('class="conclusion" id=', html)
        self.assertIn("原始内容总结1", html)

    def test_missing_episode_stays_between_neighbours(self):
        data = sample()
        failure = dict(title="缺失的第2集", stage="transcript_quality", error="字幕缺失")
        data["videos"].pop(1)
        data["watch_order_items"][1] = failure_card_item(failure)
        data.update(completed_count=2, failed_count=1, failures=[failure])
        html = render_watch_order_html(data)
        self.assertIn("部分集数尚未解析", html)
        self.assertLess(html.index('id="chapter-1"'), html.index('id="chapter-2"'))
        self.assertLess(html.index('id="chapter-2"'), html.index('id="chapter-3"'))
        self.assertIn("缺失的第2集", html)

    def test_failure_order_must_not_be_guessed(self):
        data = sample()
        del data["watch_order_items"]
        data["failures"] = [dict(title="missing")]
        with self.assertRaisesRegex(ValueError, "ordered"):
            render_watch_order_html(data)

    def test_incomplete_order_does_not_pretend_complete(self):
        data = sample()
        data["watch_order_items"].pop()
        with self.assertRaisesRegex(ValueError, "count"):
            render_watch_order_html(data)

    def test_groups_cannot_skip_overlap_or_reorder_episodes(self):
        for start, end in [(1, 3), (4, 3), (2, 4), (True, 2)]:
            candidate = summary()
            candidate["groups"][1].update(start=start, end=end)
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                validate_collection_summary(candidate, 3)

    def test_summary_must_cover_last_episode(self):
        candidate = summary()
        candidate["groups"].pop()
        with self.assertRaisesRegex(ValueError, "every"):
            validate_collection_summary(candidate, 3)

    def test_missing_content_is_not_substituted_with_title(self):
        data = sample()
        del data["videos"][0]["target_summary"]
        with self.assertRaisesRegex(ValueError, "target_summary"):
            render_watch_order_html(data)

    def test_single_and_unrelated_playlist_still_use_existing_view(self):
        data = sample()
        data["source_subkind"] = ""
        self.assertIn('id="rank-list"', render_watch_order_html(data))
        data = sample()
        data.update(requested_count=1, completed_count=1, videos=data["videos"][:1], watch_order_items=data["watch_order_items"][:1])
        self.assertIn('id="rank-list"', render_watch_order_html(data))

    def test_source_content_is_escaped_and_unsafe_links_are_omitted(self):
        data = sample()
        data["videos"][0].update(target_summary='<script>alert("bad")</script>', url="javascript:alert(1)")
        data["watch_order_items"][0] = successful_card_item(data["videos"][0])
        html = render_watch_order_html(data)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn('href="javascript:', html)

    def test_time_link_preserves_episode_and_uses_episode_local_seconds(self):
        self.assertEqual(segment_url("https://www.bilibili.com/video/BVexample?p=12&t=8", "01:30"),
                         "https://www.bilibili.com/video/BVexample?p=12&t=90")

    def test_manifest_summary_is_passed_to_renderer_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = sample()["videos"][0]
            path = root / "payload.json"
            path.write_text(json.dumps(payload))
            manifest = dict(source_url="https://example.com", total_count=1, completed_count=1,
                            source_subkind="bilibili_multipart", collection_summary=summary(),
                            items=[dict(status="completed", payload_path=str(path), html_path="one.html")])
            original = copy.deepcopy(manifest)
            built = build_watch_order_payload(manifest, root)
            self.assertEqual(built["collection_summary"], summary())
            self.assertEqual(manifest, original)


if __name__ == "__main__":
    unittest.main()
