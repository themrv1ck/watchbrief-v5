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
        self.assertEqual(request["codex_prompt_version"], "watchbrief_v5.codex_review_prompt.v3")
        self.assertRegex(request["codex_prompt_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertRegex(request["stability_metadata"]["transcript_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(request["stability_metadata"]["qwen_model_id"], DEFAULT_QWEN_MODEL)
        self.assertEqual(request["stability_metadata"]["scoring_formula_version"], "watchbrief_v5.scoring_formula.v1")

    def test_qwen_terms_are_passed_into_review_request(self) -> None:
        request = build_review_request(self.build_local_extract())
        prompt = request["messages"][1]["content"]

        self.assertIn("important_terms", prompt)
        self.assertIn("corrected_terms", prompt)
        self.assertIn("flow", prompt)

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

        self.assertEqual(result["tag"], "只建议跳看")
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
        self.assertEqual(parsed["tag"], "只建议跳看")
        self.assertIn("本地模式", parsed["content_caveat"])

    def test_build_local_review_response_uses_qwen_extract_fields(self) -> None:
        response = build_local_review_response(self.build_local_extract())

        self.assertIn("任务结构", response["one_line_brief"])
        self.assertEqual(response["watch_segments"][0]["priority"], "primary")
        self.assertEqual(response["codex_model"], LOCAL_REVIEW_MODEL_ID)

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
