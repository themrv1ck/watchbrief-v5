#!/usr/bin/env python3
"""WatchBrief V5 pipeline orchestration.

This module only orchestrates already-built stages. It does not call Codex,
OpenAI, or any cloud model. Review output must be supplied by a mock/manual/local
response provider.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import shutil
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from .acquisition_errors import (
        AcquisitionError,
        AudioDownloadBlockedError,
        BilibiliSubtitleError,
        SubtitleUnavailableError,
    )
    from .analyzer.codex_review import CODEX_REVIEW_PROMPT_VERSION, build_review_request, parse_review_response
    from .analyzer.local_extract import build_local_extract_payload, configured_qwen_api_base, configured_qwen_model
    from .analyzer.local_review import LOCAL_REVIEW_MODEL_ID, LOCAL_REVIEW_PROMPT_FINGERPRINT
    from .analyzer.prompts import WATCHBRIEF_VERSION
    from .audio_downloader import download_standard_audio
    from .providers.youtube_connect_provider import (
        extract_youtube_video_id,
        fetch_youtube_connect_transcript,
        material_from_youtube_connect,
    )
    from .report_targets import DEFAULT_REPORT_TARGET, normalize_report_target
    from .renderer import render_single_video_html
    from .report_cache import build_report_cache_identity, read_cached_report, report_cache_dir, write_cached_report
    from .resolver import refresh_xiaohongshu_media_url_from_browser, resolve_url
    from .subtitle_fetcher import fetch_platform_subtitles
    from .transcriber import transcribe_audio_to_material
    from .transcript_source_adapter import transcript_material_to_local_extract_input
    from .transcript_quality import MIN_TRANSCRIPT_COVERAGE_RATIO, validate_transcript_coverage
    from .watch_order import write_watch_order
    from .scoring import SCORING_FORMULA_VERSION
except ImportError:  # pragma: no cover - direct script execution
    from acquisition_errors import AcquisitionError, AudioDownloadBlockedError, BilibiliSubtitleError, SubtitleUnavailableError
    from analyzer.codex_review import CODEX_REVIEW_PROMPT_VERSION, build_review_request, parse_review_response
    from analyzer.local_extract import build_local_extract_payload, configured_qwen_api_base, configured_qwen_model
    from analyzer.local_review import LOCAL_REVIEW_MODEL_ID, LOCAL_REVIEW_PROMPT_FINGERPRINT
    from analyzer.prompts import WATCHBRIEF_VERSION
    from audio_downloader import download_standard_audio
    from providers.youtube_connect_provider import (
        extract_youtube_video_id,
        fetch_youtube_connect_transcript,
        material_from_youtube_connect,
    )
    from report_targets import DEFAULT_REPORT_TARGET, normalize_report_target
    from renderer import render_single_video_html
    from report_cache import build_report_cache_identity, read_cached_report, report_cache_dir, write_cached_report
    from resolver import refresh_xiaohongshu_media_url_from_browser, resolve_url
    from subtitle_fetcher import fetch_platform_subtitles
    from transcriber import transcribe_audio_to_material
    from transcript_source_adapter import transcript_material_to_local_extract_input
    from transcript_quality import MIN_TRANSCRIPT_COVERAGE_RATIO, validate_transcript_coverage
    from watch_order import write_watch_order
    from scoring import SCORING_FORMULA_VERSION


ReviewResponseProvider = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
MISSING_TEXT_VALUES = {"", "未知", "unknown", "none", "null", "n/a", "na"}
PUBLISH_DATE_FIELDS = ("publish_date", "release_date", "upload_date", "timestamp", "pubdate", "published_at", "date")
DURATION_FIELDS = ("duration", "duration_seconds", "duration_sec", "duration_string", "length")
SUBTITLE_TIMELINE_AUDIO_FALLBACK_REASON = "subtitle_timeline_exceeds_video_duration"
PLATFORM_SUBTITLE_AUDIO_FALLBACK_SOURCES = {"subtitle_bcc", "subtitle_srt", "subtitle_vtt"}
SUBTITLE_QUALITY_AUDIO_FALLBACK_REASONS = {
    SUBTITLE_TIMELINE_AUDIO_FALLBACK_REASON,
    "coverage_below_threshold",
    "segment_count_too_low",
    "plain_text_char_count_too_low",
    "video_duration_missing_for_quality_gate",
}


def audio_source_url_type(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.netloc.endswith("xiaohongshu.com") and "/explore/" in parsed.path:
        query = urllib.parse.parse_qs(parsed.query)
        return "note_url_with_xsec" if "xsec_token" in query else "note_url"
    if parsed.scheme and parsed.netloc:
        return "media_or_external_url_with_query" if parsed.query else "media_or_external_url"
    return "none"


def audio_source_for_item(item: dict[str, Any], metadata: dict[str, str]) -> tuple[str, dict[str, Any]]:
    media_url = str(item.get("_media_url") or item.get("media_url") or item.get("video_url") or "").strip()
    download_url = str(item.get("_download_url") or item.get("download_url") or "").strip()
    if media_url:
        url = media_url
        source = "resolver_media_url"
    elif download_url:
        url = download_url
        source = "resolver_download_url"
    else:
        url = str(metadata.get("url") or item.get("url") or "").strip()
        source = "item_url"
    return url, {
        "source_url_source": source,
        "source_url_type": audio_source_url_type(url),
        "media_url_available": bool(media_url),
        "download_url_available": bool(download_url),
    }


def is_xiaohongshu_note_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(str(url or ""))
    return parsed.netloc.endswith("xiaohongshu.com") and "/explore/" in parsed.path


def safe_media_refresh_debug(refresh_result: dict[str, Any]) -> dict[str, Any]:
    media_url = str(refresh_result.get("media_url") or "")
    debug: dict[str, Any] = {
        "media_url_available": bool(media_url),
        "media_url_type": audio_source_url_type(media_url),
    }
    for key in ("provider", "browser", "resource_count", "video_element_count", "reason_code", "error"):
        value = refresh_result.get(key)
        if value not in (None, ""):
            debug[key] = value
    return debug


class PipelineError(RuntimeError):
    def __init__(self, stage: str, reason_code: str, message: str) -> None:
        self.stage = stage
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"{stage}:{reason_code}: {message}")


class ReviewResponseUnavailableError(PipelineError):
    def __init__(self) -> None:
        super().__init__("codex_review", "review_response_unavailable", "mock/manual review response provider is required")


class TranscriptCoverageTooLowError(PipelineError):
    def __init__(self, debug: dict[str, Any]) -> None:
        self.debug = copy.deepcopy(debug)
        super().__init__("transcript_quality", "transcript_coverage_too_low", "transcript coverage is too low for reliable analysis")


def subtitle_quality_should_fallback_to_audio(debug: dict[str, Any]) -> bool:
    transcript_source = str(debug.get("transcript_source") or "")
    reason = str(debug.get("transcript_quality_reason") or "")
    return transcript_source in PLATFORM_SUBTITLE_AUDIO_FALLBACK_SOURCES and reason in SUBTITLE_QUALITY_AUDIO_FALLBACK_REASONS


def subtitle_quality_fallback_reason_code(reason: str) -> str:
    if reason == SUBTITLE_TIMELINE_AUDIO_FALLBACK_REASON:
        return "subtitle_timeline_mismatch"
    return "subtitle_quality_insufficient"


def subtitle_quality_fallback_error(reason: str) -> str:
    if reason == SUBTITLE_TIMELINE_AUDIO_FALLBACK_REASON:
        return "subtitle timeline exceeds video duration; retrying with audio transcription"
    return "platform subtitle quality is insufficient; retrying with audio transcription"


@dataclass
class PipelineDependencies:
    resolve_url_func: Callable[..., dict[str, Any]] = resolve_url
    fetch_subtitles_func: Callable[..., Any] = fetch_platform_subtitles
    download_audio_func: Callable[..., Any] = download_standard_audio
    transcribe_func: Callable[..., Any] = transcribe_audio_to_material
    youtube_connect_fallback_func: Optional[Callable[[str], Any]] = fetch_youtube_connect_transcript
    build_local_extract_func: Callable[..., dict[str, Any]] = build_local_extract_payload
    build_review_request_func: Callable[..., dict[str, Any]] = build_review_request
    parse_review_response_func: Callable[[Any], dict[str, Any]] = parse_review_response
    render_func: Callable[[dict[str, Any]], str] = render_single_video_html
    cleanup_func: Callable[[Path], None] = lambda path: shutil.rmtree(path, ignore_errors=True)
    write_json_func: Callable[[Path, dict[str, Any]], None] = lambda path, data: write_json(path, data)
    write_html_func: Callable[[Path, str], None] = lambda path, html: write_text(path, html)
    write_watch_order_func: Callable[[dict[str, Any], Path], Path] = write_watch_order
    resolver_options: dict[str, Any] = field(default_factory=dict)
    subtitle_options: dict[str, Any] = field(default_factory=dict)
    audio_download_options: dict[str, Any] = field(default_factory=dict)
    media_url_refresh_func: Optional[Callable[..., dict[str, Any]]] = refresh_xiaohongshu_media_url_from_browser
    transcriber_options: dict[str, Any] = field(default_factory=dict)
    local_extract_options: dict[str, Any] = field(default_factory=dict)
    review_options: dict[str, Any] = field(default_factory=dict)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_slug(value: Any, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80] or fallback


def clean_metadata_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def is_missing_metadata_value(value: Any) -> bool:
    return clean_metadata_value(value).lower() in MISSING_TEXT_VALUES


def date_from_ymd(year: int, month: int, day: int) -> str | None:
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_publish_date_value(value: Any, *, field_name: str) -> str | None:
    if is_missing_metadata_value(value):
        return None
    if isinstance(value, (int, float)) and field_name in {"timestamp", "pubdate", "published_at"}:
        try:
            return dt.datetime.fromtimestamp(float(value)).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    text = clean_metadata_value(value)
    if re.fullmatch(r"\d{8}", text):
        return date_from_ymd(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    if re.fullmatch(r"\d{9,12}(?:\.\d+)?", text) and field_name in {"timestamp", "pubdate", "published_at"}:
        try:
            return dt.datetime.fromtimestamp(float(text)).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    match = re.search(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)", text)
    if match:
        return date_from_ymd(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def normalized_publish_date(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    for field_name in PUBLISH_DATE_FIELDS:
        if field_name not in item:
            continue
        parsed = parse_publish_date_value(item.get(field_name), field_name=field_name)
        if parsed:
            return parsed, {
                "publish_date_missing": False,
                "publish_date_source": field_name,
            }
    return "未知", {
        "publish_date_missing": True,
        "publish_date_source": None,
    }


def parse_duration_seconds(value: Any) -> int | None:
    if is_missing_metadata_value(value):
        return None
    if isinstance(value, (int, float)):
        seconds = int(round(float(value)))
        return seconds if seconds >= 0 else None

    text = clean_metadata_value(value)
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        seconds = int(round(float(text)))
        return seconds if seconds >= 0 else None
    if re.fullmatch(r"\d{1,3}:\d{2}(?::\d{2})?", text):
        parts = [int(part) for part in text.split(":")]
        if len(parts) == 2:
            minutes, seconds = parts
            return minutes * 60 + seconds
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    return None


def format_duration_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}秒"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{remainder:02d}秒"
    hours, minute_remainder = divmod(minutes, 60)
    return f"{hours}小时{minute_remainder:02d}分{remainder:02d}秒"


def normalized_duration(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    for field_name in DURATION_FIELDS:
        if field_name not in item:
            continue
        seconds = parse_duration_seconds(item.get(field_name))
        if seconds is not None:
            return format_duration_seconds(seconds), {
                "duration_missing": False,
                "duration_source": field_name,
            }
    return "未知", {
        "duration_missing": True,
        "duration_source": None,
    }


def metadata_from_item_with_debug(item: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    publish_date, publish_date_debug = normalized_publish_date(item)
    duration, duration_debug = normalized_duration(item)
    return {
        "title": str(item.get("title") or "Untitled Video"),
        "url": str(item.get("url") or ""),
        "channel": str(item.get("channel") or "未知"),
        "duration": duration,
        "date": publish_date,
    }, {
        **publish_date_debug,
        **duration_debug,
    }


def metadata_from_item(item: dict[str, Any]) -> dict[str, str]:
    metadata, _debug = metadata_from_item_with_debug(item)
    return metadata


def apply_metadata_display_fields(payload: dict[str, Any], metadata: dict[str, str]) -> dict[str, Any]:
    normalized = copy.deepcopy(payload)
    normalized["date"] = metadata["date"]
    normalized["duration"] = metadata["duration"]
    return normalized


def material_from_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict) and result.get("material_version") == "watchbrief_v5.transcript_material.v1":
        return result
    if isinstance(result, dict) and isinstance(result.get("material"), dict):
        return result["material"]
    material = getattr(result, "material", None)
    if isinstance(material, dict):
        return material
    raise PipelineError("transcript_material", "format_conversion_failed", "stage did not return transcript material")


def audio_path_from_result(result: Any) -> Path:
    if isinstance(result, dict) and result.get("audio_path"):
        return Path(result["audio_path"])
    audio_path = getattr(result, "audio_path", None)
    if audio_path:
        return Path(audio_path)
    raise PipelineError("audio_downloader", "audio_download_failed", "audio downloader did not return audio_path")


def audio_method_from_result(result: Any) -> str:
    if isinstance(result, dict) and result.get("method"):
        return str(result["method"])
    method = getattr(result, "method", None)
    return str(method or "yt_dlp")


def audio_method_from_error(exc: BaseException) -> str:
    command = getattr(exc, "command", None)
    if isinstance(command, list) and command and command[0] == "bilibili-playinfo-fallback":
        return "bilibili_playinfo_fallback"
    if isinstance(command, list) and command and command[0] == "bilibili-playurl-api":
        return "bilibili_playurl_api"
    return "yt_dlp"


def audio_debug_from_result(result: Any) -> dict[str, Any]:
    debug: dict[str, Any] = {"method": audio_method_from_result(result)}
    for key in (
        "cookies_source",
        "audio_url_source",
        "fallback_success",
        "playurl_status",
        "cookies_browser_attempts",
        "selected_cookies_browser",
        "cookies_fallback_reason",
    ):
        if isinstance(result, dict):
            value = result.get(key)
        else:
            value = getattr(result, key, None)
        if value not in (None, ""):
            debug[key] = value
    return debug


def audio_debug_from_error(exc: BaseException) -> dict[str, Any]:
    debug = getattr(exc, "debug", None)
    if isinstance(debug, dict) and debug:
        merged = copy.deepcopy(debug)
        merged.setdefault("method", audio_method_from_error(exc))
        return merged
    return {"method": audio_method_from_error(exc)}


def subtitle_debug_from_result(result: Any) -> dict[str, Any]:
    debug = result.get("debug") if isinstance(result, dict) else getattr(result, "debug", None)
    if isinstance(debug, dict) and debug:
        return copy.deepcopy(debug)
    material = material_from_result(result)
    source = material.get("source", {}) if isinstance(material, dict) else {}
    language = str(material.get("language") or source.get("subtitle_language") or "") if isinstance(material, dict) else ""
    subtitle_format = str(source.get("subtitle_format") or source.get("kind") or "") if isinstance(source, dict) else ""
    return {
        "subtitle_probe_attempted": False,
        "subtitle_candidates": [],
        "subtitle_candidate_failures": [],
        "selected_subtitle_lang": language,
        "selected_subtitle_kind": str(source.get("subtitle_kind") or "") if isinstance(source, dict) else "",
        "selected_subtitle_format": subtitle_format.replace("subtitle_", ""),
        "subtitle_fetch_reason": "subtitle_available",
        "audio_downloader_skipped_due_to_subtitle": True,
    }


def subtitle_debug_from_error(exc: BaseException) -> dict[str, Any]:
    debug = getattr(exc, "debug", None)
    if isinstance(debug, dict) and debug:
        return copy.deepcopy(debug)
    return {
        "subtitle_probe_attempted": False,
        "subtitle_candidates": [],
        "subtitle_candidate_failures": [],
        "subtitle_fetch_reason": "subtitle_unavailable",
        "audio_downloader_skipped_due_to_subtitle": False,
    }


def local_extract_start_debug(options: dict[str, Any]) -> dict[str, Any]:
    external_provider = str(options.get("external_extract_provider") or "").strip()
    if external_provider:
        external_model = str(options.get("external_model_id") or "").strip()
        external_api_base = str(options.get("external_api_base") or "").strip()
        model_id = f"{external_provider}:{external_model}".rstrip(":")
        return {
            "qwen_model": model_id,
            "qwen_base_url": external_api_base,
            "qwen_api_base": external_api_base,
            "extract_provider": external_provider,
            "external_model_id": external_model,
            "external_api_base": external_api_base,
            "timeout": int(options.get("qwen_timeout") or 120),
        }
    qwen_base_url = configured_qwen_api_base(options.get("qwen_api_base"))
    return {
        "qwen_model": configured_qwen_model(options.get("qwen_model")) or "auto",
        "qwen_base_url": qwen_base_url,
        "qwen_api_base": qwen_base_url,
        "timeout": int(options.get("qwen_timeout") or 120),
    }


def report_target_from_options(options: dict[str, Any]) -> str:
    return normalize_report_target(options.get("report_target") or DEFAULT_REPORT_TARGET)


def transcript_hash_from_segments(segments: list[dict[str, Any]]) -> str:
    normalized = json.dumps(segments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_review_response_with_metadata(
    parse_func: Callable[..., dict[str, Any]],
    raw_response: Any,
    stability_metadata: dict[str, Any],
    *,
    report_target: str,
) -> dict[str, Any]:
    try:
        return parse_func(raw_response, stability_metadata=stability_metadata, report_target=report_target)
    except TypeError as exc:
        if "stability_metadata" not in str(exc) and "report_target" not in str(exc):
            raise
        try:
            return parse_func(raw_response, stability_metadata=stability_metadata)
        except TypeError as second_exc:
            if "stability_metadata" not in str(second_exc):
                raise
            return parse_func(raw_response)


def error_record(item: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, AcquisitionError):
        stage = exc.stage
        reason_code = exc.reason_code
        message = exc.message
    elif isinstance(exc, PipelineError):
        stage = exc.stage
        reason_code = exc.reason_code
        message = exc.message
    elif hasattr(exc, "stage") and hasattr(exc, "reason_code"):
        stage = str(getattr(exc, "stage"))
        reason_code = str(getattr(exc, "reason_code"))
        message = str(getattr(exc, "message", str(exc)))
    else:
        stage = "pipeline"
        reason_code = "pipeline_failed"
        message = str(exc)
    record = {
        "title": str(item.get("title") or "Untitled Video"),
        "url": str(item.get("url") or ""),
        "stage": stage,
        "reason_code": reason_code,
        "error": message,
    }
    debug = getattr(exc, "debug", None)
    if isinstance(debug, dict) and debug:
        record["debug"] = copy.deepcopy(debug)
    return record


def is_youtube_resolver_fallback_trigger(source_url: str, exc: BaseException) -> bool:
    if not extract_youtube_video_id(source_url):
        return False
    stage = str(getattr(exc, "stage", ""))
    if stage and stage != "resolver":
        return False
    reason_code = str(getattr(exc, "reason_code", ""))
    text = " ".join(
        str(value or "")
        for value in (
            reason_code,
            getattr(exc, "message", ""),
            getattr(exc, "stderr", ""),
            str(exc),
        )
    ).lower()
    markers = (
        "platform_restriction",
        "sign in to confirm",
        "not a bot",
        "bot check",
        "login verification",
        "login required",
        "sign in",
    )
    return any(marker in text for marker in markers)


def youtube_connect_fallback_debug(
    *,
    provider: str,
    success: bool,
    reason_code: str,
    error: str = "",
) -> dict[str, Any]:
    debug = {
        "resolver_failed": True,
        "resolver_reason_code": reason_code or "platform_restriction",
        "transcript_fallback_provider": provider,
        "transcript_fallback_success": success,
    }
    if error:
        debug["transcript_fallback_error"] = error
    return debug


def resolve_with_youtube_connect_fallback(
    source_url: str,
    exc: BaseException,
    deps: PipelineDependencies,
) -> Optional[dict[str, Any]]:
    if not is_youtube_resolver_fallback_trigger(source_url, exc):
        return None
    if deps.youtube_connect_fallback_func is None:
        return None
    video_id = extract_youtube_video_id(source_url)
    reason_code = str(getattr(exc, "reason_code", "") or "platform_restriction")
    transcript = deps.youtube_connect_fallback_func(source_url)
    material = material_from_youtube_connect(transcript)
    title = str(getattr(transcript, "title", "") or video_id)
    duration = str(getattr(transcript, "duration", "") or "unknown")
    fallback_debug = youtube_connect_fallback_debug(
        provider=str(getattr(transcript, "provider", "") or "youtube-connect"),
        success=True,
        reason_code=reason_code,
    )
    item = {
        "title": title,
        "url": source_url,
        "id": video_id,
        "duration": duration,
        "channel": "未知",
        "date": "未知",
        "resolver_debug": {
            "resolver_method": "yt_dlp",
            "fallback_method": "youtube_connect_transcript",
            **fallback_debug,
        },
        "transcript_fallback": {
            **fallback_debug,
            "source_platform": "youtube",
            "video_id": video_id,
            "transcript_source": material.get("source", {}).get("kind", "youtube_connect"),
            "subtitle_lang": getattr(transcript, "subtitle_lang", "en"),
            "subtitle_format": getattr(transcript, "subtitle_format", "transcript"),
            "segment_count": material.get("segment_count"),
            "char_count": material.get("char_count"),
        },
        "_transcript_material": material,
    }
    return {
        "resolution_version": "watchbrief_v5.resolution.v1",
        "source_kind": "single",
        "url": source_url,
        "title": title,
        "id": video_id,
        "duration": duration,
        "channel": "未知",
        "videos": [item],
        "resolver_debug": item["resolver_debug"],
    }


def initial_manifest(source_url: str, source_kind: str, total_count: int) -> dict[str, Any]:
    return {
        "manifest_version": "watchbrief_v5.pipeline_manifest.v1",
        "source_url": source_url,
        "source_kind": source_kind,
        "total_count": total_count,
        "completed_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "non_video_count": 0,
        "items": [],
    }


def update_manifest(manifest: dict[str, Any], manifest_path: Path, deps: PipelineDependencies) -> None:
    manifest["completed_count"] = sum(1 for item in manifest["items"] if item.get("status") == "completed")
    manifest["failed_count"] = sum(1 for item in manifest["items"] if item.get("status") == "failed")
    manifest["skipped_count"] = sum(1 for item in manifest["items"] if item.get("status") == "skipped")
    manifest["non_video_count"] = sum(
        1
        for item in manifest["items"]
        if item.get("status") == "skipped" and item.get("reason_code") == "non_video_note"
    )
    deps.write_json_func(manifest_path, manifest)


def should_skip_video_item(item: dict[str, Any]) -> bool:
    return bool(item.get("skip_reason_code"))


def process_skipped_item(
    item: dict[str, Any],
    *,
    artifact_root: Optional[Path] = None,
    index: int,
    deps: PipelineDependencies,
) -> dict[str, Any]:
    report_target = report_target_from_options(deps.review_options)
    title = str(item.get("title") or "Untitled Video")
    slug = f"{index:02d}-{safe_slug(title, 'note')}"
    artifact_dir = (artifact_root or Path.cwd() / "payloads") / slug
    item_manifest_path = artifact_dir / "item_manifest.json"
    reason_code = str(item.get("skip_reason_code") or "skipped")
    stage = str(item.get("skip_stage") or "resolver")
    message = "non-video note skipped before video pipeline" if reason_code == "non_video_note" else "item skipped before video pipeline"
    item_manifest = {
        "item_manifest_version": "watchbrief_v5.pipeline_item.v1",
        "status": "skipped",
        "analysis_mode": str(deps.review_options.get("analysis_mode") or "standard"),
        "report_target": report_target,
        "index": index,
        "title": title,
        "url": str(item.get("url") or ""),
        "reason_code": reason_code,
        "skip_stage": stage,
        "note_id": str(item.get("note_id") or item.get("id") or ""),
        "note_type": str(item.get("note_type") or ""),
        "note_media_kind": str(item.get("note_media_kind") or ""),
        "is_video_note": bool(item.get("is_video_note")),
        "is_image_text_note": bool(item.get("is_image_text_note")),
        "steps": [
            {
                "step": "resolver",
                "status": "skipped",
                "reason_code": reason_code,
                "error": message,
                "audio_downloader_skipped": True,
                "transcriber_skipped": True,
                "local_extract_skipped": True,
                "codex_review_skipped": True,
                "renderer_skipped": True,
            }
        ],
        "resolver_debug": item.get("resolver_debug", {}),
    }
    deps.write_json_func(item_manifest_path, item_manifest)
    return {
        "status": "skipped",
        "index": index,
        "report_target": report_target,
        "title": title,
        "url": str(item.get("url") or ""),
        "reason_code": reason_code,
        "stage": stage,
        "error": message,
        "item_manifest_path": str(item_manifest_path),
        "note_id": str(item.get("note_id") or item.get("id") or ""),
        "note_type": str(item.get("note_type") or ""),
        "note_media_kind": str(item.get("note_media_kind") or ""),
        "resolver_debug": item.get("resolver_debug", {}),
    }


def process_source(
    source_url: str,
    output_dir: Path,
    *,
    review_response_provider: Optional[ReviewResponseProvider],
    deps: Optional[PipelineDependencies] = None,
    work_dir: Optional[Path] = None,
    debug_dir: Optional[Path] = None,
) -> dict[str, Any]:
    dependencies = deps or PipelineDependencies()
    report_target = report_target_from_options(dependencies.review_options)
    output_dir.mkdir(parents=True, exist_ok=True)
    debug_root = debug_dir or output_dir
    debug_root.mkdir(parents=True, exist_ok=True)
    work_root = work_dir or (debug_root / "_work")

    try:
        if dependencies.resolver_options:
            resolved = dependencies.resolve_url_func(source_url, **dependencies.resolver_options)
        else:
            resolved = dependencies.resolve_url_func(source_url)
    except BaseException as exc:
        fallback_error: BaseException | None = None
        try:
            fallback_resolution = resolve_with_youtube_connect_fallback(source_url, exc, dependencies)
        except BaseException as fallback_exc:
            fallback_resolution = None
            fallback_error = fallback_exc
        if fallback_resolution is not None:
            resolved = fallback_resolution
        else:
            manifest = initial_manifest(source_url, "unknown", 1)
            manifest["report_target"] = report_target
            if hasattr(exc, "debug"):
                manifest["resolver_debug"] = getattr(exc, "debug")
            if fallback_error is not None and is_youtube_resolver_fallback_trigger(source_url, exc):
                manifest["transcript_fallback_debug"] = youtube_connect_fallback_debug(
                    provider="youtube-connect",
                    success=False,
                    reason_code=str(getattr(exc, "reason_code", "") or "platform_restriction"),
                    error=str(fallback_error),
                )
            manifest_path = debug_root / "manifest.json"
            record = error_record({"title": "Untitled Video", "url": source_url}, exc)
            failed_item = {
                "status": "failed",
                "index": 1,
                "report_target": report_target,
                "title": "Untitled Video",
                "url": source_url,
                "error": record,
            }
            if hasattr(exc, "debug"):
                failed_item["resolver_debug"] = getattr(exc, "debug")
            if fallback_error is not None and is_youtube_resolver_fallback_trigger(source_url, exc):
                failed_item["transcript_fallback_debug"] = manifest["transcript_fallback_debug"]
            manifest["items"].append(failed_item)
            update_manifest(manifest, manifest_path, dependencies)
            return manifest
    source_kind = str(resolved.get("source_kind") or "single")
    videos = resolved.get("videos")
    if not isinstance(videos, list) or not videos:
        videos = [resolved]

    manifest = initial_manifest(source_url, source_kind, len(videos))
    analysis_mode = str(dependencies.review_options.get("analysis_mode") or "standard")
    manifest["analysis_mode"] = analysis_mode
    manifest["report_target"] = report_target
    if resolved.get("resolver_debug"):
        manifest["resolver_debug"] = resolved["resolver_debug"]
    if source_kind == "list" and resolved.get("title"):
        manifest["playlist_title"] = str(resolved.get("title"))
    for key in ("source_subkind", "source_platform", "note_count", "video_note_count", "non_video_count", "board_id"):
        if resolved.get(key) not in (None, ""):
            manifest[key] = resolved[key]
    if debug_root != output_dir:
        manifest["debug_dir"] = str(debug_root)
        manifest["output_dir"] = str(output_dir)
    manifest_path = debug_root / "manifest.json"
    update_manifest(manifest, manifest_path, dependencies)

    for index, item in enumerate(videos, start=1):
        if should_skip_video_item(item):
            item_result = process_skipped_item(
                item,
                artifact_root=debug_root / "payloads",
                index=index,
                deps=dependencies,
            )
        else:
            item_result = process_video_item(
                item,
                output_dir,
                work_root=work_root,
                artifact_root=debug_root / "payloads",
                index=index,
                review_response_provider=review_response_provider,
                deps=dependencies,
            )
        manifest["items"].append(item_result)
        update_manifest(manifest, manifest_path, dependencies)

    if source_kind == "list":
        try:
            watch_order_path = dependencies.write_watch_order_func(manifest, output_dir)
            manifest["watch_order_path"] = str(watch_order_path)
            update_manifest(manifest, manifest_path, dependencies)
        except Exception as exc:
            raise PipelineError("watch_order", "watch_order_failed", str(exc)) from exc

    return manifest


def process_video_item(
    item: dict[str, Any],
    output_dir: Path,
    *,
    work_root: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    index: int,
    review_response_provider: Optional[ReviewResponseProvider],
    deps: PipelineDependencies,
) -> dict[str, Any]:
    report_target = report_target_from_options(deps.review_options)
    slug = f"{index:02d}-{safe_slug(item.get('title'), 'video')}"
    work_dir = (work_root or (output_dir / "_work")) / slug
    subtitle_dir = work_dir / "subtitles"
    audio_dir = work_dir / "audio"
    transcript_dir = work_dir / "transcript"
    artifact_dir = (artifact_root or (output_dir / "payloads")) / slug
    html_path = output_dir / f"{slug}.html"
    payload_path = artifact_dir / "normalized_payload.json"
    item_manifest_path = artifact_dir / "item_manifest.json"
    review_request_path = artifact_dir / "review_request.json"
    local_extract_path = artifact_dir / "local_extract.json"

    item_manifest: dict[str, Any] = {
        "item_manifest_version": "watchbrief_v5.pipeline_item.v1",
        "status": "running",
        "analysis_mode": str(deps.review_options.get("analysis_mode") or "standard"),
        "report_target": report_target,
        "index": index,
        "title": str(item.get("title") or "Untitled Video"),
        "url": str(item.get("url") or ""),
        "work_dir": str(work_dir),
        "html_path": str(html_path),
        "payload_path": str(payload_path),
        "steps": [],
    }
    if isinstance(item.get("transcript_fallback"), dict):
        fallback_info = copy.deepcopy(item["transcript_fallback"])
        for key in (
            "resolver_failed",
            "resolver_reason_code",
            "transcript_fallback_provider",
            "transcript_fallback_success",
        ):
            if key in fallback_info:
                item_manifest[key] = fallback_info[key]

    def step(name: str, status: str, extra: Optional[dict[str, Any]] = None) -> None:
        row = {"step": name, "status": status}
        if extra:
            row.update(extra)
        item_manifest["steps"].append(row)
        deps.write_json_func(item_manifest_path, item_manifest)

    def download_and_transcribe_audio(
        *,
        subtitle_available: bool,
        rejection_debug: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        audio_kwargs = {
            "subtitle_checked": True,
            "subtitle_available": subtitle_available,
        }
        if rejection_debug:
            audio_kwargs["subtitle_available"] = False
        for key in ("bvid", "cid", "playinfo"):
            if item.get(key):
                audio_kwargs[key] = item[key]
        audio_kwargs.update(deps.audio_download_options)
        audio_source_url, audio_source_debug = audio_source_for_item(item, metadata)
        try:
            audio_result = deps.download_audio_func(
                audio_source_url,
                audio_dir,
                **audio_kwargs,
            )
        except BaseException as exc:
            refresh_result: dict[str, Any] = {}
            refreshed_media_url = ""
            if (
                isinstance(exc, AcquisitionError)
                and exc.stage == "audio_downloader"
                and not audio_source_debug.get("media_url_available")
                and is_xiaohongshu_note_url(audio_source_url)
                and deps.media_url_refresh_func is not None
            ):
                try:
                    refresh_result = deps.media_url_refresh_func(
                        audio_source_url,
                        browser=str(audio_source_debug.get("selected_cookies_browser") or item.get("selected_cookies_browser") or "safari"),
                    )
                except Exception as refresh_exc:
                    refresh_result = {
                        "media_url": "",
                        "media_url_available": False,
                        "reason_code": "media_url_refresh_failed",
                        "error": str(refresh_exc),
                    }
                refresh_debug = safe_media_refresh_debug(refresh_result)
                step(
                    "media_url_refresh",
                    "completed" if refresh_debug.get("media_url_available") else "unavailable",
                    refresh_debug,
                )
                refreshed_media_url = str(refresh_result.get("media_url") or "").strip()
            if refreshed_media_url:
                item["_media_url"] = refreshed_media_url
                retry_source_debug = {
                    "source_url_source": "refreshed_media_url",
                    "source_url_type": audio_source_url_type(refreshed_media_url),
                    "media_url_available": True,
                    "download_url_available": bool(item.get("_download_url") or item.get("download_url")),
                    "media_url_refreshed": True,
                }
                try:
                    audio_result = deps.download_audio_func(
                        refreshed_media_url,
                        audio_dir,
                        **audio_kwargs,
                    )
                    audio_source_debug = retry_source_debug
                except BaseException as retry_exc:
                    if isinstance(retry_exc, AcquisitionError) and retry_exc.stage == "audio_downloader":
                        existing_debug = getattr(retry_exc, "debug", None)
                        merged_debug = copy.deepcopy(existing_debug) if isinstance(existing_debug, dict) else {}
                        merged_debug.update(retry_source_debug)
                        setattr(retry_exc, "debug", merged_debug)
                    raise
            else:
                if isinstance(exc, AcquisitionError) and exc.stage == "audio_downloader":
                    existing_debug = getattr(exc, "debug", None)
                    merged_debug = copy.deepcopy(existing_debug) if isinstance(existing_debug, dict) else {}
                    merged_debug.update(audio_source_debug)
                    if refresh_result:
                        merged_debug.update(safe_media_refresh_debug(refresh_result))
                    setattr(exc, "debug", merged_debug)
                raise
        audio_path = audio_path_from_result(audio_result)
        audio_step = audio_debug_from_result(audio_result)
        audio_step.update(audio_source_debug)
        audio_step["audio_path"] = str(audio_path)
        audio_step["audio_downloader_skipped_due_to_subtitle"] = False
        if rejection_debug:
            audio_step.update({
                "subtitle_rejected_due_to_quality": True,
                "subtitle_rejection_reason": rejection_debug.get("transcript_quality_reason", ""),
                "fallback_from_transcript_source": rejection_debug.get("transcript_source", ""),
            })
        step("audio_downloader", "completed", audio_step)
        transcriber_kwargs = {"language": ""}
        transcriber_kwargs.update(deps.transcriber_options)
        transcribe_result = deps.transcribe_func(audio_path, transcript_dir, **transcriber_kwargs)
        transcribed_material = material_from_result(transcribe_result)
        step(
            "transcriber",
            "completed",
            {
                "source": transcribed_material.get("source", {}),
                "sanitization": transcribed_material.get("sanitization", {}),
            },
        )
        return transcribed_material

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        deps.write_json_func(item_manifest_path, item_manifest)

        metadata, metadata_debug = metadata_from_item_with_debug(item)
        if item.get("resolver_debug"):
            resolver_status = "fallback" if item.get("transcript_fallback") else "completed"
            step("resolver", resolver_status, item["resolver_debug"])
        step("metadata", "completed", {"metadata": metadata, **metadata_debug})

        material: dict[str, Any]
        local_input: dict[str, Any] | None = None
        transcript_quality_debug: dict[str, Any] | None = None
        fallback_material = item.get("_transcript_material")
        if isinstance(fallback_material, dict):
            material = copy.deepcopy(fallback_material)
            fallback_step = copy.deepcopy(item.get("transcript_fallback") or {})
            fallback_step.setdefault("transcript_source", material.get("source", {}).get("kind", "youtube_connect"))
            fallback_step.setdefault("subtitle_lang", material.get("language", "en"))
            fallback_step.setdefault("subtitle_format", material.get("source", {}).get("subtitle_format", "transcript"))
            fallback_step.setdefault("segment_count", material.get("segment_count"))
            fallback_step.setdefault("char_count", material.get("char_count"))
            step("transcript_fallback", "completed", fallback_step)
            step(
                "subtitle_fetcher",
                "skipped",
                {
                    "reason": "transcript_fallback_available",
                    "selected_subtitle_lang": fallback_step.get("subtitle_lang", ""),
                    "selected_subtitle_kind": material.get("source", {}).get("subtitle_kind", ""),
                    "selected_subtitle_format": fallback_step.get("subtitle_format", ""),
                    "audio_downloader_skipped_due_to_subtitle": True,
                },
            )
            step(
                "audio_downloader",
                "skipped",
                {
                    "audio_downloader_skipped_due_to_subtitle": True,
                    "reason": "transcript_fallback_available",
                    "selected_subtitle_lang": fallback_step.get("subtitle_lang", ""),
                    "selected_subtitle_kind": material.get("source", {}).get("subtitle_kind", ""),
                    "selected_subtitle_format": fallback_step.get("subtitle_format", ""),
                },
            )
        else:
            try:
                subtitle_result = deps.fetch_subtitles_func(metadata["url"], subtitle_dir, **deps.subtitle_options)
                material = material_from_result(subtitle_result)
                subtitle_step = {"source": material.get("source", {})}
                for key in ("segment_count", "char_count"):
                    if material.get(key) not in (None, ""):
                        subtitle_step[key] = material[key]
                subtitle_step.update(subtitle_debug_from_result(subtitle_result))
                step("subtitle_fetcher", "completed", subtitle_step)
                subtitle_local_input = transcript_material_to_local_extract_input(metadata, material)
                subtitle_quality_debug = validate_transcript_coverage(
                    video_duration=metadata.get("duration"),
                    segments=subtitle_local_input["transcript_segments"],
                    transcript_source=subtitle_local_input["transcript_source"],
                )
                if subtitle_quality_should_fallback_to_audio(subtitle_quality_debug):
                    fallback_reason = str(subtitle_quality_debug.get("transcript_quality_reason") or "")
                    rejection_debug = {
                        "reason_code": subtitle_quality_fallback_reason_code(fallback_reason),
                        "error": subtitle_quality_fallback_error(fallback_reason),
                        "fallback_stage": "audio_downloader",
                        "subtitle_rejected_due_to_quality": True,
                        "coverage_threshold": MIN_TRANSCRIPT_COVERAGE_RATIO,
                        **subtitle_quality_debug,
                    }
                    step("transcript_quality", "fallback_to_audio", rejection_debug)
                    material = download_and_transcribe_audio(
                        subtitle_available=True,
                        rejection_debug=subtitle_quality_debug,
                    )
                else:
                    local_input = subtitle_local_input
                    transcript_quality_debug = subtitle_quality_debug
                    step(
                        "audio_downloader",
                        "skipped",
                        {
                            "audio_downloader_skipped_due_to_subtitle": True,
                            "reason": "subtitle_available",
                            "selected_subtitle_lang": subtitle_step.get("selected_subtitle_lang", ""),
                            "selected_subtitle_kind": subtitle_step.get("selected_subtitle_kind", ""),
                            "selected_subtitle_format": subtitle_step.get("selected_subtitle_format", ""),
                        },
                    )
            except SubtitleUnavailableError as exc:
                subtitle_error_step = {"reason_code": exc.reason_code, "error": exc.message}
                subtitle_error_step.update(subtitle_debug_from_error(exc))
                step("subtitle_fetcher", "unavailable", subtitle_error_step)
                material = download_and_transcribe_audio(subtitle_available=False)
            except BilibiliSubtitleError as exc:
                subtitle_error_step = {"reason_code": exc.reason_code, "error": exc.message}
                subtitle_error_step.update(subtitle_debug_from_error(exc))
                fallback_allowed = bool(subtitle_error_step.get("audio_fallback_allowed", True))
                step("subtitle_fetcher", "unavailable" if fallback_allowed else "failed", subtitle_error_step)
                if not fallback_allowed:
                    raise
                material = download_and_transcribe_audio(subtitle_available=False)

        if local_input is None or transcript_quality_debug is None:
            local_input = transcript_material_to_local_extract_input(metadata, material)
            transcript_quality_debug = validate_transcript_coverage(
                video_duration=metadata.get("duration"),
                segments=local_input["transcript_segments"],
                transcript_source=local_input["transcript_source"],
            )
        if transcript_quality_debug.get("transcript_quality_passed") is not True:
            failed_debug = {
                "stage": "transcript_quality",
                "reason_code": "transcript_coverage_too_low",
                "error": "transcript coverage is too low for reliable analysis",
                "coverage_threshold": MIN_TRANSCRIPT_COVERAGE_RATIO,
                **transcript_quality_debug,
            }
            step("transcript_quality", "failed", failed_debug)
            raise TranscriptCoverageTooLowError(failed_debug)
        step(
            "transcript_quality",
            "completed",
            {
                "coverage_threshold": MIN_TRANSCRIPT_COVERAGE_RATIO,
                **transcript_quality_debug,
            },
        )
        local_extract_kwargs = {
            "transcript_language": local_input["transcript_language"],
            "transcript_quality": local_input["transcript_quality"],
            "transcript_source": local_input["transcript_source"],
        }
        local_extract_kwargs.update(deps.local_extract_options)
        local_extract_kwargs.setdefault("debug_dir", artifact_dir)
        local_extract_debug = local_extract_start_debug(local_extract_kwargs)
        local_extract_debug["transcript_hash"] = transcript_hash_from_segments(local_input["transcript_segments"])
        local_extract_debug["segment_count"] = len(local_input["transcript_segments"])
        local_extract_debug["transcript_char_count"] = sum(
            len(str(segment.get("text") or ""))
            for segment in local_input["transcript_segments"]
            if isinstance(segment, dict)
        )
        step("local_extract", "started", local_extract_debug)
        try:
            local_extract_payload = deps.build_local_extract_func(
                local_input["metadata"],
                local_input["transcript_segments"],
                **local_extract_kwargs,
            )
        except BaseException as exc:
            record = error_record(item, exc)
            local_extract_error = {
                "stage": record["stage"],
                "reason_code": record["reason_code"],
                "error": record["error"],
                **local_extract_debug,
            }
            deps.write_json_func(artifact_dir / "local_extract_error.json", local_extract_error)
            step("local_extract", "failed", local_extract_error)
            raise
        deps.write_json_func(local_extract_path, local_extract_payload)
        chunked_debug = local_extract_payload.get("chunked_local_extract")
        completed_local_extract_debug = {
            "path": str(local_extract_path),
            "qwen_model": local_extract_payload.get("qwen_extract", {}).get("_qwen_model", local_extract_debug["qwen_model"]),
            "qwen_model_id": local_extract_payload.get("qwen_extract", {}).get("_qwen_model", local_extract_debug["qwen_model"]),
            "qwen_base_url": local_extract_debug["qwen_base_url"],
            "qwen_api_base": local_extract_debug["qwen_api_base"],
            "qwen_prompt_version": local_extract_payload.get("analysis_boundary", {}).get("qwen_prompt_version", ""),
            "transcript_hash": local_extract_debug["transcript_hash"],
            "timeout": local_extract_debug["timeout"],
            "segment_count": local_extract_debug["segment_count"],
            "transcript_char_count": local_extract_debug["transcript_char_count"],
            "chunked": bool(chunked_debug),
        }
        if isinstance(chunked_debug, dict):
            completed_local_extract_debug.update({
                "chunk_count": chunked_debug.get("chunk_count"),
                "successful_chunk_count": chunked_debug.get("successful_chunk_count"),
                "failed_chunk_count": chunked_debug.get("failed_chunk_count"),
                "success_coverage": chunked_debug.get("success_coverage"),
                "reduce_status": (chunked_debug.get("reduce") or {}).get("status") if isinstance(chunked_debug.get("reduce"), dict) else "",
            })
        step(
            "local_extract",
            "completed",
            completed_local_extract_debug,
        )

        review_request = deps.build_review_request_func(local_extract_payload, report_target=report_target)
        scoring_formula_version = str(deps.review_options.get("scoring_formula_version") or SCORING_FORMULA_VERSION)
        stability = review_request.setdefault("stability_metadata", {})
        if isinstance(stability, dict):
            stability["scoring_formula_version"] = scoring_formula_version
            stability["report_target"] = report_target
        deps.write_json_func(review_request_path, review_request)
        codex_model_id = str(deps.review_options.get("codex_model") or "")
        step(
            "codex_review_request",
            "completed",
            {
                "model_call_allowed": review_request.get("model_call_allowed"),
                "report_target": report_target,
                "transcript_hash": local_extract_debug["transcript_hash"],
                "qwen_model_id": local_extract_payload.get("qwen_extract", {}).get("_qwen_model", local_extract_debug["qwen_model"]),
                "qwen_prompt_version": local_extract_payload.get("analysis_boundary", {}).get("qwen_prompt_version", ""),
                "codex_model": codex_model_id,
                "codex_prompt_version": str(review_request.get("codex_prompt_version") or CODEX_REVIEW_PROMPT_VERSION),
                "scoring_formula_version": scoring_formula_version,
                "watchbrief_version": WATCHBRIEF_VERSION,
            },
        )

        cache_enabled = bool(deps.review_options.get("report_cache_enabled", False))
        force_reanalysis = bool(deps.review_options.get("force_reanalysis", False))
        cache_key = ""
        cached_payload_path = ""
        cache_identity: Optional[dict[str, Any]] = None
        cached_payload: Optional[dict[str, Any]] = None
        stability_metadata = copy.deepcopy(review_request.get("stability_metadata") or {})
        if codex_model_id == LOCAL_REVIEW_MODEL_ID:
            review_request["codex_prompt_fingerprint"] = LOCAL_REVIEW_PROMPT_FINGERPRINT
            review_metadata = review_request.get("stability_metadata")
            if isinstance(review_metadata, dict):
                review_metadata["codex_prompt_fingerprint"] = LOCAL_REVIEW_PROMPT_FINGERPRINT
            stability_metadata["codex_prompt_fingerprint"] = LOCAL_REVIEW_PROMPT_FINGERPRINT
        if codex_model_id:
            stability_metadata["codex_model"] = codex_model_id
        if cache_enabled:
            cache_root = report_cache_dir(deps.review_options.get("report_cache_dir"))
            cache_identity = build_report_cache_identity(review_request, codex_model=codex_model_id)
            if force_reanalysis:
                cache_key = ""
                cached_payload_path = ""
                step("report_cache", "bypassed", {"force_reanalysis": True})
            else:
                cached_payload, cached_path, cache_key = read_cached_report(cache_root, cache_identity)
                cached_payload_path = str(cached_path)
                if cached_payload is not None:
                    item_manifest["cache_hit"] = True
                    item_manifest["cache_key"] = cache_key
                    item_manifest["cached_payload_path"] = cached_payload_path
                    step(
                        "report_cache",
                        "hit",
                        {
                            "cache_hit": True,
                            "cache_key": cache_key,
                            "cached_payload_path": cached_payload_path,
                        },
                    )
                else:
                    item_manifest["cache_hit"] = False
                    item_manifest["cache_key"] = cache_key
                    item_manifest["cached_payload_path"] = cached_payload_path
                    step(
                        "report_cache",
                        "miss",
                        {
                            "cache_hit": False,
                            "cache_key": cache_key,
                            "cached_payload_path": cached_payload_path,
                        },
                    )

        if cached_payload is not None:
            normalized_payload = apply_metadata_display_fields(cached_payload, metadata)
            deps.write_json_func(payload_path, normalized_payload)
            step("codex_review", "skipped", {"cache_hit": True, "cache_key": cache_key, "cached_payload_path": cached_payload_path})
            step("validator", "completed", {"path": str(payload_path), "source": "report_cache"})
        else:
            if review_response_provider is None:
                raise ReviewResponseUnavailableError()
            provider_review_request = copy.deepcopy(review_request)
            provider_review_request["_watchbrief_debug_dir"] = str(artifact_dir)
            raw_review_response = review_response_provider(copy.deepcopy(item), provider_review_request, copy.deepcopy(local_extract_payload))
            normalized_payload = apply_metadata_display_fields(
                parse_review_response_with_metadata(
                    deps.parse_review_response_func,
                    raw_review_response,
                    stability_metadata,
                    report_target=report_target,
                ),
                metadata,
            )
            deps.write_json_func(payload_path, normalized_payload)
            step("validator", "completed", {"path": str(payload_path)})
            if cache_enabled and cache_identity is not None:
                written_cache_path, written_cache_key = write_cached_report(report_cache_dir(deps.review_options.get("report_cache_dir")), cache_identity, normalized_payload)
                item_manifest["cache_hit"] = False
                item_manifest["cache_key"] = written_cache_key
                item_manifest["cached_payload_path"] = str(written_cache_path)
                step(
                    "report_cache",
                    "stored",
                    {
                        "cache_hit": False,
                        "cache_key": written_cache_key,
                        "cached_payload_path": str(written_cache_path),
                    },
                )

        html = deps.render_func(normalized_payload)
        deps.write_html_func(html_path, html)
        step("renderer", "completed", {"html_path": str(html_path)})

        deps.cleanup_func(work_dir)
        step("cleanup", "completed", {"work_dir": str(work_dir)})

        item_manifest["status"] = "completed"
        deps.write_json_func(item_manifest_path, item_manifest)
        return {
            "status": "completed",
            "index": index,
            "report_target": report_target,
            "title": item_manifest["title"],
            "url": item_manifest["url"],
            "html_path": str(html_path),
            "payload_path": str(payload_path),
            "item_manifest_path": str(item_manifest_path),
            "resolver_debug": item.get("resolver_debug", {}),
        }
    except BaseException as exc:
        if isinstance(exc, AcquisitionError) and exc.stage == "subtitle_fetcher":
            subtitle_debug = {
                "reason_code": exc.reason_code,
                "error": exc.message,
            }
            subtitle_debug.update(subtitle_debug_from_error(exc))
            step("subtitle_fetcher", "failed", subtitle_debug)
        if isinstance(exc, AcquisitionError) and exc.stage == "audio_downloader":
            audio_debug = audio_debug_from_error(exc)
            audio_debug.update({
                "reason_code": exc.reason_code,
                "error": exc.message,
                "audio_downloader_skipped_due_to_subtitle": False,
            })
            step(
                "audio_downloader",
                "failed",
                audio_debug,
            )
        record = error_record(item, exc)
        item_manifest["status"] = "failed"
        item_manifest["error"] = record
        deps.write_json_func(item_manifest_path, item_manifest)
        return {
            "status": "failed",
            "index": index,
            "report_target": report_target,
            "title": item_manifest["title"],
            "url": item_manifest["url"],
            "error": record,
            "item_manifest_path": str(item_manifest_path),
            "resolver_debug": item.get("resolver_debug", {}),
        }
