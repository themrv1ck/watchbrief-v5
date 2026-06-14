from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, load_golden
from scripts.analyzer.codex_review import (
    build_codex_cli_command,
    build_codex_cli_env,
    build_codex_cli_prompt,
    build_codex_cli_retry_prompt,
    CodexReviewCallError,
    CodexReviewError,
    build_review_request,
    call_codex_cli,
    collect_live_review_contract_errors,
    main,
    parse_review_response,
    pre_schema_adapter,
    run_codex_review,
)
from scripts.analyzer.local_extract import DEFAULT_QWEN_MODEL, build_local_extract_payload
from scripts.analyzer.local_review import LOCAL_REVIEW_MODEL_ID, build_local_review_response
from scripts.cli import make_review_response_provider
from scripts.validator import WatchBriefValidationError


def fake_qwen_extract(seed: dict) -> dict:
    return {
        "cleaned_understanding": "转写内容主要说明心流来自任务设计。",
        "main_axis": "把心流理解为任务结构问题。",
        "core_claims": ["清楚目标、即时反馈和适度挑战能帮助进入心流。"],
        "conditions": ["目标清楚", "反馈及时"],
        "methods": ["拆小任务", "减少干扰"],
        "examples": ["用任务拆分说明启动阻力。"],
        "caveats": ["转写只覆盖示例素材。"],
        "original_quotes": ["不要先责怪自己不专注。"],
        "refined_quotes": ["心流是被任务结构诱发的状态。"],
        "transcript_quality_note": "转写清晰。",
        "language": seed["transcript"]["language"],
        "important_terms": ["flow"],
        "corrected_terms": [],
    }


class AnalyzerCodexReviewTest(unittest.TestCase):
    def build_local_extract(self) -> dict:
        mock = json.loads((ROOT / "golden" / "mock_transcript_heartflow.json").read_text(encoding="utf-8"))
        return build_local_extract_payload(
            mock["metadata"],
            mock["transcript_segments"],
            transcript_language=mock["transcript_language"],
            transcript_source=mock["transcript_source"],
            qwen_extractor=fake_qwen_extract,
        )

    def test_build_review_request_does_not_call_model(self) -> None:
        request = build_review_request(self.build_local_extract())

        self.assertEqual(request["request_version"], "watchbrief_v5.codex_review_request.v1")
        self.assertFalse(request["model_call_allowed"])
        self.assertEqual(request["expected_output"], "normalized_report_payload")
        self.assertEqual(len(request["messages"]), 2)
        self.assertEqual(request["messages"][0]["role"], "system")
        self.assertEqual(request["messages"][1]["role"], "user")
        self.assertIn("final_conclusion", request["contract"]["required_fields"])
        self.assertEqual(request["codex_prompt_version"], "watchbrief_v5.codex_review_prompt.v5")
        self.assertRegex(request["codex_prompt_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertRegex(request["stability_metadata"]["transcript_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(request["stability_metadata"]["qwen_model_id"], DEFAULT_QWEN_MODEL)
        self.assertEqual(request["stability_metadata"]["scoring_formula_version"], "watchbrief_v5.video_value_formula.v2")

    def test_qwen_terms_are_passed_into_review_request(self) -> None:
        request = build_review_request(self.build_local_extract())
        prompt = request["messages"][1]["content"]

        self.assertIn("important_terms", prompt)
        self.assertIn("corrected_terms", prompt)
        self.assertIn("flow", prompt)

    def test_build_review_request_carries_report_target_contract(self) -> None:
        request = build_review_request(self.build_local_extract(), report_target="content_brief")

        self.assertEqual(request["report_target"], "content_brief")
        self.assertEqual(request["stability_metadata"]["report_target"], "content_brief")
        self.assertIn("report_target_contract", request["contract"])
        self.assertIn("content_brief", request["messages"][1]["content"])
        self.assertIn("direct_statements", request["messages"][1]["content"])

    def test_review_prompt_includes_structured_assessment_rubric(self) -> None:
        request = build_review_request(self.build_local_extract())
        prompt = request["messages"][1]["content"]

        self.assertIn("structured_assessment_rubric", prompt)
        self.assertIn("不要把所有普通视频都压到 0-3", prompt)
        self.assertIn("报告可替代性只能影响观看建议", prompt)
        self.assertIn("报告可替代性只能影响观看建议", prompt)

    def test_review_prompt_requires_chineseized_domain_terms(self) -> None:
        request = build_review_request(self.build_local_extract())
        prompt = request["messages"][1]["content"]

        self.assertIn("最终报告必须以中文为主", prompt)
        self.assertIn("领域术语要自然中文化", prompt)
        self.assertIn("英文视频标题、频道名、品牌名、产品名", prompt)
        self.assertIn("中文（English）", prompt)
        self.assertIn("personal system product -> 个人方法系统产品", prompt)
        self.assertIn("education product -> 教育型产品 / 知识产品", prompt)
        self.assertIn("big burning problem -> 强痛点 / 核心痛点", prompt)
        self.assertIn("desired outcome -> 目标结果", prompt)
        self.assertIn("time frame -> 实现周期", prompt)
        self.assertIn("landing page -> 落地页", prompt)
        self.assertIn("CTA -> 行动号召 / 行动按钮", prompt)
        self.assertIn("features and benefits -> 功能与收益", prompt)
        self.assertIn("普通营销 / 产品 / 创作者术语不得成串裸露英文", prompt)

    def test_review_prompt_carries_watch_segment_candidates_and_rejects_opening_hook_default(self) -> None:
        extract = self.build_local_extract()
        extract["metadata"]["duration"] = "16分20秒"
        extract["transcript"]["segments"] = [
            {"start": "00:00", "end": "01:00", "text": "开头钩子讲神秘手表和体验冲击。"},
            {"start": "10:00", "end": "11:00", "text": "Zero Skill 自进化智能体会自己造工具和 skill。"},
            {"start": "12:00", "end": "13:00", "text": "early fusion 早融合把心率 IMU 音频 图像对齐到同一条时间线。"},
            {"start": "14:00", "end": "15:00", "text": "隐私和本地部署方案，可以把 server 套件部署到 home lab。"},
        ]
        extract["qwen_extract"].update({
            "main_axis": "解释可穿戴智能体如何自进化、多模态融合并支持本地部署。",
            "core_claims": ["Zero Skill 自进化和 early fusion 是关键机制。"],
            "methods": ["使用本地部署 server 套件对接本地模型。"],
            "important_terms": ["Zero Skill", "early fusion", "本地部署", "server", "home lab"],
        })

        request = build_review_request(extract)
        prompt = request["messages"][1]["content"]

        self.assertIn("watch_segment_candidates", prompt)
        self.assertIn("12:00", prompt)
        self.assertIn("15:00", prompt)
        self.assertIn("不是替用户决定原视频要不要看", prompt)
        self.assertIn("理解入口", prompt)
        self.assertIn("不要因为 transcript excerpt 从 00:00 开始就默认选择开头", prompt)
        self.assertIn("开头钩子", prompt)

    def test_build_review_request_requires_boundary_flag(self) -> None:
        local_extract = self.build_local_extract()
        local_extract["analysis_boundary"]["does_not_generate_final_report_fields"] = False
        with self.assertRaises(CodexReviewError):
            build_review_request(local_extract)

    def test_parse_review_response_validates_normalized_payload(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        response = json.dumps(payload, ensure_ascii=False)
        parsed = parse_review_response(response)

        self.assertEqual(parsed["title"], payload["title"])
        self.assertEqual(parsed["final_conclusion"], payload["final_conclusion"])
        self.assertIn("score_trace", parsed)

    def test_parse_review_response_does_not_mutate_input_object(self) -> None:
        payload = load_golden("sample_payload_charm.json")
        original = copy.deepcopy(payload)
        parsed = parse_review_response(payload)

        self.assertEqual(payload, original)
        self.assertEqual(parsed["title"], original["title"])
        self.assertEqual(parsed["replacement_score"], original["replacement_score"])

    def test_parse_review_response_rejects_invalid_final_conclusion(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["final_conclusion"] = "报告已经覆盖核心内容，原视频不用完整看。"
        with self.assertRaises(WatchBriefValidationError):
            parse_review_response(payload)

    def test_parse_review_response_rejects_non_json_text(self) -> None:
        with self.assertRaises(CodexReviewError) as context:
            parse_review_response("不是 JSON")
        self.assertEqual(context.exception.reason_code, "invalid_json")

    def test_parse_review_response_rejects_schema_invalid_payload(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["title"]
        with self.assertRaises(CodexReviewError) as context:
            parse_review_response(payload)
        self.assertEqual(context.exception.reason_code, "schema_invalid")

    def test_mock_path_does_not_call_model_transport(self) -> None:
        request = build_review_request(self.build_local_extract())
        self.assertFalse(request["model_call_allowed"])
        self.assertEqual(request["execution"], "manual_or_later_phase_only")

    def test_codex_path_requires_explicit_enablement(self) -> None:
        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                review_provider="manual",
                transport=lambda *args, **kwargs: self.fail("transport must not be called"),
            )
        self.assertEqual(context.exception.reason_code, "model_disabled")

    def test_codex_cli_provider_requires_explicit_enablement(self) -> None:
        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=False,
                review_provider="codex-cli",
                transport=lambda *args, **kwargs: self.fail("transport must not be called"),
            )
        self.assertEqual(context.exception.reason_code, "model_disabled")

    def test_explicit_codex_cli_path_invokes_transport_without_api_key(self) -> None:
        captured = {}

        def transport(review_request, *, model, timeout, codex_home, codex_home_root, codex_account, codex_bin):
            captured["request"] = review_request
            captured["model"] = model
            captured["timeout"] = timeout
            captured["codex_home"] = codex_home
            captured["codex_home_root"] = codex_home_root
            captured["codex_account"] = codex_account
            captured["codex_bin"] = codex_bin
            return json.dumps(load_golden("sample_payload_heartflow.json"), ensure_ascii=False)

        result = run_codex_review(
            self.build_local_extract(),
            enable_codex_review=True,
            review_provider="codex-cli",
            model="test-model",
            timeout=7,
            codex_home_root="~/.watchbrief_codex",
            codex_account="default",
            transport=transport,
        )

        self.assertEqual(result["title"], "如何把任务改造成更容易进入心流的状态")
        self.assertEqual(result["codex_model"], "test-model")
        self.assertRegex(result["transcript_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["codex_prompt_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(captured["model"], "test-model")
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(captured["codex_home_root"], "~/.watchbrief_codex")
        self.assertEqual(captured["codex_account"], "default")
        self.assertIn("normalized_report_payload", captured["request"]["messages"][1]["content"])

    def test_legacy_codex_provider_means_codex_cli_when_enabled(self) -> None:
        calls = []

        def transport(review_request, **kwargs):
            calls.append((review_request, kwargs))
            return json.dumps(load_golden("sample_payload_heartflow.json"), ensure_ascii=False)

        result = run_codex_review(
            self.build_local_extract(),
            enable_codex_review=True,
            review_provider="codex",
            transport=transport,
        )

        self.assertEqual(result["tag"], "不推荐观看")
        self.assertEqual(len(calls), 1)

    def test_build_codex_cli_command_uses_codex_exec(self) -> None:
        command = build_codex_cli_command(
            codex_bin="/usr/local/bin/codex",
            model="gpt-test",
            output_last_message=Path("/tmp/out.json"),
        )
        self.assertEqual(command[:2], ["/usr/local/bin/codex", "exec"])
        self.assertIn("-m", command)
        self.assertIn("gpt-test", command)
        self.assertNotIn("--output-schema", command)

    def test_codex_cli_prompt_requires_watch_verdict_pipe_time(self) -> None:
        request = build_review_request(self.build_local_extract())
        prompt = build_codex_cli_prompt(request)

        self.assertIn("watch_verdict must contain either an exact pipe time range", prompt)
        self.assertIn("copy that exact primary `start | end` into watch_verdict", prompt)
        self.assertIn("Never use `start - end`", prompt)

    def test_build_codex_cli_env_uses_account_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            account_home = Path(temp_dir) / "default"
            account_home.mkdir()
            env = build_codex_cli_env(
                codex_home_root=temp_dir,
                codex_account="default",
                base_env={},
            )
        self.assertTrue(env["CODEX_HOME"].endswith("/default"))

    def test_build_codex_cli_env_missing_home_is_auth_failed(self) -> None:
        with self.assertRaises(CodexReviewCallError) as context:
            build_codex_cli_env(codex_home="/path/that/does/not/exist", base_env={})
        self.assertEqual(context.exception.reason_code, "auth_failed")

    def test_pre_schema_adapter_scales_percent_replacement_score(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["replacement_score"] = 87
        self.assertEqual(pre_schema_adapter(payload)["replacement_score"], 8.7)

    def test_pre_schema_adapter_splits_arrow_chain_string(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["arrow_chain"] = "任务模糊 → 干扰变多 → 计时启动 → 短时推进 → 休息切换"
        self.assertEqual(
            pre_schema_adapter(payload)["arrow_chain"],
            ["任务模糊", "干扰变多", "计时启动", "短时推进", "休息切换"],
        )

    def test_pre_schema_adapter_maps_watch_segment_label_to_title(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["watch_segments"][0]["label"] = "首选片段：任务改造步骤"
        del payload["watch_segments"][0]["title"]

        adapted = pre_schema_adapter(payload)

        self.assertEqual(adapted["watch_segments"][0]["title"], "首选片段：任务改造步骤")
        self.assertNotIn("label", adapted["watch_segments"][0])

    def test_pre_schema_adapter_stringifies_only_one_segment_object(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["only_one_segment"] = {
            "start": "10:51",
            "end": "15:08",
            "reason": "这一段已经覆盖全片核心。",
        }
        self.assertEqual(
            pre_schema_adapter(payload)["only_one_segment"],
            "只选一段：10:51 | 15:08。这一段已经覆盖全片核心。",
        )

    def test_pre_schema_adapter_stringifies_score_basis_values(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["score_basis"]["information_density"] = 4
        payload["score_basis"]["evidence_quality"] = {"basis": "经验说明"}
        adapted = pre_schema_adapter(payload)

        self.assertEqual(adapted["score_basis"]["information_density"], "4")
        self.assertEqual(adapted["score_basis"]["evidence_quality"], "basis: 经验说明")

    def test_pre_schema_adapter_does_not_create_missing_watch_segments(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["watch_segments"]

        adapted = pre_schema_adapter(payload)

        self.assertNotIn("watch_segments", adapted)

    def test_pre_schema_adapter_adds_missing_content_caveat_only(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["content_caveat"]

        adapted = pre_schema_adapter(payload)

        self.assertEqual(adapted["content_caveat"], "")

    def test_pre_schema_adapter_drops_local_extract_term_helpers(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["important_terms"] = ["WOOP"]
        payload["corrected_terms"] = ["Woop -> WOOP"]

        adapted = pre_schema_adapter(payload)

        self.assertNotIn("important_terms", adapted)
        self.assertNotIn("corrected_terms", adapted)

    def test_deterministic_adapter_restores_empty_source_metadata(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["title"] = ""
        payload["url"] = ""
        payload["channel"] = ""

        adapted = parse_review_response(
            payload,
            apply_adapter=True,
            stability_metadata={
                "source_title": "源视频标题",
                "source_url": "https://example.com/video",
                "source_channel": "源频道",
            },
        )

        self.assertEqual(adapted["title"], "源视频标题")
        self.assertEqual(adapted["url"], "https://example.com/video")
        self.assertEqual(adapted["channel"], "源频道")

    def test_call_codex_cli_missing_command_is_classified(self) -> None:
        with mock.patch("scripts.analyzer.codex_review.shutil.which", return_value=None):
            with self.assertRaises(CodexReviewCallError) as context:
                call_codex_cli(build_review_request(self.build_local_extract()), model="gpt-test", timeout=1)
        self.assertEqual(context.exception.reason_code, "codex_cli_missing")

    def test_call_codex_cli_not_logged_in_is_classified(self) -> None:
        completed = mock.Mock()
        completed.returncode = 1
        completed.stdout = "Not logged in"
        completed.stderr = ""
        with mock.patch("scripts.analyzer.codex_review.shutil.which", return_value="/usr/local/bin/codex"):
            with mock.patch("scripts.analyzer.codex_review.subprocess.run", return_value=completed):
                with self.assertRaises(CodexReviewCallError) as context:
                    call_codex_cli(build_review_request(self.build_local_extract()), model="gpt-test", timeout=1)
        self.assertEqual(context.exception.reason_code, "codex_not_logged_in")

    def test_cli_mock_provider_does_not_call_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_path = Path(temp_dir) / "mock.json"
            payload = load_golden("sample_payload_heartflow.json")
            mock_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with mock.patch("scripts.cli.run_codex_review", side_effect=AssertionError("must not call codex")):
                provider = make_review_response_provider(
                    mock_review_response=mock_path,
                    review_provider="mock",
                    enable_codex_review=False,
                    model="gpt-test",
                    codex_model=None,
                    codex_home=None,
                    codex_home_root=None,
                    codex_account=None,
                    timeout=1,
                )
                self.assertEqual(provider({}, {}, {})["title"], payload["title"])

    def test_local_review_provider_builds_schema_valid_payload_without_codex(self) -> None:
        local_extract = self.build_local_extract()
        request = build_review_request(local_extract)
        with mock.patch("scripts.cli.run_codex_review", side_effect=AssertionError("must not call codex")):
            provider = make_review_response_provider(
                mock_review_response=None,
                review_provider="local",
                enable_codex_review=False,
                model="gpt-test",
                codex_model=None,
                codex_home=None,
                codex_home_root=None,
                codex_account=None,
                timeout=1,
            )
            raw = provider({}, request, local_extract)

        parsed = parse_review_response(raw, stability_metadata={**request["stability_metadata"], "codex_model": LOCAL_REVIEW_MODEL_ID})
        self.assertEqual(parsed["codex_model"], LOCAL_REVIEW_MODEL_ID)
        self.assertIn(parsed["tag"], {"不推荐观看", "只建议跳看"})
        self.assertIn("本地模式", parsed["content_caveat"])

    def test_build_local_review_response_uses_qwen_extract_fields(self) -> None:
        response = build_local_review_response(self.build_local_extract())

        self.assertIn("任务结构", response["one_line_brief"])
        self.assertEqual(response["watch_segments"][0]["priority"], "primary")
        self.assertEqual(response["codex_model"], LOCAL_REVIEW_MODEL_ID)

    def test_build_local_review_response_supports_content_brief_target(self) -> None:
        response = build_local_review_response(self.build_local_extract(), report_target="content_brief")

        self.assertEqual(response["report_target"], "content_brief")
        self.assertIn("direct_statements", response["target_sections"])
        self.assertIn("key_points", response["target_sections"])

    def test_local_review_ranks_high_value_segments_instead_of_opening_hook(self) -> None:
        extract = self.build_local_extract()
        extract["metadata"]["duration"] = "16分20秒"
        segments = []
        texts = [
            "开场讲一个神秘手表体验，引出好奇心和生活记录。",
            "第一天体验很空，首页出现几张卡片，属于铺垫。",
            "朋友案例说明 AI 发现了一些生活细节。",
            "继续讲朋友案例和情绪故事。",
            "这里仍然是体验故事，没有展开技术原理。",
            "生活记录案例继续推进。",
            "体验反馈说明 AI 会提醒日常事项。",
            "这一段是过渡铺垫。",
            "继续铺垫用户为什么想知道原理。",
            "准备进入原理说明。",
            "接下来讲原理，Zero Skill 自进化智能体，agent 在没有前置工具时自己造工具。",
            "agent 遇到问题会现场写代码，解决后把 skill 工具沉淀进工具库。",
            "第二个关键点是多模态 early fusion 早融合，对比 late fusion 晚融合。",
            "早融合把心率 IMU 音频 图像 对齐到同一条时间线，减少细节丢失。",
            "隐私问题的方案是本地部署 server 套件，home lab 对接本地模型或自己的 API。",
            "最后讲限制，开发版硬件、续航、麦克风和声纹识别仍有提升空间。",
        ]
        for index, text in enumerate(texts):
            segments.append({
                "start": f"{index:02d}:00",
                "end": f"{index + 1:02d}:00",
                "text": text,
            })
        extract["transcript"]["segments"] = segments
        extract["transcript"]["segment_count"] = len(segments)
        extract["transcript"]["char_count"] = sum(len(item["text"]) for item in segments)
        extract["time_windows"] = [
            {"start": "00:00", "end": "01:00", "excerpt": texts[0]},
            {"start": "01:00", "end": "02:00", "excerpt": texts[1]},
        ]
        extract["qwen_extract"].update({
            "main_axis": "通过可穿戴设备理解人的状态，并说明智能体如何自进化和本地部署。",
            "core_claims": [
                "Zero Skill 自进化让 agent 能为不同用户生成不同 skill。",
                "early fusion 早融合能把多模态信号放到同一时间线理解。",
                "隐私风险必须通过本地部署和数据边界来处理。",
            ],
            "methods": [
                "让 agent 现场写代码并沉淀工具。",
                "用本地部署 server 套件对接本地模型或自有 API。",
            ],
            "examples": ["手表通过心率、音频和 IMU 识别紧张状态。"],
            "important_terms": ["Zero Skill", "agent", "early fusion", "本地部署", "server", "home lab"],
        })

        response = build_local_review_response(extract)
        parsed = parse_review_response(
            response,
            stability_metadata={**build_review_request(extract)["stability_metadata"], "codex_model": LOCAL_REVIEW_MODEL_ID},
        )

        primary = parsed["watch_segments"][0]
        self.assertEqual(primary["priority"], "primary")
        self.assertNotEqual(primary["start"], "00:00")
        self.assertGreaterEqual(int(primary["start"].split(":")[0]), 10)
        self.assertIn("核心逻辑入口", primary["title"])
        self.assertIn(f"{primary['start']} | {primary['end']}", parsed["watch_verdict"])
        self.assertIn(f"{primary['start']} | {primary['end']}", parsed["only_one_segment"])

    def test_local_review_adds_phase_breakdown_for_long_course(self) -> None:
        extract = self.build_local_extract()
        extract["metadata"]["title"] = "一小时课程：如何设计任务系统"
        extract["metadata"]["duration"] = "1小时00分00秒"
        segments = []
        topics = [
            "开场介绍课程目标，说明为什么心流不是单纯意志力问题。",
            "第一阶段讲目标设定，解释清楚目标如何降低启动阻力。",
            "第二阶段讲即时反馈，说明反馈节点如何帮助持续推进。",
            "第三阶段讲挑战难度，解释太简单和太难都会破坏心流。",
            "第四阶段讲任务拆分，用写作和学习任务举例说明小入口。",
            "结尾总结适用边界，提醒没有反馈的任务要先重做任务结构。",
        ]
        for index, text in enumerate(topics):
            segments.append({
                "start": f"{index * 10:02d}:00",
                "end": f"{(index + 1) * 10:02d}:00",
                "text": text,
            })
        extract["transcript"]["segments"] = segments
        extract["transcript"]["segment_count"] = len(segments)
        extract["transcript"]["char_count"] = sum(len(item["text"]) for item in segments)
        extract["qwen_extract"].update({
            "main_axis": "这是一门课程，系统讲解如何把任务设计成更容易进入心流的结构。",
            "cleaned_understanding": "课程按目标、反馈、挑战、拆分和边界逐步讲解任务设计。",
            "core_claims": ["心流更依赖任务结构，而不是单纯意志力。"],
            "important_terms": ["课程", "心流", "任务系统"],
        })

        parsed = parse_review_response(
            build_local_review_response(extract),
            stability_metadata={**build_review_request(extract)["stability_metadata"], "codex_model": LOCAL_REVIEW_MODEL_ID},
        )

        self.assertIn("long_content_breakdown", parsed)
        self.assertGreaterEqual(len(parsed["long_content_breakdown"]), 2)
        self.assertEqual(parsed["long_content_breakdown"][0]["start"], "00:00")
        self.assertIn("转写内容集中在", parsed["long_content_breakdown"][0]["summary"])

    def test_local_review_scores_vary_with_extract_evidence(self) -> None:
        sparse = self.build_local_extract()
        sparse["qwen_extract"].update({
            "core_claims": [],
            "methods": [],
            "examples": [],
            "caveats": [],
            "refined_quotes": [],
            "important_terms": [],
            "time_windows": [],
        })
        rich = self.build_local_extract()
        rich["metadata"]["duration"] = "32分10秒"
        rich["qwen_extract"].update({
            "core_claims": ["观点一", "观点二", "观点三", "观点四"],
            "methods": ["方法一", "方法二", "方法三", "方法四"],
            "examples": ["例子一", "例子二", "例子三", "例子四"],
            "caveats": ["边界一", "边界二", "边界三"],
            "refined_quotes": ["金句一", "金句二", "金句三", "金句四"],
            "important_terms": ["术语一", "术语二", "术语三", "术语四", "术语五"],
            "time_windows": [
                {"start": "00:00", "end": "02:00", "excerpt": "开头"},
                {"start": "02:00", "end": "04:00", "excerpt": "展开"},
                {"start": "04:00", "end": "06:00", "excerpt": "例子"},
            ],
        })

        sparse_parsed = parse_review_response(
            build_local_review_response(sparse),
            stability_metadata={**build_review_request(sparse)["stability_metadata"], "codex_model": LOCAL_REVIEW_MODEL_ID},
        )
        rich_parsed = parse_review_response(
            build_local_review_response(rich),
            stability_metadata={**build_review_request(rich)["stability_metadata"], "codex_model": LOCAL_REVIEW_MODEL_ID},
        )

        self.assertNotEqual(sparse_parsed["replacement_score"], rich_parsed["replacement_score"])
        self.assertLess(sparse_parsed["replacement_score"], rich_parsed["replacement_score"])
        self.assertNotEqual(sparse_parsed["structured_assessment"], rich_parsed["structured_assessment"])

    def test_local_review_dense_explainers_do_not_collapse_to_same_score(self) -> None:
        first = self.build_local_extract()
        first["metadata"]["duration"] = "31分37秒"
        first["time_windows"] = [{"start": f"0{i}:00", "end": f"0{i}:30", "excerpt": "dense"} for i in range(6)]
        first["qwen_extract"].update({
            "core_claims": [f"印度中国论点{i}" for i in range(14)],
            "methods": [f"地缘分析方法{i}" for i in range(5)],
            "examples": [f"边境例证{i}" for i in range(7)],
            "caveats": ["商业植入内容", "数据边界", "术语误差", "反方不足"],
            "refined_quotes": [f"金句{i}" for i in range(7)],
            "important_terms": [f"术语{i}" for i in range(22)],
        })
        second = self.build_local_extract()
        second["metadata"]["duration"] = "34分18秒"
        second["time_windows"] = [{"start": f"0{i}:00", "end": f"0{i}:30", "excerpt": "dense"} for i in range(6)]
        second["qwen_extract"].update({
            "core_claims": [f"沙特城市论点{i}" for i in range(9)],
            "methods": [f"现场观察方法{i}" for i in range(5)],
            "examples": [f"Neom例证{i}" for i in range(4)],
            "caveats": ["可行性存疑", "搬迁争议", "信息透明度低", "治理风险"],
            "refined_quotes": [f"金句{i}" for i in range(5)],
            "important_terms": [f"术语{i}" for i in range(9)],
        })

        first_parsed = parse_review_response(
            build_local_review_response(first),
            stability_metadata={**build_review_request(first)["stability_metadata"], "codex_model": LOCAL_REVIEW_MODEL_ID},
        )
        second_parsed = parse_review_response(
            build_local_review_response(second),
            stability_metadata={**build_review_request(second)["stability_metadata"], "codex_model": LOCAL_REVIEW_MODEL_ID},
        )

        self.assertNotEqual(first_parsed["structured_assessment"], second_parsed["structured_assessment"])
        self.assertNotEqual(first_parsed["replacement_score"], second_parsed["replacement_score"])
        self.assertGreaterEqual(first_parsed["replacement_score"], 7.6)
        self.assertGreaterEqual(second_parsed["replacement_score"], 7.5)
        self.assertLessEqual(first_parsed["replacement_score"], 8.2)
        self.assertLessEqual(second_parsed["replacement_score"], 8.2)

    def test_cli_manual_provider_without_enablement_does_not_call_codex(self) -> None:
        with mock.patch("scripts.cli.run_codex_review", side_effect=AssertionError("must not call codex")):
            with self.assertRaises(ValueError):
                make_review_response_provider(
                    mock_review_response=None,
                    review_provider="manual",
                    enable_codex_review=False,
                    model="gpt-test",
                    codex_model=None,
                    codex_home=None,
                    codex_home_root=None,
                    codex_account=None,
                    timeout=1,
                )

    def test_cli_dry_run_writes_review_request_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            local_extract_path = temp_path / "local_extract.json"
            output_path = temp_path / "review_request.json"
            local_extract_path.write_text(json.dumps(self.build_local_extract(), ensure_ascii=False), encoding="utf-8")

            with mock.patch.object(
                sys,
                "argv",
                [
                    "codex_review.py",
                    "--dry-run-review-request",
                    "--local-extract",
                    str(local_extract_path),
                    "--output",
                    str(output_path),
                ],
            ):
                self.assertEqual(main(), 0)

            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(output["request_version"], "watchbrief_v5.codex_review_request.v1")
            self.assertFalse(output["model_call_allowed"])

    def test_codex_empty_response_is_classified(self) -> None:
        def transport(review_request, **kwargs):
            return ""

        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=True,
                review_provider="codex-cli",
                transport=transport,
            )
        self.assertEqual(context.exception.reason_code, "empty_response")

    def test_codex_invalid_json_is_classified(self) -> None:
        def transport(review_request, **kwargs):
            return "not json"

        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=True,
                review_provider="codex-cli",
                transport=transport,
            )
        self.assertEqual(context.exception.reason_code, "invalid_json")

    def test_codex_schema_invalid_is_classified(self) -> None:
        bad_payload = load_golden("sample_payload_heartflow.json")
        del bad_payload["title"]

        def transport(review_request, **kwargs):
            return json.dumps(bad_payload, ensure_ascii=False)

        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=True,
                review_provider="codex-cli",
                transport=transport,
        )
        self.assertEqual(context.exception.reason_code, "schema_invalid")

    def test_live_review_requires_structured_assessment_for_watch_order(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["structured_assessment"]

        self.assertEqual(
            collect_live_review_contract_errors(payload),
            ["$.structured_assessment must be an object"],
        )

    def test_missing_structured_assessment_retries_then_accepts_fixed_payload(self) -> None:
        bad_payload = load_golden("sample_payload_heartflow.json")
        del bad_payload["structured_assessment"]
        fixed_payload = load_golden("sample_payload_heartflow.json")
        calls = []

        def transport(review_request, **kwargs):
            calls.append(review_request)
            payload = bad_payload if len(calls) == 1 else fixed_payload
            return json.dumps(payload, ensure_ascii=False)

        result = run_codex_review(
            self.build_local_extract(),
            enable_codex_review=True,
            review_provider="codex-cli",
            transport=transport,
        )

        self.assertEqual(result["title"], fixed_payload["title"])
        self.assertEqual(len(calls), 2)
        self.assertIn("_prompt_override", calls[1])
        self.assertIn("structured_assessment must be an object", calls[1]["_prompt_override"])

    def test_schema_invalid_retries_twice_then_fails(self) -> None:
        bad_payload = load_golden("sample_payload_heartflow.json")
        del bad_payload["title"]
        calls = []

        def transport(review_request, **kwargs):
            calls.append(review_request)
            return json.dumps(bad_payload, ensure_ascii=False)

        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=True,
                review_provider="codex-cli",
                transport=transport,
            )

        self.assertEqual(context.exception.reason_code, "schema_invalid")
        self.assertEqual(len(calls), 3)
        self.assertIn("_prompt_override", calls[1])
        self.assertIn("_prompt_override", calls[2])

    def test_retry_prompt_contains_schema_error_summary(self) -> None:
        request = build_review_request(self.build_local_extract())
        prompt = build_codex_cli_retry_prompt(
            request,
            previous_json='{"replacement_score": 87}',
            schema_errors=[
                "$.replacement_score is above maximum",
                "$.arrow_chain must be array",
                "$.watch_segments[0].title is required",
            ],
        )

        self.assertIn("$.replacement_score is above maximum", prompt)
        self.assertIn("replacement_score must be between 0 and 10", prompt)
        self.assertIn("arrow_chain must be an array of strings", prompt)
        self.assertIn("watch_segments[].title is required", prompt)
        self.assertIn("watch_verdict must contain an exact `start | end` pipe time range", prompt)
        self.assertIn("只修 JSON", prompt)

    def test_retry_prompt_targets_watch_verdict_time_error(self) -> None:
        request = build_review_request(self.build_local_extract())
        prompt = build_codex_cli_retry_prompt(
            request,
            previous_json='{"watch_verdict": "看报告基本够，原视频只建议跳看首选片段。"}',
            schema_errors=["watch_verdict: must contain a pipe time range or a no-watch decision"],
        )

        self.assertIn("only fix watch_verdict", prompt)
        self.assertIn("exact primary watch_segments start/end as `start | end`", prompt)
        self.assertIn("20:44 | 32:34", prompt)

    def test_arrow_chain_max_length_retry_prompt_is_targeted(self) -> None:
        request = build_review_request(self.build_local_extract())
        prompt = build_codex_cli_retry_prompt(
            request,
            previous_json='{"arrow_chain": ["这是一个明显超过十八个字的链路节点"]}',
            schema_errors=["$.arrow_chain[0] is longer than maxLength"],
        )

        self.assertIn("arrow_chain targeted correction", prompt)
        self.assertIn("18 Chinese characters or fewer", prompt)
        self.assertIn("only shorten arrow_chain strings", prompt)

    def test_legacy_module_label_retry_prompt_is_targeted(self) -> None:
        request = build_review_request(self.build_local_extract())
        prompt = build_codex_cli_retry_prompt(
            request,
            previous_json='{"content_caveat": "这里出现完整笔记模块"}',
            schema_errors=["$: legacy module label is not allowed"],
        )

        self.assertIn("legacy module label targeted correction", prompt)
        self.assertIn("Remove old V1/V2 module labels", prompt)
        self.assertIn("要点提炼", prompt)

    def test_schema_invalid_after_adapter_still_fails(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["watch_segments"]

        def transport(review_request, **kwargs):
            return json.dumps(payload, ensure_ascii=False)

        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=True,
                review_provider="codex-cli",
                transport=transport,
                max_schema_retries=0,
            )

        self.assertEqual(context.exception.reason_code, "schema_invalid")

    def test_codex_validator_invalid_final_conclusion_is_classified(self) -> None:
        bad_payload = load_golden("sample_payload_heartflow.json")
        bad_payload["final_conclusion"] = "报告已经覆盖核心内容，原视频只建议跳看重点片段。"

        def transport(review_request, **kwargs):
            return json.dumps(bad_payload, ensure_ascii=False)

        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=True,
                review_provider="codex-cli",
                transport=transport,
            )
        self.assertEqual(context.exception.reason_code, "validator_failed")

    def test_codex_validator_invalid_watch_verdict_retries_and_accepts_fixed_payload(self) -> None:
        bad_payload = load_golden("sample_payload_heartflow.json")
        bad_payload["watch_verdict"] = "看报告基本够，原视频只建议跳看首选片段。"
        fixed_payload = load_golden("sample_payload_heartflow.json")
        fixed_payload["watch_verdict"] = "看报告基本够，原视频只建议跳看 10:51 | 15:08；想补前置误区，再看 01:59 | 05:12。"
        calls = []

        def transport(review_request, **kwargs):
            calls.append(review_request)
            payload = bad_payload if len(calls) == 1 else fixed_payload
            return json.dumps(payload, ensure_ascii=False)

        result = run_codex_review(
            self.build_local_extract(),
            enable_codex_review=True,
            review_provider="codex-cli",
            transport=transport,
        )

        self.assertEqual(result["watch_verdict"], fixed_payload["watch_verdict"])
        self.assertEqual(len(calls), 2)
        self.assertIn("_prompt_override", calls[1])
        self.assertIn("watch_verdict", calls[1]["_prompt_override"])
        primaries = [segment for segment in result["watch_segments"] if segment["priority"] == "primary"]
        self.assertEqual(len(primaries), 1)
        self.assertIn(f"{primaries[0]['start']} | {primaries[0]['end']}", result["only_one_segment"])

    def test_codex_transport_errors_are_preserved(self) -> None:
        def transport(review_request, **kwargs):
            raise CodexReviewCallError("quota_limited", "quota exhausted", 429)

        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=True,
                review_provider="codex-cli",
                transport=transport,
            )
        self.assertEqual(context.exception.reason_code, "quota_limited")

    def test_codex_timeout_is_classified(self) -> None:
        def transport(review_request, **kwargs):
            raise CodexReviewCallError("timeout", "request timed out")

        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=True,
                review_provider="codex-cli",
                transport=transport,
            )
        self.assertEqual(context.exception.reason_code, "timeout")

    def test_codex_model_error_is_classified(self) -> None:
        def transport(review_request, **kwargs):
            raise CodexReviewCallError("model_error", "upstream model error", 500)

        with self.assertRaises(CodexReviewCallError) as context:
            run_codex_review(
                self.build_local_extract(),
                enable_codex_review=True,
                review_provider="codex-cli",
                transport=transport,
            )
        self.assertEqual(context.exception.reason_code, "model_error")


if __name__ == "__main__":
    unittest.main()
