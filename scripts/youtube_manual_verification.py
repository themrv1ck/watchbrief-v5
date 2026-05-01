#!/usr/bin/env python3
"""Manual YouTube verification helpers.

These helpers only open the user's browser to the target URL and print the
read-only resolver probe command. They never interact with CAPTCHA or login
controls.
"""

from __future__ import annotations

import subprocess
import shlex
from typing import Any


BOT_VERIFICATION_MARKERS = (
    "platform_restriction",
    "sign in to confirm",
    "not a bot",
    "bot check",
    "login verification",
)


def needs_manual_youtube_verification(error: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            error.get("stage"),
            error.get("reason_code"),
            error.get("error"),
        )
    ).lower()
    return any(marker in text for marker in BOT_VERIFICATION_MARKERS)


def chrome_verification_command(url: str) -> list[str]:
    return ["open", "-a", "Google Chrome", str(url)]


def ytdlp_subtitle_probe_command(url: str, browser: str = "chrome") -> list[str]:
    return ["yt-dlp", "--cookies-from-browser", browser, "--skip-download", "--list-subs", str(url)]


def open_chrome_for_manual_verification(url: str, *, runner: Any = subprocess.run) -> None:
    runner(chrome_verification_command(url), check=True)


def shell_command(command: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)
