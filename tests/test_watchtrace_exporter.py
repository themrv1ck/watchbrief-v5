import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "tools" / "watchtrace" / "export_bilibili_events.py"


spec = importlib.util.spec_from_file_location("export_bilibili_events", EXPORTER_PATH)
exporter = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(exporter)


class WatchTraceExporterTest(unittest.TestCase):
    def test_normalize_history_item(self):
        event = exporter.normalize_item(
            {
                "title": "真实 Agent 落地演示",
                "author_name": "Seth Lab",
                "duration": 1800,
                "progress": 900,
                "history": {"bvid": "BV1abc123456"},
            },
            source="history",
            observed_at="2026-06-14T20:30:00+0800",
        )

        self.assertEqual(event["schema_version"], "watchbefore.event.v1")
        self.assertEqual(event["url"], "https://www.bilibili.com/video/BV1abc123456")
        self.assertEqual(event["bvid"], "BV1abc123456")
        self.assertEqual(event["duration_seconds"], 1800)
        self.assertEqual(event["watched_seconds"], 900)
        self.assertEqual(event["watch_ratio"], 0.5)
        self.assertEqual(event["source"], "history")

    def test_watch_later_progress_is_not_treated_as_watched(self):
        event = exporter.normalize_item(
            {
                "title": "稍后再看 AI 视频",
                "owner": {"name": "UP 主"},
                "duration": 1200,
                "progress": 800,
                "bvid": "BV1later1234",
            },
            source="watch_later",
            observed_at="2026-06-14T20:30:00+0800",
        )

        self.assertEqual(event["watched_seconds"], 0)
        self.assertEqual(event["watch_ratio"], 0.0)
        self.assertEqual(exporter.behavior_bucket(event), "queued")

    def test_write_outputs_for_watchbrief_import(self):
        events = [
            {
                "schema_version": "watchbefore.event.v1",
                "platform": "bilibili",
                "url": "https://www.bilibili.com/video/BV1one",
                "bvid": "BV1one",
                "title": "one",
                "author": "a",
                "duration_seconds": 100,
                "watched_seconds": 100,
                "watch_ratio": 1.0,
                "source": "history",
                "observed_at": "2026-06-14T20:30:00+0800",
            },
            {
                "schema_version": "watchbefore.event.v1",
                "platform": "bilibili",
                "url": "https://www.bilibili.com/video/BV1two",
                "bvid": "BV1two",
                "title": "two",
                "author": "b",
                "duration_seconds": 100,
                "watched_seconds": 0,
                "watch_ratio": 0.0,
                "source": "watch_later",
                "observed_at": "2026-06-14T20:30:00+0800",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            events_path = base / "events.jsonl"
            urls_path = base / "urls.txt"
            summary_path = base / "summary.json"
            bucket_dir = base / "buckets"

            exporter.write_jsonl(events_path, events)
            exporter.write_urls(urls_path, events)
            bucket_paths = exporter.write_bucket_urls(bucket_dir, events)
            summary = exporter.write_summary(summary_path, events=events, added_count=2, browser="arc", errors=[])

            self.assertEqual(len(events_path.read_text(encoding="utf-8").splitlines()), 2)
            self.assertEqual(urls_path.read_text(encoding="utf-8").splitlines(), [
                "https://www.bilibili.com/video/BV1one",
                "https://www.bilibili.com/video/BV1two",
            ])
            self.assertEqual(Path(bucket_paths["completed"]).read_text(encoding="utf-8").splitlines(), [
                "https://www.bilibili.com/video/BV1one",
            ])
            self.assertEqual(Path(bucket_paths["queued"]).read_text(encoding="utf-8").splitlines(), [
                "https://www.bilibili.com/video/BV1two",
            ])
            self.assertEqual(summary["behavior_counts"], {"completed": 1, "queued": 1})
            self.assertEqual(json.loads(summary_path.read_text(encoding="utf-8"))["selected_browser"], "arc")


if __name__ == "__main__":
    unittest.main()
