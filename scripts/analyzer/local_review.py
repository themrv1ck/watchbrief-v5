"""Deterministic local review provider for WatchBrief V5.

This provider is intentionally conservative. It turns Qwen local_extract output
into a schema-valid report without calling Codex or any cloud review model.
"""

from __future__ import annotations

import copy
import math
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
LOCAL_REVIEW_PROMPT_FINGERPRINT = "9b918bafde1f4cfc8f9d8ee8fa809bc8fb40d9c4a8ce7a0d3a6780a92c0a6d25"

HIGH_VALUE_KEYWORDS: tuple[tuple[str, float, str], ...] = (
    ("原理", 2.0, "原理解释"),
    ("机制", 1.8, "机制解释"),
    ("为什么", 1.4, "原因解释"),
    ("怎么", 1.0, "方法说明"),
    ("如何", 1.0, "方法说明"),
    ("方案", 1.8, "方案拆解"),
    ("第一套", 1.4, "方案拆解"),
    ("第二套", 1.4, "方案拆解"),
    ("第三套", 1.6, "方案拆解"),
    ("代价", 1.2, "边界说明"),
    ("限制", 1.3, "边界说明"),
    ("边界", 1.2, "边界说明"),
    ("提升空间", 1.2, "边界说明"),
    ("自进化", 2.6, "自进化机制"),
    ("Zero Skill", 2.8, "自进化机制"),
    ("agent", 1.8, "智能体机制"),
    ("智能体", 1.8, "智能体机制"),
    ("skill", 1.8, "技能机制"),
    ("工具", 1.5, "工具生成"),
    ("代码", 1.2, "工具生成"),
    ("MCP", 1.4, "接口能力"),
    ("API", 1.2, "接口能力"),
    ("评测", 1.2, "证据依据"),
    ("benchmark", 1.2, "证据依据"),
    ("论文", 1.3, "证据依据"),
    ("多模态", 2.4, "多模态原理"),
    ("融合", 1.5, "多模态原理"),
    ("early fusion", 2.8, "早融合原理"),
    ("早融合", 2.8, "早融合原理"),
    ("late fusion", 1.7, "早融合对比"),
    ("晚融合", 1.7, "早融合对比"),
    ("时间线", 1.4, "时间线分析"),
    ("心率", 0.9, "身体信号"),
    ("IMU", 1.2, "传感器数据"),
    ("音频", 0.8, "多模态数据"),
    ("图像", 0.8, "多模态数据"),
    ("隐私", 2.4, "隐私方案"),
    ("本地部署", 3.0, "本地部署"),
    ("部署", 2.2, "部署方案"),
    ("server", 1.8, "私有化部署"),
    ("服务器", 1.8, "私有化部署"),
    ("home lab", 1.8, "私有化部署"),
    ("本地模型", 2.0, "本地模型"),
    ("云端", 1.2, "云端边界"),
    ("原始数据", 1.8, "数据边界"),
    ("特征向量", 1.8, "数据边界"),
    ("数据完全不经过", 2.2, "私有化部署"),
)

LOW_VALUE_KEYWORDS: tuple[str, ...] = (
    "点赞",
    "收藏",
    "评论区",
    "感谢大家",
    "欢迎",
    "看到这里",
    "你是不是有点好奇",
    "事情是这样的",
)

CORE_LOGIC_TRIGGERS: tuple[str, ...] = (
    "讲讲原理",
    "讲原理",
    "核心逻辑",
    "核心机制",
    "底层逻辑",
    "怎么做到",
    "如何做到",
    "路径",
    "关系大了",
)

DEFINITION_TRIGGERS: tuple[str, ...] = (
    "本身不重要",
    "背后对接",
    "背后连接",
    "这玩意是啥",
    "到底是什么",
    "是什么东西",
)

TOPIC_BOUNDARY_MARKERS: tuple[str, ...] = (
    "隐私问题",
    "限制",
    "提升空间",
    "最后",
    "总结",
    "商业化",
    "上市",
)

LONG_CONTENT_MARKERS: tuple[str, ...] = (
    "播客",
    "podcast",
    "访谈",
    "长访谈",
    "interview",
    "演讲",
    "讲座",
    "lecture",
    "keynote",
    "课程",
    "公开课",
    "course",
    "masterclass",
    "seminar",
    "workshop",
    "培训",
    "训练营",
)
LONG_CONTENT_DURATION_THRESHOLD_SECONDS = 45 * 60


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
    qwen_extract = local_extract_payload.get("qwen_extract") if isinstance(local_extract_payload.get("qwen_extract"), dict) else {}
    windows = qwen_extract.get("time_windows")
    if not isinstance(windows, list):
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


def parse_timecode_seconds(value: Any) -> int:
    text = clean_text(value)
    if not text:
        return 0
    parts = text.split(":")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def format_timecode(seconds: int) -> str:
    value = max(0, int(seconds))
    hours = value // 3600
    minutes = (value % 3600) // 60
    secs = value % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def meaningful_topic_terms(qwen_extract: dict[str, Any]) -> list[str]:
    raw_terms = text_list(qwen_extract.get("important_terms"))
    fields = (
        qwen_extract.get("core_claims"),
        qwen_extract.get("conditions"),
        qwen_extract.get("methods"),
        qwen_extract.get("examples"),
    )
    for field in fields:
        for item in text_list(field):
            for keyword, _weight, _label in HIGH_VALUE_KEYWORDS:
                if keyword.lower() in item.lower():
                    raw_terms.append(keyword)
    excluded = {"X.PIN", "差评硬件部", "AI", "API", "APP", "agent"}
    terms: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        cleaned = clean_text(term)
        if not cleaned or cleaned in excluded or len(cleaned) < 2:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        terms.append(cleaned)
    return terms[:24]


def keyword_hits(text: str, qwen_extract: dict[str, Any]) -> tuple[float, list[str]]:
    lowered = text.lower()
    score = 0.0
    labels: list[str] = []
    for keyword, weight, label in HIGH_VALUE_KEYWORDS:
        if keyword.lower() in lowered:
            occurrences = max(1, lowered.count(keyword.lower()))
            score += weight + min(1.5, (occurrences - 1) * 0.35)
            labels.append(label)
    for term in meaningful_topic_terms(qwen_extract):
        if term.lower() in lowered:
            score += 0.7
            labels.append(term)
    return score, labels


def dedupe_labels(labels: list[str], *, limit: int = 3) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for label in labels:
        cleaned = clean_text(label)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def normalized_transcript_segments(local_extract_payload: dict[str, Any]) -> list[dict[str, Any]]:
    transcript = local_extract_payload.get("transcript") if isinstance(local_extract_payload.get("transcript"), dict) else {}
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        return []
    normalized: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = parse_timecode_seconds(segment.get("start"))
        end = parse_timecode_seconds(segment.get("end"))
        text = clean_text(segment.get("text"))
        if end <= start or not text:
            continue
        normalized.append({
            "start": clean_text(segment.get("start")) or format_timecode(start),
            "end": clean_text(segment.get("end")) or format_timecode(end),
            "start_seconds": start,
            "end_seconds": end,
            "text": text,
        })
    return normalized


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker.lower() in text.lower() for marker in markers)


def build_transcript_window(
    normalized: list[dict[str, Any]],
    *,
    start_index: int,
    min_seconds: int,
    max_seconds: int,
    stop_markers: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    if start_index < 0 or start_index >= len(normalized):
        return []
    start_seconds = int(normalized[start_index]["start_seconds"])
    window: list[dict[str, Any]] = []
    for segment in normalized[start_index:]:
        elapsed = int(segment["start_seconds"]) - start_seconds
        if elapsed > max_seconds:
            break
        if window and elapsed >= min_seconds and contains_any(str(segment.get("text") or ""), stop_markers):
            break
        window.append(segment)
        if int(window[-1]["end_seconds"]) - start_seconds >= max_seconds:
            break
    return window


def candidate_from_window(
    window: list[dict[str, Any]],
    *,
    score: float,
    labels: list[str],
    excerpt_limit: int = 420,
) -> dict[str, Any] | None:
    if not window:
        return None
    text_blob = clean_text(" ".join(item["text"] for item in window))
    if not text_blob:
        return None
    return {
        "start": window[0]["start"],
        "end": window[-1]["end"],
        "start_seconds": window[0]["start_seconds"],
        "end_seconds": window[-1]["end_seconds"],
        "excerpt": text_blob[:excerpt_limit],
        "score": round(score, 4),
        "labels": dedupe_labels(labels, limit=5),
    }


def understanding_entry_candidates(local_extract_payload: dict[str, Any], qwen_extract: dict[str, Any]) -> list[dict[str, Any]]:
    """Find segments that work as a reader's first understanding entry.

    A high-value late segment can be useful, but the primary jump target should
    answer the reader's first questions: what is this, what problem does it
    address, and what mechanism/path produces the claimed effect.
    """
    normalized = normalized_transcript_segments(local_extract_payload)
    if not normalized:
        return []
    candidates: list[dict[str, Any]] = []
    definition_added = False
    for index, segment in enumerate(normalized):
        text = str(segment.get("text") or "")
        if not definition_added and int(segment.get("start_seconds") or 0) <= 300 and contains_any(text, DEFINITION_TRIGGERS):
            start_index = max(0, index - 3)
            window = build_transcript_window(
                normalized,
                start_index=start_index,
                min_seconds=35,
                max_seconds=70,
                stop_markers=("事情是这样的", "OK", "体验就说这么多"),
            )
            candidate = candidate_from_window(
                window,
                score=60.0,
                labels=["理解入口", "对象定义", "主要内容"],
            )
            if candidate:
                candidates.append(candidate)
                definition_added = True
        if contains_any(text, CORE_LOGIC_TRIGGERS):
            window = build_transcript_window(
                normalized,
                start_index=index,
                min_seconds=130,
                max_seconds=240,
                stop_markers=TOPIC_BOUNDARY_MARKERS,
            )
            candidate = candidate_from_window(
                window,
                score=120.0,
                labels=["理解入口", "核心逻辑", "路径效果", "机制解释"],
            )
            if candidate:
                candidates.append(candidate)

    if not candidates:
        # Generic fallback for product/technology videos that do not explicitly
        # say "讲原理": prefer a bridge that combines mechanism and effect terms.
        windows = segment_window_candidates(local_extract_payload, qwen_extract, duration_seconds=0)
        for window in windows:
            text = clean_text(window.get("excerpt"))
            has_mechanism = contains_any(text, ("原理", "机制", "自进化", "多模态", "融合", "时间线", "工具", "方法"))
            has_effect = contains_any(text, ("解决", "效果", "做到", "发现", "识别", "还原", "理解", "帮你", "校准"))
            has_object = contains_any(text, ("产品", "系统", "智能体", "工具", "硬件", "手表", "设备", "这个东西"))
            if has_mechanism and has_effect and has_object:
                candidate = copy.deepcopy(window)
                candidate["score"] = float(candidate.get("score") or 0) + 10.0
                candidate["labels"] = dedupe_labels(["理解入口", "核心逻辑", *[str(item) for item in candidate.get("labels", [])]], limit=5)
                candidates.append(candidate)
    return candidates


def segment_window_candidates(
    local_extract_payload: dict[str, Any],
    qwen_extract: dict[str, Any],
    *,
    duration_seconds: int,
) -> list[dict[str, Any]]:
    normalized = normalized_transcript_segments(local_extract_payload)
    if not normalized:
        return []

    target_duration = 105 if duration_seconds >= 600 else 60
    step_seconds = 45 if duration_seconds >= 600 else 30
    candidates: list[dict[str, Any]] = []
    next_start = normalized[0]["start_seconds"]
    for start_index, segment in enumerate(normalized):
        if segment["start_seconds"] < next_start:
            continue
        window: list[dict[str, Any]] = []
        end_index = start_index
        while end_index < len(normalized):
            window.append(normalized[end_index])
            if normalized[end_index]["end_seconds"] - segment["start_seconds"] >= target_duration:
                break
            end_index += 1
        if not window:
            continue
        text_blob = clean_text(" ".join(item["text"] for item in window))
        score, labels = keyword_hits(text_blob, qwen_extract)
        window_duration = max(1, window[-1]["end_seconds"] - window[0]["start_seconds"])
        score += min(2.0, len(text_blob) / max(80, window_duration) * 0.18)
        if duration_seconds >= 600 and window[0]["start_seconds"] < 90:
            score -= 3.5
        if duration_seconds >= 600 and window[0]["start_seconds"] > max(0, duration_seconds - 120):
            score -= 1.5
        for marker in LOW_VALUE_KEYWORDS:
            if marker in text_blob:
                score -= 1.0
        candidates.append({
            "start": window[0]["start"],
            "end": window[-1]["end"],
            "start_seconds": window[0]["start_seconds"],
            "end_seconds": window[-1]["end_seconds"],
            "excerpt": text_blob[:360],
            "score": round(score, 4),
            "labels": dedupe_labels(labels),
        })
        next_start = segment["start_seconds"] + step_seconds
    return candidates


def parse_candidate_segment_text(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        start = clean_text(value.get("start"))
        end = clean_text(value.get("end"))
        reason = clean_text(value.get("reason") or value.get("summary") or value.get("text") or value.get("value"))
    else:
        text = clean_text(value)
        match = re.search(r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*\|\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?)(?:\s*[：:]\s*(?P<reason>.*))?", text)
        if not match:
            return None
        start = match.group("start")
        end = match.group("end")
        reason = clean_text(match.group("reason"))
    if not start or not end:
        return None
    start_seconds = parse_timecode_seconds(start)
    end_seconds = parse_timecode_seconds(end)
    if end_seconds <= start_seconds:
        return None
    return {
        "start": start,
        "end": end,
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "excerpt": reason,
        "score": 12.0,
        "labels": ["模型候选片段"] if reason else [],
    }


def qwen_candidate_segments(qwen_extract: dict[str, Any]) -> list[dict[str, Any]]:
    value = qwen_extract.get("candidate_watch_segments")
    if not isinstance(value, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in value:
        parsed = parse_candidate_segment_text(item)
        if parsed:
            candidates.append(parsed)
    return candidates


def overlap_ratio(first: dict[str, Any], second: dict[str, Any]) -> float:
    start = max(int(first["start_seconds"]), int(second["start_seconds"]))
    end = min(int(first["end_seconds"]), int(second["end_seconds"]))
    if end <= start:
        return 0.0
    shorter = max(1, min(
        int(first["end_seconds"]) - int(first["start_seconds"]),
        int(second["end_seconds"]) - int(second["start_seconds"]),
    ))
    return (end - start) / shorter


def title_focus(labels: list[str], excerpt: str) -> str:
    text = f"{' '.join(labels)} {excerpt}"
    if "理解入口" in labels and "对象定义" in labels:
        return "这个东西是什么"
    if "理解入口" in labels:
        return "核心逻辑入口"
    if any(marker in text for marker in ("本地部署", "部署", "server", "home lab", "隐私", "特征向量", "原始数据")):
        return "隐私与部署方案"
    if any(marker.lower() in text.lower() for marker in ("early fusion", "早融合", "late fusion", "晚融合", "多模态")):
        return "多模态早融合原理"
    if any(marker in text for marker in ("Zero Skill", "自进化", "造工具", "工具生成", "skill")):
        return "自进化与工具生成"
    if any(marker in text for marker in ("限制", "边界", "提升空间", "开发版")):
        return "能力边界与限制"
    if any(marker in text for marker in ("方法", "方案", "步骤")):
        return "方法与方案"
    return "高价值核心段"


def build_watch_segments(local_extract_payload: dict[str, Any], qwen_extract: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, str]]:
    duration_seconds = parse_duration_seconds(metadata.get("duration"))
    candidates = qwen_candidate_segments(qwen_extract)
    candidates.extend(understanding_entry_candidates(local_extract_payload, qwen_extract))
    candidates.extend(segment_window_candidates(local_extract_payload, qwen_extract, duration_seconds=duration_seconds))
    fallback = first_time_window(local_extract_payload)
    if not candidates:
        candidates = [{
            "start": fallback["start"],
            "end": fallback["end"],
            "start_seconds": parse_timecode_seconds(fallback["start"]),
            "end_seconds": parse_timecode_seconds(fallback["end"]),
            "excerpt": fallback["excerpt"],
            "score": 0.0,
            "labels": [],
        }]
    ranked = sorted(candidates, key=lambda item: (float(item.get("score") or 0), int(item.get("start_seconds") or 0)), reverse=True)
    selected: list[dict[str, Any]] = []
    for candidate in ranked:
        if len(selected) >= 3:
            break
        if any(overlap_ratio(candidate, existing) > 0.35 for existing in selected):
            continue
        if float(candidate.get("score") or 0) < 1.0 and selected:
            continue
        selected.append(candidate)
    if not selected:
        selected.append(ranked[0])
    selected = sorted(selected, key=lambda item: int(item.get("start_seconds") or 0))
    primary = max(selected, key=lambda item: float(item.get("score") or 0))
    ordered = [primary] + [item for item in selected if item is not primary]
    priorities = ("primary", "optional", "backup")
    title_prefix = {
        "primary": "首选片段：",
        "optional": "可选补看：",
        "backup": "备选：",
    }
    result: list[dict[str, str]] = []
    for priority, candidate in zip(priorities, ordered):
        labels = candidate.get("labels") if isinstance(candidate.get("labels"), list) else []
        focus = title_focus([str(item) for item in labels], clean_text(candidate.get("excerpt")))
        reason_core = "、".join(dedupe_labels([str(item) for item in labels], limit=3)) or focus
        excerpt = clean_text(candidate.get("excerpt"))
        if priority == "primary" and "理解入口" in labels:
            reason = f"这一段最适合作为理解入口，集中回答视频主要讲什么、通过什么路径产生什么效果。"
        else:
            reason = f"这一段集中覆盖{reason_core}，能补充理解视频的关键内容。"
        if excerpt:
            reason = f"{reason} 片段线索：{excerpt[:120]}"
        result.append({
            "priority": priority,
            "start": clean_text(candidate.get("start")) or fallback["start"],
            "end": clean_text(candidate.get("end")) or fallback["end"],
            "title": f"{title_prefix[priority]}{focus}",
            "reason": reason,
        })
    return result



def bounded_score(base: float, *signals: float, minimum: float = 2.5, maximum: float = 7.4) -> float:
    """Return a one-decimal local-rules score from bounded evidence signals.

    Local review is a fallback when no real review model is used. It must be
    deterministic, but it must not collapse every successful report to the same
    midpoint. The inputs here come only from Qwen local_extract / transcript
    metadata, so the range is intentionally conservative.
    """
    value = base + sum(float(item) for item in signals)
    return round(min(maximum, max(minimum, value)) + 1e-8, 1)


def diminishing_count_bonus(count: int, *, scale: float, cap: float) -> float:
    """Convert an evidence count into a smooth bonus without early flat caps.

    The previous local scorer used hard caps such as ``min(1.8, examples*0.55)``.
    Real 30-minute explainers often exceed those caps in every dimension, which
    made unrelated videos converge to the same deterministic score (the observed
    5.6/5.6 bug). A logarithmic bonus keeps local scoring conservative while
    preserving differences between 4, 7, and 14 extracted evidence items.
    """
    if count <= 0:
        return 0.0
    return min(cap, math.log1p(count) * scale)


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


def long_content_breakdown_applies(metadata: dict[str, Any], qwen_extract: dict[str, Any]) -> bool:
    duration_seconds = parse_duration_seconds(metadata.get("duration"))
    if duration_seconds <= LONG_CONTENT_DURATION_THRESHOLD_SECONDS:
        return False
    fields: list[str] = [
        clean_text(metadata.get("title")),
        clean_text(metadata.get("channel")),
        clean_text(qwen_extract.get("main_axis")),
        clean_text(qwen_extract.get("cleaned_understanding")),
    ]
    fields.extend(text_list(qwen_extract.get("important_terms")))
    fields.extend(text_list(qwen_extract.get("core_claims"))[:4])
    haystack = " ".join(fields).lower()
    return any(marker.lower() in haystack for marker in LONG_CONTENT_MARKERS)


def normalize_phase_outline_item(item: Any, index: int) -> dict[str, str] | None:
    if isinstance(item, dict):
        start = clean_text(item.get("start") or item.get("begin"))
        end = clean_text(item.get("end") or item.get("finish"))
        summary = clean_text(item.get("summary") or item.get("content") or item.get("description") or item.get("reason"))
        title = clean_text(item.get("title") or item.get("phase") or item.get("label"))
    else:
        raw = clean_text(item)
        match = re.search(
            r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*(?:\||-|—|–|－|~|～|至|到)\s*"
            r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?)(?:\s*[：:]\s*(?P<summary>.*))?",
            raw,
        )
        if not match:
            return None
        start = match.group("start")
        end = match.group("end")
        summary = clean_text(match.group("summary") or raw)
        title = ""
    if not start or not end or not summary:
        return None
    if not title:
        title = f"第{index}阶段：{short_node(summary, '阶段内容')}"
    return {
        "start": start,
        "end": end,
        "title": title,
        "summary": sentence(summary, "这一阶段讲解原文中的一个独立部分"),
    }


def phase_times_are_ordered(phases: list[dict[str, str]]) -> bool:
    previous_end = -1
    for phase in phases:
        start = parse_timecode_seconds(phase.get("start"))
        end = parse_timecode_seconds(phase.get("end"))
        if end <= start or start < previous_end:
            return False
        previous_end = end
    return True


def phase_title(index: int, text_blob: str) -> str:
    cleaned = clean_text(text_blob)
    return f"第{index}阶段：{short_node(cleaned, '阶段内容')}"


def build_transcript_phase_breakdown(
    local_extract_payload: dict[str, Any],
    metadata: dict[str, Any],
) -> list[dict[str, str]]:
    normalized = normalized_transcript_segments(local_extract_payload)
    if len(normalized) < 2:
        return []
    duration_seconds = parse_duration_seconds(metadata.get("duration"))
    if duration_seconds >= 2 * 3600:
        target_seconds = 15 * 60
    elif duration_seconds >= 3600:
        target_seconds = 10 * 60
    else:
        target_seconds = 8 * 60

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_start = int(normalized[0]["start_seconds"])
    for segment in normalized:
        if current and int(segment["start_seconds"]) - current_start >= target_seconds:
            groups.append(current)
            current = []
            current_start = int(segment["start_seconds"])
        current.append(segment)
    if current:
        groups.append(current)

    if len(groups) < 2:
        midpoint = max(1, len(normalized) // 2)
        groups = [normalized[:midpoint], normalized[midpoint:]]

    phases: list[dict[str, str]] = []
    for index, group in enumerate(groups[:24], start=1):
        if not group:
            continue
        text_blob = clean_text(" ".join(str(item.get("text") or "") for item in group))
        if not text_blob:
            continue
        summary = f"这一阶段的转写内容集中在：{text_blob[:220]}"
        phases.append({
            "start": clean_text(group[0].get("start")),
            "end": clean_text(group[-1].get("end")),
            "title": phase_title(index, text_blob),
            "summary": sentence(summary, "这一阶段讲解原文中的一个独立部分"),
        })
    return phases if len(phases) >= 2 else []


def build_long_content_breakdown(
    local_extract_payload: dict[str, Any],
    qwen_extract: dict[str, Any],
    metadata: dict[str, Any],
) -> list[dict[str, str]]:
    if not long_content_breakdown_applies(metadata, qwen_extract):
        return []
    outline = qwen_extract.get("phase_outline")
    phases: list[dict[str, str]] = []
    if isinstance(outline, list):
        for index, item in enumerate(outline, start=1):
            phase = normalize_phase_outline_item(item, index)
            if phase:
                phases.append(phase)
    if len(phases) >= 2 and phase_times_are_ordered(phases):
        return phases[:24]
    return build_transcript_phase_breakdown(local_extract_payload, metadata)


def local_structured_assessment(
    qwen_extract: dict[str, Any],
    metadata: dict[str, Any],
    local_extract_payload: dict[str, Any] | None = None,
) -> dict[str, float]:
    claims = text_list(qwen_extract.get("core_claims"))
    methods = text_list(qwen_extract.get("methods"))
    examples = text_list(qwen_extract.get("examples"))
    caveats = text_list(qwen_extract.get("caveats"))
    quotes = text_list(qwen_extract.get("refined_quotes"))
    terms = text_list(qwen_extract.get("important_terms"))
    windows = qwen_extract.get("time_windows")
    if not isinstance(windows, list) and isinstance(local_extract_payload, dict):
        windows = local_extract_payload.get("time_windows")
    window_count = len(windows) if isinstance(windows, list) else 0
    duration_seconds = parse_duration_seconds(metadata.get("duration"))
    duration_minutes = duration_seconds / 60 if duration_seconds else 0
    has_sponsor_noise = any("商业" in item or "植入" in item or "sponsor" in item.lower() for item in caveats)

    # Calibrated against Codex gpt-5.5 on the India/Saudi explainer pair:
    # dense, well-structured 30+ minute explainers should land in the high-7
    # band, not in the mid-4 local-fallback band. The logarithmic counts still
    # keep sparse/short inputs low and preserve differences between dense items.
    density = bounded_score(
        5.2,
        diminishing_count_bonus(len(claims), scale=0.75, cap=2.1),
        diminishing_count_bonus(len(methods), scale=0.25, cap=0.7),
        diminishing_count_bonus(len(terms), scale=0.18, cap=0.65),
        0.4 if duration_minutes >= 20 else 0.2 if duration_minutes >= 10 else 0.0,
        maximum=8.4,
    )
    evidence = bounded_score(
        5.45,
        diminishing_count_bonus(len(examples), scale=0.76, cap=1.8),
        diminishing_count_bonus(len(quotes), scale=0.20, cap=0.55),
        diminishing_count_bonus(len(caveats), scale=0.22, cap=0.55),
        -0.1 if has_sponsor_noise else 0.0,
        maximum=8.2,
    )
    originality = bounded_score(
        5.15,
        diminishing_count_bonus(len(terms), scale=0.55, cap=1.75),
        diminishing_count_bonus(len(caveats), scale=0.25, cap=0.65),
        diminishing_count_bonus(len(claims), scale=0.12, cap=0.45),
        maximum=8.0,
    )
    watch_value = bounded_score(
        5.45,
        diminishing_count_bonus(window_count, scale=0.65, cap=1.5),
        diminishing_count_bonus(len(examples), scale=0.36, cap=0.9),
        0.75 if duration_minutes >= 15 else 0.25 if duration_minutes >= 5 else 0.0,
        -0.35 if duration_seconds and duration_seconds < 180 else 0.0,
        -0.05 if has_sponsor_noise else 0.0,
        maximum=8.6,
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
        "content_brief": {
            "direct_statements": first_items(qwen_extract.get("main_axis"), claims, fallback=main_axis),
            "key_points": first_items(claims, examples, fallback=cleaned),
            "practical_takeaways": first_items(methods, fallback="原文没有明确可照做部分，只能保留内容观点。"),
            "boundaries": first_items(caveats, fallback="没有明确边界时，不补充原文之外的限制。"),
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

    final_core = claims[0] if claims else main_axis
    method_text = "；".join(methods[:2]) if methods else "按内容主轴理解关键做法"
    caveat_text = "；".join(caveats[:2]) if caveats else "本地模式不会额外补充原文之外的判断"
    highest_source = refined_quotes[0] if refined_quotes else cleaned
    highest = highest_source if len(highest_source) >= 16 else f"{main_axis}，关键在于{final_core}"

    structured_assessment = local_structured_assessment(qwen_extract, metadata, payload)
    watch_segments = build_watch_segments(payload, qwen_extract, metadata)
    long_content_breakdown = build_long_content_breakdown(payload, qwen_extract, metadata)
    primary = watch_segments[0]
    primary_range = f"{primary['start']} | {primary['end']}"
    if len(watch_segments) > 1:
        watch_verdict = f"报告已提炼主要内容与核心路径；如果用户要补看原片上下文，入口是 {primary_range}，其余片段按自己的问题选择。"
    else:
        watch_verdict = f"报告已提炼主要内容；如果用户要补看原片上下文，入口是 {primary_range}，是否继续观看由用户自行决定。"

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
        "watch_verdict": watch_verdict,
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
        "watch_segments": watch_segments,
        "only_one_segment": f"只补一段：{primary_range}。这一段是本地规则从全量转写中定位到的理解入口片段。",
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
    if long_content_breakdown:
        report["long_content_breakdown"] = long_content_breakdown
    if target != DEFAULT_REPORT_TARGET:
        report["report_target"] = target
        report["target_summary"] = sentence(cleaned, main_axis)
        report["target_sections"] = local_target_sections(target, qwen_extract, main_axis=main_axis, cleaned=cleaned)
    return report
