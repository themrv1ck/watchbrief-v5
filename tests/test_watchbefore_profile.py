import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "scripts" / "preferences" / "build_profile.py"


spec = importlib.util.spec_from_file_location("build_profile", PROFILE_PATH)
profile = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(profile)


class WatchBeforeProfileTest(unittest.TestCase):
    def sample_events(self):
        return [
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "title": "Agent Systems",
                "author": "Lab A",
                "url": "https://www.bilibili.com/video/BV1done",
                "watch_ratio": 1.0,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "title": "Partial Video",
                "author": "Lab B",
                "url": "https://www.bilibili.com/video/BV1part",
                "watch_ratio": 0.45,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "title": "Skipped Video",
                "author": "Lab C",
                "url": "https://www.bilibili.com/video/BV1skip",
                "watch_ratio": 0.1,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "history",
                "title": "Bounce Video",
                "author": "Lab D",
                "url": "https://www.bilibili.com/video/BV1bounce",
                "watch_ratio": 0.0,
            },
            {
                "schema_version": "watchbefore.event.v1",
                "source": "watch_later",
                "title": "Queued Video",
                "author": "Lab E",
                "url": "https://www.bilibili.com/video/BV1queue",
                "watch_ratio": 0.0,
            },
        ]

    def test_load_events_accepts_legacy_schema_and_normalizes_it(self):
        rows = [
            {"schema_version": "watchfilter.event.v1", "url": "https://www.bilibili.com/video/BV1old"},
            {"schema_version": "unknown", "url": "https://www.bilibili.com/video/BV1bad"},
            {"schema_version": "watchbefore.event.v1"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            events = profile.load_events(path)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["schema_version"], "watchbefore.event.v1")

    def test_build_profile_records_behavior_without_claiming_content_interest(self):
        payload = json.loads(profile.build_profile(self.sample_events()))

        self.assertEqual(payload["schema_version"], "watchbefore.profile.v2")
        self.assertEqual(payload["source"], "watchtrace_bilibili")
        self.assertEqual(payload["behavior_summary"]["total_events"], 5)
        self.assertEqual(payload["behavior_summary"]["history_events"], 4)
        self.assertEqual(payload["behavior_summary"]["watch_later_events_excluded"], 1)
        self.assertEqual(payload["interest_profile"]["status"], "insufficient_content_analysis")
        self.assertEqual(payload["interest_profile"]["domains"], [])
        self.assertEqual(payload["interest_profile"]["content_preferences"], [])
        self.assertNotIn("positive_topics", text := json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("negative_topics", text)
        self.assertIn("最终看不看由用户决定", text)
        self.assertIn("不从标题、作者或稍后再看记录猜测兴趣", text)


if __name__ == "__main__":
    unittest.main()
