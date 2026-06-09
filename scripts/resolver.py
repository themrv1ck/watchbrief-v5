#!/usr/bin/env python3
"""URL resolver for WatchBrief V5 acquisition layer."""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

try:
    from .acquisition_errors import (
        BILIBILI_412_BLOCKED,
        BILIBILI_CID_NOT_FOUND,
        BILIBILI_COOKIE_EXPIRED,
        BILIBILI_COOKIE_MISSING,
        BILIBILI_LIST_EXPANSION_FAILED,
        BILIBILI_METADATA_NOT_FOUND,
        BILIBILI_PLAYINFO_UNAVAILABLE,
        BilibiliResolverError,
        PlatformRestrictionError,
        ResolverError,
        XIAOHONGSHU_BOARD_EMPTY,
        XIAOHONGSHU_BOARD_RESOLVER_FAILED,
        XiaohongshuBoardResolverError,
        is_platform_restriction,
    )
    from .audio_downloader import (
        BILIBILI_USER_AGENT,
        cookie_header_from_file,
        extract_json_after_marker,
        is_bilibili_url,
        request_headers,
    )
    from .cookie_strategy import (
        DEFAULT_COOKIE_BROWSER_ATTEMPTS,
        browser_attempts_from_source_labels,
        browser_from_cookie_source_label,
        cookie_debug,
        cookie_fallback_reason_from_text,
        normalize_cookie_browser_attempts,
    )
except ImportError:  # pragma: no cover
    from acquisition_errors import (
        BILIBILI_412_BLOCKED,
        BILIBILI_CID_NOT_FOUND,
        BILIBILI_COOKIE_EXPIRED,
        BILIBILI_COOKIE_MISSING,
        BILIBILI_LIST_EXPANSION_FAILED,
        BILIBILI_METADATA_NOT_FOUND,
        BILIBILI_PLAYINFO_UNAVAILABLE,
        BilibiliResolverError,
        PlatformRestrictionError,
        ResolverError,
        XIAOHONGSHU_BOARD_EMPTY,
        XIAOHONGSHU_BOARD_RESOLVER_FAILED,
        XiaohongshuBoardResolverError,
        is_platform_restriction,
    )
    from audio_downloader import (
        BILIBILI_USER_AGENT,
        cookie_header_from_file,
        extract_json_after_marker,
        is_bilibili_url,
        request_headers,
    )
    from cookie_strategy import (
        DEFAULT_COOKIE_BROWSER_ATTEMPTS,
        browser_attempts_from_source_labels,
        browser_from_cookie_source_label,
        cookie_debug,
        cookie_fallback_reason_from_text,
        normalize_cookie_browser_attempts,
    )


DEFAULT_BROWSER_AUTH_ORDER = DEFAULT_COOKIE_BROWSER_ATTEMPTS
BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{8,})\b")
BILIBILI_LIST_PATH_RE = re.compile(r"^/list/(ml\d+)(?:/|$)")
BILIBILI_FAV_LIST_API = "https://api.bilibili.com/x/v3/fav/resource/list"
NETEASE_PROGRAM_DETAIL_API = "https://music.163.com/api/dj/program/detail"
NETEASE_PLAYER_URL_API = "https://music.163.com/api/song/enhance/player/url"
XIAOHONGSHU_HOST_RE = re.compile(r"(^|\.)xiaohongshu\.com$")
XIAOHONGSHU_BOARD_API = "https://www.xiaohongshu.com/api/sns/web/v1/board/note"
XIAOHONGSHU_SENSITIVE_QUERY_KEYS = {"xsec_token", "xsec_source", "token", "sign", "signature"}
PUBLISH_METADATA_FIELDS = ("date", "publish_date", "release_date", "upload_date", "timestamp", "pubdate", "published_at")
BILIBILI_PUBLISH_TIME_FIELDS = ("pubdate", "pubtime", "ctime")


def stderr_summary(stderr: str, *, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(stderr or "")).strip()[:limit]


def classify_bilibili_resolver_failure(stderr: str) -> str:
    text = str(stderr or "").lower()
    if "412" in text or "precondition failed" in text:
        return BILIBILI_412_BLOCKED
    if "cookie" in text and any(marker in text for marker in ("expired", "invalid", "失效", "过期")):
        return BILIBILI_COOKIE_EXPIRED
    if any(marker in text for marker in ("login required", "not logged in", "sign in", "需要登录", "请登录")):
        return BILIBILI_COOKIE_MISSING
    if "cookie" in text and any(marker in text for marker in ("missing", "no cookies", "could not find", "not found")):
        return BILIBILI_COOKIE_MISSING
    return BILIBILI_METADATA_NOT_FOUND


def bilibili_bvid_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    path_bvid = next((part for part in parsed.path.strip("/").split("/") if part.startswith("BV")), "")
    if path_bvid:
        return path_bvid
    query = urllib.parse.parse_qs(parsed.query)
    query_bvid = str((query.get("bvid") or [""])[0]).strip()
    if query_bvid.startswith("BV"):
        return query_bvid
    match = BVID_RE.search(str(url or ""))
    return match.group(1) if match else ""


def bilibili_list_id_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    match = BILIBILI_LIST_PATH_RE.match(parsed.path)
    if match:
        return match.group(1)
    query = urllib.parse.parse_qs(parsed.query)
    # Bilibili space/favlist URLs expose the favorite-list id as fid.
    # Treat it as the same media_id used by x/v3/fav/resource/list so the
    # resolver can fetch authoritative titles instead of accepting yt-dlp's
    # flat-playlist placeholders (Video 2, Video 3, ...).
    if parsed.path.rstrip("/").endswith("/favlist"):
        fid = str((query.get("fid") or [""])[0]).strip()
        if fid.isdigit():
            return f"ml{fid}"
    return ""


def is_bilibili_list_url(url: str) -> bool:
    return bool(is_bilibili_url(url) and bilibili_list_id_from_url(url))


def is_xiaohongshu_url(url: str) -> bool:
    host = urllib.parse.urlparse(str(url or "")).netloc.lower()
    return bool(XIAOHONGSHU_HOST_RE.search(host))


def xiaohongshu_board_id_from_text(value: str) -> str:
    text = urllib.parse.unquote(str(value or ""))
    match = re.search(r"(?:^|/)board/([^/?#&]+)", text)
    return match.group(1).strip() if match else ""


def xiaohongshu_board_id_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    for index, part in enumerate(parts):
        if part == "board" and index + 1 < len(parts):
            board_id = parts[index + 1].strip()
            if board_id:
                return board_id
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("board_id", "boardId", "board", "board_id_to", "target_board_id"):
        value = str((query.get(key) or [""])[0]).strip()
        if value:
            return value
    for values in query.values():
        for value in values:
            board_id = xiaohongshu_board_id_from_text(value)
            if board_id:
                return board_id
    if parsed.fragment:
        return xiaohongshu_board_id_from_text(parsed.fragment)
    return ""


def is_xiaohongshu_board_url(url: str) -> bool:
    return bool(is_xiaohongshu_url(url) and xiaohongshu_board_id_from_url(url))


def is_netease_music_url(url: str) -> bool:
    host = urllib.parse.urlparse(str(url or "")).netloc.lower()
    return host == "163cn.tv" or host.endswith("music.163.com") or host.endswith("music.163.com.cn")


def netease_program_id_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    query = urllib.parse.parse_qs(parsed.query)
    value = str((query.get("id") or query.get("programId") or [""])[0]).strip()
    if value.isdigit():
        return value
    match = re.search(r"(?:^|/)program(?:/|$).*?(?:[?&]id=|/)(\d+)", str(url or ""))
    return match.group(1) if match else ""


def fetch_final_url(url: str, *, headers: dict[str, str], urlopen_func: Any, timeout: int) -> str:
    request = urllib.request.Request(url, headers=headers)
    with urlopen_func(request, timeout=timeout) as response:
        final_url = getattr(response, "geturl", lambda: "")()
        return str(final_url or url)


def format_unix_ms_date(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    if number > 10_000_000_000:
        number = number / 1000.0
    return datetime.datetime.fromtimestamp(number, datetime.timezone.utc).strftime("%Y%m%d")


def fetch_netease_json(url: str, *, headers: dict[str, str], urlopen_func: Any, timeout: int) -> dict[str, Any]:
    try:
        payload = json.loads(fetch_text(url, headers=headers, urlopen_func=urlopen_func, timeout=timeout))
    except Exception as exc:
        raise ResolverError("NetEase Music API returned invalid JSON", ["netease-api", url], str(exc)) from exc
    if not isinstance(payload, dict):
        raise ResolverError("NetEase Music API JSON must be an object", ["netease-api", url])
    return payload


def resolve_netease_program(
    source_url: str,
    *,
    urlopen_func: Any,
    timeout: int,
) -> dict[str, Any]:
    headers = {
        "User-Agent": BILIBILI_USER_AGENT,
        "Referer": "https://music.163.com/",
    }
    canonical_url = source_url
    program_id = netease_program_id_from_url(source_url)
    if not program_id:
        try:
            canonical_url = fetch_final_url(source_url, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 60))
        except Exception as exc:
            raise ResolverError("NetEase Music short URL expansion failed", ["netease-short-url", source_url], str(exc)) from exc
        program_id = netease_program_id_from_url(canonical_url)
    if not program_id:
        raise ResolverError("NetEase Music program id was not found", ["netease-program", source_url])

    detail_url = NETEASE_PROGRAM_DETAIL_API + "?" + urllib.parse.urlencode({"id": program_id})
    detail = fetch_netease_json(detail_url, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 120))
    program = detail.get("program")
    if not isinstance(program, dict) or int(detail.get("code") or 0) != 200:
        raise ResolverError("NetEase Music program detail was unavailable", ["netease-program-detail", program_id], str(detail)[:500])
    main_song = program.get("mainSong") if isinstance(program.get("mainSong"), dict) else {}
    song_id = str(main_song.get("id") or program.get("mainTrackId") or "").strip()
    if not song_id.isdigit():
        raise ResolverError("NetEase Music program audio id was not found", ["netease-program-detail", program_id])

    player_url = NETEASE_PLAYER_URL_API + "?" + urllib.parse.urlencode({"id": song_id, "ids": f"[{song_id}]", "br": "320000"})
    player = fetch_netease_json(player_url, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 120))
    data = player.get("data")
    first = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    media_url = str(first.get("url") or "").strip()
    if not media_url:
        raise ResolverError("NetEase Music program audio URL was unavailable", ["netease-player-url", song_id], str(player)[:500])

    title = str(program.get("name") or main_song.get("name") or "Untitled Podcast")
    radio = program.get("radio") if isinstance(program.get("radio"), dict) else {}
    dj = program.get("dj") if isinstance(program.get("dj"), dict) else {}
    channel = str(radio.get("name") or dj.get("nickname") or "网易云音乐播客")
    duration_ms = program.get("duration") or main_song.get("duration") or first.get("time")
    try:
        duration_seconds = max(0, int(float(duration_ms) / 1000))
    except (TypeError, ValueError):
        duration_seconds = 0
    date = format_unix_ms_date(program.get("scheduledPublishTime") or program.get("createTime"))
    item = {
        "title": title,
        "url": canonical_url,
        "id": program_id,
        "duration": str(duration_seconds) if duration_seconds else "",
        "channel": channel,
        "date": date,
        "source_platform": "netease_music_podcast",
        "_media_url": media_url,
        "resolver_debug": {
            "resolver_method": "netease_music_program_api",
            "source_platform": "netease_music_podcast",
            "program_id": program_id,
            "song_id": song_id,
            "media_url_available": True,
            "fallback_success": True,
            "reason_code": "",
        },
    }
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "single",
        "source_platform": "netease_music_podcast",
        "url": canonical_url,
        "title": title,
        "id": program_id,
        "duration": item["duration"],
        "channel": channel,
        "date": date,
        "videos": [item],
        "resolver_debug": item["resolver_debug"],
    }


def xiaohongshu_canonical_note_url(note_id: str) -> str:
    note_id = str(note_id or "").strip()
    return f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""


def xiaohongshu_note_download_url(note_id: str, xsec_token: str = "") -> str:
    base = xiaohongshu_canonical_note_url(note_id)
    token = str(xsec_token or "").strip()
    if not base or not token:
        return base
    return base + "?" + urllib.parse.urlencode({"xsec_token": token})


def xiaohongshu_url_kind(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    if XIAOHONGSHU_HOST_RE.search(parsed.netloc.lower()) and "/explore/" in parsed.path:
        query = urllib.parse.parse_qs(parsed.query)
        return "note_url_with_xsec" if "xsec_token" in query else "note_url"
    if parsed.scheme and parsed.netloc:
        return "media_or_external_url_with_query" if parsed.query else "media_or_external_url"
    return "none"


def sanitize_xiaohongshu_url_for_output(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text)
    if XIAOHONGSHU_HOST_RE.search(parsed.netloc.lower()) and "/explore/" in parsed.path:
        path_parts = [part for part in parsed.path.split("/") if part]
        if "explore" in path_parts:
            index = path_parts.index("explore")
            if index + 1 < len(path_parts):
                return xiaohongshu_canonical_note_url(path_parts[index + 1])
    query = urllib.parse.parse_qs(parsed.query)
    if query and any(key in XIAOHONGSHU_SENSITIVE_QUERY_KEYS for key in query):
        return urllib.parse.urlunparse(parsed._replace(query="[redacted]"))
    return text


def sanitize_xiaohongshu_metadata_for_debug(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if "xsec" in key_lower or "cookie" in key_lower or key_lower.endswith("token"):
                cleaned[key_text] = "[redacted]"
            else:
                cleaned[key_text] = sanitize_xiaohongshu_metadata_for_debug(nested)
        return cleaned
    if isinstance(value, list):
        return [sanitize_xiaohongshu_metadata_for_debug(item) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        cleaned_url = sanitize_xiaohongshu_url_for_output(value)
        return cleaned_url if xiaohongshu_url_kind(cleaned_url).startswith("note_url") else "[redacted-url]"
    return value


def drop_internal_download_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): drop_internal_download_fields(nested)
            for key, nested in value.items()
            if str(key) not in {"_download_url", "_media_url"}
        }
    if isinstance(value, list):
        return [drop_internal_download_fields(item) for item in value]
    return value


def add_bilibili_resolver_headers(command: list[str]) -> list[str]:
    return [
        *command,
        "--add-header",
        "Referer: https://www.bilibili.com/",
        "--add-header",
        f"User-Agent: {BILIBILI_USER_AGENT}",
    ]


def add_cookie_source(command: list[str], source: dict[str, Any]) -> list[str]:
    if source["type"] == "browser":
        return [*command, "--cookies-from-browser", str(source["value"])]
    if source["type"] == "file":
        return [*command, "--cookies", str(Path(source["value"]).expanduser())]
    return command


def cookie_source_label(source: Optional[dict[str, Any]]) -> str:
    if not source:
        return "none"
    if source["type"] == "browser":
        return f"browser:{source['value']}"
    if source["type"] == "file":
        return "cookies_file"
    if source["type"] == "header":
        return "cookie_header"
    return str(source.get("type") or "unknown")


def cookie_sources(
    *,
    cookies_from_browser: Optional[str],
    cookie_file: Optional[Path],
    cookie_header: Optional[str],
    allow_browser_auth: bool,
    browser_auth_order: tuple[str, ...],
    cookie_browser_attempts: Any = None,
) -> list[Optional[dict[str, Any]]]:
    if cookies_from_browser:
        return [{"type": "browser", "value": cookies_from_browser}]
    if cookie_file:
        return [{"type": "file", "value": cookie_file}]
    if cookie_header:
        return [{"type": "header", "value": "provided"}]
    if allow_browser_auth:
        attempts = normalize_cookie_browser_attempts(cookie_browser_attempts) or normalize_cookie_browser_attempts(browser_auth_order)
        return [{"type": "browser", "value": browser} for browser in attempts]
    return [None]


def source_cookie_header(source: Optional[dict[str, Any]], cookie_file: Optional[Path], cookie_header: Optional[str]) -> str:
    if source and source.get("type") == "file":
        return cookie_header_from_file(Path(source["value"]).expanduser())
    if source and source.get("type") == "header":
        return str(cookie_header or "").strip()
    if cookie_file:
        return cookie_header_from_file(cookie_file.expanduser())
    return str(cookie_header or "").strip()


def browser_cookie_header(browser: str, *, domain_name: str, loader: Any = None) -> str:
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


def source_cookie_header_for_domain(
    source: Optional[dict[str, Any]],
    cookie_file: Optional[Path],
    cookie_header: Optional[str],
    *,
    domain_name: str,
    browser_cookie_loader: Any = None,
) -> str:
    if source and source.get("type") == "browser":
        return browser_cookie_header(str(source.get("value") or ""), domain_name=domain_name, loader=browser_cookie_loader)
    return source_cookie_header(source, cookie_file, cookie_header)


def _run_json_command(command: list[str], runner: Any = subprocess.run, timeout: int = 60) -> dict[str, Any]:
    result = runner(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr = str(result.stderr or "")
        if is_platform_restriction(stderr):
            raise PlatformRestrictionError("resolver", "platform restricted resolver access", command, stderr)
        raise ResolverError("yt-dlp failed to resolve URL", command, stderr)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ResolverError(f"resolver returned invalid JSON: {exc}", command, str(result.stdout)[:500]) from exc
    if not isinstance(payload, dict):
        raise ResolverError("resolver JSON must be an object", command)
    return payload


def fetch_text(url: str, *, headers: dict[str, str], urlopen_func: Any, timeout: int) -> str:
    request = urllib.request.Request(url, headers=headers)
    with urlopen_func(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def classify_bilibili_fallback_exception(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 412:
            return BILIBILI_412_BLOCKED
        if exc.code in {401, 403}:
            return BILIBILI_COOKIE_EXPIRED
    return classify_bilibili_resolver_failure(str(exc))


def video_data_from_initial_state(initial_state: dict[str, Any]) -> dict[str, Any]:
    video_data = initial_state.get("videoData")
    return video_data if isinstance(video_data, dict) else {}


def fetch_bilibili_view_metadata(
    bvid: str,
    *,
    headers: dict[str, str],
    urlopen_func: Any,
    timeout: int,
) -> dict[str, Any]:
    api_url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"bvid": bvid})
    try:
        payload = json.loads(fetch_text(api_url, headers=headers, urlopen_func=urlopen_func, timeout=timeout))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def fetch_bilibili_list_metadata(
    list_id: str,
    *,
    headers: dict[str, str],
    urlopen_func: Any,
    timeout: int,
) -> dict[str, Any]:
    media_id = str(list_id or "").strip()
    if media_id.lower().startswith("ml"):
        media_id = media_id[2:]
    if not media_id.isdigit():
        return {}
    api_url = BILIBILI_FAV_LIST_API + "?" + urllib.parse.urlencode({
        "media_id": media_id,
        "pn": 1,
        "ps": 20,
        "keyword": "",
        "order": "mtime",
        "tid": 0,
        "platform": "web",
        "type": 0,
    })
    try:
        payload = json.loads(fetch_text(api_url, headers=headers, urlopen_func=urlopen_func, timeout=timeout))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    try:
        code = int(payload.get("code"))
    except (TypeError, ValueError):
        return {}
    if code != 0:
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def html_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", str(html or ""), flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    text = re.sub(r"\s+", " ", match.group(1)).strip()
    for suffix in ("_哔哩哔哩_bilibili", "-哔哩哔哩", "_哔哩哔哩"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def parse_xiaohongshu_initial_state(html: str) -> dict[str, Any]:
    marker = "window.__INITIAL_STATE__="
    start = str(html or "").find(marker)
    if start == -1:
        return {}
    start += len(marker)
    end = str(html).find("</script>", start)
    if end == -1:
        return {}
    raw = str(html)[start:end].strip().rstrip(";")
    raw = re.sub(r"(?<=[:\[,])undefined(?=[,}\]])", "null", raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def unwrap_xiaohongshu_ref(value: Any) -> Any:
    """Unwrap Vue ref-shaped values found in Xiaohongshu initial state."""
    if isinstance(value, dict) and value.get("__v_isRef") is True:
        for key in ("_value", "_rawValue"):
            if key in value:
                return value.get(key)
    return value


def xiaohongshu_board_details_from_state(state: dict[str, Any]) -> dict[str, Any]:
    board = state.get("board") if isinstance(state.get("board"), dict) else {}
    details = unwrap_xiaohongshu_ref(board.get("boardDetails"))
    return details if isinstance(details, dict) else {}


def xiaohongshu_state_notes(state: dict[str, Any], board_id: str) -> list[dict[str, Any]]:
    board = state.get("board") if isinstance(state.get("board"), dict) else {}
    feeds_map = unwrap_xiaohongshu_ref(board.get("boardFeedsMap"))
    candidates: list[Any] = []
    if isinstance(feeds_map, dict):
        current = unwrap_xiaohongshu_ref(feeds_map.get(board_id))
        if isinstance(current, dict):
            candidates.append(unwrap_xiaohongshu_ref(current.get("notes")))
    notes: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate = unwrap_xiaohongshu_ref(candidate)
        if not isinstance(candidate, list):
            continue
        for entry in candidate:
            if isinstance(entry, dict):
                notes.append(entry)
    return notes


def xiaohongshu_deep_state_notes(state: dict[str, Any], board_id: str) -> list[dict[str, Any]]:
    """Find board note objects outside boardFeedsMap in the live page state."""
    seen: set[str] = set()
    notes: list[dict[str, Any]] = []

    def looks_like_note(node: dict[str, Any]) -> bool:
        card = xiaohongshu_note_card(node)
        note_id = xiaohongshu_note_id(node, card)
        if not note_id:
            return False
        if note_id == board_id:
            return False
        markers = (
            "noteId", "note_id", "xsecToken", "xsec_token", "displayTitle", "noteCard",
            "note_card", "video", "videoInfo", "imageList", "images", "cover", "type", "noteType",
        )
        return any(key in node for key in markers) or card is not node

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if looks_like_note(node):
                card = xiaohongshu_note_card(node)
                note_id = xiaohongshu_note_id(node, card)
                if note_id and note_id not in seen:
                    seen.add(note_id)
                    notes.append(node)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(state)
    return notes


def xiaohongshu_dom_board_notes(
    *,
    board_id: str,
    url: str,
    timeout: int = 30,
    runner: Any = subprocess.run,
    browser: str | None = None,
) -> dict[str, Any]:
    """Extract visible board note cards from an already-open browser tab.

    This fallback is read-only: it does not open a browser, read cookies, click,
    or download media. It only light-scrolls a matching already-open board tab
    and reads note anchors from the DOM. Chrome is tried first; Safari is tried
    second so Safari-authenticated board pages are not misreported as missing.
    """
    extract_js = r"""
(function(){
const boardId=__WATCHBRIEF_BOARD_ID__;
const provider=__WATCHBRIEF_DOM_PROVIDER__;
const unwrap=v=>(v&&v.__v_isRef===true)?(v._value!==undefined?v._value:v._rawValue):v;
const state=window.__INITIAL_STATE__||{};
const board=state.board||{};
const details=unwrap(board.boardDetails)||{};
const feedsMap=unwrap(board.boardFeedsMap)||{};
const current=unwrap(feedsMap[boardId])||{};
const stateNotes=unwrap(current.notes);
if (Array.isArray(stateNotes) && stateNotes.length) {
  return JSON.stringify({provider:provider,url:location.href,title:details.name||document.title,declared_total_count:details.total||0,notes:stateNotes});
}
const text=e=>(e&&e.textContent||'').replace(/\s+/g,' ').trim();
const pickTitle=e=>text(e.querySelector('.title,.note-title,[class*=title],[class*=Title]'))||text(e);
const noteFromAnchor=a=>{
  const href=a.href||a.getAttribute('href')||'';
  const m=href.match(/\/(?:explore|discovery\/item)\/([0-9a-f]{24})/i);
  if(!m)return null;
  const card=a.closest('[class*=note],[class*=card],[data-note-id],section,article,div')||a;
  const img=card.querySelector('img');
  const video=card.querySelector('video,[class*=play],[class*=video],[aria-label*=视频]');
  const type=(video?'video':'normal');
  return {noteId:m[1],url:href,title:pickTitle(card),type:type,noteType:type,cover:img?{url:(img.currentSrc||img.src||'')}:undefined};
};
const notes=[];
const seen=new Set();
document.querySelectorAll('a[href*="/explore/"],a[href*="/discovery/item/"]').forEach(a=>{const n=noteFromAnchor(a);if(n&&!seen.has(n.noteId)){seen.add(n.noteId);notes.push(n);}});
return JSON.stringify({provider:provider,url:location.href,title:details.name||document.title,declared_total_count:details.total||0,notes});
})()
"""
    script = r'''
on run argv
    set targetBrowser to item 1 of argv
    set targetBoardId to item 2 of argv
    set jsSource to item 3 of argv
    set scrollSource to "window.scrollBy(0, Math.max(600, window.innerHeight || 800)); 'ok';"
    if targetBrowser is "chrome" then
        tell application "Google Chrome"
            repeat with wi from 1 to count of windows
                repeat with ti from 1 to count of tabs of window wi
                    set tabUrl to URL of tab ti of window wi
                    if tabUrl contains targetBoardId then
                        repeat 5 times
                            execute javascript scrollSource in tab ti of window wi
                            delay 0.45
                        end repeat
                        return execute javascript jsSource in tab ti of window wi
                    end if
                end repeat
            end repeat
        end tell
        return "{\"provider\":\"chrome-dom\",\"notes\":[],\"error\":\"NO_MATCHING_TAB\"}"
    else if targetBrowser is "safari" then
        tell application "Safari"
            repeat with wi from 1 to count of windows
                repeat with ti from 1 to count of tabs of window wi
                    set tabUrl to URL of tab ti of window wi
                    if tabUrl contains targetBoardId then
                        repeat 5 times
                            do JavaScript scrollSource in tab ti of window wi
                            delay 0.45
                        end repeat
                        return do JavaScript jsSource in tab ti of window wi
                    end if
                end repeat
            end repeat
        end tell
        return "{\"provider\":\"safari-dom\",\"notes\":[],\"error\":\"NO_MATCHING_TAB\"}"
    end if
    return "{\"provider\":\"browser-dom\",\"notes\":[],\"error\":\"UNKNOWN_BROWSER\"}"
end run
'''

    requested_browser = str(browser or "").strip().lower()
    if requested_browser in {"google chrome", "chrome"}:
        browser_order = (("chrome", "chrome-dom"),)
    elif requested_browser == "safari":
        browser_order = (("safari", "safari-dom"),)
    else:
        browser_order = (("chrome", "chrome-dom"), ("safari", "safari-dom"))

    errors: list[str] = []
    for browser_name, provider in browser_order:
        js = extract_js.replace("__WATCHBRIEF_DOM_PROVIDER__", json.dumps(provider)).replace("__WATCHBRIEF_BOARD_ID__", json.dumps(board_id))
        try:
            result = runner(["osascript", "-e", script, browser_name, board_id, js], capture_output=True, text=True, timeout=min(timeout, 30))
        except Exception as exc:
            errors.append(f"{provider}:{exc}")
            continue
        if result.returncode != 0:
            errors.append(f"{provider}:{str(result.stderr or 'osascript failed').strip()}")
            continue
        raw = str(result.stdout or "").strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            errors.append(f"{provider}:DOM_FALLBACK_INVALID_JSON")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{provider}:DOM_FALLBACK_NON_OBJECT")
            continue
        notes = payload.get("notes")
        error = str(payload.get("error") or "")
        if isinstance(notes, list) and any(isinstance(entry, dict) for entry in notes):
            return payload
        errors.append(f"{provider}:{error or 'no notes in DOM fallback'}")
    return {"provider": "browser-dom", "notes": [], "error": "; ".join(errors) or "NO_MATCHING_TAB"}


def refresh_xiaohongshu_media_url_from_browser(
    note_url: str,
    *,
    browser: str = "safari",
    timeout: int = 30,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    """Open/refresh a Xiaohongshu note page and return an in-memory media URL.

    The signed media URL is returned to the caller for immediate download, but
    debug fields intentionally contain only booleans/counts/types so manifests
    and HTML do not persist tokenized URLs.
    """
    requested_browser = str(browser or "safari").strip().lower()
    if requested_browser in {"google chrome", "chrome"}:
        browser_name = "chrome"
        provider = "chrome-performance"
    else:
        browser_name = "safari"
        provider = "safari-performance"
    js = r"""
(function(){
  function isMedia(u){return /^https?:/i.test(String(u||'')) && /(sns-video|\.mp4|\.m3u8)/i.test(String(u||''));}
  function mediaType(u){var s=String(u||''); if(/\.m3u8/i.test(s)) return 'm3u8'; if(/\.mp4/i.test(s)) return 'mp4'; return 'media';}
  var videos=[...document.querySelectorAll('video')];
  var resources=performance.getEntriesByType('resource').map(e=>e.name).filter(isMedia);
  var mediaUrl=resources.length ? resources[resources.length-1] : '';
  return JSON.stringify({
    media_url: mediaUrl,
    provider: '__WATCHBRIEF_PROVIDER__',
    browser: '__WATCHBRIEF_BROWSER__',
    media_url_available: !!mediaUrl,
    media_url_type: mediaType(mediaUrl),
    resource_count: resources.length,
    video_element_count: videos.length
  });
})()
""".replace("__WATCHBRIEF_PROVIDER__", provider).replace("__WATCHBRIEF_BROWSER__", browser_name)
    script = r'''
on run argv
    set targetBrowser to item 1 of argv
    set targetUrlFile to item 2 of argv
    set jsSource to item 3 of argv
    set targetUrl to read POSIX file targetUrlFile as «class utf8»
    if targetBrowser is "chrome" then
        tell application "Google Chrome"
            if (count of windows) = 0 then make new window
            set targetTab to missing value
            repeat with wi from 1 to count of windows
                repeat with ti from 1 to count of tabs of window wi
                    try
                        if URL of tab ti of window wi contains targetUrl then set targetTab to tab ti of window wi
                    end try
                end repeat
            end repeat
            if targetTab is missing value then
                tell window 1
                    set targetTab to make new tab with properties {URL:targetUrl}
                end tell
            else
                set URL of targetTab to targetUrl
            end if
            delay 8
            return execute javascript jsSource in targetTab
        end tell
    else
        tell application "Safari"
            activate
            if (count of windows) = 0 then make new document
            set targetTab to current tab of window 1
            set URL of targetTab to targetUrl
            delay 8
            return do JavaScript jsSource in targetTab
        end tell
    end if
end run
'''
    temp_url_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", prefix="watchbrief_xhs_note_url_", delete=False) as handle:
            handle.write(str(note_url or ""))
            temp_url_path = handle.name
        result = runner(["osascript", "-e", script, browser_name, temp_url_path, js], capture_output=True, text=True, timeout=min(timeout, 45))
    except Exception as exc:
        if temp_url_path:
            try:
                Path(temp_url_path).unlink(missing_ok=True)
            except Exception:
                pass
        return {"media_url": "", "provider": provider, "browser": browser_name, "media_url_available": False, "reason_code": "media_url_refresh_failed", "error": str(exc)}
    if temp_url_path:
        try:
            Path(temp_url_path).unlink(missing_ok=True)
        except Exception:
            pass
    if result.returncode != 0:
        return {
            "media_url": "",
            "provider": provider,
            "browser": browser_name,
            "media_url_available": False,
            "reason_code": "media_url_refresh_failed",
            "error": str(result.stderr or "osascript failed").strip(),
        }
    try:
        payload = json.loads(str(result.stdout or "").strip())
    except json.JSONDecodeError:
        return {"media_url": "", "provider": provider, "browser": browser_name, "media_url_available": False, "reason_code": "media_url_refresh_invalid_json"}
    if not isinstance(payload, dict):
        return {"media_url": "", "provider": provider, "browser": browser_name, "media_url_available": False, "reason_code": "media_url_refresh_non_object"}
    payload.setdefault("provider", provider)
    payload.setdefault("browser", browser_name)
    payload["media_url_available"] = bool(str(payload.get("media_url") or ""))
    return payload


def xiaohongshu_api_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def fetch_xiaohongshu_board_notes_api(
    board_id: str,
    *,
    headers: dict[str, str],
    urlopen_func: Any,
    timeout: int,
    cursor: str = "",
    num: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    api_url = XIAOHONGSHU_BOARD_API + "?" + urllib.parse.urlencode({
        "boardId": board_id,
        "num": num,
        "cursor": cursor,
        "imageFormats": "jpg,webp,avif",
    })
    text = fetch_text(api_url, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 120))
    payload = json.loads(text)
    data = xiaohongshu_api_data(payload)
    raw_notes = data.get("notes")
    notes = [entry for entry in raw_notes if isinstance(entry, dict)] if isinstance(raw_notes, list) else []
    return notes, {
        "cursor": str(data.get("cursor") or ""),
        "has_more": bool(data.get("hasMore") or data.get("has_more")),
        "api_url_path": "/api/sns/web/v1/board/note",
    }


def xiaohongshu_note_card(note: dict[str, Any]) -> dict[str, Any]:
    for key in ("noteCard", "note_card", "card"):
        value = note.get(key)
        if isinstance(value, dict):
            return value
    return note


def first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def image_url_from_value(value: Any) -> str:
    if isinstance(value, dict):
        return first_text(value.get("url"), value.get("urlDefault"), value.get("urlPre"), value.get("src"))
    if isinstance(value, str):
        return value.strip()
    return ""


def first_image_url(*values: Any) -> str:
    for value in values:
        if isinstance(value, list) and value:
            url = image_url_from_value(value[0])
        else:
            url = image_url_from_value(value)
        if url:
            return url
    return ""


def xiaohongshu_note_video_blob(note: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    for container in (card, note):
        for key in ("video", "videoInfo", "video_info", "videoMedia", "video_media"):
            value = container.get(key)
            if isinstance(value, dict):
                return value
    return {}


def xiaohongshu_video_url(video_blob: dict[str, Any]) -> str:
    for key in ("url", "videoUrl", "video_url", "masterUrl", "master_url"):
        value = first_text(video_blob.get(key))
        if value:
            return value
    media = video_blob.get("media")
    if isinstance(media, dict):
        return first_text(media.get("streamUrl"), media.get("url"))
    return ""


def xiaohongshu_note_id(note: dict[str, Any], card: dict[str, Any]) -> str:
    return first_text(
        note.get("noteId"),
        note.get("note_id"),
        note.get("id"),
        card.get("noteId"),
        card.get("note_id"),
        card.get("id"),
    )


def xiaohongshu_note_author(card: dict[str, Any]) -> str:
    user = card.get("user") if isinstance(card.get("user"), dict) else {}
    return first_text(user.get("nickname"), user.get("nickName"), user.get("name"), user.get("userId"))


def xiaohongshu_note_duration(note: dict[str, Any], card: dict[str, Any], video_blob: dict[str, Any]) -> str:
    return first_text(
        note.get("duration"),
        note.get("durationSec"),
        note.get("duration_seconds"),
        card.get("duration"),
        card.get("durationSec"),
        card.get("duration_seconds"),
        video_blob.get("duration"),
        video_blob.get("durationSec"),
        video_blob.get("duration_seconds"),
    )


def xiaohongshu_note_is_video(note: dict[str, Any], card: dict[str, Any], video_blob: dict[str, Any]) -> bool:
    note_type = first_text(note.get("type"), note.get("noteType"), note.get("note_type"), card.get("type"), card.get("noteType"), card.get("note_type")).lower()
    if "video" in note_type:
        return True
    if video_blob:
        return True
    if xiaohongshu_video_url(video_blob):
        return True
    duration = xiaohongshu_note_duration(note, card, video_blob)
    return bool(duration and duration not in {"0", "0.0"})


def xiaohongshu_note_has_images(note: dict[str, Any], card: dict[str, Any]) -> bool:
    for container in (card, note):
        for key in ("images", "imageList", "image_list", "imageb"):
            value = container.get(key)
            if isinstance(value, list) and value:
                return True
    return False


def normalize_xiaohongshu_board_note(note: dict[str, Any], index: int, *, board_id: str, debug_base: dict[str, Any]) -> dict[str, Any]:
    card = xiaohongshu_note_card(note)
    note_id = xiaohongshu_note_id(note, card)
    xsec_token = first_text(note.get("xsecToken"), note.get("xsec_token"), card.get("xsecToken"), card.get("xsec_token"))
    note_type = first_text(note.get("type"), note.get("noteType"), note.get("note_type"), card.get("type"), card.get("noteType"), card.get("note_type"))
    video_blob = xiaohongshu_note_video_blob(note, card)
    is_video = xiaohongshu_note_is_video(note, card, video_blob)
    has_images = xiaohongshu_note_has_images(note, card)
    title = first_text(note.get("displayTitle"), note.get("title"), card.get("displayTitle"), card.get("title"), f"小红书笔记 {index + 1}")
    source_note_url = first_text(note.get("url"), note.get("webpage_url"), card.get("url"), card.get("webpage_url"))
    download_url = source_note_url or xiaohongshu_note_download_url(note_id, xsec_token)
    if note_id and xsec_token and xiaohongshu_url_kind(download_url) != "note_url_with_xsec":
        download_url = xiaohongshu_note_download_url(note_id, xsec_token)
    note_url = xiaohongshu_canonical_note_url(note_id) or sanitize_xiaohongshu_url_for_output(source_note_url)
    cover = first_image_url(note.get("cover"), card.get("cover"), card.get("imageList"), card.get("images"), note.get("imageList"), note.get("images"))
    duration = xiaohongshu_note_duration(note, card, video_blob)
    media_url = xiaohongshu_video_url(video_blob)
    media_kind = "video" if is_video else "image_text_note" if has_images or not video_blob else "non_video_note"
    resolver_debug = {
        **debug_base,
        "note_id": note_id,
        "board_id": board_id,
        "note_type": note_type,
        "note_media_kind": media_kind,
        "is_video_note": is_video,
        "is_image_text_note": media_kind == "image_text_note",
        "cover": cover,
        "xsec_token_present": bool(xsec_token),
        "note_url_canonicalized": bool(note_url and note_url != download_url),
        "download_url_available": bool(download_url),
        "download_url_type": xiaohongshu_url_kind(download_url),
        "media_url_available": bool(media_url),
        "media_url_type": xiaohongshu_url_kind(media_url),
        "raw_metadata": sanitize_xiaohongshu_metadata_for_debug(note),
    }
    item: dict[str, Any] = {
        "title": title,
        "url": note_url,
        "id": note_id,
        "note_id": note_id,
        "note_url": note_url,
        "note_type": note_type,
        "note_media_kind": media_kind,
        "is_video_note": is_video,
        "is_image_text_note": media_kind == "image_text_note",
        "channel": xiaohongshu_note_author(card) or "未知",
        "duration": duration,
        "date": "未知",
        "cover": cover,
        "resolver_debug": resolver_debug,
    }
    if download_url and download_url != note_url:
        item["_download_url"] = download_url
    if media_url:
        item["_media_url"] = media_url
    if duration:
        item["duration_seconds"] = duration
    if not is_video:
        item["skip_reason_code"] = "non_video_note"
        item["skip_stage"] = "resolver"
    return item


def xiaohongshu_request_headers(url: str, cookie_header: str = "") -> dict[str, str]:
    headers = request_headers(url, cookie_header)
    headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": url,
        "Origin": "https://www.xiaohongshu.com",
    })
    return headers


def resolve_xiaohongshu_board(
    url: str,
    *,
    board_id: str,
    cookie_header: str,
    cookies_source: str,
    attempted_sources: list[str],
    fallback_reason: str,
    urlopen_func: Any,
    timeout: int,
    xiaohongshu_dom_fallback_func: Any = xiaohongshu_dom_board_notes,
) -> dict[str, Any]:
    debug = {
        "resolver_method": "xiaohongshu_board",
        "cookies_source": cookies_source,
        "attempted_cookie_sources": attempted_sources,
        **cookie_debug(
            attempts=browser_attempts_from_source_labels(attempted_sources),
            selected=browser_from_cookie_source_label(cookies_source),
            fallback_reason=fallback_reason,
        ),
        "board_id": board_id,
        "api_path": "/api/sns/web/v1/board/note",
        "page_initial_state_found": False,
        "note_list_provider": "",
        "reason_code": "",
        "provider_attempts": [],
        "provider_errors": {},
        "used_historical_sample_cache": False,
    }
    headers = xiaohongshu_request_headers(url, cookie_header)
    try:
        html = fetch_text(url, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 120))
    except Exception as exc:
        debug["reason_code"] = XIAOHONGSHU_BOARD_RESOLVER_FAILED
        raise XiaohongshuBoardResolverError(
            XIAOHONGSHU_BOARD_RESOLVER_FAILED,
            "Xiaohongshu board page fetch failed",
            ["xiaohongshu-board", url],
            str(exc),
            debug=debug,
        ) from exc

    state = parse_xiaohongshu_initial_state(html)
    debug["page_initial_state_found"] = bool(state)
    details = xiaohongshu_board_details_from_state(state)
    title = first_text(details.get("name"), html_title(html), board_id)
    try:
        total_count = int(details.get("total") or 0)
    except (TypeError, ValueError):
        total_count = 0
    debug.update({"board_title": title, "declared_total_count": total_count})

    api_error = ""
    api_meta: dict[str, Any] = {}
    notes: list[dict[str, Any]] = []
    provider_attempts: list[str] = debug["provider_attempts"]
    provider_errors: dict[str, str] = debug["provider_errors"]

    provider_attempts.append("api")
    try:
        notes, api_meta = fetch_xiaohongshu_board_notes_api(
            board_id,
            headers=headers,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )
        if notes:
            debug["note_list_provider"] = "board-note-api"
        else:
            provider_errors["api"] = "no notes returned by board note api"
    except Exception as exc:
        api_error = str(exc)
        provider_errors["api"] = api_error
        debug["api_error"] = api_error

    if not notes:
        provider_attempts.append("initial_state")
        notes = xiaohongshu_state_notes(state, board_id)
        if notes:
            debug["note_list_provider"] = "page-initial-state"
        else:
            deep_notes = xiaohongshu_deep_state_notes(state, board_id)
            if deep_notes:
                notes = deep_notes
                debug["note_list_provider"] = "page-initial-state-deep-scan"
            else:
                provider_errors["initial_state"] = "no notes in initial state"

    if not notes:
        provider_attempts.append("dom")
        dom_kwargs = {"board_id": board_id, "url": url, "timeout": timeout}
        selected_browser = browser_from_cookie_source_label(cookies_source)
        if selected_browser:
            dom_kwargs["browser"] = selected_browser
        try:
            dom_payload = xiaohongshu_dom_fallback_func(**dom_kwargs)
        except TypeError:
            dom_payload = xiaohongshu_dom_fallback_func(board_id=board_id, url=url, timeout=timeout)
        dom_notes = dom_payload.get("notes") if isinstance(dom_payload, dict) else []
        if isinstance(dom_notes, list) and any(isinstance(entry, dict) for entry in dom_notes):
            notes = [entry for entry in dom_notes if isinstance(entry, dict)]
            debug["note_list_provider"] = str(dom_payload.get("provider") or "chrome-dom") if isinstance(dom_payload, dict) else "chrome-dom"
            if isinstance(dom_payload, dict):
                dom_title = first_text(dom_payload.get("title"))
                if dom_title and (title == board_id or not title or title.lower().startswith("undefined")):
                    title = dom_title
                try:
                    dom_total = int(dom_payload.get("declared_total_count") or 0)
                except (TypeError, ValueError):
                    dom_total = 0
                if dom_total and not total_count:
                    total_count = dom_total
        else:
            provider_errors["dom"] = str(dom_payload.get("error") or "no notes in DOM fallback") if isinstance(dom_payload, dict) else "DOM_FALLBACK_NON_OBJECT"

    if not notes:
        reason = XIAOHONGSHU_BOARD_EMPTY if total_count == 0 and not api_error else XIAOHONGSHU_BOARD_RESOLVER_FAILED
        debug["reason_code"] = reason
        message = "Xiaohongshu board is empty" if reason == XIAOHONGSHU_BOARD_EMPTY else "Xiaohongshu board note list could not be read"
        raise XiaohongshuBoardResolverError(reason, message, ["xiaohongshu-board", url], api_error or json.dumps(provider_errors, ensure_ascii=False), debug=debug)

    if total_count and len(notes) < total_count:
        provider_errors["note_count"] = f"incomplete note list: got {len(notes)} of declared {total_count}"
        debug["reason_code"] = XIAOHONGSHU_BOARD_RESOLVER_FAILED
        raise XiaohongshuBoardResolverError(
            XIAOHONGSHU_BOARD_RESOLVER_FAILED,
            "Xiaohongshu board note list incomplete",
            ["xiaohongshu-board", url],
            provider_errors["note_count"],
            debug=debug,
        )

    note_debug_base = {
        "resolver_method": "xiaohongshu_board",
        "cookies_source": cookies_source,
        "attempted_cookie_sources": attempted_sources,
        "note_list_provider": debug["note_list_provider"],
    }
    items = [
        normalize_xiaohongshu_board_note(note, index, board_id=board_id, debug_base=note_debug_base)
        for index, note in enumerate(notes)
    ]
    video_count = sum(1 for item in items if item.get("is_video_note"))
    non_video_count = sum(1 for item in items if not item.get("is_video_note"))
    debug.update({
        "reason_code": "",
        "board_title": title,
        "declared_total_count": total_count,
        "note_count": len(items),
        "video_note_count": video_count,
        "non_video_count": non_video_count,
        **api_meta,
    })
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "list",
        "source_subkind": "xiaohongshu_board",
        "source_platform": "小红书",
        "url": url,
        "source_url": url,
        "title": title,
        "playlist_title": title,
        "list_title": title,
        "board_id": board_id,
        "note_count": len(items),
        "video_note_count": video_count,
        "non_video_count": non_video_count,
        "videos": items,
        "resolver_debug": debug,
    }


def collect_bvid_items(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            bvid = str(node.get("bvid") or node.get("bvidStr") or "").strip()
            if bvid.startswith("BV"):
                items.append({
                    "bvid": bvid,
                    "title": str(node.get("title") or node.get("name") or "").strip(),
                    "duration": str(node.get("duration") or node.get("duration_string") or "").strip(),
                    "channel": str((node.get("upper") or {}).get("name") if isinstance(node.get("upper"), dict) else node.get("uploader") or "").strip(),
                    **publish_metadata_from_entry(node),
                })
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        bvid = item["bvid"]
        if bvid in seen:
            continue
        seen.add(bvid)
        unique.append(item)
    return unique


def bvid_from_entry(entry: dict[str, Any]) -> str:
    for key in ("bvid", "id", "url", "webpage_url", "original_url"):
        value = str(entry.get(key) or "").strip()
        if value.startswith("BV"):
            return value
        match = BVID_RE.search(value)
        if match:
            return match.group(1)
    return ""


def has_metadata_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def is_placeholder_video_title(value: Any) -> bool:
    return bool(re.fullmatch(r"Video\s+\d+", str(value or "").strip()))


def fill_video_from_bilibili_view_metadata(
    video: dict[str, Any],
    *,
    cookie_header: str,
    source_url: str,
    urlopen_func: Any,
    timeout: int,
) -> dict[str, Any]:
    current = dict(video)
    bvid = str(current.get("bvid") or current.get("id") or "").strip()
    if not bvid.startswith("BV"):
        bvid = bilibili_bvid_from_url(str(current.get("url") or ""))
    if not bvid:
        return current
    try:
        metadata = fetch_bilibili_view_metadata(
            bvid,
            headers=request_headers(str(current.get("url") or source_url), cookie_header),
            urlopen_func=urlopen_func,
            timeout=min(timeout, 120),
        )
    except Exception:
        return current
    if not metadata:
        return current
    title = str(metadata.get("title") or "").strip()
    if title and (not current.get("title") or is_placeholder_video_title(current.get("title"))):
        current["title"] = title
    if metadata.get("duration") not in (None, "", 0) and not has_metadata_value(current.get("duration")):
        current["duration"] = metadata.get("duration")
    owner = metadata.get("owner") if isinstance(metadata.get("owner"), dict) else {}
    channel = str(owner.get("name") or "").strip()
    if channel and not current.get("channel"):
        current["channel"] = channel
    pages = metadata.get("pages") if isinstance(metadata.get("pages"), list) else []
    if pages and not current.get("cid"):
        first_page = next((page for page in pages if isinstance(page, dict)), {})
        if isinstance(first_page, dict) and first_page.get("cid") not in (None, ""):
            current["cid"] = str(first_page.get("cid"))
    for key, value in publish_metadata_from_entry(metadata).items():
        if has_metadata_value(value) and not has_metadata_value(current.get(key)):
            current[key] = value
    current["bvid"] = bvid
    return current


def enrich_bilibili_list_item_metadata(
    videos: list[dict[str, Any]],
    *,
    source_url: str,
    cookie_header: str,
    urlopen_func: Any,
    timeout: int,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for video in videos:
        needs_metadata = (
            not has_metadata_value(video.get("title"))
            or is_placeholder_video_title(video.get("title"))
            or not has_metadata_value(video.get("duration"))
            or not has_metadata_value(video.get("channel"))
        )
        enriched.append(
            fill_video_from_bilibili_view_metadata(
                video,
                cookie_header=cookie_header,
                source_url=source_url,
                urlopen_func=urlopen_func,
                timeout=timeout,
            )
            if needs_metadata
            else dict(video)
        )
    return enriched


def assert_bilibili_list_has_real_titles(videos: list[dict[str, Any]], *, source_url: str, resolver_debug: dict[str, Any]) -> None:
    bad = [str(video.get("bvid") or video.get("url") or index + 1) for index, video in enumerate(videos) if is_placeholder_video_title(video.get("title")) or not str(video.get("title") or "").strip()]
    if bad:
        debug = dict(resolver_debug or {})
        debug["missing_title_items"] = bad[:10]
        debug["missing_title_count"] = len(bad)
        raise BilibiliResolverError(
            BILIBILI_LIST_EXPANSION_FAILED,
            "Bilibili list item titles were not resolved; refusing to render placeholder Video N reports",
            ["bilibili-list-title-validation", source_url],
            debug=debug,
        )


def publish_metadata_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in PUBLISH_METADATA_FIELDS:
        value = entry.get(key)
        if has_metadata_value(value):
            metadata[key] = value
    if "pubdate" not in metadata:
        for key in BILIBILI_PUBLISH_TIME_FIELDS:
            value = entry.get(key)
            if has_metadata_value(value):
                metadata["pubdate"] = value
                break
    return metadata


def normalize_bilibili_list_video(
    *,
    bvid: str,
    index: int,
    title: str = "",
    duration: Any = "",
    channel: str = "",
    publish_metadata: Optional[dict[str, Any]] = None,
    resolver_debug: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    video_url = f"https://www.bilibili.com/video/{bvid}"
    item: dict[str, Any] = {
        "title": str(title or f"Video {index + 1}"),
        "url": video_url,
        "id": bvid,
        "bvid": bvid,
        "duration": str(duration or ""),
        "channel": str(channel or ""),
    }
    if publish_metadata:
        item.update({key: value for key, value in publish_metadata.items() if has_metadata_value(value)})
    if resolver_debug is not None:
        item["resolver_debug"] = resolver_debug
    return item


def videos_from_bilibili_list_api(data: dict[str, Any], resolver_debug: dict[str, Any]) -> list[dict[str, Any]]:
    medias = data.get("medias")
    if not isinstance(medias, list):
        return []
    videos: list[dict[str, Any]] = []
    for index, media in enumerate(medias):
        if not isinstance(media, dict):
            continue
        bvid = str(media.get("bvid") or "").strip()
        if not bvid:
            continue
        upper = media.get("upper") if isinstance(media.get("upper"), dict) else {}
        videos.append(normalize_bilibili_list_video(
            bvid=bvid,
            index=index,
            title=str(media.get("title") or ""),
            duration=media.get("duration") or "",
            channel=str(upper.get("name") or ""),
            publish_metadata=publish_metadata_from_entry(media),
            resolver_debug=resolver_debug,
        ))
    return videos


def list_title_from_bilibili_api(data: dict[str, Any]) -> str:
    info = data.get("info")
    if isinstance(info, dict):
        return str(info.get("title") or "").strip()
    return ""


def choose_bilibili_list_title(*candidates: Any, videos: list[dict[str, Any]], list_id: str) -> str:
    item_titles = {str(video.get("title") or "").strip() for video in videos if str(video.get("title") or "").strip()}
    cleaned = [str(candidate or "").strip() for candidate in candidates if str(candidate or "").strip()]
    for candidate in cleaned:
        if len(item_titles) > 1 and candidate in item_titles:
            continue
        return candidate
    return cleaned[0] if cleaned else str(list_id or "Bilibili List")


def build_bilibili_list_resolution(
    source_url: str,
    *,
    videos: list[dict[str, Any]],
    title: str,
    list_id: str,
    resolver_debug: dict[str, Any],
    fallback_title: str = "",
) -> dict[str, Any]:
    if not videos:
        raise BilibiliResolverError(
            BILIBILI_LIST_EXPANSION_FAILED,
            "Bilibili list expansion returned no videos",
            ["bilibili-list-expansion", source_url],
            debug=resolver_debug,
        )
    resolved_title = choose_bilibili_list_title(title, fallback_title, videos=videos, list_id=list_id)
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "list",
        "url": source_url,
        "source_url": source_url,
        "title": resolved_title,
        "playlist_title": resolved_title,
        "list_title": resolved_title,
        "list_id": list_id,
        "video_count": len(videos),
        "videos": videos,
        "resolver_debug": resolver_debug,
    }


def resolve_bilibili_list_from_page_or_api(
    url: str,
    *,
    list_id: str,
    cookie_header: str,
    cookies_source: str,
    attempted_sources: list[str],
    fallback_reason: str,
    urlopen_func: Any,
    timeout: int,
) -> dict[str, Any]:
    debug = {
        "resolver_method": "bilibili_list_expansion",
        "cookies_source": cookies_source,
        "attempted_cookie_sources": attempted_sources,
        **cookie_debug(
            attempts=browser_attempts_from_source_labels(attempted_sources),
            selected=browser_from_cookie_source_label(cookies_source),
            fallback_reason=fallback_reason,
        ),
        "fallback_method": "bilibili_fav_list_api",
        "fallback_success": False,
        "reason_code": BILIBILI_LIST_EXPANSION_FAILED,
        "is_412": False,
        "list_id": list_id,
        "expansion_provider": "",
    }
    headers = request_headers(url, cookie_header)
    api_data = fetch_bilibili_list_metadata(list_id, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 120))
    api_videos = videos_from_bilibili_list_api(api_data, debug) if api_data else []
    api_title = list_title_from_bilibili_api(api_data) if api_data else ""
    page_title = ""
    if api_videos:
        api_videos = enrich_bilibili_list_item_metadata(
            api_videos,
            source_url=url,
            cookie_header=cookie_header,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )
        assert_bilibili_list_has_real_titles(api_videos, source_url=url, resolver_debug=debug)
        if len({str(video.get("title") or "").strip() for video in api_videos if str(video.get("title") or "").strip()}) > 1 and api_title and any(api_title == str(video.get("title") or "").strip() for video in api_videos):
            try:
                page_title = html_title(fetch_text(url, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 120)))
            except Exception:
                page_title = ""
        debug["fallback_success"] = True
        debug["reason_code"] = ""
        debug["expansion_provider"] = "bilibili-fav-list-api"
        return build_bilibili_list_resolution(
            url,
            videos=api_videos,
            title=api_title,
            fallback_title=page_title,
            list_id=list_id,
            resolver_debug=debug,
        )

    try:
        html = fetch_text(url, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 120))
    except Exception as exc:
        raise BilibiliResolverError(
            BILIBILI_LIST_EXPANSION_FAILED,
            "Bilibili list expansion page fetch failed",
            ["bilibili-list-expansion", url],
            str(exc),
            debug=debug,
        ) from exc

    initial_state = extract_json_after_marker(html, "__INITIAL_STATE__") or {}
    bvid_items = collect_bvid_items(initial_state)
    if not bvid_items:
        bvid_items = [{"bvid": match, "title": "", "duration": "", "channel": ""} for match in BVID_RE.findall(html)]
    unique_items: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in bvid_items:
        bvid = str(item.get("bvid") or "").strip()
        if not bvid or bvid in seen:
            continue
        seen.add(bvid)
        unique_items.append(item)
    videos = [
        normalize_bilibili_list_video(
            bvid=str(item.get("bvid") or ""),
            index=index,
            title=str(item.get("title") or ""),
            duration=item.get("duration") or "",
            channel=str(item.get("channel") or ""),
            publish_metadata=publish_metadata_from_entry(item),
            resolver_debug=debug,
        )
        for index, item in enumerate(unique_items)
    ]
    if videos:
        videos = enrich_bilibili_list_item_metadata(
            videos,
            source_url=url,
            cookie_header=cookie_header,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )
        assert_bilibili_list_has_real_titles(videos, source_url=url, resolver_debug=debug)
        debug["fallback_method"] = "bilibili_page_initial_state"
        debug["fallback_success"] = True
        debug["reason_code"] = ""
        debug["expansion_provider"] = "page-initial-state"
        return build_bilibili_list_resolution(
            url,
            videos=videos,
            title=html_title(html) or list_id,
            list_id=list_id,
            resolver_debug=debug,
        )
    raise BilibiliResolverError(
        BILIBILI_LIST_EXPANSION_FAILED,
        "Bilibili list expansion failed",
        ["bilibili-list-expansion", url],
        debug=debug,
    )


def merge_bilibili_video_data(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    for key, value in fallback.items():
        if merged.get(key) in (None, "", [], {}):
            merged[key] = value
    return merged


def bilibili_cid_from_video_data(video_data: dict[str, Any]) -> str:
    cid = str(video_data.get("cid") or "").strip()
    if cid:
        return cid
    pages = video_data.get("pages")
    if isinstance(pages, list) and pages:
        first = pages[0]
        if isinstance(first, dict):
            return str(first.get("cid") or "").strip()
    return ""


def bilibili_owner_from_video_data(video_data: dict[str, Any], initial_state: dict[str, Any]) -> str:
    owner = video_data.get("owner")
    if isinstance(owner, dict) and owner.get("name"):
        return str(owner["name"])
    up_data = initial_state.get("upData")
    if isinstance(up_data, dict) and up_data.get("name"):
        return str(up_data["name"])
    return "未知"


def build_bilibili_resolution_from_metadata(
    url: str,
    video_data: dict[str, Any],
    initial_state: dict[str, Any],
    debug: dict[str, Any],
) -> dict[str, Any]:
    bvid = str(video_data.get("bvid") or bilibili_bvid_from_url(url)).strip()
    cid = bilibili_cid_from_video_data(video_data)
    if not bvid:
        raise BilibiliResolverError(BILIBILI_METADATA_NOT_FOUND, "Bilibili bvid was not found", ["bilibili-metadata-fallback", url], debug=debug)
    if not cid:
        raise BilibiliResolverError(BILIBILI_CID_NOT_FOUND, "Bilibili cid was not found", ["bilibili-metadata-fallback", url], debug=debug)

    title = str(video_data.get("title") or "Untitled Video")
    duration = str(video_data.get("duration") or "")
    channel = bilibili_owner_from_video_data(video_data, initial_state)
    publish_metadata = publish_metadata_from_entry(video_data)
    item = {
        "title": title,
        "url": url,
        "id": bvid,
        "bvid": bvid,
        "cid": cid,
        "duration": duration,
        "channel": channel,
        "resolver_debug": debug,
    }
    item.update(publish_metadata)
    root_metadata = {
        "duration": duration,
        "channel": channel,
        **publish_metadata,
    }
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "single",
        "url": url,
        "title": title,
        "id": bvid,
        "bvid": bvid,
        "cid": cid,
        **root_metadata,
        "videos": [item],
        "resolver_debug": debug,
    }


def resolve_bilibili_with_page_fallback(
    url: str,
    *,
    cookie_header: str,
    cookies_source: str,
    attempted_sources: list[str],
    last_reason_code: str,
    urlopen_func: Any,
    timeout: int,
) -> dict[str, Any]:
    debug = {
        "resolver_method": "bilibili_metadata_fallback",
        "cookies_source": cookies_source,
        "attempted_cookie_sources": attempted_sources,
        **cookie_debug(
            attempts=browser_attempts_from_source_labels(attempted_sources),
            selected=browser_from_cookie_source_label(cookies_source),
            fallback_reason=last_reason_code,
        ),
        "fallback_method": "bilibili_page_metadata",
        "fallback_success": False,
        "reason_code": last_reason_code,
        "is_412": last_reason_code == BILIBILI_412_BLOCKED,
    }
    command = ["bilibili-metadata-fallback", url]
    headers = request_headers(url, cookie_header)
    try:
        html = fetch_text(url, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 120))
    except Exception as exc:
        reason = classify_bilibili_fallback_exception(exc)
        debug["reason_code"] = reason
        raise BilibiliResolverError(reason, "Bilibili metadata fallback page fetch failed", command, str(exc), debug=debug) from exc

    initial_state = extract_json_after_marker(html, "__INITIAL_STATE__")
    playinfo = extract_json_after_marker(html, "__playinfo__")
    if not isinstance(initial_state, dict) and not isinstance(playinfo, dict):
        bvid = bilibili_bvid_from_url(url)
        if not bvid:
            debug["reason_code"] = BILIBILI_METADATA_NOT_FOUND
            raise BilibiliResolverError(BILIBILI_METADATA_NOT_FOUND, "Bilibili metadata markers were not found", command, debug=debug)
        video_data = fetch_bilibili_view_metadata(
            bvid,
            headers=headers,
            urlopen_func=urlopen_func,
            timeout=min(timeout, 120),
        )
        if not video_data:
            debug["reason_code"] = BILIBILI_METADATA_NOT_FOUND
            raise BilibiliResolverError(BILIBILI_METADATA_NOT_FOUND, "Bilibili metadata markers were not found", command, debug=debug)
        debug["fallback_method"] = "bilibili_view_api"
        debug["fallback_success"] = True
        debug["reason_code"] = ""
        return build_bilibili_resolution_from_metadata(url, video_data, {}, debug)

    if isinstance(initial_state, dict):
        video_data = video_data_from_initial_state(initial_state)
    else:
        initial_state = {}
        video_data = {}

    bvid = str(video_data.get("bvid") or bilibili_bvid_from_url(url)).strip()
    if bvid:
        api_video_data = fetch_bilibili_view_metadata(
            bvid,
            headers=headers,
            urlopen_func=urlopen_func,
            timeout=min(timeout, 120),
        )
        if api_video_data:
            video_data = merge_bilibili_video_data(video_data, api_video_data)

    if not video_data:
        debug["reason_code"] = BILIBILI_METADATA_NOT_FOUND
        raise BilibiliResolverError(BILIBILI_METADATA_NOT_FOUND, "Bilibili video metadata was not found", command, debug=debug)

    if not bilibili_cid_from_video_data(video_data):
        debug["reason_code"] = BILIBILI_PLAYINFO_UNAVAILABLE if isinstance(playinfo, dict) else BILIBILI_CID_NOT_FOUND
        raise BilibiliResolverError(str(debug["reason_code"]), "Bilibili cid/playinfo was not available", command, debug=debug)

    debug["fallback_success"] = True
    debug["reason_code"] = ""
    return build_bilibili_resolution_from_metadata(url, video_data, initial_state, debug)


def canonical_entry_url(entry: dict[str, Any]) -> str:
    for key in ("webpage_url", "original_url"):
        value = str(entry.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    raw_url = str(entry.get("url") or "").strip()
    if raw_url.startswith(("http://", "https://")):
        return raw_url
    extractor = str(entry.get("extractor_key") or entry.get("ie_key") or "").lower()
    if raw_url and "youtube" in extractor:
        return f"https://www.youtube.com/watch?v={raw_url}"
    return raw_url


def normalize_video_entry(entry: dict[str, Any], index: int) -> dict[str, Any]:
    url = canonical_entry_url(entry)
    if not url:
        raise ResolverError(f"resolved list entry {index} does not contain a usable URL")
    normalized = {
        "title": str(entry.get("title") or f"Video {index + 1}"),
        "url": url,
        "id": str(entry.get("id") or ""),
        "duration": str(entry.get("duration_string") or entry.get("duration") or ""),
        "channel": str(entry.get("channel") or entry.get("uploader") or ""),
    }
    normalized.update(publish_metadata_from_entry(entry))
    for optional_key in ("bvid", "cid", "resolver_debug"):
        if optional_key in entry:
            normalized[optional_key] = entry[optional_key]  # type: ignore[assignment]
    return normalized


def bilibili_page_number_from_url(url: str, fallback: int) -> int:
    parsed = urllib.parse.urlparse(str(url or ""))
    query = urllib.parse.parse_qs(parsed.query)
    raw = query.get("p", [""])[0]
    try:
        page = int(raw)
    except (TypeError, ValueError):
        page = fallback
    return page if page > 0 else fallback


def enrich_bilibili_multipart_videos(
    videos: list[dict[str, Any]],
    *,
    source_url: str,
    cookie_header: str,
    urlopen_func: Any,
    timeout: int,
) -> list[dict[str, Any]]:
    """Fill Bilibili multi-P entries with per-page title/cid/duration from the view API."""
    bvid = bilibili_bvid_from_url(source_url)
    if not bvid:
        for video in videos:
            candidate = str(video.get("bvid") or video.get("id") or video.get("url") or "")
            match = BVID_RE.search(candidate)
            if match:
                bvid = match.group(1)
                break
    if not bvid:
        return videos
    try:
        video_data = fetch_bilibili_view_metadata(
            bvid,
            headers=request_headers(source_url, cookie_header),
            urlopen_func=urlopen_func,
            timeout=min(timeout, 120),
        )
    except Exception:
        return videos
    pages = video_data.get("pages") if isinstance(video_data, dict) else None
    if not isinstance(pages, list) or not pages:
        return videos
    page_by_index = {idx: page for idx, page in enumerate(pages, 1) if isinstance(page, dict)}
    owner = video_data.get("owner") if isinstance(video_data.get("owner"), dict) else {}
    channel = str(owner.get("name") or "")
    publish_metadata = publish_metadata_from_entry(video_data)
    enriched: list[dict[str, Any]] = []
    for fallback_index, video in enumerate(videos, 1):
        current = dict(video)
        page_index = bilibili_page_number_from_url(str(current.get("url") or ""), fallback_index)
        page = page_by_index.get(page_index, {})
        if page:
            part = str(page.get("part") or "").strip()
            if part and (not current.get("title") or str(current.get("title")).startswith("Video ")):
                current["title"] = part
            if page.get("duration") not in (None, "", 0):
                current["duration"] = page.get("duration")
            if page.get("cid") not in (None, ""):
                current["cid"] = str(page.get("cid"))
            current["page_index"] = page_index
        current.setdefault("bvid", bvid)
        if channel and not current.get("channel"):
            current["channel"] = channel
        for key, value in publish_metadata.items():
            if has_metadata_value(value) and not has_metadata_value(current.get(key)):
                current[key] = value
        enriched.append(current)
    return enriched


def build_bilibili_list_resolution_from_ytdlp(
    source_url: str,
    payload: dict[str, Any],
    *,
    list_id: str,
    cookie_header: str,
    selected_source_label: str,
    attempted_sources: list[str],
    fallback_reason: str,
    urlopen_func: Any,
    timeout: int,
) -> dict[str, Any]:
    debug = dict(payload.get("_resolver_debug") or {})
    debug.update({
        "list_id": list_id,
        "expansion_provider": "yt-dlp-flat-playlist",
    })
    headers = request_headers(source_url, cookie_header)
    api_data = fetch_bilibili_list_metadata(list_id, headers=headers, urlopen_func=urlopen_func, timeout=min(timeout, 120))
    api_title = list_title_from_bilibili_api(api_data) if api_data else ""
    api_videos = videos_from_bilibili_list_api(api_data, debug) if api_data else []
    api_by_bvid = {str(video.get("bvid") or ""): video for video in api_videos}
    if api_videos:
        debug["metadata_provider"] = "bilibili-fav-list-api"

    entries = payload.get("entries")
    videos: list[dict[str, Any]] = []
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            bvid = bvid_from_entry(entry)
            if not bvid:
                continue
            api_video = api_by_bvid.get(bvid, {})
            entry_title = str(entry.get("title") or "").strip()
            api_title = str(api_video.get("title") or "").strip()
            title = api_title if (api_title and (not entry_title or is_placeholder_video_title(entry_title))) else entry_title
            videos.append(normalize_bilibili_list_video(
                bvid=bvid,
                index=index,
                title=title,
                duration=entry.get("duration_string") or entry.get("duration") or api_video.get("duration") or "",
                channel=str(entry.get("channel") or entry.get("uploader") or api_video.get("channel") or ""),
                publish_metadata={
                    **publish_metadata_from_entry(entry),
                    **publish_metadata_from_entry(api_video),
                },
                resolver_debug=debug,
            ))
    if videos:
        videos = enrich_bilibili_list_item_metadata(
            videos,
            source_url=source_url,
            cookie_header=cookie_header,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )
    if api_videos and len(api_videos) > len(videos):
        api_debug = {
            "resolver_method": "bilibili_list_expansion",
            "cookies_source": selected_source_label,
            "attempted_cookie_sources": attempted_sources,
            **cookie_debug(
                attempts=browser_attempts_from_source_labels(attempted_sources),
                selected=browser_from_cookie_source_label(selected_source_label),
                fallback_reason=fallback_reason,
            ),
            "fallback_method": "bilibili_fav_list_api",
            "fallback_success": True,
            "reason_code": "",
            "is_412": False,
            "list_id": list_id,
            "expansion_provider": "bilibili-fav-list-api",
        }
        api_videos = enrich_bilibili_list_item_metadata(
            api_videos,
            source_url=source_url,
            cookie_header=cookie_header,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )
        assert_bilibili_list_has_real_titles(api_videos, source_url=source_url, resolver_debug=api_debug)
        for video in api_videos:
            video["resolver_debug"] = api_debug
        return build_bilibili_list_resolution(
            source_url,
            videos=api_videos,
            title=api_title,
            fallback_title=str(payload.get("playlist_title") or payload.get("title") or ""),
            list_id=list_id,
            resolver_debug=api_debug,
        )
    try:
        expected_count = int(payload.get("playlist_count") or 0)
    except (TypeError, ValueError):
        expected_count = 0
    if expected_count and len(videos) < expected_count:
        return resolve_bilibili_list_from_page_or_api(
            source_url,
            list_id=list_id,
            cookie_header=cookie_header,
            cookies_source=selected_source_label,
            attempted_sources=attempted_sources,
            fallback_reason=fallback_reason,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )
    if videos:
        assert_bilibili_list_has_real_titles(videos, source_url=source_url, resolver_debug=debug)
    if not videos:
        return resolve_bilibili_list_from_page_or_api(
            source_url,
            list_id=list_id,
            cookie_header=cookie_header,
            cookies_source=selected_source_label,
            attempted_sources=attempted_sources,
            fallback_reason=fallback_reason,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )
    return build_bilibili_list_resolution(
        source_url,
        videos=videos,
        title=api_title,
        fallback_title=str(payload.get("playlist_title") or payload.get("title") or ""),
        list_id=list_id,
        resolver_debug=debug,
    )


def resolve_url(
    url: str,
    *,
    runner: Any = subprocess.run,
    timeout: int = 60,
    cookies_from_browser: Optional[str] = None,
    cookie_file: Optional[Path] = None,
    cookie_header: Optional[str] = None,
    allow_browser_auth: bool = True,
    browser_auth_order: tuple[str, ...] = DEFAULT_BROWSER_AUTH_ORDER,
    cookie_browser_attempts: Any = None,
    urlopen_func: Any = urllib.request.urlopen,
    browser_cookie_loader: Any = None,
    xiaohongshu_dom_fallback_func: Any = xiaohongshu_dom_board_notes,
    allow_playlist_expansion: bool = True,
) -> dict[str, Any]:
    source_url = str(url or "").strip()
    if not source_url.startswith(("http://", "https://")):
        raise ResolverError("resolver input must be an http(s) URL")
    bilibili_list_id = bilibili_list_id_from_url(source_url) if is_bilibili_url(source_url) else ""
    xiaohongshu_board_id = xiaohongshu_board_id_from_url(source_url) if is_xiaohongshu_url(source_url) else ""

    if is_netease_music_url(source_url):
        return resolve_netease_program(source_url, urlopen_func=urlopen_func, timeout=timeout)

    if xiaohongshu_board_id:
        attempted_sources: list[str] = []
        selected_source: Optional[dict[str, Any]] = None
        fallback_reason = ""
        last_error: XiaohongshuBoardResolverError | None = None
        sources = cookie_sources(
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookie_file,
            cookie_header=cookie_header,
            allow_browser_auth=allow_browser_auth,
            browser_auth_order=browser_auth_order,
            cookie_browser_attempts=cookie_browser_attempts,
        )
        for source_index, source in enumerate(sources):
            selected_source = source
            label = cookie_source_label(source)
            attempted_sources.append(label)
            try:
                return resolve_xiaohongshu_board(
                    source_url,
                    board_id=xiaohongshu_board_id,
                    cookie_header=source_cookie_header_for_domain(
                        source,
                        cookie_file,
                        cookie_header,
                        domain_name="xiaohongshu.com",
                        browser_cookie_loader=browser_cookie_loader,
                    ),
                    cookies_source=label,
                    attempted_sources=attempted_sources,
                    fallback_reason=fallback_reason,
                    urlopen_func=urlopen_func,
                    timeout=timeout,
                    xiaohongshu_dom_fallback_func=xiaohongshu_dom_fallback_func,
                )
            except XiaohongshuBoardResolverError as exc:
                last_error = exc
                has_next_source = source_index < len(sources) - 1
                if has_next_source and source and source.get("type") == "browser":
                    fallback_reason = str(exc.reason_code or XIAOHONGSHU_BOARD_RESOLVER_FAILED)
                    continue
                break
        if last_error is not None:
            if isinstance(last_error.debug, dict):
                last_error.debug.setdefault("attempted_cookie_sources", attempted_sources)
                last_error.debug.setdefault("cookies_source", cookie_source_label(selected_source))
            raise last_error

    base_command = [
        "yt-dlp",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
    ]
    if allow_playlist_expansion:
        base_command.append("--flat-playlist")
    else:
        base_command.append("--no-playlist")
    if is_bilibili_url(source_url):
        base_command = add_bilibili_resolver_headers(base_command)

    last_command = [*base_command, source_url]
    last_stderr = ""
    last_reason_code = ""
    attempted_sources: list[str] = []
    payload: dict[str, Any] | None = None
    selected_source: Optional[dict[str, Any]] = None

    sources = cookie_sources(
        cookies_from_browser=cookies_from_browser,
        cookie_file=cookie_file,
        cookie_header=cookie_header,
        allow_browser_auth=allow_browser_auth,
        browser_auth_order=browser_auth_order,
        cookie_browser_attempts=cookie_browser_attempts,
    )

    fallback_reason = ""
    for source_index, source in enumerate(sources):
        selected_source = source
        label = cookie_source_label(source)
        attempted_sources.append(label)
        command = add_cookie_source(base_command, source) if source else list(base_command)
        command = [*command, source_url]
        result = runner(command, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise ResolverError(f"resolver returned invalid JSON: {exc}", command, str(result.stdout)[:500]) from exc
            if not isinstance(payload, dict):
                raise ResolverError("resolver JSON must be an object", command)
            payload["_resolver_debug"] = {
                "resolver_method": "yt_dlp",
                "cookies_source": label,
                "attempted_cookie_sources": attempted_sources,
                **cookie_debug(
                    attempts=browser_attempts_from_source_labels(attempted_sources),
                    selected=browser_from_cookie_source_label(label),
                    fallback_reason=fallback_reason,
                ),
                "fallback_method": "",
                "fallback_success": False,
                "reason_code": "",
                "is_412": False,
            }
            selected_source = source
            break
        last_command = command
        last_stderr = str(result.stderr or "")
        last_reason_code = classify_bilibili_resolver_failure(last_stderr) if is_bilibili_url(source_url) else ""
        fallback_candidate_reason = cookie_fallback_reason_from_text(
            last_reason_code or ("platform_restriction" if is_platform_restriction(last_stderr) else "resolver_failed"),
            last_stderr,
        )
        has_next_source = source_index < len(sources) - 1
        if source and source.get("type") == "browser" and has_next_source and fallback_candidate_reason:
            fallback_reason = fallback_candidate_reason
            continue
        if not is_bilibili_url(source_url):
            debug = {
                "resolver_method": "yt_dlp",
                "cookies_source": attempted_sources[-1] if attempted_sources else "none",
                "attempted_cookie_sources": attempted_sources,
                **cookie_debug(
                    attempts=browser_attempts_from_source_labels(attempted_sources),
                    selected=browser_from_cookie_source_label(attempted_sources[-1] if attempted_sources else ""),
                    fallback_reason=fallback_reason or fallback_candidate_reason,
                ),
                "fallback_method": "",
                "fallback_success": False,
                "reason_code": "platform_restriction" if is_platform_restriction(last_stderr) else "resolver_failed",
                "stderr_summary": stderr_summary(last_stderr),
                "is_412": False,
            }
            if is_platform_restriction(last_stderr):
                error = PlatformRestrictionError("resolver", "platform restricted resolver access", command, last_stderr)
                error.debug = debug
                raise error
            error = ResolverError("yt-dlp failed to resolve URL", command, last_stderr)
            error.debug = debug
            raise error
        if last_reason_code not in {BILIBILI_412_BLOCKED, BILIBILI_COOKIE_MISSING}:
            break

    if payload is None:
        if is_bilibili_url(source_url):
            if bilibili_list_id and allow_playlist_expansion:
                return resolve_bilibili_list_from_page_or_api(
                    source_url,
                    list_id=bilibili_list_id,
                    cookie_header=source_cookie_header(selected_source, cookie_file, cookie_header),
                    cookies_source=attempted_sources[-1] if attempted_sources else "none",
                    attempted_sources=attempted_sources,
                    fallback_reason=fallback_reason or last_reason_code,
                    urlopen_func=urlopen_func,
                    timeout=timeout,
                )
            authorized_for_fallback = any(source != "none" for source in attempted_sources)
            if last_reason_code == BILIBILI_412_BLOCKED and authorized_for_fallback:
                try:
                    return resolve_bilibili_with_page_fallback(
                        source_url,
                        cookie_header=source_cookie_header(selected_source, cookie_file, cookie_header),
                        cookies_source=attempted_sources[-1] if attempted_sources else "none",
                        attempted_sources=attempted_sources,
                        last_reason_code=last_reason_code,
                        urlopen_func=urlopen_func,
                        timeout=timeout,
                    )
                except BilibiliResolverError:
                    raise
            debug = {
                "resolver_method": "yt_dlp",
                "cookies_source": attempted_sources[-1] if attempted_sources else "none",
                "attempted_cookie_sources": attempted_sources,
                **cookie_debug(
                    attempts=browser_attempts_from_source_labels(attempted_sources),
                    selected=browser_from_cookie_source_label(attempted_sources[-1] if attempted_sources else ""),
                    fallback_reason=fallback_reason or last_reason_code,
                ),
                "fallback_method": "",
                "fallback_success": False,
                "reason_code": last_reason_code or BILIBILI_METADATA_NOT_FOUND,
                "is_412": last_reason_code == BILIBILI_412_BLOCKED,
            }
            raise BilibiliResolverError(
                last_reason_code or BILIBILI_METADATA_NOT_FOUND,
                "Bilibili resolver failed",
                last_command,
                last_stderr,
                debug=debug,
            )
        payload = _run_json_command(last_command, runner=runner, timeout=timeout)

    if bilibili_list_id and allow_playlist_expansion:
        return build_bilibili_list_resolution_from_ytdlp(
            source_url,
            payload,
            list_id=bilibili_list_id,
            cookie_header=source_cookie_header(selected_source, cookie_file, cookie_header),
            selected_source_label=cookie_source_label(selected_source),
            attempted_sources=attempted_sources,
            fallback_reason=fallback_reason,
            urlopen_func=urlopen_func,
            timeout=timeout,
        )

    entries = payload.get("entries")
    if allow_playlist_expansion and isinstance(entries, list) and entries:
        videos = [normalize_video_entry(entry, index) for index, entry in enumerate(entries) if isinstance(entry, dict)]
        if is_bilibili_url(source_url):
            videos = enrich_bilibili_multipart_videos(
                videos,
                source_url=source_url,
                cookie_header=source_cookie_header(selected_source, cookie_file, cookie_header),
                urlopen_func=urlopen_func,
                timeout=timeout,
            )
        if not videos:
            raise ResolverError("resolver list payload did not contain usable video entries", last_command)
        for video in videos:
            video.setdefault("resolver_debug", payload.get("_resolver_debug", {}))  # type: ignore[arg-type]
        return {
            "resolution_version": "watchbrief_v5.resolution.v1",
            "source_kind": "list",
            "url": source_url,
            "title": str(payload.get("title") or payload.get("playlist_title") or "Untitled List"),
            "video_count": len(videos),
            "videos": videos,
            "resolver_debug": payload.get("_resolver_debug", {}),
        }
    item = normalize_video_entry({**payload, "url": canonical_entry_url(payload) or source_url}, 0)
    item.setdefault("resolver_debug", payload.get("_resolver_debug", {}))  # type: ignore[arg-type]
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "single",
        "url": canonical_entry_url(payload) or source_url,
        "title": str(payload.get("title") or "Untitled Video"),
        "id": str(payload.get("id") or ""),
        "duration": str(payload.get("duration_string") or payload.get("duration") or ""),
        "channel": str(payload.get("channel") or payload.get("uploader") or ""),
        "videos": [item],
        "resolver_debug": payload.get("_resolver_debug", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a single video URL or list URL without downloading media.")
    parser.add_argument("url")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cookies-from-browser", "--bilibili-cookies-from-browser", dest="cookies_from_browser")
    parser.add_argument("--cookies-file", "--bilibili-cookies-file", dest="cookies_file", type=Path)
    parser.add_argument("--no-browser-auth", action="store_true")
    args = parser.parse_args()
    resolved = resolve_url(
        args.url,
        cookies_from_browser=args.cookies_from_browser,
        cookie_file=args.cookies_file.expanduser() if args.cookies_file else None,
        allow_browser_auth=not args.no_browser_auth,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(drop_internal_download_fields(resolved), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
