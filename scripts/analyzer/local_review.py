"""Deterministic local review provider for WatchBrief V5.

This provider is intentionally conservative. It turns Qwen local_extract output
into a schema-valid report without calling Codex or any cloud review model.
"""

from __future__ import annotations

import copy
import re
from typing import Any

try:
    from ..report_targets import DEFAULT_REPORT_TARGET, normalize_report_target, report_target_section_keys
    from ..scoring import SCORING_FORMULA, SCORING_FORMULA_VERSION, SCORE_TRACE_FINAL_SOURCE
    from ..validator import CODEX_REVIEW_PROMPT_VERSION, QWEN_LOCAL_EXTRACT_PROMPT_VERSION, WATCHBRIEF_VERSION
except ImportError:  # pragma: no cover - direct script execution
    from report_targets import DEFAULT_REPORT_TARGET, normalize_report_target, report_target_section_keys
    from scoring import SCORING_FORMULA, SCORING_FORMULA_VERSION, SCORE_TRACE_FINAL_SOURCE
    from validator import CODEX_REVIEW_PROMPT_VERSION, QWEN_LOCAL_EXTRACT_PROMPT_VERSION, WATCHBRIEF_VERSION


LOCAL_REVIEW_MODEL_ID = "local-rules"
LOCAL_REVIEW_PROMPT_FINGERPRINT = "d3b5dc0049fa4205742bfbddc978545183dbbf8fced5e73f928334603656e004"


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



def bounded_score(base: float, *signals: float, minimum: float = 2.5, maximum: float = 7.2) -> float:
    """Return a one-decimal local-rules score from bounded evidence signals.

    Local review is a fallback when no real review model is used. It must be
    deterministic, but it must not collapse every successful report to the same
    4.5. The inputs here come only from Qwen local_extract / transcript metadata,
    so the range is intentionally conservative.
    """
    value = base + sum(float(item) for item in signals)
    return round(min(maximum, max(minimum, value)) + 1e-8, 1)


def parse_duration_seconds(value: Any) -> int:
    text = clean_text(value)
    if not text or text == "未知":
        return 0
    total = 0
    hour_match = re.search(r"(\d+)\s*小时", text)
    minute_match = re.search(r"(\d+)\s*分", text)
    second_match = re.search(r"(\d+)\s*秒", text)
    if hour_match:
        total += int(hour_match.group(1)) * 3600
    if minute_match:
        total += int(minute_match.group(1)) * 60
    if second_match:
        total += int(second_match.group(1))
    if total:
        return total
    colon = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", text)
    if colon:
        hours = int(colon.group(1) or 0)
        minutes = int(colon.group(2))
        seconds = int(colon.group(3))
        return hours * 3600 + minutes * 60 + seconds
    return 0


def local_structured_assessment(qwen_extract: dict[str, Any], metadata: dict[str, Any]) -> dict[str, float]:
    claims = text_list(qwen_extract.get("core_claims"))
    methods = text_list(qwen_extract.get("methods"))
    examples = text_list(qwen_extract.get("examples"))
    caveats = text_list(qwen_extract.get("caveats"))
    quotes = text_list(qwen_extract.get("refined_quotes"))
    terms = text_list(qwen_extract.get("important_terms"))
    windows = qwen_extract.get("time_windows")
    window_count = len(windows) if isinstance(windows, list) else 0
    duration_seconds = parse_duration_seconds(metadata.get("duration"))

    density = bounded_score(
        3.2,
        min(1.4, len(claims) * 0.35),
        min(1.0, len(methods) * 0.25),
        min(0.6, len(terms) * 0.10),
        0.4 if duration_seconds >= 1200 else 0.0,
    )
    evidence = bounded_score(
        3.0,
        min(1.8, len(examples) * 0.55),
        min(0.8, len(quotes) * 0.20),
        0.4 if len(caveats) >= 2 else 0.0,
    )
    originality = bounded_score(
        3.1,
        min(1.2, len(terms) * 0.18),
        min(0.9, len(caveats) * 0.22),
        min(0.7, len(claims) * 0.12),
    )
    watch_value = bounded_score(
        3.4,
        min(1.2, window_count * 0.35),
        0.5 if duration_seconds >= 900 else 0.0,
        0.3 if len(examples) >= 3 else 0.0,
        -0.4 if duration_seconds and duration_seconds < 180 else 0.0,
    )
    return {
        "信息密度": density,
        "论据质量": evidence,
        "独创性": originality,
        "观看性价比": watch_value,
    }

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


def first_items(*values: Any, fallback: str) -> list[str]:
    rows: list[str] = []
    for value in values:
        rows.extend(text_list(value))
    if not rows:
        rows = [fallback]
    return rows[:6]


def local_target_sections(report_target: str, qwen_extract: dict[str, Any], *, main_axis: str, cleaned: str) -> dict[str, list[str]]:
    target = normalize_report_target(report_target)
    if target == DEFAULT_REPORT_TARGET:
        return {}
    claims = qwen_extract.get("core_claims")
    methods = qwen_extract.get("methods")
    examples = qwen_extract.get("examples")
    caveats = qwen_extract.get("caveats")
    quotes = qwen_extract.get("refined_quotes") or qwen_extract.get("original_quotes")
    sections = {
        "text_structure": {
            "structure_overview": first_items(qwen_extract.get("main_axis"), fallback=main_axis),
            "progression_logic": first_items(claims, methods, fallback="内容先提出主轴，再展开观点和方法。"),
            "transition_methods": first_items(examples, quotes, fallback="转场主要依靠例子、解释或重复强调来推进。"),
            "compression_pattern": first_items(caveats, fallback=cleaned),
        },
        "knowledge_notes": {
            "core_concepts": first_items(qwen_extract.get("important_terms"), claims, fallback=main_axis),
            "key_facts": first_items(claims, examples, fallback=cleaned),
            "methods": first_items(methods, fallback="原文没有明确步骤，只能保留主轴判断。"),
            "caveats": first_items(caveats, fallback="没有明确边界时，不补充原文之外的限制。"),
        },
        "viewpoint_breakdown": {
            "claims": first_items(claims, fallback=main_axis),
            "assumptions": first_items(qwen_extract.get("conditions"), fallback="该观点默认听众接受视频给出的背景和前提。"),
            "evidence": first_items(examples, quotes, fallback=cleaned),
            "counterpoints": first_items(caveats, fallback="原文没有充分展开反方，只能记录已出现的边界。"),
        },
        "creation_review": {
            "positioning": first_items(qwen_extract.get("main_axis"), fallback=main_axis),
            "hook_and_pacing": first_items(claims, quotes, fallback="开场和节奏主要围绕核心主轴展开。"),
            "material_use": first_items(examples, methods, fallback="素材使用以观点解释为主。"),
            "reuse_notes": first_items(methods, caveats, fallback=cleaned),
        },
    }[target]
    return {key: sections[key] for key in report_target_section_keys(target)}


def build_local_review_response(local_extract_payload: dict[str, Any], *, report_target: str = DEFAULT_REPORT_TARGET) -> dict[str, Any]:
    """Build a normalized report payload from local_extract without cloud review."""
    target = normalize_report_target(report_target)
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

    structured_assessment = local_structured_assessment(qwen_extract, metadata)

    report = {
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
        "structured_assessment": structured_assessment,
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
    if target != DEFAULT_REPORT_TARGET:
        report["report_target"] = target
        report["target_summary"] = sentence(cleaned, main_axis)
        report["target_sections"] = local_target_sections(target, qwen_extract, main_axis=main_axis, cleaned=cleaned)
    return report
