from __future__ import annotations

import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from helpers import ROOT
from scripts.acquisition_errors import BilibiliSubtitleError, PlatformRestrictionError, SubtitleUnavailableError
from scripts.subtitle_fetcher import (
    build_youtube_subtitle_candidates,
    fetch_platform_subtitles,
    parse_youtube_subtitle_listing,
)


VTT_CONTENT = """WEBVTT

00:00:00.000 --> 00:00:02.000
hello subtitle
"""


def write_subtitle_for_command(command: list[str], language: str, content: str = VTT_CONTENT) -> Path:
    output_index = command.index("--output") + 1
    output_dir = Path(command[output_index]).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"abc.{language}.vtt"
    path.write_text(content, encoding="utf-8")
    return path


class AcquisitionSubtitleFetcherTest(unittest.TestCase):
    def test_fetch_platform_subtitles_returns_transcript_material(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def run(command, capture_output, text, timeout):
                (output_dir / "abc.zh.vtt").write_text(VTT_CONTENT, encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = fetch_platform_subtitles("https://example.com/watch/abc", output_dir, runner=run)

            self.assertEqual(result.subtitle_path.name, "abc.zh.vtt")
            self.assertEqual(result.material["material_version"], "watchbrief_v5.transcript_material.v1")
            self.assertEqual(result.material["source"]["kind"], "subtitle_vtt")
            self.assertEqual(result.material["segments"][0]["start"], "00:00")
            self.assertTrue(result.material["adapter_boundary"]["no_audio_download"])

    def test_no_subtitle_files_is_subtitle_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with self.assertRaises(SubtitleUnavailableError) as context:
                fetch_platform_subtitles("https://example.com/watch/no-sub", Path(temp_dir), runner=run)
            self.assertEqual(context.exception.reason_code, "subtitle_unavailable")

    def test_platform_restriction_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 403: Forbidden")

            with self.assertRaises(PlatformRestrictionError) as context:
                fetch_platform_subtitles("https://example.com/watch/blocked", Path(temp_dir), runner=run)
            self.assertEqual(context.exception.reason_code, "platform_restriction")

    def test_explicit_browser_cookies_are_passed_to_ytdlp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            seen_command: list[str] = []

            def run(command, capture_output, text, timeout):
                seen_command.extend(command)
                if "--list-subs" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="[info] Available subtitles for abc:\nLanguage Name Formats\nen English vtt, srt\n",
                        stderr="",
                    )
                write_subtitle_for_command(command, "en")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = fetch_platform_subtitles(
                "https://www.youtube.com/watch?v=abc",
                Path(temp_dir),
                cookies_from_browser="chrome",
                runner=run,
            )

            self.assertEqual(result.language, "en")
            self.assertIn("--cookies-from-browser", seen_command)
            self.assertIn("chrome", seen_command)

    def test_youtube_default_browser_auth_falls_back_from_chrome_to_safari(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            seen_commands: list[list[str]] = []

            def run(command, capture_output, text, timeout):
                seen_commands.append(list(command))
                if "chrome" in command:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="Extracted 0 cookies from chrome")
                if "--list-subs" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="[info] Available subtitles for abc:\nLanguage Name Formats\nen English vtt, srt\n",
                        stderr="",
                    )
                write_subtitle_for_command(command, "en")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = fetch_platform_subtitles(
                "https://www.youtube.com/watch?v=abc",
                Path(temp_dir),
                cookie_browser_attempts=["chrome", "safari"],
                runner=run,
            )

            self.assertEqual(result.language, "en")
            self.assertIn("chrome", seen_commands[0])
            self.assertTrue(any("safari" in command for command in seen_commands[1:]))
            self.assertEqual(result.debug["cookies_browser_attempts"], ["chrome", "safari"])
            self.assertEqual(result.debug["selected_cookies_browser"], "safari")
            self.assertEqual(result.debug["cookies_fallback_reason"], "extracted_0_cookies")

    def test_youtube_uses_en_manual_without_zh_aggregate_request(self) -> None:
        seen_sub_langs: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                if "--list-subs" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=(
                            "[info] Available automatic captions for abc:\n"
                            "Language Name Formats\n"
                            "zh-Hans Chinese (Simplified) vtt, srt\n"
                            "[info] Available subtitles for abc:\n"
                            "Language Name Formats\n"
                            "en English vtt, srt\n"
                        ),
                        stderr="",
                    )
                language = command[command.index("--sub-langs") + 1]
                seen_sub_langs.append(language)
                if language == "zh-Hans":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 429: Too Many Requests")
                write_subtitle_for_command(command, language)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = fetch_platform_subtitles(
                "https://www.youtube.com/watch?v=abc",
                Path(temp_dir),
                languages=("zh-Hans", "zh", "en"),
                cookies_from_browser="chrome",
                runner=run,
            )

            self.assertEqual(result.language, "en")
            self.assertEqual(result.debug["selected_subtitle_kind"], "manual")
            self.assertEqual(result.debug["selected_subtitle_format"], "vtt")
            self.assertEqual(seen_sub_langs, ["en"])
            self.assertNotIn("zh-Hans,zh,en", seen_sub_langs)

    def test_youtube_candidate_failure_continues_to_next_english_candidate(self) -> None:
        seen_sub_langs: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                if "--list-subs" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=(
                            "[info] Available subtitles for abc:\n"
                            "Language Name Formats\n"
                            "en-US English (United States) vtt, srt\n"
                            "en-GB English (United Kingdom) vtt, srt\n"
                        ),
                        stderr="",
                    )
                language = command[command.index("--sub-langs") + 1]
                seen_sub_langs.append(language)
                if language == "en-US":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 429: Too Many Requests")
                write_subtitle_for_command(command, language)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = fetch_platform_subtitles(
                "https://www.youtube.com/watch?v=abc",
                Path(temp_dir),
                runner=run,
            )

            self.assertEqual(result.language, "en-GB")
            self.assertEqual(seen_sub_langs, ["en-US", "en-GB"])
            self.assertEqual(result.debug["subtitle_candidate_failures"][0]["language"], "en-US")
            self.assertIn("429", result.debug["subtitle_candidate_failures"][0]["stderr"])

    def test_youtube_automatic_english_caption_is_selected_after_manual(self) -> None:
        seen_download_command: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                if "--list-subs" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=(
                            "[info] Available automatic captions for abc:\n"
                            "Language Name Formats\n"
                            "en English vtt, srt\n"
                        ),
                        stderr="",
                    )
                seen_download_command.extend(command)
                language = command[command.index("--sub-langs") + 1]
                write_subtitle_for_command(command, language)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = fetch_platform_subtitles(
                "https://www.youtube.com/watch?v=abc",
                Path(temp_dir),
                runner=run,
            )

            self.assertEqual(result.language, "en")
            self.assertEqual(result.debug["selected_subtitle_kind"], "automatic")
            self.assertIn("--write-auto-subs", seen_download_command)
            self.assertEqual(result.material["transcript_quality"], "degraded")

    def test_youtube_english_variants_are_candidates(self) -> None:
        listing = parse_youtube_subtitle_listing(
            "[info] Available subtitles for abc:\n"
            "Language Name Formats\n"
            "en-CA English (Canada) vtt, srt\n"
            "en-orig English original vtt, srt\n"
            "en-GB English (United Kingdom) vtt, srt\n"
            "en-US English (United States) vtt, srt\n"
        )

        candidates = build_youtube_subtitle_candidates(listing, ("zh-Hans", "zh", "en"))

        self.assertEqual(
            [candidate["language"] for candidate in candidates],
            ["en-US", "en-GB", "en-orig", "en-CA"],
        )

    def test_all_youtube_candidates_fail_before_audio_fallback_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                if "--list-subs" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="[info] Available subtitles for abc:\nLanguage Name Formats\nen English vtt, srt\n",
                        stderr="",
                    )
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 429: Too Many Requests")

            with self.assertRaises(SubtitleUnavailableError) as context:
                fetch_platform_subtitles("https://www.youtube.com/watch?v=abc", Path(temp_dir), runner=run)

            self.assertEqual(context.exception.reason_code, "subtitle_unavailable")
            self.assertEqual(context.exception.debug["subtitle_fetch_reason"], "all_subtitle_candidates_failed")
            self.assertEqual(context.exception.debug["subtitle_candidate_failures"][0]["language"], "en")
            self.assertFalse(context.exception.debug["audio_downloader_skipped_due_to_subtitle"])

    def test_non_youtube_non_bilibili_path_keeps_aggregate_language_request(self) -> None:
        seen_sub_langs: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def run(command, capture_output, text, timeout):
                seen_sub_langs.append(command[command.index("--sub-langs") + 1])
                (output_dir / "abc.zh.vtt").write_text(VTT_CONTENT, encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = fetch_platform_subtitles("https://example.com/video/abc", output_dir, runner=run)

            self.assertEqual(result.language, "zh")
            self.assertEqual(seen_sub_langs, ["en,en-US,en-GB,en-orig,zh-Hans,zh"])

    def test_bilibili_path_uses_bilibili_provider_not_ytdlp_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            material = {
                "material_version": "watchbrief_v5.transcript_material.v1",
                "source": {
                    "kind": "subtitle_bcc",
                    "provider": "bilibili_content_provider",
                    "subtitle_language": "zh-Hans",
                    "subtitle_kind": "manual",
                    "subtitle_format": "bcc",
                },
                "language": "zh",
                "transcript_quality": "ok",
                "segments": [{"start": "00:00", "end": "00:01", "text": "测试"}],
            }

            def run(*args, **kwargs):
                raise AssertionError("Bilibili subtitle provider must not use yt-dlp runner")

            with patch("scripts.subtitle_fetcher.fetch_bilibili_subtitle") as provider:
                provider.return_value = SimpleNamespace(
                    subtitle_path=Path(temp_dir) / "BV.mock.zh-Hans.bcc.json",
                    language="zh-Hans",
                    material=material,
                    command=["bilibili-content-provider", "https://www.bilibili.com/video/BV15qQwB4EZ9/"],
                    debug={
                        "provider": "bilibili_content_provider",
                        "source_api": "player-wbi-v2",
                        "selected_subtitle_lang": "zh-Hans",
                        "selected_subtitle_kind": "manual",
                        "selected_subtitle_format": "bcc",
                        "audio_downloader_skipped_due_to_subtitle": True,
                    },
                )

                result = fetch_platform_subtitles(
                    "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                    Path(temp_dir),
                    cookies_from_browser="chrome",
                    runner=run,
                )

            self.assertEqual(result.language, "zh-Hans")
            self.assertEqual(result.material["source"]["provider"], "bilibili_content_provider")
            self.assertEqual(result.debug["source_api"], "player-wbi-v2")
            self.assertTrue(result.debug["audio_downloader_skipped_due_to_subtitle"])
            provider.assert_called_once()
            self.assertEqual(provider.call_args.kwargs["cookies_from_browser"], "chrome")

    def test_bilibili_provider_receives_safari_cookies_from_fetcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            material = {
                "material_version": "watchbrief_v5.transcript_material.v1",
                "source": {
                    "kind": "subtitle_bcc",
                    "provider": "bilibili_content_provider",
                    "subtitle_language": "zh-Hans",
                    "subtitle_kind": "manual",
                    "subtitle_format": "bcc",
                },
                "language": "zh",
                "transcript_quality": "ok",
                "segments": [{"start": "00:00", "end": "00:01", "text": "测试"}],
            }

            with patch("scripts.subtitle_fetcher.fetch_bilibili_subtitle") as provider:
                provider.return_value = SimpleNamespace(
                    subtitle_path=Path(temp_dir) / "BV.mock.zh-Hans.bcc.json",
                    language="zh-Hans",
                    material=material,
                    command=["bilibili-content-provider", "https://www.bilibili.com/video/BV15qQwB4EZ9/"],
                    debug={
                        "provider": "bilibili_content_provider",
                        "source_api": "player-wbi-v2",
                        "selected_subtitle_lang": "zh-Hans",
                        "selected_subtitle_kind": "manual",
                        "selected_subtitle_format": "bcc",
                        "audio_downloader_skipped_due_to_subtitle": True,
                    },
                )

                fetch_platform_subtitles(
                    "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                    Path(temp_dir),
                    cookies_from_browser="safari",
                    runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Bilibili provider must not call yt-dlp")),
                )

            provider.assert_called_once()
            self.assertEqual(provider.call_args.kwargs["cookies_from_browser"], "safari")

    def test_bilibili_provider_uses_default_browser_fallback_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            material = {
                "material_version": "watchbrief_v5.transcript_material.v1",
                "source": {
                    "kind": "subtitle_bcc",
                    "provider": "bilibili_content_provider",
                    "subtitle_language": "zh-Hans",
                    "subtitle_kind": "manual",
                    "subtitle_format": "bcc",
                },
                "language": "zh",
                "transcript_quality": "ok",
                "segments": [{"start": "00:00", "end": "00:01", "text": "测试"}],
            }

            with patch("scripts.subtitle_fetcher.fetch_bilibili_subtitle") as provider:
                provider.side_effect = [
                    BilibiliSubtitleError("login_required_for_subtitle", "login required", ["bilibili-content-provider"]),
                    SimpleNamespace(
                        subtitle_path=Path(temp_dir) / "BV.mock.zh-Hans.bcc.json",
                        language="zh-Hans",
                        material=material,
                        command=["bilibili-content-provider", "https://www.bilibili.com/video/BV15qQwB4EZ9/"],
                        debug={
                            "provider": "bilibili_content_provider",
                            "source_api": "player-wbi-v2",
                            "selected_subtitle_lang": "zh-Hans",
                            "selected_subtitle_kind": "manual",
                            "selected_subtitle_format": "bcc",
                            "audio_downloader_skipped_due_to_subtitle": True,
                        },
                    ),
                ]

                result = fetch_platform_subtitles(
                    "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                    Path(temp_dir),
                    cookie_browser_attempts=["chrome", "safari"],
                    runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Bilibili provider must not call yt-dlp")),
                )

            self.assertEqual(provider.call_args_list[0].kwargs["cookies_from_browser"], "chrome")
            self.assertEqual(provider.call_args_list[1].kwargs["cookies_from_browser"], "safari")
            self.assertEqual(result.debug["cookies_browser_attempts"], ["chrome", "safari"])
            self.assertEqual(result.debug["selected_cookies_browser"], "safari")
            self.assertEqual(result.debug["cookies_fallback_reason"], "login_required_for_subtitle")

    def test_youtube_connect_provider_entry_is_reserved_and_documented(self) -> None:
        provider_path = ROOT / "scripts" / "providers" / "youtube_connect_provider.py"
        self.assertTrue(provider_path.exists())
        self.assertIn("YouTube Connect", provider_path.read_text(encoding="utf-8"))
        self.assertIn("YouTube Connect", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("YouTube Connect", (ROOT / "CONTRACT_V5.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
