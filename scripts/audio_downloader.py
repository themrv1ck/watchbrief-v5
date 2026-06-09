#!/usr/bin/env python3
"""Audio downloader for WatchBrief V5 acquisition layer."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    from .acquisition_errors import (
        AudioDownloadBlockedError,
        AudioDownloadError,
        AUDIO_DOWNLOAD_TIMEOUT,
        BILIBILI_412_BLOCKED,
        BILIBILI_AUDIO_CONVERT_FAILED,
        BILIBILI_AUDIO_DOWNLOAD_FAILED,
        BILIBILI_AUDIO_URL_NOT_FOUND,
        BILIBILI_COOKIE_EXPIRED,
        BILIBILI_COOKIE_MISSING,
        BILIBILI_PLAYURL_API_FAILED,
        BILIBILI_PLAYURL_API_FORBIDDEN,
        BILIBILI_PLAYURL_API_NO_DASH_AUDIO,
        BilibiliAudioDownloadError,
        FormatConversionError,
        PlatformRestrictionError,
        is_format_conversion_failure,
        is_platform_restriction,
    )
    from .cookie_strategy import attach_cookie_debug, cookie_debug, cookie_fallback_reason, normalize_cookie_browser_attempts
except ImportError:  # pragma: no cover
    from acquisition_errors import (
        AudioDownloadBlockedError,
        AudioDownloadError,
        AUDIO_DOWNLOAD_TIMEOUT,
        BILIBILI_412_BLOCKED,
        BILIBILI_AUDIO_CONVERT_FAILED,
        BILIBILI_AUDIO_DOWNLOAD_FAILED,
        BILIBILI_AUDIO_URL_NOT_FOUND,
        BILIBILI_COOKIE_EXPIRED,
        BILIBILI_COOKIE_MISSING,
        BILIBILI_PLAYURL_API_FAILED,
        BILIBILI_PLAYURL_API_FORBIDDEN,
        BILIBILI_PLAYURL_API_NO_DASH_AUDIO,
        BilibiliAudioDownloadError,
        FormatConversionError,
        PlatformRestrictionError,
        is_format_conversion_failure,
        is_platform_restriction,
    )
    from cookie_strategy import attach_cookie_debug, cookie_debug, cookie_fallback_reason, normalize_cookie_browser_attempts


STANDARD_AUDIO_EXTENSION = ".wav"
BILIBILI_HOST_MARKERS = ("bilibili.com", "b23.tv")
BILIBILI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class AudioDownloadResult:
    audio_path: Path
    command: list[str]
    method: str = "yt_dlp"
    audio_paths: list[Path] | None = None
    cookies_source: str = ""
    audio_url_source: str = ""
    fallback_success: bool = False
    playurl_status: str = ""
    cookies_browser_attempts: list[str] | None = None
    selected_cookies_browser: str = ""
    cookies_fallback_reason: str = ""


def audio_sort_key(path: Path) -> tuple[str, int, str]:
    """Sort yt-dlp multipart outputs in playback order.

    Bilibili multipart downloads commonly produce names like ``BV..._p1.wav``
    through ``BV..._p35.wav``. Lexical sorting puts ``p10`` before ``p2``;
    natural sorting is required before transcription/merge.
    """
    stem = path.stem
    match = re.search(r"(?:^|[_\-.])p(\d+)(?:$|[_\-.])", stem, flags=re.IGNORECASE)
    if match:
        prefix = stem[: match.start()]
        return (prefix, int(match.group(1)), stem)
    return (stem, 0, stem)


def pick_audio_files(output_dir: Path) -> list[Path]:
    candidates = [
        path for path in output_dir.rglob(f"*{STANDARD_AUDIO_EXTENSION}")
        if path.is_file() and path.stat().st_size > 0
    ]
    if not candidates:
        raise AudioDownloadError("audio download produced no standard WAV file")
    return sorted(candidates, key=audio_sort_key)


def pick_audio_file(output_dir: Path) -> Path:
    return pick_audio_files(output_dir)[0]


def ensure_audio_fallback_allowed(*, subtitle_checked: bool, subtitle_available: bool) -> None:
    if not subtitle_checked:
        raise AudioDownloadBlockedError("audio download is blocked until subtitle fetch has been attempted")
    if subtitle_available:
        raise AudioDownloadBlockedError("audio download is blocked because subtitles are available")


def is_bilibili_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return any(marker in lowered for marker in BILIBILI_HOST_MARKERS)


def classify_bilibili_audio_failure(stderr: str) -> str:
    text = str(stderr or "").lower()
    if "412" in text or "precondition failed" in text:
        return BILIBILI_412_BLOCKED
    if "cookie" in text and any(marker in text for marker in ("expired", "invalid", "失效", "过期")):
        return BILIBILI_COOKIE_EXPIRED
    if any(marker in text for marker in ("login required", "not logged in", "sign in", "需要登录", "请登录")):
        return BILIBILI_COOKIE_MISSING
    if "cookie" in text and any(marker in text for marker in ("missing", "no cookies", "cookies are not", "could not find", "not found")):
        return BILIBILI_COOKIE_MISSING
    if any(marker in text for marker in ("unable to extract", "no video formats", "requested format is not available", "no audio", "audio url")):
        return BILIBILI_AUDIO_URL_NOT_FOUND
    if is_format_conversion_failure(stderr):
        return BILIBILI_AUDIO_CONVERT_FAILED
    return BILIBILI_AUDIO_DOWNLOAD_FAILED


def cookie_header_from_file(path: Optional[Path]) -> str:
    if path is None:
        return ""
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return ""
    if "=" in text and "\t" not in text:
        return " ".join(text.split())
    pairs: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            pairs.append(f"{parts[5]}={parts[6]}")
    return "; ".join(pairs)


def read_browser_cookie_header(browser: str, *, domain_name: str = "bilibili.com", loader: Any = None) -> str:
    browser_name = str(browser or "").strip().lower().replace("-", "_")
    if not browser_name:
        return ""
    if loader is None:
        try:
            import browser_cookie3  # type: ignore
        except Exception:
            return ""
        loader = getattr(browser_cookie3, browser_name, None)
    if loader is None:
        return ""
    try:
        jar = loader(domain_name=domain_name)
    except Exception:
        return ""
    pairs: list[str] = []
    for cookie in jar:
        name = str(getattr(cookie, "name", "") or "")
        value = str(getattr(cookie, "value", "") or "")
        if name and value:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def effective_cookie_header(
    cookie_header: Optional[str],
    cookie_file: Optional[Path],
    *,
    cookies_from_browser: Optional[str] = None,
    browser_cookie_loader: Any = None,
) -> str:
    explicit_header = str(cookie_header or os.environ.get("WATCHBRIEF_BILIBILI_COOKIE_HEADER") or "").strip()
    if explicit_header:
        return explicit_header
    file_header = cookie_header_from_file(
        cookie_file or (Path(os.environ["WATCHBRIEF_BILIBILI_COOKIE_FILE"]).expanduser() if os.environ.get("WATCHBRIEF_BILIBILI_COOKIE_FILE") else None)
    )
    if file_header:
        return file_header
    browser = configured_cookies_from_browser(cookies_from_browser)
    if browser:
        return read_browser_cookie_header(browser, loader=browser_cookie_loader)
    return ""


def configured_cookie_file(cookie_file: Optional[Path]) -> Optional[Path]:
    if cookie_file is not None:
        return cookie_file
    env_value = os.environ.get("WATCHBRIEF_BILIBILI_COOKIE_FILE", "").strip()
    if env_value:
        return Path(env_value).expanduser()
    return None


def configured_cookies_from_browser(cookies_from_browser: Optional[str]) -> str:
    return str(cookies_from_browser or os.environ.get("WATCHBRIEF_BILIBILI_COOKIES_FROM_BROWSER") or "").strip()


def cookies_source_label(*, cookies_from_browser: Optional[str] = None, cookie_file: Optional[Path] = None, cookie_header: Optional[str] = None) -> str:
    browser = configured_cookies_from_browser(cookies_from_browser)
    if browser:
        return f"browser:{browser}"
    if configured_cookie_file(cookie_file):
        return "cookies_file"
    if str(cookie_header or os.environ.get("WATCHBRIEF_BILIBILI_COOKIE_HEADER") or "").strip():
        return "cookie_header"
    return "none"


def add_bilibili_cookie_args(
    command: list[str],
    *,
    cookies_from_browser: Optional[str] = None,
    cookie_file: Optional[Path] = None,
) -> list[str]:
    browser = configured_cookies_from_browser(cookies_from_browser)
    cookie_path = configured_cookie_file(cookie_file)
    if browser:
        return [*command, "--cookies-from-browser", browser]
    if cookie_path:
        return [*command, "--cookies", str(cookie_path.expanduser())]
    return command


def add_cookie_args(
    command: list[str],
    *,
    cookies_from_browser: Optional[str] = None,
    cookie_file: Optional[Path] = None,
) -> list[str]:
    if cookies_from_browser:
        return [*command, "--cookies-from-browser", str(cookies_from_browser)]
    if cookie_file:
        return [*command, "--cookies", str(Path(cookie_file).expanduser())]
    return command


def has_explicit_bilibili_cookie_source(
    *,
    cookies_from_browser: Optional[str] = None,
    cookie_file: Optional[Path] = None,
    cookie_header: Optional[str] = None,
) -> bool:
    return bool(
        configured_cookies_from_browser(cookies_from_browser)
        or configured_cookie_file(cookie_file)
        or str(cookie_header or os.environ.get("WATCHBRIEF_BILIBILI_COOKIE_HEADER") or "").strip()
    )


def raise_bilibili_audio_failure(reason_code: str, command: list[str], stderr: str, debug: Optional[dict[str, Any]] = None) -> None:
    messages = {
        BILIBILI_COOKIE_MISSING: "Bilibili login cookies are missing",
        BILIBILI_COOKIE_EXPIRED: "Bilibili login cookies are expired or invalid",
        BILIBILI_412_BLOCKED: "Bilibili returned HTTP 412 / risk-control block",
        BILIBILI_PLAYURL_API_FAILED: "Bilibili playurl API request failed",
        BILIBILI_PLAYURL_API_FORBIDDEN: "Bilibili playurl API denied access",
        BILIBILI_PLAYURL_API_NO_DASH_AUDIO: "Bilibili playurl API returned no usable dash audio",
        BILIBILI_AUDIO_URL_NOT_FOUND: "Bilibili audio URL was not found",
        BILIBILI_AUDIO_DOWNLOAD_FAILED: "Bilibili audio download failed",
        BILIBILI_AUDIO_CONVERT_FAILED: "Bilibili audio conversion failed",
    }
    raise BilibiliAudioDownloadError(reason_code, messages[reason_code], command, stderr, debug=debug)


def request_headers(page_url: str, cookie_header: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": BILIBILI_USER_AGENT,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def fetch_text(url: str, *, headers: dict[str, str], urlopen_func: Any, timeout: int) -> str:
    request = urllib.request.Request(url, headers=headers)
    with urlopen_func(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def fetch_bytes(url: str, *, headers: dict[str, str], urlopen_func: Any, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urlopen_func(request, timeout=timeout) as response:
        return response.read()


def extract_json_after_marker(html: str, marker: str) -> Optional[dict[str, Any]]:
    index = html.find(marker)
    if index == -1:
        return None
    brace_start = html.find("{", index)
    if brace_start == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(html[brace_start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def extract_bilibili_audio_url_with_source(payload: dict[str, Any]) -> tuple[str, str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    dash = data.get("dash") if isinstance(data, dict) else None
    audio_items = dash.get("audio") if isinstance(dash, dict) else None
    if isinstance(audio_items, list):
        for audio_index, item in enumerate(audio_items):
            if not isinstance(item, dict):
                continue
            for key in ("baseUrl", "base_url", "url"):
                value = str(item.get(key) or "").strip()
                if value.startswith(("http://", "https://")):
                    return value, f"dash.audio[{audio_index}].{key}"
            for key in ("backupUrl", "backup_url", "backupUrls", "backup_urls"):
                backup_values = item.get(key)
                if isinstance(backup_values, list):
                    for backup_index, backup_value in enumerate(backup_values):
                        value = str(backup_value or "").strip()
                        if value.startswith(("http://", "https://")):
                            return value, f"dash.audio[{audio_index}].{key}[{backup_index}]"
                else:
                    value = str(backup_values or "").strip()
                    if value.startswith(("http://", "https://")):
                        return value, f"dash.audio[{audio_index}].{key}"
    durl_items = data.get("durl") if isinstance(data, dict) else None
    if isinstance(durl_items, list):
        for durl_index, item in enumerate(durl_items):
            if not isinstance(item, dict):
                continue
            for key in ("url", "baseUrl", "base_url"):
                value = str(item.get(key) or "").strip()
                if value.startswith(("http://", "https://")):
                    return value, f"durl[{durl_index}].{key}"
            for key in ("backupUrl", "backup_url", "backupUrls", "backup_urls"):
                backup_values = item.get(key)
                if isinstance(backup_values, list):
                    for backup_index, backup_value in enumerate(backup_values):
                        value = str(backup_value or "").strip()
                        if value.startswith(("http://", "https://")):
                            return value, f"durl[{durl_index}].{key}[{backup_index}]"
                else:
                    value = str(backup_values or "").strip()
                    if value.startswith(("http://", "https://")):
                        return value, f"durl[{durl_index}].{key}"
    return "", ""


def extract_bilibili_audio_url_from_payload(payload: dict[str, Any]) -> str:
    return extract_bilibili_audio_url_with_source(payload)[0]


def bilibili_bvid_from_url(url: str) -> str:
    parts = urllib.parse.urlparse(url).path.strip("/").split("/")
    return next((part for part in parts if part.startswith("BV")), "")


def bilibili_playurl_api_url(*, bvid: str, cid: str) -> str:
    return "https://api.bilibili.com/x/player/playurl?" + urllib.parse.urlencode({
        "bvid": bvid,
        "cid": cid,
        "qn": 80,
        "fnval": 16,
        "fourk": 1,
        "platform": "pc",
    })


def debug_for_bilibili_audio(
    *,
    method: str,
    cookies_source: str,
    audio_url_source: str = "",
    fallback_success: bool = False,
    playurl_status: str = "",
    reason_code: str = "",
) -> dict[str, Any]:
    return {
        "method": method,
        "cookies_source": cookies_source,
        "audio_url_source": audio_url_source,
        "fallback_success": fallback_success,
        "playurl_status": playurl_status,
        "reason_code": reason_code,
    }


def fetch_bilibili_audio_url_from_playurl_api(
    page_url: str,
    *,
    bvid: str,
    cid: str,
    cookie_header: str,
    urlopen_func: Any,
    timeout: int,
) -> tuple[str, str, str]:
    bvid = str(bvid or bilibili_bvid_from_url(page_url)).strip()
    cid = str(cid or "").strip()
    if not bvid or not cid:
        return "", "", "missing_bvid_or_cid"
    api_url = bilibili_playurl_api_url(bvid=bvid, cid=cid)
    command = ["bilibili-playurl-api", page_url]
    try:
        payload = json.loads(fetch_text(api_url, headers=request_headers(page_url, cookie_header), urlopen_func=urlopen_func, timeout=timeout))
    except urllib.error.HTTPError as exc:
        reason = BILIBILI_412_BLOCKED if exc.code == 412 else BILIBILI_PLAYURL_API_FORBIDDEN if exc.code in {401, 403} else BILIBILI_PLAYURL_API_FAILED
        raise_bilibili_audio_failure(
            reason,
            command,
            str(exc),
            debug_for_bilibili_audio(method="bilibili_playurl_api", cookies_source="", playurl_status=f"http_{exc.code}", reason_code=reason),
        )
    except Exception as exc:
        raise_bilibili_audio_failure(
            BILIBILI_PLAYURL_API_FAILED,
            command,
            str(exc),
            debug_for_bilibili_audio(method="bilibili_playurl_api", cookies_source="", playurl_status="request_failed", reason_code=BILIBILI_PLAYURL_API_FAILED),
        )
    if not isinstance(payload, dict):
        return "", "", "invalid_payload"
    code = payload.get("code")
    if code not in (0, "0", None):
        if code in (-101, -403, 401, 403):
            raise_bilibili_audio_failure(
                BILIBILI_PLAYURL_API_FORBIDDEN,
                command,
                str(payload.get("message") or f"api_code_{code}"),
                debug_for_bilibili_audio(method="bilibili_playurl_api", cookies_source="", playurl_status=f"api_code_{code}", reason_code=BILIBILI_PLAYURL_API_FORBIDDEN),
            )
        return "", "", f"api_code_{code}"
    audio_url, source = extract_bilibili_audio_url_with_source(payload)
    if audio_url:
        return audio_url, source, "ok"
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    dash = data.get("dash") if isinstance(data, dict) else None
    if isinstance(dash, dict) and "audio" in dash:
        return "", "", "no_dash_audio"
    return "", "", "audio_url_not_found"


def classify_bilibili_exception(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 412:
            return BILIBILI_412_BLOCKED
        if exc.code in {401, 403}:
            return BILIBILI_COOKIE_EXPIRED
    text = str(exc).lower()
    return classify_bilibili_audio_failure(text)


def bilibili_ids_from_initial_state(initial_state: dict[str, Any], page_url: str) -> tuple[str, str]:
    video_data = initial_state.get("videoData") if isinstance(initial_state.get("videoData"), dict) else {}
    bvid = str(video_data.get("bvid") or initial_state.get("bvid") or "").strip()
    cid = str(video_data.get("cid") or initial_state.get("cid") or "").strip()
    if not bvid:
        match = urllib.parse.urlparse(page_url).path.strip("/").split("/")
        bvid = next((part for part in match if part.startswith("BV")), "")
    return bvid, cid


def fetch_bilibili_audio_url_from_page(
    url: str,
    *,
    cookie_header: str,
    urlopen_func: Any,
    timeout: int,
    bvid: str = "",
    cid: str = "",
) -> str:
    headers = request_headers(url, cookie_header)
    html = fetch_text(url, headers=headers, urlopen_func=urlopen_func, timeout=timeout)
    playinfo = extract_json_after_marker(html, "__playinfo__")
    if isinstance(playinfo, dict):
        audio_url = extract_bilibili_audio_url_from_payload(playinfo)
        if audio_url:
            return audio_url

    initial_state = extract_json_after_marker(html, "__INITIAL_STATE__")
    if isinstance(initial_state, dict):
        state_bvid, state_cid = bilibili_ids_from_initial_state(initial_state, url)
        bvid = bvid or state_bvid
        cid = cid or state_cid
    if bvid and cid:
        audio_url, _source, _status = fetch_bilibili_audio_url_from_playurl_api(
            url,
            bvid=bvid,
            cid=cid,
            cookie_header=cookie_header,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )
        if audio_url:
            return audio_url
    return ""


def download_bilibili_audio_via_playinfo(
    url: str,
    output_dir: Path,
    *,
    runner: Any,
    timeout: int,
    urlopen_func: Any,
    cookie_header: str,
    cookies_source: str = "",
    bvid: str = "",
    cid: str = "",
    playinfo: Optional[dict[str, Any]] = None,
) -> AudioDownloadResult:
    fallback_command = ["bilibili-playinfo-fallback", url]
    method = "bilibili_playinfo_fallback"
    audio_url_source = ""
    playurl_status = ""
    try:
        audio_url = ""
        if isinstance(playinfo, dict):
            audio_url, audio_url_source = extract_bilibili_audio_url_with_source(playinfo)
        if not audio_url and bvid and cid:
            fallback_command = ["bilibili-playurl-api", url]
            method = "bilibili_playurl_api"
            audio_url, audio_url_source, playurl_status = fetch_bilibili_audio_url_from_playurl_api(
                url,
                bvid=bvid,
                cid=cid,
                cookie_header=cookie_header,
                urlopen_func=urlopen_func,
                timeout=min(timeout, 120),
            )
        if not audio_url:
            audio_url = fetch_bilibili_audio_url_from_page(
                url,
                cookie_header=cookie_header,
                urlopen_func=urlopen_func,
                timeout=min(timeout, 120),
                bvid=bvid,
                cid=cid,
            )
            if audio_url and not audio_url_source:
                audio_url_source = "page_or_playinfo"
    except Exception as exc:
        if isinstance(exc, BilibiliAudioDownloadError):
            if not exc.debug:
                exc.debug = debug_for_bilibili_audio(method=method, cookies_source=cookies_source, playurl_status=playurl_status or "exception", reason_code=exc.reason_code)
            elif not exc.debug.get("cookies_source"):
                exc.debug["cookies_source"] = cookies_source
            raise
        reason = classify_bilibili_exception(exc)
        if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403} and method == "bilibili_playurl_api":
            reason = BILIBILI_PLAYURL_API_FORBIDDEN
        raise_bilibili_audio_failure(
            reason,
            fallback_command,
            str(exc),
            debug_for_bilibili_audio(method=method, cookies_source=cookies_source, playurl_status=playurl_status or "exception", reason_code=reason),
        )
    if not audio_url:
        reason = BILIBILI_PLAYURL_API_NO_DASH_AUDIO if playurl_status == "no_dash_audio" else BILIBILI_AUDIO_URL_NOT_FOUND
        raise_bilibili_audio_failure(
            reason,
            fallback_command,
            "",
            debug_for_bilibili_audio(method=method, cookies_source=cookies_source, playurl_status=playurl_status or "audio_url_not_found", reason_code=reason),
        )

    raw_audio_path = output_dir / "bilibili_audio.m4a"
    wav_path = output_dir / "bilibili_audio.wav"
    try:
        raw_audio_path.write_bytes(
            fetch_bytes(
                audio_url,
                headers=request_headers(url, cookie_header),
                urlopen_func=urlopen_func,
                timeout=timeout,
            )
        )
    except Exception as exc:
        reason = classify_bilibili_exception(exc)
        if reason not in {BILIBILI_412_BLOCKED, BILIBILI_COOKIE_EXPIRED, BILIBILI_COOKIE_MISSING}:
            reason = BILIBILI_AUDIO_DOWNLOAD_FAILED
        raise_bilibili_audio_failure(
            reason,
            fallback_command,
            str(exc),
            debug_for_bilibili_audio(
                method=method,
                cookies_source=cookies_source,
                audio_url_source=audio_url_source,
                fallback_success=False,
                playurl_status=playurl_status,
                reason_code=reason,
            ),
        )

    ffmpeg_command = [
        "ffmpeg",
        "-y",
        "-i",
        str(raw_audio_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        str(wav_path),
    ]
    result = runner(ffmpeg_command, capture_output=True, text=True, timeout=timeout)
    stderr = str(result.stderr or "")
    if result.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
        raise_bilibili_audio_failure(
            BILIBILI_AUDIO_CONVERT_FAILED,
            ffmpeg_command,
            stderr,
            debug_for_bilibili_audio(
                method=method,
                cookies_source=cookies_source,
                audio_url_source=audio_url_source,
                fallback_success=False,
                playurl_status=playurl_status,
                reason_code=BILIBILI_AUDIO_CONVERT_FAILED,
            ),
        )
    return AudioDownloadResult(
        audio_path=wav_path,
        command=fallback_command,
        method=method,
        cookies_source=cookies_source,
        audio_url_source=audio_url_source,
        fallback_success=True,
        playurl_status=playurl_status or "ok",
    )


def download_standard_audio(
    url: str,
    output_dir: Path,
    *,
    subtitle_checked: bool,
    subtitle_available: bool,
    runner: Any = subprocess.run,
    timeout: int = 600,
    urlopen_func: Any = urllib.request.urlopen,
    cookie_file: Optional[Path] = None,
    cookie_header: Optional[str] = None,
    cookies_from_browser: Optional[str] = None,
    cookie_browser_attempts: Any = None,
    browser_cookie_loader: Any = None,
    bvid: str = "",
    cid: str = "",
    playinfo: Optional[dict[str, Any]] = None,
) -> AudioDownloadResult:
    attempts = normalize_cookie_browser_attempts(cookie_browser_attempts)
    if attempts and not cookies_from_browser and not cookie_file and not cookie_header:
        fallback_reason = ""
        last_exc: BaseException | None = None
        for index, browser in enumerate(attempts):
            try:
                result = download_standard_audio(
                    url,
                    output_dir,
                    subtitle_checked=subtitle_checked,
                    subtitle_available=subtitle_available,
                    runner=runner,
                    timeout=timeout,
                    urlopen_func=urlopen_func,
                    cookie_file=cookie_file,
                    cookie_header=cookie_header,
                    cookies_from_browser=browser,
                    browser_cookie_loader=browser_cookie_loader,
                    bvid=bvid,
                    cid=cid,
                    playinfo=playinfo,
                )
                result.cookies_browser_attempts = list(attempts)
                result.selected_cookies_browser = browser
                result.cookies_fallback_reason = fallback_reason
                return result
            except (PlatformRestrictionError, AudioDownloadError, BilibiliAudioDownloadError) as exc:
                reason = cookie_fallback_reason(exc)
                attach_cookie_debug(exc, attempts=attempts, selected=browser, fallback_reason=reason or fallback_reason)
                last_exc = exc
                if not reason or index == len(attempts) - 1:
                    raise
                fallback_reason = reason
        if last_exc is not None:
            raise last_exc

    ensure_audio_fallback_allowed(subtitle_checked=subtitle_checked, subtitle_available=subtitle_available)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format",
        "wav",
        "--audio-quality",
        "0",
        "--output",
        str(output_dir / "%(id)s.%(ext)s"),
        url,
    ]
    if is_bilibili_url(url):
        command = add_bilibili_cookie_args(
            command,
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookie_file,
        )
    else:
        command = add_cookie_args(
            command,
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookie_file,
        )
    try:
        result = runner(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stderr = str(exc.stderr or exc.output or "")
        raise AudioDownloadError("audio download timed out", command, stderr, reason_code=AUDIO_DOWNLOAD_TIMEOUT) from exc
    stderr = str(result.stderr or "")
    if result.returncode != 0:
        if is_bilibili_url(url):
            reason = classify_bilibili_audio_failure(stderr)
            should_try_fallback = reason not in {
                BILIBILI_COOKIE_MISSING,
                BILIBILI_COOKIE_EXPIRED,
                BILIBILI_AUDIO_CONVERT_FAILED,
            }
            if reason == BILIBILI_412_BLOCKED:
                should_try_fallback = has_explicit_bilibili_cookie_source(
                    cookies_from_browser=cookies_from_browser,
                    cookie_file=cookie_file,
                    cookie_header=cookie_header,
                )
            if should_try_fallback:
                try:
                    return download_bilibili_audio_via_playinfo(
                        url,
                        output_dir,
                        runner=runner,
                        timeout=timeout,
                        urlopen_func=urlopen_func,
                        cookie_header=effective_cookie_header(cookie_header, cookie_file, cookies_from_browser=cookies_from_browser, browser_cookie_loader=browser_cookie_loader),
                        cookies_source=cookies_source_label(cookies_from_browser=cookies_from_browser, cookie_file=cookie_file, cookie_header=cookie_header),
                        bvid=bvid,
                        cid=cid,
                        playinfo=playinfo,
                    )
                except BilibiliAudioDownloadError:
                    raise
            raise_bilibili_audio_failure(reason, command, stderr)
        if is_platform_restriction(stderr):
            raise PlatformRestrictionError("audio_downloader", "platform restricted audio access", command, stderr)
        if is_format_conversion_failure(stderr):
            raise FormatConversionError("audio_downloader", "audio format conversion failed", command, stderr)
        raise AudioDownloadError("audio download failed", command, stderr)
    try:
        audio_paths = pick_audio_files(output_dir)
        audio_path = audio_paths[0]
    except AudioDownloadError as exc:
        if is_bilibili_url(url):
            try:
                return download_bilibili_audio_via_playinfo(
                    url,
                    output_dir,
                    runner=runner,
                    timeout=timeout,
                    urlopen_func=urlopen_func,
                    cookie_header=effective_cookie_header(cookie_header, cookie_file, cookies_from_browser=cookies_from_browser, browser_cookie_loader=browser_cookie_loader),
                    cookies_source=cookies_source_label(cookies_from_browser=cookies_from_browser, cookie_file=cookie_file, cookie_header=cookie_header),
                    bvid=bvid,
                    cid=cid,
                    playinfo=playinfo,
                )
            except BilibiliAudioDownloadError:
                raise
        raise AudioDownloadError(exc.message, command, stderr) from exc
    return AudioDownloadResult(
        audio_path=audio_path,
        command=command,
        method="yt_dlp",
        audio_paths=audio_paths,
        cookies_source=(
            cookies_source_label(cookies_from_browser=cookies_from_browser, cookie_file=cookie_file, cookie_header=cookie_header)
            if is_bilibili_url(url) or cookies_from_browser or cookie_file or cookie_header
            else ""
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and convert video audio to standard WAV after subtitles are unavailable.")
    parser.add_argument("url")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--subtitle-checked", action="store_true")
    parser.add_argument("--subtitle-available", action="store_true")
    parser.add_argument(
        "--bilibili-cookies-from-browser",
        "--cookies-from-browser",
        dest="bilibili_cookies_from_browser",
        help="explicit browser cookie source for Bilibili, e.g. chrome, safari, edge",
    )
    parser.add_argument("--bilibili-cookies-file", "--cookies-file", dest="bilibili_cookies_file", type=Path, help="Bilibili cookies.txt file")
    parser.add_argument("--json-output", required=True, type=Path)
    args = parser.parse_args()
    result = download_standard_audio(
        args.url,
        args.output_dir,
        subtitle_checked=args.subtitle_checked,
        subtitle_available=args.subtitle_available,
        cookies_from_browser=args.bilibili_cookies_from_browser,
        cookie_file=args.bilibili_cookies_file.expanduser() if args.bilibili_cookies_file else None,
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps({
            "audio_path": str(result.audio_path),
            "audio_paths": [str(path) for path in (result.audio_paths or [result.audio_path])],
            "method": result.method,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
