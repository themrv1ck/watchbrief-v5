from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts.acquisition_errors import (
    AudioDownloadBlockedError,
    AudioDownloadError,
    BilibiliAudioDownloadError,
    FormatConversionError,
    PlatformRestrictionError,
)
from scripts.audio_downloader import download_standard_audio, request_headers


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class AcquisitionAudioDownloaderTest(unittest.TestCase):
    def test_audio_download_blocked_before_subtitle_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(AudioDownloadBlockedError):
                download_standard_audio(
                    "https://example.com/watch/abc",
                    Path(temp_dir),
                    subtitle_checked=False,
                    subtitle_available=False,
                )

    def test_audio_download_blocked_when_subtitles_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(AudioDownloadBlockedError):
                download_standard_audio(
                    "https://example.com/watch/abc",
                    Path(temp_dir),
                    subtitle_checked=True,
                    subtitle_available=True,
                )

    def test_download_standard_audio_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def run(command, capture_output, text, timeout):
                (output_dir / "abc.wav").write_bytes(b"RIFFmock")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = download_standard_audio(
                "https://example.com/watch/abc",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                runner=run,
            )

            self.assertEqual(result.audio_path.name, "abc.wav")
            self.assertIn("--audio-format", result.command)

    def test_explicit_browser_cookies_are_passed_to_non_bilibili_audio_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            seen_command: list[str] = []

            def run(command, capture_output, text, timeout):
                seen_command.extend(command)
                (output_dir / "abc.wav").write_bytes(b"RIFFmock")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = download_standard_audio(
                "https://www.youtube.com/watch?v=abc",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                cookies_from_browser="chrome",
                runner=run,
            )

            self.assertIn("--cookies-from-browser", seen_command)
            self.assertIn("chrome", seen_command)
            self.assertEqual(result.cookies_source, "browser:chrome")

    def test_explicit_safari_cookies_are_passed_to_non_bilibili_audio_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            seen_command: list[str] = []

            def run(command, capture_output, text, timeout):
                seen_command.extend(command)
                (output_dir / "abc.wav").write_bytes(b"RIFFmock")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = download_standard_audio(
                "https://www.youtube.com/watch?v=abc",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                cookies_from_browser="safari",
                runner=run,
            )

            self.assertIn("--cookies-from-browser", seen_command)
            self.assertIn("safari", seen_command)
            self.assertEqual(result.cookies_source, "browser:safari")

    def test_default_browser_auth_falls_back_from_chrome_to_safari_for_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            seen_commands: list[list[str]] = []

            def run(command, capture_output, text, timeout):
                seen_commands.append(list(command))
                if "chrome" in command:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="Extracted 0 cookies from chrome")
                (output_dir / "abc.wav").write_bytes(b"RIFFmock")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = download_standard_audio(
                "https://www.youtube.com/watch?v=abc",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                cookie_browser_attempts=["chrome", "safari"],
                runner=run,
            )

            self.assertEqual(len(seen_commands), 2)
            self.assertIn("chrome", seen_commands[0])
            self.assertIn("safari", seen_commands[1])
            self.assertEqual(result.cookies_source, "browser:safari")
            self.assertEqual(result.cookies_browser_attempts, ["chrome", "safari"])
            self.assertEqual(result.selected_cookies_browser, "safari")
            self.assertEqual(result.cookies_fallback_reason, "extracted_0_cookies")

    def test_default_browser_auth_falls_back_from_chrome_to_safari_for_no_video_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            seen_commands: list[list[str]] = []

            def run(command, capture_output, text, timeout):
                seen_commands.append(list(command))
                if "chrome" in command:
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="ERROR: No video formats found!")
                (output_dir / "abc.wav").write_bytes(b"RIFFmock")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = download_standard_audio(
                "https://www.xiaohongshu.com/explore/mock?xsec_token=test",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                cookie_browser_attempts=["chrome", "safari"],
                runner=run,
            )

            self.assertEqual(len(seen_commands), 2)
            self.assertIn("chrome", seen_commands[0])
            self.assertIn("safari", seen_commands[1])
            self.assertEqual(result.cookies_source, "browser:safari")
            self.assertEqual(result.selected_cookies_browser, "safari")
            self.assertEqual(result.cookies_fallback_reason, "no_video_formats")

    def test_audio_download_failure_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="network failed")

            with self.assertRaises(AudioDownloadError) as context:
                download_standard_audio(
                    "https://example.com/watch/abc",
                    Path(temp_dir),
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                )
            self.assertEqual(context.exception.reason_code, "audio_download_failed")

    def test_format_conversion_failure_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="ffmpeg conversion failed")

            with self.assertRaises(FormatConversionError) as context:
                download_standard_audio(
                    "https://example.com/watch/abc",
                    Path(temp_dir),
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                )
            self.assertEqual(context.exception.reason_code, "format_conversion_failed")

    def test_platform_restriction_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="Sign in to confirm your age")

            with self.assertRaises(PlatformRestrictionError):
                download_standard_audio(
                    "https://example.com/watch/abc",
                    Path(temp_dir),
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                )

    def assert_bilibili_reason(self, stderr: str, expected_reason: str, *, urlopen_func=None) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr=stderr)

            with self.assertRaises(BilibiliAudioDownloadError) as context:
                download_standard_audio(
                    "https://www.bilibili.com/video/BV1xx411c7mD/",
                    Path(temp_dir),
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                    urlopen_func=urlopen_func or self.empty_bilibili_page,
                )
            self.assertEqual(context.exception.reason_code, expected_reason)

    def empty_bilibili_page(self, request, timeout):
        return FakeResponse(b"<html></html>")

    def playinfo_page_then_audio(self, request, timeout):
        url = request.full_url
        if "bilibili.com/video" in url:
            html = (
                "<script>window.__playinfo__ = "
                + json.dumps({"data": {"dash": {"audio": [{"baseUrl": "https://upos.mock/audio.m4a"}]}}})
                + "</script>"
            )
            return FakeResponse(html.encode("utf-8"))
        return FakeResponse(b"mock audio bytes")

    def initial_state_then_api_then_audio(self, request, timeout):
        url = request.full_url
        if "bilibili.com/video" in url:
            html = (
                "<script>window.__INITIAL_STATE__ = "
                + json.dumps({"videoData": {"bvid": "BV1xx411c7mD", "cid": 12345}})
                + ";</script>"
            )
            return FakeResponse(html.encode("utf-8"))
        if "x/player/playurl" in url:
            payload = {"data": {"dash": {"audio": [{"base_url": "https://upos.mock/audio.m4a"}]}}}
            return FakeResponse(json.dumps(payload).encode("utf-8"))
        return FakeResponse(b"mock audio bytes")

    def playurl_api_then_audio(self, audio_item: dict, seen_requests: list) -> callable:
        def urlopen(request, timeout):
            seen_requests.append(request)
            url = request.full_url
            if "x/player/playurl" in url:
                payload = {"code": 0, "data": {"dash": {"audio": [audio_item]}}}
                return FakeResponse(json.dumps(payload).encode("utf-8"))
            return FakeResponse(b"mock audio bytes")
        return urlopen

    def test_bilibili_cookie_missing_is_classified(self) -> None:
        self.assert_bilibili_reason("ERROR: login required, please sign in", "bilibili_cookie_missing")

    def test_bilibili_browser_cookie_not_found_is_classified(self) -> None:
        self.assert_bilibili_reason("ERROR: could not find chrome cookies", "bilibili_cookie_missing")

    def test_bilibili_cookie_expired_is_classified(self) -> None:
        self.assert_bilibili_reason("ERROR: cookie expired: SESSDATA is invalid", "bilibili_cookie_expired")

    def test_bilibili_412_blocked_is_classified(self) -> None:
        self.assert_bilibili_reason("HTTP Error 412: Precondition Failed", "bilibili_412_blocked")

    def test_bilibili_412_without_explicit_cookie_source_does_not_fallback(self) -> None:
        called = False

        def urlopen(request, timeout):
            nonlocal called
            called = True
            return self.playinfo_page_then_audio(request, timeout)

        self.assert_bilibili_reason(
            "HTTP Error 412: Precondition Failed",
            "bilibili_412_blocked",
            urlopen_func=urlopen,
        )
        self.assertFalse(called)

    def test_bilibili_audio_url_not_found_is_classified(self) -> None:
        self.assert_bilibili_reason("unable to extract audio url; no video formats found", "bilibili_audio_url_not_found")

    def test_bilibili_audio_download_failed_is_classified(self) -> None:
        def failing_audio_fetch(request, timeout):
            url = request.full_url
            if "bilibili.com/video" in url:
                html = (
                    "<script>window.__playinfo__ = "
                    + json.dumps({"data": {"dash": {"audio": [{"baseUrl": "https://upos.mock/audio.m4a"}]}}})
                    + "</script>"
                )
                return FakeResponse(html.encode("utf-8"))
            raise OSError("network reset")

        self.assert_bilibili_reason(
            "temporary network reset while downloading media",
            "bilibili_audio_download_failed",
            urlopen_func=failing_audio_fetch,
        )

    def test_bilibili_audio_convert_failed_is_classified(self) -> None:
        self.assert_bilibili_reason("ffmpeg conversion failed while postprocessing", "bilibili_audio_convert_failed")

    def test_bilibili_missing_wav_after_success_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with self.assertRaises(BilibiliAudioDownloadError) as context:
                download_standard_audio(
                    "https://b23.tv/mock",
                    Path(temp_dir),
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                )
            self.assertEqual(context.exception.reason_code, "bilibili_audio_url_not_found")

    def test_bilibili_playinfo_fallback_downloads_and_converts_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def run(command, capture_output, text, timeout):
                if command[0] == "yt-dlp":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="unable to extract audio url")
                if command[0] == "ffmpeg":
                    Path(command[-1]).write_bytes(b"RIFFmock")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                raise AssertionError(command)

            result = download_standard_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                runner=run,
                urlopen_func=self.playinfo_page_then_audio,
            )

            self.assertEqual(result.audio_path.name, "bilibili_audio.wav")
            self.assertEqual(result.command[0], "bilibili-playinfo-fallback")
            self.assertEqual(result.method, "bilibili_playinfo_fallback")

    def test_bilibili_412_with_explicit_cookie_file_uses_playinfo_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            cookie_file = output_dir / "cookies.txt"
            cookie_file.write_text("SESSDATA=abc", encoding="utf-8")

            def run(command, capture_output, text, timeout):
                if command[0] == "yt-dlp":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 412: Precondition Failed")
                if command[0] == "ffmpeg":
                    Path(command[-1]).write_bytes(b"RIFFmock")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                raise AssertionError(command)

            result = download_standard_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                runner=run,
                urlopen_func=self.playinfo_page_then_audio,
                cookie_file=cookie_file,
            )

            self.assertEqual(result.method, "bilibili_playinfo_fallback")

    def test_bilibili_initial_state_fallback_uses_playurl_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            seen_urls: list[str] = []

            def run(command, capture_output, text, timeout):
                if command[0] == "yt-dlp":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="no video formats")
                if command[0] == "ffmpeg":
                    Path(command[-1]).write_bytes(b"RIFFmock")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                raise AssertionError(command)

            def urlopen(request, timeout):
                seen_urls.append(request.full_url)
                return self.initial_state_then_api_then_audio(request, timeout)

            result = download_standard_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                runner=run,
                urlopen_func=urlopen,
            )

            self.assertEqual(result.audio_path.name, "bilibili_audio.wav")
            self.assertTrue(any("x/player/playurl" in url for url in seen_urls))

    def test_bilibili_resolver_bvid_cid_uses_playurl_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            seen_requests: list = []

            def run(command, capture_output, text, timeout):
                if command[0] == "yt-dlp":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="no video formats")
                if command[0] == "ffmpeg":
                    Path(command[-1]).write_bytes(b"RIFFmock")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                raise AssertionError(command)

            result = download_standard_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                runner=run,
                urlopen_func=self.playurl_api_then_audio({"baseUrl": "https://upos.mock/audio.m4a"}, seen_requests),
                bvid="BV1xx411c7mD",
                cid="12345",
            )

            self.assertEqual(result.method, "bilibili_playurl_api")
            self.assertEqual(result.audio_url_source, "dash.audio[0].baseUrl")
            self.assertTrue(result.fallback_success)
            self.assertTrue(any("x/player/playurl" in request.full_url for request in seen_requests))
            self.assertTrue(any("bvid=BV1xx411c7mD" in request.full_url and "cid=12345" in request.full_url for request in seen_requests))

    def test_bilibili_playurl_api_accepts_base_url_and_backup_url(self) -> None:
        cases = [
            ({"base_url": "https://upos.mock/base_url.m4a"}, "dash.audio[0].base_url"),
            ({"backupUrl": ["https://upos.mock/backup_url.m4a"]}, "dash.audio[0].backupUrl[0]"),
            ({"backup_url": ["https://upos.mock/backup_url2.m4a"]}, "dash.audio[0].backup_url[0]"),
        ]
        for audio_item, expected_source in cases:
            with self.subTest(expected_source=expected_source), tempfile.TemporaryDirectory() as temp_dir:
                output_dir = Path(temp_dir)
                seen_requests: list = []

                def run(command, capture_output, text, timeout):
                    if command[0] == "yt-dlp":
                        return subprocess.CompletedProcess(command, 1, stdout="", stderr="no video formats")
                    if command[0] == "ffmpeg":
                        Path(command[-1]).write_bytes(b"RIFFmock")
                        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                    raise AssertionError(command)

                result = download_standard_audio(
                    "https://www.bilibili.com/video/BV1xx411c7mD/",
                    output_dir,
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                    urlopen_func=self.playurl_api_then_audio(audio_item, seen_requests),
                    bvid="BV1xx411c7mD",
                    cid="12345",
                )

                self.assertEqual(result.method, "bilibili_playurl_api")
                self.assertEqual(result.audio_url_source, expected_source)

    def test_bilibili_playurl_api_no_dash_audio_is_specific(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="no video formats")

            def urlopen(request, timeout):
                if "x/player/playurl" in request.full_url:
                    return FakeResponse(json.dumps({"code": 0, "data": {"dash": {"audio": []}}}).encode("utf-8"))
                return FakeResponse(b"mock")

            with self.assertRaises(BilibiliAudioDownloadError) as context:
                download_standard_audio(
                    "https://www.bilibili.com/video/BV1xx411c7mD/",
                    output_dir,
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                    urlopen_func=urlopen,
                    bvid="BV1xx411c7mD",
                    cid="12345",
                )

            self.assertEqual(context.exception.reason_code, "bilibili_playurl_api_no_dash_audio")
            self.assertEqual(context.exception.debug["method"], "bilibili_playurl_api")
            self.assertEqual(context.exception.debug["playurl_status"], "no_dash_audio")

    def test_bilibili_playurl_api_forbidden_is_specific(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="no video formats")

            def urlopen(request, timeout):
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

            with self.assertRaises(BilibiliAudioDownloadError) as context:
                download_standard_audio(
                    "https://www.bilibili.com/video/BV1xx411c7mD/",
                    output_dir,
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                    urlopen_func=urlopen,
                    bvid="BV1xx411c7mD",
                    cid="12345",
                )

            self.assertEqual(context.exception.reason_code, "bilibili_playurl_api_forbidden")

    def test_bilibili_fallback_convert_failure_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def run(command, capture_output, text, timeout):
                if command[0] == "yt-dlp":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="unable to extract audio url")
                if command[0] == "ffmpeg":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="ffmpeg conversion failed")
                raise AssertionError(command)

            with self.assertRaises(BilibiliAudioDownloadError) as context:
                download_standard_audio(
                    "https://www.bilibili.com/video/BV1xx411c7mD/",
                    Path(temp_dir),
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                    urlopen_func=self.playinfo_page_then_audio,
                )
            self.assertEqual(context.exception.reason_code, "bilibili_audio_convert_failed")

    def test_bilibili_cookie_file_is_sent_to_fallback_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            cookie_file = output_dir / "cookies.txt"
            cookie_file.write_text("SESSDATA=abc; bili_jct=def", encoding="utf-8")
            seen_cookies: list[str] = []

            def run(command, capture_output, text, timeout):
                if command[0] == "yt-dlp":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="unable to extract audio url")
                if command[0] == "ffmpeg":
                    Path(command[-1]).write_bytes(b"RIFFmock")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                raise AssertionError(command)

            def urlopen(request, timeout):
                seen_cookies.append(request.headers.get("Cookie", ""))
                return self.playinfo_page_then_audio(request, timeout)

            download_standard_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                runner=run,
                urlopen_func=urlopen,
                cookie_file=cookie_file,
            )

            self.assertIn("SESSDATA=abc", seen_cookies[0])

    def test_bilibili_playurl_audio_download_uses_required_headers_and_cookies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            cookie_file = output_dir / "cookies.txt"
            cookie_file.write_text("SESSDATA=abc", encoding="utf-8")
            seen_audio_headers: list[dict] = []

            def run(command, capture_output, text, timeout):
                if command[0] == "yt-dlp":
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="no video formats")
                if command[0] == "ffmpeg":
                    Path(command[-1]).write_bytes(b"RIFFmock")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                raise AssertionError(command)

            def urlopen(request, timeout):
                if "x/player/playurl" in request.full_url:
                    payload = {"code": 0, "data": {"dash": {"audio": [{"baseUrl": "https://upos.mock/audio.m4a"}]}}}
                    return FakeResponse(json.dumps(payload).encode("utf-8"))
                seen_audio_headers.append(dict(request.headers))
                return FakeResponse(b"mock audio bytes")

            result = download_standard_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                runner=run,
                urlopen_func=urlopen,
                cookie_file=cookie_file,
                bvid="BV1xx411c7mD",
                cid="12345",
            )

            self.assertEqual(result.method, "bilibili_playurl_api")
            self.assertEqual(result.cookies_source, "cookies_file")
            self.assertEqual(seen_audio_headers[0]["Referer"], "https://www.bilibili.com/")
            self.assertIn("Mozilla/5.0", seen_audio_headers[0]["User-agent"])
            self.assertEqual(seen_audio_headers[0]["Cookie"], "SESSDATA=abc")

    def test_bilibili_cookies_from_browser_is_passed_to_ytdlp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            seen_command: list[str] = []

            def run(command, capture_output, text, timeout):
                seen_command.extend(command)
                (output_dir / "abc.wav").write_bytes(b"RIFFmock")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            download_standard_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                runner=run,
                cookies_from_browser="chrome",
            )

            self.assertIn("--cookies-from-browser", seen_command)
            self.assertIn("chrome", seen_command)

    def test_bilibili_cookies_file_is_passed_to_ytdlp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            cookie_file = output_dir / "cookies.txt"
            cookie_file.write_text("SESSDATA=abc", encoding="utf-8")
            seen_command: list[str] = []

            def run(command, capture_output, text, timeout):
                seen_command.extend(command)
                (output_dir / "abc.wav").write_bytes(b"RIFFmock")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            download_standard_audio(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                output_dir,
                subtitle_checked=True,
                subtitle_available=False,
                runner=run,
                cookie_file=cookie_file,
            )

            self.assertIn("--cookies", seen_command)
            self.assertIn(str(cookie_file), seen_command)

    def test_bilibili_request_headers_include_required_referer_and_user_agent(self) -> None:
        headers = request_headers("https://www.bilibili.com/video/BV1xx411c7mD/", "SESSDATA=abc")

        self.assertEqual(headers["Referer"], "https://www.bilibili.com/")
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertEqual(headers["Cookie"], "SESSDATA=abc")

    def test_bilibili_fallback_412_failure_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            cookie_file = output_dir / "cookies.txt"
            cookie_file.write_text("SESSDATA=abc", encoding="utf-8")

            def run(command, capture_output, text, timeout):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 412: Precondition Failed")

            def urlopen(request, timeout):
                raise urllib.error.HTTPError(request.full_url, 412, "Precondition Failed", {}, None)

            with self.assertRaises(BilibiliAudioDownloadError) as context:
                download_standard_audio(
                    "https://www.bilibili.com/video/BV1xx411c7mD/",
                    output_dir,
                    subtitle_checked=True,
                    subtitle_available=False,
                    runner=run,
                    urlopen_func=urlopen,
                    cookie_file=cookie_file,
                )

            self.assertEqual(context.exception.reason_code, "bilibili_412_blocked")
            self.assertEqual(context.exception.command[0], "bilibili-playinfo-fallback")


if __name__ == "__main__":
    unittest.main()
