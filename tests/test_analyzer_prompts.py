from __future__ import annotations

import json
import unittest

from helpers import ROOT
from scripts.analyzer.local_extract import build_local_extract_payload
from scripts.analyzer.prompts import ANALYZER_SYSTEM_PROMPT, NORMALIZED_PAYLOAD_CONTRACT, QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT, build_review_user_prompt


def fake_qwen_extract(seed: dict) -> dict:
    return {
        "cleaned_understanding": "转写内容主要说明心流来自任务设计。",
        "main_axis": "把心流理解为任务结构问题。",
        "core_claims": ["清楚目标、即时反馈和适度挑战能帮助进入心流。"],
        "conditions": [],
        "methods": ["拆小任务"],
        "examples": [],
        "caveats": [],
        "original_quotes": [],
        "refined_quotes": [],
        "transcript_quality_note": "转写清晰。",
        "language": seed["transcript"]["language"],
        "important_terms": ["flow"],
        "corrected_terms": [],
    }


class AnalyzerPromptsTest(unittest.TestCase):
    def test_prompt_contains_v5_field_boundaries(self) -> None:
        self.assertIn("replacement_score 只表示", ANALYZER_SYSTEM_PROMPT)
        self.assertIn("topic 不能影响 replacement_score", ANALYZER_SYSTEM_PROMPT)
        self.assertIn("final_conclusion 只回答", ANALYZER_SYSTEM_PROMPT)
        self.assertIn("不要重新引入“要点提炼 / 可执行动作清单 / 完整笔记”", ANALYZER_SYSTEM_PROMPT)

    def test_payload_contract_lists_required_fields_and_tags(self) -> None:
        self.assertIn("final_conclusion", NORMALIZED_PAYLOAD_CONTRACT["required_fields"])
        self.assertIn("watch_segments", NORMALIZED_PAYLOAD_CONTRACT["required_fields"])
        self.assertIn("score_trace", NORMALIZED_PAYLOAD_CONTRACT["required_fields"])
        self.assertIn("报告基本可替代", NORMALIZED_PAYLOAD_CONTRACT["tag_enum"])

    def test_qwen_prompt_contains_video_transcript_editor_rules(self) -> None:
        self.assertIn("视频转写内容提炼编辑", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("不要先整篇翻译", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("定义 / 观点 / 条件 / 方法 / 例子 / 提醒", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("叔本华 / Schopenhauer", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("输出 final_conclusion", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)

    def test_build_review_user_prompt_embeds_qwen_extract(self) -> None:
        mock = json.loads((ROOT / "golden" / "mock_transcript_heartflow.json").read_text(encoding="utf-8"))
        local_extract = build_local_extract_payload(mock["metadata"], mock["transcript_segments"], qwen_extractor=fake_qwen_extract)
        prompt = build_review_user_prompt(local_extract)

        self.assertIn("只能输出 JSON 对象", prompt)
        self.assertIn("normalized_report_payload 契约", prompt)
        self.assertIn("local_extract_payload", prompt)
        self.assertIn("qwen_extract", prompt)
        self.assertIn("core_claims", prompt)
        self.assertIn("important_terms", prompt)
        self.assertIn("corrected_terms", prompt)
        self.assertIn("不要自己重新猜人名", prompt)
        self.assertIn(mock["metadata"]["title"], prompt)
        self.assertIn('"local_qwen_model_call": true', prompt)


if __name__ == "__main__":
    unittest.main()
