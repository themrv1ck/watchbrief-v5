import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "scripts" / "preferences" / "build_content_profile.py"
SAMPLE_PATH = ROOT / "scripts" / "preferences" / "select_history_audio_sample.py"


spec = importlib.util.spec_from_file_location("build_content_profile", PROFILE_PATH)
profile = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(profile)
sample_spec = importlib.util.spec_from_file_location("select_history_audio_sample", SAMPLE_PATH)
sample = importlib.util.module_from_spec(sample_spec)
assert sample_spec.loader is not None
sample_spec.loader.exec_module(sample)


class WatchBeforeContentProfileTest(unittest.TestCase):
    def test_build_content_profile_outputs_one_interest_profile_and_excludes_watch_later(self):
        events = [
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/video/BV1done",
                "watch_ratio": 1.0,
                "watched_seconds": 600,
                "duration_seconds": 600,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/video/BV1bounce",
                "watch_ratio": 0.5,
                "watched_seconds": 600,
                "duration_seconds": 1200,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "watch_later",
                "url": "https://www.bilibili.com/video/BV1queue",
                "watch_ratio": 0.0,
            },
        ]
        reports = [
            {"url": "https://www.bilibili.com/video/BV1done", "topic": "效率 / 心流"},
            {"url": "https://www.bilibili.com/video/BV1bounce", "topic": "效率 / 空泛鸡汤"},
            {"url": "https://www.bilibili.com/video/BV1queue", "topic": "Agent / 工作流"},
        ]

        captured = []

        def synthesize(observations):
            captured.extend(observations)
            return {
                "summary": "你长期关注个人效率与认知表现。",
                "domains": [{
                    "name": "个人效率与认知表现",
                    "description": "关注心流、任务设计与持续专注。",
                    "subtopics": ["心流", "任务设计"],
                    "keywords": ["心流", "任务设计", "专注"],
                    "confidence": "high",
                    "evidence_ids": ["r001", "r002"],
                }],
                "content_preferences": [{
                    "name": "机制讲解",
                    "description": "偏好解释原理并给出可执行方法的内容。",
                    "confidence": "medium",
                    "evidence_ids": ["r001", "r002"],
                }],
            }

        payload = json.loads(
            profile.build_content_profile(events, reports, synthesis_provider=synthesize)
        )

        self.assertEqual(payload["schema_version"], "watchbefore.profile.v2")
        self.assertEqual(payload["source"], "watchbefore_interest_synthesis")
        self.assertEqual(payload["behavior_summary"]["matched_history_reports"], 2)
        self.assertEqual(payload["behavior_summary"]["watch_later_events_excluded"], 1)
        self.assertEqual(len(captured), 2)
        self.assertNotIn("Agent", json.dumps(captured, ensure_ascii=False))
        self.assertEqual(payload["interest_profile"]["domains"][0]["name"], "个人效率与认知表现")
        self.assertEqual(payload["interest_profile"]["domains"][0]["evidence_count"], 2)
        self.assertNotIn("positive_topics", payload["interest_profile"])
        self.assertNotIn("queued_topics", payload["interest_profile"])
        self.assertNotIn("negative_topics", payload["interest_profile"])

    def test_load_reports_accepts_report_cache_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cached.json"
            path.write_text(json.dumps({
                "cache_version": "watchbrief_v5.report_cache.v2",
                "normalized_payload": {
                    "url": "https://www.bilibili.com/video/BV1done",
                    "topic": "效率 / 心流",
                },
            }, ensure_ascii=False), encoding="utf-8")

            reports = profile.load_reports([path])

        self.assertEqual(reports, [{
            "url": "https://www.bilibili.com/video/BV1done",
            "topic": "效率 / 心流",
            "_analysis_source": "normalized_payload",
            "_source_priority": 3,
        }])

    def test_load_reports_uses_local_extract_when_report_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local_extract.json"
            path.write_text(json.dumps({
                "metadata": {
                    "url": "https://www.bilibili.com/video/BV1done",
                },
                "qwen_extract": {
                    "main_axis": "讲解心流状态如何由任务结构和反馈形成。",
                    "important_terms": ["心流", "任务结构"],
                },
            }, ensure_ascii=False), encoding="utf-8")

            reports = profile.load_reports([path])

        self.assertEqual(reports, [{
            "url": "https://www.bilibili.com/video/BV1done",
            "topic": "讲解心流状态如何由任务结构和反馈形成。 / 心流 / 任务结构",
            "_analysis_source": "local_extract",
            "_source_priority": 2,
        }])

    def test_load_reports_prefers_normalized_payload_over_local_extract_for_same_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            extract_path = Path(tmp) / "local_extract.json"
            report_path = Path(tmp) / "normalized_payload.json"
            extract_path.write_text(json.dumps({
                "metadata": {"url": "https://www.bilibili.com/video/BV1done"},
                "qwen_extract": {
                    "main_axis": "讲解心流状态如何由任务结构和反馈形成。",
                    "important_terms": ["心流"],
                },
            }, ensure_ascii=False), encoding="utf-8")
            report_path.write_text(json.dumps({
                "url": "https://www.bilibili.com/video/BV1done",
                "topic": "效率 / 心流",
            }, ensure_ascii=False), encoding="utf-8")

            reports = profile.load_reports([extract_path, report_path])

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["topic"], "效率 / 心流")
        self.assertEqual(reports[0]["_analysis_source"], "normalized_payload")

    def test_history_observation_aggregates_watch_time_and_watch_count_for_same_url(self):
        events = [
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/video/BV1same",
                "watch_ratio": 0.0,
                "watched_seconds": 0,
                "duration_seconds": 600,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/video/BV1same",
                "watch_ratio": 1.0,
                "watched_seconds": 600,
                "duration_seconds": 600,
            },
        ]

        self.assertEqual(
            profile.history_observations_by_url(events)["https://www.bilibili.com/video/BV1same"],
            {
                "watch_ratio": 1.0,
                "watched_seconds": 600,
                "duration_seconds": 600,
                "watch_count": 1,
            },
        )

    def test_profile_uses_all_positive_watch_time_and_emits_engagement_metrics(self):
        events = [
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/video/BV1short",
                "watch_ratio": 0.1,
                "watched_seconds": 30,
                "duration_seconds": 300,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/video/BV1long",
                "watch_ratio": 1.0,
                "watched_seconds": 970,
                "duration_seconds": 970,
            },
        ]
        reports = [
            {"url": "https://www.bilibili.com/video/BV1short", "topic": "短内容"},
            {"url": "https://www.bilibili.com/video/BV1long", "topic": "长内容"},
        ]

        def synthesize(_observations):
            return {
                "summary": "样本",
                "domains": [{
                    "name": "内容分析",
                    "description": "测试领域",
                    "subtopics": ["短内容", "长内容"],
                    "keywords": ["短内容", "长内容", "内容"],
                    "confidence": "low",
                    "evidence_ids": ["r001", "r002"],
                }],
                "content_preferences": [],
            }

        payload = json.loads(
            profile.build_content_profile(events, reports, synthesis_provider=synthesize)
        )

        self.assertEqual(payload["behavior_summary"]["interest_evidence_reports"], 2)
        domain = payload["interest_profile"]["domains"][0]
        self.assertEqual(domain["watched_seconds"], 1000)
        self.assertEqual(domain["watch_count"], 2)
        self.assertEqual(domain["watch_time_share"], 1.0)

    def test_audio_sample_selects_smallest_watch_time_ranked_prefix(self):
        events = [
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": f"https://www.bilibili.com/video/BV{i}",
                "watched_seconds": seconds,
                "watch_ratio": 1.0,
                "duration_seconds": seconds,
            }
            for i, seconds in enumerate((500, 300, 150, 50), start=1)
        ]
        reports = [{"url": "https://www.bilibili.com/video/BV1", "topic": "已有正文"}]

        selected = sample.select_missing_history_sample(
            events,
            reports,
            target_watch_time_coverage=0.9,
        )

        self.assertEqual(
            [item["url"] for item in selected["selected"]],
            [
                "https://www.bilibili.com/video/BV2",
                "https://www.bilibili.com/video/BV3",
            ],
        )
        self.assertEqual(selected["projected_watch_time_coverage"], 0.95)

    def test_audio_sample_excludes_ambiguous_bangumi_season_url(self):
        events = [
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/bangumi/play/ss44175",
                "watched_seconds": 800,
                "watch_ratio": 0.5,
                "duration_seconds": 1600,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/video/BV1safe",
                "watched_seconds": 200,
                "watch_ratio": 1.0,
                "duration_seconds": 200,
            },
        ]

        selected = sample.select_missing_history_sample(
            events,
            [],
            target_watch_time_coverage=0.9,
        )

        self.assertEqual(
            [item["url"] for item in selected["selected"]],
            ["https://www.bilibili.com/video/BV1safe"],
        )
        self.assertEqual(selected["unsupported_missing_video_count"], 1)
        self.assertEqual(selected["unsupported_missing_watched_seconds"], 800)

    def test_audio_sample_keeps_terminal_failures_in_denominator_but_not_selection(self):
        events = [
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/video/BV1gone",
                "watched_seconds": 800,
                "watch_ratio": 1.0,
                "duration_seconds": 800,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "url": "https://www.bilibili.com/video/BV1safe",
                "watched_seconds": 200,
                "watch_ratio": 1.0,
                "duration_seconds": 200,
            },
        ]

        selected = sample.select_missing_history_sample(
            events,
            [],
            target_watch_time_coverage=0.9,
            excluded_urls={"https://www.bilibili.com/video/BV1gone"},
        )

        self.assertEqual(
            [item["url"] for item in selected["selected"]],
            ["https://www.bilibili.com/video/BV1safe"],
        )
        self.assertEqual(selected["excluded_missing_video_count"], 1)
        self.assertEqual(selected["excluded_missing_watched_seconds"], 800)
        self.assertEqual(selected["projected_watch_time_coverage"], 0.2)

    def test_interest_synthesis_rejects_unknown_evidence_ids(self):
        observations = [
            {
                "id": "r001",
                "content_topic": "心流",
                "watch_ratio": 1.0,
                "watched_seconds": 600,
                "duration_seconds": 600,
            },
            {
                "id": "r002",
                "content_topic": "任务设计",
                "watch_ratio": 0.8,
                "watched_seconds": 480,
                "duration_seconds": 600,
            },
        ]
        synthesis = {
            "summary": "个人效率",
            "domains": [{
                "name": "个人效率",
                "description": "关注心流与任务设计。",
                "subtopics": ["心流"],
                "keywords": ["心流", "任务设计", "专注"],
                "confidence": "high",
                "evidence_ids": ["r001", "r999"],
            }],
            "content_preferences": [],
        }

        with self.assertRaisesRegex(profile.InterestSynthesisError, "unknown evidence ids"):
            profile.validate_interest_synthesis(synthesis, observations)

    def test_load_reports_uses_completed_chunk_themes_when_reduce_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload_dir = Path(tmp) / "payloads" / "01-video"
            chunk_dir = payload_dir / "local_extract_chunks"
            chunk_dir.mkdir(parents=True)
            (payload_dir / "item_manifest.json").write_text(json.dumps({
                "item_manifest_version": "watchbrief_v5.pipeline_item.v1",
                "url": "https://www.bilibili.com/video/BV1chunk",
            }, ensure_ascii=False), encoding="utf-8")
            (chunk_dir / "chunk_001_adapted.json").write_text(json.dumps({
                "core_theme": "用人生故事讲解如何坚持长期梦想。",
                "important_terms": ["Randy Pausch", "童年梦想"],
            }, ensure_ascii=False), encoding="utf-8")

            reports = profile.load_reports([payload_dir / "item_manifest.json"])

        self.assertEqual(reports, [{
            "url": "https://www.bilibili.com/video/BV1chunk",
            "topic": "用人生故事讲解如何坚持长期梦想。 / Randy Pausch / 童年梦想",
            "_analysis_source": "chunked_local_extract",
            "_source_priority": 1,
        }])


if __name__ == "__main__":
    unittest.main()
