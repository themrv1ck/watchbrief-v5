#!/usr/bin/env python3
"""Deterministic validation for WatchBrief V5 normalized payloads."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Optional

try:
    from .report_targets import DEFAULT_REPORT_TARGET, REPORT_TARGETS, normalize_report_target, report_target_section_keys
    from .scoring import SCORE_TRACE_FINAL_SOURCE, build_score_trace, formula_version_for_weights, scoring_weights_from_payload, tag_for_score
except ImportError:  # pragma: no cover
    from report_targets import DEFAULT_REPORT_TARGET, REPORT_TARGETS, normalize_report_target, report_target_section_keys
    from scoring import SCORE_TRACE_FINAL_SOURCE, build_score_trace, formula_version_for_weights, scoring_weights_from_payload, tag_for_score


VALID_TAGS = (
    "报告足够替代",
    "报告基本可替代",
    "只建议跳看",
    "值得补看",
    "建议完整看",
    "不推荐观看",
    "解析不足",
)
WATCHBRIEF_VERSION = "watchbrief_v5"
QWEN_LOCAL_EXTRACT_PROMPT_VERSION = "watchbrief_v5.qwen_local_extract_prompt.v2"
CODEX_REVIEW_PROMPT_VERSION = "watchbrief_v5.codex_review_prompt.v5"

PATH_TABLE_KEYS = ("problem", "mechanism", "turning_point", "landing")
WATCH_SEGMENT_PRIORITY_ORDER = ("primary", "optional", "backup")
WATCH_SEGMENT_TITLE_PREFIX = {
    "primary": "首选片段：",
    "optional": "可选补看：",
    "backup": "备选：",
}
LONG_CONTENT_BREAKDOWN_FIELD = "long_content_breakdown"
LONG_CONTENT_DURATION_THRESHOLD_SECONDS = 45 * 60
LONG_CONTENT_TYPE_MARKERS = (
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
SCORE_BASIS_KEYS = (
    "information_density",
    "evidence_quality",
    "originality",
    "watch_value",
)
STRUCTURED_ASSESSMENT_KEYS = ("信息密度", "论据质量", "独创性", "观看性价比")
SCORE_TRACE_KEYS = (
    "information_density",
    "evidence_quality",
    "originality",
    "watch_value",
    "formula",
    "computed_replacement_score",
    "model_suggested_score",
    "final_score_source",
    "score_semantics",
)

REQUIRED_REPORT_FIELDS = (
    "title",
    "url",
    "channel",
    "duration",
    "date",
    "topic",
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
    "only_one_segment",
    "score_basis",
    "structured_assessment",
    "score_trace",
    "transcript_hash",
    "qwen_model_id",
    "qwen_prompt_version",
    "qwen_prompt_fingerprint",
    "codex_model",
    "codex_prompt_version",
    "codex_prompt_fingerprint",
    "scoring_formula_version",
    "watchbrief_version",
    "confidence_note",
)

LEGACY_FIELDS = {
    "core_thesis",
    "main_content_intro",
    "summary_sentence",
    "gain_actions",
    "worth_watching",
    "direct_watch_advice",
    "recommended_sections",
    "worth_watching_sections",
    "not_worth_watching_sections",
    "closing_summary",
    "core_takeaways",
    "framework_nodes",
    "why_watch",
    "why_not_full_watch",
}

LEGACY_MODULE_LABELS = ("要点提炼", "可执行动作清单", "完整笔记")

LEGACY_TOPIC_DEFAULTS = {
    "心理学/社交/魅力",
    "心理学 / 社交 / 魅力",
    "心理学/吸引力/信任",
    "心理学 / 吸引力 / 信任",
}

TIME_POINT = r"\d{1,2}:\d{2}(?::\d{2})?"
PIPE_TIME_RANGE_RE = re.compile(rf"(?<!\d)({TIME_POINT})\s*\|\s*({TIME_POINT})(?!\d)")
NON_PIPE_TIME_RANGE_RE = re.compile(
    rf"(?<!\d)({TIME_POINT})\s*(?:-|—|–|－|~|～|至|到)\s*({TIME_POINT})(?!\d)"
)
ANY_TIME_RE = re.compile(rf"(?<!\d){TIME_POINT}(?!\d)")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ONE_LINE_FORBIDDEN_MARKERS = (
    "看报告",
    "报告足够",
    "报告基本够",
    "原视频",
    "跳看",
    "补看",
    "完整观看",
    "完整看",
    "不必看",
    "不用看",
    "无需看",
    "建议看",
)
ONE_LINE_SCORE_ADVICE_RE = re.compile(r"(?:评分|分数)\s*(?:为|是|[:：])?\s*\d+(?:\.\d+)?\s*分?|\d+(?:\.\d+)?\s*分\s*(?:视频|原片|原视频|观看|建议)")

WATCH_VERDICT_REPORT_MARKERS = ("报告", "看报告")
WATCH_VERDICT_DECISION_MARKERS = (
    "原视频",
    "不必看",
    "不用看",
    "无需看",
    "跳看",
    "补看",
    "完整看",
    "完整观看",
    "建议看",
)
NO_WATCH_MARKERS = ("不必看", "不用看", "无需看", "不用再看", "不推荐观看")

FINAL_CONCLUSION_FORBIDDEN_MARKERS = (
    "报告足够",
    "报告基本够",
    "报告基本可替代",
    "报告可替代",
    "报告已经",
    "看报告",
    "需要看原视频",
    "原视频",
    "完整观看",
    "完整看",
    "补看",
    "跳看",
    "观看建议",
    "评分",
    "分数",
    "评分理由",
    "值得看",
    "不值得看",
    "论证偏弱",
    "信息密度",
    "观点不新",
    "视频偏长",
    "广告偏长",
    "案例铺陈偏长",
    "适合入门",
    "缺少数据",
    "缺少证据",
    "只建议",
    "不必看",
    "不用看",
    "无需看",
)

INCOMPLETE_FINAL_SUFFIXES = ("所以", "但是", "如果", "关键不是", "因此", "这说明")
SENTENCE_ENDINGS = ("。", "！", "？", "!", "?")

OVER_ABSTRACT_HIGHEST_COMPRESSION = {
    "长期投资问题",
    "认知升级",
    "成长路径",
    "底层逻辑",
    "心态调整",
    "自我提升",
    "关系经营",
}


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str
    code: str


class WatchBriefValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        summary = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        super().__init__(summary)


def text(value: Any) -> str:
    return " ".join(str(value or "").split())


def string_has_old_module_label(value: Any) -> bool:
    if isinstance(value, str):
        return any(label in value for label in LEGACY_MODULE_LABELS)
    if isinstance(value, dict):
        return any(string_has_old_module_label(item) for item in value.values())
    if isinstance(value, list):
        return any(string_has_old_module_label(item) for item in value)
    return False


def first_pipe_range(value: Any) -> Optional[tuple[str, str]]:
    match = PIPE_TIME_RANGE_RE.search(text(value))
    if not match:
        return None
    return match.group(1), match.group(2)


def has_non_pipe_range(value: Any) -> bool:
    return bool(NON_PIPE_TIME_RANGE_RE.search(text(value)))


def parse_time_point_seconds(value: Any) -> Optional[int]:
    raw = text(value)
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return None


def parse_duration_seconds(value: Any) -> int:
    raw = text(value)
    if not raw or raw == "未知":
        return 0
    total = 0
    hour_match = re.search(r"(\d+)\s*小时", raw)
    minute_match = re.search(r"(\d+)\s*分", raw)
    second_match = re.search(r"(\d+)\s*秒", raw)
    if hour_match:
        total += int(hour_match.group(1)) * 3600
    if minute_match:
        total += int(minute_match.group(1)) * 60
    if second_match:
        total += int(second_match.group(1))
    if total:
        return total
    parsed_time = parse_time_point_seconds(raw)
    return int(parsed_time or 0)


def requires_long_content_breakdown(payload: dict[str, Any]) -> bool:
    if parse_duration_seconds(payload.get("duration")) <= LONG_CONTENT_DURATION_THRESHOLD_SECONDS:
        return False
    haystack = " ".join(
        text(payload.get(field))
        for field in (
            "title",
            "channel",
            "topic",
            "one_line_brief",
            "highest_compression",
            "final_conclusion",
        )
    ).lower()
    return any(marker.lower() in haystack for marker in LONG_CONTENT_TYPE_MARKERS)


def _validate_long_content_breakdown(payload: dict[str, Any], issues: list[ValidationIssue]) -> None:
    field = LONG_CONTENT_BREAKDOWN_FIELD
    required = requires_long_content_breakdown(payload)
    if field not in payload:
        if required:
            issues.append(ValidationIssue(field, "long podcast/lecture/course reports must include phase breakdown", "long_breakdown_required"))
        return

    duration_seconds = parse_duration_seconds(payload.get("duration"))
    if duration_seconds <= LONG_CONTENT_DURATION_THRESHOLD_SECONDS:
        issues.append(ValidationIssue(field, "only videos longer than 45 minutes may include long content breakdown", "long_breakdown_scope"))

    phases = payload.get(field)
    if not isinstance(phases, list):
        issues.append(ValidationIssue(field, "must be a list", "long_breakdown_type"))
        return
    if not 2 <= len(phases) <= 24:
        issues.append(ValidationIssue(field, "must contain 2 to 24 phases", "long_breakdown_length"))

    previous_end: Optional[int] = None
    for index, phase in enumerate(phases):
        path = f"{field}[{index}]"
        if not isinstance(phase, dict):
            issues.append(ValidationIssue(path, "phase must be an object", "long_breakdown_phase_type"))
            continue
        for key in ("start", "end", "title", "summary"):
            if not text(phase.get(key)):
                issues.append(ValidationIssue(f"{path}.{key}", "required phase field is missing", "long_breakdown_required_field"))
        start = parse_time_point_seconds(phase.get("start"))
        end = parse_time_point_seconds(phase.get("end"))
        if start is None or end is None:
            issues.append(ValidationIssue(path, "phase start/end must be MM:SS or H:MM:SS", "long_breakdown_time"))
            continue
        if end <= start:
            issues.append(ValidationIssue(path, "phase end must be after start", "long_breakdown_time_order"))
        if previous_end is not None and start < previous_end:
            issues.append(ValidationIssue(path, "phases must be ordered and non-overlapping", "long_breakdown_overlap"))
        previous_end = max(previous_end or 0, end)
        if len(text(phase.get("summary"))) < 8:
            issues.append(ValidationIssue(f"{path}.summary", "phase summary must describe the concrete content", "long_breakdown_summary"))


def validate_normalized_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    issues: list[ValidationIssue] = []

    if not isinstance(payload, dict):
        raise WatchBriefValidationError([
            ValidationIssue("$", "payload must be an object", "payload_type")
        ])

    for field in REQUIRED_REPORT_FIELDS:
        if field not in payload:
            issues.append(ValidationIssue(field, "required field is missing", "required"))

    for field in sorted(LEGACY_FIELDS.intersection(payload.keys())):
        issues.append(ValidationIssue(field, "legacy field is not allowed in V5 renderer input", "legacy_field"))

    if string_has_old_module_label(payload):
        issues.append(ValidationIssue("$", "legacy module label is not allowed", "legacy_module_label"))

    report_target = DEFAULT_REPORT_TARGET
    if "report_target" in payload:
        try:
            report_target = normalize_report_target(payload.get("report_target"))
        except ValueError:
            issues.append(ValidationIssue("report_target", f"must be one of {REPORT_TARGETS}", "report_target_enum"))
    _validate_target_payload(payload, report_target, issues)

    replacement_score = payload.get("replacement_score")
    if not isinstance(replacement_score, (int, float)) or isinstance(replacement_score, bool):
        issues.append(ValidationIssue("replacement_score", "must be a number", "score_type"))
    elif not 0 <= float(replacement_score) <= 10:
        issues.append(ValidationIssue("replacement_score", "must be between 0 and 10", "score_range"))

    tag = text(payload.get("tag"))
    if tag not in VALID_TAGS:
        issues.append(ValidationIssue("tag", "must be a fixed recommendation tag", "tag_enum"))

    topic = text(payload.get("topic"))
    if not topic:
        issues.append(ValidationIssue("topic", "must not be empty", "topic_empty"))
    if topic in LEGACY_TOPIC_DEFAULTS:
        issues.append(ValidationIssue("topic", "must not inherit old template defaults", "topic_legacy_default"))

    one_line = text(payload.get("one_line_brief"))
    if not one_line.startswith("这期视频主要讲："):
        issues.append(ValidationIssue("one_line_brief", "must start with 这期视频主要讲：", "one_line_prefix"))
    if ANY_TIME_RE.search(one_line) or any(marker in one_line for marker in ONE_LINE_FORBIDDEN_MARKERS) or ONE_LINE_SCORE_ADVICE_RE.search(one_line):
        issues.append(ValidationIssue("one_line_brief", "must not contain viewing advice, score reasons, or time ranges", "one_line_scope"))

    watch_verdict = text(payload.get("watch_verdict"))
    if not any(marker in watch_verdict for marker in WATCH_VERDICT_REPORT_MARKERS):
        issues.append(ValidationIssue("watch_verdict", "must say whether the report is enough", "watch_verdict_report"))
    if not any(marker in watch_verdict for marker in WATCH_VERDICT_DECISION_MARKERS):
        issues.append(ValidationIssue("watch_verdict", "must say whether to watch the original video", "watch_verdict_decision"))
    if not PIPE_TIME_RANGE_RE.search(watch_verdict) and not any(marker in watch_verdict for marker in NO_WATCH_MARKERS):
        issues.append(ValidationIssue("watch_verdict", "must contain a pipe time range or a no-watch decision", "watch_verdict_time"))
    if has_non_pipe_range(watch_verdict):
        issues.append(ValidationIssue("watch_verdict", "displayed time ranges must use start | end", "time_pipe"))

    highest = text(payload.get("highest_compression"))
    if not highest:
        issues.append(ValidationIssue("highest_compression", "must not be empty", "highest_empty"))
    if highest in OVER_ABSTRACT_HIGHEST_COMPRESSION or len(highest) < 16:
        issues.append(ValidationIssue("highest_compression", "must be concrete and not over-abstract", "highest_abstract"))

    path_table = payload.get("path_table")
    if not isinstance(path_table, dict):
        issues.append(ValidationIssue("path_table", "must be an object", "path_table_type"))
    else:
        for key in PATH_TABLE_KEYS:
            if not text(path_table.get(key)):
                issues.append(ValidationIssue(f"path_table.{key}", "required path node is missing", "path_table_required"))
        extra_keys = set(path_table) - set(PATH_TABLE_KEYS)
        if extra_keys:
            issues.append(ValidationIssue("path_table", f"unexpected keys: {sorted(extra_keys)}", "path_table_extra"))

    arrow_chain = payload.get("arrow_chain")
    if not isinstance(arrow_chain, list):
        issues.append(ValidationIssue("arrow_chain", "must be a list", "arrow_chain_type"))
    else:
        if not 5 <= len(arrow_chain) <= 7:
            issues.append(ValidationIssue("arrow_chain", "must contain 5 to 7 nodes", "arrow_chain_length"))
        for index, node in enumerate(arrow_chain):
            node_text = text(node)
            if not node_text:
                issues.append(ValidationIssue(f"arrow_chain[{index}]", "node must not be empty", "arrow_chain_empty"))
            if len(node_text) > 18:
                issues.append(ValidationIssue(f"arrow_chain[{index}]", "node must be short", "arrow_chain_too_long"))

    final = text(payload.get("final_conclusion"))
    if not final:
        issues.append(ValidationIssue("final_conclusion", "must not be empty", "final_empty"))
    if final and not final.endswith(SENTENCE_ENDINGS):
        issues.append(ValidationIssue("final_conclusion", "must be a complete sentence", "final_sentence"))
    final_without_punctuation = final.rstrip("。！？!? ")
    if final_without_punctuation.endswith(INCOMPLETE_FINAL_SUFFIXES):
        issues.append(ValidationIssue("final_conclusion", "must not end with an unfinished connective", "final_incomplete"))
    if any(marker in final for marker in FINAL_CONCLUSION_FORBIDDEN_MARKERS):
        issues.append(ValidationIssue("final_conclusion", "must not contain viewing advice, score reasons, video evaluation, or caveats", "final_scope"))

    segments = payload.get("watch_segments")
    primary_segment: Optional[dict[str, Any]] = None
    if not isinstance(segments, list):
        issues.append(ValidationIssue("watch_segments", "must be a list", "segments_type"))
    else:
        if len(segments) > 3:
            issues.append(ValidationIssue("watch_segments", "must contain at most 3 segments", "segments_max"))
        priority_counts = {"primary": 0, "optional": 0, "backup": 0}
        previous_order = -1
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                issues.append(ValidationIssue(f"watch_segments[{index}]", "segment must be an object", "segment_type"))
                continue
            priority = text(segment.get("priority"))
            if priority not in priority_counts:
                issues.append(ValidationIssue(f"watch_segments[{index}].priority", "invalid priority", "segment_priority"))
                continue
            order = WATCH_SEGMENT_PRIORITY_ORDER.index(priority)
            if order <= previous_order:
                issues.append(ValidationIssue(f"watch_segments[{index}].priority", "segments must be ordered primary, optional, backup", "segment_order"))
            previous_order = order
            priority_counts[priority] += 1
            if priority == "primary":
                primary_segment = segment
            for field in ("start", "end", "title", "reason"):
                if not text(segment.get(field)):
                    issues.append(ValidationIssue(f"watch_segments[{index}].{field}", "required segment field is missing", "segment_required"))
            title = text(segment.get("title"))
            expected_prefix = WATCH_SEGMENT_TITLE_PREFIX[priority]
            if title and not title.startswith(expected_prefix):
                issues.append(ValidationIssue(f"watch_segments[{index}].title", f"must start with {expected_prefix}", "segment_title_prefix"))
            if has_non_pipe_range(segment.get("title")) or has_non_pipe_range(segment.get("reason")):
                issues.append(ValidationIssue(f"watch_segments[{index}]", "displayed time ranges must use start | end", "time_pipe"))
        if priority_counts["primary"] != 1:
            issues.append(ValidationIssue("watch_segments", "must contain exactly one primary segment", "primary_count"))
        if priority_counts["optional"] > 1:
            issues.append(ValidationIssue("watch_segments", "must contain at most one optional segment", "optional_count"))
        if priority_counts["backup"] > 1:
            issues.append(ValidationIssue("watch_segments", "must contain at most one backup segment", "backup_count"))

    only_one = text(payload.get("only_one_segment"))
    if has_non_pipe_range(only_one):
        issues.append(ValidationIssue("only_one_segment", "displayed time ranges must use start | end", "time_pipe"))
    if primary_segment:
        expected = (text(primary_segment.get("start")), text(primary_segment.get("end")))
        actual = first_pipe_range(only_one)
        if actual != expected:
            issues.append(ValidationIssue("only_one_segment", "must match primary start/end exactly", "only_one_match"))

    _validate_long_content_breakdown(payload, issues)

    score_basis = payload.get("score_basis")
    if not isinstance(score_basis, dict):
        issues.append(ValidationIssue("score_basis", "must be an object", "score_basis_type"))
    else:
        for key in SCORE_BASIS_KEYS:
            value = text(score_basis.get(key))
            if not value:
                issues.append(ValidationIssue(f"score_basis.{key}", "score basis is required", "score_basis_required"))
            elif len(value) < 12:
                issues.append(ValidationIssue(f"score_basis.{key}", "score basis must be concrete", "score_basis_concrete"))

    structured = payload.get("structured_assessment")
    if structured is not None:
        _validate_structured_assessment(structured, issues, "structured_assessment")
    else:
        issues.append(ValidationIssue("structured_assessment", "must be present for deterministic scoring", "structured_required"))

    _validate_score_trace_and_tag(payload, issues)
    _validate_stability_metadata(payload, issues)

    if not text(payload.get("confidence_note")):
        issues.append(ValidationIssue("confidence_note", "must not be empty", "confidence_note_empty"))

    if issues:
        raise WatchBriefValidationError(issues)

    return copy.deepcopy(payload)


def _validate_target_payload(payload: dict[str, Any], report_target: str, issues: list[ValidationIssue]) -> None:
    if report_target == DEFAULT_REPORT_TARGET:
        if "target_summary" in payload:
            issues.append(ValidationIssue("target_summary", "is only allowed for non-watch report targets", "target_unexpected"))
        if "target_sections" in payload:
            issues.append(ValidationIssue("target_sections", "is only allowed for non-watch report targets", "target_unexpected"))
        return

    if text(payload.get("report_target")) != report_target:
        issues.append(ValidationIssue("report_target", "must be explicit for non-watch report targets", "target_required"))
    if not text(payload.get("target_summary")):
        issues.append(ValidationIssue("target_summary", "must not be empty for non-watch report targets", "target_summary_required"))

    sections = payload.get("target_sections")
    required_keys = report_target_section_keys(report_target)
    if not isinstance(sections, dict):
        issues.append(ValidationIssue("target_sections", "must be an object for non-watch report targets", "target_sections_type"))
        return
    actual_keys = tuple(sections.keys())
    if actual_keys != required_keys:
        issues.append(ValidationIssue("target_sections", f"must contain keys in order: {list(required_keys)}", "target_sections_keys"))
    for key in required_keys:
        rows = sections.get(key)
        if not isinstance(rows, list):
            issues.append(ValidationIssue(f"target_sections.{key}", "must be a list of strings", "target_section_type"))
            continue
        if not 1 <= len(rows) <= 6:
            issues.append(ValidationIssue(f"target_sections.{key}", "must contain 1 to 6 rows", "target_section_length"))
        for index, row in enumerate(rows):
            if not text(row):
                issues.append(ValidationIssue(f"target_sections.{key}[{index}]", "row must not be empty", "target_section_row"))


def _validate_structured_assessment(value: Any, issues: list[ValidationIssue], path: str) -> None:
    if not isinstance(value, dict):
        issues.append(ValidationIssue(path, "must be an object", "structured_type"))
        return
    for key in STRUCTURED_ASSESSMENT_KEYS:
        score = value.get(key)
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            issues.append(ValidationIssue(f"{path}.{key}", "must be a number", "structured_score_type"))
        elif not 0 <= float(score) <= 10:
            issues.append(ValidationIssue(f"{path}.{key}", "must be between 0 and 10", "structured_score_range"))


def _validate_score_trace_and_tag(payload: dict[str, Any], issues: list[ValidationIssue]) -> None:
    score_trace = payload.get("score_trace")
    if not isinstance(score_trace, dict):
        issues.append(ValidationIssue("score_trace", "must be an object", "score_trace_type"))
        return
    for key in SCORE_TRACE_KEYS:
        if key not in score_trace:
            issues.append(ValidationIssue(f"score_trace.{key}", "required score trace field is missing", "score_trace_required"))
    try:
        expected = build_score_trace(payload)
    except Exception as exc:
        issues.append(ValidationIssue("score_trace", f"cannot compute deterministic score: {exc}", "score_trace_compute"))
        return
    numeric_keys = ("information_density", "evidence_quality", "originality", "watch_value", "computed_replacement_score")
    for key in numeric_keys:
        value = score_trace.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(ValidationIssue(f"score_trace.{key}", "must be a number", "score_trace_number"))
        elif round(float(value), 1) != float(expected[key]):
            issues.append(ValidationIssue(f"score_trace.{key}", "must match deterministic scoring inputs", "score_trace_mismatch"))
    if score_trace.get("formula") != expected["formula"]:
        issues.append(ValidationIssue("score_trace.formula", "must match deterministic formula", "score_trace_formula"))
    if score_trace.get("final_score_source") != SCORE_TRACE_FINAL_SOURCE:
        issues.append(ValidationIssue("score_trace.final_score_source", "must be deterministic_formula", "score_trace_source"))
    if score_trace.get("score_semantics") != "video_overall_value":
        issues.append(ValidationIssue("score_trace.score_semantics", "must be video_overall_value", "score_trace_semantics"))
    replacement_score = payload.get("replacement_score")
    if isinstance(replacement_score, (int, float)) and not isinstance(replacement_score, bool):
        if round(float(replacement_score), 1) != float(expected["computed_replacement_score"]):
            issues.append(ValidationIssue("replacement_score", "must match deterministic score_trace", "score_deterministic"))
        expected_tag = tag_for_score(float(expected["computed_replacement_score"]), payload.get("tag"))
        if text(payload.get("tag")) != expected_tag:
            issues.append(ValidationIssue("tag", "must match deterministic score band", "tag_score_conflict"))
        verdict = text(payload.get("watch_verdict"))
        score = float(expected["computed_replacement_score"])
        has_precise_watch_segments = bool(PIPE_TIME_RANGE_RE.search(verdict))
        has_strong_no_watch = any(marker in verdict for marker in ("不必看", "不用看", "无需看", "不推荐观看"))
        if score > 6.5 and has_strong_no_watch and not has_precise_watch_segments:
            issues.append(ValidationIssue("watch_verdict", "must not conflict with high video value", "watch_verdict_score_conflict"))
        if score < 5.0 and any(marker in verdict for marker in ("建议完整看", "建议完整观看", "值得补看", "值得完整看")):
            issues.append(ValidationIssue("watch_verdict", "must not conflict with low video value", "watch_verdict_score_conflict"))


def _validate_stability_metadata(payload: dict[str, Any], issues: list[ValidationIssue]) -> None:
    for key in ("transcript_hash", "qwen_prompt_fingerprint", "codex_prompt_fingerprint"):
        value = text(payload.get(key))
        if not SHA256_RE.fullmatch(value):
            issues.append(ValidationIssue(key, "must be a sha256 hex fingerprint", "stable_fingerprint"))
    for key in ("qwen_model_id", "codex_model"):
        if not text(payload.get(key)):
            issues.append(ValidationIssue(key, "must not be empty", "stable_model_id"))
    if text(payload.get("qwen_prompt_version")) != QWEN_LOCAL_EXTRACT_PROMPT_VERSION:
        issues.append(ValidationIssue("qwen_prompt_version", "must match Qwen prompt version", "stable_prompt_version"))
    if text(payload.get("codex_prompt_version")) != CODEX_REVIEW_PROMPT_VERSION:
        issues.append(ValidationIssue("codex_prompt_version", "must match Codex prompt version", "stable_prompt_version"))
    expected_formula_version = formula_version_for_weights(scoring_weights_from_payload(payload))
    if text(payload.get("scoring_formula_version")) != expected_formula_version:
        issues.append(ValidationIssue("scoring_formula_version", "must match scoring formula version", "stable_formula_version"))
    if text(payload.get("watchbrief_version")) != WATCHBRIEF_VERSION:
        issues.append(ValidationIssue("watchbrief_version", "must match WatchBrief version", "stable_watchbrief_version"))


def validate_renderer_input(payload: dict[str, Any]) -> dict[str, Any]:
    return validate_normalized_report_payload(payload)


def watch_order_density_value(video: dict[str, Any]) -> float:
    structured = video.get("structured_assessment")
    issues: list[ValidationIssue] = []
    _validate_structured_assessment(structured, issues, "structured_assessment")
    if issues:
        raise WatchBriefValidationError(issues)
    return float(structured["信息密度"])


def watch_order_density_percent(video: dict[str, Any]) -> int:
    return round(watch_order_density_value(video) * 10)


def validate_watch_order_payload(payload: dict[str, Any]) -> dict[str, Any]:
    issues: list[ValidationIssue] = []
    if not isinstance(payload, dict):
        raise WatchBriefValidationError([
            ValidationIssue("$", "watch order payload must be an object", "watch_order_type")
        ])
    videos = payload.get("videos")
    if not isinstance(videos, list):
        issues.append(ValidationIssue("videos", "must be a list", "videos_type"))
    else:
        for index, video in enumerate(videos):
            if not isinstance(video, dict):
                issues.append(ValidationIssue(f"videos[{index}]", "must be an object", "watch_order_video_type"))
                continue
            try:
                watch_order_density_value(video)
            except WatchBriefValidationError as exc:
                issues.extend(
                    ValidationIssue(f"videos[{index}].{issue.path}", issue.message, issue.code)
                    for issue in exc.issues
                )
    if issues:
        raise WatchBriefValidationError(issues)
    return copy.deepcopy(payload)
