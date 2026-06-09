#!/usr/bin/env python3
"""Manual YouTube verification helpers.

These helpers only open the user's browser to the target URL and print the
read-only resolver probe command. They never interact with CAPTCHA or login
controls.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any, Iterable


BOT_VERIFICATION_MARKERS = (
    "platform_restriction",
    "sign in to confirm",
    "sign in",
    "not a bot",
    "bot check",
    "login verification",
    "login_required",
    "login required",
    "youtube is blocking requests from your ip",
    "blocking requests from your ip",
)

TRANSCRIPT_FALLBACK_FAILURE_MARKERS = (
    "youtube-connect",
    "youtube transcript fallback",
    "safari transcript fallback",
    "transcript_fallback_success false",
    "transcript fallback returned no usable transcript segments",
    "no usable transcript segments",
)


def _walk_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _walk_values(nested)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            yield from _walk_values(nested)
    elif value is not None:
        yield str(value)


def _context_text(*contexts: Any) -> str:
    return " ".join(piece for context in contexts for piece in _walk_values(context)).lower()


def _has_resolver_failure(text: str) -> bool:
    return "resolver_failed" in text or ("resolver" in text and "failed" in text)


def _has_transcript_fallback_failure(text: str) -> bool:
    has_fallback_marker = any(marker in text for marker in TRANSCRIPT_FALLBACK_FAILURE_MARKERS)
    has_failure_marker = (
        "transcript_fallback_success false" in text
        or ("transcript_fallback_success" in text and "false" in text)
        or "returned no usable transcript segments" in text
        or "no usable transcript segments" in text
        or "unavailable" in text
        or "failed" in text
    )
    return has_fallback_marker and has_failure_marker


def needs_manual_youtube_verification(*contexts: Any) -> bool:
    text = _context_text(*contexts)
    if any(marker in text for marker in BOT_VERIFICATION_MARKERS):
        return True
    return _has_resolver_failure(text) and _has_transcript_fallback_failure(text)


def chrome_verification_command(url: str) -> list[str]:
    return ["open", "-a", "Google Chrome", str(url)]


def ytdlp_subtitle_probe_command(url: str, browser: str = "chrome") -> list[str]:
    return ["yt-dlp", "--cookies-from-browser", browser, "--skip-download", "--list-subs", str(url)]


def open_chrome_for_manual_verification(url: str, *, runner: Any = subprocess.run) -> None:
    runner(chrome_verification_command(url), check=True)


def shell_command(command: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)
