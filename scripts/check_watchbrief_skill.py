#!/usr/bin/env python3
"""Self-check script for WatchBrief V5 package integrity and discoverability."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
GOLDEN_REQUIRED = [
    "mock_transcript_heartflow.json",
    "sample_payload_heartflow.json",
    "sample_payload_charm.json",
    "sample_heartflow.html",
    "sample_charm.html",
]
REQUIRED_TEST_FILES = [
    "test_field_contract.py",
    "test_validator.py",
    "test_renderer.py",
    "test_template_sources.py",
    "test_watch_order.py",
    "test_webui.py",
    "test_external_model_adapters.py",
    "test_video_pipeline.py",
    "test_acquisition_scope.py",
    "test_bilibili_content_provider.py",
    "test_analyzer_codex_review.py",
]
REQUIRED_SCRIPTS = [
    "scripts/cli.py",
    "scripts/check_watchbrief_skill.py",
    "scripts/video_pipeline.py",
    "scripts/renderer.py",
    "scripts/watch_order.py",
    "scripts/webui.py",
    "scripts/bilibili_content_provider.py",
    "scripts/analyzer/codex_review.py",
    "scripts/analyzer/cloud_review.py",
    "scripts/analyzer/external_extract.py",
    "scripts/analyzer/local_extract.py",
    "scripts/analyzer/model_clients.py",
    "scripts/transcript_source_adapter.py",
]
VIDEO_TEMPLATE_REQUIRED = [
    "视频到底讲了什么？",
    "如果要看，只看哪里？",
    "one-line brief",
    "final-conclusion",
    "feedback-launcher",
]
VIDEO_TEMPLATE_FORBIDDEN = [
    "categoryConfig",
    "rank-card",
    "filter-banner",
]
WATCH_ORDER_TEMPLATE_REQUIRED = [
    "视频提炼 · 观看顺序",
    "filter-banner",
    "rank-card",
    "categoryConfig",
    "strong",
    "medium",
    "low",
    "skip",
    "failed",
]
WATCH_ORDER_TEMPLATE_FORBIDDEN = [
    "视频到底讲了什么？",
    "如果要看，只看哪里？",
    "feedback-launcher",
]


def fail(message: str) -> tuple[str, bool]:
    return message, False


def ok(message: str) -> tuple[str, bool]:
    return message, True


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, *rest = text.split("---", 2)
    if not rest:
        return {}
    block = rest[0]
    result: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip("\"'")
    return result


def check_skill_frontmatter(skill_path: Path) -> tuple[str, bool]:
    if not skill_path.exists():
        return fail("SKILL.md missing")
    data = parse_frontmatter(skill_path)
    if data.get("name") != "watchbrief_v5":
        return fail("SKILL frontmatter missing name: watchbrief_v5")
    if not data.get("description"):
        return fail("SKILL frontmatter missing description")
    if "WatchBrief V5" not in data["description"]:
        return fail("SKILL description should mention WatchBrief V5")
    return ok("SKILL.md frontmatter valid")


def check_agent_config(agent_path: Path) -> tuple[str, bool]:
    if not agent_path.exists():
        return fail("agents/openai.yaml missing")
    text = agent_path.read_text(encoding="utf-8")
    display = re.search(r"^\s*display_name:\s*(.+)$", text, re.M)
    prompt = re.search(r"^\s*default_prompt:\s*(.+)$", text, re.M)
    if not display:
        return fail("openai.yaml missing display_name")
    if not prompt:
        return fail("openai.yaml missing default_prompt")
    if display.group(1).strip().strip('\"') != "WatchBrief":
        return fail("display_name should be WatchBrief")
    if "$watchbrief_v5" not in prompt.group(1):
        return fail("default_prompt should mention $watchbrief_v5")
    return ok("agents/openai.yaml valid")


def check_required_paths() -> list[tuple[str, bool]]:
    paths = [
        ("watchbrief_v5/README.md", BASE_DIR / "README.md"),
        ("watchbrief_v5/ACKNOWLEDGEMENTS.md", BASE_DIR / "ACKNOWLEDGEMENTS.md"),
        ("watchbrief_v5/CONTRACT_V5.md", BASE_DIR / "CONTRACT_V5.md"),
        ("references/video_report_v5.html", BASE_DIR / "references" / "video_report_v5.html"),
        ("references/00watch_order_v5.html", BASE_DIR / "references" / "00watch_order_v5.html"),
        ("schemas/single_video_report.schema.json", BASE_DIR / "schemas" / "single_video_report.schema.json"),
        ("schemas/watch_order.schema.json", BASE_DIR / "schemas" / "watch_order.schema.json"),
    ]

    checks: list[tuple[str, bool]] = []
    for label, path in paths:
        checks.append(ok(label) if path.exists() else fail(f"missing {label}"))
    return checks


def check_template_sources() -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    video_path = BASE_DIR / "references" / "video_report_v5.html"
    watch_order_path = BASE_DIR / "references" / "00watch_order_v5.html"
    if not video_path.exists() or not watch_order_path.exists():
        return [fail("template source check skipped because template file is missing")]

    video_text = video_path.read_text(encoding="utf-8")
    watch_order_text = watch_order_path.read_text(encoding="utf-8")
    if video_text == watch_order_text:
        checks.append(fail("video_report_v5.html and 00watch_order_v5.html must not be identical"))
    else:
        checks.append(ok("video/watch-order template sources are distinct"))

    for marker in VIDEO_TEMPLATE_REQUIRED:
        checks.append(ok(f"video template contains {marker}") if marker in video_text else fail(f"video template missing {marker}"))
    for marker in VIDEO_TEMPLATE_FORBIDDEN:
        checks.append(
            ok(f"video template excludes watch-order marker {marker}")
            if marker not in video_text
            else fail(f"video template must not contain watch-order marker {marker}")
        )

    for marker in WATCH_ORDER_TEMPLATE_REQUIRED:
        checks.append(
            ok(f"watch-order template contains {marker}")
            if marker in watch_order_text
            else fail(f"watch-order template missing {marker}")
        )
    for marker in WATCH_ORDER_TEMPLATE_FORBIDDEN:
        checks.append(
            ok(f"watch-order template excludes single-video marker {marker}")
            if marker not in watch_order_text
            else fail(f"watch-order template must not contain single-video marker {marker}")
        )
    return checks


def check_golden() -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    golden_root = BASE_DIR / "golden"
    if not golden_root.exists():
        checks.append(fail("missing golden folder"))
        return checks
    for name in GOLDEN_REQUIRED:
        path = golden_root / name
        checks.append(ok(f"golden/{name}") if path.exists() else fail(f"missing golden/{name}"))
        if path.exists() and path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                checks.append(fail(f"golden/{name} not valid json"))
            else:
                checks.append(ok(f"golden/{name} json ok"))
    return checks


def check_scripts() -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    for rel in REQUIRED_SCRIPTS:
        path = BASE_DIR / rel
        checks.append(ok(rel) if path.exists() else fail(f"missing {rel}"))
    return checks


def check_tests() -> list[tuple[str, bool]]:
    tests_root = BASE_DIR / "tests"
    if not tests_root.exists():
        return [fail("tests folder missing")]
    checks = [ok("tests folder exists")]
    for rel in REQUIRED_TEST_FILES:
        path = tests_root / rel
        checks.append(ok(rel) if path.exists() else fail(f"missing test file {rel}"))
    test_count = len(list(tests_root.glob("test_*.py")))
    checks.append(ok(f"tests count={test_count}") if test_count >= 10 else fail("insufficient tests"))
    return checks


def check_discoverability(strict_install: bool) -> list[tuple[str, bool]]:
    checks = []
    watch_dir = Path.home() / ".codex" / "skills" / "watchbrief_v5"
    if watch_dir.exists():
        checks.append(ok("codex installed watchbrief_v5"))
    elif strict_install:
        checks.append(fail("watchbrief_v5 not installed under ~/.codex/skills"))
    else:
        checks.append((f"WARN: not installed in ~/.codex/skills/watchbrief_v5 (expected at {watch_dir})", True))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate WatchBrief V5 folder integrity and check discoverability.")
    parser.add_argument("--json", action="store_true", help="output machine-readable JSON")
    parser.add_argument("--strict-install", action="store_true", help="require ~/.codex/skills/watchbrief_v5 installed")
    args = parser.parse_args()

    checks: list[tuple[str, bool]] = []
    checks.append(check_skill_frontmatter(BASE_DIR / "SKILL.md"))
    checks.append(check_agent_config(BASE_DIR / "agents" / "openai.yaml"))
    checks.extend(check_required_paths())
    checks.extend(check_template_sources())
    checks.extend(check_scripts())
    checks.extend(check_golden())
    checks.extend(check_tests())
    checks.extend(check_discoverability(args.strict_install))

    failed = [(label, ok_state) for label, ok_state in checks if not ok_state]
    passed = len(checks) - len(failed)

    if args.json:
        payload = {
            "ok": len(failed) == 0,
            "passed": passed,
            "failed": len(failed),
            "checks": [{"name": label, "pass": bool(state)} for label, state in checks],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for label, state in checks:
            if state is True:
                print(f"[OK] {label}")
            elif label.startswith("WARN:"):
                print(f"[WARN] {label}")
            else:
                print(f"[FAIL] {label}")
        if failed:
            print(f"Summary: {passed} passed, {len(failed)} failed")
        else:
            print("Summary: PASS")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
