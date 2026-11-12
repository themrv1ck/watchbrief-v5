#!/usr/bin/env python3
"""Run WatchBefore reports one URL at a time for preference learning."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URLS = Path("~/.watchbrief/preferences/watchtrace_urls.txt").expanduser()
DEFAULT_RUN_ROOT = Path("~/.watchbrief/profile-learning-20260614").expanduser()


def load_urls(path: Path) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for raw in path.expanduser().read_text(encoding="utf-8").splitlines():
        url = raw.strip()
        if not url or url.startswith("#"):
            continue
        if url in seen:
            continue
        urls.append(url)
        seen.add(url)
    return urls


def safe_slug(value: str, fallback: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|#?&=%]+", "-", str(value or "").strip())
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80] or fallback


def url_slug(index: int, url: str) -> str:
    bvid_match = re.search(r"/video/([^/?#]+)", url)
    if bvid_match:
        suffix = safe_slug(bvid_match.group(1), "video")
    else:
        suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{index:03d}-{suffix}"


def has_normalized_payload(debug_dir: Path) -> bool:
    return any(debug_dir.rglob("normalized_payload.json"))


def has_local_extract(debug_dir: Path) -> bool:
    return any(debug_dir.rglob("local_extract.json"))


def has_completed_chunked_extract(debug_dir: Path) -> bool:
    for plan_path in debug_dir.rglob("local_extract_chunked_plan.json"):
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        chunks = plan.get("chunks", [])
        if not isinstance(chunks, list) or not chunks:
            continue
        chunk_count = int(plan.get("chunk_count") or len(chunks))
        min_coverage = float(plan.get("min_success_coverage") or 0.75)
        adapted_count = len(list((plan_path.parent / "local_extract_chunks").glob("chunk_*_adapted.json")))
        required_count = max(1, int(chunk_count * min_coverage))
        if adapted_count >= required_count:
            return True
    return False


def has_profile_analysis_artifact(debug_dir: Path) -> bool:
    return has_normalized_payload(debug_dir) or has_local_extract(debug_dir) or has_completed_chunked_extract(debug_dir)


def manifest_summary(debug_dir: Path) -> dict[str, Any]:
    manifest_path = debug_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        "completed_count": manifest.get("completed_count", 0),
        "failed_count": manifest.get("failed_count", 0),
        "total_count": manifest.get("total_count", 0),
        "manifest_path": str(manifest_path),
    }


def build_command(args: argparse.Namespace, url: str, output_dir: Path, debug_dir: Path) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "cli.py"),
        "--source-url",
        url,
        "--review-provider",
        args.review_provider,
        "--analysis-mode",
        args.analysis_mode,
        "--timeout",
        str(args.timeout),
        "--qwen-timeout",
        str(args.qwen_timeout),
        "--no-playlist-expansion",
        "--output-dir",
        str(output_dir),
        "--debug-dir",
        str(debug_dir),
    ]
    if args.no_browser_auth:
        command.append("--no-browser-auth")
    if args.cookies_file:
        command.extend(["--cookies-file", str(args.cookies_file.expanduser())])
    if args.force_audio_transcription:
        command.append("--force-audio-transcription")
    return command


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def run_command(args: argparse.Namespace, command: list[str], debug_dir: Path) -> tuple[int | None, str, str, bool, bool]:
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + args.process_timeout
    stopped_after_analysis = False
    timed_out = False
    while process.poll() is None:
        if args.stop_after_analysis_artifact and has_profile_analysis_artifact(debug_dir):
            stopped_after_analysis = True
            process.terminate()
            break
        if time.monotonic() >= deadline:
            timed_out = True
            process.kill()
            break
        time.sleep(args.poll_interval)

    try:
        stdout, stderr = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        timed_out = True
    return process.returncode, stdout, stderr, stopped_after_analysis, timed_out


def run_batch(args: argparse.Namespace) -> int:
    urls = load_urls(args.urls)
    selected = urls[args.start - 1:]
    if args.limit:
        selected = selected[:args.limit]
    run_root = args.run_root.expanduser()
    output_root = run_root / "output"
    debug_root = run_root / "debug-items"
    log_root = run_root / "logs"
    summary_path = run_root / "summary.jsonl"
    output_root.mkdir(parents=True, exist_ok=True)
    debug_root.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)

    completed = 0
    failed = 0
    skipped = 0
    for offset, url in enumerate(selected, start=args.start):
        slug = url_slug(offset, url)
        output_dir = output_root / slug
        debug_dir = debug_root / slug
        log_path = log_root / f"{slug}.log"
        already_done = has_profile_analysis_artifact(debug_dir) if args.stop_after_analysis_artifact else has_normalized_payload(debug_dir)
        if already_done and not args.force:
            skipped += 1
            row = {"index": offset, "url": url, "slug": slug, "status": "skipped", **manifest_summary(debug_dir)}
            append_jsonl(summary_path, row)
            print(f"[skip] {offset}/{len(urls)} {url}", flush=True)
            continue

        command = build_command(args, url, output_dir, debug_dir)
        print(f"[run] {offset}/{len(urls)} {url}", flush=True)
        try:
            returncode, stdout, stderr, stopped_after_analysis, timed_out = run_command(args, command, debug_dir)
            log_path.write_text(
                "COMMAND:\n"
                + " ".join(command)
                + "\n\nSTDOUT:\n"
                + stdout
                + "\n\nSTDERR:\n"
                + stderr,
                encoding="utf-8",
            )
            has_normalized = has_normalized_payload(debug_dir)
            has_analysis = has_profile_analysis_artifact(debug_dir)
            ok = (returncode == 0 and has_normalized) or (args.stop_after_analysis_artifact and has_analysis)
            if has_normalized and returncode == 0:
                status = "completed"
            elif args.stop_after_analysis_artifact and has_analysis:
                status = "analysis_completed"
            elif timed_out:
                status = "timeout"
            else:
                status = "failed"
            completed += 1 if ok else 0
            failed += 0 if ok else 1
            row = {
                "index": offset,
                "url": url,
                "slug": slug,
                "status": status,
                "returncode": returncode,
                "log_path": str(log_path),
                "stopped_after_analysis_artifact": stopped_after_analysis,
                "has_normalized_payload": has_normalized,
                "has_profile_analysis_artifact": has_analysis,
                **manifest_summary(debug_dir),
            }
            append_jsonl(summary_path, row)
            print(f"[{status}] {offset}/{len(urls)} rc={returncode}", flush=True)
        except subprocess.TimeoutExpired as exc:
            failed += 1
            log_path.write_text(str(exc), encoding="utf-8")
            row = {
                "index": offset,
                "url": url,
                "slug": slug,
                "status": "timeout",
                "log_path": str(log_path),
            }
            append_jsonl(summary_path, row)
            print(f"[timeout] {offset}/{len(urls)}", flush=True)

    print(json.dumps({
        "selected": len(selected),
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "summary_path": str(summary_path),
        "debug_root": str(debug_root),
        "output_root": str(output_root),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WatchBefore reports one URL at a time for preference learning.")
    parser.add_argument("--urls", type=Path, default=DEFAULT_URLS)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--review-provider", default="local")
    parser.add_argument("--analysis-mode", default="fast")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--qwen-timeout", type=int, default=600)
    parser.add_argument("--process-timeout", type=int, default=1200)
    parser.add_argument("--poll-interval", type=float, default=5)
    parser.add_argument("--stop-after-analysis-artifact", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-browser-auth", action="store_true")
    parser.add_argument("--cookies-file", type=Path)
    parser.add_argument("--force-audio-transcription", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    return run_batch(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
