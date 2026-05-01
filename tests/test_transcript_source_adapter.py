from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT
from scripts.analyzer.local_extract import build_local_extract_payload
from scripts.transcript_source_adapter import (
    FINAL_REPORT_FIELDS_BLOCKED_IN_TRANSCRIPT_MATERIAL,
    TranscriptSourceError,
    load_transcript_source,
    parse_time_to_seconds,
    transcript_material_to_local_extract_input,
)


FIXTURE_DIR = ROOT / "fixtures" / "transcripts"
MOCK_METADATA = {
    "title": "如何把任务改造成更容易进入心流的状态",
    "url": "https://example.com/watch/heartflow",
    "channel": "Mock Channel",
    "duration": "16:42",
    "date": "2026-04-20",
}


def fake_qwen_extract(seed: dict) -> dict:
    return {
        "cleaned_understanding": "转写材料说明如何设计任务以进入心流。",
        "main_axis": "心流来自任务结构而不是硬撑意志力。",
        "core_claims": ["任务需要清楚目标和即时反馈。"],
        "conditions": ["目标清楚"],
        "methods": ["拆小任务"],
        "examples": ["写作任务的拆分例子。"],
        "caveats": ["转写质量会影响细节。"],
        "original_quotes": ["不要先责怪自己不专注。"],
        "refined_quotes": ["任务结构会影响专注状态。"],
        "transcript_quality_note": "转写可用。",
        "language": seed["transcript"]["language"],
        "important_terms": ["flow"],
        "corrected_terms": [],
    }


class TranscriptSourceAdapterTest(unittest.TestCase):
    def write_temp_transcript(self, segments: list[dict]) -> str:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = f"{temp_dir.name}/transcript.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"language": "zh", "transcript_quality": "ok", "segments": segments}, handle, ensure_ascii=False)
        return path

    def assert_all_segments_have_legal_timestamps(self, material: dict) -> None:
        for segment in material["segments"]:
            self.assertLess(parse_time_to_seconds(segment["start"]), parse_time_to_seconds(segment["end"]))

    def test_load_srt_subtitle_fixture(self) -> None:
        material = load_transcript_source(FIXTURE_DIR / "heartflow.srt", language="zh", transcript_quality="ok")

        self.assertEqual(material["material_version"], "watchbrief_v5.transcript_material.v1")
        self.assertEqual(material["source"]["kind"], "subtitle_srt")
        self.assertEqual(material["language"], "zh")
        self.assertEqual(material["transcript_quality"], "ok")
        self.assertEqual(material["segment_count"], 3)
        self.assertEqual(material["segments"][0]["start"], "00:00")
        self.assertEqual(material["segments"][0]["end"], "01:58")
        self.assertTrue(material["adapter_boundary"]["no_subtitle_download"])
        self.assertTrue(material["adapter_boundary"]["no_audio_download"])
        self.assertTrue(material["adapter_boundary"]["no_transcription"])

    def test_load_vtt_subtitle_fixture(self) -> None:
        material = load_transcript_source(FIXTURE_DIR / "heartflow.vtt", language="zh", transcript_quality="degraded")

        self.assertEqual(material["source"]["kind"], "subtitle_vtt")
        self.assertEqual(material["transcript_quality"], "degraded")
        self.assertEqual(material["segments"][0]["start"], "10:51")
        self.assertEqual(material["segments"][1]["end"], "13:40")

    def test_load_transcription_json_fixture(self) -> None:
        material = load_transcript_source(FIXTURE_DIR / "heartflow_transcript.json")

        self.assertEqual(material["source"]["kind"], "transcription_json")
        self.assertEqual(material["language"], "zh")
        self.assertEqual(material["transcript_quality"], "ok")
        self.assertEqual(material["segments"][1]["start"], "01:59")
        self.assertEqual(material["segments"][2]["text"], "所以不要先责怪自己不专注，要先检查任务入口是不是太大。")

    def test_load_transcription_txt_fixture(self) -> None:
        material = load_transcript_source(FIXTURE_DIR / "heartflow_transcript.txt", language="zh", transcript_quality="poor")

        self.assertEqual(material["source"]["kind"], "transcription_txt")
        self.assertEqual(material["transcript_quality"], "poor")
        self.assertEqual(material["segment_count"], 3)
        self.assertEqual(material["segments"][1]["start"], "01:59")
        self.assertEqual(material["segments"][1]["end"], "03:30")

    def test_load_transcription_json_with_numeric_seconds(self) -> None:
        material = load_transcript_source(FIXTURE_DIR / "whisper_numeric_transcript.json", language="zh", transcript_quality="degraded")

        self.assertEqual(material["source"]["kind"], "transcription_json")
        self.assertEqual(material["segments"][0]["start"], "00:00")
        self.assertEqual(material["segments"][0]["end"], "00:02")

    def test_material_can_feed_local_extract(self) -> None:
        material = load_transcript_source(FIXTURE_DIR / "heartflow_transcript.json")
        local_input = transcript_material_to_local_extract_input(MOCK_METADATA, material)
        local_extract = build_local_extract_payload(
            local_input["metadata"],
            local_input["transcript_segments"],
            transcript_language=local_input["transcript_language"],
            transcript_quality=local_input["transcript_quality"],
            transcript_source=local_input["transcript_source"],
            qwen_extractor=fake_qwen_extract,
        )

        self.assertEqual(local_extract["transcript"]["language"], "zh")
        self.assertEqual(local_extract["transcript"]["quality"], "ok")
        self.assertEqual(local_extract["transcript"]["segment_count"], 3)
        self.assertIn("00:00 | 01:58", local_extract["transcript"]["excerpt"])

    def test_invalid_timestamp_fails(self) -> None:
        with self.assertRaises(TranscriptSourceError):
            load_transcript_source(FIXTURE_DIR / "bad_timestamp.txt", language="zh", transcript_quality="ok")

    def test_missing_language_records_unknown(self) -> None:
        material = load_transcript_source(FIXTURE_DIR / "heartflow.srt", transcript_quality="ok")
        self.assertEqual(material["language"], "unknown")

    def test_invalid_transcript_quality_fails(self) -> None:
        with self.assertRaises(TranscriptSourceError):
            load_transcript_source(FIXTURE_DIR / "heartflow.srt", language="zh", transcript_quality="excellent")

    def test_transcript_material_does_not_contain_final_report_fields(self) -> None:
        material = load_transcript_source(FIXTURE_DIR / "heartflow.srt", language="zh", transcript_quality="ok")
        for field in FINAL_REPORT_FIELDS_BLOCKED_IN_TRANSCRIPT_MATERIAL:
            self.assertNotIn(field, material)

    def test_cli_output_shape_is_plain_json_material(self) -> None:
        material = load_transcript_source(FIXTURE_DIR / "heartflow.srt", language="zh", transcript_quality="ok")
        encoded = json.dumps(material, ensure_ascii=False)
        self.assertIn("watchbrief_v5.transcript_material.v1", encoded)
        self.assertNotIn("normalized_report_payload", encoded)

    def test_start_equals_end_empty_text_segment_is_dropped_with_warning(self) -> None:
        segments = [
            {"start": index * 2, "end": index * 2 + 1, "text": f"有效片段 {index}"}
            for index in range(20)
        ]
        segments.insert(10, {"start": 20, "end": 20, "text": "   "})
        material = load_transcript_source(Path(self.write_temp_transcript(segments)))

        self.assertEqual(material["transcript_quality"], "degraded")
        self.assertEqual(material["segment_count"], 20)
        self.assertIn("dropped_invalid_timestamp_segment index=10 start>=end", material["warnings"])
        self.assertEqual(material["sanitization"]["dropped_segment_count"], 1)
        self.assertEqual(material["sanitization"]["repaired_segment_count"], 0)
        self.assert_all_segments_have_legal_timestamps(material)

    def test_start_greater_than_end_non_empty_segment_is_repaired_with_warning(self) -> None:
        segments = [
            {"start": 0, "end": 1, "text": "第一段"},
            {"start": 2, "end": 3, "text": "第二段"},
            {"start": 6, "end": 5, "text": "坏段但有文本"},
            {"start": 8, "end": 9, "text": "后续段"},
        ]
        segments.extend(
            {"start": 10 + index * 2, "end": 11 + index * 2, "text": f"补充段 {index}"}
            for index in range(18)
        )

        material = load_transcript_source(Path(self.write_temp_transcript(segments)))

        self.assertEqual(material["transcript_quality"], "degraded")
        self.assertIn("repaired_invalid_timestamp_segment index=2 start>=end to=00:06 | 00:07", material["warnings"])
        self.assertIn({"start": "00:06", "end": "00:07", "text": "坏段但有文本"}, material["segments"])
        self.assertEqual(material["sanitization"]["dropped_segment_count"], 0)
        self.assertEqual(material["sanitization"]["repaired_segment_count"], 1)
        self.assert_all_segments_have_legal_timestamps(material)

    def test_non_empty_bad_segment_is_dropped_when_it_cannot_be_inferred(self) -> None:
        segments = [
            {"start": index * 2, "end": index * 2 + 1, "text": f"有效片段 {index}"}
            for index in range(20)
        ]
        segments.insert(10, {"start": 20, "end": 19, "text": "无法推断的坏段"})

        material = load_transcript_source(Path(self.write_temp_transcript(segments)))

        self.assertEqual(material["transcript_quality"], "degraded")
        self.assertEqual(material["segment_count"], 20)
        self.assertIn("dropped_invalid_timestamp_segment index=10 start>=end", material["warnings"])
        self.assertNotIn("无法推断的坏段", [segment["text"] for segment in material["segments"]])
        self.assertEqual(material["sanitization"]["dropped_segment_count"], 1)
        self.assertEqual(material["sanitization"]["repaired_segment_count"], 0)
        self.assert_all_segments_have_legal_timestamps(material)

    def test_many_repairable_bad_timestamp_segments_do_not_fail(self) -> None:
        segments = [
            {"start": 0, "end": 0, "text": "坏段一"},
            {"start": 2, "end": 1, "text": "坏段二"},
            {"start": 4, "end": 5, "text": "有效一"},
            {"start": 6, "end": 7, "text": "有效二"},
            {"start": 8, "end": 9, "text": "有效三"},
        ]

        material = load_transcript_source(Path(self.write_temp_transcript(segments)))

        self.assertEqual(material["segment_count"], 5)
        self.assertEqual(material["sanitization"]["repaired_segment_count"], 2)
        self.assertEqual(material["sanitization"]["dropped_segment_count"], 0)
        self.assert_all_segments_have_legal_timestamps(material)

    def test_all_unusable_timestamp_segments_still_fail(self) -> None:
        segments = [
            {"start": 0, "end": 0, "text": "   "},
            {"start": 2, "end": 2, "text": ""},
        ]

        with self.assertRaises(TranscriptSourceError):
            load_transcript_source(Path(self.write_temp_transcript(segments)))


if __name__ == "__main__":
    unittest.main()
