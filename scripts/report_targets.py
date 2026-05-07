"""Report target definitions for WatchBrief V5."""

from __future__ import annotations

from typing import Any


DEFAULT_REPORT_TARGET = "watch_decision"
REPORT_TARGETS = (
    DEFAULT_REPORT_TARGET,
    "text_structure",
    "knowledge_notes",
    "viewpoint_breakdown",
    "creation_review",
)

REPORT_TARGET_LABELS = {
    DEFAULT_REPORT_TARGET: "观看决策",
    "text_structure": "文本结构分析",
    "knowledge_notes": "知识笔记",
    "viewpoint_breakdown": "观点拆解",
    "creation_review": "创作复盘",
}

REPORT_TARGET_DESCRIPTIONS = {
    DEFAULT_REPORT_TARGET: "判断报告能否替代原视频、原视频是否还值得看，以及应该看哪一段。",
    "text_structure": "分析内容如何开场、推进、转折和收束，适合学习文本组织方式。",
    "knowledge_notes": "整理概念、事实、方法和边界，适合沉淀可复习的知识笔记。",
    "viewpoint_breakdown": "拆开主张、前提、论据和争议点，适合判断观点是否站得住。",
    "creation_review": "复盘选题、钩子、节奏、素材组织和可复用创作经验。",
}

REPORT_TARGET_SECTION_SPECS = {
    "text_structure": (
        ("structure_overview", "结构骨架"),
        ("progression_logic", "推进顺序"),
        ("transition_methods", "转场与连接"),
        ("compression_pattern", "可复用结构"),
    ),
    "knowledge_notes": (
        ("core_concepts", "核心概念"),
        ("key_facts", "关键知识"),
        ("methods", "方法与步骤"),
        ("caveats", "边界提醒"),
    ),
    "viewpoint_breakdown": (
        ("claims", "主要观点"),
        ("assumptions", "隐含前提"),
        ("evidence", "论据链条"),
        ("counterpoints", "可争议点"),
    ),
    "creation_review": (
        ("positioning", "选题定位"),
        ("hook_and_pacing", "开场与节奏"),
        ("material_use", "素材组织"),
        ("reuse_notes", "可复用经验"),
    ),
}


def normalize_report_target(value: Any) -> str:
    target = str(value or DEFAULT_REPORT_TARGET).strip() or DEFAULT_REPORT_TARGET
    if target not in REPORT_TARGETS:
        raise ValueError(f"unsupported report target: {target}")
    return target


def report_target_label(target: Any) -> str:
    normalized = normalize_report_target(target)
    return REPORT_TARGET_LABELS[normalized]


def report_target_description(target: Any) -> str:
    normalized = normalize_report_target(target)
    return REPORT_TARGET_DESCRIPTIONS[normalized]


def report_target_section_specs(target: Any) -> tuple[tuple[str, str], ...]:
    normalized = normalize_report_target(target)
    return REPORT_TARGET_SECTION_SPECS.get(normalized, ())


def report_target_section_keys(target: Any) -> tuple[str, ...]:
    return tuple(key for key, _label in report_target_section_specs(target))


def report_target_contract(target: Any) -> dict[str, Any]:
    normalized = normalize_report_target(target)
    return {
        "report_target": normalized,
        "label": report_target_label(normalized),
        "description": report_target_description(normalized),
        "target_summary": "string，中文，概括该目标模式下最重要的结论。",
        "target_sections": {
            key: f"array[string]，中文，{label}，1 到 6 条，每条必须来自视频内容证据。"
            for key, label in report_target_section_specs(normalized)
        },
    }
