from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, load_golden
from scripts.analyzer.local_extract import (
    DEFAULT_QWEN_MODEL,
    FINAL_REPORT_FIELDS_BLOCKED_IN_LOCAL_EXTRACT,
    LocalExtractError,
    LocalQwenError,
    build_qwen_chat_request,
    build_qwen_chunk_extract_user_prompt,
    build_qwen_chunk_reduce_user_prompt,
    build_local_extract_payload,
    build_qwen_local_extract_user_prompt,
    call_qwen_prompt_json,
    choose_qwen_model,
    configured_qwen_api_base,
    configured_qwen_model,
    is_qwen_model_id,
)
from scripts.validator import validate_normalized_report_payload


def fake_qwen_extract(seed: dict) -> dict:
    return {
        "cleaned_understanding": "转写内容主要在解释如何用任务边界和反馈机制降低进入心流的阻力。",
        "main_axis": "把心流从意志力问题改写成任务设计问题。",
        "core_claims": ["心流依赖清楚目标、即时反馈和适度挑战。"],
        "conditions": ["任务入口足够小", "反馈足够及时"],
        "methods": ["先拆小任务", "限制干扰", "设置短时挑战"],
        "examples": ["作者用写作任务说明如何降低启动阻力。"],
        "caveats": ["如果任务本身缺少反馈，方法效果会变弱。"],
        "original_quotes": ["不要先责怪自己不专注。"],
        "refined_quotes": ["专注不是硬拧出来的，而是被任务结构诱发出来的。"],
        "transcript_quality_note": "转写清晰，不影响主旨判断。",
        "language": seed["transcript"]["language"],
        "important_terms": ["flow"],
        "corrected_terms": [],
    }


def make_long_segments(count: int = 12) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    for index in range(count):
        segments.append({
            "start": f"00:{index:02d}",
            "end": f"00:{index + 1:02d}",
            "text": f"第 {index} 段说明产品构建、用户验证、销售和复盘。" + (" this is detailed evidence" * 8),
        })
    return segments


def fake_chunk_summary(seed: dict) -> dict:
    chunk = seed["chunk"]
    return {
        "chunk_start": chunk["start"],
        "chunk_end": chunk["end"],
        "core_theme": f"第 {chunk['index']} 块讲产品验证。",
        "useful_points": [f"{chunk['start']} | {chunk['end']}：这一块有可取观点。"],
        "skippable_content": ["重复铺垫可以跳过。"],
        "candidate_watch_segments": [
            {"start": chunk["start"], "end": chunk["end"], "reason": "覆盖关键论证。"}
        ],
        "information_density": "中：包含方法和例子。",
        "scoring_evidence": ["有方法论证据。"],
        "transcript_quality_note": "转写可读。",
        "language": seed["transcript"]["language"],
        "important_terms": ["product"],
        "corrected_terms": [],
    }


def chunked_qwen_extract(calls: list[dict]):
    def extractor(seed: dict) -> dict:
        calls.append(seed)
        if seed.get("local_extract_mode") == "chunk":
            return fake_chunk_summary(seed)
        if seed.get("local_extract_mode") == "chunk_reduce":
            data = fake_qwen_extract(seed)
            data["main_axis"] = "把各块产品构建建议归并成可验证的产品路线。"
            data["core_claims"] = [item["core_theme"] for item in seed["chunk_summaries"]]
            return data
        return fake_qwen_extract(seed)

    return extractor


class MockHTTPResponse:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.payload.encode("utf-8")


def qwen_chat_response(content: str) -> str:
    return json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False)


def minimal_chunk_summary_json() -> str:
    return json.dumps({
        "chunk_start": "00:00",
        "chunk_end": "00:10",
        "core_theme": "这一段讲产品验证。",
        "useful_points": ["用小产品验证需求。"],
        "skippable_content": [],
        "candidate_watch_segments": ["00:00 | 00:10：保留关键论证。"],
        "information_density": "中：包含方法。",
        "scoring_evidence": ["有方法和例子。"],
        "transcript_quality_note": "转写可读。",
        "language": "en",
        "important_terms": [],
        "corrected_terms": [],
    }, ensure_ascii=False)


class AnalyzerLocalExtractTest(unittest.TestCase):
    def load_mock(self) -> dict:
        return json.loads((ROOT / "golden" / "mock_transcript_heartflow.json").read_text(encoding="utf-8"))

    def test_build_local_extract_from_mock_transcript(self) -> None:
        mock = self.load_mock()
        payload = build_local_extract_payload(
            mock["metadata"],
            mock["transcript_segments"],
            transcript_language=mock["transcript_language"],
            transcript_source=mock["transcript_source"],
            qwen_extractor=fake_qwen_extract,
        )

        self.assertEqual(payload["extract_version"], "watchbrief_v5.local_extract.v1")
        self.assertEqual(payload["metadata"]["title"], mock["metadata"]["title"])
        self.assertEqual(payload["transcript"]["segment_count"], 6)
        self.assertTrue(payload["transcript"]["has_timestamps"])
        self.assertIn("00:00 | 01:58", payload["transcript"]["excerpt"])
        self.assertEqual(payload["time_windows"][0]["start"], "00:00")
        self.assertEqual(payload["time_windows"][0]["end"], "05:12")
        self.assertEqual(payload["time_windows"][1]["start"], "10:51")
        self.assertEqual(payload["time_windows"][1]["end"], "15:08")
        self.assertTrue(payload["analysis_boundary"]["local_qwen_model_call"])
        self.assertEqual(payload["qwen_extract"]["main_axis"], "把心流从意志力问题改写成任务设计问题。")
        self.assertTrue(payload["analysis_boundary"]["no_real_url_processing"])
        self.assertRegex(payload["transcript_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(payload["analysis_boundary"]["qwen_prompt_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["qwen_extract"]["_qwen_model"], DEFAULT_QWEN_MODEL)
        self.assertNotIn("chunked_local_extract", payload)

    def test_local_extract_does_not_generate_final_report_fields(self) -> None:
        mock = self.load_mock()
        payload = build_local_extract_payload(mock["metadata"], mock["transcript_segments"], qwen_extractor=fake_qwen_extract)
        for field in FINAL_REPORT_FIELDS_BLOCKED_IN_LOCAL_EXTRACT:
            self.assertNotIn(field, payload)
            self.assertNotIn(field, payload["qwen_extract"])

    def test_english_prompt_requires_domain_term_chineseization(self) -> None:
        seed = {
            "metadata": {
                "title": "A Full Guide To Making Your First Profitable Product",
                "channel": "Dan Koe",
            },
            "transcript": {
                "language": "en",
                "segments": [
                    {"start": "00:00", "end": "00:03", "text": "Build a personal system product and landing page."}
                ],
            },
        }

        prompt = build_qwen_local_extract_user_prompt(seed)

        self.assertIn("最终报告必须以中文为主", prompt)
        self.assertIn("领域术语要自然中文化", prompt)
        self.assertIn("personal system product -> 个人方法系统产品", prompt)
        self.assertIn("landing page -> 落地页", prompt)
        self.assertIn("offer -> 产品承诺 / 销售主张", prompt)
        self.assertIn("标题、频道名、品牌名、产品名、人名、工具名", prompt)
        self.assertIn("中文（English）", prompt)
        self.assertNotIn("重要英文术语保留英文原词", prompt)

    def test_chunk_prompts_require_domain_term_chineseization(self) -> None:
        chunk_seed = {
            "local_extract_mode": "chunk",
            "transcript": {"language": "en"},
            "chunk": {
                "index": 1,
                "start": "00:00",
                "end": "00:30",
                "segments": [
                    {"start": "00:00", "end": "00:03", "text": "Offer, CTA, and social proof matter."}
                ],
            },
        }
        reduce_seed = {
            "local_extract_mode": "chunk_reduce",
            "transcript": {"language": "en"},
            "chunk_summaries": [
                {"core_theme": "Offer and CTA", "important_terms": ["offer", "CTA"]}
            ],
        }

        chunk_prompt = build_qwen_chunk_extract_user_prompt(chunk_seed)
        reduce_prompt = build_qwen_chunk_reduce_user_prompt(reduce_seed)

        for prompt in (chunk_prompt, reduce_prompt):
            self.assertIn("领域术语要自然中文化", prompt)
            self.assertIn("CTA -> 行动号召 / 行动按钮", prompt)
            self.assertIn("social proof -> 信任背书 / 社会证明", prompt)
            self.assertIn("中文（English）", prompt)
            self.assertNotIn("重要英文术语保留英文原词", prompt)

    def test_missing_metadata_fails(self) -> None:
        mock = self.load_mock()
        metadata = dict(mock["metadata"])
        del metadata["title"]
        with self.assertRaises(LocalExtractError):
            build_local_extract_payload(metadata, mock["transcript_segments"], qwen_extractor=fake_qwen_extract)

    def test_missing_segment_timestamp_fails(self) -> None:
        mock = self.load_mock()
        transcript_segments = list(mock["transcript_segments"])
        transcript_segments[0] = dict(transcript_segments[0])
        del transcript_segments[0]["start"]
        with self.assertRaises(LocalExtractError):
            build_local_extract_payload(mock["metadata"], transcript_segments, qwen_extractor=fake_qwen_extract)

    def test_rejects_non_qwen_model(self) -> None:
        with self.assertRaises(LocalQwenError) as context:
            choose_qwen_model(model_id="llama-3.1")
        self.assertEqual(context.exception.reason_code, "non_qwen_model_rejected")

    def test_qwen_model_defaults_to_g2_1_model(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(configured_qwen_model(), DEFAULT_QWEN_MODEL)
            self.assertEqual(choose_qwen_model(), DEFAULT_QWEN_MODEL)

    def test_qwen_model_can_come_from_environment(self) -> None:
        with mock.patch.dict(os.environ, {"WATCHBRIEF_QWEN_MODEL": "my-qwen-local"}, clear=False):
            self.assertEqual(choose_qwen_model(), "my-qwen-local")

    def test_qwen_base_url_env_takes_priority(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "WATCHBRIEF_QWEN_BASE_URL": "http://127.0.0.1:1235/v1/",
                "WATCHBRIEF_QWEN_API_BASE": "http://127.0.0.1:1234/v1",
            },
            clear=False,
        ):
            self.assertEqual(configured_qwen_api_base("http://config.example/v1"), "http://127.0.0.1:1235/v1")

    def test_qwen_api_base_env_remains_supported(self) -> None:
        with mock.patch.dict(os.environ, {"WATCHBRIEF_QWEN_API_BASE": "http://127.0.0.1:1234/v1/"}, clear=True):
            self.assertEqual(configured_qwen_api_base(), "http://127.0.0.1:1234/v1")

    def test_qwen_model_id_accepts_qwen_family(self) -> None:
        self.assertTrue(is_qwen_model_id("Qwen3-32B-Instruct"))
        self.assertTrue(is_qwen_model_id("custom-qwen-local"))

    def test_qwen_request_disables_reasoning_for_lm_studio_content_output(self) -> None:
        request = build_qwen_chat_request(DEFAULT_QWEN_MODEL, "输出 JSON")

        self.assertEqual(request["reasoning_effort"], "none")
        self.assertFalse(request["stream"])

    def test_english_transcript_prompt_does_not_translate_first(self) -> None:
        mock_payload = {
            "transcript": {
                "language": "en",
                "excerpt": "00:00 | 00:05 The product is called Linear.",
            }
        }
        prompt = build_qwen_local_extract_user_prompt(mock_payload)

        self.assertIn("Read the original English transcript directly", prompt)
        self.assertIn("Do not first translate the whole transcript", prompt)
        self.assertIn("输出中文结构化提炼", prompt)

    def test_mixed_transcript_keeps_important_english_terms(self) -> None:
        mock = self.load_mock()
        segments = [{"start": "00:00", "end": "00:05", "text": "这个方法叫 Flow state，不是普通放松。"}]
        payload = build_local_extract_payload(
            mock["metadata"],
            segments,
            transcript_language="mixed",
            transcript_source="mock",
            qwen_extractor=fake_qwen_extract,
        )

        self.assertEqual(payload["transcript"]["language"], "mixed")
        self.assertIn("flow", [term.lower() for term in payload["qwen_extract"]["important_terms"]])

    def test_qwen_intermediate_json_is_not_renderer_payload(self) -> None:
        mock = self.load_mock()
        payload = build_local_extract_payload(mock["metadata"], mock["transcript_segments"], qwen_extractor=fake_qwen_extract)

        self.assertIn("qwen_extract", payload)
        self.assertNotIn("replacement_score", payload["qwen_extract"])
        self.assertNotIn("final_conclusion", payload["qwen_extract"])

    def test_entity_normalization_terms_are_preserved(self) -> None:
        mock = self.load_mock()

        def entity_qwen_extract(seed: dict) -> dict:
            data = fake_qwen_extract(seed)
            data["important_terms"] = ["叔本华 / Schopenhauer", "尼采 / Nietzsche", "阿兰·德波顿 / Alain de Botton"]
            data["corrected_terms"] = ["叔奔华 -> 叔本华 / Schopenhauer"]
            return data

        payload = build_local_extract_payload(
            mock["metadata"],
            mock["transcript_segments"],
            qwen_extractor=entity_qwen_extract,
        )

        self.assertIn("叔本华 / Schopenhauer", payload["qwen_extract"]["important_terms"])
        self.assertIn("叔奔华 -> 叔本华 / Schopenhauer", payload["qwen_extract"]["corrected_terms"])

    def test_entity_normalization_covers_fixed_philosopher_names(self) -> None:
        mock = self.load_mock()

        def entity_qwen_extract(seed: dict) -> dict:
            data = fake_qwen_extract(seed)
            data["important_terms"] = ["叔奔华", "尼彩", "柏腊图", "沙特", "阿兰德波东"]
            data["corrected_terms"] = []
            return data

        payload = build_local_extract_payload(
            mock["metadata"],
            mock["transcript_segments"],
            qwen_extractor=entity_qwen_extract,
        )

        self.assertEqual(
            payload["qwen_extract"]["important_terms"],
            [
                "叔本华 / Schopenhauer",
                "尼采 / Nietzsche",
                "柏拉图 / Plato",
                "萨特 / Sartre",
                "阿兰·德波顿 / Alain de Botton",
            ],
        )
        self.assertIn("阿兰德波东 -> 阿兰·德波顿 / Alain de Botton", payload["qwen_extract"]["corrected_terms"])

    def test_qwen_http_timeout_returns_local_qwen_timeout(self) -> None:
        mock = self.load_mock()

        def timeout_urlopen(request, timeout):
            self.assertEqual(timeout, 9)
            raise TimeoutError("timed out")

        with self.assertRaises(LocalQwenError) as context:
            build_local_extract_payload(
                mock["metadata"],
                mock["transcript_segments"],
                qwen_model="qwen-test",
                qwen_timeout=9,
                urlopen_func=timeout_urlopen,
            )

        self.assertEqual(context.exception.reason_code, "local_qwen_timeout")

    def test_qwen_bad_json_returns_local_qwen_invalid_response(self) -> None:
        mock = self.load_mock()

        def bad_json_urlopen(request, timeout):
            return MockHTTPResponse("<html>not json</html>")

        with self.assertRaises(LocalQwenError) as context:
            build_local_extract_payload(
                mock["metadata"],
                mock["transcript_segments"],
                qwen_model="qwen-test",
                qwen_timeout=5,
                urlopen_func=bad_json_urlopen,
            )

        self.assertEqual(context.exception.reason_code, "local_qwen_invalid_response")

    def test_qwen_prompt_json_accepts_pure_json(self) -> None:
        def urlopen_func(request, timeout):
            return MockHTTPResponse(qwen_chat_response('{"ok": true}'))

        parsed = call_qwen_prompt_json(
            "输出 JSON",
            selected_model="qwen-test",
            api_base="http://127.0.0.1:1234/v1",
            urlopen_func=urlopen_func,
            timeout=5,
        )

        self.assertEqual(parsed, {"ok": True})

    def test_qwen_prompt_json_extracts_markdown_json_block(self) -> None:
        def urlopen_func(request, timeout):
            return MockHTTPResponse(qwen_chat_response('```json\n{"ok": true}\n```'))

        parsed = call_qwen_prompt_json(
            "输出 JSON",
            selected_model="qwen-test",
            api_base="http://127.0.0.1:1234/v1",
            urlopen_func=urlopen_func,
            timeout=5,
        )

        self.assertEqual(parsed, {"ok": True})

    def test_qwen_prompt_json_extracts_json_with_surrounding_text(self) -> None:
        def urlopen_func(request, timeout):
            return MockHTTPResponse(qwen_chat_response('下面是结果：\n{"ok": true}\n以上。'))

        parsed = call_qwen_prompt_json(
            "输出 JSON",
            selected_model="qwen-test",
            api_base="http://127.0.0.1:1234/v1",
            urlopen_func=urlopen_func,
            timeout=5,
        )

        self.assertEqual(parsed, {"ok": True})

    def test_qwen_prompt_json_repairs_bare_arrow_terms(self) -> None:
        def urlopen_func(request, timeout):
            return MockHTTPResponse(qwen_chat_response('{"corrected_terms": ["Marcus Aurelius" → "马可·奥勒留"]}'))

        parsed = call_qwen_prompt_json(
            "输出 JSON",
            selected_model="qwen-test",
            api_base="http://127.0.0.1:1234/v1",
            urlopen_func=urlopen_func,
            timeout=5,
        )

        self.assertEqual(parsed, {"corrected_terms": ["Marcus Aurelius -> 马可·奥勒留"]})

    def test_qwen_prompt_json_repairs_missing_commas_between_array_strings(self) -> None:
        def urlopen_func(request, timeout):
            return MockHTTPResponse(qwen_chat_response('{"refined_quotes": ["第一句"\n"第二句"]}'))

        parsed = call_qwen_prompt_json(
            "输出 JSON",
            selected_model="qwen-test",
            api_base="http://127.0.0.1:1234/v1",
            urlopen_func=urlopen_func,
            timeout=5,
        )

        self.assertEqual(parsed, {"refined_quotes": ["第一句", "第二句"]})

    def test_chunk_reduce_timeout_falls_back_to_deterministic_merge(self) -> None:
        mock = self.load_mock()

        def extractor(seed: dict) -> dict:
            if seed.get("local_extract_mode") == "chunk":
                return fake_chunk_summary(seed)
            if seed.get("local_extract_mode") == "chunk_reduce":
                raise LocalQwenError("local_qwen_timeout", "mock reduce timeout")
            return fake_qwen_extract(seed)

        payload = build_local_extract_payload(
            mock["metadata"],
            make_long_segments(),
            transcript_language="en",
            qwen_extractor=extractor,
            chunked_char_threshold=200,
            chunked_request_bytes_threshold=999_999,
            chunked_segment_threshold=999_999,
            chunk_target_chars=650,
            chunk_overlap_chars=120,
        )

        self.assertEqual(payload["chunked_local_extract"]["reduce"]["status"], "fallback_completed")
        self.assertEqual(payload["qwen_extract"]["_local_extract_mode"], "chunked_fallback")
        self.assertGreater(len(payload["qwen_extract"]["core_claims"]), 0)

    def test_qwen_invalid_intermediate_output_returns_local_extract_invalid_output(self) -> None:
        mock = self.load_mock()
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"cleaned_understanding": "字段不完整"}, ensure_ascii=False),
                    }
                }
            ]
        }

        def invalid_output_urlopen(request, timeout):
            return MockHTTPResponse(json.dumps(response, ensure_ascii=False))

        with self.assertRaises(LocalExtractError) as context:
            build_local_extract_payload(
                mock["metadata"],
                mock["transcript_segments"],
                qwen_model="qwen-test",
                qwen_timeout=5,
                urlopen_func=invalid_output_urlopen,
            )

        self.assertEqual(context.exception.reason_code, "local_extract_invalid_output")

    def test_short_transcript_still_uses_single_qwen_path(self) -> None:
        mock = self.load_mock()
        calls: list[dict] = []

        payload = build_local_extract_payload(
            mock["metadata"],
            mock["transcript_segments"],
            transcript_language=mock["transcript_language"],
            qwen_extractor=chunked_qwen_extract(calls),
        )

        self.assertEqual(len(calls), 1)
        self.assertNotIn("local_extract_mode", calls[0])
        self.assertNotIn("chunked_local_extract", payload)

    def test_long_transcript_triggers_chunked_mode_and_reduce(self) -> None:
        mock = self.load_mock()
        calls: list[dict] = []

        payload = build_local_extract_payload(
            mock["metadata"],
            make_long_segments(),
            transcript_language="en",
            qwen_extractor=chunked_qwen_extract(calls),
            chunked_char_threshold=200,
            chunked_request_bytes_threshold=999_999,
            chunked_segment_threshold=999_999,
            chunk_target_chars=650,
            chunk_overlap_chars=120,
        )
        chunk_calls = [seed for seed in calls if seed.get("local_extract_mode") == "chunk"]

        self.assertGreater(len(chunk_calls), 1)
        self.assertEqual(calls[-1]["local_extract_mode"], "chunk_reduce")
        self.assertTrue(payload["chunked_local_extract"]["enabled"])
        self.assertEqual(payload["chunked_local_extract"]["chunk_count"], len(chunk_calls))
        self.assertEqual(payload["chunked_local_extract"]["reduce"]["status"], "completed")
        self.assertEqual(payload["qwen_extract"]["_local_extract_mode"], "chunked")

    def test_chunk_order_and_overlap_are_preserved(self) -> None:
        mock = self.load_mock()
        calls: list[dict] = []

        payload = build_local_extract_payload(
            mock["metadata"],
            make_long_segments(),
            transcript_language="en",
            qwen_extractor=chunked_qwen_extract(calls),
            chunked_char_threshold=200,
            chunked_request_bytes_threshold=999_999,
            chunked_segment_threshold=999_999,
            chunk_target_chars=650,
            chunk_overlap_chars=120,
        )
        chunks = payload["chunked_local_extract"]["chunks"]
        starts = [chunk["start"] for chunk in chunks]
        input_starts = [chunk["input_start"] for chunk in chunks]

        self.assertEqual(starts, sorted(starts))
        self.assertEqual(input_starts[0], starts[0])
        self.assertLessEqual(input_starts[1], starts[1])
        self.assertGreater(chunks[1]["overlap_char_count"], 0)

    def test_chunk_timeout_does_not_fail_whole_item_when_coverage_is_enough(self) -> None:
        mock = self.load_mock()
        attempts: dict[int, int] = {}

        def extractor(seed: dict) -> dict:
            if seed.get("local_extract_mode") == "chunk":
                index = int(seed["chunk"]["index"])
                attempts[index] = attempts.get(index, 0) + 1
                if index == 2:
                    raise LocalQwenError("local_qwen_timeout", "mock timeout")
                return fake_chunk_summary(seed)
            if seed.get("local_extract_mode") == "chunk_reduce":
                return fake_qwen_extract(seed)
            return fake_qwen_extract(seed)

        payload = build_local_extract_payload(
            mock["metadata"],
            make_long_segments(),
            transcript_language="en",
            qwen_extractor=extractor,
            chunked_char_threshold=200,
            chunked_request_bytes_threshold=999_999,
            chunked_segment_threshold=999_999,
            chunk_target_chars=650,
            chunk_overlap_chars=120,
            chunk_max_retries=1,
            chunk_min_success_coverage=0.5,
        )

        self.assertEqual(attempts[2], 2)
        self.assertEqual(payload["chunked_local_extract"]["failed_chunk_count"], 1)
        self.assertEqual(payload["chunked_local_extract"]["reduce"]["status"], "completed")

    def test_chunk_non_json_triggers_repair_retry_and_counts_success(self) -> None:
        mock = self.load_mock()
        prompts: list[str] = []
        state = {"chunk_calls": 0}

        def urlopen_func(request, timeout):
            body = json.loads(request.data.decode("utf-8"))
            prompt = body["messages"][-1]["content"]
            prompts.append(prompt)
            if "上一轮 chunk_summary 输出不是合法 JSON" in prompt:
                return MockHTTPResponse(qwen_chat_response(minimal_chunk_summary_json()))
            if "转写 chunk" in prompt:
                state["chunk_calls"] += 1
                if state["chunk_calls"] == 1:
                    return MockHTTPResponse(qwen_chat_response("我会先解释一下，这不是 JSON"))
                return MockHTTPResponse(qwen_chat_response(minimal_chunk_summary_json()))
            if "chunk summaries" in prompt:
                return MockHTTPResponse(qwen_chat_response(json.dumps(fake_qwen_extract({"transcript": {"language": "en"}}), ensure_ascii=False)))
            raise AssertionError("unexpected prompt")

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_local_extract_payload(
                mock["metadata"],
                make_long_segments(),
                transcript_language="en",
                qwen_model="qwen-test",
                qwen_timeout=5,
                urlopen_func=urlopen_func,
                debug_dir=Path(temp_dir),
                chunked_char_threshold=200,
                chunked_request_bytes_threshold=999_999,
                chunked_segment_threshold=999_999,
                chunk_target_chars=650,
                chunk_overlap_chars=120,
            )
            plan = json.loads((Path(temp_dir) / "local_extract_chunked_plan.json").read_text(encoding="utf-8"))

        repaired = plan["chunks"][0]
        self.assertTrue(any("上一轮 chunk_summary 输出不是合法 JSON" in prompt for prompt in prompts))
        self.assertTrue(repaired["raw_response_was_non_json"])
        self.assertTrue(repaired["repair_attempted"])
        self.assertTrue(repaired["repair_success"])
        self.assertEqual(repaired["final_chunk_status"], "completed")
        self.assertEqual(payload["chunked_local_extract"]["failed_chunk_count"], 0)
        self.assertEqual(payload["chunked_local_extract"]["reduce"]["status"], "completed")

    def test_chunk_repair_failure_records_parse_error_but_reduce_can_continue(self) -> None:
        mock = self.load_mock()
        state = {"chunk_calls": 0}

        def urlopen_func(request, timeout):
            body = json.loads(request.data.decode("utf-8"))
            prompt = body["messages"][-1]["content"]
            if "上一轮 chunk_summary 输出不是合法 JSON" in prompt:
                return MockHTTPResponse(qwen_chat_response("仍然不是 JSON"))
            if "转写 chunk" in prompt:
                state["chunk_calls"] += 1
                if state["chunk_calls"] == 1:
                    return MockHTTPResponse(qwen_chat_response("这段输出缺少 JSON 对象"))
                return MockHTTPResponse(qwen_chat_response(minimal_chunk_summary_json()))
            if "chunk summaries" in prompt:
                return MockHTTPResponse(qwen_chat_response(json.dumps(fake_qwen_extract({"transcript": {"language": "en"}}), ensure_ascii=False)))
            raise AssertionError("unexpected prompt")

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_local_extract_payload(
                mock["metadata"],
                make_long_segments(),
                transcript_language="en",
                qwen_model="qwen-test",
                qwen_timeout=5,
                urlopen_func=urlopen_func,
                debug_dir=Path(temp_dir),
                chunked_char_threshold=200,
                chunked_request_bytes_threshold=999_999,
                chunked_segment_threshold=999_999,
                chunk_target_chars=650,
                chunk_overlap_chars=120,
                chunk_min_success_coverage=0.1,
            )
            plan = json.loads((Path(temp_dir) / "local_extract_chunked_plan.json").read_text(encoding="utf-8"))

        failed = plan["chunks"][0]
        self.assertTrue(failed["raw_response_was_non_json"])
        self.assertTrue(failed["repair_attempted"])
        self.assertFalse(failed["repair_success"])
        self.assertIn("Qwen response did not contain a JSON object", failed["parse_error"])
        self.assertEqual(failed["final_chunk_status"], "failed")
        self.assertEqual(payload["chunked_local_extract"]["failed_chunk_count"], 1)
        self.assertEqual(payload["chunked_local_extract"]["reduce"]["status"], "completed")

    def test_chunk_failures_fail_when_success_coverage_is_insufficient(self) -> None:
        mock = self.load_mock()

        def extractor(seed: dict) -> dict:
            if seed.get("local_extract_mode") == "chunk":
                index = int(seed["chunk"]["index"])
                if index > 1:
                    raise LocalQwenError("local_qwen_timeout", "mock timeout")
                return fake_chunk_summary(seed)
            return fake_qwen_extract(seed)

        with self.assertRaises(LocalQwenError) as context:
            build_local_extract_payload(
                mock["metadata"],
                make_long_segments(),
                transcript_language="en",
                qwen_extractor=extractor,
                chunked_char_threshold=200,
                chunked_request_bytes_threshold=999_999,
                chunked_segment_threshold=999_999,
                chunk_target_chars=650,
                chunk_overlap_chars=120,
                chunk_max_retries=1,
                chunk_min_success_coverage=0.9,
            )

        self.assertEqual(context.exception.reason_code, "chunked_local_extract_failed")

    def test_chunked_debug_plan_is_written(self) -> None:
        mock = self.load_mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_local_extract_payload(
                mock["metadata"],
                make_long_segments(),
                transcript_language="en",
                qwen_extractor=chunked_qwen_extract([]),
                debug_dir=Path(temp_dir),
                chunked_char_threshold=200,
                chunked_request_bytes_threshold=999_999,
                chunked_segment_threshold=999_999,
                chunk_target_chars=650,
                chunk_overlap_chars=120,
            )
            plan_path = Path(temp_dir) / "local_extract_chunked_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertTrue(plan["enabled"])
        self.assertEqual(plan["chunk_count"], payload["chunked_local_extract"]["chunk_count"])
        self.assertIn("input_char_count", plan["chunks"][0])
        self.assertEqual(plan["reduce"]["status"], "completed")

    def test_final_normalized_payload_schema_stays_unchanged(self) -> None:
        schema = json.loads((ROOT / "schemas" / "single_video_report.schema.json").read_text(encoding="utf-8"))
        golden = validate_normalized_report_payload(load_golden("sample_payload_heartflow.json"))
        primaries = [item for item in golden["watch_segments"] if item["priority"] == "primary"]

        self.assertNotIn("chunked_local_extract", schema["properties"])
        self.assertNotIn("chunk_summaries", schema["properties"])
        self.assertEqual(len(primaries), 1)
        self.assertIn(primaries[0]["start"], golden["only_one_segment"])
        self.assertIn(primaries[0]["end"], golden["only_one_segment"])


if __name__ == "__main__":
    unittest.main()
