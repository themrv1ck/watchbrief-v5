#!/usr/bin/env python3
"""Platform subtitle fetcher for WatchBrief V5 acquisition layer."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

try:
    from .acquisition_errors import BilibiliSubtitleError, PlatformRestrictionError, SubtitleUnavailableError, is_platform_restriction
    from .audio_downloader import is_bilibili_url
    from .bilibili_content_provider import fetch_bilibili_subtitle
    from .cookie_strategy import attach_cookie_debug, cookie_debug, cookie_fallback_reason, normalize_cookie_browser_attempts
    from .transcript_source_adapter import load_transcript_source
except ImportError:  # pragma: no cover
    from acquisition_errors import BilibiliSubtitleError, PlatformRestrictionError, SubtitleUnavailableError, is_platform_restriction
    from audio_downloader import is_bilibili_url
    from bilibili_content_provider import fetch_bilibili_subtitle
    from cookie_strategy import attach_cookie_debug, cookie_debug, cookie_fallback_reason, normalize_cookie_browser_attempts
    from transcript_source_adapter import load_transcript_source


SUBTITLE_EXTENSIONS = (".srt", ".vtt")
DEFAULT_SUBTITLE_LANGUAGES = ("en", "en-US", "en-GB", "en-orig", "zh-Hans", "zh")
ENGLISH_SUBTITLE_PRIORITY = ("en", "en-US", "en-GB", "en-orig")
SUBTITLE_FORMATS = ("vtt", "srt", "ttml", "srv3", "srv2", "srv1", "json3")


@dataclass
class SubtitleFetchResult:
    subtitle_path: Path
    language: str
    material: dict[str, Any]
    command: list[str]
    debug: dict[str, Any]


def subtitle_language_from_name(path: Path) -> str:
    parts = path.name.split(".")
    if len(parts) >= 3:
        return parts[-2]
    return ""


def is_youtube_url(url: str) -> bool:
    host = urlparse(str(url or "")).netloc.lower()
    return host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com"} or host.endswith(".youtube.com")


def add_cookie_args(command: list[str], cookies_from_browser: Optional[str], cookie_file: Optional[Path]) -> list[str]:
    if cookies_from_browser:
        command = [*command[:-1], "--cookies-from-browser", str(cookies_from_browser), command[-1]]
    if cookie_file:
        command = [*command[:-1], "--cookies", str(Path(cookie_file).expanduser()), command[-1]]
    return command


def english_language_rank(language: str) -> int:
    value = str(language or "").strip()
    lowered = value.lower()
    for index, priority in enumerate(ENGLISH_SUBTITLE_PRIORITY):
        if lowered == priority.lower():
            return index
    if lowered.startswith("en-"):
        return len(ENGLISH_SUBTITLE_PRIORITY)
    return 10_000


def is_english_subtitle_language(language: str) -> bool:
    return english_language_rank(language) < 10_000


def subtitle_format_from_name(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix if suffix in {"vtt", "srt"} else suffix


def stderr_summary(stderr: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(stderr or "")).strip()
    return text[:limit]


def subtitle_source_debug(
    *,
    language: str,
    kind: str,
    subtitle_format: str,
    candidates: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    probe_attempted: bool,
    probe_failed: bool = False,
    fetch_reason: str = "selected_subtitle_available",
) -> dict[str, Any]:
    return {
        "subtitle_probe_attempted": probe_attempted,
        "subtitle_candidates": candidates,
        "subtitle_candidate_failures": failures,
        "selected_subtitle_lang": language,
        "selected_subtitle_kind": kind,
        "selected_subtitle_format": subtitle_format,
        "subtitle_fetch_reason": fetch_reason,
        "subtitle_probe_failed": probe_failed,
        "audio_downloader_skipped_due_to_subtitle": True,
    }


def pick_subtitle_file(output_dir: Path, language_priority: tuple[str, ...]) -> Path:
    candidates = [
        path for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUBTITLE_EXTENSIONS and path.stat().st_size > 0
    ]
    if not candidates:
        raise SubtitleUnavailableError("platform subtitle fetch produced no subtitle files")
    for language in language_priority:
        for candidate in candidates:
            if f".{language}." in candidate.name or candidate.name.endswith(f".{language}{candidate.suffix}"):
                return candidate
    return sorted(candidates)[0]


def parse_youtube_subtitle_listing(stdout: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    section = ""
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "Available automatic captions" in line:
            section = "automatic"
            continue
        if "Available subtitles" in line:
            section = "manual"
            continue
        if not section or line.startswith("[") or line.lower().startswith("language "):
            continue
        language = line.split(maxsplit=1)[0].strip()
        if not language or language.lower() == "language":
            continue
        formats = [
            fmt for fmt in SUBTITLE_FORMATS
            if re.search(rf"(^|[\s,]){re.escape(fmt)}($|[\s,])", line)
        ]
        if not formats:
            continue
        kind = section
        if section == "automatic" and not is_english_subtitle_language(language):
            kind = "translated"
        entries.append({"language": language, "kind": kind, "formats": formats})
    return entries


def normalize_language_preferences(languages: tuple[str, ...]) -> tuple[str, ...]:
    configured = tuple(language for language in languages if str(language or "").strip())
    merged = [*DEFAULT_SUBTITLE_LANGUAGES, *configured]
    seen: set[str] = set()
    result: list[str] = []
    for language in merged:
        key = language.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(language)
    return tuple(result)


def language_preference_rank(language: str, languages: tuple[str, ...]) -> int:
    english_rank = english_language_rank(language)
    if english_rank < 10_000:
        return english_rank
    lowered = str(language or "").lower()
    for index, configured in enumerate(languages):
        if lowered == configured.lower():
            return 100 + index
    return 1_000


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (str(candidate["language"]).lower(), str(candidate["kind"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def build_youtube_subtitle_candidates(
    listing: list[dict[str, Any]],
    languages: tuple[str, ...],
) -> list[dict[str, Any]]:
    preferences = normalize_language_preferences(languages)
    if listing:
        manual_english = [
            entry for entry in listing
            if entry.get("kind") == "manual" and is_english_subtitle_language(str(entry.get("language") or ""))
        ]
        automatic_english = [
            entry for entry in listing
            if entry.get("kind") == "automatic" and is_english_subtitle_language(str(entry.get("language") or ""))
        ]
        configured_manual = [
            entry for entry in listing
            if entry.get("kind") == "manual"
            and not is_english_subtitle_language(str(entry.get("language") or ""))
            and str(entry.get("language") or "").lower() in {language.lower() for language in preferences}
        ]
        translated = [
            entry for entry in listing
            if entry.get("kind") == "translated"
            and str(entry.get("language") or "").lower() in {language.lower() for language in preferences}
        ]
        ordered = [
            *sorted(manual_english, key=lambda item: language_preference_rank(str(item.get("language") or ""), preferences)),
            *sorted(automatic_english, key=lambda item: language_preference_rank(str(item.get("language") or ""), preferences)),
            *sorted(configured_manual, key=lambda item: language_preference_rank(str(item.get("language") or ""), preferences)),
            *sorted(translated, key=lambda item: language_preference_rank(str(item.get("language") or ""), preferences)),
        ]
        return dedupe_candidates([
            {"language": str(item["language"]), "kind": str(item["kind"]), "formats": item.get("formats") or []}
            for item in ordered
        ])

    fallback: list[dict[str, Any]] = []
    for language in preferences:
        if is_english_subtitle_language(language):
            fallback.append({"language": language, "kind": "manual", "formats": ["vtt", "srt"]})
    for language in preferences:
        if is_english_subtitle_language(language):
            fallback.append({"language": language, "kind": "automatic", "formats": ["vtt", "srt"]})
    for language in preferences:
        if not is_english_subtitle_language(language):
            fallback.append({"language": language, "kind": "manual", "formats": ["vtt", "srt"]})
    for language in preferences:
        if not is_english_subtitle_language(language):
            fallback.append({"language": language, "kind": "translated", "formats": ["vtt", "srt"]})
    return dedupe_candidates(fallback)


def youtube_probe_command(
    url: str,
    *,
    cookies_from_browser: Optional[str],
    cookie_file: Optional[Path],
) -> list[str]:
    command = ["yt-dlp", "--skip-download", "--list-subs", url]
    return add_cookie_args(command, cookies_from_browser, cookie_file)


def youtube_download_command(
    url: str,
    output_dir: Path,
    candidate: dict[str, Any],
    *,
    cookies_from_browser: Optional[str],
    cookie_file: Optional[Path],
) -> list[str]:
    command = ["yt-dlp", "--skip-download"]
    if candidate["kind"] == "manual":
        command.append("--write-subs")
    else:
        command.append("--write-auto-subs")
    command.extend([
        "--sub-langs",
        str(candidate["language"]),
        "--sub-format",
        "vtt/srt/best",
        "--output",
        str(output_dir / "%(id)s.%(ext)s"),
        url,
    ])
    return add_cookie_args(command, cookies_from_browser, cookie_file)


def fetch_youtube_subtitles(
    url: str,
    output_dir: Path,
    *,
    languages: tuple[str, ...],
    cookies_from_browser: Optional[str],
    cookie_file: Optional[Path],
    runner: Any,
    timeout: int,
) -> SubtitleFetchResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, Any]] = []
    probe_command = youtube_probe_command(url, cookies_from_browser=cookies_from_browser, cookie_file=cookie_file)
    probe_failed = False
    listing: list[dict[str, Any]] = []
    probe_result = runner(probe_command, capture_output=True, text=True, timeout=timeout)
    if probe_result.returncode == 0:
        listing = parse_youtube_subtitle_listing(str(probe_result.stdout or ""))
    else:
        probe_failed = True
        failures.append({
            "language": "",
            "kind": "probe",
            "reason_code": "subtitle_probe_failed",
            "stderr": stderr_summary(str(probe_result.stderr or "")),
        })

    candidates = build_youtube_subtitle_candidates(listing, languages)
    if not candidates:
        error = SubtitleUnavailableError("YouTube subtitle probe produced no usable subtitle candidates", probe_command, str(probe_result.stderr or ""))
        error.debug = {
            "subtitle_probe_attempted": True,
            "subtitle_candidates": [],
            "subtitle_candidate_failures": failures,
            "subtitle_fetch_reason": "no_subtitle_candidates",
            "subtitle_probe_failed": probe_failed,
            "audio_downloader_skipped_due_to_subtitle": False,
        }
        raise error

    last_command = probe_command
    last_stderr = str(probe_result.stderr or "")
    for index, candidate in enumerate(candidates, 1):
        candidate_dir = output_dir / f"candidate-{index:02d}-{candidate['kind']}-{safe_path_part(str(candidate['language']))}"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        command = youtube_download_command(
            url,
            candidate_dir,
            candidate,
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookie_file,
        )
        last_command = command
        result = runner(command, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            last_stderr = str(result.stderr or "")
            failures.append({
                "language": candidate["language"],
                "kind": candidate["kind"],
                "reason_code": "subtitle_candidate_failed",
                "stderr": stderr_summary(last_stderr),
            })
            continue
        try:
            subtitle_path = pick_subtitle_file(candidate_dir, (str(candidate["language"]),))
        except SubtitleUnavailableError as exc:
            failures.append({
                "language": candidate["language"],
                "kind": candidate["kind"],
                "reason_code": "subtitle_candidate_no_file",
                "stderr": stderr_summary(exc.stderr),
            })
            continue
        language = subtitle_language_from_name(subtitle_path) or str(candidate["language"])
        selected_format = subtitle_format_from_name(subtitle_path)
        quality = "ok" if candidate["kind"] == "manual" else "degraded"
        material = load_transcript_source(subtitle_path, language=language, transcript_quality=quality)
        source = material.setdefault("source", {})
        if isinstance(source, dict):
            source.update({
                "provider": "watchbrief-youtube-subtitle-fetcher",
                "subtitle_kind": candidate["kind"],
                "subtitle_language": language,
                "subtitle_format": selected_format,
            })
        debug = subtitle_source_debug(
            language=language,
            kind=str(candidate["kind"]),
            subtitle_format=selected_format,
            candidates=candidates,
            failures=failures,
            probe_attempted=True,
            probe_failed=probe_failed,
        )
        return SubtitleFetchResult(subtitle_path=subtitle_path, language=language, material=material, command=command, debug=debug)

    error = SubtitleUnavailableError("all YouTube subtitle candidates failed", last_command, last_stderr)
    error.debug = {
        "subtitle_probe_attempted": True,
        "subtitle_candidates": candidates,
        "subtitle_candidate_failures": failures,
        "subtitle_fetch_reason": "all_subtitle_candidates_failed",
        "subtitle_probe_failed": probe_failed,
        "audio_downloader_skipped_due_to_subtitle": False,
    }
    raise error


def safe_path_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "subtitle"


def fetch_platform_subtitles(
    url: str,
    output_dir: Path,
    *,
    languages: tuple[str, ...] = DEFAULT_SUBTITLE_LANGUAGES,
    cookies_from_browser: Optional[str] = None,
    cookie_browser_attempts: Any = None,
    cookie_file: Optional[Path] = None,
    runner: Any = subprocess.run,
    timeout: int = 120,
) -> SubtitleFetchResult:
    attempts = normalize_cookie_browser_attempts(cookie_browser_attempts)
    if attempts and not cookies_from_browser and not cookie_file:
        fallback_reason = ""
        last_exc: BaseException | None = None
        for index, browser in enumerate(attempts):
            try:
                result = fetch_platform_subtitles(
                    url,
                    output_dir,
                    languages=languages,
                    cookies_from_browser=browser,
                    cookie_file=cookie_file,
                    runner=runner,
                    timeout=timeout,
                )
                result.debug.update(cookie_debug(attempts=attempts, selected=browser, fallback_reason=fallback_reason))
                return result
            except (PlatformRestrictionError, SubtitleUnavailableError, BilibiliSubtitleError) as exc:
                reason = cookie_fallback_reason(exc)
                attach_cookie_debug(exc, attempts=attempts, selected=browser, fallback_reason=reason or fallback_reason)
                last_exc = exc
                if not reason or index == len(attempts) - 1:
                    raise
                fallback_reason = reason
        if last_exc is not None:
            raise last_exc

    output_dir.mkdir(parents=True, exist_ok=True)
    if is_bilibili_url(url):
        result = fetch_bilibili_subtitle(
            url,
            output_dir,
            languages=languages,
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookie_file,
            timeout=timeout,
        )
        return SubtitleFetchResult(
            subtitle_path=result.subtitle_path,
            language=result.language,
            material=result.material,
            command=result.command,
            debug=result.debug,
        )

    if is_youtube_url(url):
        return fetch_youtube_subtitles(
            url,
            output_dir,
            languages=languages,
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookie_file,
            runner=runner,
            timeout=timeout,
        )

    language_arg = ",".join(languages)
    command = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        language_arg,
        "--sub-format",
        "vtt/srt/best",
        "--output",
        str(output_dir / "%(id)s.%(ext)s"),
        url,
    ]
    command = add_cookie_args(command, cookies_from_browser, cookie_file)
    result = runner(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr = str(result.stderr or "")
        if is_platform_restriction(stderr):
            raise PlatformRestrictionError("subtitle_fetcher", "platform restricted subtitle access", command, stderr)
        raise SubtitleUnavailableError("platform subtitles unavailable", command, stderr)

    subtitle_path = pick_subtitle_file(output_dir, languages)
    language = subtitle_language_from_name(subtitle_path) or languages[0]
    quality = "degraded" if "auto" in subtitle_path.name.lower() else "ok"
    material = load_transcript_source(subtitle_path, language=language, transcript_quality=quality)
    selected_format = subtitle_format_from_name(subtitle_path)
    debug = subtitle_source_debug(
        language=language,
        kind="manual" if quality == "ok" else "automatic",
        subtitle_format=selected_format,
        candidates=[{"language": language, "kind": "platform", "formats": [selected_format]}],
        failures=[],
        probe_attempted=False,
    )
    return SubtitleFetchResult(subtitle_path=subtitle_path, language=language, material=material, command=command, debug=debug)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch platform subtitles and normalize them into transcript material.")
    parser.add_argument("url")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--material-output", required=True, type=Path)
    args = parser.parse_args()
    result = fetch_platform_subtitles(args.url, args.output_dir)
    args.material_output.parent.mkdir(parents=True, exist_ok=True)
    args.material_output.write_text(json.dumps(result.material, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
