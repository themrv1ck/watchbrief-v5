#!/usr/bin/env python3
"""Persistent, auditable catch-up runner for the WatchBefore interest profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HOME = Path.home()
PREFERENCES = HOME / ".watchbrief" / "preferences"
EVENTS = PREFERENCES / "events.jsonl"
FORMAL_PROFILE = PREFERENCES / "seth.yaml"
RUN_ROOT = HOME / ".watchbrief" / "profile-learning-weekly" / "profile-catchup-service"
REPORT_ROOTS = (
    ROOT / "outputs" / "history-audio-profile-20260730",
    HOME / ".watchbrief" / "profile-learning-weekly",
)
MODEL = "qwen3-30b-a3b-instruct-2507-mlx"
TARGET_COVERAGE = 0.7


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def cleanup_work(root: Path) -> None:
    """Remove transient audio and per-item pipeline caches, retaining audit logs."""
    for work_dir in root.rglob("_work"):
        if work_dir.is_dir():
            shutil.rmtree(work_dir, ignore_errors=True)


def run(command: list[str], *, log_path: Path | None = None, allow_failure: bool = False) -> int:
    if log_path:
        with log_path.open("a", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True)
    else:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode and not allow_failure:
        raise RuntimeError(f"command failed with exit code {result.returncode}: {command[0]}")
    return result.returncode


def qwen_probe() -> bool:
    request = urllib.request.Request(
        "http://127.0.0.1:1234/v1/chat/completions",
        data=json.dumps({"model": MODEL, "messages": [{"role": "user", "content": "仅回复 OK"}], "temperature": 0}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode())
        return bool(payload.get("choices"))
    except Exception:
        return False


def ensure_qwen(log_path: Path) -> None:
    if qwen_probe():
        return
    run(["lms", "load", MODEL, "--identifier", MODEL, "--ttl", "86400", "-y"], log_path=log_path)
    run(["lms", "server", "start", "--port", "1234", "--bind", "127.0.0.1"], log_path=log_path)
    if not qwen_probe():
        raise RuntimeError("local Qwen health probe failed after load")


def export_arc_cookie_file() -> tuple[Path, Path]:
    """Create a 0600 Netscape cookie file without exposing its contents."""
    import browser_cookie3  # type: ignore

    directory = Path(tempfile.mkdtemp(prefix="watchbefore-profile-arc."))
    output = directory / "arc-cookies.txt"
    source = browser_cookie3.arc(domain_name="bilibili.com")
    cookies = [item for item in source if str(getattr(item, "name", "") or "") and str(getattr(item, "value", "") or "")]
    if not cookies:
        shutil.rmtree(directory, ignore_errors=True)
        raise RuntimeError("Arc cookie export produced no Bilibili cookies")
    jar = MozillaCookieJar(str(output))
    for item in cookies:
        jar.set_cookie(item)
    jar.save(ignore_discard=True, ignore_expires=True)
    os.chmod(output, 0o600)
    return directory, output


def manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_candidate(candidate: Path) -> dict[str, Any]:
    from scripts.preference_profile import load_preference_profile

    profile = load_preference_profile(candidate)
    if profile["schema_version"] != "watchbefore.profile.v2":
        raise RuntimeError("candidate has wrong profile schema")
    summary = profile["behavior_summary"]
    if float(summary.get("analyzed_watch_time_coverage") or 0) < TARGET_COVERAGE:
        raise RuntimeError("candidate coverage is below 70%")
    domains = profile["interest_profile"]["domains"]
    if not domains:
        raise RuntimeError("candidate has no interest domains")
    previous: tuple[int, int] | None = None
    for domain in domains:
        if int(domain.get("evidence_count") or 0) < 3:
            raise RuntimeError("candidate domain has fewer than three evidence items")
        current = (int(domain.get("watched_seconds") or 0), int(domain.get("watch_count") or 0))
        if previous and current > previous:
            raise RuntimeError("candidate domains are not sorted by watch time then watch count")
        previous = current
        domain_text = " ".join(str(domain.get(key) or "") for key in ("name", "description", "subtopics", "keywords"))
        if "稍后再看" in domain_text or "watch_later" in domain_text.lower():
            raise RuntimeError("candidate contains a watch-later domain")
    return profile


def promote(candidate: Path) -> str:
    backup = PREFERENCES / "backups" / f"profile-catchup-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir(parents=True, exist_ok=True)
    if FORMAL_PROFILE.exists():
        shutil.copy2(FORMAL_PROFILE, backup / "seth.yaml")
    os.replace(candidate, FORMAL_PROFILE)
    return sha256(FORMAL_PROFILE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    args = parser.parse_args()
    run_root = args.run_root.expanduser()
    run_root.mkdir(parents=True, exist_ok=True)
    status_path = run_root / "service-status.json"
    log_path = run_root / "service.log"
    sample_path = run_root / "sample-manifest.json"
    urls_path = run_root / "missing-urls.txt"
    candidate = run_root / "candidate-seth.yaml"
    status: dict[str, Any] = {"state": "starting", "started_at": time.time(), "formal_profile_sha256_before": sha256(FORMAL_PROFILE)}
    atomic_json(status_path, status)
    try:
        ensure_qwen(log_path)
        report_args = [str(path) for path in REPORT_ROOTS]
        run([
            sys.executable, "scripts/preferences/select_history_audio_sample.py", "--events", str(EVENTS),
            "--reports", *report_args, "--target-watch-time-coverage", str(TARGET_COVERAGE),
            "--output-manifest", str(sample_path), "--output-urls", str(urls_path),
        ], log_path=log_path)
        sample = manifest(sample_path)
        status.update({"state": "sampling", "sample": sample})
        atomic_json(status_path, status)
        if int(sample["selected_video_count"]) > 0:
            cookie_dir, cookie_file = export_arc_cookie_file()
            try:
                status.update({"state": "analyzing", "temporary_auth": "active"})
                atomic_json(status_path, status)
                run([
                    sys.executable, "scripts/preferences/run_profile_learning_batch.py", "--urls", str(urls_path),
                    "--run-root", str(run_root / "batch"), "--review-provider", "local", "--analysis-mode", "fast",
                    "--timeout", "600", "--qwen-timeout", "600", "--process-timeout", "1200", "--poll-interval", "2",
                    "--stop-after-analysis-artifact", "--force-audio-transcription", "--cookies-file", str(cookie_file),
                ], log_path=log_path, allow_failure=True)
            finally:
                shutil.rmtree(cookie_dir, ignore_errors=True)
        run([
            sys.executable, "scripts/preferences/select_history_audio_sample.py", "--events", str(EVENTS),
            "--reports", *report_args, "--target-watch-time-coverage", str(TARGET_COVERAGE),
            "--output-manifest", str(run_root / "coverage-final.json"), "--output-urls", str(run_root / "remaining-urls.txt"),
        ], log_path=log_path)
        final_coverage = manifest(run_root / "coverage-final.json")
        status.update({"state": "validating", "temporary_auth": "deleted", "final_coverage": final_coverage})
        atomic_json(status_path, status)
        if float(final_coverage["analyzed_watch_time_coverage_before"]) < TARGET_COVERAGE:
            raise RuntimeError("coverage remains below 70% after batch")
        run([
            sys.executable, "scripts/preferences/build_content_profile.py", "--events", str(EVENTS),
            "--reports", *report_args, "--output", str(candidate), "--qwen-model", MODEL, "--qwen-timeout", "600",
            "--debug-dir", str(run_root / "interest-synthesis"),
        ], log_path=log_path)
        profile = validate_candidate(candidate)
        status.update({"state": "completed", "profile_sha256": promote(candidate), "profile_behavior_summary": profile["behavior_summary"], "finished_at": time.time()})
        atomic_json(status_path, status)
        return 0
    except Exception as exc:
        status.update({"state": "failed", "error": str(exc), "temporary_auth": "deleted", "finished_at": time.time(), "formal_profile_sha256_after": sha256(FORMAL_PROFILE)})
        atomic_json(status_path, status)
        return 1
    finally:
        cleanup_work(run_root / "batch")


if __name__ == "__main__":
    raise SystemExit(main())
