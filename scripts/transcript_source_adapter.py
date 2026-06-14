#!/usr/bin/env python3
"""Local transcript source adapter for WatchBrief V5.

This module only reads existing local subtitle/transcription files and
normalizes them into timestamped transcript material. It does not resolve URLs,
call yt-dlp, download media, fetch subtitles, transcribe audio, call models, or
create the video pipeline.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any, Optional


VALID_TRANSCRIPT_QUALITIES = ("ok", "degraded", "poor")
FINAL_REPORT_FIELDS_BLOCKED_IN_TRANSCRIPT_MATERIAL = {
    "replacement_score",
    "tag",
    "one_line_brief",
    "watch_verdict",
    "highest_compression",
    "path_table",
    "arrow_chain",
    "final_conclusion",
    "content_caveat",
    "watch_segments",
    "long_content_breakdown",
    "only_one_segment",
    "score_basis",
    "confidence_note",
}

SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
JSON_EXTENSIONS = {".json"}
TXT_EXTENSIONS = {".txt"}

TIME_CORE_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?(?:[\.,]\d{1,3})?$")
TXT_LINE_RE = re.compile(
    r"^\s*\[?\s*(?P<start>\d{1,2}:\d{2}(?::\d{2})?(?:[\.,]\d{1,3})?)\s*"
    r"(?:\||-->|-|—|–|－|~|～|至|到)\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?(?:[\.,]\d{1,3})?)\s*\]?\s*"
    r"(?P<text>.+?)\s*$"
)


class TranscriptSourceError(ValueError):
    pass


class TranscriptSanitizationError(TranscriptSourceError):
    pass


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_language(value: Any) -> str:
    language = clean_text(value).lower()
    if not language:
        return "unknown"
    if any(char.isspace() for char in language):
        raise TranscriptSourceError("language must be a compact language code or label")
    if language in {"zh", "zh-cn", "zh-hans", "zh-hant", "zh-tw", "zh-hk", "zh-sg", "cmn", "chinese", "中文"}:
        return "zh"
    if language in {"en", "en-us", "en-gb", "en-ca", "en-au", "english"}:
        return "en"
    if language in {"mixed", "mix", "zh-en", "en-zh", "中英混合"}:
        return "mixed"
    if language in {"unknown", "und", "auto"}:
        return "unknown"
    return "unknown"


def normalize_transcript_quality(value: Any) -> str:
    quality = clean_text(value)
    if quality not in VALID_TRANSCRIPT_QUALITIES:
        raise TranscriptSourceError(f"transcript_quality must be one of {', '.join(VALID_TRANSCRIPT_QUALITIES)}")
    return quality


def parse_time_to_seconds(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < 0:
            raise TranscriptSourceError(f"invalid timestamp: {value}")
        return int(value)
    raw = clean_text(value).replace(",", ".")
    if not TIME_CORE_RE.match(raw):
        raise TranscriptSourceError(f"invalid timestamp: {value}")
    parts = raw.split(":")
    seconds_part = parts[-1].split(".", 1)[0]
    if len(parts) == 2:
        hours = 0
        minutes = int(parts[0])
        seconds = int(seconds_part)
    elif len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(seconds_part)
    else:
        raise TranscriptSourceError(f"invalid timestamp: {value}")
    if minutes >= 60 and len(parts) == 3:
        raise TranscriptSourceError(f"invalid timestamp minutes: {value}")
    if seconds >= 60:
        raise TranscriptSourceError(f"invalid timestamp seconds: {value}")
    return hours * 3600 + minutes * 60 + seconds


def canonical_timestamp(value: Any) -> str:
    total_seconds = parse_time_to_seconds(value)
    return seconds_to_timestamp(total_seconds)


def seconds_to_timestamp(total_seconds: int) -> str:
    if total_seconds < 0:
        raise TranscriptSourceError(f"invalid timestamp: {total_seconds}")
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def normalize_segment(start: Any, end: Any, value: Any, path: str) -> dict[str, str]:
    segment = raw_segment(start, end, value, path)
    if parse_time_to_seconds(segment["start"]) >= parse_time_to_seconds(segment["end"]):
        raise TranscriptSourceError(f"{path} start must be before end")
    if not segment["text"]:
        raise TranscriptSourceError(f"{path} text is required")
    return segment


def raw_segment(start: Any, end: Any, value: Any, path: str) -> dict[str, str]:
    start_text = canonical_timestamp(start)
    end_text = canonical_timestamp(end)
    segment_text = clean_text(value)
    return {"start": start_text, "end": end_text, "text": segment_text}


def segment_seconds(segment: dict[str, str]) -> tuple[int, int]:
    return parse_time_to_seconds(segment["start"]), parse_time_to_seconds(segment["end"])


def warning_message(action: str, index: int, reason: str) -> str:
    return f"{action}_invalid_timestamp_segment index={index} {reason}"


def infer_short_legal_segment(
    segment: dict[str, str],
    *,
    previous_end: Optional[int],
    next_start: Optional[int],
) -> Optional[dict[str, str]]:
    start_seconds, _ = segment_seconds(segment)
    inferred_start = max(start_seconds, previous_end or 0)
    if next_start is not None and next_start <= inferred_start:
        return None
    inferred_end = inferred_start + 1
    if next_start is not None:
        inferred_end = min(inferred_end, next_start)
    if inferred_start >= inferred_end:
        return None
    return {
        "start": seconds_to_timestamp(inferred_start),
        "end": seconds_to_timestamp(inferred_end),
        "text": segment["text"],
    }


def next_segment_start(raw_segments: list[dict[str, str]], current_index: int) -> Optional[int]:
    for segment in raw_segments[current_index + 1:]:
        try:
            start_seconds, end_seconds = segment_seconds(segment)
        except TranscriptSourceError:
            continue
        if start_seconds < end_seconds:
            return start_seconds
    return None


def sanitize_transcript_segments(
    raw_segments: list[dict[str, str]],
    *,
    transcript_quality: str,
) -> tuple[list[dict[str, str]], str, list[str], dict[str, int]]:
    if not raw_segments:
        raise TranscriptSourceError("transcript contains no timestamp segments")

    sanitized: list[dict[str, str]] = []
    warnings: list[str] = []
    invalid_count = 0
    dropped_count = 0
    repaired_count = 0
    previous_end: Optional[int] = None

    for index, segment in enumerate(raw_segments):
        start_seconds, end_seconds = segment_seconds(segment)
        text_value = clean_text(segment.get("text"))
        if start_seconds < end_seconds and text_value:
            clean_segment = {"start": segment["start"], "end": segment["end"], "text": text_value}
            sanitized.append(clean_segment)
            previous_end = end_seconds
            continue

        invalid_count += 1
        if start_seconds >= end_seconds:
            reason = "start>=end"
        else:
            reason = "empty_text"

        if not text_value:
            dropped_count += 1
            warnings.append(warning_message("dropped", index, reason))
            continue

        repaired = infer_short_legal_segment(
            {**segment, "text": text_value},
            previous_end=previous_end,
            next_start=next_segment_start(raw_segments, index),
        )
        if repaired is None:
            dropped_count += 1
            warnings.append(warning_message("dropped", index, reason))
            continue

        sanitized.append(repaired)
        repaired_count += 1
        _, previous_end = segment_seconds(repaired)
        warnings.append(
            f"repaired_invalid_timestamp_segment index={index} {reason} "
            f"to={repaired['start']} | {repaired['end']}"
        )

    if not sanitized:
        raise TranscriptSanitizationError("transcript contains no usable timestamp segments after sanitization")

    for index, segment in enumerate(sanitized):
        start_seconds, end_seconds = segment_seconds(segment)
        if start_seconds >= end_seconds:
            raise TranscriptSanitizationError(f"sanitized segments[{index}] start must be before end")

    normalized_quality = "degraded" if warnings else transcript_quality
    stats = {
        "raw_segment_count": len(raw_segments),
        "invalid_segment_count": invalid_count,
        "dropped_segment_count": dropped_count,
        "repaired_segment_count": repaired_count,
        "usable_segment_count": len(sanitized),
    }
    return sanitized, normalized_quality, warnings, stats


def parse_time_line(line: str) -> Optional[tuple[str, str]]:
    if "-->" not in line:
        return None
    left, right = line.split("-->", 1)
    end = right.strip().split()[0]
    return left.strip(), end.strip()


def parse_srt_or_vtt(content: str) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    current_time: Optional[tuple[str, str]] = None
    current_text: list[str] = []

    def flush() -> None:
        nonlocal current_time, current_text
        if current_time is not None:
            segments.append(raw_segment(current_time[0], current_time[1], " ".join(current_text), f"segments[{len(segments)}]"))
        current_time = None
        current_text = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line.upper() == "WEBVTT" or line.isdigit() or line.startswith(("NOTE", "STYLE", "REGION")):
            continue
        maybe_time = parse_time_line(line)
        if maybe_time:
            flush()
            current_time = maybe_time
            current_text = []
            continue
        if current_time is not None:
            current_text.append(line)
    flush()
    if not segments:
        raise TranscriptSourceError("subtitle file contains no timestamp segments")
    return segments


def split_timestamp_range(value: Any) -> tuple[str, str]:
    text = clean_text(value)
    match = TXT_LINE_RE.match(f"{text} placeholder")
    if match:
        return match.group("start"), match.group("end")
    for separator in ("|", "-->", "-", "—", "–", "－", "~", "～", "至", "到"):
        if separator in text:
            start, end = text.split(separator, 1)
            return start.strip(), end.strip()
    raise TranscriptSourceError(f"invalid timestamp range: {value}")


def segment_from_json_item(item: dict[str, Any], index: int) -> dict[str, str]:
    if not isinstance(item, dict):
        raise TranscriptSourceError(f"segments[{index}] must be an object")
    if item.get("start") is not None and item.get("end") is not None:
        start, end = item.get("start"), item.get("end")
    elif item.get("timestamp") is not None:
        start, end = split_timestamp_range(item.get("timestamp"))
    else:
        raise TranscriptSourceError(f"segments[{index}] missing start/end")
    text_value = item.get("text", item.get("content", item.get("transcript")))
    return raw_segment(start, end, text_value, f"segments[{index}]")


def parse_json_transcript(content: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    data = json.loads(content)
    metadata: dict[str, Any] = {}
    if isinstance(data, list):
        raw_segments = data
    elif isinstance(data, dict):
        metadata = data
        raw_segments = (
            data.get("transcript_segments")
            or data.get("segments")
            or data.get("items")
        )
    else:
        raise TranscriptSourceError("transcription JSON must be an object or list")
    if not isinstance(raw_segments, list):
        raise TranscriptSourceError("transcription JSON must contain transcript_segments or segments")
    segments = [segment_from_json_item(item, index) for index, item in enumerate(raw_segments)]
    if not segments:
        raise TranscriptSourceError("transcription JSON contains no timestamp segments")
    return segments, metadata


def parse_txt_transcript(content: str) -> list[dict[str, str]]:
    segments: list[dict[str, str]] = []
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = TXT_LINE_RE.match(line)
        if not match:
            raise TranscriptSourceError(f"line {line_number} must start with a timestamp range")
        segments.append(raw_segment(match.group("start"), match.group("end"), match.group("text"), f"line {line_number}"))
    if not segments:
        raise TranscriptSourceError("transcription TXT contains no timestamp segments")
    return segments


def detect_source_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return "subtitle_srt"
    if suffix == ".vtt":
        return "subtitle_vtt"
    if suffix == ".json":
        return "transcription_json"
    if suffix == ".txt":
        return "transcription_txt"
    raise TranscriptSourceError(f"unsupported transcript source extension: {path.suffix}")


def load_transcript_source(
    path: Path,
    *,
    language: Any = "",
    transcript_quality: Any = "",
) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise TranscriptSourceError(f"transcript source file not found: {path}")

    source_kind = detect_source_kind(path)
    content = path.read_text(encoding="utf-8-sig")
    metadata: dict[str, Any] = {}

    if source_kind in {"subtitle_srt", "subtitle_vtt"}:
        segments = parse_srt_or_vtt(content)
    elif source_kind == "transcription_json":
        segments, metadata = parse_json_transcript(content)
    elif source_kind == "transcription_txt":
        segments = parse_txt_transcript(content)
    else:
        raise TranscriptSourceError(f"unsupported transcript source kind: {source_kind}")

    normalized_language = normalize_language(language or metadata.get("language") or metadata.get("transcript_language"))
    normalized_quality = normalize_transcript_quality(transcript_quality or metadata.get("transcript_quality") or metadata.get("quality"))
    segments, normalized_quality, warnings, sanitization = sanitize_transcript_segments(
        segments,
        transcript_quality=normalized_quality,
    )

    material = {
        "material_version": "watchbrief_v5.transcript_material.v1",
        "source": {
            "path": str(path),
            "kind": source_kind,
        },
        "language": normalized_language,
        "transcript_quality": normalized_quality,
        "segment_count": len(segments),
        "char_count": sum(len(segment["text"]) for segment in segments),
        "has_timestamps": True,
        "warnings": warnings,
        "sanitization": sanitization,
        "segments": copy.deepcopy(segments),
        "adapter_boundary": {
            "no_real_url_processing": True,
            "no_subtitle_download": True,
            "no_audio_download": True,
            "no_transcription": True,
            "no_model_call": True,
        },
    }
    forbidden = FINAL_REPORT_FIELDS_BLOCKED_IN_TRANSCRIPT_MATERIAL.intersection(material.keys())
    if forbidden:
        raise TranscriptSourceError(f"transcript material leaked final report fields: {sorted(forbidden)}")
    return material


def transcript_material_to_local_extract_input(metadata: dict[str, Any], material: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(material, dict):
        raise TranscriptSourceError("transcript material must be an object")
    return {
        "metadata": copy.deepcopy(metadata),
        "transcript_language": normalize_language(material.get("language")),
        "transcript_quality": normalize_transcript_quality(material.get("transcript_quality")),
        "transcript_source": clean_text(material.get("source", {}).get("kind")) or "local_file",
        "transcript_segments": copy.deepcopy(material.get("segments")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize an existing local transcript/subtitle file for WatchBrief V5.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--language", default="")
    parser.add_argument("--transcript-quality", default="")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    material = load_transcript_source(
        args.input,
        language=args.language,
        transcript_quality=args.transcript_quality,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(material, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
