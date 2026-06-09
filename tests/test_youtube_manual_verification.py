from __future__ import annotations

import unittest

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts.youtube_manual_verification import (
    chrome_verification_command,
    needs_manual_youtube_verification,
    open_chrome_for_manual_verification,
    ytdlp_subtitle_probe_command,
)


class YouTubeManualVerificationTest(unittest.TestCase):
    def test_detects_bot_verification_errors(self) -> None:
        self.assertTrue(needs_manual_youtube_verification({
            "stage": "resolver",
            "reason_code": "platform_restriction",
            "error": "Sign in to confirm you’re not a bot",
        }))

    def test_detects_nested_transcript_fallback_block_details(self) -> None:
        self.assertTrue(needs_manual_youtube_verification({
            "status": "failed",
            "url": "https://www.youtube.com/watch?v=LSQgoNraoVo",
            "error": {
                "stage": "resolver",
                "reason_code": "resolver_failed",
                "error": "yt-dlp failed to resolve URL",
                "debug": {
                    "stderr": "YouTube is blocking requests from your IP",
                },
            },
            "transcript_fallback_debug": {
                "resolver_failed": True,
                "transcript_fallback_provider": "youtube-connect",
                "transcript_fallback_success": False,
                "safari_error": "Safari transcript fallback returned no usable transcript segments",
            },
        }))

    def test_subtitle_absence_without_resolver_block_does_not_trigger_verification(self) -> None:
        self.assertFalse(needs_manual_youtube_verification({
            "stage": "subtitle",
            "reason_code": "subtitle_unavailable",
            "error": "No usable transcript segments",
        }))

    def test_open_chrome_uses_only_allowed_browser_command(self) -> None:
        seen = []
        url = "https://www.youtube.com/watch?v=Er2s-CFoZSo"

        def runner(command, check):
            seen.append((command, check))

        open_chrome_for_manual_verification(url, runner=runner)

        self.assertEqual(seen, [(["open", "-a", "Google Chrome", url], True)])

    def test_probe_command_is_read_only(self) -> None:
        url = "https://www.youtube.com/watch?v=Er2s-CFoZSo"

        self.assertEqual(chrome_verification_command(url), ["open", "-a", "Google Chrome", url])
        self.assertEqual(
            ytdlp_subtitle_probe_command(url),
            ["yt-dlp", "--cookies-from-browser", "chrome", "--skip-download", "--list-subs", url],
        )


if __name__ == "__main__":
    unittest.main()
