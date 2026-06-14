#!/usr/bin/env python3
"""Local Qwen-family extraction from already-provided transcript segments."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from .prompts import (
        QWEN_LOCAL_EXTRACT_PROMPT_VERSION,
        QWEN_LOCAL_EXTRACT_FIELDS,
        QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT,
        TERM_LOCALIZATION_RULES,
        build_qwen_local_extract_user_prompt,
    )
    from ..entity_normalization import normalize_qwen_extract_entities
    from ..stability import prompt_fingerprint, transcript_hash_from_segments
except ImportError:  # pragma: no cover
    SCRIPTS_DIR = Path(__file__).resolve().parents[1]
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from prompts import QWEN_LOCAL_EXTRACT_PROMPT_VERSION, QWEN_LOCAL_EXTRACT_FIELDS, QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT, TERM_LOCALIZATION_RULES, build_qwen_local_extract_user_prompt
    from entity_normalization import normalize_qwen_extract_entities
    from stability import prompt_fingerprint, transcript_hash_from_segments


DEFAULT_QWEN_API_BASE = "http://127.0.0.1:1234/v1"
DEFAULT_QWEN_MODEL = "qwen3-30b-a3b-instruct-2507-mlx"
QWEN_MODEL_ENV = "WATCHBRIEF_QWEN_MODEL"
QWEN_BASE_URL_ENV = "WATCHBRIEF_QWEN_BASE_URL"
QWEN_API_BASE_ENV = "WATCHBRIEF_QWEN_API_BASE"
CHUNKED_CHAR_THRESHOLD = 30_000
CHUNKED_REQUEST_BYTES_THRESHOLD = 80_000
CHUNKED_SEGMENT_THRESHOLD = 500
CHUNK_TARGET_CHARS = 10_000
CHUNK_OVERLAP_CHARS = 500
CHUNK_MAX_RETRIES = 1
CHUNK_MIN_SUCCESS_COVERAGE = 0.75

REQUIRED_METADATA_FIELDS = ("title", "url", "channel", "duration", "date")
REQUIRED_SEGMENT_FIELDS = ("start", "end", "text")
QWEN_EXTRACT_REQUIRED_FIELDS = (
    "cleaned_understanding",
    "main_axis",
    "core_claims",
    "conditions",
    "methods",
    "examples",
    "caveats",
    "original_quotes",
    "refined_quotes",
    "transcript_quality_note",
    "language",
    "important_terms",
    "corrected_terms",
)
QWEN_EXTRACT_ARRAY_FIELDS = (
    "core_claims",
    "conditions",
    "methods",
    "examples",
    "caveats",
    "original_quotes",
    "refined_quotes",
    "important_terms",
    "corrected_terms",
)
QWEN_CHUNK_REQUIRED_FIELDS = (
    "chunk_start",
    "chunk_end",
    "core_theme",
    "useful_points",
    "skippable_content",
    "candidate_watch_segments",
    "information_density",
    "scoring_evidence",
    "transcript_quality_note",
    "language",
    "important_terms",
    "corrected_terms",
)
QWEN_CHUNK_ARRAY_FIELDS = (
    "useful_points",
    "skippable_content",
    "candidate_watch_segments",
    "scoring_evidence",
    "important_terms",
    "corrected_terms",
)
FINAL_REPORT_FIELDS_BLOCKED_IN_LOCAL_EXTRACT = {
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

QWEN_CHUNK_EXTRACT_FIELDS = {
    "chunk_start": "本 chunk 覆盖的起始时间。",
    "chunk_end": "本 chunk 覆盖的结束时间。",
    "core_theme": "这个 chunk 最核心在讲什么。",
    "useful_points": "本 chunk 中对最终报告有价值的观点数组。",
    "skippable_content": "本 chunk 中可以跳过的铺垫、重复、闲聊或低密度内容数组。",
    "candidate_watch_segments": "候选观看片段数组；每项写成 start | end：为什么这段值得看。",
    "information_density": "高 / 中 / 低，并用一句话说明依据。",
    "scoring_evidence": "对最终评分有帮助的证据数组，关注信息密度、论据质量、独创性、观看性价比。",
    "transcript_quality_note": "本 chunk 转写质量是否影响判断。",
    "language": "zh / en / mixed / unknown。",
    "important_terms": "关键术语、产品名、人名、工具名数组，保留原文。",
    "corrected_terms": "疑似 ASR 错误的专名纠错数组；每项必须是字符串：原词 -> 修正词；不要写裸箭头表达式；没有就空数组。",
}


class LocalExtractError(ValueError):
    stage = "local_extract"

    def __init__(self, message: str, *, reason_code: str = "local_extract_failed") -> None:
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"{reason_code}: {message}")


class LocalQwenError(LocalExtractError):
    stage = "local_extract"

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message, reason_code=reason_code)


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def require_string(value: Any, path: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        raise LocalExtractError(f"{path} is required")
    return cleaned


def normalize_extract_language(value: Any) -> str:
    raw = clean_text(value).lower()
    if raw in {"zh", "zh-cn", "zh-hans", "zh-hant", "zh-tw", "zh-hk", "zh-sg", "cmn", "chinese", "中文"}:
        return "zh"
    if raw in {"en", "en-us", "en-gb", "en-ca", "en-au", "english"}:
        return "en"
    if raw in {"mixed", "mix", "zh-en", "en-zh", "中英混合"}:
        return "mixed"
    return "unknown"


def normalize_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    if not isinstance(metadata, dict):
        raise LocalExtractError("metadata must be an object")
    normalized: dict[str, str] = {}
    for field in REQUIRED_METADATA_FIELDS:
        normalized[field] = require_string(metadata.get(field), f"metadata.{field}")
    return normalized


def normalize_transcript_segments(segments: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(segments, list):
        raise LocalExtractError("transcript_segments must be a list")
    if not segments:
        raise LocalExtractError("transcript_segments must not be empty")

    normalized: list[dict[str, str]] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise LocalExtractError(f"transcript_segments[{index}] must be an object")
        item = {
            field: require_string(segment.get(field), f"transcript_segments[{index}].{field}")
            for field in REQUIRED_SEGMENT_FIELDS
        }
        normalized.append(item)
    return normalized


def build_transcript_excerpt(segments: list[dict[str, str]], max_chars: int = 2400) -> str:
    rows: list[str] = []
    used = 0
    for segment in segments:
        line = f'{segment["start"]} | {segment["end"]} {segment["text"]}'
        if used + len(line) > max_chars and rows:
            break
        rows.append(line)
        used += len(line)
    return "\n".join(rows)


def build_time_windows(segments: list[dict[str, str]], window_size: int = 3, limit: int = 6) -> list[dict[str, str]]:
    windows: list[dict[str, str]] = []
    for start_index in range(0, len(segments), window_size):
        chunk = segments[start_index:start_index + window_size]
        if not chunk:
            continue
        text_blob = clean_text(" ".join(item["text"] for item in chunk))
        windows.append({
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "excerpt": text_blob[:360],
            "segment_count": str(len(chunk)),
        })
        if len(windows) >= limit:
            break
    return windows


def segment_prompt_length(segment: dict[str, str]) -> int:
    return len(f'{segment["start"]} | {segment["end"]} {segment["text"]}')


def transcript_text_char_count(segments: list[dict[str, str]]) -> int:
    return sum(len(item["text"]) for item in segments)


def estimate_qwen_request_bytes(local_extract_seed: dict[str, Any]) -> int:
    request_payload = {
        "messages": [
            {"role": "system", "content": QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": build_qwen_local_extract_user_prompt(local_extract_seed)},
        ]
    }
    return len(json.dumps(request_payload, ensure_ascii=False).encode("utf-8"))


def chunk_trigger_decision(
    local_extract_seed: dict[str, Any],
    *,
    char_threshold: int,
    request_bytes_threshold: int,
    segment_threshold: int,
) -> dict[str, Any]:
    transcript = local_extract_seed.get("transcript", {}) if isinstance(local_extract_seed.get("transcript"), dict) else {}
    char_count = int(transcript.get("char_count") or 0)
    segment_count = int(transcript.get("segment_count") or 0)
    request_bytes = estimate_qwen_request_bytes(local_extract_seed)
    reasons: list[str] = []
    if char_count > char_threshold:
        reasons.append("transcript_char_count")
    if request_bytes > request_bytes_threshold:
        reasons.append("local_extract_request_bytes")
    if segment_count > segment_threshold:
        reasons.append("segment_count")
    return {
        "enabled": bool(reasons),
        "trigger_reasons": reasons,
        "transcript_char_count": char_count,
        "segment_count": segment_count,
        "estimated_request_bytes": request_bytes,
        "thresholds": {
            "char_threshold": char_threshold,
            "request_bytes_threshold": request_bytes_threshold,
            "segment_threshold": segment_threshold,
        },
    }


def build_transcript_chunks(
    segments: list[dict[str, str]],
    *,
    target_chars: int = CHUNK_TARGET_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    start_index = 0
    total = len(segments)
    while start_index < total:
        end_index = start_index
        primary_chars = 0
        while end_index < total and (primary_chars < target_chars or end_index == start_index):
            primary_chars += segment_prompt_length(segments[end_index])
            end_index += 1

        overlap_start = start_index
        overlap_total = 0
        cursor = start_index - 1
        while cursor >= 0 and overlap_total < overlap_chars:
            overlap_total += segment_prompt_length(segments[cursor])
            overlap_start = cursor
            cursor -= 1

        primary_segments = segments[start_index:end_index]
        input_segments = segments[overlap_start:end_index]
        chunks.append({
            "index": len(chunks) + 1,
            "start_index": start_index,
            "end_index": end_index - 1,
            "input_start_index": overlap_start,
            "input_end_index": end_index - 1,
            "start": primary_segments[0]["start"],
            "end": primary_segments[-1]["end"],
            "input_start": input_segments[0]["start"],
            "input_end": input_segments[-1]["end"],
            "segment_count": len(primary_segments),
            "input_segment_count": len(input_segments),
            "char_count": sum(segment_prompt_length(item) for item in primary_segments),
            "input_char_count": sum(segment_prompt_length(item) for item in input_segments),
            "overlap_char_count": sum(segment_prompt_length(item) for item in input_segments[: start_index - overlap_start]),
            "segments": copy.deepcopy(input_segments),
        })
        start_index = end_index
    return chunks


def chunk_plan_record(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_index": chunk["index"],
        "index": chunk["index"],
        "start": chunk["start"],
        "end": chunk["end"],
        "input_start": chunk["input_start"],
        "input_end": chunk["input_end"],
        "segment_count": chunk["segment_count"],
        "input_segment_count": chunk["input_segment_count"],
        "char_count": chunk["char_count"],
        "input_char_count": chunk["input_char_count"],
        "overlap_char_count": chunk["overlap_char_count"],
        "status": "pending",
        "attempts": 0,
        "raw_response_was_non_json": False,
        "parse_error": "",
        "repair_attempted": False,
        "repair_success": False,
        "final_chunk_status": "pending",
    }


def build_chunk_seed(local_extract_seed: dict[str, Any], chunk: dict[str, Any], chunk_count: int) -> dict[str, Any]:
    transcript = local_extract_seed["transcript"]
    return {
        "local_extract_mode": "chunk",
        "metadata": copy.deepcopy(local_extract_seed["metadata"]),
        "chunk": {
            "index": chunk["index"],
            "chunk_count": chunk_count,
            "start": chunk["start"],
            "end": chunk["end"],
            "input_start": chunk["input_start"],
            "input_end": chunk["input_end"],
            "segment_count": chunk["segment_count"],
            "input_segment_count": chunk["input_segment_count"],
            "char_count": chunk["char_count"],
            "input_char_count": chunk["input_char_count"],
            "overlap_char_count": chunk["overlap_char_count"],
        },
        "transcript": {
            "language": transcript["language"],
            "quality": transcript["quality"],
            "source": transcript["source"],
            "segment_count": chunk["input_segment_count"],
            "char_count": chunk["input_char_count"],
            "has_timestamps": True,
            "excerpt": build_transcript_excerpt(chunk["segments"]),
            "segments": copy.deepcopy(chunk["segments"]),
        },
        "time_windows": build_time_windows(chunk["segments"]),
    }


def build_chunk_reduce_seed(
    local_extract_seed: dict[str, Any],
    chunk_summaries: list[dict[str, Any]],
    chunk_debug: dict[str, Any],
) -> dict[str, Any]:
    return {
        "local_extract_mode": "chunk_reduce",
        "metadata": copy.deepcopy(local_extract_seed["metadata"]),
        "transcript": {
            key: local_extract_seed["transcript"][key]
            for key in ("language", "quality", "source", "segment_count", "char_count", "has_timestamps", "excerpt")
        },
        "time_windows": copy.deepcopy(local_extract_seed["time_windows"]),
        "chunked_local_extract": {
            "chunk_count": chunk_debug["chunk_count"],
            "successful_chunk_count": chunk_debug["successful_chunk_count"],
            "failed_chunk_count": chunk_debug["failed_chunk_count"],
            "success_coverage": chunk_debug["success_coverage"],
        },
        "chunk_summaries": copy.deepcopy(chunk_summaries),
    }


def build_qwen_chunk_extract_user_prompt(chunk_seed: dict[str, Any]) -> str:
    language = str(chunk_seed.get("transcript", {}).get("language") or "unknown")
    language_note = ""
    if language == "en":
        language_note = (
            "\n英文 transcript 规则：Read the original English transcript directly. "
            "Do not first translate the whole transcript. Output the extracted analysis in Chinese. 输出中文结构化提炼。"
            "营销、产品、创作者领域的普通术语要自然中文化；保留英文只限标题、频道名、品牌名、产品名、人名、工具名或无法自然翻译的原词。"
            "必要时第一次写“中文（English）”，后续只用中文。\n"
        )
    elif language == "mixed":
        language_note = "\n中英混合 transcript 规则：直接阅读原始混合文本，输出中文结构化提炼；领域术语要自然中文化，保留英文只限专名或无法自然翻译的原词。\n"
    return (
        "请根据下面的转写 chunk 输出轻量 chunk_summary JSON。\n"
        "只能输出 JSON 对象，不要 Markdown，不要解释。\n"
        "这是 local_extract 的分块中间结果，不是最终 WatchBrief V5 报告。\n"
        "不得输出 replacement_score、tag、watch_verdict、final_conclusion、watch_segments、long_content_breakdown 或 HTML。\n"
        "保留原始时间码，候选观看片段必须写清 start | end。\n"
        f"{TERM_LOCALIZATION_RULES}\n"
        f"{language_note}\n"
        "必须包含这些字段：\n"
        f"{json.dumps(QWEN_CHUNK_EXTRACT_FIELDS, ensure_ascii=False, indent=2)}\n\n"
        "转写 chunk：\n"
        f"{json.dumps(chunk_seed, ensure_ascii=False, indent=2)}"
    )


def build_qwen_chunk_repair_user_prompt(raw_content: str, chunk: dict[str, Any]) -> str:
    return (
        "上一轮 chunk_summary 输出不是合法 JSON。\n"
        "你的任务只是在不重新分析转写、不新增观点的前提下，把上一轮内容修成一个合法 JSON 对象。\n"
        "只能输出 JSON 对象，不要 Markdown，不要解释，不要代码块。\n"
        "如果上一轮内容里缺少某个必填字段：字符串字段用空字符串，数组字段用空数组；不要编造新内容。\n"
        "必须包含这些字段，且字段名必须完全一致：\n"
        f"{json.dumps(QWEN_CHUNK_EXTRACT_FIELDS, ensure_ascii=False, indent=2)}\n\n"
        "当前 chunk 元信息：\n"
        f"{json.dumps({'chunk_index': chunk['index'], 'chunk_start': chunk['start'], 'chunk_end': chunk['end']}, ensure_ascii=False, indent=2)}\n\n"
        "上一轮原始输出：\n"
        f"{str(raw_content or '').strip()}"
    )


def build_qwen_chunk_reduce_user_prompt(reduce_seed: dict[str, Any]) -> str:
    language = str(reduce_seed.get("transcript", {}).get("language") or "unknown")
    language_note = ""
    if language == "en":
        language_note = (
            "\n英文 transcript 规则：Read the original English transcript directly from chunk summaries. "
            "Do not translate the whole transcript. Output Chinese structured extraction. 输出中文结构化提炼。"
            "营销、产品、创作者领域的普通术语要自然中文化；保留英文只限标题、频道名、品牌名、产品名、人名、工具名或无法自然翻译的原词。"
            "必要时第一次写“中文（English）”，后续只用中文。\n"
        )
    elif language == "mixed":
        language_note = "\n中英混合 transcript 规则：按 chunk summaries 直接理解原文，输出中文结构化提炼；领域术语要自然中文化，保留英文只限专名或无法自然翻译的原词。\n"
    return (
        "请把下面所有 chunk_summary 归并成一个 Qwen local_extract intermediate JSON。\n"
        "只能输出 JSON 对象，不要 Markdown，不要解释。\n"
        "这是中间提炼结果，不是最终 WatchBrief V5 报告。\n"
        "不得输出 replacement_score、tag、watch_verdict、final_conclusion、watch_segments、long_content_breakdown 或 HTML。\n"
        "不要按 chunk 机械拼接，要归并重复观点，保留能帮助最终评分和片段选择的证据。\n"
        f"{TERM_LOCALIZATION_RULES}\n"
        f"{language_note}\n"
        "必须包含这些字段：\n"
        f"{json.dumps(QWEN_LOCAL_EXTRACT_FIELDS, ensure_ascii=False, indent=2)}\n\n"
        "chunk summaries：\n"
        f"{json.dumps(reduce_seed, ensure_ascii=False, indent=2)}"
    )


def is_qwen_model_id(model_id: str) -> bool:
    return "qwen" in str(model_id or "").lower()


def configured_qwen_api_base(api_base: Optional[str] = None) -> str:
    return str(
        os.environ.get(QWEN_BASE_URL_ENV)
        or api_base
        or os.environ.get(QWEN_API_BASE_ENV)
        or DEFAULT_QWEN_API_BASE
    ).rstrip("/")


def configured_qwen_model(model_id: Optional[str] = None) -> str:
    return str(model_id or os.environ.get(QWEN_MODEL_ENV) or DEFAULT_QWEN_MODEL).strip()


def is_timeout_exception(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", "")
        return "timed out" in str(reason or exc).lower() or "timeout" in str(reason or exc).lower()
    return "timed out" in str(exc).lower()


def write_debug_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_debug_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(content or ""), encoding="utf-8")


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[dict[str, Any]] = None,
    urlopen_func: Any = urllib.request.urlopen,
    timeout: int = 60,
    raw_response_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen_func(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (TimeoutError, socket.timeout) as exc:
        raise LocalQwenError("local_qwen_timeout", f"Qwen request timed out after {timeout}s") from exc
    except urllib.error.URLError as exc:
        if is_timeout_exception(exc):
            raise LocalQwenError("local_qwen_timeout", f"Qwen request timed out after {timeout}s") from exc
        raise LocalQwenError("local_qwen_unavailable", f"Qwen endpoint unavailable: {exc}") from exc
    if raw_response_callback:
        raw_response_callback(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalQwenError("local_qwen_invalid_response", "Qwen endpoint returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise LocalQwenError("local_qwen_model_error", "LM Studio returned non-object JSON")
    return data


def list_lm_studio_models(
    *,
    api_base: Optional[str] = None,
    urlopen_func: Any = urllib.request.urlopen,
    timeout: int = 30,
) -> list[str]:
    data = http_json(
        f"{configured_qwen_api_base(api_base)}/models",
        urlopen_func=urlopen_func,
        timeout=timeout,
    )
    rows = data.get("data")
    if not isinstance(rows, list):
        return []
    models: list[str] = []
    for item in rows:
        if isinstance(item, dict) and item.get("id"):
            models.append(str(item["id"]))
    return models


def choose_qwen_model(
    *,
    model_id: Optional[str] = None,
    api_base: Optional[str] = None,
    urlopen_func: Any = urllib.request.urlopen,
    timeout: int = 30,
) -> str:
    configured = configured_qwen_model(model_id)
    if configured:
        if not is_qwen_model_id(configured):
            raise LocalQwenError("non_qwen_model_rejected", f"local_extract requires a Qwen-family model, got: {configured}")
        return configured
    for candidate in list_lm_studio_models(api_base=api_base, urlopen_func=urlopen_func, timeout=timeout):
        if is_qwen_model_id(candidate):
            return candidate
    raise LocalQwenError("local_qwen_unavailable", "No Qwen-family model is available in LM Studio")


def extract_json_object_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        raise LocalQwenError("local_qwen_invalid_response", "Qwen returned empty text")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    normalized_text = normalize_json_like_text(text)
    try:
        parsed = json.loads(normalized_text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(normalized_text):
            if char != "{":
                continue
            try:
                parsed, end = decoder.raw_decode(normalized_text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return normalized_text[index:index + end]
        raise LocalQwenError("local_qwen_invalid_response", "Qwen response did not contain a JSON object")
    if not isinstance(parsed, dict):
        raise LocalQwenError("local_qwen_invalid_response", "Qwen response JSON must be an object")
    return normalized_text


def normalize_json_like_text(text: str) -> str:
    def replace_bare_arrow(match: re.Match[str]) -> str:
        return json.dumps(f"{match.group(1)} -> {match.group(2)}", ensure_ascii=False)

    normalized = re.sub(r'"([^"\n]+)"\s*→\s*"([^"\n]+)"', replace_bare_arrow, str(text or ""))
    # Qwen occasionally emits adjacent JSON array strings without a comma, e.g.
    # ["第一句"\n"第二句"].  Repair only the narrow string-string boundary; this
    # preserves valid JSON and fixes the observed local_qwen_invalid_response case.
    normalized = re.sub(r'("(?:[^"\\]|\\.)*")\s+(?=")', r'\1, ', normalized)
    return normalized


def is_strict_json_object_text(raw_text: str) -> bool:
    try:
        parsed = json.loads(str(raw_text or "").strip())
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict)


def parse_qwen_message_json(raw_text: str) -> dict[str, Any]:
    extracted = extract_json_object_text(raw_text)
    try:
        parsed = json.loads(extracted)
    except json.JSONDecodeError as exc:
        raise LocalQwenError("local_qwen_invalid_response", f"Qwen response JSON parse failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LocalQwenError("local_qwen_invalid_response", "Qwen response JSON must be an object")
    return parsed


def extract_message_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    raise LocalQwenError("local_qwen_invalid_response", "Qwen chat response did not contain message content")


def validate_qwen_extract(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LocalExtractError("Qwen intermediate output must be an object", reason_code="local_extract_invalid_output")
    forbidden = FINAL_REPORT_FIELDS_BLOCKED_IN_LOCAL_EXTRACT.intersection(payload.keys())
    if forbidden:
        raise LocalExtractError(
            f"Qwen intermediate output leaked final report fields: {sorted(forbidden)}",
            reason_code="local_extract_invalid_output",
        )
    missing = [field for field in QWEN_EXTRACT_REQUIRED_FIELDS if field not in payload]
    if missing:
        raise LocalExtractError(
            f"Qwen intermediate output missing fields: {missing}",
            reason_code="local_extract_invalid_output",
        )
    normalized = copy.deepcopy(payload)
    for field in ("cleaned_understanding", "main_axis", "transcript_quality_note", "language"):
        normalized[field] = clean_text(normalized.get(field))
    normalized["language"] = normalize_extract_language(normalized.get("language"))
    for field in QWEN_EXTRACT_ARRAY_FIELDS:
        value = normalized.get(field)
        if not isinstance(value, list):
            raise LocalExtractError(
                f"Qwen intermediate field must be array: {field}",
                reason_code="local_extract_invalid_output",
            )
        normalized[field] = [clean_text(item) for item in value if clean_text(item)]
    return normalized


def clean_chunk_array_item(item: Any) -> str:
    if isinstance(item, dict):
        start = clean_text(item.get("start"))
        end = clean_text(item.get("end"))
        reason = clean_text(item.get("reason") or item.get("value") or item.get("text") or item.get("summary"))
        if start and end and reason:
            return f"{start} | {end}：{reason}"
        if start and end:
            return f"{start} | {end}"
        if reason:
            return reason
        return clean_text(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return clean_text(item)


def validate_qwen_chunk_summary(payload: dict[str, Any], chunk: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LocalExtractError("Qwen chunk output must be an object", reason_code="local_extract_invalid_output")
    forbidden = FINAL_REPORT_FIELDS_BLOCKED_IN_LOCAL_EXTRACT.intersection(payload.keys())
    if forbidden:
        raise LocalExtractError(
            f"Qwen chunk output leaked final report fields: {sorted(forbidden)}",
            reason_code="local_extract_invalid_output",
        )
    missing = [field for field in QWEN_CHUNK_REQUIRED_FIELDS if field not in payload]
    if missing:
        raise LocalExtractError(
            f"Qwen chunk output missing fields: {missing}",
            reason_code="local_extract_invalid_output",
        )
    normalized = copy.deepcopy(payload)
    for field in ("chunk_start", "chunk_end", "core_theme", "information_density", "transcript_quality_note", "language"):
        normalized[field] = clean_text(normalized.get(field))
    normalized["chunk_start"] = normalized["chunk_start"] or str(chunk["start"])
    normalized["chunk_end"] = normalized["chunk_end"] or str(chunk["end"])
    normalized["language"] = normalize_extract_language(normalized.get("language"))
    for field in QWEN_CHUNK_ARRAY_FIELDS:
        value = normalized.get(field)
        if not isinstance(value, list):
            raise LocalExtractError(
                f"Qwen chunk field must be array: {field}",
                reason_code="local_extract_invalid_output",
            )
        normalized[field] = [clean_chunk_array_item(item) for item in value if clean_chunk_array_item(item)]
    normalized["_chunk_index"] = int(chunk["index"])
    normalized["_chunk_start"] = str(chunk["start"])
    normalized["_chunk_end"] = str(chunk["end"])
    normalized["_chunk_char_count"] = int(chunk["char_count"])
    return normalized


def build_qwen_chat_request(selected_model: str, user_prompt: str) -> dict[str, Any]:
    return {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "reasoning_effort": "none",
        "stream": False,
    }


def call_qwen_prompt_json(
    user_prompt: str,
    *,
    selected_model: str,
    api_base: Optional[str],
    urlopen_func: Any,
    timeout: int,
    debug_request_path: Optional[Path] = None,
    debug_raw_response_path: Optional[Path] = None,
) -> dict[str, Any]:
    raw_content = call_qwen_prompt_text(
        user_prompt,
        selected_model=selected_model,
        api_base=api_base,
        urlopen_func=urlopen_func,
        timeout=timeout,
        debug_request_path=debug_request_path,
        debug_raw_response_path=debug_raw_response_path,
    )
    return parse_qwen_message_json(raw_content)


def call_qwen_prompt_text(
    user_prompt: str,
    *,
    selected_model: str,
    api_base: Optional[str],
    urlopen_func: Any,
    timeout: int,
    debug_request_path: Optional[Path] = None,
    debug_raw_response_path: Optional[Path] = None,
) -> str:
    request_payload = build_qwen_chat_request(selected_model, user_prompt)
    if debug_request_path:
        write_debug_json(
            debug_request_path,
            {
                "qwen_api_base": configured_qwen_api_base(api_base),
                "qwen_model": selected_model,
                "timeout": timeout,
                "request": request_payload,
            },
        )

    def capture_raw_response(raw: str) -> None:
        if debug_raw_response_path:
            write_debug_text(debug_raw_response_path, raw)

    response = http_json(
        f"{configured_qwen_api_base(api_base)}/chat/completions",
        method="POST",
        payload=request_payload,
        urlopen_func=urlopen_func,
        timeout=timeout,
        raw_response_callback=capture_raw_response,
    )
    return extract_message_text(response)


def call_qwen_chunk_prompt_json_with_repair(
    chunk_seed: dict[str, Any],
    chunk: dict[str, Any],
    record: dict[str, Any],
    *,
    selected_model: str,
    api_base: Optional[str],
    urlopen_func: Any,
    timeout: int,
    debug_request_path: Optional[Path] = None,
    debug_raw_response_path: Optional[Path] = None,
    debug_repair_request_path: Optional[Path] = None,
    debug_repair_raw_response_path: Optional[Path] = None,
) -> dict[str, Any]:
    raw_content = call_qwen_prompt_text(
        build_qwen_chunk_extract_user_prompt(chunk_seed),
        selected_model=selected_model,
        api_base=api_base,
        urlopen_func=urlopen_func,
        timeout=timeout,
        debug_request_path=debug_request_path,
        debug_raw_response_path=debug_raw_response_path,
    )
    record["raw_response_was_non_json"] = not is_strict_json_object_text(raw_content)
    try:
        parsed = parse_qwen_message_json(raw_content)
        record["parse_error"] = ""
        return parsed
    except LocalQwenError as parse_exc:
        record["raw_response_was_non_json"] = True
        record["parse_error"] = parse_exc.message
        record["repair_attempted"] = True

    repair_content = call_qwen_prompt_text(
        build_qwen_chunk_repair_user_prompt(raw_content, chunk),
        selected_model=selected_model,
        api_base=api_base,
        urlopen_func=urlopen_func,
        timeout=timeout,
        debug_request_path=debug_repair_request_path,
        debug_raw_response_path=debug_repair_raw_response_path,
    )
    try:
        parsed = parse_qwen_message_json(repair_content)
    except LocalQwenError as repair_exc:
        record["repair_success"] = False
        record["parse_error"] = f"{record.get('parse_error', '')}; repair: {repair_exc.message}".strip("; ")
        raise repair_exc
    record["repair_success"] = True
    return parsed


def call_qwen_local_extract(
    local_extract_seed: dict[str, Any],
    *,
    model_id: Optional[str] = None,
    api_base: Optional[str] = None,
    urlopen_func: Any = urllib.request.urlopen,
    timeout: int = 120,
    debug_dir: Optional[Path] = None,
) -> dict[str, Any]:
    selected_model = choose_qwen_model(
        model_id=model_id,
        api_base=api_base,
        urlopen_func=urlopen_func,
        timeout=min(timeout, 30),
    )
    debug_root = Path(debug_dir) if debug_dir else None
    parsed = call_qwen_prompt_json(
        build_qwen_local_extract_user_prompt(local_extract_seed),
        selected_model=selected_model,
        api_base=api_base,
        urlopen_func=urlopen_func,
        timeout=timeout,
        debug_request_path=(debug_root / "local_extract_request.json") if debug_root else None,
        debug_raw_response_path=(debug_root / "local_extract_raw_response.txt") if debug_root else None,
    )
    normalized = validate_qwen_extract(parsed)
    normalized["_qwen_model"] = selected_model
    if debug_root:
        write_debug_json(debug_root / "local_extract_adapted.json", normalized)
    return normalized


def _dedupe_strings(values: list[Any], *, limit: int = 12) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
        if len(deduped) >= limit:
            break
    return deduped


def build_deterministic_chunk_reduce_fallback(
    reduce_seed: dict[str, Any],
    chunk_summaries: list[dict[str, Any]],
    *,
    selected_model: str,
) -> dict[str, Any]:
    """Merge completed chunk summaries without another model call.

    The chunk stage has already paid the expensive Qwen analysis cost.  If the
    final reduce call stalls, failing the whole item wastes good chunk evidence.
    This fallback preserves the intermediate local_extract contract so Codex can
    still make the final WatchBrief judgment from the available evidence.
    """
    transcript = reduce_seed.get("transcript") if isinstance(reduce_seed.get("transcript"), dict) else {}
    useful_points: list[Any] = []
    skippable: list[Any] = []
    candidate_segments: list[Any] = []
    scoring_evidence: list[Any] = []
    terms: list[Any] = []
    corrections: list[Any] = []
    themes: list[Any] = []
    quality_notes: list[Any] = []
    for summary in chunk_summaries:
        if not isinstance(summary, dict):
            continue
        themes.append(summary.get("core_theme"))
        useful_points.extend(summary.get("useful_points") or [])
        skippable.extend(summary.get("skippable_content") or [])
        candidate_segments.extend(summary.get("candidate_watch_segments") or [])
        scoring_evidence.extend(summary.get("scoring_evidence") or [])
        terms.extend(summary.get("important_terms") or [])
        corrections.extend(summary.get("corrected_terms") or [])
        quality_notes.append(summary.get("transcript_quality_note"))

    core_claims = _dedupe_strings(themes + useful_points, limit=10)
    methods = _dedupe_strings(useful_points + candidate_segments, limit=10)
    evidence = _dedupe_strings(scoring_evidence + candidate_segments, limit=10)
    caveats = _dedupe_strings(skippable, limit=8)
    payload = {
        "cleaned_understanding": "；".join(core_claims[:4]) or "已基于分块转写完成局部提炼，最终判断应以分块证据为准。",
        "main_axis": core_claims[0] if core_claims else "分块证据归并后的主题轴线。",
        "core_claims": core_claims,
        "conditions": evidence[:6],
        "methods": methods,
        "examples": evidence[:6],
        "caveats": caveats,
        "original_quotes": [],
        "refined_quotes": _dedupe_strings(useful_points, limit=8),
        "transcript_quality_note": "；".join(_dedupe_strings(quality_notes, limit=4)) or "分块转写可读；reduce 阶段使用确定性归并兜底。",
        "language": transcript.get("language") or "unknown",
        "important_terms": _dedupe_strings(terms, limit=20),
        "corrected_terms": _dedupe_strings(corrections, limit=20),
    }
    normalized = validate_qwen_extract(payload)
    normalized["_qwen_model"] = selected_model
    normalized["_local_extract_mode"] = "chunked_fallback"
    normalized["_fallback_reason"] = "chunk_reduce_model_call_failed"
    return normalized


def call_qwen_chunked_local_extract(
    local_extract_seed: dict[str, Any],
    normalized_segments: list[dict[str, str]],
    *,
    decision: dict[str, Any],
    qwen_extractor: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    model_id: Optional[str] = None,
    api_base: Optional[str] = None,
    urlopen_func: Any = urllib.request.urlopen,
    timeout: int = 120,
    debug_dir: Optional[Path] = None,
    chunk_target_chars: int = CHUNK_TARGET_CHARS,
    chunk_overlap_chars: int = CHUNK_OVERLAP_CHARS,
    chunk_max_retries: int = CHUNK_MAX_RETRIES,
    min_success_coverage: float = CHUNK_MIN_SUCCESS_COVERAGE,
) -> dict[str, Any]:
    selected_model = configured_qwen_model(model_id)
    if qwen_extractor is None:
        selected_model = choose_qwen_model(
            model_id=model_id,
            api_base=api_base,
            urlopen_func=urlopen_func,
            timeout=min(timeout, 30),
        )
    chunks = build_transcript_chunks(
        normalized_segments,
        target_chars=chunk_target_chars,
        overlap_chars=chunk_overlap_chars,
    )
    debug_root = Path(debug_dir) if debug_dir else None
    chunk_debug: dict[str, Any] = {
        "enabled": True,
        "trigger_reasons": list(decision.get("trigger_reasons") or []),
        "transcript_char_count": int(decision.get("transcript_char_count") or 0),
        "segment_count": int(decision.get("segment_count") or 0),
        "estimated_request_bytes": int(decision.get("estimated_request_bytes") or 0),
        "thresholds": copy.deepcopy(decision.get("thresholds") or {}),
        "chunk_target_chars": chunk_target_chars,
        "chunk_overlap_chars": chunk_overlap_chars,
        "chunk_count": len(chunks),
        "successful_chunk_count": 0,
        "failed_chunk_count": 0,
        "success_coverage": 0.0,
        "min_success_coverage": min_success_coverage,
        "chunks": [chunk_plan_record(chunk) for chunk in chunks],
        "reduce": {"status": "pending"},
    }

    def write_plan() -> None:
        if debug_root:
            write_debug_json(debug_root / "local_extract_chunked_plan.json", chunk_debug)

    write_plan()
    chunk_summaries: list[dict[str, Any]] = []
    successful_chars = 0
    for chunk in chunks:
        record = chunk_debug["chunks"][int(chunk["index"]) - 1]
        chunk_seed = build_chunk_seed(local_extract_seed, chunk, len(chunks))
        last_error = ""
        for attempt in range(1, chunk_max_retries + 2):
            record["attempts"] = attempt
            try:
                if qwen_extractor:
                    parsed = copy.deepcopy(qwen_extractor(chunk_seed))
                else:
                    chunk_dir = debug_root / "local_extract_chunks" if debug_root else None
                    parsed = call_qwen_chunk_prompt_json_with_repair(
                        chunk_seed,
                        chunk,
                        record,
                        selected_model=selected_model,
                        api_base=api_base,
                        urlopen_func=urlopen_func,
                        timeout=timeout,
                        debug_request_path=(chunk_dir / f"chunk_{int(chunk['index']):03d}_attempt_{attempt}_request.json") if chunk_dir else None,
                        debug_raw_response_path=(chunk_dir / f"chunk_{int(chunk['index']):03d}_attempt_{attempt}_raw_response.txt") if chunk_dir else None,
                        debug_repair_request_path=(chunk_dir / f"chunk_{int(chunk['index']):03d}_attempt_{attempt}_repair_request.json") if chunk_dir else None,
                        debug_repair_raw_response_path=(chunk_dir / f"chunk_{int(chunk['index']):03d}_attempt_{attempt}_repair_raw_response.txt") if chunk_dir else None,
                    )
                normalized = validate_qwen_chunk_summary(parsed, chunk)
                normalized["_qwen_model"] = selected_model
                chunk_summaries.append(normalized)
                successful_chars += int(chunk["char_count"])
                record["status"] = "completed"
                record["final_chunk_status"] = "completed"
                record["error"] = ""
                if debug_root:
                    write_debug_json(
                        debug_root / "local_extract_chunks" / f"chunk_{int(chunk['index']):03d}_adapted.json",
                        normalized,
                    )
                break
            except LocalQwenError as exc:
                last_error = exc.message
                record["status"] = "retrying" if exc.reason_code == "local_qwen_timeout" and attempt <= chunk_max_retries else "failed"
                record["final_chunk_status"] = record["status"]
                record["reason_code"] = exc.reason_code
                record["error"] = exc.message
                write_plan()
                if exc.reason_code == "local_qwen_timeout" and attempt <= chunk_max_retries:
                    continue
                break
            except LocalExtractError as exc:
                last_error = exc.message
                record["status"] = "failed"
                record["final_chunk_status"] = "failed"
                record["reason_code"] = exc.reason_code
                record["error"] = exc.message
                break
        if record["status"] != "completed":
            record["error"] = last_error or record.get("error", "chunk failed")
            record["final_chunk_status"] = "failed"
        write_plan()

    total_chars = max(1, sum(int(chunk["char_count"]) for chunk in chunks))
    chunk_debug["successful_chunk_count"] = sum(1 for item in chunk_debug["chunks"] if item["status"] == "completed")
    chunk_debug["failed_chunk_count"] = len(chunks) - int(chunk_debug["successful_chunk_count"])
    chunk_debug["success_coverage"] = round(successful_chars / total_chars, 4)
    if not chunk_summaries:
        chunk_debug["reduce"] = {"status": "skipped", "reason_code": "chunked_local_extract_failed", "error": "no chunk summaries succeeded"}
        write_plan()
        raise LocalQwenError("chunked_local_extract_failed", "no chunk summaries succeeded")
    if float(chunk_debug["success_coverage"]) < min_success_coverage:
        message = (
            f"successful chunk coverage {chunk_debug['success_coverage']:.2f} "
            f"below required {min_success_coverage:.2f}"
        )
        chunk_debug["reduce"] = {"status": "skipped", "reason_code": "chunked_local_extract_failed", "error": message}
        write_plan()
        raise LocalQwenError("chunked_local_extract_failed", message)

    reduce_seed = build_chunk_reduce_seed(local_extract_seed, chunk_summaries, chunk_debug)
    try:
        if qwen_extractor:
            parsed_reduce = copy.deepcopy(qwen_extractor(reduce_seed))
        else:
            parsed_reduce = call_qwen_prompt_json(
                build_qwen_chunk_reduce_user_prompt(reduce_seed),
                selected_model=selected_model,
                api_base=api_base,
                urlopen_func=urlopen_func,
                timeout=timeout,
                debug_request_path=(debug_root / "local_extract_chunked_reduce_request.json") if debug_root else None,
                debug_raw_response_path=(debug_root / "local_extract_chunked_reduce_raw_response.txt") if debug_root else None,
            )
        reduced = validate_qwen_extract(parsed_reduce)
    except LocalQwenError as exc:
        reduced = build_deterministic_chunk_reduce_fallback(
            reduce_seed,
            chunk_summaries,
            selected_model=selected_model,
        )
        reduced["_chunk_count"] = len(chunks)
        reduced["_successful_chunk_count"] = int(chunk_debug["successful_chunk_count"])
        reduced["_failed_chunk_count"] = int(chunk_debug["failed_chunk_count"])
        chunk_debug["reduce"] = {
            "status": "fallback_completed",
            "reason_code": exc.reason_code,
            "error": exc.message,
        }
        if debug_root:
            write_debug_json(debug_root / "local_extract_chunked_reduce_fallback.json", reduced)
        write_plan()
        reduced["_chunked_local_extract"] = copy.deepcopy(chunk_debug)
        return reduced
    except LocalExtractError as exc:
        chunk_debug["reduce"] = {"status": "failed", "reason_code": exc.reason_code, "error": exc.message}
        write_plan()
        raise

    reduced["_qwen_model"] = selected_model
    reduced["_local_extract_mode"] = "chunked"
    reduced["_chunk_count"] = len(chunks)
    reduced["_successful_chunk_count"] = int(chunk_debug["successful_chunk_count"])
    reduced["_failed_chunk_count"] = int(chunk_debug["failed_chunk_count"])
    chunk_debug["reduce"] = {"status": "completed"}
    if debug_root:
        write_debug_json(debug_root / "local_extract_chunked_reduce_adapted.json", reduced)
    write_plan()
    reduced["_chunked_local_extract"] = copy.deepcopy(chunk_debug)
    return reduced


def build_local_extract_payload(
    metadata: dict[str, Any],
    transcript_segments: list[dict[str, Any]],
    *,
    transcript_language: str = "",
    transcript_quality: str = "ok",
    transcript_source: str = "mock",
    qwen_extractor: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    qwen_model: Optional[str] = None,
    qwen_api_base: Optional[str] = None,
    qwen_timeout: int = 120,
    debug_dir: Optional[Path] = None,
    urlopen_func: Any = urllib.request.urlopen,
    chunked_char_threshold: int = CHUNKED_CHAR_THRESHOLD,
    chunked_request_bytes_threshold: int = CHUNKED_REQUEST_BYTES_THRESHOLD,
    chunked_segment_threshold: int = CHUNKED_SEGMENT_THRESHOLD,
    chunk_target_chars: int = CHUNK_TARGET_CHARS,
    chunk_overlap_chars: int = CHUNK_OVERLAP_CHARS,
    chunk_max_retries: int = CHUNK_MAX_RETRIES,
    chunk_min_success_coverage: float = CHUNK_MIN_SUCCESS_COVERAGE,
) -> dict[str, Any]:
    normalized_metadata = normalize_metadata(metadata)
    normalized_segments = normalize_transcript_segments(transcript_segments)
    normalized_language = normalize_extract_language(transcript_language)
    local_extract_seed = {
        "metadata": normalized_metadata,
        "transcript": {
            "language": normalized_language,
            "quality": clean_text(transcript_quality) or "ok",
            "source": clean_text(transcript_source) or "mock",
            "segment_count": len(normalized_segments),
            "char_count": transcript_text_char_count(normalized_segments),
            "has_timestamps": True,
            "excerpt": build_transcript_excerpt(normalized_segments),
            "segments": copy.deepcopy(normalized_segments),
        },
        "time_windows": build_time_windows(normalized_segments),
    }
    decision = chunk_trigger_decision(
        local_extract_seed,
        char_threshold=chunked_char_threshold,
        request_bytes_threshold=chunked_request_bytes_threshold,
        segment_threshold=chunked_segment_threshold,
    )
    if decision["enabled"]:
        qwen_extract = call_qwen_chunked_local_extract(
            local_extract_seed,
            normalized_segments,
            decision=decision,
            qwen_extractor=qwen_extractor,
            model_id=qwen_model,
            api_base=qwen_api_base,
            urlopen_func=urlopen_func,
            timeout=qwen_timeout,
            debug_dir=debug_dir,
            chunk_target_chars=chunk_target_chars,
            chunk_overlap_chars=chunk_overlap_chars,
            chunk_max_retries=chunk_max_retries,
            min_success_coverage=chunk_min_success_coverage,
        )
    else:
        qwen_extract = qwen_extractor(local_extract_seed) if qwen_extractor else call_qwen_local_extract(
            local_extract_seed,
            model_id=qwen_model,
            api_base=qwen_api_base,
            urlopen_func=urlopen_func,
            timeout=qwen_timeout,
            debug_dir=debug_dir,
        )
    chunked_local_extract = qwen_extract.pop("_chunked_local_extract", None) if isinstance(qwen_extract, dict) else None
    qwen_extract = normalize_qwen_extract_entities(validate_qwen_extract(qwen_extract))
    qwen_extract.setdefault("_qwen_model", configured_qwen_model(qwen_model) or "auto")
    transcript_hash = transcript_hash_from_segments(normalized_segments)
    qwen_prompt_fingerprint = prompt_fingerprint(
        QWEN_LOCAL_EXTRACT_PROMPT_VERSION,
        QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT,
        build_qwen_local_extract_user_prompt(local_extract_seed),
    )
    payload = {
        "extract_version": "watchbrief_v5.local_extract.v1",
        "transcript_hash": transcript_hash,
        **local_extract_seed,
        "qwen_extract": qwen_extract,
        "analysis_boundary": {
            "local_qwen_model_call": True,
            "qwen_family_only": True,
            "no_real_url_processing": True,
            "does_not_generate_final_report_fields": True,
            "qwen_prompt_version": QWEN_LOCAL_EXTRACT_PROMPT_VERSION,
            "qwen_prompt_fingerprint": qwen_prompt_fingerprint,
        },
    }
    if chunked_local_extract:
        payload["chunked_local_extract"] = chunked_local_extract
    forbidden = FINAL_REPORT_FIELDS_BLOCKED_IN_LOCAL_EXTRACT.intersection(payload.keys())
    if forbidden:
        raise LocalExtractError(f"local extract leaked final report fields: {sorted(forbidden)}")
    return payload


def _load_input(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise LocalExtractError("input JSON must be an object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a WatchBrief V5 local Qwen extract payload from transcript JSON.")
    parser.add_argument("--input", required=True, type=Path, help="JSON with metadata and transcript_segments")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--qwen-model", help=f"Qwen-family model id. Defaults to ${QWEN_MODEL_ENV} or {DEFAULT_QWEN_MODEL}.")
    parser.add_argument("--qwen-api-base", default="", help=f"LM Studio API base. Defaults to ${QWEN_API_BASE_ENV} or {DEFAULT_QWEN_API_BASE}.")
    parser.add_argument("--qwen-timeout", type=int, default=120, help="Qwen local_extract timeout seconds.")
    args = parser.parse_args()

    data = _load_input(args.input)
    payload = build_local_extract_payload(
        data.get("metadata"),
        data.get("transcript_segments"),
        transcript_language=data.get("transcript_language", ""),
        transcript_quality=data.get("transcript_quality", "ok"),
        transcript_source=data.get("transcript_source", "mock"),
        qwen_model=args.qwen_model,
        qwen_api_base=args.qwen_api_base or None,
        qwen_timeout=args.qwen_timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
