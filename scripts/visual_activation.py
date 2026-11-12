"""Deterministic per-video decision for optional visual evidence."""

from __future__ import annotations

import re
from math import ceil
from typing import Any


AUTO_VISUAL_MAX_DURATION_SECONDS = 1800
AUTO_VISUAL_MAX_FRAMES = 181
VISUAL_CUE_PATTERNS = {
    "screen": re.compile(r"屏幕|界面|画面(?:中|上|里|显示)|这里(?:显示|可以看到)|图中|图表|幻灯片|PPT|on.screen|as.shown", re.I),
    "operation": re.compile(r"点击|点开|拖动|拖拽|拖到|滑动|按(?:下|住).{0,4}按钮|打开.{0,8}菜单|调(?:整|节).{0,8}参数|调一下|抓.{0,4}静帧|加.{0,4}节点|操作步骤", re.I),
    "comparison": re.compile(r"前后对比|效果对比|对比画面|实机演示|实际演示|展示.{0,8}效果|before.and.after", re.I),
    "visual_form": re.compile(r"构图|运镜|镜头语言|色彩(?:变化|对比|设计)|光影|调色|仿色|胶片感", re.I),
}


def decide_visual_analysis(
    mode: str,
    transcript_segments: list[dict[str, Any]],
    *,
    duration_seconds: int | None,
    max_gap_seconds: float = 10.0,
) -> dict[str, Any]:
    """Return an auditable decision; title and channel are intentionally excluded."""
    if mode not in {"auto", "on", "off"}:
        raise ValueError(f"unsupported visual analysis mode: {mode}")
    if mode == "on":
        return {"mode": mode, "enabled": True, "reason_code": "explicit_on", "cue_groups": [], "cue_segment_count": 0}
    if mode == "off":
        return {"mode": mode, "enabled": False, "reason_code": "explicit_off", "cue_groups": [], "cue_segment_count": 0}
    if duration_seconds is None:
        return {"mode": mode, "enabled": False, "reason_code": "duration_unavailable", "cue_groups": [], "cue_segment_count": 0}
    if max_gap_seconds <= 0:
        raise ValueError("visual frame gap must be positive")
    estimated_frames = ceil(duration_seconds / max_gap_seconds) + 1
    if duration_seconds > AUTO_VISUAL_MAX_DURATION_SECONDS or estimated_frames > AUTO_VISUAL_MAX_FRAMES:
        return {
            "mode": mode,
            "enabled": False,
            "reason_code": "auto_frame_budget_exceeded",
            "duration_seconds": duration_seconds,
            "max_duration_seconds": AUTO_VISUAL_MAX_DURATION_SECONDS,
            "estimated_frame_count": estimated_frames,
            "max_auto_frame_count": AUTO_VISUAL_MAX_FRAMES,
            "cue_groups": [],
            "cue_segment_count": 0,
        }

    groups: set[str] = set()
    matching_segments = 0
    for segment in transcript_segments:
        if not isinstance(segment, dict):
            continue
        content = str(segment.get("text") or "")
        hits = {name for name, pattern in VISUAL_CUE_PATTERNS.items() if pattern.search(content)}
        if hits:
            matching_segments += 1
            groups.update(hits)
    enabled = matching_segments >= 2 and len(groups) >= 2 and bool(groups.intersection({"screen", "comparison"}))
    return {
        "mode": mode,
        "enabled": enabled,
        "reason_code": "transcript_visual_dependency" if enabled else "insufficient_visual_evidence",
        "duration_seconds": duration_seconds,
        "estimated_frame_count": estimated_frames,
        "cue_groups": sorted(groups),
        "cue_segment_count": matching_segments,
    }
