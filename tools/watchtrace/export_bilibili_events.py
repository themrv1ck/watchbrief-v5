#!/usr/bin/env python3
"""Export Bilibili watch behavior into WatchBefore event files.

This script reads browser login state through browser_cookie3, but never prints
or stores cookie values. It stores only normalized viewing events.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "watchbefore.event.v1"
LEGACY_SCHEMA_VERSIONS = {"watchfilter.event.v1"}
HISTORY_ENDPOINT = "https://api.bilibili.com/x/web-interface/history/cursor"
WATCH_LATER_ENDPOINT = "https://api.bilibili.com/x/v2/history/toview"
DEFAULT_BROWSER_ORDER = ("chrome", "arc", "safari", "edge")
DEFAULT_OUTPUT = Path("~/.watchbrief/preferences/events.jsonl").expanduser()
DEFAULT_URLS_OUTPUT = Path("~/.watchbrief/preferences/watchtrace_urls.txt").expanduser()
DEFAULT_SUMMARY_OUTPUT = Path("~/.watchbrief/preferences/watchtrace_import_summary.json").expanduser()
DEFAULT_BUCKET_DIR = Path("~/.watchbrief/preferences/watchtrace_buckets").expanduser()


class WatchTraceExportError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def to_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            return int(float(value))
    return default


def bvid_from_url(url: str) -> str:
    match = re.search(r"/video/(BV[0-9A-Za-z]+)", url) or re.search(r"\b(BV[0-9A-Za-z]+)\b", url)
    return match.group(1) if match else ""


def canonical_url(raw: Any, bvid: str = "") -> str:
    text = str(raw or "")
    resolved_bvid = bvid or bvid_from_url(text)
    if resolved_bvid:
        return f"https://www.bilibili.com/video/{resolved_bvid}"
    if text.startswith(("http://", "https://")):
        return text.split("?", 1)[0].split("#", 1)[0]
    return ""


def watch_ratio(watched_seconds: int, duration_seconds: int) -> float:
    if duration_seconds <= 0:
        return 0.0
    ratio = max(0.0, min(1.0, watched_seconds / duration_seconds))
    return round(ratio, 4)


def watched_seconds_from_progress(progress: Any, duration_seconds: int) -> int:
    progress_number = to_int(progress, 0)
    if progress_number < 0 and duration_seconds > 0:
        return duration_seconds
    if duration_seconds > 0:
        return max(0, min(duration_seconds, progress_number))
    return max(0, progress_number)


def normalize_item(item: dict[str, Any], *, source: str, observed_at: str) -> dict[str, Any] | None:
    history = item.get("history") if isinstance(item.get("history"), dict) else {}
    bvid = clean_text(item.get("bvid") or history.get("bvid") or item.get("param"))
    url = canonical_url(item.get("uri") or item.get("url"), bvid)
    duration_seconds = max(0, to_int(item.get("duration") or item.get("page_duration") or item.get("total_duration"), 0))
    watched_seconds = 0 if source == "watch_later" else watched_seconds_from_progress(item.get("progress"), duration_seconds)
    title = clean_text(item.get("title") or item.get("long_title") or item.get("name"))
    author = clean_text(
        item.get("author_name")
        or item.get("author")
        or (item.get("owner") or {}).get("name")
        or (item.get("upper") or {}).get("name")
    )
    if not url or not title:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": "bilibili",
        "url": url,
        "bvid": bvid or bvid_from_url(url),
        "title": title,
        "author": author,
        "duration_seconds": duration_seconds,
        "watched_seconds": watched_seconds,
        "watch_ratio": 0.0 if source == "watch_later" else watch_ratio(watched_seconds, duration_seconds),
        "source": source,
        "observed_at": observed_at,
    }


def cookie_header_from_browser(browser: str) -> str:
    try:
        import browser_cookie3  # type: ignore
    except Exception as exc:
        raise WatchTraceExportError(f"browser_cookie3 unavailable: {exc}") from exc

    loader = getattr(browser_cookie3, browser.replace("-", "_").lower(), None)
    if loader is None:
        return ""
    try:
        jar = loader(domain_name="bilibili.com")
    except Exception:
        return ""

    pairs: list[str] = []
    for cookie in jar:
        name = str(getattr(cookie, "name", "") or "")
        value = str(getattr(cookie, "value", "") or "")
        if name and value:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def get_json(url: str, *, cookie_header: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 WatchTrace/0.1",
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json,text/plain,*/*",
            "Cookie": cookie_header,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise WatchTraceExportError(f"HTTP {exc.code} for {url}") from exc
    except Exception as exc:
        raise WatchTraceExportError(f"request failed for {url}: {exc}") from exc

    code = payload.get("code")
    if code not in (None, 0):
        message = clean_text(payload.get("message") or "unknown")
        raise WatchTraceExportError(f"Bilibili API {code}: {message}")
    return payload


def api_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("list", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def fetch_history(cookie_header: str, *, max_pages: int, timeout: int, observed_at: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    max_value = 0
    view_at = 0
    for _page in range(max_pages):
        params = urllib.parse.urlencode({"ps": 30, "max": max_value, "view_at": view_at, "business": "archive"})
        payload = get_json(f"{HISTORY_ENDPOINT}?{params}", cookie_header=cookie_header, timeout=timeout)
        items = api_list(payload)
        for item in items:
            event = normalize_item(item, source="history", observed_at=observed_at)
            if event:
                events.append(event)
        cursor = payload.get("data", {}).get("cursor", {}) if isinstance(payload.get("data"), dict) else {}
        next_max = to_int(cursor.get("max"), 0)
        next_view_at = to_int(cursor.get("view_at"), 0)
        if not items or not next_max or next_max == max_value:
            break
        max_value = next_max
        view_at = next_view_at
    return events


def fetch_watch_later(cookie_header: str, *, timeout: int, observed_at: str) -> list[dict[str, Any]]:
    payload = get_json(f"{WATCH_LATER_ENDPOINT}?ps=100", cookie_header=cookie_header, timeout=timeout)
    events: list[dict[str, Any]] = []
    for item in api_list(payload):
        event = normalize_item(item, source="watch_later", observed_at=observed_at)
        if event:
            events.append(event)
    return events


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("schema_version") in {SCHEMA_VERSION, *LEGACY_SCHEMA_VERSIONS} and item.get("url"):
            item["schema_version"] = SCHEMA_VERSION
            events.append(item)
    return events


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for event in events:
        key = f"{event.get('source')}:{event.get('bvid') or event.get('url')}"
        by_key[key] = event
    return list(by_key.values())


def behavior_bucket(event: dict[str, Any]) -> str:
    if event.get("source") == "watch_later":
        return "queued"
    ratio = float(event.get("watch_ratio") or 0)
    if ratio >= 0.9:
        return "completed"
    if ratio >= 0.2:
        return "partial"
    if ratio < 0.05:
        return "bounce"
    return "skimmed"


def write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")


def write_urls(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    urls = []
    seen = set()
    for event in events:
        url = str(event.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    path.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")


def write_bucket_urls(bucket_dir: Path, events: list[dict[str, Any]]) -> dict[str, str]:
    bucket_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[str]] = {}
    for event in events:
        bucket = behavior_bucket(event)
        url = str(event.get("url") or "")
        if not url:
            continue
        grouped.setdefault(bucket, [])
        if url not in grouped[bucket]:
            grouped[bucket].append(url)

    outputs: dict[str, str] = {}
    for bucket in ("completed", "partial", "skimmed", "bounce", "queued"):
        path = bucket_dir / f"{bucket}_urls.txt"
        urls = grouped.get(bucket, [])
        path.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
        outputs[bucket] = str(path)
    return outputs


def write_summary(path: Path, *, events: list[dict[str, Any]], added_count: int, browser: str, errors: list[str]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for event in events:
        bucket = behavior_bucket(event)
        counts[bucket] = counts.get(bucket, 0) + 1
    summary = {
        "schema_version": "watchbefore.import_summary.v1",
        "total_events": len(events),
        "added_events": added_count,
        "selected_browser": browser,
        "behavior_counts": counts,
        "errors": errors,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def browser_order(value: str) -> tuple[str, ...]:
    if value == "auto":
        return DEFAULT_BROWSER_ORDER
    return tuple(part.strip().lower() for part in value.split(",") if part.strip())


def export_events(args: argparse.Namespace) -> dict[str, Any]:
    observed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    errors: list[str] = []
    selected_browser = ""
    fetched: list[dict[str, Any]] = []

    for browser in browser_order(args.browser):
        cookie_header = cookie_header_from_browser(browser)
        if not cookie_header:
            errors.append(f"{browser}: no readable Bilibili login state")
            continue
        current: list[dict[str, Any]] = []
        try:
            current.extend(fetch_history(cookie_header, max_pages=args.max_history_pages, timeout=args.timeout, observed_at=observed_at))
        except WatchTraceExportError as exc:
            errors.append(f"{browser}: history failed: {exc}")
        try:
            current.extend(fetch_watch_later(cookie_header, timeout=args.timeout, observed_at=observed_at))
        except WatchTraceExportError as exc:
            errors.append(f"{browser}: watch_later failed: {exc}")
        if current:
            selected_browser = browser
            fetched = current
            break

    if not fetched:
        raise WatchTraceExportError("no Bilibili events exported; " + "; ".join(errors))

    output = args.output.expanduser()
    existing = [] if args.replace else load_events(output)
    merged = dedupe_events(existing + fetched)
    added_count = max(0, len(merged) - len(existing))
    write_jsonl(output, merged)
    write_urls(args.urls_output.expanduser(), merged)
    bucket_paths = write_bucket_urls(args.bucket_dir.expanduser(), merged)
    summary = write_summary(
        args.summary_output.expanduser(),
        events=merged,
        added_count=added_count,
        browser=selected_browser,
        errors=errors,
    )
    summary["events_path"] = str(output)
    summary["urls_path"] = str(args.urls_output.expanduser())
    summary["bucket_paths"] = bucket_paths
    summary["summary_path"] = str(args.summary_output.expanduser())
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Bilibili history/watch-later events for WatchBefore.")
    parser.add_argument("--browser", default="auto", help="auto or comma-separated browsers: chrome,arc,safari,edge")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--urls-output", type=Path, default=DEFAULT_URLS_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY_OUTPUT)
    parser.add_argument("--bucket-dir", type=Path, default=DEFAULT_BUCKET_DIR)
    parser.add_argument("--max-history-pages", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--replace", action="store_true", help="replace existing events instead of merging")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = export_events(args)
    except WatchTraceExportError as exc:
        print(f"WatchTrace export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
