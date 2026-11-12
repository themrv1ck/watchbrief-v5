#!/usr/bin/env python3
"""CLI entrypoint for WatchBefore v6."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.analyzer.codex_review import parse_review_response, run_codex_review
    from scripts.analyzer.cloud_review import run_cloud_review
    from scripts.analyzer.external_extract import build_external_extract_payload
    from scripts.analyzer.local_extract import DEFAULT_QWEN_MODEL, build_local_extract_payload
    from scripts.analyzer.local_review import LOCAL_REVIEW_MODEL_ID, build_local_review_response
    from scripts.analyzer.prompts import WATCHBRIEF_VERSION
    from scripts.cookie_strategy import DEFAULT_COOKIE_BROWSER_ATTEMPTS
    from scripts.preference_profile import load_preference_profile as load_preference_profile_metadata
    from scripts.report_targets import DEFAULT_REPORT_TARGET, REPORT_TARGETS, normalize_report_target
    from scripts.resolver import resolve_url
    from scripts.scoring import DEFAULT_SCORING_PROFILE, SCORING_PROFILES, formula_version_for_weights, parse_scoring_weights, weights_for_profile
    from scripts.video_pipeline import PipelineDependencies, PipelineError, process_source, resolve_with_youtube_connect_fallback
    from scripts.visual_analyzer import (
        DEFAULT_MAX_FRAME_GAP_SECONDS,
        MimoVLVisualAnalyzer,
        VisualAnalysisError,
    )
    from scripts.watchbrief_codex_state import DEFAULT_WATCHBRIEF_CODEX_HOME_ROOT, read_current_watchbrief_codex_account, watchbrief_codex_root
    from scripts.youtube_manual_verification import (
        chrome_verification_command,
        needs_manual_youtube_verification,
        shell_command,
        ytdlp_subtitle_probe_command,
    )
else:
    from .analyzer.codex_review import parse_review_response, run_codex_review
    from .analyzer.cloud_review import run_cloud_review
    from .analyzer.external_extract import build_external_extract_payload
    from .analyzer.local_extract import DEFAULT_QWEN_MODEL, build_local_extract_payload
    from .analyzer.local_review import LOCAL_REVIEW_MODEL_ID, build_local_review_response
    from .analyzer.prompts import WATCHBRIEF_VERSION
    from .cookie_strategy import DEFAULT_COOKIE_BROWSER_ATTEMPTS
    from .preference_profile import load_preference_profile as load_preference_profile_metadata
    from .report_targets import DEFAULT_REPORT_TARGET, REPORT_TARGETS, normalize_report_target
    from .resolver import resolve_url
    from .scoring import DEFAULT_SCORING_PROFILE, SCORING_PROFILES, formula_version_for_weights, parse_scoring_weights, weights_for_profile
    from .video_pipeline import PipelineDependencies, PipelineError, process_source, resolve_with_youtube_connect_fallback
    from .visual_analyzer import (
        DEFAULT_MAX_FRAME_GAP_SECONDS,
        MimoVLVisualAnalyzer,
        VisualAnalysisError,
    )
    from .watchbrief_codex_state import DEFAULT_WATCHBRIEF_CODEX_HOME_ROOT, read_current_watchbrief_codex_account, watchbrief_codex_root
    from .youtube_manual_verification import (
        chrome_verification_command,
        needs_manual_youtube_verification,
        shell_command,
        ytdlp_subtitle_probe_command,
    )


ReviewResponseProvider = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
DEFAULT_COOKIES_FROM_BROWSER = "chrome"
DEFAULT_COOKIE_BROWSERS = DEFAULT_COOKIE_BROWSER_ATTEMPTS
DEFAULT_PREFERENCE_PROFILE = Path("~/.watchbrief/preferences/seth.yaml").expanduser()
EXTERNAL_REVIEW_PROVIDERS = {"gemini", "claude", "kimi", "openai-compatible"}
EXTERNAL_EXTRACT_PROVIDERS = {"local-openai-compatible", "openai-compatible", "gemini", "claude", "kimi", "codex-cli-extract"}
DEFAULT_EXTRACT_API_BASES = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "claude": "https://api.anthropic.com/v1",
    "kimi": "https://api.moonshot.ai/v1",
}
ANALYSIS_MODE_DEFAULTS = {
    "fast": {"timeout": 600, "force_reanalysis": False, "keep_debug_artifacts": False},
    "standard": {"timeout": 900, "force_reanalysis": False, "keep_debug_artifacts": False},
    "deep": {"timeout": 1200, "force_reanalysis": True, "keep_debug_artifacts": True},
}


def codex_review_preflight_ready(
    *,
    codex_home: str | None,
    codex_home_root: str | None,
    codex_account: str | None,
    codex_bin: str = "codex",
) -> bool:
    if shutil.which(codex_bin) is None:
        return False
    if codex_home:
        home = Path(codex_home).expanduser()
    else:
        root = watchbrief_codex_root(codex_home_root or DEFAULT_WATCHBRIEF_CODEX_HOME_ROOT)
        account = codex_account or read_current_watchbrief_codex_account(root)
        home = root / account
    return bool(home.exists() and (home / "auth.json").exists() and (home / "config.toml").exists())


def resolve_auto_review_provider(args: argparse.Namespace) -> None:
    if args.review_provider != "auto":
        return
    if codex_review_preflight_ready(
        codex_home=args.codex_home,
        codex_home_root=args.codex_home_root,
        codex_account=args.codex_account,
    ):
        args.review_provider = "codex-cli"
        args.enable_codex_review = True
        if not args.codex_home and not args.codex_home_root:
            args.codex_home_root = DEFAULT_WATCHBRIEF_CODEX_HOME_ROOT
        if not args.codex_home and not args.codex_account:
            args.codex_account = read_current_watchbrief_codex_account(watchbrief_codex_root(args.codex_home_root))
        return
    args.review_provider = "local"


def safe_slug(value: str, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[\\/:*?\"<>|#?&=%]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80] or fallback


def timestamp_slug() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def task_name_from_source(source_value: str, *, source_file: Path | None = None) -> str:
    if source_file is not None:
        return safe_slug(source_file.stem, "watchbrief-list")
    parsed = urlparse(source_value)
    if parsed.netloc or parsed.path:
        raw = Path(parsed.path.rstrip("/")).name or parsed.netloc or "watchbrief"
        return safe_slug(raw, "watchbrief")
    return safe_slug(source_value, "watchbrief")


def source_url_may_expand_to_list(source_value: str) -> bool:
    parsed = urlparse(source_value)
    if not parsed.scheme and not parsed.netloc:
        return False
    path = parsed.path.lower()
    query = parse_qs(parsed.query)
    if "/list/" in path or "/playlist" in path or "/favlist" in path:
        return True
    if "board" in path and "xiaohongshu.com" in parsed.netloc.lower():
        return True
    return any(key in query for key in ("list", "playlist", "fid"))


def list_title_from_resolution(resolution: dict[str, Any] | None) -> str:
    if not isinstance(resolution, dict):
        return ""
    for key in ("playlist_title", "list_title", "collection_title", "title"):
        value = str(resolution.get(key) or "").strip()
        if value and value.lower() not in {"untitled list", "bilibili list"}:
            return value
    return ""


def task_name_for_delivery(
    source_value: str,
    *,
    source_file: Path | None = None,
    expected_source_kind: str = "single",
    cached_resolution: dict[str, Any] | None = None,
) -> str:
    if expected_source_kind == "list":
        title = list_title_from_resolution(cached_resolution)
        if title:
            return safe_slug(title, "watchbrief-list")
        return "watch"
    if source_file is not None:
        return task_name_from_source(source_value, source_file=source_file)
    return task_name_from_source(source_value)


def unique_delivery_dir(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.name}-{timestamp_slug()}")


def default_list_output_dir(task_name: str, *, parent: Path | None = None, stable_name: bool = False) -> Path:
    desktop = parent or Path.home() / "Desktop"
    if stable_name:
        return unique_delivery_dir(desktop / task_name)
    return desktop / f"{task_name}-{timestamp_slug()}"


def default_diagnostic_output_dir(task_name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"watchbrief_v5_{safe_slug(task_name, 'watch')}_"))


def parse_source_file(source_file: Path) -> list[str]:
    urls: list[str] = []
    for raw in source_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def output_paths_from_manifest(manifest: dict[str, Any]) -> list[str]:
    if manifest.get("source_kind") == "list" and manifest.get("watch_order_path"):
        return [str(manifest["watch_order_path"])]
    paths: list[str] = []
    for item in manifest.get("items", []):
        if item.get("status") == "completed" and item.get("html_path"):
            paths.append(str(item["html_path"]))
    return paths


def open_output_paths(manifest: dict[str, Any], *, runner: Any = subprocess.run) -> None:
    for output_path in output_paths_from_manifest(manifest):
        runner(["open", output_path], check=False)


def make_resolver_for_source_file(
    source_file: Path,
    resolver_func: Callable[..., dict[str, Any]] | None = None,
) -> Callable[..., dict[str, Any]]:
    urls = parse_source_file(source_file)
    if not urls:
        raise ValueError(f"no valid urls in {source_file}")

    resolve_func = resolver_func or resolve_url

    def source_file_resolver(_source: str, **kwargs: Any) -> dict[str, Any]:
        videos: list[dict[str, Any]] = []
        for source_index, url in enumerate(urls, start=1):
            try:
                resolved = resolve_func(url, **kwargs)
            except BaseException as exc:
                fallback_resolution = resolve_with_youtube_connect_fallback(
                    url,
                    exc,
                    PipelineDependencies(resolver_options=dict(kwargs)),
                )
                if fallback_resolution is None:
                    raise
                resolved = fallback_resolution
            resolved_items = resolved.get("videos")
            if not isinstance(resolved_items, list) or not resolved_items:
                resolved_items = [resolved]
            elif kwargs.get("allow_playlist_expansion") is False:
                resolved_items = [resolved_items[0]]
            for item_index, raw_item in enumerate(resolved_items, start=1):
                item = copy.deepcopy(raw_item) if isinstance(raw_item, dict) else {}
                for key in (
                    "title",
                    "url",
                    "channel",
                    "duration",
                    "date",
                    "publish_date",
                    "release_date",
                    "upload_date",
                    "timestamp",
                    "pubdate",
                    "published_at",
                    "duration_seconds",
                    "duration_sec",
                    "duration_string",
                    "length",
                    "bvid",
                    "aid",
                    "cid",
                    "resolver_debug",
                    "transcript_fallback",
                    "_transcript_material",
                ):
                    if not item.get(key) and key in resolved:
                        item[key] = copy.deepcopy(resolved[key])
                fallback_title = f"Video {source_index:02d}"
                if len(resolved_items) > 1:
                    fallback_title = f"{fallback_title}-{item_index:02d}"
                item["title"] = str(item.get("title") or fallback_title)
                item["url"] = str(item.get("url") or url)
                item["channel"] = str(item.get("channel") or "未知")
                item["duration"] = item.get("duration") or "未知"
                item["date"] = item.get("date") or "未知"
                videos.append(item)

        return {
            "source_kind": "list",
            "source_url": str(source_file.resolve()),
            "title": source_file.stem,
            "videos": videos,
        }

    return source_file_resolver


def make_review_response_provider(
    *,
    mock_review_response: Path | None,
    review_provider: str,
    enable_codex_review: bool,
    model: str,
    codex_model: str | None,
    codex_home: str | None,
    codex_home_root: str | None,
    codex_account: str | None,
    review_model: str | None = None,
    review_api_base: str | None = None,
    review_api_key_env: str | None = None,
    timeout: int = 120,
) -> ReviewResponseProvider:
    if mock_review_response is not None:
        mock_payload = json.loads(mock_review_response.read_text(encoding="utf-8"))
        if not isinstance(mock_payload, dict):
            raise ValueError("mock review response must be a JSON object")

        def provider(_item: dict[str, Any], _request: dict[str, Any], _extract: dict[str, Any]) -> dict[str, Any]:
            return mock_payload

        return provider

    if review_provider in {"local", "local-rules"}:
        def provider(_item: dict[str, Any], request: dict[str, Any], extract: dict[str, Any]) -> dict[str, Any]:
            return build_local_review_response(extract, report_target=normalize_report_target(request.get("report_target") or DEFAULT_REPORT_TARGET))

        return provider

    if review_provider in EXTERNAL_REVIEW_PROVIDERS:
        def provider(_item: dict[str, Any], request: dict[str, Any], extract: dict[str, Any]) -> str:
            debug_dir = Path(str(request["_watchbrief_debug_dir"])) if request.get("_watchbrief_debug_dir") else None
            return run_cloud_review(
                extract,
                review_provider=review_provider,
                model=review_model or "",
                api_base=review_api_base or "",
                api_key_env=review_api_key_env or "",
                timeout=timeout,
                report_target=normalize_report_target(request.get("report_target") or DEFAULT_REPORT_TARGET),
                debug_dir=debug_dir,
            )

        return provider

    if not enable_codex_review:
        raise ValueError("real Codex CLI review requires --enable-codex-review")
    if review_provider not in {"manual", "codex", "codex-cli"}:
        raise ValueError("real Codex CLI review requires --review-provider codex-cli")

    def provider(_item: dict[str, Any], request: dict[str, Any], extract: dict[str, Any]) -> dict[str, Any]:
        debug_dir = Path(str(request["_watchbrief_debug_dir"])) if request.get("_watchbrief_debug_dir") else None
        return run_codex_review(
            extract,
            enable_codex_review=True,
            review_provider="codex-cli",
            model=model,
            codex_model=codex_model,
            codex_home=codex_home,
            codex_home_root=codex_home_root,
            codex_account=codex_account,
            timeout=timeout,
            report_target=normalize_report_target(request.get("report_target") or DEFAULT_REPORT_TARGET),
            debug_dir=debug_dir,
        )

    return provider


def effective_cookies_from_browser(args: argparse.Namespace) -> str | None:
    if args.bilibili_cookies_from_browser:
        return str(args.bilibili_cookies_from_browser)
    return None


def effective_cookie_browser_attempts(args: argparse.Namespace) -> tuple[str, ...]:
    if args.bilibili_cookies_from_browser:
        return ()
    if args.no_browser_auth or args.bilibili_cookies_file:
        return ()
    return DEFAULT_COOKIE_BROWSERS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run WatchBefore video content pipeline.")
    parser.add_argument("--version", action="version", version=f"WatchBefore {WATCHBRIEF_VERSION.removeprefix('watchbrief_')}")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-url", dest="source_url", help="single video URL or list URL")
    source_group.add_argument("--source-file", dest="source_file", help="local file with one URL per line")
    parser.add_argument("--output-dir", help="single-video HTML parent. For formal list runs, parent folder for the playlist-named delivery folder.")
    parser.add_argument(
        "--diagnostic-run",
        "--repro-run",
        dest="diagnostic_run",
        action="store_true",
        help="route diagnosis/repro artifacts to a temporary directory by default and mark generated HTML as diagnostic",
    )
    parser.add_argument("--work-dir", type=Path, help="temporary work folder for downloads/transcripts")
    parser.add_argument("--debug-dir", type=Path, help="debug artifact folder for manifest, payloads, and Codex traces")
    parser.add_argument("--keep-debug-artifacts", action="store_true", help="keep default temporary debug artifacts after a successful run")
    parser.add_argument("--open-output", action="store_true", help="open final HTML output after completion. Defaults to disabled.")
    parser.add_argument(
        "--mock-review-response",
        type=Path,
        help="mock watch-review response JSON object used for each item",
    )
    parser.add_argument(
        "--review-provider",
        choices=("auto", "manual", "mock", "codex", "codex-cli", "local", "local-rules", "gemini", "claude", "kimi", "openai-compatible"),
        default="auto",
    )
    parser.add_argument(
        "--enable-codex-review",
        action="store_true",
        help="explicitly allow real Codex review call",
    )
    parser.add_argument("--model", default="gpt-5.5", help="compat alias for --codex-model")
    parser.add_argument("--codex-model", help="Codex CLI model when real review is enabled")
    parser.add_argument("--codex-home", help="explicit CODEX_HOME directory for Codex CLI")
    parser.add_argument("--codex-home-root", help="root directory containing Codex account homes")
    parser.add_argument("--codex-account", help="account folder under --codex-home-root")
    parser.add_argument("--review-model", help="model id for Gemini/Claude/Kimi/OpenAI-compatible review providers")
    parser.add_argument("--review-api-base", help="API base for OpenAI-compatible review providers")
    parser.add_argument("--review-api-key-env", help="environment variable name that contains the review provider API key")
    parser.add_argument(
        "--extract-provider",
        choices=("local-qwen", "local-openai-compatible", "openai-compatible", "gemini", "claude", "kimi", "codex-cli-extract"),
        default="local-qwen",
        help="transcript extraction backend before review",
    )
    parser.add_argument("--extract-model", help="model id for non-Qwen extraction providers")
    parser.add_argument("--extract-api-base", help="API base for non-Qwen extraction providers")
    parser.add_argument("--extract-api-key-env", help="environment variable name that contains the extraction provider API key")
    parser.add_argument("--force-reanalysis", action="store_true", help="ignore validated report cache and run Codex review again")
    parser.add_argument(
        "--analysis-mode",
        choices=tuple(ANALYSIS_MODE_DEFAULTS.keys()),
        default="standard",
        help="analysis strength preset: fast uses 600s/cache reuse; standard uses 900s; deep uses 1200s plus reanalysis/debug defaults",
    )
    parser.add_argument(
        "--report-target",
        choices=REPORT_TARGETS,
        default=DEFAULT_REPORT_TARGET,
        help=f"report target mode: {', '.join(REPORT_TARGETS)}",
    )
    parser.add_argument(
        "--preference-profile",
        type=Path,
        default=DEFAULT_PREFERENCE_PROFILE,
        help=(
            "required WatchBefore preference profile; defaults to "
            "~/.watchbrief/preferences/seth.yaml and the run stops if it is missing or invalid"
        ),
    )
    parser.add_argument("--timeout", type=int, help="model/review timeout seconds. Defaults to the selected --analysis-mode preset")
    parser.add_argument("--transcriber", choices=("auto", "mlx_audio", "whisper"), default="auto", help="transcriber provider. auto means MiMo MLX only.")
    parser.add_argument("--mlx-model", help="MiMo MLX transcription model id")
    parser.add_argument("--whisper-model", default="base", help="Whisper model when --transcriber whisper or explicit fallback is used")
    parser.add_argument("--allow-whisper-fallback", action="store_true", help="allow Whisper only if MiMo MLX is unavailable or fails")
    visual_group = parser.add_mutually_exclusive_group()
    visual_group.add_argument(
        "--visual-analysis",
        dest="visual_analysis",
        action="store_const",
        const="on",
        help="force MiMo-VL frame evidence for every video",
    )
    visual_group.add_argument(
        "--no-visual-analysis",
        dest="visual_analysis",
        action="store_const",
        const="off",
        help="disable MiMo-VL even when transcript cues suggest visual dependence",
    )
    parser.set_defaults(visual_analysis="auto")
    parser.add_argument(
        "--mimo-vl-root",
        type=Path,
        help="MiMo-VL runtime root; defaults to WATCHBRIEF_MIMO_VL_ROOT or ~/Documents/MiMo-VL-7B-RL-2508",
    )
    parser.add_argument(
        "--visual-max-frame-gap-seconds",
        type=float,
        default=DEFAULT_MAX_FRAME_GAP_SECONDS,
        help="maximum deterministic gap between full-timeline frames; default 10 seconds",
    )
    parser.add_argument(
        "--visual-max-tokens",
        type=int,
        default=4096,
        help="maximum MiMo-VL JSON response tokens",
    )
    parser.add_argument(
        "--visual-max-frames-per-request",
        type=int,
        default=6,
        help="MiMo-VL images per request, from 1 to 6; default 6",
    )
    parser.add_argument(
        "--visual-timeout",
        type=int,
        help="MiMo-VL inference timeout seconds; defaults to --timeout",
    )
    parser.add_argument("--qwen-model", help=f"Qwen-family local extraction model id; defaults to WATCHBRIEF_QWEN_MODEL or {DEFAULT_QWEN_MODEL}")
    parser.add_argument("--qwen-api-base", help="LM Studio API base for Qwen local extraction; defaults to WATCHBRIEF_QWEN_API_BASE or localhost:1234/v1")
    parser.add_argument("--qwen-timeout", type=int, help="Qwen local_extract timeout seconds. Defaults to --timeout.")
    parser.add_argument(
        "--scoring-profile",
        choices=tuple(SCORING_PROFILES.keys()) + ("custom",),
        default=DEFAULT_SCORING_PROFILE,
        help="deterministic scoring preference: personal-interest or custom",
    )
    parser.add_argument(
        "--scoring-weights",
        help="custom scoring weights, e.g. information_density=0.35,evidence_quality=0.35,topic_interest=0.3",
    )
    parser.add_argument(
        "--bilibili-cookies-from-browser",
        "--cookies-from-browser",
        dest="bilibili_cookies_from_browser",
        help=f"explicit browser cookie source for platform acquisition, e.g. chrome, safari, edge. Defaults to {' -> '.join(DEFAULT_COOKIE_BROWSERS)}",
    )
    parser.add_argument(
        "--bilibili-cookies-file",
        "--cookies-file",
        dest="bilibili_cookies_file",
        type=Path,
        help="explicit cookies.txt file for yt-dlp and Bilibili fallback",
    )
    parser.add_argument("--no-browser-auth", action="store_true", help="disable authorized browser session use")
    parser.add_argument(
        "--xiaohongshu-media-refresh-browser",
        choices=("arc", "chrome", "safari"),
        default="",
        help="explicit browser allowed to refresh Xiaohongshu media URLs after an audio acquisition failure; omitted disables browser refresh",
    )
    parser.add_argument(
        "--force-audio-transcription",
        action="store_true",
        help="skip platform subtitles and explicitly download audio for MiMo/selected transcriber",
    )
    parser.add_argument(
        "--no-playlist-expansion",
        action="store_true",
        help="diagnostic/rerun mode: process only the current URL item and do not expand playlists or Bilibili multi-P pages",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    resolve_auto_review_provider(args)
    if args.mimo_vl_root and args.visual_analysis == "off":
        raise SystemExit("--mimo-vl-root cannot be used with --no-visual-analysis")
    if args.visual_max_frame_gap_seconds <= 0:
        raise SystemExit("--visual-max-frame-gap-seconds must be positive")
    if args.visual_max_tokens <= 0:
        raise SystemExit("--visual-max-tokens must be positive")
    if not 1 <= args.visual_max_frames_per_request <= 6:
        raise SystemExit("--visual-max-frames-per-request must be between 1 and 6")
    if args.visual_timeout is not None and args.visual_timeout <= 0:
        raise SystemExit("--visual-timeout must be positive")

    if (args.review_provider == "codex-cli" or args.extract_provider == "codex-cli-extract") and not args.codex_home:
        if not args.codex_home_root:
            args.codex_home_root = DEFAULT_WATCHBRIEF_CODEX_HOME_ROOT
        if not args.codex_account:
            args.codex_account = read_current_watchbrief_codex_account(watchbrief_codex_root(args.codex_home_root))

    source_path: Path | None = None
    mode_defaults = ANALYSIS_MODE_DEFAULTS[args.analysis_mode]
    if args.timeout is None:
        args.timeout = int(mode_defaults["timeout"])
    if not args.force_reanalysis and bool(mode_defaults["force_reanalysis"]):
        args.force_reanalysis = True
    if not args.keep_debug_artifacts and bool(mode_defaults["keep_debug_artifacts"]):
        args.keep_debug_artifacts = True
    try:
        scoring_weights = parse_scoring_weights(args.scoring_weights) if args.scoring_weights else weights_for_profile(args.scoring_profile)
    except ValueError as exc:
        raise SystemExit(str(exc))
    scoring_formula_version = formula_version_for_weights(scoring_weights)
    try:
        preference_profile = load_preference_profile_metadata(args.preference_profile)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc))

    if args.source_file and args.source_file != "-":
        source_path = Path(args.source_file).expanduser()
        if not source_path.exists():
            raise SystemExit(f"source file not found: {source_path}")
        source_resolver = make_resolver_for_source_file(source_path)
        source_value = str(source_path)
    else:
        source_resolver = resolve_url
        source_value = args.source_url

    try:
        review_provider = make_review_response_provider(
            mock_review_response=args.mock_review_response,
            review_provider=args.review_provider,
            enable_codex_review=args.enable_codex_review,
            model=args.model,
            codex_model=args.codex_model,
            codex_home=args.codex_home,
            codex_home_root=args.codex_home_root,
            codex_account=args.codex_account,
            review_model=args.review_model,
            review_api_base=args.review_api_base,
            review_api_key_env=args.review_api_key_env,
            timeout=args.timeout,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))

    resolver_options: dict[str, Any] = {
        "allow_browser_auth": not args.no_browser_auth,
        "allow_playlist_expansion": not args.no_playlist_expansion,
    }
    subtitle_options: dict[str, Any] = {}
    audio_download_options: dict[str, Any] = {}
    cookies_from_browser = effective_cookies_from_browser(args)
    if cookies_from_browser:
        resolver_options["cookies_from_browser"] = cookies_from_browser
        subtitle_options["cookies_from_browser"] = cookies_from_browser
        audio_download_options["cookies_from_browser"] = cookies_from_browser
    else:
        cookie_browser_attempts = effective_cookie_browser_attempts(args)
        if cookie_browser_attempts:
            resolver_options["cookie_browser_attempts"] = list(cookie_browser_attempts)
            subtitle_options["cookie_browser_attempts"] = list(cookie_browser_attempts)
            audio_download_options["cookie_browser_attempts"] = list(cookie_browser_attempts)
    if args.bilibili_cookies_file:
        cookie_file = args.bilibili_cookies_file.expanduser()
        resolver_options["cookie_file"] = cookie_file
        subtitle_options["cookie_file"] = cookie_file
        audio_download_options["cookie_file"] = cookie_file
    visual_download_options = dict(audio_download_options)

    expected_source_kind = "list" if source_path is not None else "single"
    cached_resolution: dict[str, Any] | None = None
    cached_resolution_error: BaseException | None = None
    should_pre_resolve_url = (
        source_path is None
        and (
            args.output_dir is None
            or (not args.diagnostic_run and source_url_may_expand_to_list(source_value))
        )
    )
    if should_pre_resolve_url:
        try:
            cached_resolution = resolve_url(source_value, **resolver_options)
            expected_source_kind = str(cached_resolution.get("source_kind") or "single")
        except BaseException as exc:
            cached_resolution_error = exc
            expected_source_kind = "single"

    if cached_resolution is not None or cached_resolution_error is not None:
        def source_resolver(_source: str) -> dict[str, Any]:
            if cached_resolution_error is not None:
                raise cached_resolution_error
            return dict(cached_resolution or {})

    transcriber_options: dict[str, Any] = {
        "provider": args.transcriber,
        "whisper_model": args.whisper_model,
        "allow_whisper_fallback": args.allow_whisper_fallback,
    }
    if args.mlx_model:
        transcriber_options["model"] = args.mlx_model

    local_extract_options: dict[str, Any] = {}
    build_local_extract_func = None
    if args.extract_provider == "local-qwen":
        if args.qwen_model:
            local_extract_options["qwen_model"] = args.qwen_model
        if args.qwen_api_base:
            local_extract_options["qwen_api_base"] = args.qwen_api_base
        local_extract_options["qwen_timeout"] = args.qwen_timeout if args.qwen_timeout is not None else args.timeout
    else:
        extract_timeout = args.qwen_timeout if args.qwen_timeout is not None else args.timeout
        extract_api_base = (
            args.extract_api_base
            or (args.qwen_api_base if args.extract_provider == "local-openai-compatible" else "")
            or DEFAULT_EXTRACT_API_BASES.get(args.extract_provider, "")
        )
        local_extract_options.update({
            "external_extract_provider": args.extract_provider,
            "external_model_id": args.extract_model or "",
            "external_api_base": extract_api_base,
            "qwen_timeout": extract_timeout,
        })

        def external_extract_func(metadata: dict[str, Any], transcript_segments: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
            kwargs.pop("external_extract_provider", None)
            kwargs.pop("external_model_id", None)
            kwargs.pop("external_api_base", None)
            kwargs.pop("qwen_timeout", None)
            return build_external_extract_payload(
                metadata,
                transcript_segments,
                provider=args.extract_provider,
                model=args.extract_model or "",
                api_base=extract_api_base,
                api_key_env=args.extract_api_key_env or "",
                timeout=extract_timeout,
                codex_home=args.codex_home,
                codex_home_root=args.codex_home_root,
                codex_account=args.codex_account,
                **kwargs,
            )

        build_local_extract_func = external_extract_func

    if args.review_provider in {"local", "local-rules"}:
        review_model_id = LOCAL_REVIEW_MODEL_ID
    elif args.review_provider in EXTERNAL_REVIEW_PROVIDERS:
        review_model_id = args.review_model or args.review_provider
    else:
        review_model_id = args.codex_model or args.model
    def parse_review_response_with_scoring(raw_response: Any, **kwargs: Any) -> dict[str, Any]:
        return parse_review_response(raw_response, scoring_weights=scoring_weights, **kwargs)

    visual_analyzer: MimoVLVisualAnalyzer | None = None
    def get_visual_analyzer() -> MimoVLVisualAnalyzer:
        nonlocal visual_analyzer
        if visual_analyzer is None:
            visual_analyzer = MimoVLVisualAnalyzer(
                root=args.mimo_vl_root,
                max_tokens=args.visual_max_tokens,
                max_frames_per_request=args.visual_max_frames_per_request,
                timeout=args.visual_timeout if args.visual_timeout is not None else args.timeout,
                working_directory=Path(__file__).resolve().parents[1],
            )
        return visual_analyzer

    def analyze_visual_when_needed(*args_: Any, **kwargs: Any) -> dict[str, Any]:
        return get_visual_analyzer().analyze_video(*args_, **kwargs)

    if args.visual_analysis == "on":
        try:
            get_visual_analyzer()
        except (VisualAnalysisError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc

    deps = PipelineDependencies(
        resolve_url_func=source_resolver,
        build_local_extract_func=build_local_extract_func or build_local_extract_payload,
        parse_review_response_func=parse_review_response_with_scoring,
        resolver_options=resolver_options if source_path is not None or source_resolver is resolve_url else {},
        subtitle_options=subtitle_options,
        audio_download_options=audio_download_options,
        visual_download_options=visual_download_options,
        media_url_refresh_browser=args.xiaohongshu_media_refresh_browser,
        transcriber_options=transcriber_options,
        analyze_visual_func=(visual_analyzer.analyze_video if args.visual_analysis == "on" else analyze_visual_when_needed if args.visual_analysis == "auto" else None),
        prepare_visual_func=get_visual_analyzer if args.visual_analysis == "auto" else None,
        visual_analysis_options={
            "max_gap_seconds": args.visual_max_frame_gap_seconds,
        } if args.visual_analysis != "off" else {},
        visual_analysis_mode=args.visual_analysis,
        local_extract_options=local_extract_options,
        review_options={
            "analysis_mode": args.analysis_mode,
            "report_target": args.report_target,
            "codex_model": review_model_id,
            "report_cache_enabled": True,
            "force_reanalysis": args.force_reanalysis,
            "scoring_weights": scoring_weights,
            "scoring_formula_version": scoring_formula_version,
            "preference_profile": preference_profile,
        },
        force_audio_transcription=args.force_audio_transcription,
    )
    explicit_output_dir = args.output_dir is not None
    has_resolved_list_title = expected_source_kind == "list" and bool(list_title_from_resolution(cached_resolution))
    task_name = task_name_for_delivery(
        source_value,
        source_file=source_path,
        expected_source_kind=expected_source_kind,
        cached_resolution=cached_resolution,
    )
    if explicit_output_dir:
        explicit_output_path = Path(args.output_dir).expanduser()
        if expected_source_kind == "list" and not args.diagnostic_run:
            output_dir = default_list_output_dir(
                task_name,
                parent=explicit_output_path,
                stable_name=has_resolved_list_title,
            )
        else:
            output_dir = explicit_output_path
    elif args.diagnostic_run:
        output_dir = default_diagnostic_output_dir(task_name)
    elif expected_source_kind == "list":
        output_dir = default_list_output_dir(task_name, stable_name=has_resolved_list_title)
    else:
        output_dir = Path.home() / "Desktop"
    temporary_debug_dir: Path | None = None
    explicit_debug_dir = args.debug_dir is not None
    debug_dir = args.debug_dir.expanduser() if args.debug_dir else None
    if debug_dir is None:
        if args.diagnostic_run:
            debug_dir = output_dir / "_debug"
        else:
            temporary_debug_dir = Path(tempfile.mkdtemp(prefix="watchbrief_v5_debug_"))
            debug_dir = temporary_debug_dir
    work_dir = args.work_dir.expanduser() if args.work_dir else None

    manifest_path = (debug_dir or output_dir) / "manifest.json"
    try:
        manifest = process_source(
            source_value,
            output_dir,
            review_response_provider=review_provider,
            deps=deps,
            work_dir=work_dir,
            debug_dir=debug_dir,
    )
    except BaseException:
        if temporary_debug_dir and temporary_debug_dir.exists():
            print(f"debug_artifacts: {temporary_debug_dir}")
        raise
    finally:
        if visual_analyzer is not None:
            visual_analyzer.close()
    if args.diagnostic_run:
        manifest.update({
            "run_mode": "diagnostic",
            "artifact_class": "diagnostic",
            "diagnostic_run": True,
            "diagnostic_output_dir": str(output_dir),
            "diagnostic_output_explicit": explicit_output_dir,
        })
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"manifest_path: {manifest_path}")

    for item in manifest.get("items", []):
        print(f"[{item.get('status')}] {item.get('title')} -> {item.get('html_path', '')}")
        if item.get("status") == "skipped":
            print(f"  stage={item.get('stage', 'resolver')} reason={item.get('reason_code', 'skipped')} message={item.get('error', 'skipped')}")
        if item.get("status") == "failed":
            error = item.get("error") or {}
            stage = str(error.get("stage", "pipeline"))
            reason = str(error.get("reason_code", "pipeline_failed"))
            message = str(error.get("error", "unknown"))
            print(f"  stage={stage} reason={reason} message={message}")
            if needs_manual_youtube_verification(
                error,
                item,
                {"transcript_fallback_debug": manifest.get("transcript_fallback_debug")},
            ):
                url = str(item.get("url") or source_value)
                print("  manual_verification_required: open Chrome and complete YouTube verification manually")
                print(f"  manual_verification_open: {shell_command(chrome_verification_command(url))}")
                print(f"  manual_verification_probe: {shell_command(ytdlp_subtitle_probe_command(url, cookies_from_browser or DEFAULT_COOKIES_FROM_BROWSER))}")

    for output_path in output_paths_from_manifest(manifest):
        if manifest.get("source_kind") == "list" and output_path == str(manifest.get("watch_order_path") or ""):
            label = "Diagnostic Watch Order" if args.diagnostic_run else "Watch Order"
            print(f"{label}: {output_path}")
        else:
            label = "Diagnostic HTML" if args.diagnostic_run else "HTML"
            print(f"{label}: {output_path}")
    if args.open_output:
        open_output_paths(manifest)
    if temporary_debug_dir and manifest.get("failed_count", 0) == 0 and not args.keep_debug_artifacts:
        shutil.rmtree(temporary_debug_dir, ignore_errors=True)
        print("debug_artifacts: removed after successful delivery")
    elif temporary_debug_dir and manifest.get("failed_count", 0) == 0:
        print(f"debug_artifacts: {temporary_debug_dir}")
    elif temporary_debug_dir:
        print(f"debug_artifacts: {temporary_debug_dir}")
    elif args.diagnostic_run and debug_dir:
        print(f"diagnostic_artifacts: {output_dir}")
        print(f"debug_artifacts: {debug_dir}")
    elif explicit_debug_dir:
        print(f"debug_artifacts: {debug_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as exc:
        print(f"pipeline_failed stage={exc.stage} reason={exc.reason_code} message={exc.message}")
        raise SystemExit(2)
    except Exception as exc:
        print(f"pipeline_failed stage=pipeline reason=unexpected message={exc}")
        raise SystemExit(2)
