#!/usr/bin/env python3
"""YouTube Connect transcript fallback provider.

This provider is intentionally narrow: it only fetches a single video's
timestamped transcript and turns it into WatchBrief transcript material. It does
not expand playlists, download media, call models, or inspect browser cookies.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Protocol

try:
    from ..acquisition_errors import SubtitleUnavailableError
    from ..transcript_source_adapter import clean_text, normalize_language, seconds_to_timestamp
except ImportError:  # pragma: no cover - direct script execution
    from acquisition_errors import SubtitleUnavailableError
    from transcript_source_adapter import clean_text, normalize_language, seconds_to_timestamp


@dataclass(frozen=True)
class YouTubeConnectTranscript:
    provider: str
    source_platform: str
    video_id: str
    title: str
    duration: str
    playlist_title: str
    subtitle_kind: str
    subtitle_lang: str
    subtitle_format: str
    segments: list[dict[str, Any]]
    plain_text: str
    source_url: str


class YouTubeConnectProvider(Protocol):
    def fetch_transcript(self, url: str) -> YouTubeConnectTranscript:
        """Return a WatchBrief-ready YouTube transcript or raise a typed acquisition error."""


DEFAULT_YOUTUBE_CONNECT_LANGUAGES = ("en", "en-US", "en-GB", "en-orig")
VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


def extract_youtube_video_id(url_or_id: str) -> str:
    value = str(url_or_id or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    match = VIDEO_ID_RE.search(value)
    return match.group(1) if match else ""


def is_youtube_single_video_url(url: str) -> bool:
    return bool(extract_youtube_video_id(url))


def snippet_value(snippet: Any, key: str, default: Any = None) -> Any:
    if isinstance(snippet, dict):
        return snippet.get(key, default)
    return getattr(snippet, key, default)


def normalize_snippets_to_segments(snippets: Iterable[Any]) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    for index, snippet in enumerate(snippets):
        text = clean_text(snippet_value(snippet, "text", ""))
        if not text:
            continue
        try:
            start_float = float(snippet_value(snippet, "start", 0) or 0)
            duration_float = float(snippet_value(snippet, "duration", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise SubtitleUnavailableError("YouTube Connect transcript contained invalid timestamps") from exc
        start_seconds = max(0, int(math.floor(start_float)))
        end_seconds = max(start_seconds + 1, int(math.ceil(start_float + max(duration_float, 0.0))))
        segments.append({
            "start": seconds_to_timestamp(start_seconds),
            "end": seconds_to_timestamp(end_seconds),
            "text": text,
        })
    if not segments:
        raise SubtitleUnavailableError("YouTube Connect returned no usable transcript segments")
    return segments


def transcript_metadata_value(transcript: Any, key: str, default: Any = None) -> Any:
    if isinstance(transcript, dict):
        return transcript.get(key, default)
    return getattr(transcript, key, default)


def fetched_transcript_to_snippets(transcript: Any) -> list[Any]:
    snippets = transcript_metadata_value(transcript, "snippets", None)
    if snippets is not None:
        return list(snippets)
    return list(transcript)


def material_from_youtube_connect(transcript: YouTubeConnectTranscript) -> dict[str, Any]:
    return {
        "material_version": "watchbrief_v5.transcript_material.v1",
        "source": {
            "kind": "youtube_connect",
            "provider": transcript.provider,
            "source_platform": transcript.source_platform,
            "video_id": transcript.video_id,
            "title": transcript.title,
            "duration": transcript.duration,
            "source_url": transcript.source_url,
            "subtitle_language": transcript.subtitle_lang,
            "subtitle_kind": transcript.subtitle_kind,
            "subtitle_format": transcript.subtitle_format,
        },
        "language": normalize_language(transcript.subtitle_lang),
        "transcript_quality": "degraded" if transcript.subtitle_kind == "automatic" else "ok",
        "segment_count": len(transcript.segments),
        "char_count": sum(len(str(segment.get("text") or "")) for segment in transcript.segments),
        "has_timestamps": True,
        "segments": list(transcript.segments),
        "adapter_boundary": {
            "no_real_url_processing": False,
            "no_subtitle_download": True,
            "no_audio_download": True,
            "no_transcription": True,
            "no_model_call": True,
        },
    }


def fetch_youtube_connect_transcript(
    url: str,
    *,
    languages: tuple[str, ...] = DEFAULT_YOUTUBE_CONNECT_LANGUAGES,
    api_factory: Optional[Callable[[], Any]] = None,
) -> YouTubeConnectTranscript:
    video_id = extract_youtube_video_id(url)
    if not video_id:
        raise SubtitleUnavailableError("YouTube Connect fallback requires a YouTube video id")
    try:
        if api_factory is None:
            from youtube_transcript_api import YouTubeTranscriptApi

            api_factory = YouTubeTranscriptApi
        api = api_factory()
        fetched = api.fetch(video_id, languages=list(languages))
    except Exception as exc:
        raise SubtitleUnavailableError(f"YouTube Connect transcript unavailable: {exc}") from exc

    snippets = fetched_transcript_to_snippets(fetched)
    segments = normalize_snippets_to_segments(snippets)
    language_code = str(
        transcript_metadata_value(fetched, "language_code", "")
        or transcript_metadata_value(fetched, "language", "")
        or languages[0]
    )
    is_generated = bool(transcript_metadata_value(fetched, "is_generated", False))
    duration_seconds = max(
        int(math.ceil(
            max(
                [float(snippet_value(snippet, "start", 0) or 0) + float(snippet_value(snippet, "duration", 0) or 0) for snippet in snippets]
            )
        )),
        1,
    )
    return YouTubeConnectTranscript(
        provider="youtube-connect",
        source_platform="youtube",
        video_id=video_id,
        title=video_id,
        duration=seconds_to_timestamp(duration_seconds),
        playlist_title="",
        subtitle_kind="automatic" if is_generated else "manual",
        subtitle_lang=language_code,
        subtitle_format="transcript",
        segments=segments,
        plain_text=" ".join(segment["text"] for segment in segments),
        source_url=url,
    )
