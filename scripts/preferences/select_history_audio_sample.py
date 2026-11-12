#!/usr/bin/env python3
"""Select the smallest missing transcript sample needed for watch-time coverage."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.preferences.build_content_profile import (
        history_observations_by_url,
        load_events,
        load_report_paths,
        load_reports,
        url_key,
    )
except ModuleNotFoundError:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.preferences.build_content_profile import (
        history_observations_by_url,
        load_events,
        load_report_paths,
        load_reports,
        url_key,
    )


DEFAULT_EVENTS = Path("~/.watchbrief/preferences/events.jsonl").expanduser()
SINGLE_BILIBILI_VIDEO_PATTERN = re.compile(
    r"^https?://(?:www\.)?bilibili\.com/video/[^/?#]+",
    flags=re.IGNORECASE,
)


def is_single_bilibili_video_url(value: str) -> bool:
    return bool(SINGLE_BILIBILI_VIDEO_PATTERN.match(str(value or "").strip()))


def select_missing_history_sample(
    events: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    *,
    target_watch_time_coverage: float,
    excluded_urls: set[str] | None = None,
) -> dict[str, Any]:
    if not 0 < target_watch_time_coverage <= 1:
        raise ValueError("target_watch_time_coverage must be greater than 0 and at most 1")
    history = history_observations_by_url(events)
    excluded = {url_key(item) for item in (excluded_urls or set()) if url_key(item)}
    positive_history = {
        key: value
        for key, value in history.items()
        if int(value.get("watched_seconds") or 0) > 0
    }
    analyzed_urls = {url_key(report.get("url")) for report in reports}
    total_watched_seconds = sum(
        int(item.get("watched_seconds") or 0) for item in positive_history.values()
    )
    analyzed_watched_seconds = sum(
        int(item.get("watched_seconds") or 0)
        for key, item in positive_history.items()
        if key in analyzed_urls
    )
    unsupported_missing = [
        item
        for key, item in positive_history.items()
        if key not in analyzed_urls and not is_single_bilibili_video_url(key)
    ]
    excluded_missing = [
        item
        for key, item in positive_history.items()
        if key not in analyzed_urls and key in excluded
    ]
    missing = [
        {
            "url": key,
            "watched_seconds": int(item.get("watched_seconds") or 0),
            "watch_count": int(item.get("watch_count") or 0),
            "watch_ratio": float(item.get("watch_ratio") or 0),
            "duration_seconds": int(item.get("duration_seconds") or 0),
        }
        for key, item in positive_history.items()
        if (
            key not in analyzed_urls
            and key not in excluded
            and is_single_bilibili_video_url(key)
        )
    ]
    missing.sort(
        key=lambda item: (
            int(item["watched_seconds"]),
            int(item["watch_count"]),
            str(item["url"]),
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    projected_watched_seconds = analyzed_watched_seconds
    for item in missing:
        if (
            total_watched_seconds > 0
            and projected_watched_seconds / total_watched_seconds
            >= target_watch_time_coverage
        ):
            break
        selected.append(item)
        projected_watched_seconds += int(item["watched_seconds"])

    def coverage(value: int) -> float:
        return round(value / total_watched_seconds, 6) if total_watched_seconds else 0.0

    return {
        "schema_version": "watchbefore.history_audio_sample.v1",
        "selection_rule": (
            "Rank missing analyzed videos by cumulative watched_seconds, then watch_count; "
            "select the smallest prefix reaching target watch-time coverage."
        ),
        "target_watch_time_coverage": target_watch_time_coverage,
        "history_positive_watch_videos": len(positive_history),
        "history_watched_seconds": total_watched_seconds,
        "analyzed_watch_videos_before": sum(
            1 for key in positive_history if key in analyzed_urls
        ),
        "analyzed_watched_seconds_before": analyzed_watched_seconds,
        "analyzed_watch_time_coverage_before": coverage(analyzed_watched_seconds),
        "selected_video_count": len(selected),
        "unsupported_missing_video_count": len(unsupported_missing),
        "unsupported_missing_watched_seconds": sum(
            int(item.get("watched_seconds") or 0) for item in unsupported_missing
        ),
        "excluded_missing_video_count": len(excluded_missing),
        "excluded_missing_watched_seconds": sum(
            int(item.get("watched_seconds") or 0) for item in excluded_missing
        ),
        "excluded_urls": sorted(excluded),
        "selected_watched_seconds": sum(
            int(item["watched_seconds"]) for item in selected
        ),
        "selected_full_duration_seconds": sum(
            int(item["duration_seconds"]) for item in selected
        ),
        "projected_analyzed_watched_seconds": projected_watched_seconds,
        "projected_watch_time_coverage": coverage(projected_watched_seconds),
        "selected": selected,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select missing Bilibili history videos for audio transcription by watch-time coverage."
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--reports", nargs="+", required=True)
    parser.add_argument("--target-watch-time-coverage", type=float, default=0.8)
    parser.add_argument("--exclude-url", action="append", default=[])
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--output-urls", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sample = select_missing_history_sample(
        load_events(args.events),
        load_reports(load_report_paths(args.reports)),
        target_watch_time_coverage=args.target_watch_time_coverage,
        excluded_urls=set(args.exclude_url),
    )
    manifest_path = args.output_manifest.expanduser()
    urls_path = args.output_urls.expanduser()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    urls_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(sample, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    urls_path.write_text(
        "".join(f"{item['url']}\n" for item in sample["selected"]),
        encoding="utf-8",
    )
    print(f"sample_manifest: {manifest_path}")
    print(f"sample_urls: {urls_path}")
    print(f"selected_video_count: {sample['selected_video_count']}")
    print(f"coverage_before: {sample['analyzed_watch_time_coverage_before']}")
    print(f"projected_coverage: {sample['projected_watch_time_coverage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
