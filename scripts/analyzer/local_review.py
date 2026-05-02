"""Deterministic local review provider for WatchBrief V5.

This provider is intentionally conservative. It turns Qwen local_extract output
into a schema-valid report without calling Codex or any cloud review model.
"""

from __future__ import annotations

import copy
from typing import Any

try:
    from ..scoring import SCORING_FORMULA, SCORING_FORMULA_VERSION, SCORE_TRACE_FINAL_SOURCE
    from ..validator import CODEX_REVIEW_PROMPT_VERSION, QWEN_LOCAL_EXTRACT_PROMPT_VERSION, WATCHBRIEF_VERSION
except ImportError:  # pragma: no cover - direct script execution
    from scoring import SCORING_FORMULA, SCORING_FORMULA_VERSION, SCORE_TRACE_FINAL_SOURCE
    from validator import CODEX_REVIEW_PROMPT_VERSION, QWEN_LOCAL_EXTRACT_PROMPT_VERSION, WATCHBRIEF_VERSION


LOCAL_REVIEW_MODEL_ID = "local-rules"
LOCAL_REVIEW_PROMPT_FINGERPRINT = "0" * 64


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows = [clean_text(item) for item in value]
    return [item for item in rows if item]


def short_node(value: str, fallback: str) -> str:
    text = clean_text(value) or fallback
    if len(text) <= 18:
        return text
    return text[:17] + "…"


def sentence(value: str, fallback: str) -> str:
    text = clean_text(value) or fallback
    text = text.rstrip("。！？!?；;，, ")
    if not text:
        text = fallback
    return f"{text}。"


def first_time_window(local_extract_payload: dict[str, Any]) -> dict[str, str]:
    windows = local_extract_payload.get("time_windows")
    if isinstance(windows, list):
        for window in windows:
            if isinstance(window, dict) and clean_text(window.get("start")) and clean_text(window.get("end")):
                return {
                    "start": clean_text(window.get("start")),
                    "end": clean_text(window.get("end")),
                    "excerpt": clean_text(window.get("excerpt")),
                }
    segments = local_extract_payload.get("transcript", {}).get("segments")
    if isinstance(segments, list) and segments:
        first = segments[0] if isinstance(segments[0], dict) else {}
        last = segments[-1] if isinstance(segments[-1], dict) else {}
        return {
            "start": clean_text(first.get("start")) or "00:00",
            "end": clean_text(last.get("end")) or clean_text(first.get("end")) or "00:00",
            "excerpt": clean_text(first.get("text")),
        }
    return {"start": "00:00", "end": "00:00", "excerpt": ""}


def topic_from_extract(qwen_extract: dict[str, Any]) -> str:
    terms = text_list(qwen_extract.get("important_terms"))
    if terms:
        return " / ".join(terms[:4])
    axis = clean_text(qwen_extract.get("main_axis"))
    return short_node(axis, "本地视频内容分析")


def arrow_chain_from_extract(qwen_extract: dict[str, Any]) -> list[str]:
    claims = text_list(qwen_extract.get("core_claims"))
    methods = text_list(qwen_extract.get("methods"))
    caveats = text_list(qwen_extract.get("caveats"))
    seeds = [
        "内容主轴",
        claims[0] if claims else "核心观点",
        methods[0] if methods else "关键方法",
        methods[1] if len(methods) > 1 else "实践步骤",
        caveats[0] if caveats else "边界提醒",
        "观看取舍",
    ]
    return [short_node(item, fallback) for item, fallback in zip(seeds, seeds)]


def build_local_review_response(local_extract_payload: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized report payload from local_extract without cloud review."""
    payload = copy.deepcopy(local_extract_payload) if isinstance(local_extract_payload, dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    qwen_extract = payload.get("qwen_extract") if isinstance(payload.get("qwen_extract"), dict) else {}

    title = clean_text(metadata.get("title")) or "Untitled Video"
    url = clean_text(metadata.get("url")) or ""
    channel = clean_text(metadata.get("channel")) or "未知"
    duration = clean_text(metadata.get("duration")) or "未知"
    date = clean_text(metadata.get("date")) or "未知"

    main_axis = clean_text(qwen_extract.get("main_axis")) or "这段内容围绕一个核心主题展开"
    cleaned = clean_text(qwen_extract.get("cleaned_understanding")) or main_axis
    claims = text_list(qwen_extract.get("core_claims"))
    methods = text_list(qwen_extract.get("methods"))
    examples = text_list(qwen_extract.get("examples"))
    caveats = text_list(qwen_extract.get("caveats"))
    refined_quotes = text_list(qwen_extract.get("refined_quotes"))
    primary = first_time_window(payload)
    primary_range = f"{primary['start']} | {primary['end']}"

    final_core = claims[0] if claims else main_axis
    method_text = "；".join(methods[:2]) if methods else "按内容主轴理解关键做法"
    caveat_text = "；".join(caveats[:2]) if caveats else "本地模式不会额外补充原文之外的判断"
    highest_source = refined_quotes[0] if refined_quotes else cleaned
    highest = highest_source if len(highest_source) >= 16 else f"{main_axis}，关键在于{final_core}"

    return {
        "title": title,
        "url": url,
        "channel": channel,
        "duration": duration,
        "date": date,
        "topic": topic_from_extract(qwen_extract),
        "replacement_score": 4.6,
        "tag": "只建议跳看",
        "one_line_brief": f"这期视频主要讲：{main_axis}。",
        "watch_verdict": f"看报告基本够，原视频只建议跳看 {primary_range}；如果想听原作者表达，再看这一段。",
        "highest_compression": sentence(highest, "这段内容的核心价值在于把分散表达整理成一个可以直接理解的主轴"),
        "path_table": {
            "problem": sentence(cleaned, "视频先提出一个需要理解的问题"),
            "mechanism": sentence(claims[0] if claims else main_axis, "它用核心观点解释问题背后的机制"),
            "turning_point": sentence(method_text, "关键转折是从问题进入可操作方法"),
            "landing": sentence(caveat_text, "最后落到适用边界和实际取舍"),
        },
        "arrow_chain": arrow_chain_from_extract(qwen_extract),
        "final_conclusion": sentence(final_core, "这段内容真正想强调的是把核心观点落到可理解的判断里"),
        "content_caveat": f"本地模式只基于 Qwen local_extract 和确定性规则生成，适合没有 Codex 账号时使用；细腻判断会弱于 Codex review。{caveat_text}",
        "watch_segments": [
            {
                "priority": "primary",
                "start": primary["start"],
                "end": primary["end"],
                "title": "首选片段：本地规则选出的核心段",
                "reason": f"这一段覆盖当前转写中最集中的主题表达：{clean_text(primary.get('excerpt')) or main_axis}",
            }
        ],
        "only_one_segment": f"只选一段：{primary_range}。这一段是本地规则能定位到的核心信息段。",
        "score_basis": {
            "information_density": "本地规则无法做复杂审稿，按 Qwen 中间提炼的主轴和方法数量给出保守估计。",
            "evidence_quality": "证据主要来自字幕或转写文本，未调用 Codex 做反例和论证强度复核。",
            "originality": "只根据核心观点、方法和例子判断新意，不额外引入外部知识。",
            "watch_value": "报告覆盖主轴后仍建议跳看核心段，保留原作者表达和上下文价值。",
        },
        "structured_assessment": {
            "信息密度": 4.8 if claims or methods else 4.0,
            "论据质量": 4.2 if examples else 3.8,
            "独创性": 4.2,
            "观看性价比": 4.9,
        },
        "score_trace": {
            "information_density": 0,
            "evidence_quality": 0,
            "originality": 0,
            "watch_value": 0,
            "formula": SCORING_FORMULA,
            "computed_replacement_score": 0,
            "model_suggested_score": 4.6,
            "final_score_source": SCORE_TRACE_FINAL_SOURCE,
            "warnings": ["local-rules provider used"],
        },
        "transcript_hash": clean_text(payload.get("transcript_hash")) or ("0" * 64),
        "qwen_model_id": clean_text(qwen_extract.get("_qwen_model")) or "local-qwen",
        "qwen_prompt_version": clean_text(payload.get("analysis_boundary", {}).get("qwen_prompt_version")) or QWEN_LOCAL_EXTRACT_PROMPT_VERSION,
        "qwen_prompt_fingerprint": clean_text(payload.get("analysis_boundary", {}).get("qwen_prompt_fingerprint")) or ("0" * 64),
        "codex_model": LOCAL_REVIEW_MODEL_ID,
        "codex_prompt_version": CODEX_REVIEW_PROMPT_VERSION,
        "codex_prompt_fingerprint": LOCAL_REVIEW_PROMPT_FINGERPRINT,
        "scoring_formula_version": SCORING_FORMULA_VERSION,
        "watchbrief_version": WATCHBRIEF_VERSION,
        "confidence_note": "本地模式已生成可读报告，但未调用 Codex review；适合无 Codex 账号的本机使用场景。",
    }
