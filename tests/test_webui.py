from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts import webui


class WebUITest(unittest.TestCase):
    def test_static_shell_exposes_customization_sections(self) -> None:
        html = webui.render_index_html()

        self.assertIn("WatchBrief WebUI", html)
        self.assertIn("LOCAL-FIRST VIDEO REPORT", html)
        self.assertIn("默认推荐配置已准备好", html)
        self.assertIn("隐藏当前配置", html)
        self.assertIn('id="toggleInspector"', html)
        self.assertIn("inspector-hidden", webui.STYLES_CSS)
        self.assertIn("toggleInspector", webui.APP_JS)
        self.assertIn("完全不懂代码也照着做", html)
        self.assertIn("WatchBrief 不保存你的密码，不展示 cookie", html)
        self.assertIn("本地模式（无 Codex）", html)
        self.assertIn("新建任务", html)
        self.assertIn("模型与账号", html)
        self.assertIn("输出与登录态", html)
        self.assertIn("任务记录", html)
        self.assertIn("输入来源", html)
        self.assertIn("运行方式", html)
        self.assertIn("提炼模型后端", html)
        self.assertIn("本地通用模型：Gemma / Llama / Mistral", html)
        self.assertIn("Gemini API", html)
        self.assertIn("Claude API", html)
        self.assertIn("Kimi API", html)
        self.assertIn("Codex CLI 提炼", html)
        self.assertIn("本地 Qwen 模型", html)
        self.assertIn("提炼 API key 环境变量", html)
        self.assertIn("Review 引擎", html)
        self.assertIn("没有 Codex 账号就选", html)
        self.assertIn("Codex CLI", html)
        self.assertIn("OpenAI-compatible API", html)
        self.assertIn("Review API key 环境变量", html)
        self.assertIn("推荐：读取本机后自动选择", html)
        self.assertIn("输出方式", html)
        self.assertIn("报告格式", html)
        self.assertIn("PDF（同时保留 HTML）", html)
        self.assertIn("cookies.txt 路径", html)
        self.assertIn("不要填 token", html)

    def test_local_review_provider_is_default_without_codex_enablement(self) -> None:
        command = webui.build_cli_command({"source_url": "https://example.com/v"})

        self.assertIn("--review-provider", command)
        self.assertIn("local", command)
        self.assertNotIn("--enable-codex-review", command)
        self.assertNotIn("--codex-model", command)

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
        self.assertIn("--review-api-key-env", command)
        self.assertIn("GEMINI_API_KEY", command)
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
            }
            status = webui.service_status()
        finally:
            webui.local_capabilities = original

        self.assertFalse(status["local_model"]["qwen_ok"])
        self.assertEqual(status["transcriber"]["recommended"], "whisper")
        self.assertEqual(status["paths"]["desktop"], "/Users/example/Desktop")
        joined = str(status)
        self.assertNotIn("SECRET", joined)
        self.assertNotIn("token", joined.lower())


if __name__ == "__main__":
    unittest.main()
