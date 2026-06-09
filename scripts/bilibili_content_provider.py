#!/usr/bin/env python3
"""Bilibili subtitle provider for WatchBrief V5.

This provider implements the public Bilibili subtitle API flow directly for
WatchBrief. It does not import or vendor Bilibili Evolved, BilibiliDown,
BBDown, yutto, or browser-extension code.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    from .acquisition_errors import (
        BILIBILI_METADATA_NOT_FOUND,
        BILIBILI_SUBTITLE_API_FAILED,
        BILIBILI_SUBTITLE_DOWNLOAD_FAILED,
        BILIBILI_SUBTITLE_JSON_INVALID,
        BilibiliSubtitleError,
        LOGIN_REQUIRED_FOR_SUBTITLE,
        NO_SUBTITLE_AVAILABLE,
        SubtitleUnavailableError,
    )
    from .audio_downloader import (
        BILIBILI_USER_AGENT,
        cookie_header_from_file,
        is_bilibili_url,
    )
except ImportError:  # pragma: no cover
    from acquisition_errors import (
        BILIBILI_METADATA_NOT_FOUND,
        BILIBILI_SUBTITLE_API_FAILED,
        BILIBILI_SUBTITLE_DOWNLOAD_FAILED,
        BILIBILI_SUBTITLE_JSON_INVALID,
        BilibiliSubtitleError,
        LOGIN_REQUIRED_FOR_SUBTITLE,
        NO_SUBTITLE_AVAILABLE,
        SubtitleUnavailableError,
    )
    from audio_downloader import (
        BILIBILI_USER_AGENT,
        cookie_header_from_file,
        is_bilibili_url,
    )


BILIBILI_LANGUAGE_PRIORITY = ("zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "en", "en-US", "en-GB")
BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{8,})\b")
AVID_RE = re.compile(r"(?:^|/)(?:av|AV)(\d+)(?:/|$)")


@dataclass
class BilibiliPage:
    page: int
    cid: str
    title: str
    duration: int


@dataclass
class BilibiliMetadata:
    source_url: str
    bvid: str
    aid: str
    cid: str
    title: str
    owner: str
    duration: int
    part_title: str
    page_index: int
    pages: list[BilibiliPage]


@dataclass
class BilibiliSubtitleTrack:
    track_id: str
    lan: str
    lan_doc: str
    subtitle_url: str
    subtitle_kind: str
    source_api: str


@dataclass
class BilibiliTranscriptResult:
    subtitle_path: Path
    language: str
    transcript: dict[str, Any]
    material: dict[str, Any]
    command: list[str]
    debug: dict[str, Any]


def extract_bvid(value: str) -> str:
    match = BVID_RE.search(str(value or ""))
    return match.group(1) if match else ""


def extract_aid(value: str) -> str:
    raw = str(value or "").strip()
    if raw.isdigit():
        return raw
    if raw.lower().startswith("av") and raw[2:].isdigit():
        return raw[2:]
    path = urllib.parse.urlparse(raw).path
    match = AVID_RE.search(path)
    return match.group(1) if match else ""


def requested_page_index(url: str) -> int:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(str(url or "")).query)
    for key in ("p", "page"):
        raw = (query.get(key) or [""])[0]
        if str(raw).isdigit() and int(raw) > 0:
            return int(raw)
    return 1


def requested_cid(url: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(str(url or "")).query)
    raw = (query.get("cid") or [""])[0]
    return str(raw or "").strip()


def normalize_subtitle_url(value: str) -> str:
    url = str(value or "").strip()
    if url.startswith("//"):
        return f"https:{url}"
    return url


def seconds_to_timestamp(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def language_for_material(language: str) -> str:
    lowered = str(language or "").lower()
    if lowered.startswith("ai-"):
        lowered = lowered[3:]
    if lowered.startswith("zh") or "中文" in lowered:
        return "zh"
    if lowered.startswith("en") or lowered == "english":
        return "en"
    return "unknown"


def subtitle_kind(item: dict[str, Any]) -> str:
    lan = str(item.get("lan") or "").lower()
    ai_type = int(item.get("ai_type") or 0)
    ai_status = int(item.get("ai_status") or 0)
    return "unknown" if lan.startswith("ai-") or ai_type or ai_status else "manual"


def language_rank(language: str, preferences: tuple[str, ...]) -> int:
    lowered = str(language or "").lower()
    for index, preference in enumerate(preferences):
        if lowered == preference.lower():
            return index
    if lowered.startswith("zh"):
        return len(preferences) + 10
    if lowered.startswith("en"):
        return len(preferences) + 20
    return len(preferences) + 100


def language_preferences(languages: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for language in (*BILIBILI_LANGUAGE_PRIORITY, *languages):
        value = str(language or "").strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return tuple(result)


def request_headers(source_url: str, cookie_header: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": BILIBILI_USER_AGENT,
        "Referer": source_url if str(source_url or "").startswith(("http://", "https://")) else "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def read_browser_cookie_header(browser: str, *, loader: Any = None) -> str:
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
        jar = loader(domain_name="bilibili.com")
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
    *,
    cookies_from_browser: Optional[str] = None,
    cookie_file: Optional[Path] = None,
    cookie_header: Optional[str] = None,
    browser_cookie_loader: Any = None,
) -> str:
    if cookie_header:
        return str(cookie_header).strip()
    if cookie_file:
        return cookie_header_from_file(Path(cookie_file).expanduser())
    if cookies_from_browser:
        return read_browser_cookie_header(str(cookies_from_browser), loader=browser_cookie_loader)
    return ""


def cookies_source_label(
    *,
    cookies_from_browser: Optional[str] = None,
    cookie_file: Optional[Path] = None,
    cookie_header: Optional[str] = None,
) -> str:
    if cookies_from_browser:
        return f"browser:{cookies_from_browser}"
    if cookie_file:
        return "cookies_file"
    if cookie_header:
        return "cookie_header"
    return "none"


def fetch_json(
    url: str,
    *,
    headers: dict[str, str],
    urlopen_func: Any,
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    with urlopen_func(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="ignore"), strict=False)
    if not isinstance(payload, dict):
        raise ValueError("response JSON must be an object")
    return payload


def metadata_api_url(source_url: str) -> str:
    bvid = extract_bvid(source_url)
    aid = extract_aid(source_url)
    if bvid:
        return "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"bvid": bvid})
    if aid:
        return "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"aid": aid})
    raise BilibiliSubtitleError(
        BILIBILI_METADATA_NOT_FOUND,
        "Bilibili URL does not contain a BV or AV id",
        ["bilibili-content-provider", source_url],
    )


def parse_pages(data: dict[str, Any]) -> list[BilibiliPage]:
    pages: list[BilibiliPage] = []
    for index, page in enumerate(data.get("pages") or [], 1):
        if not isinstance(page, dict):
            continue
        cid = str(page.get("cid") or "").strip()
        if not cid:
            continue
        pages.append(BilibiliPage(
            page=int(page.get("page") or index),
            cid=cid,
            title=str(page.get("part") or ""),
            duration=int(page.get("duration") or 0),
        ))
    return pages


def choose_page(data: dict[str, Any], source_url: str) -> BilibiliPage:
    pages = parse_pages(data)
    cid_hint = requested_cid(source_url)
    if cid_hint:
        for page in pages:
            if page.cid == cid_hint:
                return page
    page_index = requested_page_index(source_url)
    for page in pages:
        if page.page == page_index:
            return page
    if pages:
        return pages[0]
    cid = str(data.get("cid") or "").strip()
    if cid:
        return BilibiliPage(page=1, cid=cid, title=str(data.get("title") or ""), duration=int(data.get("duration") or 0))
    raise BilibiliSubtitleError(
        BILIBILI_METADATA_NOT_FOUND,
        "Bilibili metadata does not contain cid/pages",
        ["bilibili-content-provider", source_url],
    )


def parse_metadata_payload(source_url: str, payload: dict[str, Any]) -> BilibiliMetadata:
    if int(payload.get("code") or 0) != 0:
        raise BilibiliSubtitleError(
            BILIBILI_METADATA_NOT_FOUND,
            f"Bilibili metadata API returned code={payload.get('code')}",
            ["bilibili-content-provider", source_url],
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BilibiliSubtitleError(
            BILIBILI_METADATA_NOT_FOUND,
            "Bilibili metadata API returned no data object",
            ["bilibili-content-provider", source_url],
        )
    selected_page = choose_page(data, source_url)
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    return BilibiliMetadata(
        source_url=source_url,
        bvid=str(data.get("bvid") or extract_bvid(source_url)),
        aid=str(data.get("aid") or extract_aid(source_url)),
        cid=selected_page.cid,
        title=str(data.get("title") or ""),
        owner=str(owner.get("name") or ""),
        duration=int(selected_page.duration or data.get("duration") or 0),
        part_title=selected_page.title,
        page_index=int(selected_page.page or 1),
        pages=parse_pages(data),
    )


def fetch_metadata(
    source_url: str,
    *,
    headers: dict[str, str],
    urlopen_func: Any,
    timeout: int,
) -> BilibiliMetadata:
    url = metadata_api_url(source_url)
    try:
        payload = fetch_json(url, headers=headers, urlopen_func=urlopen_func, timeout=timeout)
    except Exception as exc:
        raise BilibiliSubtitleError(
            BILIBILI_METADATA_NOT_FOUND,
            "Bilibili metadata API request failed",
            ["bilibili-content-provider", source_url],
            str(exc),
        ) from exc
    return parse_metadata_payload(source_url, payload)


def subtitle_api_urls(metadata: BilibiliMetadata) -> list[tuple[str, str]]:
    params = {"aid": metadata.aid, "cid": metadata.cid}
    if metadata.bvid:
        params["bvid"] = metadata.bvid
    return [
        ("player-wbi-v2", "https://api.bilibili.com/x/player/wbi/v2?" + urllib.parse.urlencode(params)),
        ("player-v2", "https://api.bilibili.com/x/player/v2?" + urllib.parse.urlencode(params)),
    ]


def map_tracks(subtitles: list[Any], source_api: str) -> list[BilibiliSubtitleTrack]:
    tracks: list[BilibiliSubtitleTrack] = []
    for item in subtitles:
        if not isinstance(item, dict):
            continue
        tracks.append(BilibiliSubtitleTrack(
            track_id=str(item.get("id_str") or item.get("id") or ""),
            lan=str(item.get("lan") or ""),
            lan_doc=str(item.get("lan_doc") or ""),
            subtitle_url=normalize_subtitle_url(str(item.get("subtitle_url") or "")),
            subtitle_kind=subtitle_kind(item),
            source_api=source_api,
        ))
    return tracks


def public_track(track: BilibiliSubtitleTrack) -> dict[str, Any]:
    return {
        "id": track.track_id,
        "language": track.lan,
        "language_label": track.lan_doc,
        "kind": track.subtitle_kind,
        "source_api": track.source_api,
        "has_subtitle_url": bool(track.subtitle_url),
    }


def fetch_subtitle_tracks(
    metadata: BilibiliMetadata,
    *,
    headers: dict[str, str],
    urlopen_func: Any,
    timeout: int,
) -> tuple[list[BilibiliSubtitleTrack], dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    need_login_seen = False
    candidates: list[dict[str, Any]] = []
    for source_api, url in subtitle_api_urls(metadata):
        try:
            payload = fetch_json(url, headers=headers, urlopen_func=urlopen_func, timeout=timeout)
        except Exception as exc:
            failures.append({"source_api": source_api, "reason_code": BILIBILI_SUBTITLE_API_FAILED, "error": clean_text(exc)})
            continue
        if int(payload.get("code") or 0) != 0:
            failures.append({"source_api": source_api, "reason_code": BILIBILI_SUBTITLE_API_FAILED, "api_code": payload.get("code")})
            continue
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        need_login_seen = bool(data.get("need_login_subtitle")) or need_login_seen
        subtitle = data.get("subtitle") if isinstance(data.get("subtitle"), dict) else {}
        tracks = map_tracks(subtitle.get("subtitles") or [], source_api)
        candidates.extend(public_track(track) for track in tracks)
        usable = [track for track in tracks if track.subtitle_url]
        if usable:
            return usable, {
                "subtitle_candidates": candidates,
                "subtitle_candidate_failures": failures,
                "need_login_subtitle": need_login_seen,
                "source_api": source_api,
            }
        if tracks:
            need_login_seen = True

    debug = {
        "subtitle_candidates": candidates,
        "subtitle_candidate_failures": failures,
        "need_login_subtitle": need_login_seen,
        "source_api": "",
    }
    if need_login_seen:
        raise BilibiliSubtitleError(
            LOGIN_REQUIRED_FOR_SUBTITLE,
            "Bilibili subtitles require a valid user login state",
            ["bilibili-content-provider", metadata.source_url],
            debug=debug,
        )
    if failures and not candidates:
        raise BilibiliSubtitleError(
            BILIBILI_SUBTITLE_API_FAILED,
            "Bilibili subtitle APIs failed",
            ["bilibili-content-provider", metadata.source_url],
            debug=debug,
        )
    raise SubtitleUnavailableError(
        "Bilibili video has no available subtitle tracks",
        ["bilibili-content-provider", metadata.source_url],
        reason_code=NO_SUBTITLE_AVAILABLE,
        debug=debug,
    )


def choose_track(tracks: list[BilibiliSubtitleTrack], languages: tuple[str, ...]) -> BilibiliSubtitleTrack:
    preferences = language_preferences(languages)
    return sorted(tracks, key=lambda track: (language_rank(track.lan, preferences), track.track_id or track.subtitle_url))[0]


def parse_bcc_body(payload: dict[str, Any]) -> list[dict[str, str]]:
    body = payload.get("body")
    if not isinstance(body, list):
        raise ValueError("BCC JSON does not contain body list")
    segments: list[dict[str, str]] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        text = clean_text(item.get("content"))
        if not text:
            continue
        try:
            start_seconds = max(0, int(math.floor(float(item.get("from")))))
            end_seconds = max(0, int(math.ceil(float(item.get("to")))))
        except Exception:
            continue
        if end_seconds <= start_seconds:
            end_seconds = start_seconds + 1
        segments.append({
            "start": seconds_to_timestamp(start_seconds),
            "end": seconds_to_timestamp(end_seconds),
            "text": text,
        })
    if not segments:
        raise ValueError("BCC JSON body produced no usable segments")
    return segments


def fetch_bcc_json(
    track: BilibiliSubtitleTrack,
    *,
    headers: dict[str, str],
    urlopen_func: Any,
    timeout: int,
) -> dict[str, Any]:
    try:
        return fetch_json(track.subtitle_url, headers=headers, urlopen_func=urlopen_func, timeout=timeout)
    except Exception as exc:
        raise BilibiliSubtitleError(
            BILIBILI_SUBTITLE_DOWNLOAD_FAILED,
            "Bilibili subtitle file download failed",
            ["bilibili-content-provider", "subtitle_url"],
            str(exc),
        ) from exc


def build_plain_text(segments: list[dict[str, str]]) -> str:
    return "\n".join(segment["text"] for segment in segments)


def build_transcript(
    metadata: BilibiliMetadata,
    track: BilibiliSubtitleTrack,
    segments: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "provider": "bilibili_content_provider",
        "source_platform": "bilibili",
        "bvid": metadata.bvid,
        "aid": metadata.aid,
        "cid": metadata.cid,
        "title": metadata.title,
        "owner": metadata.owner,
        "duration": str(metadata.duration),
        "part_title": metadata.part_title,
        "subtitle_kind": track.subtitle_kind,
        "subtitle_lang": track.lan,
        "subtitle_format": "bcc",
        "segments": segments,
        "plain_text": build_plain_text(segments),
        "source_url": metadata.source_url,
    }


def build_material(transcript: dict[str, Any], track: BilibiliSubtitleTrack, bcc_path: Path) -> dict[str, Any]:
    segments = transcript["segments"]
    quality = "ok" if track.subtitle_kind == "manual" else "degraded"
    return {
        "material_version": "watchbrief_v5.transcript_material.v1",
        "source": {
            "path": str(bcc_path),
            "kind": "subtitle_bcc",
            "provider": "bilibili_content_provider",
            "source_platform": "bilibili",
            "subtitle_kind": track.subtitle_kind,
            "subtitle_language": track.lan,
            "subtitle_format": "bcc",
            "bvid": transcript["bvid"],
            "aid": transcript["aid"],
            "cid": transcript["cid"],
            "title": transcript["title"],
            "owner": transcript["owner"],
            "part_title": transcript["part_title"],
            "source_url": transcript["source_url"],
        },
        "language": language_for_material(track.lan),
        "transcript_quality": quality,
        "segment_count": len(segments),
        "char_count": sum(len(segment["text"]) for segment in segments),
        "has_timestamps": True,
        "warnings": [],
        "sanitization": {
            "raw_segment_count": len(segments),
            "invalid_segment_count": 0,
            "dropped_segment_count": 0,
            "repaired_segment_count": 0,
            "usable_segment_count": len(segments),
        },
        "segments": segments,
        "adapter_boundary": {
            "no_real_url_processing": False,
            "no_subtitle_download": False,
            "no_audio_download": True,
            "no_transcription": True,
            "no_model_call": True,
        },
    }


def provider_debug(
    *,
    metadata: BilibiliMetadata,
    track: Optional[BilibiliSubtitleTrack],
    track_debug: dict[str, Any],
    cookies_source: str,
    fetch_reason: str,
    audio_fallback_allowed: bool,
) -> dict[str, Any]:
    return {
        "provider": "bilibili_content_provider",
        "source_platform": "bilibili",
        "subtitle_probe_attempted": True,
        "subtitle_candidates": track_debug.get("subtitle_candidates") or [],
        "subtitle_candidate_failures": track_debug.get("subtitle_candidate_failures") or [],
        "selected_subtitle_lang": track.lan if track else "",
        "selected_subtitle_kind": track.subtitle_kind if track else "",
        "selected_subtitle_format": "bcc" if track else "",
        "subtitle_fetch_reason": fetch_reason,
        "subtitle_probe_failed": False,
        "audio_downloader_skipped_due_to_subtitle": bool(track),
        "audio_fallback_allowed": audio_fallback_allowed,
        "need_login_subtitle": bool(track_debug.get("need_login_subtitle")),
        "cookies_source": cookies_source,
        "bvid": metadata.bvid,
        "aid": metadata.aid,
        "cid": metadata.cid,
        "source_api": track.source_api if track else track_debug.get("source_api", ""),
    }


def subtitle_error_allows_audio_fallback(reason_code: str) -> bool:
    """Return whether a Bilibili subtitle-layer failure may continue via audio ASR.

    Login-required subtitle metadata means the subtitle API cannot give us text,
    not that the video audio is unavailable. Treat it like a missing subtitle so
    the pipeline can try the already-authorized audio/playurl path and then run
    the normal transcript quality gate on the ASR result.
    """
    return reason_code in {
        LOGIN_REQUIRED_FOR_SUBTITLE,
        NO_SUBTITLE_AVAILABLE,
        BILIBILI_SUBTITLE_API_FAILED,
        BILIBILI_SUBTITLE_DOWNLOAD_FAILED,
    }


def safe_file_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-") or "bilibili"


def fetch_bilibili_subtitle(
    source_url: str,
    output_dir: Path,
    *,
    languages: tuple[str, ...] = BILIBILI_LANGUAGE_PRIORITY,
    cookies_from_browser: Optional[str] = None,
    cookie_file: Optional[Path] = None,
    cookie_header: Optional[str] = None,
    browser_cookie_loader: Any = None,
    urlopen_func: Any = urllib.request.urlopen,
    timeout: int = 120,
) -> BilibiliTranscriptResult:
    if not is_bilibili_url(source_url) and not extract_bvid(source_url) and not extract_aid(source_url):
        raise BilibiliSubtitleError(BILIBILI_METADATA_NOT_FOUND, "not a Bilibili URL or id", ["bilibili-content-provider", source_url])
    output_dir.mkdir(parents=True, exist_ok=True)
    cookies_source = cookies_source_label(cookies_from_browser=cookies_from_browser, cookie_file=cookie_file, cookie_header=cookie_header)
    cookie_value = effective_cookie_header(
        cookies_from_browser=cookies_from_browser,
        cookie_file=cookie_file,
        cookie_header=cookie_header,
        browser_cookie_loader=browser_cookie_loader,
    )
    headers = request_headers(source_url, cookie_value)
    metadata = fetch_metadata(source_url, headers=headers, urlopen_func=urlopen_func, timeout=timeout)
    try:
        tracks, track_debug = fetch_subtitle_tracks(metadata, headers=headers, urlopen_func=urlopen_func, timeout=timeout)
    except SubtitleUnavailableError as exc:
        if isinstance(exc.debug, dict):
            exc.debug.update(provider_debug(
                metadata=metadata,
                track=None,
                track_debug=exc.debug,
                cookies_source=cookies_source,
                fetch_reason=NO_SUBTITLE_AVAILABLE,
                audio_fallback_allowed=True,
            ))
        raise
    except BilibiliSubtitleError as exc:
        if isinstance(exc.debug, dict):
            exc.debug.update(provider_debug(
                metadata=metadata,
                track=None,
                track_debug=exc.debug,
                cookies_source=cookies_source,
                fetch_reason=exc.reason_code,
                audio_fallback_allowed=subtitle_error_allows_audio_fallback(exc.reason_code),
            ))
        raise
    track = choose_track(tracks, languages)
    bcc_payload = fetch_bcc_json(track, headers=headers, urlopen_func=urlopen_func, timeout=timeout)
    try:
        segments = parse_bcc_body(bcc_payload)
    except Exception as exc:
        raise BilibiliSubtitleError(
            BILIBILI_SUBTITLE_JSON_INVALID,
            "Bilibili BCC subtitle JSON is invalid",
            ["bilibili-content-provider", "subtitle_url"],
            str(exc),
            debug=provider_debug(
                metadata=metadata,
                track=track,
                track_debug=track_debug,
                cookies_source=cookies_source,
                fetch_reason=BILIBILI_SUBTITLE_JSON_INVALID,
                audio_fallback_allowed=False,
            ),
        ) from exc
    transcript = build_transcript(metadata, track, segments)
    bcc_path = output_dir / f"{safe_file_part(metadata.bvid)}.{safe_file_part(metadata.cid)}.{safe_file_part(track.lan)}.bcc.json"
    bcc_path.write_text(json.dumps(bcc_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    material = build_material(transcript, track, bcc_path)
    debug = provider_debug(
        metadata=metadata,
        track=track,
        track_debug=track_debug,
        cookies_source=cookies_source,
        fetch_reason="selected_subtitle_available",
        audio_fallback_allowed=False,
    )
    return BilibiliTranscriptResult(
        subtitle_path=bcc_path,
        language=track.lan,
        transcript=transcript,
        material=material,
        command=["bilibili-content-provider", source_url],
        debug=debug,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Bilibili subtitles as WatchBrief transcript material.")
    parser.add_argument("url")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--material-output", required=True, type=Path)
    parser.add_argument("--transcript-output", type=Path)
    parser.add_argument("--cookies-from-browser")
    parser.add_argument("--cookies-file", type=Path)
    args = parser.parse_args()
    result = fetch_bilibili_subtitle(
        args.url,
        args.output_dir,
        cookies_from_browser=args.cookies_from_browser,
        cookie_file=args.cookies_file.expanduser() if args.cookies_file else None,
    )
    args.material_output.parent.mkdir(parents=True, exist_ok=True)
    args.material_output.write_text(json.dumps(result.material, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.transcript_output:
        args.transcript_output.parent.mkdir(parents=True, exist_ok=True)
        args.transcript_output.write_text(json.dumps(result.transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
