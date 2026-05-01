"""Shared browser cookie selection policy for WatchBrief acquisition stages."""

from __future__ import annotations

import json
from typing import Any, Iterable


DEFAULT_COOKIE_BROWSER_ATTEMPTS = ("chrome", "safari")


def normalize_cookie_browser_attempts(value: Any) -> tuple[str, ...]:
    if value in (None, "", False):
        return ()
    if isinstance(value, str):
        raw_values: Iterable[Any] = value.split(",")
    else:
        raw_values = value
    attempts: list[str] = []
    for raw in raw_values:
        browser = str(raw or "").strip()
        if browser and browser not in attempts:
            attempts.append(browser)
    return tuple(attempts)


def browser_from_cookie_source_label(label: str) -> str:
    prefix = "browser:"
    text = str(label or "")
    return text[len(prefix):] if text.startswith(prefix) else ""


def browser_attempts_from_source_labels(labels: list[str]) -> list[str]:
    return [browser for browser in (browser_from_cookie_source_label(label) for label in labels) if browser]


def cookie_fallback_reason_from_text(reason_code: str = "", stderr: str = "", debug: Any = None) -> str:
    reason = str(reason_code or "").strip()
    debug_text = ""
    if isinstance(debug, dict) and debug:
        debug_text = json.dumps(debug, ensure_ascii=False, sort_keys=True)
    text = " ".join((reason, str(stderr or ""), debug_text)).lower()
    if "extracted 0 cookies" in text:
        return "extracted_0_cookies"
    if "login_required_for_subtitle" in text:
        return "login_required_for_subtitle"
    if "platform_restriction" in text:
        return "platform_restriction"
    if "browser cookie" in text or "cookies database" in text or "could not find" in text:
        return "browser_cookie_access_error"
    if "no cookies" in text or "cookie missing" in text or "bilibili_cookie_missing" in text:
        return "browser_cookie_access_error"
    if "sign in" in text or "not a bot" in text or "login required" in text or "not logged in" in text:
        return "platform_restriction"
    if "http error 403" in text or "forbidden" in text:
        return "platform_restriction"
    if "no video formats found" in text or "no formats found" in text:
        return "no_video_formats"
    return ""


def cookie_fallback_reason(exc: BaseException) -> str:
    return cookie_fallback_reason_from_text(
        str(getattr(exc, "reason_code", "") or ""),
        str(getattr(exc, "stderr", "") or ""),
        getattr(exc, "debug", None),
    )


def cookie_debug(*, attempts: Iterable[str], selected: str = "", fallback_reason: str = "") -> dict[str, Any]:
    return {
        "cookies_browser_attempts": list(attempts),
        "selected_cookies_browser": selected,
        "cookies_fallback_reason": fallback_reason,
    }


def attach_cookie_debug(exc: BaseException, *, attempts: Iterable[str], selected: str = "", fallback_reason: str = "") -> None:
    existing = getattr(exc, "debug", None)
    debug = dict(existing) if isinstance(existing, dict) else {}
    debug.update(cookie_debug(attempts=attempts, selected=selected, fallback_reason=fallback_reason))
    setattr(exc, "debug", debug)
