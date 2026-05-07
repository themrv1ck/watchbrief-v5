from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts import webui


class WebUITest(unittest.TestCase):
    def test_static_shell_exposes_customization_sections(self) -> None:
        html = webui.render_index_html()

        self.assertIn("WatchBrief WebUI", html)
        self.assertIn("LOCAL-FIRST VIDEO REPORT", html)
        self.assertIn("普通用户模式：贴链接就能开始", html)
        self.assertNotIn("普通用户只看这里", html)
        self.assertNotIn("电脑里没有本地大模型也能用", html)
        self.assertNotIn("一键切到无本地模型模式", html)
        self.assertNotIn("开始前自动检查这 5 件事", html)
        self.assertIn("环境诊断", html)
        self.assertIn("ENVIRONMENT CHECK", html)
        self.assertIn("这里是独立诊断窗口", html)
        self.assertIn("运行诊断", html)
        run_panel = html.split('data-panel="run"', 1)[1].split('data-panel="models"', 1)[0]
        diagnostics_panel = html.split('data-panel="diagnostics"', 1)[1].split('data-panel="history"', 1)[0]
        self.assertNotIn("一键环境诊断", run_panel)
        self.assertIn("一键环境诊断", diagnostics_panel)
        self.assertIn("/api/diagnostics", webui.APP_JS)
        self.assertIn("runDiagnostics", webui.APP_JS)
        self.assertIn("renderDiagnostics", webui.APP_JS)
        self.assertIn("showPanel('diagnostics')", webui.APP_JS)
        self.assertIn("diagnostics-panel", webui.STYLES_CSS)
        self.assertIn("diagnostics-page-grid", webui.STYLES_CSS)
        self.assertIn("diagnostic-row", webui.STYLES_CSS)
        self.assertIn("模型来源", html)
        self.assertIn("转写", html)
        self.assertIn("updatePreflight", webui.APP_JS)
        self.assertIn("setPreflightItem", webui.APP_JS)
        self.assertIn("缺模型来源", webui.APP_JS)
        self.assertIn("LAST_STATUS", webui.APP_JS)
        self.assertIn("高级设置", html)
        self.assertIn("不懂就保持收起", html)
        self.assertIn("隐藏当前配置", html)
        self.assertIn('class="config-toggle-icon"', html)
        self.assertIn('aria-label="隐藏当前配置"', html)
        self.assertIn('id="toggleInspector"', html)
        self.assertIn("inspector-hidden", webui.STYLES_CSS)
        self.assertIn(".config-toggle-icon", webui.STYLES_CSS)
        self.assertIn("toggleInspector", webui.APP_JS)
        self.assertIn("applyEngineProfiles", webui.APP_JS)
        self.assertIn("ANALYSIS_MODE_PRESETS", webui.APP_JS)
        self.assertIn("DEFAULT_EXTRACT_PROFILES", webui.APP_JS)
        self.assertIn("DEFAULT_REVIEW_PROFILES", webui.APP_JS)
        self.assertIn("完全不懂代码也照着做", html)
        self.assertIn("WatchBrief 不保存你的密码，不展示 cookie", html)
        self.assertIn("本地规则判断，无需账号", html)
        self.assertIn("新建任务", html)
        self.assertIn("模型与账号", html)
        self.assertIn("输出与登录态", html)
        self.assertIn("任务记录", html)
        self.assertNotIn("验收矩阵", html)
        self.assertNotIn("真实视频验收矩阵", html)
        self.assertNotIn("/api/smoke-matrix", webui.APP_JS)
        self.assertNotIn("loadSmokeMatrix", webui.APP_JS)
        self.assertNotIn("matrixRow", webui.APP_JS)
        self.assertNotIn("matrixInfoPanel", html)
        self.assertNotIn("data-dismiss=\"matrixInfoPanel\"", html)
        self.assertNotIn("发布整理", html)
        self.assertIn("失败处理", html)
        self.assertIn("环境诊断", html)
        self.assertIn("用 Deep 重试", html)
        self.assertIn("清空任务记录", html)
        self.assertIn("/api/tasks/clear", webui.APP_JS)
        self.assertIn("historyRescuePanel", html)
        self.assertIn("data-dismiss=\"historyRescuePanel\"", html)
        self.assertNotIn("失败自救", html)
        self.assertNotIn("重新自检", html)
        self.assertNotIn("切无本地模型", html)
        self.assertIn("rescue-panel", webui.STYLES_CSS)
        self.assertIn("selectDeepRetry", webui.APP_JS)
        self.assertIn("TASK_REFRESH_TIMER", webui.APP_JS)
        self.assertIn("每 5 秒自动刷新", webui.APP_JS)
        self.assertIn("输入来源", html)
        run_panel = html.split('data-panel="run"', 1)[1].split('data-panel="models"', 1)[0]
        self.assertLess(run_panel.index("输入来源"), run_panel.index("分析模式"))
        self.assertNotIn("普通用户只看这里", run_panel)
        self.assertNotIn("开始前自动检查这 5 件事", run_panel)
        self.assertIn("运行方式", html)
        self.assertIn("报告目标", html)
        self.assertIn("默认观看决策，和分析模式分开", html)
        self.assertIn("文本结构分析", html)
        self.assertIn("知识笔记", html)
        self.assertIn("观点拆解", html)
        self.assertIn("创作复盘", html)
        self.assertIn("分析模式", html)
        self.assertIn("默认 Standard，不懂就不用展开", html)
        self.assertIn("Fast 快速筛选", html)
        self.assertIn("Standard 标准报告", html)
        self.assertIn("Deep 深度分析", html)
        self.assertIn("重新分析、不走旧缓存", html)
        self.assertIn("我的评分规则", html)
        self.assertIn("默认均衡，不懂就不用展开", html)
        self.assertIn("更看重信息量", html)
        self.assertIn("更看重证据", html)
        self.assertIn("自定义评分权重", html)
        self.assertIn("collapsed-choice-block", html)
        self.assertIn("内容理解模型", html)
        self.assertIn("本地通用模型，Gemma / Llama / Mistral 等", html)
        self.assertIn("Gemini 云端理解", html)
        self.assertIn("Claude 云端理解", html)
        self.assertIn("Kimi 云端理解", html)
        self.assertIn("Codex CLI 做内容理解", html)
        self.assertIn('data-extract-panel="qwen"', html)
        self.assertIn('data-extract-panel="external"', html)
        self.assertIn("选择内容理解模型后", html)
        self.assertIn("本地 Qwen 模型", html)
        self.assertIn("内容理解 API key 环境变量", html)
        self.assertIn("观看判断模型", html)
        self.assertIn('data-review-panel="codex"', html)
        self.assertIn('data-review-panel="cloud"', html)
        self.assertIn("没有 Codex 账号就选", html)
        self.assertIn("Codex CLI", html)
        self.assertIn("OpenAI-compatible 云端接口", html)
        self.assertIn("观看判断 API key 环境变量", html)
        self.assertIn("WatchBrief 不让模型直接写 HTML", html)
        self.assertIn("小红书专辑会完整保留内容结构", html)
        self.assertIn("raw metadata 只进 debug/manifest", html)
        self.assertIn("推荐：读取本机后自动选择", html)
        self.assertIn("输出方式", html)
        self.assertIn("选择文件夹", html)
        self.assertIn("输出位置", html)
        self.assertIn("选择输出文件夹", html)
        self.assertIn("google-source-box", html)
        self.assertIn("拖入 txt", html)
        self.assertIn("一行一个链接", html)
        self.assertIn("选择 txt 文件", html)
        self.assertIn("sourceDropZone", webui.APP_JS)
        self.assertIn("createSourceFileFromText", webui.APP_JS)
        self.assertIn("chooseOutputDirectory", webui.APP_JS)
        self.assertIn("chooseUrlFile", webui.APP_JS)
        self.assertIn("updateOutputChoiceLabel", webui.APP_JS)
        self.assertIn("/api/select-directory", webui.APP_JS)
        self.assertIn("/api/select-url-file", webui.APP_JS)
        self.assertIn("/api/source-text-file", webui.APP_JS)
        self.assertIn("applyScoringProfile", webui.APP_JS)
        self.assertIn("taskProgressStrip", webui.APP_JS)
        self.assertIn("clearSourceInputs", webui.APP_JS)
        self.assertIn("已打开任务记录页", webui.APP_JS)
        self.assertIn("showPanel('history')", webui.APP_JS)
        self.assertIn("打开报告", webui.APP_JS)
        self.assertIn("查看日志", webui.APP_JS)
        self.assertIn("data-action=\"open-path\"", webui.APP_JS)
        self.assertIn("/api/open-path", webui.APP_JS)
        self.assertIn("切到 Deep 后重试", webui.APP_JS)
        self.assertIn("停止任务", webui.APP_JS)
        self.assertIn('data-action="stop-task"', webui.APP_JS)
        self.assertIn("stopTask", webui.APP_JS)
        self.assertIn("/stop", webui.APP_JS)
        self.assertIn("task-action.danger", webui.STYLES_CSS)
        self.assertIn('data-action="deep-retry"', webui.APP_JS)
        self.assertIn("已切到 Deep", webui.APP_JS)
        self.assertIn("报告格式", html)
        self.assertIn("PDF（同时保留 HTML）", html)
        self.assertIn("cookies.txt 路径", html)
        self.assertIn("不要填 token", html)

    def test_stop_task_marks_running_task_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks_path = Path(tmp) / "tasks.json"
            log_path = Path(tmp) / "task.log"
            log_path.write_text("running\n", encoding="utf-8")
            tasks_path.write_text(
                json.dumps([
                    {"id": "abc123", "status": "running", "pid": 4321, "log_path": str(log_path)}
                ]),
                encoding="utf-8",
            )
            with patch.object(webui, "TASKS_PATH", tasks_path), patch.object(webui.os, "killpg") as killpg:
                stopped = webui.stop_task("abc123")

            self.assertEqual(stopped["status"], "cancelled")
            killpg.assert_called_once_with(4321, webui.signal.SIGTERM)
            self.assertIn("webui_task_stopped_by_user", log_path.read_text(encoding="utf-8"))
            saved = json.loads(tasks_path.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["status"], "cancelled")

    def test_clear_tasks_preserves_running_and_queued_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks_path = Path(tmp) / "tasks.json"
            tasks_path.write_text(
                json.dumps([
                    {"id": "running-one", "status": "running"},
                    {"id": "queued-one", "status": "queued"},
                    {"id": "done-one", "status": "completed"},
                    {"id": "failed-one", "status": "failed"},
                    {"id": "cancelled-one", "status": "cancelled"},
                ]),
                encoding="utf-8",
            )
            with patch.object(webui, "TASKS_PATH", tasks_path):
                result = webui.clear_tasks()
                stored = json.loads(tasks_path.read_text(encoding="utf-8"))
        self.assertEqual([task["id"] for task in stored], ["running-one", "queued-one"])
        self.assertEqual(result["removed"], 3)
        self.assertEqual(result["kept_running"], 2)

    def test_stale_running_task_is_marked_failed_before_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks_path = Path(tmp) / "tasks.json"
            tasks_path.write_text(
                json.dumps([
                    {
                        "id": "stale-bili-list",
                        "source": "https://space.bilibili.com/163343210/favlist?fid=3958254310&ftype=create",
                        "status": "running",
                        "pid": 987654321,
                        "log_path": "/tmp/stale.log",
                        "updated_at": 30,
                    }
                ], ensure_ascii=False),
                encoding="utf-8",
            )
            with (
                patch.object(webui, "TASKS_PATH", tasks_path),
                patch.object(webui, "_pid_is_running", return_value=False),
            ):
                tasks = webui.tasks_for_api()

        self.assertEqual(tasks[0]["status"], "failed")
        self.assertIn("任务进程已不存在", tasks[0]["error"])

    def test_open_path_from_payload_opens_existing_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "task.log"
            target.write_text("hello", encoding="utf-8")
            with patch.object(webui, "open_local_paths") as open_paths:
                result = webui.open_path_from_payload({"path": str(target)})
        self.assertEqual(result["opened"], str(target))
        open_paths.assert_called_once_with([target])

    def test_codex_review_provider_is_default_when_codex_is_ready(self) -> None:
        with patch.object(webui, "codex_cli_status", return_value={"gpt55_ready": True}):
            command = webui.build_cli_command({"source_url": "https://example.com/v"})

        self.assertIn("--review-provider", command)
        self.assertIn("codex-cli", command)
        self.assertIn("--enable-codex-review", command)
        self.assertIn("--codex-model", command)

    def test_local_review_provider_is_only_default_when_codex_is_not_ready(self) -> None:
        with patch.object(webui, "codex_cli_status", return_value={"gpt55_ready": False}):
            command = webui.build_cli_command({"source_url": "https://example.com/v"})

        self.assertIn("--review-provider", command)
        self.assertIn("local", command)
        self.assertNotIn("--enable-codex-review", command)
        self.assertNotIn("--codex-model", command)

    def test_analysis_mode_maps_to_first_class_cli_flag_and_strategy(self) -> None:
        fast = webui.build_cli_command({"source_url": "https://example.com/v", "analysis_mode": "fast"})
        standard = webui.build_cli_command({"source_url": "https://example.com/v", "analysis_mode": "standard"})
        deep = webui.build_cli_command({"source_url": "https://example.com/v", "analysis_mode": "deep"})

        self.assertIn("--timeout", fast)
        self.assertIn("600", fast)
        self.assertIn("900", standard)
        self.assertIn("1200", deep)
        self.assertIn("--force-reanalysis", deep)
        self.assertIn("--keep-debug-artifacts", deep)
        self.assertNotIn("--force-reanalysis", fast)
        self.assertNotIn("--keep-debug-artifacts", standard)
        self.assertIn("--analysis-mode", fast)
        self.assertIn("fast", fast)
        self.assertIn("standard", standard)
        self.assertIn("deep", deep)
        self.assertIn("--report-target", standard)
        self.assertIn("watch_decision", standard)

        deep_manual = webui.build_cli_command(
            {
                "source_url": "https://example.com/v",
                "analysis_mode": "deep",
                "force_reanalysis": "false",
                "keep_debug_artifacts": "false",
            }
        )
        self.assertNotIn("--force-reanalysis", deep_manual)
        self.assertNotIn("--keep-debug-artifacts", deep_manual)

        with self.assertRaisesRegex(ValueError, "分析模式"):
            webui.build_cli_command({"source_url": "https://example.com/v", "analysis_mode": "legacy"})

    def test_report_target_maps_to_cli_flag(self) -> None:
        command = webui.build_cli_command({"source_url": "https://example.com/v", "report_target": "viewpoint_breakdown"})

        self.assertIn("--report-target", command)
        self.assertIn("viewpoint_breakdown", command)

        with self.assertRaisesRegex(ValueError, "报告目标"):
            webui.build_cli_command({"source_url": "https://example.com/v", "report_target": "legacy"})

    def test_multi_report_targets_expand_to_multiple_preview_commands(self) -> None:
        payload = {
            "source_url": "https://example.com/v",
            "report_targets": ["watch_decision", "knowledge_notes", "creation_review"],
        }

        self.assertEqual(webui.parse_report_targets(payload), ["watch_decision", "knowledge_notes", "creation_review"])
        preview = webui.command_preview(payload)

        self.assertEqual(preview["report_targets"], ["watch_decision", "knowledge_notes", "creation_review"])
        self.assertEqual(len(preview["commands"]), 3)
        self.assertIn("watch_decision", preview["commands"][0])
        self.assertIn("knowledge_notes", preview["commands"][1])
        self.assertIn("creation_review", preview["commands"][2])

    def test_scoring_profile_maps_to_cli_with_first_class_analysis_flag(self) -> None:
        command = webui.build_cli_command({"source_url": "https://example.com/v", "scoring_profile": "evidence-first"})

        self.assertIn("--scoring-profile", command)
        self.assertIn("evidence-first", command)
        self.assertIn("--analysis-mode", command)
        self.assertIn("standard", command)

        custom = webui.build_cli_command({
            "source_url": "https://example.com/v",
            "scoring_profile": "custom",
            "scoring_weights": "information_density=2,evidence_quality=3,originality=2,watch_value=3",
        })
        self.assertIn("--scoring-profile", custom)
        self.assertIn("custom", custom)
        self.assertIn("--scoring-weights", custom)
        self.assertIn("information_density=2,evidence_quality=3,originality=2,watch_value=3", custom)

        with self.assertRaisesRegex(ValueError, "评分规则"):
            webui.build_cli_command({"source_url": "https://example.com/v", "scoring_profile": "legacy"})
        with self.assertRaisesRegex(ValueError, "自定义评分"):
            webui.build_cli_command({"source_url": "https://example.com/v", "scoring_profile": "custom"})

    def test_task_progress_snapshot_reads_manifest_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            item_manifest_path = Path(tmp) / "item_manifest.json"
            log_path = Path(tmp) / "task.log"
            manifest_path.write_text(json.dumps({
                "total_count": 5,
                "completed_count": 2,
                "failed_count": 1,
                "skipped_count": 0,
                "items": [
                    {"status": "completed", "title": "A"},
                    {"status": "completed", "title": "B"},
                    {"status": "failed", "title": "C"},
                    {"status": "running", "title": "D", "item_manifest_path": str(item_manifest_path)},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            item_manifest_path.write_text(json.dumps({
                "steps": [
                    {"step": "resolver", "status": "completed"},
                    {"step": "local_extract", "status": "started"},
                ]
            }, ensure_ascii=False), encoding="utf-8")
            log_path.write_text(f"manifest_path: {manifest_path}\n", encoding="utf-8")

            progress = webui.task_progress_snapshot({"status": "running", "log_path": str(log_path)})

        self.assertEqual(progress["total"], 5)
        self.assertEqual(progress["finished"], 3)
        self.assertEqual(progress["percent"], 60)
        self.assertEqual(progress["active_label"], "内容理解")
        self.assertEqual(progress["active_title"], "D")

    def test_extract_delivery_paths_from_task_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "task.log"
            log_path.write_text(
                "[completed] one -> /tmp/out/01-one.html\n"
                "HTML: /tmp/out/02-two.html\n"
                "Watch Order: /tmp/out/00-watch-order.html\n"
                "debug_artifacts: /tmp/debug\n",
                encoding="utf-8",
            )

            paths = webui.extract_delivery_paths(log_path)

        self.assertEqual(paths["html_paths"], ["/tmp/out/01-one.html", "/tmp/out/02-two.html"])
        self.assertEqual(paths["watch_order_paths"], ["/tmp/out/00-watch-order.html"])
        self.assertEqual(paths["debug_paths"], ["/tmp/debug"])

    def test_environment_diagnostics_classifies_model_sources_without_tokens(self) -> None:
        fake_capabilities = {
            "local_model": {"ok": False, "qwen_ok": False},
            "transcriber": {
                "mlx_audio": {"ok": False},
                "whisper": {"ok": True, "path": "/usr/local/bin/whisper"},
                "recommended": "whisper",
                "recommendation_reason": "未检测到 MLX-Audio，WebUI 默认改用 Whisper",
            },
            "review": {
                "codex_cli_ok": False,
                "codex_cli_path": "",
                "codex_cli_version": "",
                "codex_cli_gpt55_ready": None,
                "codex_cli_warning": "",
                "api_key_envs": {
                    "gemini": {"env": "GEMINI_API_KEY", "configured": True},
                    "claude": {"env": "ANTHROPIC_API_KEY", "configured": False},
                },
            },
            "report": {"pdf": {"ok": False, "browser_path": ""}},
            "directory_picker": {"available": True},
            "paths": {"desktop": "/Users/example/Desktop"},
        }
        with patch.object(webui, "local_capabilities", return_value=fake_capabilities):
            diagnostics = webui.environment_diagnostics()

        self.assertTrue(diagnostics["ok"])
        self.assertIn("可以开始正式任务", diagnostics["summary"])
        joined = "\n".join(item["detail"] + item.get("action", "") for item in diagnostics["checks"])
        self.assertIn("GEMINI_API_KEY", joined)
        self.assertIn("Whisper", joined)
        self.assertNotIn("sk-", joined)
        self.assertEqual(diagnostics["fail_count"], 0)

    def test_environment_diagnostics_warns_about_old_codex_gpt55_without_tokens(self) -> None:
        fake_capabilities = {
            "local_model": {"ok": False, "qwen_ok": False},
            "transcriber": {"mlx_audio": {"ok": True}, "whisper": {"ok": False}, "recommended": "auto"},
            "review": {
                "codex_cli_ok": True,
                "codex_cli_path": "/opt/homebrew/bin/codex",
                "codex_cli_version": "codex-cli 0.118.0",
                "codex_cli_gpt55_ready": False,
                "codex_cli_warning": "当前 Codex CLI 版本已知无法稳定调用 gpt-5.5；请升级 Codex CLI 后再用 Codex 判断。",
                "api_key_envs": {},
            },
            "report": {"pdf": {"ok": True, "browser_path": "/Applications/Google Chrome.app"}},
            "directory_picker": {"available": True},
            "paths": {"desktop": "/Users/example/Desktop"},
        }
        with patch.object(webui, "local_capabilities", return_value=fake_capabilities):
            diagnostics = webui.environment_diagnostics()

        joined = "\n".join(item["name"] + item["state"] + item["detail"] + item.get("action", "") for item in diagnostics["checks"])
        self.assertIn("Codex CLI", joined)
        self.assertIn("warn", joined)
        self.assertIn("gpt-5.5", joined)
        self.assertNotIn("sk-", joined)

    def test_release_readiness_exposes_worklog_rollback_and_commands(self) -> None:
        readiness = webui.release_readiness_status()

        self.assertIn("发布整理清单", readiness["summary"])
        self.assertIn("git apply -R", readiness["rollback_command"])
        names = [item["name"] for item in readiness["checks"]]
        self.assertIn("工作日志", names)
        self.assertIn("回退 patch", names)
        self.assertIn("测试命令", names)
        self.assertTrue(any("test_webui.py" in item["detail"] for item in readiness["checks"]))

    def test_codex_cli_default_model_is_gpt_55(self) -> None:
        command = webui.build_cli_command({"source_url": "https://example.com/v", "review_provider": "codex-cli"})

        self.assertIn("--codex-model", command)
        self.assertIn("gpt-5.5", command)

    def test_build_cli_command_uses_custom_settings_without_opening_browser(self) -> None:
        command = webui.build_cli_command(
            {
                "source_url": "https://www.xiaohongshu.com/explore/mock",
                "review_provider": "codex-cli",
                "codex_model": "gpt-5.5",
                "codex_home_root": "~/.watchbrief_codex",
                "codex_account": "account2",
                "qwen_model": "qwen3-custom-mlx",
                "qwen_api_base": "http://127.0.0.1:1234/v1",
                "qwen_timeout": 900,
                "timeout": 600,
                "browser_auth": "auto",
                "force_reanalysis": True,
                "report_format": "html",
                "renderer": "local-html",
            }
        )

        self.assertEqual(command[0], webui.python_executable())
        self.assertIn("--source-url", command)
        self.assertIn("https://www.xiaohongshu.com/explore/mock", command)
        self.assertIn("--review-provider", command)
        self.assertIn("codex-cli", command)
        self.assertIn("--enable-codex-review", command)
        self.assertIn("--codex-model", command)
        self.assertIn("gpt-5.5", command)
        self.assertIn("--codex-account", command)
        self.assertIn("account2", command)
        self.assertIn("--qwen-model", command)
        self.assertIn("qwen3-custom-mlx", command)
        self.assertIn("--qwen-api-base", command)
        self.assertIn("http://127.0.0.1:1234/v1", command)
        self.assertIn("--qwen-timeout", command)
        self.assertIn("900", command)
        self.assertIn("--timeout", command)
        self.assertIn("600", command)
        self.assertIn("--force-reanalysis", command)
        self.assertNotIn("--open-output", command)
        self.assertNotIn("--output-dir", command)
        self.assertNotIn("--cookies-from-browser", command)

    def test_build_cli_command_supports_explicit_browser_output_and_transcriber(self) -> None:
        command = webui.build_cli_command(
            {
                "source_file": "/tmp/watchbrief-urls.txt",
                "review_provider": "codex-cli",
                "browser_auth": "safari",
                "output_dir": "/tmp/out",
                "transcriber": "whisper",
                "whisper_model": "small",
                "allow_whisper_fallback": True,
                "open_output": True,
            }
        )

        self.assertIn("--source-file", command)
        self.assertIn("/tmp/watchbrief-urls.txt", command)
        self.assertIn("--cookies-from-browser", command)
        self.assertIn("safari", command)
        self.assertIn("--output-dir", command)
        self.assertIn("/tmp/out", command)
        self.assertIn("--transcriber", command)
        self.assertIn("whisper", command)
        self.assertIn("--whisper-model", command)
        self.assertIn("small", command)
        self.assertIn("--allow-whisper-fallback", command)
        self.assertIn("--open-output", command)
        self.assertNotIn("--source-url", command)

    def test_recommended_transcriber_uses_whisper_when_mlx_audio_is_missing(self) -> None:
        original = webui.local_capabilities
        try:
            webui.local_capabilities = lambda force=False: {
                "transcriber": {"recommended": "whisper"}
            }
            command = webui.build_cli_command(
                {
                    "source_url": "https://example.com/v",
                    "transcriber": "recommended",
                }
            )
        finally:
            webui.local_capabilities = original

        self.assertIn("--transcriber", command)
        self.assertIn("whisper", command)

    def test_recommended_transcriber_keeps_cli_auto_when_mlx_audio_exists(self) -> None:
        original = webui.local_capabilities
        try:
            webui.local_capabilities = lambda force=False: {
                "transcriber": {"recommended": "auto"}
            }
            command = webui.build_cli_command(
                {
                    "source_url": "https://example.com/v",
                    "transcriber": "recommended",
                }
            )
        finally:
            webui.local_capabilities = original

        self.assertNotIn("--transcriber", command)

    def test_output_mode_supports_custom_and_diagnostic_defaults(self) -> None:
        custom = webui.build_cli_command(
            {
                "source_url": "https://example.com/v",
                "output_mode": "custom",
                "output_dir": "/tmp/watchbrief-final",
            }
        )
        diagnostic = webui.build_cli_command(
            {
                "source_url": "https://example.com/v",
                "output_mode": "diagnostic",
            }
        )

        self.assertIn("--output-dir", custom)
        self.assertIn("/tmp/watchbrief-final", custom)
        self.assertIn("--diagnostic-run", diagnostic)
        self.assertNotIn("--output-dir", diagnostic)

        with self.assertRaisesRegex(ValueError, "output_dir"):
            webui.build_cli_command(
                {
                    "source_url": "https://example.com/v",
                    "output_mode": "custom",
                }
            )

    def test_choose_output_directory_uses_macos_folder_picker(self) -> None:
        calls = []

        def fake_runner(command, capture_output, text, timeout):
            calls.append(command)

            class Result:
                returncode = 0
                stdout = "/tmp/watchbrief-output\n"
                stderr = ""

            return Result()

        selected = webui.choose_output_directory(
            runner=fake_runner,
            osascript_path="/usr/bin/osascript",
            platform="darwin",
        )

        self.assertEqual(selected, "/tmp/watchbrief-output")
        self.assertEqual(calls[0][0], "/usr/bin/osascript")
        self.assertIn("choose folder", calls[0][2])

    def test_choose_output_directory_reports_unsupported_platform(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "手动输入"):
            webui.choose_output_directory(platform="linux", osascript_path="/usr/bin/osascript")

    def test_choose_url_file_uses_macos_file_picker(self) -> None:
        calls = []

        def fake_runner(command, capture_output, text, timeout):
            calls.append(command)

            class Result:
                returncode = 0
                stdout = "/tmp/urls.txt\n"
                stderr = ""

            return Result()

        selected = webui.choose_url_file(
            runner=fake_runner,
            osascript_path="/usr/bin/osascript",
            platform="darwin",
        )

        self.assertEqual(selected, "/tmp/urls.txt")
        self.assertEqual(calls[0][0], "/usr/bin/osascript")
        self.assertIn("choose file", calls[0][2])
        self.assertIn("txt", calls[0][2])

    def test_choose_url_file_reports_unsupported_platform(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "手动输入"):
            webui.choose_url_file(platform="linux", osascript_path="/usr/bin/osascript")

    def test_mock_review_requires_json_path_and_does_not_enable_codex(self) -> None:
        command = webui.build_cli_command(
            {
                "source_url": "https://www.youtube.com/watch?v=mock",
                "review_provider": "mock",
                "mock_review_response": "/tmp/mock-payload.json",
            }
        )

        self.assertIn("--review-provider", command)
        self.assertIn("mock", command)
        self.assertIn("--mock-review-response", command)
        self.assertIn("/tmp/mock-payload.json", command)
        self.assertNotIn("--enable-codex-review", command)

    def test_cloud_review_provider_is_forwarded_without_codex_enablement(self) -> None:
        command = webui.build_cli_command(
            {
                "source_url": "https://example.com/v",
                "review_provider": "gemini",
                "review_model": "gemini-2.5-flash",
                "review_api_key_env": "GEMINI_API_KEY",
            }
        )

        self.assertIn("--review-provider", command)
        self.assertIn("gemini", command)
        self.assertIn("--review-model", command)
        self.assertIn("gemini-2.5-flash", command)
        self.assertIn("--review-api-base", command)
        self.assertIn("https://generativelanguage.googleapis.com/v1beta", command)
        self.assertIn("--review-api-key-env", command)
        self.assertIn("GEMINI_API_KEY", command)
        self.assertNotIn("--enable-codex-review", command)

    def test_claude_review_uses_grouped_default_model_and_env(self) -> None:
        command = webui.build_cli_command(
            {
                "source_url": "https://example.com/v",
                "review_provider": "claude",
            }
        )

        self.assertIn("--review-provider", command)
        self.assertIn("claude", command)
        self.assertIn("--review-model", command)
        self.assertIn("claude-sonnet-4-20250514", command)
        self.assertIn("--review-api-base", command)
        self.assertIn("https://api.anthropic.com/v1", command)
        self.assertIn("--review-api-key-env", command)
        self.assertIn("ANTHROPIC_API_KEY", command)
        self.assertNotIn("--codex-home-root", command)
        self.assertNotIn("--enable-codex-review", command)

    def test_local_openai_compatible_extract_supports_non_qwen_model(self) -> None:
        command = webui.build_cli_command(
            {
                "source_url": "https://example.com/v",
                "extract_provider": "local-openai-compatible",
                "extract_model": "gemma-3-local",
                "qwen_api_base": "http://127.0.0.1:1234/v1",
            }
        )

        self.assertIn("--extract-provider", command)
        self.assertIn("local-openai-compatible", command)
        self.assertIn("--extract-model", command)
        self.assertIn("gemma-3-local", command)
        self.assertIn("--extract-api-base", command)
        self.assertIn("http://127.0.0.1:1234/v1", command)
        self.assertNotIn("--qwen-model", command)

    def test_gemini_extract_uses_env_name_not_token_value(self) -> None:
        command = webui.build_cli_command(
            {
                "source_url": "https://example.com/v",
                "extract_provider": "gemini",
                "extract_api_key_env": "GEMINI_API_KEY",
            }
        )

        self.assertIn("--extract-provider", command)
        self.assertIn("gemini", command)
        self.assertIn("--extract-model", command)
        self.assertIn("gemini-2.5-flash", command)
        self.assertIn("--extract-api-base", command)
        self.assertIn("https://generativelanguage.googleapis.com/v1beta", command)
        self.assertIn("--extract-api-key-env", command)
        self.assertIn("GEMINI_API_KEY", command)
        self.assertNotIn("SECRET", " ".join(command))

        with self.assertRaisesRegex(ValueError, "环境变量名字"):
            webui.build_cli_command(
                {
                    "source_url": "https://example.com/v",
                    "review_provider": "gemini",
                    "review_api_key_env": "sk-real-token-value",
                }
            )

    def test_codex_extract_uses_codex_account_group_without_codex_review(self) -> None:
        command = webui.build_cli_command(
            {
                "source_url": "https://example.com/v",
                "extract_provider": "codex-cli-extract",
                "review_provider": "local",
            }
        )

        self.assertIn("--extract-provider", command)
        self.assertIn("codex-cli-extract", command)
        self.assertIn("--extract-model", command)
        self.assertIn("gpt-5.5", command)
        self.assertIn("--codex-home-root", command)
        self.assertIn("~/.watchbrief_codex", command)
        self.assertIn("--codex-account", command)
        self.assertIn("account2", command)
        self.assertNotIn("--enable-codex-review", command)

    def test_unsupported_provider_and_renderer_fail_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "gemini-extract|提炼"):
            webui.build_cli_command({"source_url": "https://example.com/v", "extract_provider": "gemini-extract"})
        with self.assertRaisesRegex(ValueError, "renderer"):
            webui.build_cli_command({"source_url": "https://example.com/v", "renderer": "unknown-renderer"})

    def test_pdf_report_format_is_post_process_not_cli_flag(self) -> None:
        preview = webui.command_preview(
            {
                "source_url": "https://example.com/v",
                "report_format": "pdf",
                "renderer": "pdf-export",
                "open_output": True,
            }
        )

        self.assertTrue(preview["pdf_export"])
        self.assertNotIn("--report-format", preview["command"])
        self.assertNotIn("pdf", preview["command"])
        self.assertNotIn("--open-output", preview["command"])

    def test_non_qwen_model_is_rejected_before_cli_launch(self) -> None:
        with self.assertRaisesRegex(ValueError, "Qwen"):
            webui.build_cli_command({"source_url": "https://example.com/v", "qwen_model": "llama-local"})

    def test_command_preview_has_cwd_and_command(self) -> None:
        preview = webui.command_preview({"source_url": "https://example.com/v"})

        self.assertEqual(preview["cwd"], str(webui.PROJECT_ROOT))
        self.assertIn(str(webui.CLI_PATH), preview["command"])
        self.assertFalse(preview["pdf_export"])

    def test_html_log_paths_and_pdf_export_use_headless_browser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "01-video.html"
            second = root / "00-watch-order.html"
            first.write_text("<html><body>one</body></html>", encoding="utf-8")
            second.write_text("<html><body>order</body></html>", encoding="utf-8")
            log_text = f"[completed] video -> {first}\nWatch Order: {second}\n"
            paths = webui.html_paths_from_log(log_text)

            def fake_runner(command, capture_output, text, timeout):
                self.assertIn("--headless", command)
                pdf_arg = next(item for item in command if str(item).startswith("--print-to-pdf="))
                Path(str(pdf_arg).split("=", 1)[1]).write_bytes(b"%PDF-1.4\n")

                class Result:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return Result()

            pdf_path = webui.export_html_to_pdf(paths[0], browser=Path("/tmp/fake-browser"), runner=fake_runner)

            self.assertEqual(paths, [first, second])
            self.assertEqual(pdf_path, first.with_suffix(".pdf"))
            self.assertTrue(pdf_path.exists())

    def test_token_like_fields_are_not_forwarded_to_cli_command(self) -> None:
        command = webui.build_cli_command(
            {
                "source_url": "https://example.com/v",
                "token": "SECRET_TOKEN",
                "api_key": "SECRET_API_KEY",
                "claude_token": "SECRET_CLAUDE",
                "kimi_token": "SECRET_KIMI",
                "codex_token": "SECRET_CODEX",
            }
        )

        joined = " ".join(command)
        self.assertNotIn("SECRET_TOKEN", joined)
        self.assertNotIn("SECRET_API_KEY", joined)
        self.assertNotIn("SECRET_CLAUDE", joined)
        self.assertNotIn("SECRET_KIMI", joined)
        self.assertNotIn("SECRET_CODEX", joined)

    def test_failure_summary_humanizes_common_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "task.log"
            log_path.write_text("stage=transcript_quality reason_code=transcript_coverage_too_low", encoding="utf-8")
            self.assertIn("覆盖率太低", webui.summarize_task_failure(log_path, 1))

            log_path.write_text("LocalQwenError: local_qwen_timeout", encoding="utf-8")
            self.assertIn("本地 Qwen 超时", webui.summarize_task_failure(log_path, 1))

    def test_status_exposes_local_capabilities_without_tokens(self) -> None:
        original = webui.local_capabilities
        try:
            webui.local_capabilities = lambda force=False: {
                "local_model": {
                    "ok": False,
                    "qwen_ok": False,
                    "base_url": "http://127.0.0.1:1234/v1",
                    "models": [],
                    "qwen_models": [],
                    "error": "connection refused",
                },
                "transcriber": {
                    "recommended": "whisper",
                    "mlx_audio": {"ok": False, "selected_python": "", "candidates": []},
                    "whisper": {"ok": True, "path": "/usr/local/bin/whisper"},
                    "recommendation_reason": "未检测到 MLX-Audio，WebUI 默认改用 Whisper",
                },
                "review": {
                    "codex_cli_ok": True,
                    "codex_cli_path": "/usr/local/bin/codex",
                    "supported": ["local", "codex-cli", "gemini", "claude", "kimi", "openai-compatible", "mock"],
                    "placeholders": ["manual-review-ui"],
                    "api_key_envs": {
                        "gemini": {"env": "GEMINI_API_KEY", "configured": False},
                        "claude": {"env": "ANTHROPIC_API_KEY", "configured": False},
                        "kimi": {"env": "MOONSHOT_API_KEY", "configured": False},
                        "openai_compatible": {"env": "WATCHBRIEF_OPENAI_COMPATIBLE_API_KEY", "configured": False},
                    },
                },
                "report": {"formats": [], "renderers": []},
                "paths": {
                    "desktop": "/Users/example/Desktop",
                    "downloads": "/Users/example/Downloads",
                    "documents": "/Users/example/Documents",
                    "webui_state": "/Users/example/.watchbrief/webui",
                    "project_root": "/repo/watchbrief_v5",
                },
                "directory_picker": {"available": True, "method": "macos_osascript"},
            }
            status = webui.service_status()
        finally:
            webui.local_capabilities = original

        self.assertFalse(status["local_model"]["qwen_ok"])
        self.assertEqual(status["transcriber"]["recommended"], "whisper")
        self.assertEqual(status["paths"]["desktop"], "/Users/example/Desktop")
        self.assertEqual(status["directory_picker"]["method"], "macos_osascript")
        joined = str(status)
        self.assertNotIn("SECRET", joined)
        self.assertNotIn("token", joined.lower())


if __name__ == "__main__":
    unittest.main()
