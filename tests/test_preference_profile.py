from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.preference_profile import build_personal_relevance, load_preference_profile


def ready_profile(*, confidence: str = "high", subtopics: list[str] | None = None) -> dict:
    return {
        "schema_version": "watchbefore.profile.v2",
        "sha256": "b" * 64,
        "interest_profile": {
            "status": "ready",
            "basis": "watchbefore_reports_and_history_engagement",
            "summary": "你长期关注个人效率与认知表现。",
            "domains": [{
                "name": "个人效率与认知表现",
                "description": "关注心流、任务设计与持续专注。",
                "subtopics": subtopics or ["心流", "任务设计"],
                "keywords": subtopics or ["心流", "任务设计", "专注"],
                "confidence": confidence,
                "evidence_count": 12,
                "evidence_ids": ["r001", "r002"],
                "watched_seconds": 7200,
                "watched_hours": 2.0,
                "watch_count": 12,
                "watch_time_share": 0.25,
            }],
            "content_preferences": [{
                "name": "机制讲解",
                "description": "偏好解释原理并给出方法。",
                "confidence": "medium",
                "evidence_count": 5,
                "evidence_ids": ["r001", "r002"],
            }],
        },
    }


class PreferenceProfileTest(unittest.TestCase):
    def test_load_profile_parses_unified_interest_profile(self) -> None:
        profile_payload = {
            "schema_version": "watchbefore.profile.v2",
            "source": "watchbefore_interest_synthesis",
            "behavior_summary": {
                "total_events": 3,
                "history_events": 2,
                "watch_later_events_excluded": 1,
            },
            "interest_profile": ready_profile()["interest_profile"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seth.yaml"
            path.write_text(
                json.dumps(profile_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            profile = load_preference_profile(path)

        self.assertEqual(profile["schema_version"], "watchbefore.profile.v2")
        self.assertEqual(profile["behavior_summary"]["total_events"], 3)
        self.assertEqual(profile["interest_profile"]["status"], "ready")
        self.assertEqual(profile["interest_profile"]["domains"][0]["name"], "个人效率与认知表现")
        self.assertEqual(profile["interest_profile"]["content_preferences"][0]["name"], "机制讲解")

    def test_load_profile_rejects_old_behavior_bucket_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seth.yaml"
            path.write_text(
                json.dumps({"schema_version": "watchbefore.profile.v1"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "expected watchbefore.profile.v2"):
                load_preference_profile(path)

    def test_personal_relevance_uses_interest_domain_not_author(self) -> None:
        relevance = build_personal_relevance(
            {"channel": "Unknown", "topic": "效率 / 心流 / 任务设计"},
            ready_profile(),
        )

        self.assertEqual(relevance["status"], "matched")
        self.assertEqual(relevance["match_level"], "interest_domain_match")
        self.assertEqual(relevance["topic_interest_score"], 8.5)
        self.assertIn("画像判断：可能喜欢", relevance["summary"])
        self.assertIn("个人效率与认知表现", relevance["summary"])
        self.assertIn("2.00 小时", relevance["summary"])
        self.assertIn("12 条", relevance["summary"])
        self.assertIn("25.00%", relevance["summary"])
        self.assertIn("不使用作者级推断", relevance["caveat"])

    def test_latin_subtopic_matches_when_followed_by_chinese_description(self) -> None:
        relevance = build_personal_relevance(
            {"topic": "AI员工 / 本地部署"},
            ready_profile(confidence="medium", subtopics=["AI", "本地模型"]),
        )

        self.assertEqual(relevance["match_level"], "interest_domain_match")
        self.assertEqual(relevance["topic_interest_score"], 7.5)

    def test_short_latin_subtopic_does_not_match_inside_another_latin_word(self) -> None:
        relevance = build_personal_relevance(
            {"topic": "OpenAI / API"},
            ready_profile(subtopics=["AI"]),
        )

        self.assertEqual(relevance["match_level"], "no_interest_domain_match")
        self.assertIn("暂时无法判断是否喜欢", relevance["summary"])

    def test_profile_without_domains_is_insufficient_and_neutral(self) -> None:
        profile = {
            "schema_version": "watchbefore.profile.v2",
            "sha256": "d" * 64,
            "interest_profile": {
                "status": "insufficient_content_analysis",
                "domains": [],
                "content_preferences": [],
            },
        }

        relevance = build_personal_relevance({"topic": "效率 / 心流"}, profile)

        self.assertEqual(relevance["status"], "insufficient_profile")
        self.assertEqual(relevance["match_level"], "insufficient_interest_profile")
        self.assertEqual(relevance["topic_interest_score"], 5.0)
        self.assertIn("暂时无法判断是否喜欢", relevance["summary"])


if __name__ == "__main__":
    unittest.main()
