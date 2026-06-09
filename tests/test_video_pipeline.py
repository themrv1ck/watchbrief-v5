from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from helpers import ROOT, load_golden
from scripts.acquisition_errors import (
    LOGIN_REQUIRED_FOR_SUBTITLE,
    NO_SUBTITLE_AVAILABLE,
    AudioDownloadError,
    BilibiliAudioDownloadError,
    BilibiliResolverError,
    BilibiliSubtitleError,
    PlatformRestrictionError,
    ResolverError,
    SubtitleUnavailableError,
)
from scripts.analyzer.local_extract import LocalExtractError, LocalQwenError, build_local_extract_payload
from scripts.analyzer.local_review import LOCAL_REVIEW_MODEL_ID, LOCAL_REVIEW_PROMPT_FINGERPRINT
from scripts.analyzer.codex_review import build_review_request
from scripts.report_cache import build_report_cache_identity, report_cache_key
from scripts.transcript_source_adapter import load_transcript_source
from scripts.video_pipeline import (
    PipelineDependencies,
    ReviewResponseUnavailableError,
    metadata_from_item,
    metadata_from_item_with_debug,
    process_source,
)


FIXTURE_DIR = ROOT / "fixtures" / "transcripts"


def fixture_material() -> dict:
    return load_transcript_source(FIXTURE_DIR / "heartflow.srt", language="zh", transcript_quality="ok")


def coverage_material(last_end: str = "45:00", *, source_kind: str = "subtitle_srt", transcript_quality: str = "ok") -> dict:
    return transcript_material_from_segments(
        [
            {"start": "00:00", "end": "10:00", "text": "第一段有效内容" * 10},
            {"start": "10:00", "end": last_end, "text": "第二段有效内容" * 10},
        ],
        source_kind=source_kind,
        transcript_quality=transcript_quality,
    )


def transcript_material_from_segments(
    segments: list[dict[str, str]],
    *,
    source_kind: str = "asr_wav",
    transcript_quality: str = "degraded",
) -> dict:
    return {
        "material_version": "watchbrief_v5.transcript_material.v1",
        "source": {"kind": source_kind, "provider": "test"},
        "language": "zh",
        "transcript_quality": transcript_quality,
        "segment_count": len(segments),
        "char_count": sum(len(segment.get("text", "")) for segment in segments),
        "has_timestamps": True,
        "segments": segments,
    }


def single_resolution() -> dict:
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "single",
        "url": "https://example.com/watch/heartflow",
        "videos": [
            {
                "title": "如何把任务改造成更容易进入心流的状态",
                "url": "https://example.com/watch/heartflow",
                "channel": "Mock Channel",
                "duration": "16:42",
                "date": "2026-04-20",
            }
        ],
    }


def bilibili_resolution() -> dict:
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "single",
        "url": "https://www.bilibili.com/video/BV1xx411c7mD/",
        "videos": [
            {
                "title": "B 站测试视频",
                "url": "https://www.bilibili.com/video/BV1xx411c7mD/",
                "channel": "Mock UP",
                "duration": "08:05",
                "date": "2026-04-20",
                "bvid": "BV1xx411c7mD",
                "cid": "12345",
                "resolver_debug": {
                    "resolver_method": "bilibili_metadata_fallback",
                    "cookies_source": "browser:chrome",
                    "fallback_method": "bilibili_view_api",
                    "fallback_success": True,
                },
            }
        ],
    }


def list_resolution() -> dict:
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "list",
        "url": "https://example.com/list/mock",
        "videos": [
            {
                "title": "如何把任务改造成更容易进入心流的状态",
                "url": "https://example.com/watch/heartflow",
                "channel": "Mock Channel",
                "duration": "16:42",
                "date": "2026-04-20",
            },
            {
                "title": "魅力不是完美表演，而是让别人读到真实信号",
                "url": "https://example.com/watch/charm",
                "channel": "Mock Channel",
                "duration": "16:42",
                "date": "2026-04-21",
            },
        ],
    }


def xiaohongshu_board_resolution() -> dict:
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "list",
        "source_subkind": "xiaohongshu_board",
        "source_platform": "小红书",
        "url": "https://www.xiaohongshu.com/board/mock",
        "title": "小红书测试专辑",
        "playlist_title": "小红书测试专辑",
        "note_count": 2,
        "video_note_count": 1,
        "non_video_count": 1,
        "videos": [
            {
                "title": "小红书视频笔记",
                "url": "https://www.xiaohongshu.com/explore/video-note-1",
                "channel": "作者A",
                "duration": "16:42",
                "date": "未知",
                "note_id": "video-note-1",
                "note_media_kind": "video",
                "is_video_note": True,
            },
            {
                "title": "小红书图文笔记",
                "url": "https://www.xiaohongshu.com/explore/image-note-1",
                "channel": "作者B",
                "duration": "",
                "date": "未知",
                "note_id": "image-note-1",
                "note_type": "normal",
                "note_media_kind": "image_text_note",
                "is_video_note": False,
                "is_image_text_note": True,
                "skip_reason_code": "non_video_note",
                "skip_stage": "resolver",
            },
        ],
        "resolver_debug": {
            "resolver_method": "xiaohongshu_board",
            "note_count": 2,
            "video_note_count": 1,
            "non_video_count": 1,
        },
    }


def review_provider_for_golden(item: dict, review_request: dict, local_extract: dict) -> dict:
    if "charm" in item["url"]:
        return load_golden("sample_payload_charm.json")
    return load_golden("sample_payload_heartflow.json")


def target_review_provider(item: dict, review_request: dict, local_extract: dict) -> dict:
    payload = review_provider_for_golden(item, review_request, local_extract)
    payload["report_target"] = str(review_request.get("report_target") or "knowledge_notes")
    payload["target_summary"] = "这是一份面向知识笔记的中文分析摘要。"
    payload["target_sections"] = {
        "core_concepts": ["心流来自目标、反馈和挑战之间的配合。"],
        "key_facts": ["视频把心流解释为任务结构问题，而不是单纯意志力问题。"],
        "methods": ["把任务拆小，并让反馈更及时。"],
        "caveats": ["转写内容只支持对视频内部观点做整理。"],
    }
    return payload


def fake_qwen_extract(seed: dict) -> dict:
    return {
        "cleaned_understanding": "转写内容主要说明如何通过任务设计进入心流。",
        "main_axis": "心流来自任务结构。",
        "core_claims": ["清楚目标和即时反馈帮助进入心流。"],
        "conditions": ["目标清楚"],
        "methods": ["拆小任务"],
        "examples": ["写作任务拆分。"],
        "caveats": ["素材为测试转写。"],
        "original_quotes": [],
        "refined_quotes": [],
        "transcript_quality_note": "测试转写清晰。",
        "language": seed["transcript"]["language"],
        "important_terms": ["flow"],
        "corrected_terms": [],
    }


def local_extract_for_tests(metadata, transcript_segments, **kwargs):
    return build_local_extract_payload(metadata, transcript_segments, **kwargs, qwen_extractor=fake_qwen_extract)


def fake_chunked_qwen_extract(seed: dict) -> dict:
    if seed.get("local_extract_mode") == "chunk":
        chunk = seed["chunk"]
        return {
            "chunk_start": chunk["start"],
            "chunk_end": chunk["end"],
            "core_theme": "测试 chunk 讲心流任务设计。",
            "useful_points": ["chunk 有可取观点。"],
            "skippable_content": ["重复解释可跳过。"],
            "candidate_watch_segments": [{"start": chunk["start"], "end": chunk["end"], "reason": "覆盖关键论证。"}],
            "information_density": "中：测试 chunk。",
            "scoring_evidence": ["有结构化证据。"],
            "transcript_quality_note": "测试转写清晰。",
            "language": seed["transcript"]["language"],
            "important_terms": ["flow"],
            "corrected_terms": [],
        }
    if seed.get("local_extract_mode") == "chunk_reduce":
        return fake_qwen_extract(seed)
    return fake_qwen_extract(seed)


def chunked_local_extract_for_tests(metadata, transcript_segments, **kwargs):
    return build_local_extract_payload(
        metadata,
        transcript_segments,
        **kwargs,
        qwen_extractor=fake_chunked_qwen_extract,
        chunked_char_threshold=1,
        chunked_request_bytes_threshold=999_999,
        chunked_segment_threshold=999_999,
        chunk_target_chars=90,
        chunk_overlap_chars=20,
    )


def report_cache_review_options(cache_dir: Path, *, force_reanalysis: bool = False, codex_model: str = "gpt-test") -> dict:
    return {
        "codex_model": codex_model,
        "report_cache_enabled": True,
        "report_cache_dir": cache_dir,
        "force_reanalysis": force_reanalysis,
    }


class VideoPipelineTest(unittest.TestCase):
    def test_low_transcript_coverage_blocks_qwen_codex_renderer_and_html(self) -> None:
        calls: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def resolve(_url):
                return {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://www.bilibili.com/video/BV1vR4y1o7xb/",
                    "videos": [
                        {
                            "title": "用上这个Notion月计划，学习工作井井有条，自律又高效！",
                            "url": "https://www.bilibili.com/video/BV1vR4y1o7xb/",
                            "duration": 187,
                            "bvid": "BV1vR4y1o7xb",
                            "cid": "27086488540",
                        }
                    ],
                }

            def fetch_subtitles(url, subtitle_dir, **kwargs):
                raise SubtitleUnavailableError("mock no subtitle")

            def download_audio(url, audio_dir, **kwargs):
                audio_path = audio_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {"audio_path": str(audio_path), "method": "yt_dlp", "cookies_source": "browser:chrome"}

            def transcribe(audio_path, transcript_dir, **kwargs):
                return transcript_material_from_segments([
                    {"start": "00:00", "end": "00:04", "text": "片头音乐"},
                    {"start": "00:04", "end": "00:11", "text": "歌词片段"},
                ])

            def local_extract(*args, **kwargs):
                calls.append("local_extract")
                raise AssertionError("local_extract must not run when transcript coverage is too low")

            def render(*args, **kwargs):
                calls.append("renderer")
                raise AssertionError("renderer must not run when transcript coverage is too low")

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=transcribe,
                build_local_extract_func=local_extract,
                render_func=render,
            )

            manifest = process_source(
                "https://www.bilibili.com/video/BV1vR4y1o7xb/",
                output_dir,
                review_response_provider=lambda *args: calls.append("codex_review") or {},
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 0)
            self.assertEqual(manifest["failed_count"], 1)
            error = manifest["items"][0]["error"]
            self.assertEqual(error["stage"], "transcript_quality")
            self.assertEqual(error["reason_code"], "transcript_coverage_too_low")
            self.assertEqual(error["debug"]["video_duration_seconds"], 187.0)
            self.assertEqual(error["debug"]["transcript_last_end"], 11.0)
            self.assertAlmostEqual(error["debug"]["transcript_coverage_ratio"], 11 / 187)
            self.assertEqual(error["debug"]["transcript_quality_reason"], "coverage_below_threshold")
            self.assertEqual(calls, [])
            self.assertFalse(list(output_dir.glob("*.html")))
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            steps = {step["step"]: step for step in item_manifest["steps"]}
            self.assertEqual(steps["transcript_quality"]["status"], "failed")
            self.assertNotIn("local_extract", steps)
            self.assertNotIn("codex_review", steps)
            self.assertNotIn("renderer", steps)

    def test_multipart_audio_parts_are_all_transcribed_and_merged_before_coverage_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            calls: list[str] = []
            captured: dict[str, Any] = {}

            def download_audio(url, audio_dir, **kwargs):
                audio_dir.mkdir(parents=True, exist_ok=True)
                paths = [audio_dir / "BV_mock_p1.wav", audio_dir / "BV_mock_p2.wav", audio_dir / "BV_mock_p3.wav"]
                for path in paths:
                    path.write_bytes(b"RIFFmock")
                return {"audio_path": str(paths[0]), "audio_paths": [str(path) for path in paths], "method": "yt_dlp"}

            def transcribe(audio_path, transcript_dir, **kwargs):
                calls.append(Path(audio_path).name)
                return transcript_material_from_segments([
                    {"start": "00:00", "end": "01:40", "text": f"{Path(audio_path).stem} 完整正文内容" * 20},
                ], source_kind="asr_wav", transcript_quality="degraded")

            def local_extract(metadata, transcript_segments, **kwargs):
                captured["segments"] = copy.deepcopy(transcript_segments)
                return local_extract_for_tests(metadata, transcript_segments, **kwargs)

            deps = PipelineDependencies(
                resolve_url_func=lambda _url: {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://www.bilibili.com/video/BV_mock/",
                    "videos": [{
                        "title": "multipart",
                        "url": "https://www.bilibili.com/video/BV_mock/",
                        "duration": 300,
                        "bvid": "BV_mock",
                    }],
                },
                fetch_subtitles_func=lambda url, subtitle_dir, **kwargs: (_ for _ in ()).throw(SubtitleUnavailableError("mock no subtitle")),
                download_audio_func=download_audio,
                transcribe_func=transcribe,
                build_local_extract_func=local_extract,
            )

            manifest = process_source(
                "https://www.bilibili.com/video/BV_mock/",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(calls, ["BV_mock_p1.wav", "BV_mock_p2.wav", "BV_mock_p3.wav"])
            self.assertEqual([segment["end"] for segment in captured["segments"]], ["01:40", "03:20", "05:00"])
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertTrue(audio_steps[0]["multipart_audio_detected"])
            self.assertEqual(audio_steps[0]["audio_part_count"], 3)
            merge_steps = [step for step in item_manifest["steps"] if step["step"] == "transcriber_merge"]
            self.assertEqual(merge_steps[0]["audio_part_count"], 3)
            quality_steps = [step for step in item_manifest["steps"] if step["step"] == "transcript_quality"]
            self.assertEqual(quality_steps[-1]["status"], "completed")
            self.assertEqual(quality_steps[-1]["transcript_last_end"], 300.0)

    def test_list_low_coverage_item_counts_as_failed_and_watch_order_shows_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            calls: list[str] = []

            def resolve(_url):
                return {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "list",
                    "url": "https://example.com/list",
                    "videos": [
                        {"title": "bad", "url": "https://example.com/bad", "duration": 187},
                    ],
                }

            def download_audio(url, audio_dir, **kwargs):
                calls.append("download_audio")
                audio_path = audio_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {"audio_path": str(audio_path), "method": "yt_dlp"}

            def transcribe(audio_path, transcript_dir, **kwargs):
                calls.append("transcribe")
                return transcript_material_from_segments([
                    {"start": "00:00", "end": "00:11", "text": "片头音乐"}
                ], source_kind="asr_wav", transcript_quality="degraded")

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                fetch_subtitles_func=lambda url, subtitle_dir, **kwargs: transcript_material_from_segments([
                    {"start": "00:00", "end": "00:11", "text": "片头音乐"}
                ], source_kind="subtitle_bcc", transcript_quality="ok"),
                download_audio_func=download_audio,
                transcribe_func=transcribe,
                build_local_extract_func=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local_extract must not run")),
            )

            manifest = process_source(
                "https://example.com/list",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 0)
            self.assertEqual(manifest["failed_count"], 1)
            self.assertEqual(calls, ["download_audio", "transcribe"])
            self.assertTrue((output_dir / "00-watch-order.html").exists())
            html = (output_dir / "00-watch-order.html").read_text(encoding="utf-8")
            self.assertIn("解析失败", html)
            self.assertIn("transcript_quality", html)
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            quality_steps = [step for step in item_manifest["steps"] if step["step"] == "transcript_quality"]
            self.assertEqual(quality_steps[0]["status"], "fallback_to_audio")
            self.assertEqual(quality_steps[0]["transcript_quality_reason"], "coverage_below_threshold")
            self.assertEqual(quality_steps[-1]["status"], "failed")
            self.assertEqual(quality_steps[-1]["transcript_source"], "asr_wav")

    def test_bilibili_ai_subtitle_title_transcript_mismatch_falls_back_to_audio(self) -> None:
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def download_audio(url, audio_dir, **kwargs):
                calls.append("download_audio")
                audio_dir.mkdir(parents=True, exist_ok=True)
                audio_path = audio_dir / "audio.wav"
                audio_path.write_bytes(b"RIFF")
                self.assertFalse(kwargs["subtitle_available"])
                return {"audio_path": str(audio_path), "method": "bilibili_playurl_api"}

            def transcribe(audio_path, transcript_dir, **kwargs):
                calls.append("transcribe")
                return transcript_material_from_segments([
                    {"start": "00:00", "end": "08:00", "text": "养成习惯成长速度自律方法复盘" * 40},
                    {"start": "08:00", "end": "16:00", "text": "顶尖的人通过环境设计和反馈机制保持长期行动" * 40},
                ], source_kind="asr_wav", transcript_quality="ok")

            deps = PipelineDependencies(
                resolve_url_func=lambda _url: {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://www.bilibili.com/video/BV1mismatch/",
                    "videos": [{
                        "title": "养成这5个习惯，你的成长速度会超越99%的人（顶尖的1%靠的不是自律）",
                        "url": "https://www.bilibili.com/video/BV1mismatch/",
                        "duration": "20:00",
                        "bvid": "BV1mismatch",
                        "cid": "12345",
                    }],
                },
                fetch_subtitles_func=lambda url, subtitle_dir, **kwargs: transcript_material_from_segments([
                    {"start": "00:00", "end": "08:00", "text": "蔡徐坤品牌代言粉丝消费力内娱偶像经济商业价值" * 30},
                    {"start": "08:00", "end": "16:30", "text": "明星塌房之后品牌合作和粉丝市场发生变化" * 30},
                ], source_kind="subtitle_bcc", transcript_quality="ok"),
                download_audio_func=download_audio,
                transcribe_func=transcribe,
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://www.bilibili.com/video/BV1mismatch/",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(calls, ["download_audio", "transcribe"])
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            quality_steps = [step for step in item_manifest["steps"] if step["step"] == "transcript_quality"]
            self.assertEqual(quality_steps[0]["status"], "fallback_to_audio")
            self.assertEqual(quality_steps[0]["reason_code"], "subtitle_title_transcript_mismatch")
            self.assertEqual(quality_steps[0]["transcript_quality_reason"], "title_transcript_mismatch")
            self.assertEqual(quality_steps[-1]["status"], "completed")
            self.assertEqual(quality_steps[-1]["transcript_source"], "asr_wav")

    def test_transcript_coverage_enough_continues_normally(self) -> None:
        calls: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def local_extract(metadata, transcript_segments, **kwargs):
                calls.append("local_extract")
                return local_extract_for_tests(metadata, transcript_segments, **kwargs)

            deps = PipelineDependencies(
                resolve_url_func=lambda _url: {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://example.com/watch",
                    "videos": [{"title": "ok", "url": "https://example.com/watch", "duration": 187}],
                },
                fetch_subtitles_func=lambda url, subtitle_dir, **kwargs: transcript_material_from_segments([
                    {"start": "00:00", "end": "01:05", "text": "足够覆盖的正文内容" * 8},
                    {"start": "01:05", "end": "02:10", "text": "继续覆盖正文内容" * 8},
                ], source_kind="subtitle_vtt", transcript_quality="ok"),
                build_local_extract_func=local_extract,
            )

            manifest = process_source(
                "https://example.com/watch",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(calls, ["local_extract"])
            self.assertTrue(Path(manifest["items"][0]["html_path"]).exists())

    def test_report_target_is_written_to_manifest_review_request_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            deps = PipelineDependencies(
                resolve_url_func=lambda _url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir, **kwargs: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                review_options={"report_target": "knowledge_notes", "codex_model": "gpt-test"},
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=target_review_provider,
                deps=deps,
            )

            self.assertEqual(manifest["report_target"], "knowledge_notes")
            self.assertEqual(manifest["items"][0]["report_target"], "knowledge_notes")
            payload = json.loads(Path(manifest["items"][0]["payload_path"]).read_text(encoding="utf-8"))
            request = json.loads((Path(manifest["items"][0]["item_manifest_path"]).parent / "review_request.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["report_target"], "knowledge_notes")
            self.assertEqual(request["report_target"], "knowledge_notes")
            self.assertIn("target_sections", payload)

    def test_short_video_does_not_fail_on_low_coverage_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            deps = PipelineDependencies(
                resolve_url_func=lambda _url: {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://example.com/short",
                    "videos": [{"title": "short", "url": "https://example.com/short", "duration": 45}],
                },
                fetch_subtitles_func=lambda url, subtitle_dir, **kwargs: transcript_material_from_segments([
                    {"start": "00:00", "end": "00:10", "text": "短视频正文内容" * 8},
                ]),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/short",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            quality_steps = [step for step in item_manifest["steps"] if step["step"] == "transcript_quality"]
            self.assertEqual(quality_steps[0]["status"], "completed")

    def test_missing_duration_uses_segment_and_text_weak_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            deps = PipelineDependencies(
                resolve_url_func=lambda _url: {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://example.com/no-duration",
                    "videos": [{"title": "no duration", "url": "https://example.com/no-duration", "duration": ""}],
                },
                fetch_subtitles_func=lambda url, subtitle_dir, **kwargs: transcript_material_from_segments([
                    {"start": "00:00", "end": "00:03", "text": "短"},
                ]),
                build_local_extract_func=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local_extract must not run")),
            )

            manifest = process_source(
                "https://example.com/no-duration",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["failed_count"], 1)
            error = manifest["items"][0]["error"]
            self.assertEqual(error["stage"], "transcript_quality")
            self.assertEqual(error["reason_code"], "transcript_coverage_too_low")
            self.assertEqual(error["debug"]["transcript_quality_reason"], "segment_count_too_low_without_duration")

    def test_platform_subtitle_with_missing_duration_falls_back_to_audio(self) -> None:
        calls: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            segments = [
                {"start": "00:13", "end": "00:14", "text": "这是低覆盖字幕内容一"},
                {"start": "00:14", "end": "00:16", "text": "这是低覆盖字幕内容二"},
                {"start": "00:16", "end": "00:18", "text": "这是低覆盖字幕内容三"},
                {"start": "00:18", "end": "00:20", "text": "这是低覆盖字幕内容四"},
                {"start": "00:20", "end": "00:22", "text": "这是低覆盖字幕内容五"},
                {"start": "00:22", "end": "00:24", "text": "这是低覆盖字幕内容六"},
                {"start": "00:24", "end": "00:26", "text": "这是低覆盖字幕内容七"},
                {"start": "00:26", "end": "00:28", "text": "这是低覆盖字幕内容八"},
                {"start": "00:28", "end": "00:30", "text": "这是低覆盖字幕内容九"},
                {"start": "00:30", "end": "00:32", "text": "这是低覆盖字幕内容十"},
                {"start": "00:32", "end": "00:34", "text": "这是低覆盖字幕内容十一"},
                {"start": "00:34", "end": "00:35", "text": "这是低覆盖字幕内容十二"},
            ]

            def download_audio(url, audio_dir, **kwargs):
                calls.append("download_audio")
                audio_dir.mkdir(parents=True, exist_ok=True)
                audio_path = audio_dir / "audio.wav"
                audio_path.write_bytes(b"RIFF")
                self.assertFalse(kwargs["subtitle_available"])
                return {"audio_path": str(audio_path), "method": "bilibili_playurl_api"}

            def transcribe(audio_path, transcript_dir, **kwargs):
                calls.append("transcribe")
                return transcript_material_from_segments([
                    {"start": "00:00", "end": "00:50", "text": "音频转写后的完整正文内容" * 20},
                    {"start": "00:50", "end": "01:40", "text": "继续音频转写后的正文内容" * 20},
                    {"start": "01:40", "end": "02:30", "text": "第三段音频转写后的正文内容" * 20},
                ], source_kind="asr_wav", transcript_quality="ok")

            deps = PipelineDependencies(
                resolve_url_func=lambda _url: {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "list",
                    "url": "https://example.com/list",
                    "videos": [{"title": "missing duration bcc", "url": "https://example.com/bad", "duration": ""}],
                },
                fetch_subtitles_func=lambda url, subtitle_dir, **kwargs: transcript_material_from_segments(
                    segments,
                    source_kind="subtitle_bcc",
                    transcript_quality="ok",
                ),
                download_audio_func=download_audio,
                transcribe_func=transcribe,
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/list",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(calls, ["download_audio", "transcribe"])
            self.assertTrue((output_dir / "00-watch-order.html").exists())
            self.assertTrue(list(output_dir.glob("01-*.html")))
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            quality_steps = [step for step in item_manifest["steps"] if step["step"] == "transcript_quality"]
            self.assertEqual(quality_steps[0]["status"], "fallback_to_audio")
            self.assertEqual(quality_steps[0]["transcript_quality_reason"], "video_duration_missing_for_quality_gate")
            self.assertEqual(quality_steps[-1]["status"], "completed")
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(audio_steps[0]["subtitle_rejection_reason"], "video_duration_missing_for_quality_gate")

    def test_missing_duration_with_enough_segments_and_text_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            deps = PipelineDependencies(
                resolve_url_func=lambda _url: {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://example.com/no-duration-ok",
                    "videos": [{"title": "no duration ok", "url": "https://example.com/no-duration-ok", "duration": ""}],
                },
                fetch_subtitles_func=lambda url, subtitle_dir, **kwargs: transcript_material_from_segments([
                    {"start": "00:00", "end": "00:10", "text": "第一段有效内容" * 5},
                    {"start": "00:10", "end": "00:20", "text": "第二段有效内容" * 5},
                    {"start": "00:20", "end": "00:30", "text": "第三段有效内容" * 5},
                ]),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/no-duration-ok",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)

    def test_metadata_publish_date_prefers_explicit_video_date_fields(self) -> None:
        metadata, debug = metadata_from_item_with_debug({
            "title": "video",
            "url": "https://example.com/watch",
            "publish_date": "20260428",
            "release_date": "20260427",
            "upload_date": "20260429",
        })

        self.assertEqual(metadata["date"], "2026-04-28")
        self.assertFalse(debug["publish_date_missing"])
        self.assertEqual(debug["publish_date_source"], "publish_date")

    def test_metadata_upload_date_and_timestamp_are_formatted_as_video_dates(self) -> None:
        self.assertEqual(metadata_from_item({"upload_date": "20260429"})["date"], "2026-04-29")
        self.assertEqual(metadata_from_item({"timestamp": 1777464000})["date"], "2026-04-29")

    def test_metadata_missing_publish_date_is_unknown_and_debugged(self) -> None:
        metadata, debug = metadata_from_item_with_debug({"title": "video", "url": "https://example.com/watch"})

        self.assertEqual(metadata["date"], "未知")
        self.assertTrue(debug["publish_date_missing"])
        self.assertIsNone(debug["publish_date_source"])

    def test_metadata_duration_uses_chinese_natural_format(self) -> None:
        cases = [
            (45, "45秒"),
            (723, "12分03秒"),
            (2698, "44分58秒"),
            (3723, "1小时02分03秒"),
            ("44:58", "44分58秒"),
            ("01:02:03", "1小时02分03秒"),
        ]
        for raw_duration, expected in cases:
            with self.subTest(raw_duration=raw_duration):
                self.assertEqual(metadata_from_item({"duration": raw_duration})["duration"], expected)

    def test_metadata_missing_duration_is_unknown_and_debugged(self) -> None:
        metadata, debug = metadata_from_item_with_debug({"title": "video", "url": "https://example.com/watch"})

        self.assertEqual(metadata["duration"], "未知")
        self.assertTrue(debug["duration_missing"])
        self.assertIsNone(debug["duration_source"])

    def test_pipeline_overrides_report_date_and_duration_from_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            captured: dict[str, Any] = {}

            def resolve(_url):
                result = single_resolution()
                result["videos"][0].update({
                    "duration": 2698,
                    "publish_date": "20260429",
                    "date": "2026-04-20",
                })
                return result

            def review_provider(item, review_request, local_extract):
                payload = load_golden("sample_payload_heartflow.json")
                payload["date"] = "2099-01-01"
                payload["duration"] = "00:44:58"
                return payload

            def local_extract(metadata, transcript_segments, **kwargs):
                captured["metadata"] = metadata
                return local_extract_for_tests(metadata, transcript_segments, **kwargs)

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                fetch_subtitles_func=lambda url, subtitle_dir: coverage_material("44:58"),
                build_local_extract_func=local_extract,
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider,
                deps=deps,
            )

            payload = json.loads(Path(manifest["items"][0]["payload_path"]).read_text(encoding="utf-8"))
            self.assertEqual(captured["metadata"]["date"], "2026-04-29")
            self.assertEqual(captured["metadata"]["duration"], "44分58秒")
            self.assertEqual(payload["date"], "2026-04-29")
            self.assertEqual(payload["duration"], "44分58秒")

            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            metadata_steps = [step for step in item_manifest["steps"] if step["step"] == "metadata"]
            self.assertEqual(metadata_steps[0]["publish_date_source"], "publish_date")
            self.assertFalse(metadata_steps[0]["publish_date_missing"])
            self.assertEqual(metadata_steps[0]["duration_source"], "duration")
            self.assertFalse(metadata_steps[0]["duration_missing"])

    def test_single_fixture_pipeline_writes_html_payload_and_manifest(self) -> None:
        events: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fetch_subtitles(url, subtitle_dir):
                events.append("fetch_subtitles")
                return fixture_material()

            def download_audio(*args, **kwargs):
                raise AssertionError("audio downloader must not run when subtitles are available")

            def cleanup(path: Path) -> None:
                events.append("cleanup")
                self.assertTrue((output_dir / "01-如何把任务改造成更容易进入心流的状态.html").exists())
                shutil.rmtree(path, ignore_errors=True)

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                build_local_extract_func=local_extract_for_tests,
                cleanup_func=cleanup,
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(events, ["fetch_subtitles", "cleanup"])
            self.assertTrue(Path(manifest["items"][0]["html_path"]).exists())
            self.assertFalse((output_dir / "00-watch-order.html").exists())
            payload = json.loads(Path(manifest["items"][0]["payload_path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["title"], "如何把任务改造成更容易进入心流的状态")
            saved_manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_manifest["completed_count"], 1)
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            subtitle_steps = [step for step in item_manifest["steps"] if step["step"] == "subtitle_fetcher"]
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(subtitle_steps[0]["status"], "completed")
            self.assertTrue(audio_steps[0]["audio_downloader_skipped_due_to_subtitle"])
            self.assertEqual(audio_steps[0]["status"], "skipped")

    def test_bilibili_provider_success_enters_local_extract_without_audio_or_transcriber(self) -> None:
        captured: dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            secret_cookie = "SESSDATA=SECRET_COOKIE_VALUE"
            material = {
                "material_version": "watchbrief_v5.transcript_material.v1",
                "source": {
                    "kind": "subtitle_bcc",
                    "provider": "bilibili_content_provider",
                    "source_platform": "bilibili",
                    "subtitle_language": "zh",
                    "subtitle_kind": "manual",
                    "subtitle_format": "bcc",
                    "bvid": "BV15qQwB4EZ9",
                    "aid": "116379755745719",
                    "cid": "37390191415",
                    "title": "视频内容一键保存到 Obsidian：打通本地知识库",
                    "source_url": "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                },
                "language": "zh",
                "transcript_quality": "ok",
                "segment_count": 2,
                "char_count": 80,
                "has_timestamps": True,
                "segments": [
                    {"start": "00:00", "end": "03:00", "text": "第一段正文内容" * 5},
                    {"start": "03:00", "end": "08:43", "text": "第二段正文内容" * 5},
                ],
                "adapter_boundary": {
                    "no_audio_download": True,
                    "no_transcription": True,
                    "no_model_call": True,
                },
            }

            def resolve(_url):
                return {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                    "videos": [
                        {
                            "title": "视频内容一键保存到 Obsidian：打通本地知识库",
                            "url": "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                            "channel": "小陈同学c_z",
                            "duration": "523",
                            "date": "2026-04-28",
                            "bvid": "BV15qQwB4EZ9",
                            "cid": "37390191415",
                        }
                    ],
                }

            def fetch_subtitles(url, subtitle_dir, **kwargs):
                captured["subtitle_url"] = url
                captured["subtitle_kwargs"] = dict(kwargs)
                return {
                    "material": material,
                    "command": ["bilibili-content-provider", url],
                    "debug": {
                        "provider": "bilibili_content_provider",
                        "source_platform": "bilibili",
                        "source_api": "player-wbi-v2",
                        "selected_subtitle_lang": "zh",
                        "selected_subtitle_kind": "manual",
                        "selected_subtitle_format": "bcc",
                        "audio_downloader_skipped_due_to_subtitle": True,
                        "need_login_subtitle": False,
                        "cookies_source": "browser:safari",
                    },
                }

            def download_audio(*args, **kwargs):
                raise AssertionError("audio downloader must not run when Bilibili provider returns subtitles")

            def transcribe(*args, **kwargs):
                raise AssertionError("MLX/Whisper transcriber must not run when Bilibili provider returns subtitles")

            def local_extract(metadata, transcript_segments, **kwargs):
                captured["local_extract_metadata"] = metadata
                captured["local_extract_segments"] = transcript_segments
                return local_extract_for_tests(metadata, transcript_segments, **kwargs)

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=transcribe,
                build_local_extract_func=local_extract,
                subtitle_options={"cookies_from_browser": "safari", "cookie_header": secret_cookie},
            )

            manifest = process_source(
                "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(captured["subtitle_url"], "https://www.bilibili.com/video/BV15qQwB4EZ9/")
            self.assertEqual(captured["subtitle_kwargs"]["cookies_from_browser"], "safari")
            self.assertEqual(len(captured["local_extract_segments"]), 2)
            self.assertEqual(captured["local_extract_metadata"]["title"], "视频内容一键保存到 Obsidian：打通本地知识库")
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            subtitle_steps = [step for step in item_manifest["steps"] if step["step"] == "subtitle_fetcher"]
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            transcriber_steps = [step for step in item_manifest["steps"] if step["step"] == "transcriber"]
            local_extract_steps = [step for step in item_manifest["steps"] if step["step"] == "local_extract"]

            self.assertEqual(subtitle_steps[0]["status"], "completed")
            self.assertEqual(subtitle_steps[0]["source"]["provider"], "bilibili_content_provider")
            self.assertEqual(subtitle_steps[0]["source"]["source_platform"], "bilibili")
            self.assertEqual(subtitle_steps[0]["source"]["subtitle_language"], "zh")
            self.assertEqual(subtitle_steps[0]["source"]["subtitle_format"], "bcc")
            self.assertEqual(subtitle_steps[0]["segment_count"], 2)
            self.assertEqual(audio_steps[0]["status"], "skipped")
            self.assertTrue(audio_steps[0]["audio_downloader_skipped_due_to_subtitle"])
            self.assertEqual(transcriber_steps, [])
            self.assertEqual(local_extract_steps[-1]["status"], "completed")
            self.assertNotIn("SECRET_COOKIE_VALUE", json.dumps(item_manifest, ensure_ascii=False))

    def test_subtitle_timeline_mismatch_falls_back_to_audio_transcription(self) -> None:
        captured: dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def resolve(_url):
                return {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://www.bilibili.com/video/BV1vR4y1o7xb/",
                    "videos": [
                        {
                            "title": "用上这个Notion月计划，学习工作井井有条，自律又高效！",
                            "url": "https://www.bilibili.com/video/BV1vR4y1o7xb/",
                            "duration": 186,
                            "bvid": "BV1vR4y1o7xb",
                            "cid": "27086488540",
                        }
                    ],
                }

            def fetch_subtitles(url, subtitle_dir, **kwargs):
                return transcript_material_from_segments(
                    [
                        {"start": "00:33", "end": "00:36", "text": "错误字幕内容" * 10},
                        {"start": "54:33", "end": "54:36", "text": "明显属于其他长视频的字幕" * 10},
                    ],
                    source_kind="subtitle_bcc",
                    transcript_quality="ok",
                )

            def download_audio(url, audio_dir, **kwargs):
                captured["audio_kwargs"] = dict(kwargs)
                audio_path = audio_dir / "fallback.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {
                    "audio_path": str(audio_path),
                    "method": "bilibili_playurl_api",
                    "cookies_source": "browser:chrome",
                }

            def transcribe(audio_path, transcript_dir, **kwargs):
                captured["transcribed_audio_path"] = str(audio_path)
                return transcript_material_from_segments(
                    [
                        {"start": "00:00", "end": "01:20", "text": "音频转写后的有效正文" * 8},
                        {"start": "01:20", "end": "03:06", "text": "继续覆盖 Notion 月计划内容" * 8},
                    ],
                    source_kind="asr_wav",
                    transcript_quality="degraded",
                )

            def local_extract(metadata, transcript_segments, **kwargs):
                captured["local_extract_source"] = kwargs.get("transcript_source")
                captured["local_extract_segments"] = transcript_segments
                return local_extract_for_tests(metadata, transcript_segments, **kwargs)

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=transcribe,
                build_local_extract_func=local_extract,
            )

            manifest = process_source(
                "https://www.bilibili.com/video/BV1vR4y1o7xb/",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(captured["audio_kwargs"]["subtitle_available"], False)
            self.assertEqual(captured["audio_kwargs"]["bvid"], "BV1vR4y1o7xb")
            self.assertEqual(captured["audio_kwargs"]["cid"], "27086488540")
            self.assertEqual(captured["local_extract_source"], "asr_wav")
            self.assertEqual(captured["local_extract_segments"][-1]["end"], "03:06")
            self.assertTrue(Path(manifest["items"][0]["html_path"]).exists())

            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            quality_steps = [step for step in item_manifest["steps"] if step["step"] == "transcript_quality"]
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            transcriber_steps = [step for step in item_manifest["steps"] if step["step"] == "transcriber"]
            local_extract_steps = [step for step in item_manifest["steps"] if step["step"] == "local_extract"]

            self.assertEqual(quality_steps[0]["status"], "fallback_to_audio")
            self.assertEqual(quality_steps[0]["transcript_quality_reason"], "subtitle_timeline_exceeds_video_duration")
            self.assertGreater(quality_steps[0]["transcript_last_end"], quality_steps[0]["video_duration_seconds"])
            self.assertEqual(audio_steps[0]["status"], "completed")
            self.assertFalse(audio_steps[0]["audio_downloader_skipped_due_to_subtitle"])
            self.assertTrue(audio_steps[0]["subtitle_rejected_due_to_quality"])
            self.assertEqual(transcriber_steps[0]["status"], "completed")
            self.assertEqual(quality_steps[-1]["status"], "completed")
            self.assertEqual(quality_steps[-1]["transcript_source"], "asr_wav")
            self.assertEqual(local_extract_steps[-1]["status"], "completed")

    def test_low_coverage_platform_subtitle_falls_back_to_audio_transcription(self) -> None:
        captured: dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def resolve(_url):
                return {
                    "resolution_version": "watchbrief_v5.resolution.v1",
                    "source_kind": "single",
                    "url": "https://www.bilibili.com/video/BV18YiRYiEEC/",
                    "videos": [
                        {
                            "title": "从正常压到虚弱元凶是皮质醇",
                            "url": "https://www.bilibili.com/video/BV18YiRYiEEC/",
                            "duration": 920,
                            "bvid": "BV18YiRYiEEC",
                            "cid": "27251575129",
                        }
                    ],
                }

            def fetch_subtitles(url, subtitle_dir, **kwargs):
                return transcript_material_from_segments(
                    [
                        {"start": "00:00", "end": "00:04", "text": "音乐"},
                        {"start": "01:31", "end": "01:34", "text": "片头音乐"},
                    ],
                    source_kind="subtitle_bcc",
                    transcript_quality="ok",
                )

            def download_audio(url, audio_dir, **kwargs):
                captured["audio_kwargs"] = dict(kwargs)
                audio_path = audio_dir / "fallback.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {
                    "audio_path": str(audio_path),
                    "method": "bilibili_playurl_api",
                    "cookies_source": "browser:chrome",
                }

            def transcribe(audio_path, transcript_dir, **kwargs):
                captured["transcribed_audio_path"] = str(audio_path)
                return transcript_material_from_segments(
                    [
                        {"start": "00:00", "end": "07:00", "text": "音频转写后的有效正文" * 12},
                        {"start": "07:00", "end": "15:20", "text": "继续覆盖皮质醇和压力内容" * 12},
                    ],
                    source_kind="asr_wav",
                    transcript_quality="degraded",
                )

            def local_extract(metadata, transcript_segments, **kwargs):
                captured["local_extract_source"] = kwargs.get("transcript_source")
                captured["local_extract_segments"] = transcript_segments
                return local_extract_for_tests(metadata, transcript_segments, **kwargs)

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=transcribe,
                build_local_extract_func=local_extract,
            )

            manifest = process_source(
                "https://www.bilibili.com/video/BV18YiRYiEEC/",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(captured["audio_kwargs"]["subtitle_available"], False)
            self.assertEqual(captured["audio_kwargs"]["bvid"], "BV18YiRYiEEC")
            self.assertEqual(captured["audio_kwargs"]["cid"], "27251575129")
            self.assertEqual(captured["local_extract_source"], "asr_wav")
            self.assertEqual(captured["local_extract_segments"][-1]["end"], "15:20")
            self.assertTrue(Path(manifest["items"][0]["html_path"]).exists())

            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            quality_steps = [step for step in item_manifest["steps"] if step["step"] == "transcript_quality"]
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            transcriber_steps = [step for step in item_manifest["steps"] if step["step"] == "transcriber"]
            local_extract_steps = [step for step in item_manifest["steps"] if step["step"] == "local_extract"]

            self.assertEqual(quality_steps[0]["status"], "fallback_to_audio")
            self.assertEqual(quality_steps[0]["reason_code"], "subtitle_quality_insufficient")
            self.assertEqual(quality_steps[0]["transcript_quality_reason"], "coverage_below_threshold")
            self.assertAlmostEqual(quality_steps[0]["transcript_coverage_ratio"], 94 / 920)
            self.assertEqual(audio_steps[0]["status"], "completed")
            self.assertTrue(audio_steps[0]["subtitle_rejected_due_to_quality"])
            self.assertEqual(audio_steps[0]["subtitle_rejection_reason"], "coverage_below_threshold")
            self.assertEqual(transcriber_steps[0]["status"], "completed")
            self.assertEqual(quality_steps[-1]["status"], "completed")
            self.assertEqual(quality_steps[-1]["transcript_source"], "asr_wav")
            self.assertEqual(local_extract_steps[-1]["status"], "completed")

    def test_youtube_resolver_platform_restriction_uses_connect_fallback_without_audio(self) -> None:
        captured: dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            source_url = "https://www.youtube.com/watch?v=Er2s-CFoZSo"

            def resolve(_url, **_kwargs):
                raise PlatformRestrictionError(
                    "resolver",
                    "platform restricted resolver access",
                    ["yt-dlp", "--dump-single-json", source_url],
                    "Sign in to confirm you’re not a bot",
                )

            def fallback(url):
                captured["fallback_url"] = url
                return SimpleNamespace(
                    provider="youtube-connect",
                    source_platform="youtube",
                    video_id="Er2s-CFoZSo",
                    title="Er2s-CFoZSo",
                    duration="42:00",
                    playlist_title="",
                    subtitle_kind="manual",
                    subtitle_lang="en",
                    subtitle_format="transcript",
                    plain_text="first second",
                    source_url=source_url,
                    segments=[
                        {"start": "00:00", "end": "20:00", "text": "first " * 20},
                        {"start": "20:00", "end": "42:00", "text": "second " * 20},
                    ],
                )

            def fetch_subtitles(*args, **kwargs):
                raise AssertionError("subtitle fetcher must not run when YouTube Connect fallback already returned transcript")

            def download_audio(*args, **kwargs):
                raise AssertionError("audio downloader must not run after YouTube Connect fallback succeeds")

            def transcribe(*args, **kwargs):
                raise AssertionError("MLX/Whisper transcriber must not run after YouTube Connect fallback succeeds")

            def local_extract(metadata, transcript_segments, **kwargs):
                captured["local_extract_metadata"] = metadata
                captured["local_extract_segments"] = transcript_segments
                return local_extract_for_tests(metadata, transcript_segments, **kwargs)

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                youtube_connect_fallback_func=fallback,
                youtube_chrome_fallback_func=None,
                youtube_safari_fallback_func=None,
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=transcribe,
                build_local_extract_func=local_extract,
            )

            manifest = process_source(
                source_url,
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(captured["fallback_url"], source_url)
            self.assertEqual(len(captured["local_extract_segments"]), 2)
            self.assertEqual(captured["local_extract_metadata"]["title"], "Er2s-CFoZSo")

            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            self.assertTrue(item_manifest["resolver_failed"])
            self.assertEqual(item_manifest["resolver_reason_code"], "platform_restriction")
            self.assertEqual(item_manifest["transcript_fallback_provider"], "youtube-connect")
            self.assertTrue(item_manifest["transcript_fallback_success"])
            fallback_steps = [step for step in item_manifest["steps"] if step["step"] == "transcript_fallback"]
            subtitle_steps = [step for step in item_manifest["steps"] if step["step"] == "subtitle_fetcher"]
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            transcriber_steps = [step for step in item_manifest["steps"] if step["step"] == "transcriber"]
            local_extract_steps = [step for step in item_manifest["steps"] if step["step"] == "local_extract"]
            self.assertEqual(fallback_steps[0]["status"], "completed")
            self.assertEqual(fallback_steps[0]["subtitle_lang"], "en")
            self.assertEqual(fallback_steps[0]["subtitle_format"], "transcript")
            self.assertEqual(fallback_steps[0]["segment_count"], 2)
            self.assertEqual(subtitle_steps[0]["status"], "skipped")
            self.assertEqual(audio_steps[0]["status"], "skipped")
            self.assertTrue(audio_steps[0]["audio_downloader_skipped_due_to_subtitle"])
            self.assertEqual(transcriber_steps, [])
            self.assertEqual(local_extract_steps[-1]["status"], "completed")

    def test_youtube_connect_failure_uses_chrome_page_transcript_before_safari(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_url = "https://www.youtube.com/watch?v=Er2s-CFoZSo"
            captured: dict[str, Any] = {}

            def resolve(_url, **_kwargs):
                raise PlatformRestrictionError(
                    "resolver",
                    "platform restricted resolver access",
                    ["yt-dlp", "--dump-single-json", source_url],
                    "Sign in to confirm you’re not a bot",
                )

            def connect_fallback(_url):
                raise SubtitleUnavailableError("YouTube Connect transcript unavailable")

            def chrome_fallback(_url):
                captured["chrome_url"] = _url
                from scripts.providers.youtube_connect_provider import YouTubeConnectTranscript

                return YouTubeConnectTranscript(
                    provider="chrome-transcript",
                    source_platform="youtube",
                    video_id="Er2s-CFoZSo",
                    title="Chrome fallback title",
                    duration="00:20",
                    playlist_title="",
                    subtitle_kind="browser_transcript",
                    subtitle_lang="zh-Hans",
                    subtitle_format="yt_initial_data_transcript",
                    plain_text="第一段 第二段",
                    source_url=source_url,
                    segments=[
                        {"start": "00:00", "end": "00:10", "text": "第一段" * 20},
                        {"start": "00:10", "end": "00:20", "text": "第二段" * 20},
                    ],
                )

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                youtube_connect_fallback_func=connect_fallback,
                youtube_chrome_fallback_func=chrome_fallback,
                youtube_safari_fallback_func=lambda _url: (_ for _ in ()).throw(AssertionError("Safari fallback must not run after Chrome succeeds")),
                fetch_subtitles_func=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subtitle fetcher must not run")),
                download_audio_func=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("audio downloader must not run")),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                source_url,
                Path(temp_dir),
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(captured["chrome_url"], source_url)
            self.assertEqual(manifest["resolver_debug"]["fallback_method"], "chrome_yt_initial_data_transcript")
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(item_manifest["transcript_fallback_provider"], "chrome-transcript")
            self.assertTrue(item_manifest["transcript_fallback_success"])

    def test_youtube_debug_platform_restriction_triggers_safari_fallback_after_generic_resolver_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_url = "https://www.youtube.com/watch?v=Er2s-CFoZSo"
            captured: dict[str, Any] = {}

            def resolve(_url, **_kwargs):
                exc = ResolverError("yt-dlp failed to resolve URL", ["yt-dlp", "--dump-single-json", source_url], "")
                exc.debug = {
                    "resolver_method": "yt_dlp",
                    "cookies_source": "browser:safari",
                    "cookies_fallback_reason": "platform_restriction",
                    "fallback_method": "",
                    "fallback_success": False,
                }
                raise exc

            def connect_fallback(_url):
                raise SubtitleUnavailableError("YouTube Connect transcript unavailable")

            def safari_fallback(_url):
                captured["safari_url"] = _url
                from scripts.providers.youtube_connect_provider import YouTubeConnectTranscript

                return YouTubeConnectTranscript(
                    provider="safari-transcript",
                    source_platform="youtube",
                    video_id="Er2s-CFoZSo",
                    title="Safari fallback title",
                    duration="00:20",
                    playlist_title="",
                    subtitle_kind="browser_transcript",
                    subtitle_lang="zh-Hans",
                    subtitle_format="yt_initial_data_transcript",
                    plain_text="第一段 第二段",
                    source_url=source_url,
                    segments=[
                        {"start": "00:00", "end": "00:10", "text": "第一段" * 20},
                        {"start": "00:10", "end": "00:20", "text": "第二段" * 20},
                    ],
                )

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                youtube_connect_fallback_func=connect_fallback,
                resolver_options={"cookies_from_browser": "safari", "allow_browser_auth": True},
                youtube_safari_fallback_func=safari_fallback,
                fetch_subtitles_func=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("subtitle fetcher must not run")),
                download_audio_func=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("audio downloader must not run")),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                source_url,
                Path(temp_dir),
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(captured["safari_url"], source_url)
            self.assertEqual(manifest["resolver_debug"]["fallback_method"], "safari_yt_initial_data_transcript")
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(item_manifest["transcript_fallback_provider"], "safari-transcript")
            self.assertTrue(item_manifest["transcript_fallback_success"])

    def test_youtube_connect_fallback_failure_preserves_resolver_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_url = "https://www.youtube.com/watch?v=Er2s-CFoZSo"

            def resolve(_url, **_kwargs):
                raise PlatformRestrictionError(
                    "resolver",
                    "platform restricted resolver access",
                    ["yt-dlp", "--dump-single-json", source_url],
                    "Sign in to confirm you’re not a bot",
                )

            def fallback(_url):
                raise SubtitleUnavailableError("YouTube Connect transcript unavailable")

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                youtube_connect_fallback_func=fallback,
                youtube_chrome_fallback_func=None,
                youtube_safari_fallback_func=None,
                download_audio_func=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("audio downloader must not run")),
            )

            manifest = process_source(
                source_url,
                Path(temp_dir),
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 0)
            self.assertEqual(manifest["failed_count"], 1)
            error = manifest["items"][0]["error"]
            self.assertEqual(error["stage"], "resolver")
            self.assertEqual(error["reason_code"], "platform_restriction")
            self.assertFalse(manifest["items"][0]["transcript_fallback_debug"]["transcript_fallback_success"])

    def test_resolver_options_are_passed_to_resolver(self) -> None:
        seen_kwargs: dict = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def resolve(url, **kwargs):
                seen_kwargs.update(kwargs)
                return single_resolution()

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                resolver_options={"cookies_from_browser": "chrome", "allow_browser_auth": True},
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(seen_kwargs["cookies_from_browser"], "chrome")

    def test_resolver_failure_writes_manifest_with_specific_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def resolve(url):
                raise BilibiliResolverError(
                    "bilibili_412_blocked",
                    "Bilibili returned HTTP 412",
                    ["yt-dlp", url],
                    "HTTP Error 412",
                    debug={
                        "resolver_method": "yt_dlp",
                        "cookies_source": "browser:chrome",
                        "fallback_method": "bilibili_page_metadata",
                        "fallback_success": False,
                        "reason_code": "bilibili_412_blocked",
                    },
                )

            deps = PipelineDependencies(resolve_url_func=resolve)

            manifest = process_source(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 0)
            self.assertEqual(manifest["failed_count"], 1)
            self.assertEqual(manifest["items"][0]["error"]["stage"], "resolver")
            self.assertEqual(manifest["items"][0]["error"]["reason_code"], "bilibili_412_blocked")
            self.assertEqual(manifest["items"][0]["resolver_debug"]["cookies_source"], "browser:chrome")
            saved_manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_manifest["failed_count"], 1)

    def test_list_fixture_processes_items_strictly_one_by_one(self) -> None:
        events: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fetch_subtitles(url, subtitle_dir):
                events.append(f"fetch:{url.rsplit('/', 1)[-1]}")
                return fixture_material()

            def review_provider(item, review_request, local_extract):
                events.append(f"review:{item['url'].rsplit('/', 1)[-1]}")
                return review_provider_for_golden(item, review_request, local_extract)

            def render(payload):
                events.append(f"render:{payload['url'].rsplit('/', 1)[-1]}")
                from scripts.renderer import render_single_video_html
                return render_single_video_html(payload)

            def cleanup(path: Path) -> None:
                events.append(f"cleanup:{path.name}")
                shutil.rmtree(path, ignore_errors=True)

            deps = PipelineDependencies(
                resolve_url_func=lambda url: list_resolution(),
                fetch_subtitles_func=fetch_subtitles,
                build_local_extract_func=local_extract_for_tests,
                render_func=render,
                cleanup_func=cleanup,
            )

            manifest = process_source(
                "https://example.com/list/mock",
                output_dir,
                review_response_provider=review_provider,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 2)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(events, [
                "fetch:heartflow",
                "review:heartflow",
                "render:heartflow",
                "cleanup:01-如何把任务改造成更容易进入心流的状态",
                "fetch:charm",
                "review:charm",
                "render:charm",
                "cleanup:02-魅力不是完美表演，而是让别人读到真实信号",
            ])
            self.assertEqual(len(list(output_dir.glob("*.html"))), 3)
            self.assertTrue((output_dir / "00-watch-order.html").exists())
            self.assertEqual(Path(manifest["watch_order_path"]).name, "00-watch-order.html")

    def test_list_html_files_keep_playlist_order_when_scores_differ(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def ranked_review_provider(item, review_request, local_extract):
                if "charm" in item["url"]:
                    payload = load_golden("sample_payload_charm.json")
                    payload["structured_assessment"] = {"信息密度": 6.0, "论据质量": 6.0, "独创性": 6.0, "观看性价比": 6.0}
                    payload["replacement_score"] = 6.0
                    payload["tag"] = "只建议跳看"
                    return payload
                payload = load_golden("sample_payload_heartflow.json")
                payload["structured_assessment"] = {"信息密度": 5.0, "论据质量": 5.0, "独创性": 5.0, "观看性价比": 5.0}
                payload["replacement_score"] = 5.0
                payload["tag"] = "只建议跳看"
                return payload

            deps = PipelineDependencies(
                resolve_url_func=lambda url: list_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/list/mock",
                output_dir,
                review_response_provider=ranked_review_provider,
                deps=deps,
            )

            first_playlist_item = output_dir / "01-如何把任务改造成更容易进入心流的状态.html"
            second_playlist_item = output_dir / "02-魅力不是完美表演，而是让别人读到真实信号.html"
            self.assertTrue(first_playlist_item.exists())
            self.assertTrue(second_playlist_item.exists())
            self.assertFalse((output_dir / "01-魅力不是完美表演，而是让别人读到真实信号.html").exists())
            self.assertFalse((output_dir / "02-如何把任务改造成更容易进入心流的状态.html").exists())

            watch_order = Path(manifest["watch_order_path"]).read_text(encoding="utf-8")
            self.assertLess(watch_order.index('"pageFile": "01-如何把任务改造成更容易进入心流的状态.html"'), watch_order.index('"pageFile": "02-魅力不是完美表演，而是让别人读到真实信号.html"'))
            self.assertEqual(Path(manifest["items"][0]["html_path"]).name, first_playlist_item.name)
            self.assertEqual(Path(manifest["items"][1]["html_path"]).name, second_playlist_item.name)
            item_manifest = json.loads(Path(manifest["items"][1]["item_manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(Path(item_manifest["html_path"]).name, second_playlist_item.name)
            self.assertNotIn("watch_order_rank", item_manifest)

    def test_xiaohongshu_board_non_video_note_is_skipped_before_audio_and_models(self) -> None:
        calls: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fetch_subtitles(url, subtitle_dir):
                calls.append(f"subtitle:{url.rsplit('/', 1)[-1]}")
                return fixture_material()

            def download_audio(*args, **kwargs):
                calls.append("audio")
                raise AssertionError("non-video note must not enter audio downloader")

            def build_local_extract(metadata, transcript_segments, **kwargs):
                calls.append(f"local:{metadata['url'].rsplit('/', 1)[-1]}")
                return local_extract_for_tests(metadata, transcript_segments, **kwargs)

            def review_provider(item, review_request, local_extract):
                calls.append(f"review:{item['url'].rsplit('/', 1)[-1]}")
                return review_provider_for_golden(item, review_request, local_extract)

            deps = PipelineDependencies(
                resolve_url_func=lambda url: xiaohongshu_board_resolution(),
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                build_local_extract_func=build_local_extract,
            )

            manifest = process_source(
                "https://www.xiaohongshu.com/board/mock",
                output_dir,
                review_response_provider=review_provider,
                deps=deps,
            )

            self.assertEqual(manifest["source_kind"], "list")
            self.assertEqual(manifest["source_subkind"], "xiaohongshu_board")
            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(manifest["skipped_count"], 1)
            self.assertEqual(manifest["non_video_count"], 1)
            self.assertEqual(manifest["items"][0]["status"], "completed")
            self.assertEqual(manifest["items"][1]["status"], "skipped")
            self.assertEqual(manifest["items"][1]["reason_code"], "non_video_note")
            self.assertEqual(calls, [
                "subtitle:video-note-1",
                "local:video-note-1",
                "review:video-note-1",
            ])
            self.assertTrue((output_dir / "01-小红书视频笔记.html").exists())
            self.assertFalse((output_dir / "02-小红书图文笔记.html").exists())
            watch_order = Path(manifest["watch_order_path"]).read_text(encoding="utf-8")
            self.assertIn("总 note 数", watch_order)
            self.assertIn("视频 note 1", watch_order)
            self.assertIn("图文跳过 1", watch_order)
            self.assertIn('"type": "skipped"', watch_order)
            self.assertIn('"filterKey": "skipped"', watch_order)
            item_manifest = json.loads(Path(manifest["items"][1]["item_manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(item_manifest["status"], "skipped")
            self.assertTrue(item_manifest["steps"][0]["audio_downloader_skipped"])

    def test_xiaohongshu_board_video_uses_internal_download_url_without_persisting_xsec(self) -> None:
        seen: dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            resolution = xiaohongshu_board_resolution()
            resolution["videos"] = [
                {
                    **resolution["videos"][0],
                    "url": "https://www.xiaohongshu.com/explore/video-note-1",
                    "_download_url": "https://www.xiaohongshu.com/explore/video-note-1?xsec_token=SECRET_XSEC",
                }
            ]
            resolution["note_count"] = 1
            resolution["video_note_count"] = 1
            resolution["non_video_count"] = 0

            def fetch_subtitles(url, subtitle_dir):
                raise SubtitleUnavailableError("mock subtitles unavailable")

            def download_audio(url, output_dir, **kwargs):
                seen["audio_url"] = url
                audio_path = output_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {
                    "audio_path": str(audio_path),
                    "method": "yt_dlp",
                    "cookies_source": "browser:safari",
                }

            deps = PipelineDependencies(
                resolve_url_func=lambda url: resolution,
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=lambda audio_path, transcript_dir, language="": fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://www.xiaohongshu.com/board/mock",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertIn("xsec_token=SECRET_XSEC", seen["audio_url"])
            item_manifest_path = Path(manifest["items"][0]["item_manifest_path"])
            item_manifest_text = item_manifest_path.read_text(encoding="utf-8")
            self.assertNotIn("SECRET_XSEC", item_manifest_text)
            item_manifest = json.loads(item_manifest_text)
            self.assertEqual(item_manifest["url"], "https://www.xiaohongshu.com/explore/video-note-1")
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(audio_steps[0]["source_url_source"], "resolver_download_url")
            self.assertEqual(audio_steps[0]["source_url_type"], "note_url_with_xsec")
            self.assertTrue(audio_steps[0]["download_url_available"])

    def test_xiaohongshu_board_video_prefers_media_url_without_manifest_leak(self) -> None:
        seen: dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            resolution = xiaohongshu_board_resolution()
            resolution["videos"] = [
                {
                    **resolution["videos"][0],
                    "url": "https://www.xiaohongshu.com/explore/video-note-1",
                    "_download_url": "https://www.xiaohongshu.com/explore/video-note-1?xsec_token=SECRET_XSEC",
                    "_media_url": "https://media.example.com/video.mp4?token=SECRET_MEDIA",
                }
            ]
            resolution["note_count"] = 1
            resolution["video_note_count"] = 1
            resolution["non_video_count"] = 0

            def fetch_subtitles(url, subtitle_dir):
                raise SubtitleUnavailableError("mock subtitles unavailable")

            def download_audio(url, output_dir, **kwargs):
                seen["audio_url"] = url
                audio_path = output_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {
                    "audio_path": str(audio_path),
                    "method": "yt_dlp",
                    "cookies_source": "browser:safari",
                }

            deps = PipelineDependencies(
                resolve_url_func=lambda url: resolution,
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=lambda audio_path, transcript_dir, language="": fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://www.xiaohongshu.com/board/mock",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertIn("SECRET_MEDIA", seen["audio_url"])
            item_manifest_text = Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8")
            self.assertNotIn("SECRET_MEDIA", item_manifest_text)
            self.assertNotIn("SECRET_XSEC", item_manifest_text)
            item_manifest = json.loads(item_manifest_text)
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(audio_steps[0]["source_url_source"], "resolver_media_url")
            self.assertEqual(audio_steps[0]["source_url_type"], "media_or_external_url_with_query")
            self.assertTrue(audio_steps[0]["media_url_available"])

    def test_xiaohongshu_board_audio_failure_refreshes_media_url_without_manifest_leak(self) -> None:
        seen: dict[str, Any] = {"audio_urls": []}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            resolution = xiaohongshu_board_resolution()
            resolution["videos"] = [
                {
                    **resolution["videos"][0],
                    "url": "https://www.xiaohongshu.com/explore/video-note-1",
                    "_download_url": "https://www.xiaohongshu.com/explore/video-note-1?xsec_token=SECRET_XSEC",
                }
            ]
            resolution["note_count"] = 1
            resolution["video_note_count"] = 1
            resolution["non_video_count"] = 0

            def fetch_subtitles(url, subtitle_dir):
                raise SubtitleUnavailableError("mock subtitles unavailable")

            def download_audio(url, output_dir, **kwargs):
                seen["audio_urls"].append(url)
                if "xsec_token=SECRET_XSEC" in url:
                    raise AudioDownloadError("mock no video formats")
                audio_path = output_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {"audio_path": str(audio_path), "method": "yt_dlp"}

            def refresh_media_url(note_url, **kwargs):
                seen["refresh_note_url"] = note_url
                return {
                    "media_url": "https://media.example.com/video.mp4?token=SECRET_MEDIA",
                    "provider": "safari-performance",
                    "media_url_available": True,
                }

            deps = PipelineDependencies(
                resolve_url_func=lambda url: resolution,
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=lambda audio_path, transcript_dir, language="": fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                media_url_refresh_func=refresh_media_url,
            )

            manifest = process_source(
                "https://www.xiaohongshu.com/board/mock",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(seen["audio_urls"][0], "https://www.xiaohongshu.com/explore/video-note-1?xsec_token=SECRET_XSEC")
            self.assertEqual(seen["audio_urls"][1], "https://media.example.com/video.mp4?token=SECRET_MEDIA")
            self.assertEqual(seen["refresh_note_url"], "https://www.xiaohongshu.com/explore/video-note-1?xsec_token=SECRET_XSEC")
            item_manifest_text = Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8")
            self.assertNotIn("SECRET_MEDIA", item_manifest_text)
            self.assertNotIn("SECRET_XSEC", item_manifest_text)
            item_manifest = json.loads(item_manifest_text)
            refresh_steps = [step for step in item_manifest["steps"] if step["step"] == "media_url_refresh"]
            self.assertEqual(refresh_steps[0]["status"], "completed")
            self.assertTrue(refresh_steps[0]["media_url_available"])
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(audio_steps[0]["source_url_source"], "refreshed_media_url")
            self.assertEqual(audio_steps[0]["source_url_type"], "media_or_external_url_with_query")

    def test_xiaohongshu_board_all_non_video_notes_creates_watch_order_without_video_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            resolution = xiaohongshu_board_resolution()
            resolution["videos"] = [resolution["videos"][1]]
            resolution["note_count"] = 1
            resolution["video_note_count"] = 0
            resolution["non_video_count"] = 1

            def fail_if_called(*args, **kwargs):
                raise AssertionError("all non-video board must not enter video pipeline")

            deps = PipelineDependencies(
                resolve_url_func=lambda url: resolution,
                fetch_subtitles_func=fail_if_called,
                download_audio_func=fail_if_called,
                build_local_extract_func=fail_if_called,
            )

            manifest = process_source(
                "https://www.xiaohongshu.com/board/mock",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 0)
            self.assertEqual(manifest["failed_count"], 0)
            self.assertEqual(manifest["skipped_count"], 1)
            self.assertEqual(len(list(output_dir.glob("*.html"))), 1)
            self.assertTrue((output_dir / "00-watch-order.html").exists())

    def test_single_item_failure_does_not_interrupt_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fetch_subtitles(url, subtitle_dir):
                if "heartflow" in url:
                    raise SubtitleUnavailableError("mock subtitles unavailable")
                return fixture_material()

            def download_audio(url, output_dir, *, subtitle_checked, subtitle_available):
                raise AudioDownloadError("mock audio failed")

            deps = PipelineDependencies(
                resolve_url_func=lambda url: list_resolution(),
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/list/mock",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(manifest["failed_count"], 1)
            self.assertEqual(manifest["items"][0]["status"], "failed")
            self.assertEqual(manifest["items"][0]["error"]["reason_code"], "audio_download_failed")
            self.assertEqual(manifest["items"][1]["status"], "completed")
            self.assertTrue(Path(manifest["items"][1]["html_path"]).exists())

    def test_audio_download_options_and_method_are_written_to_item_manifest(self) -> None:
        seen_kwargs: dict = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fetch_subtitles(url, subtitle_dir):
                raise SubtitleUnavailableError("mock subtitles unavailable")

            def download_audio(url, output_dir, **kwargs):
                seen_kwargs.update(kwargs)
                audio_path = output_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {"audio_path": str(audio_path), "method": "bilibili_playinfo_fallback"}

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=lambda audio_path, transcript_dir, language="": fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                audio_download_options={"cookies_from_browser": "chrome"},
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(seen_kwargs["cookies_from_browser"], "chrome")
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(audio_steps[0]["method"], "bilibili_playinfo_fallback")
            self.assertFalse(audio_steps[0]["audio_downloader_skipped_due_to_subtitle"])
            transcriber_steps = [step for step in item_manifest["steps"] if step["step"] == "transcriber"]
            self.assertIn("sanitization", transcriber_steps[0])
            self.assertIn("dropped_segment_count", transcriber_steps[0]["sanitization"])

    def test_chrome_cookies_reach_resolver_subtitle_and_audio_fallback_without_leaking_values(self) -> None:
        seen: dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            secret_cookie = "SESSDATA=SECRET_COOKIE_VALUE"

            def resolve(url, **kwargs):
                seen["resolver"] = dict(kwargs)
                return single_resolution()

            def fetch_subtitles(url, subtitle_dir, **kwargs):
                seen["subtitle"] = dict(kwargs)
                raise SubtitleUnavailableError("mock subtitles unavailable")

            def download_audio(url, output_dir, **kwargs):
                seen["audio"] = dict(kwargs)
                audio_path = output_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {"audio_path": str(audio_path), "method": "yt_dlp", "cookies_source": "browser:chrome"}

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                resolver_options={"cookies_from_browser": "chrome", "allow_browser_auth": True},
                fetch_subtitles_func=fetch_subtitles,
                subtitle_options={"cookies_from_browser": "chrome"},
                download_audio_func=download_audio,
                audio_download_options={"cookies_from_browser": "chrome", "cookie_header": secret_cookie},
                transcribe_func=lambda audio_path, transcript_dir, language="": fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(seen["resolver"]["cookies_from_browser"], "chrome")
            self.assertEqual(seen["subtitle"]["cookies_from_browser"], "chrome")
            self.assertEqual(seen["audio"]["cookies_from_browser"], "chrome")
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            self.assertNotIn("SECRET_COOKIE_VALUE", json.dumps(item_manifest, ensure_ascii=False))
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(audio_steps[0]["cookies_source"], "browser:chrome")

    def test_default_cookie_browser_fallback_is_recorded_in_item_manifest(self) -> None:
        seen: dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def resolve(url, **kwargs):
                seen["resolver"] = dict(kwargs)
                return {
                    **single_resolution(),
                    "videos": [
                        {
                            **single_resolution()["videos"][0],
                            "resolver_debug": {
                                "resolver_method": "yt_dlp",
                                "cookies_source": "browser:safari",
                                "attempted_cookie_sources": ["browser:chrome", "browser:safari"],
                                "cookies_browser_attempts": ["chrome", "safari"],
                                "selected_cookies_browser": "safari",
                                "cookies_fallback_reason": "extracted_0_cookies",
                            },
                        }
                    ],
                }

            def fetch_subtitles(url, subtitle_dir, **kwargs):
                seen["subtitle"] = dict(kwargs)
                raise SubtitleUnavailableError("mock subtitles unavailable")

            def download_audio(url, output_dir, **kwargs):
                seen["audio"] = dict(kwargs)
                audio_path = output_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {
                    "audio_path": str(audio_path),
                    "method": "yt_dlp",
                    "cookies_source": "browser:safari",
                    "cookies_browser_attempts": ["chrome", "safari"],
                    "selected_cookies_browser": "safari",
                    "cookies_fallback_reason": "platform_restriction",
                }

            deps = PipelineDependencies(
                resolve_url_func=resolve,
                resolver_options={"cookie_browser_attempts": ["chrome", "safari"], "allow_browser_auth": True},
                fetch_subtitles_func=fetch_subtitles,
                subtitle_options={"cookie_browser_attempts": ["chrome", "safari"]},
                download_audio_func=download_audio,
                audio_download_options={"cookie_browser_attempts": ["chrome", "safari"]},
                transcribe_func=lambda audio_path, transcript_dir, language="": fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(seen["resolver"]["cookie_browser_attempts"], ["chrome", "safari"])
            self.assertEqual(seen["subtitle"]["cookie_browser_attempts"], ["chrome", "safari"])
            self.assertEqual(seen["audio"]["cookie_browser_attempts"], ["chrome", "safari"])
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            resolver_steps = [step for step in item_manifest["steps"] if step["step"] == "resolver"]
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(resolver_steps[0]["selected_cookies_browser"], "safari")
            self.assertEqual(resolver_steps[0]["cookies_fallback_reason"], "extracted_0_cookies")
            self.assertEqual(audio_steps[0]["selected_cookies_browser"], "safari")
            self.assertEqual(audio_steps[0]["cookies_fallback_reason"], "platform_restriction")

    def test_bilibili_resolver_metadata_is_passed_to_audio_downloader_and_manifest(self) -> None:
        seen_kwargs: dict = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fetch_subtitles(url, subtitle_dir):
                raise SubtitleUnavailableError("mock subtitles unavailable")

            def download_audio(url, output_dir, **kwargs):
                seen_kwargs.update(kwargs)
                audio_path = output_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {
                    "audio_path": str(audio_path),
                    "method": "bilibili_playurl_api",
                    "cookies_source": "browser:chrome",
                    "audio_url_source": "dash.audio[0].baseUrl",
                    "fallback_success": True,
                    "playurl_status": "ok",
                }

            deps = PipelineDependencies(
                resolve_url_func=lambda url: bilibili_resolution(),
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=lambda audio_path, transcript_dir, language="": fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                audio_download_options={"cookies_from_browser": "chrome"},
            )

            manifest = process_source(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(seen_kwargs["bvid"], "BV1xx411c7mD")
            self.assertEqual(seen_kwargs["cid"], "12345")
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(audio_steps[0]["method"], "bilibili_playurl_api")
            self.assertEqual(audio_steps[0]["cookies_source"], "browser:chrome")
            self.assertEqual(audio_steps[0]["audio_url_source"], "dash.audio[0].baseUrl")
            self.assertTrue(audio_steps[0]["fallback_success"])
            local_steps = [step for step in item_manifest["steps"] if step["step"] == "local_extract"]
            self.assertRegex(local_steps[0]["transcript_hash"], r"^[0-9a-f]{64}$")
            self.assertEqual(local_steps[-1]["qwen_prompt_version"], "watchbrief_v5.qwen_local_extract_prompt.v2")
            review_steps = [step for step in item_manifest["steps"] if step["step"] == "codex_review_request"]
            self.assertRegex(review_steps[0]["transcript_hash"], r"^[0-9a-f]{64}$")
            self.assertEqual(review_steps[0]["codex_prompt_version"], "watchbrief_v5.codex_review_prompt.v5")
            self.assertEqual(review_steps[0]["scoring_formula_version"], "watchbrief_v5.video_value_formula.v2")
            self.assertEqual(review_steps[0]["watchbrief_version"], "watchbrief_v5")

    def test_bilibili_login_required_for_subtitle_falls_back_to_audio(self) -> None:
        seen_kwargs: dict = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fetch_subtitles(url, subtitle_dir):
                raise BilibiliSubtitleError(
                    LOGIN_REQUIRED_FOR_SUBTITLE,
                    "Bilibili subtitles require login",
                    ["bilibili-content-provider", url],
                    debug={
                        "provider": "bilibili_content_provider",
                        "need_login_subtitle": True,
                        "audio_fallback_allowed": True,
                        "audio_downloader_skipped_due_to_subtitle": False,
                    },
                )

            def download_audio(url, output_dir, **kwargs):
                seen_kwargs.update(kwargs)
                audio_path = output_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {"audio_path": str(audio_path), "method": "bilibili_playurl_api"}

            deps = PipelineDependencies(
                resolve_url_func=lambda url: bilibili_resolution(),
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=lambda audio_path, transcript_dir, language="": fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(seen_kwargs["subtitle_checked"], True)
            self.assertEqual(seen_kwargs["subtitle_available"], False)
            self.assertEqual(seen_kwargs["bvid"], "BV1xx411c7mD")
            self.assertEqual(seen_kwargs["cid"], "12345")
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            subtitle_steps = [step for step in item_manifest["steps"] if step["step"] == "subtitle_fetcher"]
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(subtitle_steps[-1]["status"], "unavailable")
            self.assertEqual(subtitle_steps[-1]["reason_code"], LOGIN_REQUIRED_FOR_SUBTITLE)
            self.assertTrue(subtitle_steps[-1]["audio_fallback_allowed"])
            self.assertEqual(audio_steps[-1]["status"], "completed")

    def test_bilibili_no_subtitle_available_is_the_audio_fallback_boundary(self) -> None:
        seen_kwargs: dict = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fetch_subtitles(url, subtitle_dir):
                raise SubtitleUnavailableError(
                    "Bilibili video has no subtitle tracks",
                    ["bilibili-content-provider", url],
                    reason_code=NO_SUBTITLE_AVAILABLE,
                    debug={
                        "provider": "bilibili_content_provider",
                        "audio_fallback_allowed": True,
                        "audio_downloader_skipped_due_to_subtitle": False,
                    },
                )

            def download_audio(url, output_dir, **kwargs):
                seen_kwargs.update(kwargs)
                audio_path = output_dir / "mock.wav"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"RIFFmock")
                return {"audio_path": str(audio_path), "method": "bilibili_playurl_api"}

            deps = PipelineDependencies(
                resolve_url_func=lambda url: bilibili_resolution(),
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                transcribe_func=lambda audio_path, transcript_dir, language="": fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(seen_kwargs["subtitle_checked"], True)
            self.assertEqual(seen_kwargs["subtitle_available"], False)
            self.assertEqual(seen_kwargs["bvid"], "BV1xx411c7mD")
            self.assertEqual(seen_kwargs["cid"], "12345")

    def test_separate_debug_dir_keeps_delivery_dir_free_of_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "delivery"
            debug_dir = root / "debug"
            work_dir = root / "work"

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
                work_dir=work_dir,
                debug_dir=debug_dir,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertTrue((output_dir / "01-如何把任务改造成更容易进入心流的状态.html").exists())
            self.assertFalse((output_dir / "payloads").exists())
            self.assertFalse((output_dir / "_work").exists())
            self.assertFalse((output_dir / "00-watch-order.html").exists())
            self.assertTrue((debug_dir / "manifest.json").exists())
            self.assertTrue(Path(manifest["items"][0]["payload_path"]).is_file())

    def test_bilibili_fallback_failure_method_is_written_to_item_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fetch_subtitles(url, subtitle_dir):
                raise SubtitleUnavailableError("mock subtitles unavailable")

            def download_audio(url, output_dir, **kwargs):
                raise BilibiliAudioDownloadError(
                    "bilibili_412_blocked",
                    "Bilibili returned HTTP 412 / risk-control block",
                    ["bilibili-playinfo-fallback", url],
                    "HTTP 412",
                )

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=fetch_subtitles,
                download_audio_func=download_audio,
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["failed_count"], 1)
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            audio_steps = [step for step in item_manifest["steps"] if step["step"] == "audio_downloader"]
            self.assertEqual(audio_steps[-1]["status"], "failed")
            self.assertEqual(audio_steps[-1]["method"], "bilibili_playinfo_fallback")
            self.assertEqual(audio_steps[-1]["reason_code"], "bilibili_412_blocked")

    def test_html_failure_does_not_cleanup_current_temp_files(self) -> None:
        cleanup_calls: list[Path] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fail_html(path: Path, html: str) -> None:
                raise OSError("mock html write failed")

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                write_html_func=fail_html,
                cleanup_func=lambda path: cleanup_calls.append(path),
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 0)
            self.assertEqual(manifest["failed_count"], 1)
            self.assertEqual(cleanup_calls, [])
            self.assertTrue((output_dir / "_work" / "01-如何把任务改造成更容易进入心流的状态").exists())
            self.assertFalse((output_dir / "01-如何把任务改造成更容易进入心流的状态.html").exists())

    def test_success_cleanup_runs_only_after_html_write(self) -> None:
        events: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            html_path = output_dir / "01-如何把任务改造成更容易进入心流的状态.html"

            def write_html(path: Path, html: str) -> None:
                events.append("write_html")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(html, encoding="utf-8")

            def cleanup(path: Path) -> None:
                events.append("cleanup")
                self.assertTrue(html_path.exists())
                shutil.rmtree(path, ignore_errors=True)

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                write_html_func=write_html,
                cleanup_func=cleanup,
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["completed_count"], 1)
            self.assertEqual(events[-2:], ["write_html", "cleanup"])

    def test_review_response_provider_is_required_and_no_model_call_is_attempted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                Path(temp_dir),
                review_response_provider=None,
                deps=deps,
            )

            self.assertEqual(manifest["failed_count"], 1)
            self.assertEqual(manifest["items"][0]["error"]["reason_code"], "review_response_unavailable")

    def test_local_extract_started_and_timeout_are_written_to_item_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def timeout_local_extract(metadata, transcript_segments, **kwargs):
                raise LocalQwenError("local_qwen_timeout", "Qwen request timed out after 600s")

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=timeout_local_extract,
                local_extract_options={
                    "qwen_model": "qwen-test",
                    "qwen_api_base": "http://127.0.0.1:1234/v1",
                    "qwen_timeout": 600,
                },
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["failed_count"], 1)
            self.assertEqual(manifest["items"][0]["error"]["stage"], "local_extract")
            self.assertEqual(manifest["items"][0]["error"]["reason_code"], "local_qwen_timeout")
            item_manifest_path = Path(manifest["items"][0]["item_manifest_path"])
            item_manifest = json.loads(item_manifest_path.read_text(encoding="utf-8"))
            local_steps = [step for step in item_manifest["steps"] if step["step"] == "local_extract"]
            self.assertEqual(local_steps[0]["status"], "started")
            self.assertEqual(local_steps[0]["qwen_model"], "qwen-test")
            self.assertEqual(local_steps[0]["qwen_base_url"], "http://127.0.0.1:1234/v1")
            self.assertEqual(local_steps[0]["qwen_api_base"], "http://127.0.0.1:1234/v1")
            self.assertEqual(local_steps[0]["timeout"], 600)
            self.assertEqual(local_steps[-1]["status"], "failed")
            self.assertEqual(local_steps[-1]["reason_code"], "local_qwen_timeout")
            self.assertTrue((item_manifest_path.parent / "local_extract_error.json").exists())

    def test_chunked_local_extract_debug_is_written_to_item_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=chunked_local_extract_for_tests,
                local_extract_options={"qwen_timeout": 600},
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                output_dir,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["failed_count"], 0)
            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            local_steps = [step for step in item_manifest["steps"] if step["step"] == "local_extract"]
            completed = local_steps[-1]

            self.assertEqual(completed["status"], "completed")
            self.assertTrue(completed["chunked"])
            self.assertGreater(completed["chunk_count"], 1)
            self.assertEqual(completed["reduce_status"], "completed")
            self.assertEqual(completed["failed_chunk_count"], 0)
            local_extract = json.loads(Path(manifest["items"][0]["payload_path"]).parent.joinpath("local_extract.json").read_text(encoding="utf-8"))
            self.assertTrue(local_extract["chunked_local_extract"]["enabled"])

    def test_outer_pipeline_does_not_wrap_local_qwen_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=lambda *args, **kwargs: (_ for _ in ()).throw(
                    LocalQwenError("local_qwen_timeout", "Qwen request timed out")
                ),
                local_extract_options={"qwen_timeout": 600},
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                Path(temp_dir),
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["failed_count"], 1)
            self.assertEqual(manifest["items"][0]["error"]["stage"], "local_extract")
            self.assertEqual(manifest["items"][0]["error"]["reason_code"], "local_qwen_timeout")
            self.assertNotEqual(manifest["items"][0]["error"]["reason_code"], "pipeline_failed")

    def test_local_extract_generic_error_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=lambda *args, **kwargs: (_ for _ in ()).throw(
                    LocalExtractError("mock local extract failed")
                ),
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                Path(temp_dir),
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(manifest["failed_count"], 1)
            self.assertEqual(manifest["items"][0]["error"]["stage"], "local_extract")
            self.assertEqual(manifest["items"][0]["error"]["reason_code"], "local_extract_failed")

    def test_report_cache_hit_reuses_payload_and_skips_review_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_dir = root / "cache"
            first_output = root / "first"
            second_output = root / "second"

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                review_options=report_cache_review_options(cache_dir),
            )

            first = process_source(
                "https://example.com/watch/heartflow",
                first_output,
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            self.assertEqual(first["completed_count"], 1)
            first_payload = json.loads(Path(first["items"][0]["payload_path"]).read_text(encoding="utf-8"))

            def fail_if_called(_item, _request, _extract):
                raise AssertionError("Codex review provider must not be called on cache hit")

            second = process_source(
                "https://example.com/watch/heartflow",
                second_output,
                review_response_provider=fail_if_called,
                deps=deps,
            )

            self.assertEqual(second["completed_count"], 1)
            second_payload_path = Path(second["items"][0]["payload_path"])
            second_payload = json.loads(second_payload_path.read_text(encoding="utf-8"))
            self.assertEqual(second_payload, first_payload)
            self.assertEqual(second_payload["replacement_score"], first_payload["replacement_score"])
            item_manifest = json.loads(Path(second["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            self.assertTrue(item_manifest["cache_hit"])
            self.assertRegex(item_manifest["cache_key"], r"^[0-9a-f]{64}$")
            self.assertTrue(Path(item_manifest["cached_payload_path"]).exists())
            cache_steps = [step for step in item_manifest["steps"] if step["step"] == "report_cache"]
            self.assertEqual(cache_steps[-1]["status"], "hit")
            review_steps = [step for step in item_manifest["steps"] if step["step"] == "codex_review"]
            self.assertEqual(review_steps[-1]["status"], "skipped")

    def test_force_reanalysis_bypasses_report_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_dir = root / "cache"
            calls: list[str] = []

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                review_options=report_cache_review_options(cache_dir),
            )
            process_source(
                "https://example.com/watch/heartflow",
                root / "first",
                review_response_provider=review_provider_for_golden,
                deps=deps,
            )

            force_deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                review_options=report_cache_review_options(cache_dir, force_reanalysis=True),
            )

            def provider(item, review_request, local_extract):
                calls.append("called")
                return review_provider_for_golden(item, review_request, local_extract)

            second = process_source(
                "https://example.com/watch/heartflow",
                root / "second",
                review_response_provider=provider,
                deps=force_deps,
            )

            self.assertEqual(second["completed_count"], 1)
            self.assertEqual(calls, ["called"])
            item_manifest = json.loads(Path(second["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            cache_steps = [step for step in item_manifest["steps"] if step["step"] == "report_cache"]
            self.assertEqual(cache_steps[0]["status"], "bypassed")

    def test_report_cache_key_changes_when_required_fingerprints_change(self) -> None:
        material = fixture_material()
        local_input = {
            "metadata": single_resolution()["videos"][0],
            "segments": material["segments"],
        }
        request = build_review_request(local_extract_for_tests(local_input["metadata"], local_input["segments"]))
        base = build_report_cache_identity(request, codex_model="gpt-test")
        base_key = report_cache_key(base)

        changed_hash = dict(base)
        changed_hash["transcript_hash"] = "0" * 64
        changed_prompt = dict(base)
        changed_prompt["codex_prompt_fingerprint"] = "1" * 64
        changed_formula = dict(base)
        changed_formula["scoring_formula_version"] = "watchbrief_v5.scoring_formula.v2"
        changed_source = dict(base)
        changed_source["source_url"] = "https://example.com/watch/other"

        self.assertNotEqual(report_cache_key(changed_hash), base_key)
        self.assertNotEqual(report_cache_key(changed_prompt), base_key)
        self.assertNotEqual(report_cache_key(changed_formula), base_key)
        self.assertNotEqual(report_cache_key(changed_source), base_key)

    def test_report_cache_key_changes_when_report_target_changes(self) -> None:
        material = fixture_material()
        metadata = single_resolution()["videos"][0]
        extract = local_extract_for_tests(metadata, material["segments"])
        watch_request = build_review_request(extract)
        notes_request = build_review_request(extract, report_target="knowledge_notes")

        watch_identity = build_report_cache_identity(watch_request, codex_model="gpt-test")
        notes_identity = build_report_cache_identity(notes_request, codex_model="gpt-test")

        self.assertEqual(watch_identity["report_target"], "watch_decision")
        self.assertEqual(notes_identity["report_target"], "knowledge_notes")
        self.assertNotEqual(report_cache_key(watch_identity), report_cache_key(notes_identity))

    def test_local_review_cache_identity_uses_local_review_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_dir = root / "cache"
            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                review_options=report_cache_review_options(cache_dir, codex_model=LOCAL_REVIEW_MODEL_ID),
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                root / "local-output",
                review_response_provider=lambda _item, _request, extract: {
                    **review_provider_for_golden(_item, _request, extract),
                    "codex_model": LOCAL_REVIEW_MODEL_ID,
                    "codex_prompt_fingerprint": LOCAL_REVIEW_PROMPT_FINGERPRINT,
                },
                deps=deps,
            )

            item_manifest = json.loads(Path(manifest["items"][0]["item_manifest_path"]).read_text(encoding="utf-8"))
            cache_payload = json.loads(Path(item_manifest["cached_payload_path"]).read_text(encoding="utf-8"))
            self.assertEqual(cache_payload["identity"]["codex_model"], LOCAL_REVIEW_MODEL_ID)
            self.assertEqual(cache_payload["identity"]["codex_prompt_fingerprint"], LOCAL_REVIEW_PROMPT_FINGERPRINT)

    def test_failed_payload_is_not_written_to_report_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_dir = root / "cache"

            def invalid_provider(_item, _request, _extract):
                payload = load_golden("sample_payload_heartflow.json")
                del payload["title"]
                return payload

            deps = PipelineDependencies(
                resolve_url_func=lambda url: single_resolution(),
                fetch_subtitles_func=lambda url, subtitle_dir: fixture_material(),
                build_local_extract_func=local_extract_for_tests,
                review_options=report_cache_review_options(cache_dir),
            )

            manifest = process_source(
                "https://example.com/watch/heartflow",
                root / "output",
                review_response_provider=invalid_provider,
                deps=deps,
            )

            self.assertEqual(manifest["failed_count"], 1)
            self.assertEqual(list(cache_dir.glob("*.json")) if cache_dir.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
