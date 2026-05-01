from __future__ import annotations

import unittest

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts import webui


class WebUITest(unittest.TestCase):
    def test_static_shell_exposes_customization_sections(self) -> None:
        html = webui.render_index_html()

        self.assertIn("WatchBrief WebUI", html)
        self.assertIn("新建任务", html)
        self.assertIn("模型与账号", html)
        self.assertIn("输出与登录态", html)
        self.assertIn("任务记录", html)
        self.assertIn("本地 Qwen 模型", html)
        self.assertIn("Review 引擎", html)
        self.assertIn("Codex CLI", html)
        self.assertIn("Claude：未接入", html)
        self.assertIn("Kimi：未接入", html)
        self.assertIn("报告格式", html)
        self.assertIn("PDF：未接入", html)
        self.assertIn("cookies.txt 路径", html)

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

    def test_unsupported_provider_and_pdf_fail_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "Claude|claude"):
            webui.build_cli_command({"source_url": "https://example.com/v", "review_provider": "claude"})
        with self.assertRaisesRegex(ValueError, "PDF"):
            webui.build_cli_command({"source_url": "https://example.com/v", "report_format": "pdf"})
        with self.assertRaisesRegex(ValueError, "renderer"):
            webui.build_cli_command({"source_url": "https://example.com/v", "renderer": "pdf-export"})

    def test_non_qwen_model_is_rejected_before_cli_launch(self) -> None:
        with self.assertRaisesRegex(ValueError, "Qwen"):
            webui.build_cli_command({"source_url": "https://example.com/v", "qwen_model": "llama-local"})

    def test_command_preview_has_cwd_and_command(self) -> None:
        preview = webui.command_preview({"source_url": "https://example.com/v"})

        self.assertEqual(preview["cwd"], str(webui.PROJECT_ROOT))
        self.assertIn(str(webui.CLI_PATH), preview["command"])


if __name__ == "__main__":
    unittest.main()
