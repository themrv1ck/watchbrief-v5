from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts import cli


class CliTest(unittest.TestCase):
    def write_mock_review(self, root: Path) -> Path:
        path = root / "mock_review.json"
        path.write_text(json.dumps({"title": "mock"}, ensure_ascii=False), encoding="utf-8")
        return path

    def test_source_file_resolver_reuses_metadata_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "urls.txt"
            source_file.write_text("https://example.com/one\nhttps://example.com/two\n", encoding="utf-8")
            calls: list[tuple[str, dict]] = []

            def fake_resolve(url: str, **kwargs):
                calls.append((url, kwargs))
                if url.endswith("/one"):
                    return {
                        "source_kind": "single",
                        "resolver_debug": {"cookies_source": "browser:chrome"},
                        "videos": [
                            {
                                "title": "one",
                                "url": url,
                                "duration": 187,
                                "date": "2026-04-29",
                                "bvid": "BV1",
                                "cid": "CID1",
                            }
                        ],
                    }
                return {
                    "source_kind": "single",
                    "title": "two",
                    "url": url,
                    "channel": "Mock Channel",
                    "duration_seconds": 42,
                    "timestamp": 1777464000,
                }

            resolver = cli.make_resolver_for_source_file(source_file, resolver_func=fake_resolve)
            resolved = resolver(str(source_file), cookie_browser_attempts=["chrome", "safari"])

            self.assertEqual(resolved["source_kind"], "list")
            self.assertEqual([call[0] for call in calls], ["https://example.com/one", "https://example.com/two"])
            self.assertEqual(calls[0][1]["cookie_browser_attempts"], ["chrome", "safari"])
            self.assertEqual(resolved["videos"][0]["duration"], 187)
            self.assertEqual(resolved["videos"][0]["resolver_debug"]["cookies_source"], "browser:chrome")
            self.assertEqual(resolved["videos"][1]["duration_seconds"], 42)
            self.assertEqual(resolved["videos"][1]["timestamp"], 1777464000)

    def test_source_file_resolver_uses_youtube_fallback_for_per_url_resolver_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "urls.txt"
            source_file.write_text("https://www.youtube.com/watch?v=Er2s-CFoZSo\n", encoding="utf-8")
            original_error = RuntimeError("yt-dlp failed to resolve URL")

            def fake_resolve(url: str, **kwargs):
                raise original_error

            fallback_resolution = {
                "source_kind": "single",
                "resolver_debug": {"fallback_method": "safari_yt_initial_data_transcript"},
                "videos": [
                    {
                        "title": "from safari fallback",
                        "url": "https://www.youtube.com/watch?v=Er2s-CFoZSo",
                        "duration": "00:20",
                        "transcript_fallback": {"transcript_fallback_success": True},
                        "_transcript_material": {"segments": []},
                    }
                ],
            }

            with mock.patch("scripts.cli.resolve_with_youtube_connect_fallback", return_value=fallback_resolution) as fallback:
                resolver = cli.make_resolver_for_source_file(source_file, resolver_func=fake_resolve)
                resolved = resolver(str(source_file), allow_browser_auth=True)

            self.assertEqual(resolved["source_kind"], "list")
            self.assertEqual(resolved["videos"][0]["title"], "from safari fallback")
            self.assertEqual(resolved["videos"][0]["resolver_debug"]["fallback_method"], "safari_yt_initial_data_transcript")
            self.assertIs(fallback.call_args.args[1], original_error)
            self.assertEqual(fallback.call_args.args[2].resolver_options["allow_browser_auth"], True)

    def test_source_file_resolver_can_lock_current_item_without_expanding_resolved_lists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "urls.txt"
            source_file.write_text("https://www.bilibili.com/list/ml1?bvid=BVcurrent\n", encoding="utf-8")

            def fake_resolve(url: str, **kwargs):
                self.assertFalse(kwargs["allow_playlist_expansion"])
                return {
                    "source_kind": "list",
                    "title": "multi-p should not expand in rerun mode",
                    "videos": [
                        {"title": "p1", "url": url + "&p=1", "duration": 10},
                        {"title": "p2", "url": url + "&p=2", "duration": 20},
                    ],
                }

            resolver = cli.make_resolver_for_source_file(source_file, resolver_func=fake_resolve)
            resolved = resolver(str(source_file), allow_playlist_expansion=False)

            self.assertEqual(resolved["source_kind"], "list")
            self.assertEqual(len(resolved["videos"]), 1)
            self.assertEqual(resolved["videos"][0]["title"], "p1")

    def run_cli_and_capture_deps(self, extra_args: list[str]):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            argv = [
                "cli.py",
                "--source-url",
                "https://example.com/watch",
                "--output-dir",
                str(root / "out"),
                "--mock-review-response",
                str(mock_review),
                *extra_args,
            ]
            with mock.patch.object(sys, "argv", argv):
                with mock.patch("scripts.cli.process_source", return_value={"source_kind": "single", "items": [], "failed_count": 0}) as process:
                    self.assertEqual(cli.main(), 0)
            return process.call_args.kwargs["deps"]

    def test_auto_review_provider_prefers_codex_when_ready(self) -> None:
        args = argparse.Namespace(
            review_provider="auto",
            enable_codex_review=False,
            codex_home=None,
            codex_home_root=None,
            codex_account=None,
        )
        with mock.patch("scripts.cli.codex_review_preflight_ready", return_value=True):
            with mock.patch("scripts.cli.read_current_watchbrief_codex_account", return_value="account-ready"):
                cli.resolve_auto_review_provider(args)

        self.assertEqual(args.review_provider, "codex-cli")
        self.assertTrue(args.enable_codex_review)
        self.assertEqual(args.codex_home_root, cli.DEFAULT_WATCHBRIEF_CODEX_HOME_ROOT)
        self.assertEqual(args.codex_account, "account-ready")

    def test_auto_review_provider_falls_back_to_local_only_when_codex_is_not_ready(self) -> None:
        args = argparse.Namespace(
            review_provider="auto",
            enable_codex_review=False,
            codex_home=None,
            codex_home_root=None,
            codex_account=None,
        )
        with mock.patch("scripts.cli.codex_review_preflight_ready", return_value=False):
            cli.resolve_auto_review_provider(args)

        self.assertEqual(args.review_provider, "local")
        self.assertFalse(args.enable_codex_review)

    def test_analysis_mode_defaults_to_standard_timeout(self) -> None:
        deps = self.run_cli_and_capture_deps([])

        self.assertEqual(deps.review_options["analysis_mode"], "standard")
        self.assertEqual(deps.review_options["report_target"], "watch_decision")
        self.assertEqual(deps.local_extract_options["qwen_timeout"], 900)
        self.assertFalse(deps.review_options["force_reanalysis"])

    def test_report_target_passes_to_pipeline_review_options(self) -> None:
        deps = self.run_cli_and_capture_deps(["--report-target", "knowledge_notes"])

        self.assertEqual(deps.review_options["analysis_mode"], "standard")
        self.assertEqual(deps.review_options["report_target"], "knowledge_notes")

    def test_deep_analysis_mode_sets_pipeline_defaults(self) -> None:
        deps = self.run_cli_and_capture_deps(["--analysis-mode", "deep"])

        self.assertEqual(deps.review_options["analysis_mode"], "deep")
        self.assertEqual(deps.local_extract_options["qwen_timeout"], 1200)
        self.assertTrue(deps.review_options["force_reanalysis"])

    def test_explicit_timeout_overrides_analysis_mode_timeout(self) -> None:
        deps = self.run_cli_and_capture_deps(["--analysis-mode", "fast", "--timeout", "321"])

        self.assertEqual(deps.review_options["analysis_mode"], "fast")
        self.assertEqual(deps.local_extract_options["qwen_timeout"], 321)

    def test_no_playlist_expansion_passes_to_resolver_options(self) -> None:
        deps = self.run_cli_and_capture_deps(["--no-playlist-expansion"])

        self.assertFalse(deps.resolver_options["allow_playlist_expansion"])

    def test_default_cookies_from_browser_attempts_chrome_then_safari(self) -> None:
        deps = self.run_cli_and_capture_deps([])

        self.assertEqual(deps.resolver_options["cookie_browser_attempts"], ["chrome", "safari"])
        self.assertEqual(deps.subtitle_options["cookie_browser_attempts"], ["chrome", "safari"])
        self.assertEqual(deps.audio_download_options["cookie_browser_attempts"], ["chrome", "safari"])
        self.assertNotIn("cookies_from_browser", deps.resolver_options)
        self.assertNotIn("cookies_from_browser", deps.subtitle_options)
        self.assertNotIn("cookies_from_browser", deps.audio_download_options)

    def test_source_file_keeps_resolver_browser_auth_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "urls.txt"
            source_file.write_text("https://example.com/one\n", encoding="utf-8")
            mock_review = self.write_mock_review(root)

            with mock.patch.object(
                sys,
                "argv",
                [
                    "cli.py",
                    "--source-file",
                    str(source_file),
                    "--output-dir",
                    str(root / "out"),
                    "--mock-review-response",
                    str(mock_review),
                ],
            ):
                with mock.patch("scripts.cli.process_source", return_value={"source_kind": "list", "items": [], "failed_count": 0}) as process:
                    self.assertEqual(cli.main(), 0)

            deps = process.call_args.kwargs["deps"]
            self.assertEqual(deps.resolver_options["cookie_browser_attempts"], ["chrome", "safari"])

    def test_explicit_chrome_cookies_from_browser_overrides_default(self) -> None:
        deps = self.run_cli_and_capture_deps(["--cookies-from-browser", "chrome"])

        self.assertEqual(deps.resolver_options["cookies_from_browser"], "chrome")
        self.assertEqual(deps.subtitle_options["cookies_from_browser"], "chrome")
        self.assertEqual(deps.audio_download_options["cookies_from_browser"], "chrome")
        self.assertNotIn("cookie_browser_attempts", deps.resolver_options)
        self.assertNotIn("cookie_browser_attempts", deps.subtitle_options)
        self.assertNotIn("cookie_browser_attempts", deps.audio_download_options)

    def test_explicit_safari_cookies_from_browser_is_preserved(self) -> None:
        deps = self.run_cli_and_capture_deps(["--cookies-from-browser", "safari"])

        self.assertEqual(deps.resolver_options["cookies_from_browser"], "safari")
        self.assertEqual(deps.subtitle_options["cookies_from_browser"], "safari")
        self.assertEqual(deps.audio_download_options["cookies_from_browser"], "safari")
        self.assertNotIn("cookie_browser_attempts", deps.resolver_options)
        self.assertNotIn("cookie_browser_attempts", deps.subtitle_options)
        self.assertNotIn("cookie_browser_attempts", deps.audio_download_options)

    def test_single_success_default_prints_html_path_without_opening_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            html_path = root / "out" / "01-video.html"
            manifest = {
                "source_kind": "single",
                "items": [{"status": "completed", "title": "video", "html_path": str(html_path)}],
                "failed_count": 0,
            }
            stdout = io.StringIO()
            with mock.patch.object(
                sys,
                "argv",
                [
                    "cli.py",
                    "--source-url",
                    "https://example.com/watch",
                    "--output-dir",
                    str(root / "out"),
                    "--mock-review-response",
                    str(mock_review),
                ],
            ):
                with mock.patch("scripts.cli.process_source", return_value=manifest):
                    with mock.patch("scripts.cli.open_output_paths") as open_output:
                        with contextlib.redirect_stdout(stdout):
                            self.assertEqual(cli.main(), 0)

            self.assertIn(f"HTML: {html_path}", stdout.getvalue())
            open_output.assert_not_called()

    def test_list_success_default_prints_watch_order_path_without_opening_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            watch_order_path = root / "out" / "00-watch-order.html"
            manifest = {
                "source_kind": "list",
                "items": [{"status": "completed", "title": "video", "html_path": str(root / "out" / "01-video.html")}],
                "failed_count": 0,
                "watch_order_path": str(watch_order_path),
            }
            stdout = io.StringIO()
            with mock.patch.object(
                sys,
                "argv",
                [
                    "cli.py",
                    "--source-url",
                    "https://example.com/watch?list=playlist",
                    "--output-dir",
                    str(root / "out"),
                    "--mock-review-response",
                    str(mock_review),
                ],
            ):
                with mock.patch("scripts.cli.process_source", return_value=manifest):
                    with mock.patch("scripts.cli.open_output_paths") as open_output:
                        with contextlib.redirect_stdout(stdout):
                            self.assertEqual(cli.main(), 0)

            self.assertIn(f"Watch Order: {watch_order_path}", stdout.getvalue())
            open_output.assert_not_called()

    def test_open_output_requires_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            html_path = root / "out" / "01-video.html"
            manifest = {
                "source_kind": "single",
                "items": [{"status": "completed", "title": "video", "html_path": str(html_path)}],
                "failed_count": 0,
            }
            with mock.patch.object(
                sys,
                "argv",
                [
                    "cli.py",
                    "--source-url",
                    "https://example.com/watch",
                    "--output-dir",
                    str(root / "out"),
                    "--mock-review-response",
                    str(mock_review),
                    "--open-output",
                ],
            ):
                with mock.patch("scripts.cli.process_source", return_value=manifest):
                    with mock.patch("scripts.cli.open_output_paths") as open_output:
                        self.assertEqual(cli.main(), 0)

            open_output.assert_called_once_with(manifest)

    def test_platform_restriction_prints_manual_youtube_verification_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            source_url = "https://www.youtube.com/watch?v=Er2s-CFoZSo"
            failed_manifest = {
                "source_kind": "single",
                "failed_count": 1,
                "items": [
                    {
                        "status": "failed",
                        "title": "Untitled Video",
                        "url": source_url,
                        "error": {
                            "stage": "resolver",
                            "reason_code": "platform_restriction",
                            "error": "Sign in to confirm you’re not a bot",
                        },
                    }
                ],
            }
            argv = [
                "cli.py",
                "--source-url",
                source_url,
                "--output-dir",
                str(root / "out"),
                "--mock-review-response",
                str(mock_review),
            ]
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", argv):
                with mock.patch("scripts.cli.process_source", return_value=failed_manifest):
                    with contextlib.redirect_stdout(stdout):
                        self.assertEqual(cli.main(), 0)

            output = stdout.getvalue()
            self.assertIn("manual_verification_required", output)
            self.assertIn("open -a 'Google Chrome'", output)
            self.assertIn(source_url, output)
            self.assertIn("yt-dlp --cookies-from-browser chrome --skip-download --list-subs", output)

    def test_resolver_failed_with_nested_youtube_block_prints_manual_verification_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            source_url = "https://www.youtube.com/watch?v=LSQgoNraoVo"
            failed_manifest = {
                "source_kind": "single",
                "failed_count": 1,
                "transcript_fallback_debug": {
                    "resolver_failed": True,
                    "transcript_fallback_provider": "youtube-connect",
                    "transcript_fallback_success": False,
                    "youtube_connect_error": "YouTube is blocking requests from your IP",
                    "safari_error": "Safari transcript fallback returned no usable transcript segments",
                },
                "items": [
                    {
                        "status": "failed",
                        "title": "Untitled Video",
                        "url": source_url,
                        "error": {
                            "stage": "resolver",
                            "reason_code": "resolver_failed",
                            "error": "yt-dlp failed to resolve URL",
                        },
                        "transcript_fallback_debug": {
                            "resolver_failed": True,
                            "transcript_fallback_provider": "youtube-connect",
                            "transcript_fallback_success": False,
                            "safari_error": "Safari transcript fallback returned no usable transcript segments",
                        },
                    }
                ],
            }
            argv = [
                "cli.py",
                "--source-url",
                source_url,
                "--output-dir",
                str(root / "out"),
                "--mock-review-response",
                str(mock_review),
            ]
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", argv):
                with mock.patch("scripts.cli.process_source", return_value=failed_manifest):
                    with contextlib.redirect_stdout(stdout):
                        self.assertEqual(cli.main(), 0)

            output = stdout.getvalue()
            self.assertIn("stage=resolver reason=resolver_failed message=yt-dlp failed to resolve URL", output)
            self.assertIn("manual_verification_required", output)
            self.assertIn(f"manual_verification_open: open -a 'Google Chrome' '{source_url}'", output)
            self.assertIn(
                f"manual_verification_probe: yt-dlp --cookies-from-browser chrome --skip-download --list-subs '{source_url}'",
                output,
            )

    def test_timeout_defaults_to_qwen_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            with mock.patch.object(
                sys,
                "argv",
                [
                    "cli.py",
                    "--source-url",
                    "https://example.com/watch",
                    "--output-dir",
                    str(root / "out"),
                    "--mock-review-response",
                    str(mock_review),
                    "--timeout",
                    "600",
                ],
            ):
                with mock.patch("scripts.cli.process_source", return_value={"source_kind": "single", "items": [], "failed_count": 0}) as process:
                    self.assertEqual(cli.main(), 0)

            deps = process.call_args.kwargs["deps"]
            self.assertEqual(deps.local_extract_options["qwen_timeout"], 600)

    def test_qwen_timeout_overrides_global_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            with mock.patch.object(
                sys,
                "argv",
                [
                    "cli.py",
                    "--source-url",
                    "https://example.com/watch",
                    "--output-dir",
                    str(root / "out"),
                    "--mock-review-response",
                    str(mock_review),
                    "--timeout",
                    "600",
                    "--qwen-timeout",
                    "45",
                ],
            ):
                with mock.patch("scripts.cli.process_source", return_value={"source_kind": "single", "items": [], "failed_count": 0}) as process:
                    self.assertEqual(cli.main(), 0)

            deps = process.call_args.kwargs["deps"]
            self.assertEqual(deps.local_extract_options["qwen_timeout"], 45)

    def test_single_success_output_dir_contains_only_html_and_debug_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "delivery"
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["debug_dir"] = kwargs["debug_dir"]
                delivery_dir.mkdir(parents=True, exist_ok=True)
                html_path = delivery_dir / "01-video.html"
                html_path.write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "payloads").mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "single",
                    "items": [{"status": "completed", "title": "video", "html_path": str(html_path)}],
                    "failed_count": 0,
                }

            with mock.patch.object(
                sys,
                "argv",
                [
                    "cli.py",
                    "--source-url",
                    "https://example.com/watch",
                    "--output-dir",
                    str(output_dir),
                    "--mock-review-response",
                    str(mock_review),
                ],
            ):
                with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                    self.assertEqual(cli.main(), 0)

            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["01-video.html"])
            self.assertFalse((output_dir / "00-watch-order.html").exists())
            self.assertFalse((output_dir / "payloads").exists())
            self.assertFalse((output_dir / "manifest.json").exists())
            self.assertFalse(captured["debug_dir"].exists())

    def test_list_success_keeps_watch_order_and_per_video_html_in_output_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "list-delivery"
            source_file = root / "urls.txt"
            source_file.write_text("https://example.com/one\nhttps://example.com/two\n", encoding="utf-8")
            mock_review = self.write_mock_review(root)

            def fake_process(source_value, delivery_dir, **kwargs):
                delivery_dir.mkdir(parents=True, exist_ok=True)
                for name in ("01-one.html", "02-two.html", "00-watch-order.html"):
                    (delivery_dir / name).write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "list",
                    "items": [
                        {"status": "completed", "title": "one", "html_path": str(delivery_dir / "01-one.html")},
                        {"status": "completed", "title": "two", "html_path": str(delivery_dir / "02-two.html")},
                    ],
                    "failed_count": 0,
                    "watch_order_path": str(delivery_dir / "00-watch-order.html"),
                }

            with mock.patch.object(
                sys,
                "argv",
                [
                    "cli.py",
                    "--source-file",
                    str(source_file),
                    "--output-dir",
                    str(output_dir),
                    "--mock-review-response",
                    str(mock_review),
                ],
            ):
                with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                    self.assertEqual(cli.main(), 0)

            self.assertTrue((output_dir / "00-watch-order.html").exists())
            self.assertTrue((output_dir / "01-one.html").exists())
            self.assertTrue((output_dir / "02-two.html").exists())

    def test_single_default_desktop_contains_only_html_and_no_task_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            desktop = root / "Desktop"
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["delivery_dir"] = delivery_dir
                captured["debug_dir"] = kwargs["debug_dir"]
                delivery_dir.mkdir(parents=True, exist_ok=True)
                html_path = delivery_dir / "01-video.html"
                html_path.write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "single",
                    "items": [{"status": "completed", "title": "video", "html_path": str(html_path)}],
                    "failed_count": 0,
                }

            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.resolve_url", return_value={"source_kind": "single", "videos": [{"title": "video", "url": "https://example.com/watch"}]}):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "cli.py",
                            "--source-url",
                            "https://example.com/watch",
                            "--mock-review-response",
                            str(mock_review),
                        ],
                    ):
                        with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                            self.assertEqual(cli.main(), 0)

            self.assertEqual(captured["delivery_dir"], desktop)
            self.assertEqual(sorted(path.name for path in desktop.iterdir()), ["01-video.html"])
            self.assertFalse((desktop / "00-watch-order.html").exists())
            self.assertFalse((desktop / "watch").exists())
            self.assertFalse((desktop / "payloads").exists())
            self.assertFalse(captured["debug_dir"].exists())

    def test_list_default_creates_delivery_folder_with_only_html_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "urls.txt"
            source_file.write_text("https://example.com/one\nhttps://example.com/two\n", encoding="utf-8")
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["delivery_dir"] = delivery_dir
                delivery_dir.mkdir(parents=True, exist_ok=True)
                for name in ("00-watch-order.html", "01-one.html", "02-two.html"):
                    (delivery_dir / name).write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "list",
                    "items": [
                        {"status": "completed", "title": "one", "html_path": str(delivery_dir / "01-one.html")},
                        {"status": "completed", "title": "two", "html_path": str(delivery_dir / "02-two.html")},
                    ],
                    "failed_count": 0,
                    "watch_order_path": str(delivery_dir / "00-watch-order.html"),
                }

            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.timestamp_slug", return_value="20260427-120000"):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "cli.py",
                            "--source-file",
                            str(source_file),
                            "--mock-review-response",
                            str(mock_review),
                        ],
                    ):
                        with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                            self.assertEqual(cli.main(), 0)

            expected_dir = root / "Desktop" / "watch-20260427-120000"
            self.assertEqual(captured["delivery_dir"], expected_dir)
            self.assertEqual(sorted(path.name for path in expected_dir.iterdir()), ["00-watch-order.html", "01-one.html", "02-two.html"])
            self.assertFalse((expected_dir / "payloads").exists())
            self.assertFalse((expected_dir / "manifest.json").exists())

    def test_list_url_default_outputs_directly_in_one_desktop_task_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["delivery_dir"] = delivery_dir
                delivery_dir.mkdir(parents=True, exist_ok=True)
                for name in ("00-watch-order.html", "01-one.html", "02-two.html"):
                    (delivery_dir / name).write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "list",
                    "items": [
                        {"status": "completed", "title": "one", "html_path": str(delivery_dir / "01-one.html")},
                        {"status": "completed", "title": "two", "html_path": str(delivery_dir / "02-two.html")},
                    ],
                    "failed_count": 0,
                    "watch_order_path": str(delivery_dir / "00-watch-order.html"),
                }

            playlist_url = "https://www.youtube.com/watch?v=one&list=playlist"
            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.timestamp_slug", return_value="20260428-120000"):
                    with mock.patch(
                        "scripts.cli.resolve_url",
                        return_value={
                            "source_kind": "list",
                            "source_url": playlist_url,
                            "videos": [
                                {"title": "one", "url": "https://example.com/one"},
                                {"title": "two", "url": "https://example.com/two"},
                            ],
                        },
                    ):
                        with mock.patch.object(
                            sys,
                            "argv",
                            [
                                "cli.py",
                                "--source-url",
                                playlist_url,
                                "--mock-review-response",
                                str(mock_review),
                            ],
                        ):
                            with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                                self.assertEqual(cli.main(), 0)

            expected_dir = root / "Desktop" / "watch-20260428-120000"
            self.assertEqual(captured["delivery_dir"], expected_dir)
            self.assertNotIn("WatchBrief-Runs", str(captured["delivery_dir"]))
            self.assertEqual(sorted(path.name for path in expected_dir.iterdir()), ["00-watch-order.html", "01-one.html", "02-two.html"])
            self.assertFalse([path for path in expected_dir.iterdir() if path.is_dir()])
            self.assertEqual(sorted(path.name for path in expected_dir.glob("*.html")), ["00-watch-order.html", "01-one.html", "02-two.html"])

    def test_list_url_default_output_folder_prefers_resolved_playlist_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["delivery_dir"] = delivery_dir
                delivery_dir.mkdir(parents=True, exist_ok=True)
                for name in ("00-watch-order.html", "01-one.html", "02-two.html"):
                    (delivery_dir / name).write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "list",
                    "items": [
                        {"status": "completed", "title": "one", "html_path": str(delivery_dir / "01-one.html")},
                        {"status": "completed", "title": "two", "html_path": str(delivery_dir / "02-two.html")},
                    ],
                    "failed_count": 0,
                    "watch_order_path": str(delivery_dir / "00-watch-order.html"),
                }

            playlist_url = "https://www.bilibili.com/list/ml3621337310?oid=114282922511207&bvid=BV1GRRXYEEGn"
            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.timestamp_slug", return_value="20260430-120000"):
                    with mock.patch(
                        "scripts.cli.resolve_url",
                        return_value={
                            "source_kind": "list",
                            "source_url": playlist_url,
                            "title": "纳瓦尔",
                            "playlist_title": "纳瓦尔",
                            "videos": [
                                {"title": "one", "url": "https://www.bilibili.com/video/BV1GRRXYEEGn"},
                                {"title": "two", "url": "https://www.bilibili.com/video/BV1yKZLYTEzV"},
                            ],
                        },
                    ):
                        with mock.patch.object(
                            sys,
                            "argv",
                            [
                                "cli.py",
                                "--source-url",
                                playlist_url,
                                "--mock-review-response",
                                str(mock_review),
                            ],
                        ):
                            with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                                self.assertEqual(cli.main(), 0)

            expected_dir = root / "Desktop" / "纳瓦尔"
            self.assertEqual(captured["delivery_dir"], expected_dir)
            self.assertNotIn("WatchBrief-Runs", str(captured["delivery_dir"]))
            self.assertEqual(sorted(path.name for path in expected_dir.iterdir()), ["00-watch-order.html", "01-one.html", "02-two.html"])

    def test_list_url_default_output_folder_uses_suffix_when_playlist_title_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing_dir = root / "Desktop" / "纳瓦尔"
            existing_dir.mkdir(parents=True)
            (existing_dir / "keep.txt").write_text("do not overwrite", encoding="utf-8")
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["delivery_dir"] = delivery_dir
                delivery_dir.mkdir(parents=True, exist_ok=True)
                for name in ("00-watch-order.html", "01-one.html", "02-two.html"):
                    (delivery_dir / name).write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "list",
                    "items": [
                        {"status": "completed", "title": "one", "html_path": str(delivery_dir / "01-one.html")},
                        {"status": "completed", "title": "two", "html_path": str(delivery_dir / "02-two.html")},
                    ],
                    "failed_count": 0,
                    "watch_order_path": str(delivery_dir / "00-watch-order.html"),
                }

            playlist_url = "https://www.bilibili.com/list/ml3621337310?oid=114282922511207&bvid=BV1GRRXYEEGn"
            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch(
                    "scripts.cli.resolve_url",
                    return_value={
                        "source_kind": "list",
                        "source_url": playlist_url,
                        "title": "纳瓦尔",
                        "playlist_title": "纳瓦尔",
                        "videos": [
                            {"title": "one", "url": "https://www.bilibili.com/video/BV1GRRXYEEGn"},
                            {"title": "two", "url": "https://www.bilibili.com/video/BV1yKZLYTEzV"},
                        ],
                    },
                ):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "cli.py",
                            "--source-url",
                            playlist_url,
                            "--mock-review-response",
                            str(mock_review),
                        ],
                    ):
                        with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                            self.assertEqual(cli.main(), 0)

            expected_dir = root / "Desktop" / "纳瓦尔-2"
            self.assertEqual(captured["delivery_dir"], expected_dir)
            self.assertEqual((existing_dir / "keep.txt").read_text(encoding="utf-8"), "do not overwrite")
            self.assertEqual(sorted(path.name for path in expected_dir.iterdir()), ["00-watch-order.html", "01-one.html", "02-two.html"])

    def test_list_url_explicit_output_dir_is_final_delivery_dir_not_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "WatchBrief-Runs" / "phase18B-list"
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["delivery_dir"] = delivery_dir
                delivery_dir.mkdir(parents=True, exist_ok=True)
                for name in ("00-watch-order.html", "01-one.html", "02-two.html"):
                    (delivery_dir / name).write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "list",
                    "items": [
                        {"status": "completed", "title": "one", "html_path": str(delivery_dir / "01-one.html")},
                        {"status": "completed", "title": "two", "html_path": str(delivery_dir / "02-two.html")},
                    ],
                    "failed_count": 0,
                    "watch_order_path": str(delivery_dir / "00-watch-order.html"),
                }

            with mock.patch.object(
                sys,
                "argv",
                [
                    "cli.py",
                    "--source-url",
                    "https://www.youtube.com/watch?v=one&list=playlist",
                    "--output-dir",
                    str(output_dir),
                    "--mock-review-response",
                    str(mock_review),
                ],
            ):
                with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                    self.assertEqual(cli.main(), 0)

            self.assertEqual(captured["delivery_dir"], output_dir)
            self.assertIn("WatchBrief-Runs", str(captured["delivery_dir"]))
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["00-watch-order.html", "01-one.html", "02-two.html"])
            self.assertFalse([path for path in output_dir.iterdir() if path.is_dir()])
            self.assertFalse((output_dir / "phase18B-list").exists())
            self.assertFalse((output_dir / "watch-20260428-120000").exists())

    def test_diagnostic_run_default_outputs_to_temp_not_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            desktop = root / "Desktop"
            temp_output = root / "diagnostic-temp"
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["delivery_dir"] = delivery_dir
                captured["debug_dir"] = kwargs["debug_dir"]
                delivery_dir.mkdir(parents=True, exist_ok=True)
                html_path = delivery_dir / "01-video.html"
                html_path.write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "single",
                    "items": [{"status": "completed", "title": "video", "html_path": str(html_path)}],
                    "failed_count": 0,
                }

            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.tempfile.mkdtemp", return_value=str(temp_output)):
                    with mock.patch("scripts.cli.resolve_url", return_value={"source_kind": "single", "videos": [{"title": "video", "url": "https://example.com/watch"}]}):
                        with mock.patch.object(
                            sys,
                            "argv",
                            [
                                "cli.py",
                                "--source-url",
                                "https://example.com/watch",
                                "--mock-review-response",
                                str(mock_review),
                                "--diagnostic-run",
                            ],
                        ):
                            with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                                self.assertEqual(cli.main(), 0)

            self.assertEqual(captured["delivery_dir"], temp_output)
            self.assertEqual(captured["debug_dir"], temp_output / "_debug")
            self.assertTrue((temp_output / "01-video.html").exists())
            self.assertTrue((temp_output / "_debug" / "manifest.json").exists())
            self.assertFalse(desktop.exists())
            manifest = json.loads((temp_output / "_debug" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_mode"], "diagnostic")
            self.assertEqual(manifest["artifact_class"], "diagnostic")
            self.assertTrue(manifest["diagnostic_run"])

    def test_repro_run_alias_uses_diagnostic_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            temp_output = root / "repro-temp"
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["delivery_dir"] = delivery_dir
                delivery_dir.mkdir(parents=True, exist_ok=True)
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {"source_kind": "single", "items": [], "failed_count": 0}

            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.tempfile.mkdtemp", return_value=str(temp_output)):
                    with mock.patch("scripts.cli.resolve_url", return_value={"source_kind": "single", "videos": [{"title": "video", "url": "https://example.com/watch"}]}):
                        with mock.patch.object(
                            sys,
                            "argv",
                            [
                                "cli.py",
                                "--source-url",
                                "https://example.com/watch",
                                "--mock-review-response",
                                str(mock_review),
                                "--repro-run",
                            ],
                        ):
                            with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                                self.assertEqual(cli.main(), 0)

            self.assertEqual(captured["delivery_dir"], temp_output)
            self.assertFalse((root / "Desktop").exists())

    def test_diagnostic_html_is_labeled_and_explicit_output_dir_is_respected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "custom-diagnostic"
            mock_review = self.write_mock_review(root)
            html_path = output_dir / "01-video.html"

            def fake_process(source_value, delivery_dir, **kwargs):
                delivery_dir.mkdir(parents=True, exist_ok=True)
                html_path.write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "single",
                    "items": [{"status": "completed", "title": "video", "html_path": str(html_path)}],
                    "failed_count": 0,
                }

            stdout = io.StringIO()
            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.resolve_url", return_value={"source_kind": "single", "videos": [{"title": "video", "url": "https://example.com/watch"}]}):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "cli.py",
                            "--source-url",
                            "https://example.com/watch",
                            "--output-dir",
                            str(output_dir),
                            "--mock-review-response",
                            str(mock_review),
                            "--diagnostic-run",
                        ],
                    ):
                        with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                            with contextlib.redirect_stdout(stdout):
                                self.assertEqual(cli.main(), 0)

            output = stdout.getvalue()
            self.assertIn(f"Diagnostic HTML: {html_path}", output)
            self.assertNotIn(f"\nHTML: {html_path}", output)
            self.assertTrue((output_dir / "_debug" / "manifest.json").exists())
            manifest = json.loads((output_dir / "_debug" / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["diagnostic_output_explicit"])

    def test_failed_default_debug_stays_in_temporary_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["debug_dir"] = kwargs["debug_dir"]
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                (kwargs["debug_dir"] / "payloads").mkdir(parents=True, exist_ok=True)
                return {
                    "source_kind": "single",
                    "items": [{"status": "failed", "title": "video", "error": {"stage": "pipeline", "reason_code": "pipeline_failed", "error": "mock"}}],
                    "failed_count": 1,
                }

            stdout = io.StringIO()
            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.resolve_url", return_value={"source_kind": "single", "videos": [{"title": "video", "url": "https://example.com/watch"}]}):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "cli.py",
                            "--source-url",
                            "https://example.com/watch",
                            "--mock-review-response",
                            str(mock_review),
                        ],
                    ):
                        with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                            with contextlib.redirect_stdout(stdout):
                                self.assertEqual(cli.main(), 0)

            output = stdout.getvalue()
            self.assertTrue(captured["debug_dir"].exists())
            self.assertTrue((captured["debug_dir"] / "manifest.json").exists())
            self.assertTrue((captured["debug_dir"] / "payloads").exists())
            self.assertIn(f"debug_artifacts: {captured['debug_dir']}", output)
            self.assertFalse((root / "Desktop" / "WatchBrief-Debug").exists())

    def test_exception_default_debug_stays_in_temporary_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["debug_dir"] = kwargs["debug_dir"]
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                raise RuntimeError("mock crash")

            stdout = io.StringIO()
            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.resolve_url", return_value={"source_kind": "single", "videos": [{"title": "video", "url": "https://example.com/watch"}]}):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "cli.py",
                            "--source-url",
                            "https://example.com/watch",
                            "--mock-review-response",
                            str(mock_review),
                        ],
                    ):
                        with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                            with contextlib.redirect_stdout(stdout):
                                with self.assertRaises(RuntimeError):
                                    cli.main()

            output = stdout.getvalue()
            self.assertTrue(captured["debug_dir"].exists())
            self.assertTrue((captured["debug_dir"] / "manifest.json").exists())
            self.assertIn(f"debug_artifacts: {captured['debug_dir']}", output)
            self.assertFalse((root / "Desktop" / "WatchBrief-Debug").exists())

    def test_explicit_debug_dir_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            debug_dir = root / "my-debug"
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["debug_dir"] = kwargs["debug_dir"]
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {"source_kind": "single", "items": [], "failed_count": 1}

            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.resolve_url", return_value={"source_kind": "single", "videos": [{"title": "video", "url": "https://example.com/watch"}]}):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "cli.py",
                            "--source-url",
                            "https://example.com/watch",
                            "--mock-review-response",
                            str(mock_review),
                            "--debug-dir",
                            str(debug_dir),
                        ],
                    ):
                        with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                            self.assertEqual(cli.main(), 0)

            self.assertEqual(captured["debug_dir"], debug_dir)
            self.assertTrue((debug_dir / "manifest.json").exists())
            self.assertFalse((root / "Desktop" / "WatchBrief-Debug").exists())

    def test_keep_debug_artifacts_keeps_successful_temporary_debug_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mock_review = self.write_mock_review(root)
            captured: dict[str, Path] = {}

            def fake_process(source_value, delivery_dir, **kwargs):
                captured["debug_dir"] = kwargs["debug_dir"]
                delivery_dir.mkdir(parents=True, exist_ok=True)
                html_path = delivery_dir / "01-video.html"
                html_path.write_text("<html></html>", encoding="utf-8")
                kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
                (kwargs["debug_dir"] / "manifest.json").write_text("{}", encoding="utf-8")
                return {
                    "source_kind": "single",
                    "items": [{"status": "completed", "title": "video", "html_path": str(html_path)}],
                    "failed_count": 0,
                }

            with mock.patch("scripts.cli.Path.home", return_value=root):
                with mock.patch("scripts.cli.resolve_url", return_value={"source_kind": "single", "videos": [{"title": "video", "url": "https://example.com/watch"}]}):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "cli.py",
                            "--source-url",
                            "https://example.com/watch",
                            "--mock-review-response",
                            str(mock_review),
                            "--keep-debug-artifacts",
                        ],
                    ):
                        with mock.patch("scripts.cli.process_source", side_effect=fake_process):
                            self.assertEqual(cli.main(), 0)

            self.assertTrue((captured["debug_dir"] / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
