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
        self.assertIn("视频内容综合评分", ANALYZER_SYSTEM_PROMPT)
        self.assertIn("不是替用户做观看决定", ANALYZER_SYSTEM_PROMPT)
        self.assertIn("final_conclusion 只回答", ANALYZER_SYSTEM_PROMPT)
        self.assertIn("不要重新引入“要点提炼 / 可执行动作清单 / 完整笔记”", ANALYZER_SYSTEM_PROMPT)

    def test_payload_contract_lists_required_fields_and_tags(self) -> None:
        self.assertIn("final_conclusion", NORMALIZED_PAYLOAD_CONTRACT["required_fields"])
        self.assertIn("watch_segments", NORMALIZED_PAYLOAD_CONTRACT["required_fields"])
        self.assertIn("score_trace", NORMALIZED_PAYLOAD_CONTRACT["required_fields"])
        self.assertIn("long_content_breakdown", NORMALIZED_PAYLOAD_CONTRACT["optional_fields"])
        self.assertIn("报告基本可替代", NORMALIZED_PAYLOAD_CONTRACT["tag_enum"])

    def test_qwen_prompt_contains_video_transcript_editor_rules(self) -> None:
        self.assertIn("视频转写内容提炼编辑", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("不要先整篇翻译", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("定义 / 观点 / 条件 / 方法 / 例子 / 提醒", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("叔本华 / Schopenhauer", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("输出 final_conclusion", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)
        self.assertIn("phase_outline", QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT)

    def test_prompts_contain_long_content_breakdown_rule(self) -> None:
        mock = json.loads((ROOT / "golden" / "mock_transcript_heartflow.json").read_text(encoding="utf-8"))
        local_extract = build_local_extract_payload(mock["metadata"], mock["transcript_segments"], qwen_extractor=fake_qwen_extract)
        prompt = build_review_user_prompt(local_extract)

        self.assertIn("长内容阶段拆分规则", ANALYZER_SYSTEM_PROMPT)
        self.assertIn("视频时长超过 45 分钟", ANALYZER_SYSTEM_PROMPT)
        self.assertIn("long_content_breakdown", prompt)
        self.assertIn("不是补看入口", prompt)

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

    def test_build_review_user_prompt_adds_non_watch_target_contract_only_when_requested(self) -> None:
        mock = json.loads((ROOT / "golden" / "mock_transcript_heartflow.json").read_text(encoding="utf-8"))
        local_extract = build_local_extract_payload(mock["metadata"], mock["transcript_segments"], qwen_extractor=fake_qwen_extract)

        default_prompt = build_review_user_prompt(local_extract)
        target_prompt = build_review_user_prompt(local_extract, report_target="text_structure")

        self.assertNotIn("report_target 目标模式补充契约", default_prompt)
        self.assertIn("report_target 目标模式补充契约", target_prompt)
        self.assertIn("text_structure", target_prompt)
        self.assertIn("structure_overview", target_prompt)

    def test_build_review_user_prompt_compacts_large_local_extract_payload(self) -> None:
        segments = [
            {
                "start": f"{index // 60:02d}:{index % 60:02d}",
                "end": f"{index // 60:02d}:{(index + 1) % 60:02d}",
                "text": f"segment {index} 这是一段会导致 review prompt 膨胀的完整转写文本。",
            }
            for index in range(9000)
        ]
        chunks = [
            {
                "index": index,
                "status": "completed",
                "prompt_chars": 12000,
                "response_chars": 2000,
                "segments": segments[index * 10 : index * 10 + 10],
                "excerpt": "chunk excerpt " + ("很长的原文 " * 200),
            }
            for index in range(24)
        ]
        local_extract = {
            "extract_version": "watchbrief_v5.local_extract.v1",
            "transcript_hash": "a" * 64,
            "metadata": {
                "title": "刘谦×罗永浩长访谈",
                "url": "https://www.bilibili.com/video/BV-test",
                "channel": "罗永浩的十字路口",
                "duration": "4小时28分46秒",
                "date": "2025-11-11",
            },
            "transcript": {
                "language": "zh",
                "quality": "ok",
                "source": "transcription_json",
                "segment_count": len(segments),
                "char_count": 69000,
                "has_timestamps": True,
                "excerpt": "full transcript excerpt " + ("超长原文 " * 4000),
                "segments": segments,
            },
            "time_windows": [{"start": "00:00", "end": "05:00", "excerpt": "开场片段"}],
            "qwen_extract": {
                "cleaned_understanding": "访谈围绕魔术、表演训练、公众误解和职业选择展开。",
                "main_axis": "刘谦解释魔术作为表演艺术的训练方式与人生选择。",
                "core_claims": ["魔术价值不只在技巧，也在观众心理和现场表达。"],
                "conditions": ["长期训练", "理解观众"],
                "methods": ["用观众视角反推表演节奏"],
                "examples": ["春晚表演后的公众反馈"],
                "caveats": ["转写来自长视频，存在口语重复。"],
                "original_quotes": ["魔术不是骗你，是让你愿意相信。"],
                "refined_quotes": ["魔术的核心是管理观众注意力。"],
                "transcript_quality_note": "MLX-Audio 覆盖完整。",
                "language": "zh",
                "important_terms": ["刘谦", "罗永浩"],
                "corrected_terms": ["柳千 -> 刘谦"],
                "_qwen_model": "codex-cli:gpt-5.5",
            },
            "analysis_boundary": {
                "local_qwen_model_call": True,
                "qwen_family_only": True,
                "no_real_url_processing": True,
                "does_not_generate_final_report_fields": True,
                "qwen_prompt_version": "watchbrief_v5.qwen_local_extract_prompt.v2",
                "qwen_prompt_fingerprint": "b" * 64,
            },
            "chunked_local_extract": {
                "chunk_count": 24,
                "successful_chunk_count": 24,
                "failed_chunk_count": 0,
                "success_coverage": 1.0,
                "chunks": chunks,
                "reduce": {"status": "completed"},
            },
        }

        prompt = build_review_user_prompt(local_extract)

        self.assertLess(len(prompt.encode("utf-8")), 200_000)
        self.assertIn("刘谦×罗永浩长访谈", prompt)
        self.assertIn("访谈围绕魔术、表演训练、公众误解和职业选择展开", prompt)
        self.assertIn("魔术价值不只在技巧", prompt)
        self.assertIn("刘谦", prompt)
        self.assertIn("chunk_count", prompt)
        self.assertIn("successful_chunk_count", prompt)
        self.assertNotIn("segment 8999", prompt)
        self.assertNotIn('"segments": [', prompt)
        self.assertNotIn("chunk excerpt", prompt)


if __name__ == "__main__":
    unittest.main()
