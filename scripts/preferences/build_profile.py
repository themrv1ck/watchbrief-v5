#!/usr/bin/env python3
"""Create an explicit no-inference profile before content analysis is ready."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_EVENTS = Path("~/.watchbrief/preferences/events.jsonl").expanduser()
DEFAULT_OUTPUT = Path("~/.watchbrief/preferences/seth.yaml").expanduser()
VALID_SCHEMAS = {"watchbefore.event.v1", "watchfilter.event.v1"}


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.expanduser().read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict) and item.get("schema_version") in VALID_SCHEMAS and item.get("url"):
            item["schema_version"] = "watchbefore.event.v1"
            events.append(item)
    return events


def build_profile(events: list[dict[str, Any]]) -> str:
    history_events = sum(1 for event in events if str(event.get("source") or "") == "history")
    watch_later_events = sum(1 for event in events if str(event.get("source") or "") == "watch_later")
    profile = {
        "schema_version": "watchbefore.profile.v2",
        "source": "watchtrace_bilibili",
        "behavior_summary": {
            "total_events": len(events),
            "history_events": history_events,
            "watch_later_events_excluded": watch_later_events,
            "analyzed_reports": 0,
            "matched_history_reports": 0,
        },
        "interest_profile": {
            "status": "insufficient_content_analysis",
            "basis": "watchbefore_reports_and_history_engagement",
            "summary": "当前只有行为事实，没有 WatchBefore 内容报告，不能据此判断兴趣爱好。",
            "domains": [],
            "content_preferences": [],
            "note": "不从标题、作者或稍后再看记录猜测兴趣。",
        },
        "watch_strategy": {
            "current_rule": "报告先讲清视频内容；最终看不看由用户决定。",
            "scoring_rule": "内容分析完成前，话题兴趣按中性分处理。",
        },
    }
    return json.dumps(profile, ensure_ascii=False, indent=2) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a no-inference profile until WatchBefore content reports are ready."
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events = load_events(args.events)
    profile = build_profile(events)
    args.output.expanduser().parent.mkdir(parents=True, exist_ok=True)
    args.output.expanduser().write_text(profile, encoding="utf-8")
    print(f"profile_path: {args.output.expanduser()}")
    print(f"events: {len(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
