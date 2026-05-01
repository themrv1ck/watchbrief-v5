"""Transcript coverage checks before local extraction."""

from __future__ import annotations

import re
from typing import Any


MIN_DURATION_FOR_COVERAGE_GATE_SECONDS = 60
MIN_TRANSCRIPT_COVERAGE_RATIO = 0.30
MIN_SEGMENT_COUNT_WITH_DURATION = 2
MIN_TEXT_CHARS_WITH_DURATION = 40
MIN_SEGMENT_COUNT_WITHOUT_DURATION = 3
MIN_TEXT_CHARS_WITHOUT_DURATION = 80
MAX_SUBTITLE_TIMELINE_RATIO = 1.20
MAX_SUBTITLE_TIMELINE_EXTRA_SECONDS = 20.0
PLATFORM_SUBTITLE_SOURCES = {
    "subtitle_bcc",
    "subtitle_srt",
    "subtitle_vtt",
}
TRANSCRIPT_SOURCES_REQUIRING_VIDEO_DURATION = {
    *PLATFORM_SUBTITLE_SOURCES,
    "youtube_connect",
}


def parse_time_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if seconds >= 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        seconds = float(text)
        return seconds if seconds >= 0 else None
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?", text):
        parts = [float(part) for part in text.split(":")]
        if len(parts) == 2:
            minutes, seconds = parts
            return minutes * 60 + seconds
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    match = re.fullmatch(r"(?:(\d+)小时)?(?:(\d+)分)?(?:(\d+(?:\.\d+)?)秒)?", text)
    if match and any(match.groups()):
        hours = float(match.group(1) or 0)
        minutes = float(match.group(2) or 0)
        seconds = float(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds
    return None


def segment_start_end(segment: dict[str, Any]) -> tuple[float | None, float | None]:
    start = parse_time_seconds(segment.get("start"))
    end = parse_time_seconds(segment.get("end"))
    if end is None:
        end = parse_time_seconds(segment.get("start_seconds"))
    if start is None:
        start = parse_time_seconds(segment.get("start_seconds"))
    return start, end


def transcript_coverage_debug(
    *,
    video_duration: Any,
    segments: list[dict[str, Any]],
    transcript_source: str,
) -> dict[str, Any]:
    video_duration_seconds = parse_time_seconds(video_duration)
    starts: list[float] = []
    ends: list[float] = []
    plain_text_char_count = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "")
        plain_text_char_count += len("".join(text.split()))
        start, end = segment_start_end(segment)
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)
        elif start is not None:
            ends.append(start)

    transcript_first_start = min(starts) if starts else None
    transcript_last_end = max(ends) if ends else None
    transcript_covered_duration = None
    if transcript_first_start is not None and transcript_last_end is not None:
        transcript_covered_duration = max(0.0, transcript_last_end - transcript_first_start)

    transcript_coverage_ratio = None
    transcript_uncapped_coverage_ratio = None
    transcript_timeline_overrun_seconds = None
    transcript_timeline_limit_seconds = None
    if video_duration_seconds and video_duration_seconds > 0 and transcript_last_end is not None:
        transcript_uncapped_coverage_ratio = max(0.0, transcript_last_end / video_duration_seconds)
        transcript_coverage_ratio = min(1.0, transcript_uncapped_coverage_ratio)
        transcript_timeline_overrun_seconds = max(0.0, transcript_last_end - video_duration_seconds)
        transcript_timeline_limit_seconds = max(
            video_duration_seconds * MAX_SUBTITLE_TIMELINE_RATIO,
            video_duration_seconds + MAX_SUBTITLE_TIMELINE_EXTRA_SECONDS,
        )

    debug = {
        "video_duration_seconds": video_duration_seconds,
        "transcript_first_start": transcript_first_start,
        "transcript_last_end": transcript_last_end,
        "transcript_covered_duration": transcript_covered_duration,
        "transcript_coverage_ratio": transcript_coverage_ratio,
        "transcript_uncapped_coverage_ratio": transcript_uncapped_coverage_ratio,
        "transcript_timeline_overrun_seconds": transcript_timeline_overrun_seconds,
        "transcript_timeline_limit_seconds": transcript_timeline_limit_seconds,
        "transcript_segment_count": len([segment for segment in segments if isinstance(segment, dict)]),
        "transcript_plain_text_char_count": plain_text_char_count,
        "transcript_source": transcript_source,
        "transcript_quality_reason": "coverage_ok",
    }
    return debug


def transcript_coverage_failure_reason(debug: dict[str, Any]) -> str:
    duration = debug.get("video_duration_seconds")
    ratio = debug.get("transcript_coverage_ratio")
    last_end = debug.get("transcript_last_end")
    timeline_limit = debug.get("transcript_timeline_limit_seconds")
    segment_count = int(debug.get("transcript_segment_count") or 0)
    char_count = int(debug.get("transcript_plain_text_char_count") or 0)
    transcript_source = str(debug.get("transcript_source") or "")

    if (
        transcript_source in PLATFORM_SUBTITLE_SOURCES
        and isinstance(duration, (int, float))
        and duration > 0
        and isinstance(last_end, (int, float))
        and isinstance(timeline_limit, (int, float))
        and last_end > timeline_limit
    ):
        return "subtitle_timeline_exceeds_video_duration"
    if isinstance(duration, (int, float)) and duration >= MIN_DURATION_FOR_COVERAGE_GATE_SECONDS:
        if isinstance(ratio, (int, float)) and ratio < MIN_TRANSCRIPT_COVERAGE_RATIO:
            return "coverage_below_threshold"
        if segment_count < MIN_SEGMENT_COUNT_WITH_DURATION:
            return "segment_count_too_low"
        if char_count < MIN_TEXT_CHARS_WITH_DURATION:
            return "plain_text_char_count_too_low"
    if duration is None:
        if transcript_source in TRANSCRIPT_SOURCES_REQUIRING_VIDEO_DURATION:
            return "video_duration_missing_for_quality_gate"
        if segment_count < MIN_SEGMENT_COUNT_WITHOUT_DURATION:
            return "segment_count_too_low_without_duration"
        if char_count < MIN_TEXT_CHARS_WITHOUT_DURATION:
            return "plain_text_char_count_too_low_without_duration"
    return ""


def validate_transcript_coverage(
    *,
    video_duration: Any,
    segments: list[dict[str, Any]],
    transcript_source: str,
) -> dict[str, Any]:
    debug = transcript_coverage_debug(
        video_duration=video_duration,
        segments=segments,
        transcript_source=transcript_source,
    )
    reason = transcript_coverage_failure_reason(debug)
    if reason:
        debug["transcript_quality_reason"] = reason
        debug["transcript_quality_passed"] = False
    else:
        debug["transcript_quality_passed"] = True
    return debug
