from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from helpers import ROOT, load_golden
from scripts.analyzer.cloud_review import run_cloud_review
from scripts.analyzer.external_extract import build_external_extract_payload
from scripts.analyzer.local_extract import LocalQwenError


def extract_json() -> str:
    return json.dumps(
        {
            "cleaned_understanding": "这段内容说明如何用清楚目标、即时反馈和适度挑战进入心流。",
            "main_axis": "把心流从意志力问题改写成任务设计问题。",
            "core_claims": ["心流依赖清楚目标、即时反馈和适度挑战。"],
            "conditions": ["目标清楚", "反馈及时"],
            "methods": ["拆小任务", "减少干扰"],
            "examples": ["作者用写作任务说明如何降低启动阻力。"],
            "caveats": ["任务缺少反馈时效果会变弱。"],
            "original_quotes": ["不要先责怪自己不专注。"],
            "refined_quotes": ["专注不是硬拧出来的，而是被任务结构诱发出来的。"],
            "transcript_quality_note": "转写清晰。",
            "language": "zh",
            "important_terms": ["flow"],
            "corrected_terms": [],
        },
        ensure_ascii=False,
    )


class ExternalModelAdaptersTest(unittest.TestCase):
    def load_mock(self) -> dict:
        return json.loads((ROOT / "golden" / "mock_transcript_heartflow.json").read_text(encoding="utf-8"))

    def test_local_openai_compatible_extract_allows_gemma_model(self) -> None:
        mock = self.load_mock()
        calls: list[dict] = []

        def fake_model(**kwargs):
            calls.append(kwargs)
            return extract_json()

        payload = build_external_extract_payload(
            mock["metadata"],
            mock["transcript_segments"],
            transcript_language=mock["transcript_language"],
            transcript_source=mock["transcript_source"],
            provider="local-openai-compatible",
            model="gemma-3-local",
            api_base="http://127.0.0.1:1234/v1",
            text_model_caller=fake_model,
        )

        self.assertEqual(payload["extract_version"], "watchbrief_v5.external_extract.v1")
        self.assertFalse(payload["analysis_boundary"]["local_qwen_model_call"])
        self.assertFalse(payload["analysis_boundary"]["qwen_family_only"])
        self.assertEqual(payload["analysis_boundary"]["external_extract_provider"], "local-openai-compatible")
        self.assertEqual(payload["analysis_boundary"]["external_model_id"], "gemma-3-local")
        self.assertEqual(payload["analysis_boundary"]["external_api_base"], "http://127.0.0.1:1234/v1")
        self.assertEqual(payload["qwen_extract"]["_qwen_model"], "local-openai-compatible:gemma-3-local")
        self.assertEqual(calls[0]["model"], "gemma-3-local")

    def test_local_openai_compatible_extract_requires_model_name(self) -> None:
        mock = self.load_mock()
        with self.assertRaisesRegex(LocalQwenError, "external_extract_model_missing"):
            build_external_extract_payload(
                mock["metadata"],
                mock["transcript_segments"],
                provider="local-openai-compatible",
                model="",
                text_model_caller=lambda **kwargs: extract_json(),
            )

    def test_cloud_review_adapter_calls_named_provider_without_real_network(self) -> None:
        mock = self.load_mock()
        local_extract = build_external_extract_payload(
            mock["metadata"],
            mock["transcript_segments"],
            provider="gemini",
            text_model_caller=lambda **kwargs: extract_json(),
        )
        expected = json.dumps(load_golden("sample_payload_heartflow.json"), ensure_ascii=False)
        calls: list[dict] = []

        def fake_review_model(**kwargs):
            calls.append(kwargs)
            return expected

        with tempfile.TemporaryDirectory() as tmp:
            raw = run_cloud_review(
                local_extract,
                review_provider="gemini",
                model="gemini-2.5-flash",
                api_key_env="GEMINI_API_KEY",
                debug_dir=Path(tmp),
                text_model_caller=fake_review_model,
            )
            debug_path = Path(tmp) / "gemini_review_raw_response.txt"
            debug_exists = debug_path.exists()

        self.assertEqual(raw, expected)
        self.assertEqual(calls[0]["provider"], "gemini")
        self.assertEqual(calls[0]["model"], "gemini-2.5-flash")
        self.assertEqual(calls[0]["api_key_env"], "GEMINI_API_KEY")
        self.assertTrue(debug_exists)


if __name__ == "__main__":
    unittest.main()
