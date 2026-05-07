#!/usr/bin/env python3
"""Local WebUI for WatchBrief V5.

The server is standard-library only. It builds safe CLI commands and keeps the
pipeline contract in scripts/cli.py as the single execution path.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.error
import urllib.request

try:
    from .report_targets import DEFAULT_REPORT_TARGET, REPORT_TARGETS, REPORT_TARGET_DESCRIPTIONS, REPORT_TARGET_LABELS
    from .transcriber import has_whisper_cli, mlx_audio_python_statuses
except ImportError:  # pragma: no cover
    from report_targets import DEFAULT_REPORT_TARGET, REPORT_TARGETS, REPORT_TARGET_DESCRIPTIONS, REPORT_TARGET_LABELS
    from transcriber import has_whisper_cli, mlx_audio_python_statuses

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = PROJECT_ROOT / "scripts" / "cli.py"
STATE_DIR = Path.home() / ".watchbrief" / "webui"
TASKS_PATH = STATE_DIR / "tasks.json"
SMOKE_MATRIX_PATH = STATE_DIR / "smoke_matrix.json"
LOG_DIR = STATE_DIR / "logs"
SOURCE_UPLOAD_DIR = STATE_DIR / "source_files"

DEFAULT_QWEN_MODEL = "qwen3-30b-a3b-instruct-2507-mlx"
DEFAULT_QWEN_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_ACCOUNT = "account2"
DEFAULT_CODEX_HOME_ROOT = "~/.watchbrief_codex"

SUPPORTED_REVIEW_PROVIDERS = {"local", "codex-cli", "mock", "gemini", "claude", "kimi", "openai-compatible"}
UNSUPPORTED_REVIEW_PROVIDERS = {"manual"}
SUPPORTED_EXTRACT_PROVIDERS = {"local-qwen", "local-openai-compatible", "openai-compatible", "gemini", "claude", "kimi", "codex-cli-extract"}
UNSUPPORTED_EXTRACT_PROVIDERS: set[str] = set()
SUPPORTED_REPORT_FORMATS = {"html", "pdf"}
SUPPORTED_RENDERERS = {"local-html", "pdf-export"}
SUPPORTED_BROWSER_AUTH = {"auto", "chrome", "safari", "edge", "none"}
SUPPORTED_TRANSCRIBERS = {"recommended", "auto", "mlx_audio", "whisper"}
SUPPORTED_OUTPUT_MODES = {"default", "custom", "diagnostic"}
SUPPORTED_ANALYSIS_MODES = {"fast", "standard", "deep"}
SUPPORTED_REPORT_TARGETS = set(REPORT_TARGETS)
SUPPORTED_SCORING_PROFILES = {"standard", "information-first", "evidence-first", "originality-first", "watch-value-first", "custom"}
ANALYSIS_MODE_DEFAULTS = {
    "fast": {
        "timeout": "600",
        "force_reanalysis": False,
        "keep_debug_artifacts": False,
        "description": "快速筛选：优先速度，命中缓存就复用，适合批量先判断值不值得看。",
    },
    "standard": {
        "timeout": "900",
        "force_reanalysis": False,
        "keep_debug_artifacts": False,
        "description": "标准报告：默认正式流程，结构化理解后生成观看决策。",
    },
    "deep": {
        "timeout": "1200",
        "force_reanalysis": True,
        "keep_debug_artifacts": True,
        "description": "深度分析：重新分析、不走旧报告缓存，并保留调试产物，适合重要视频复核。",
    },
}
EXTERNAL_REVIEW_PROVIDERS = {"gemini", "claude", "kimi", "openai-compatible"}
DEFAULT_REVIEW_MODELS = {
    "gemini": "gemini-2.5-flash",
    "claude": "claude-sonnet-4-20250514",
    "kimi": "kimi-k2.5",
    "openai-compatible": "",
}
DEFAULT_EXTRACT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "claude": "claude-sonnet-4-20250514",
    "kimi": "kimi-k2.5",
    "codex-cli-extract": DEFAULT_CODEX_MODEL,
    "local-openai-compatible": "",
    "openai-compatible": "",
}
DEFAULT_API_KEY_ENVS = {
    "gemini": "GEMINI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "openai-compatible": "WATCHBRIEF_OPENAI_COMPATIBLE_API_KEY",
}
DEFAULT_API_BASES = {
    "local-openai-compatible": DEFAULT_QWEN_BASE_URL,
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "claude": "https://api.anthropic.com/v1",
    "kimi": "https://api.moonshot.ai/v1",
}
CAPABILITY_CACHE_SECONDS = 8
PDF_BROWSER_ENV = "WATCHBRIEF_PDF_BROWSER"
DIRECTORY_PICKER_PROMPT = "选择 WatchBrief 输出目录"
URL_FILE_PICKER_PROMPT = "选择包含视频链接的 txt 文件"

_CAPABILITY_CACHE: tuple[float, dict[str, Any]] | None = None


def python_executable() -> str:
    return sys.executable or "python3"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: Any, field_name: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"{field_name} 必须是正整数秒")
    return text


def _validate_qwen_model(model: str) -> None:
    if model and "qwen" not in model.lower():
        raise ValueError("WatchBrief local_extract 只接受 Qwen-family 模型，模型名必须包含 qwen")


def _env_name(value: Any, field_name: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ValueError(f"{field_name} 只能填写环境变量名字，例如 GEMINI_API_KEY；不要粘贴 token 值")
    return text


def default_review_provider() -> str:
    """Prefer real review when Codex CLI can run the default model; local is fallback only."""
    status = codex_cli_status()
    return "codex-cli" if status.get("gpt55_ready") is True else "local"


def _review_provider(payload: dict[str, Any]) -> str:
    provider = _clean_text(payload.get("review_provider") or payload.get("account_provider")) or default_review_provider()
    if provider in UNSUPPORTED_REVIEW_PROVIDERS:
        raise ValueError(f"{provider} 不是可运行 review provider")
    if provider not in SUPPORTED_REVIEW_PROVIDERS:
        raise ValueError("review_provider 只支持 local、codex-cli、mock、gemini、claude、kimi、openai-compatible")
    return provider


def _extract_provider(payload: dict[str, Any]) -> str:
    provider = _clean_text(payload.get("extract_provider")) or "local-qwen"
    if provider in UNSUPPORTED_EXTRACT_PROVIDERS:
        raise ValueError(f"{provider} 提炼适配器不可用")
    if provider not in SUPPORTED_EXTRACT_PROVIDERS:
        raise ValueError("提炼 extract_provider 只支持 local-qwen、local-openai-compatible、openai-compatible、gemini、claude、kimi、codex-cli-extract")
    return provider


def _configured_qwen_api_base(api_base: str | None = None) -> str:
    return str(
        os.environ.get("WATCHBRIEF_QWEN_BASE_URL")
        or api_base
        or os.environ.get("WATCHBRIEF_QWEN_API_BASE")
        or DEFAULT_QWEN_BASE_URL
    ).rstrip("/")


def _json_get(url: str, *, timeout: float = 0.6) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def local_model_status(api_base: str | None = None) -> dict[str, Any]:
    base_url = _configured_qwen_api_base(api_base)
    try:
        data = _json_get(f"{base_url}/models")
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "qwen_ok": False,
            "base_url": base_url,
            "models": [],
            "qwen_models": [],
            "error": str(exc),
        }
    rows = data.get("data")
    models = [
        str(item.get("id"))
        for item in rows
        if isinstance(item, dict) and item.get("id")
    ] if isinstance(rows, list) else []
    qwen_models = [model for model in models if "qwen" in model.lower()]
    non_qwen_models = [model for model in models if "qwen" not in model.lower()]
    return {
        "ok": True,
        "qwen_ok": bool(qwen_models),
        "base_url": base_url,
        "models": models[:50],
        "qwen_models": qwen_models[:50],
        "non_qwen_models": non_qwen_models[:50],
        "error": "",
    }


def api_key_env_status() -> dict[str, Any]:
    return {
        "gemini": {"env": "GEMINI_API_KEY", "configured": bool(os.environ.get("GEMINI_API_KEY"))},
        "claude": {"env": "ANTHROPIC_API_KEY", "configured": bool(os.environ.get("ANTHROPIC_API_KEY"))},
        "kimi": {"env": "MOONSHOT_API_KEY", "configured": bool(os.environ.get("MOONSHOT_API_KEY"))},
        "openai_compatible": {
            "env": "WATCHBRIEF_OPENAI_COMPATIBLE_API_KEY",
            "configured": bool(os.environ.get("WATCHBRIEF_OPENAI_COMPATIBLE_API_KEY")),
        },
    }


def transcriber_status() -> dict[str, Any]:
    try:
        mlx_statuses = mlx_audio_python_statuses(timeout=2)
    except Exception as exc:  # pragma: no cover - defensive for partial installs
        mlx_statuses = [{"python": "", "status": f"check_failed: {exc}", "available": "false"}]
    mlx_available = [row for row in mlx_statuses if str(row.get("available")) == "true"]
    whisper_path = shutil.which("whisper")
    recommended = "auto" if mlx_available else ("whisper" if whisper_path else "auto")
    if mlx_available:
        reason = "检测到 MLX-Audio，使用 CLI 默认 auto"
    elif whisper_path:
        reason = "未检测到 MLX-Audio，WebUI 默认改用 Whisper"
    else:
        reason = "未检测到 MLX-Audio 或 Whisper；需要先安装转写工具"
    return {
        "mlx_audio": {
            "ok": bool(mlx_available),
            "selected_python": str(mlx_available[0].get("python") or "") if mlx_available else "",
            "candidates": mlx_statuses,
        },
        "whisper": {"ok": has_whisper_cli(), "path": whisper_path or ""},
        "recommended": recommended,
        "recommendation_reason": reason,
    }


def pdf_browser_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = _clean_text(os.environ.get(PDF_BROWSER_ENV))
    if env_path:
        candidates.append(Path(env_path).expanduser())
    for path in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ):
        candidates.append(Path(path))
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def resolve_pdf_browser() -> Path | None:
    for candidate in pdf_browser_candidates():
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def pdf_export_status() -> dict[str, Any]:
    browser = resolve_pdf_browser()
    return {
        "ok": browser is not None,
        "browser_path": str(browser or ""),
        "engine": "Chromium headless print" if browser else "",
        "candidates": [str(path) for path in pdf_browser_candidates()],
    }


def directory_picker_status() -> dict[str, Any]:
    available = sys.platform == "darwin" and bool(shutil.which("osascript"))
    return {
        "available": available,
        "method": "macos_osascript" if available else "",
    }


def choose_output_directory(
    *,
    runner: Any = subprocess.run,
    osascript_path: str | None = None,
    platform: str | None = None,
) -> str:
    current_platform = platform or sys.platform
    if current_platform != "darwin":
        raise RuntimeError("当前系统不支持本地文件夹选择按钮，请手动输入输出目录绝对路径")
    executable = osascript_path or shutil.which("osascript")
    if not executable:
        raise RuntimeError("未找到 osascript，无法打开系统文件夹选择框，请手动输入输出目录绝对路径")
    script = f'POSIX path of (choose folder with prompt "{DIRECTORY_PICKER_PROMPT}")'
    result = runner([executable, "-e", script], capture_output=True, text=True, timeout=120)
    stdout = str(getattr(result, "stdout", "") or "").strip()
    stderr = str(getattr(result, "stderr", "") or "").strip()
    if getattr(result, "returncode", 1) != 0:
        detail = " ".join((stderr or stdout or "user canceled").split())
        if "-128" in detail or "User canceled" in detail or "用户已取消" in detail:
            return ""
        raise RuntimeError(f"选择输出目录失败：{detail}")
    return str(Path(stdout).expanduser()) if stdout else ""


def choose_url_file(
    *,
    runner: Any = subprocess.run,
    osascript_path: str | None = None,
    platform: str | None = None,
) -> str:
    current_platform = platform or sys.platform
    if current_platform != "darwin":
        raise RuntimeError("当前系统不支持本地文件选择按钮，请手动输入 URL 文件绝对路径")
    executable = osascript_path or shutil.which("osascript")
    if not executable:
        raise RuntimeError("未找到 osascript，无法打开系统文件选择框，请手动输入 URL 文件绝对路径")
    script = f'POSIX path of (choose file with prompt "{URL_FILE_PICKER_PROMPT}" of type {{"txt", "text", "public.plain-text"}})'
    result = runner([executable, "-e", script], capture_output=True, text=True, timeout=120)
    stdout = str(getattr(result, "stdout", "") or "").strip()
    stderr = str(getattr(result, "stderr", "") or "").strip()
    if getattr(result, "returncode", 1) != 0:
        detail = " ".join((stderr or stdout or "user canceled").split())
        if "-128" in detail or "User canceled" in detail or "用户已取消" in detail:
            return ""
        raise RuntimeError(f"选择 URL 文件失败：{detail}")
    return str(Path(stdout).expanduser()) if stdout else ""


def should_export_pdf(payload: dict[str, Any]) -> bool:
    return (
        _clean_text(payload.get("report_format")) == "pdf"
        or _clean_text(payload.get("renderer")) == "pdf-export"
        or _truthy(payload.get("export_pdf"))
    )


def html_paths_from_log(log_text: str) -> list[Path]:
    paths: list[Path] = []
    patterns = [
        r"^(?:Diagnostic HTML|Diagnostic Watch Order|HTML|Watch Order):\s+(.+?\.html)\s*$",
        r"^\[[^\]]+\]\s+.+?\s+->\s+(.+?\.html)\s*$",
    ]
    for line in str(log_text or "").splitlines():
        for pattern in patterns:
            match = re.match(pattern, line.strip())
            if match:
                paths.append(Path(match.group(1)).expanduser())
                break
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_file():
            unique.append(path)
    return unique


def pdf_path_for_html(html_path: Path) -> Path:
    return html_path.with_suffix(".pdf")


def build_pdf_command(html_path: Path, pdf_path: Path, *, browser: Path | None = None) -> list[str]:
    selected_browser = browser or resolve_pdf_browser()
    if not selected_browser:
        raise ValueError("PDF 导出需要安装 Chrome / Edge / Chromium / Brave，或设置 WATCHBRIEF_PDF_BROWSER")
    return [
        str(selected_browser),
        "--headless",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        f"--print-to-pdf={pdf_path}",
        html_path.resolve().as_uri(),
    ]


def export_html_to_pdf(
    html_path: Path,
    *,
    browser: Path | None = None,
    runner: Any = subprocess.run,
    timeout: int = 90,
) -> Path:
    pdf_path = pdf_path_for_html(html_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_pdf_command(html_path, pdf_path, browser=browser)
    result = runner(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        detail = " ".join(str(result.stderr or result.stdout or "").split())[:400]
        raise RuntimeError(f"PDF 导出失败：{detail or 'headless browser exited with non-zero status'}")
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError("PDF 导出失败：浏览器未生成 PDF 文件")
    return pdf_path


def export_log_htmls_to_pdf(log_path: Path) -> list[Path]:
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    html_paths = html_paths_from_log(log_text)
    if not html_paths:
        raise RuntimeError("PDF 导出失败：未在任务日志中找到 HTML 输出")
    return [export_html_to_pdf(path) for path in html_paths]


def open_local_paths(paths: list[Path]) -> None:
    if sys.platform == "darwin":
        opener = "open"
    elif os.name == "nt":
        opener = "start"
    else:
        opener = "xdg-open"
    for path in paths:
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", str(path)])
        else:
            subprocess.Popen([opener, str(path)])


def codex_cli_status(codex_path: str | None = None) -> dict[str, Any]:
    """Return non-secret Codex CLI availability/version diagnostics."""
    path = codex_path or shutil.which("codex")
    status: dict[str, Any] = {
        "ok": bool(path),
        "path": path or "",
        "version": "",
        "version_tuple": [],
        "gpt55_ready": None,
        "warning": "",
    }
    if not path:
        return status
    try:
        completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10, check=False)
    except Exception as exc:
        status["warning"] = f"Codex CLI 版本检查失败：{exc}"
        return status
    output = (completed.stdout or completed.stderr or "").strip()
    status["version"] = output
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", output)
    if match:
        version_tuple = [int(match.group(1)), int(match.group(2)), int(match.group(3))]
        status["version_tuple"] = version_tuple
        # 0.118.0 has been observed to reject gpt-5.5 with "requires a newer version of Codex".
        status["gpt55_ready"] = tuple(version_tuple) > (0, 118, 0)
        if not status["gpt55_ready"]:
            status["warning"] = "当前 Codex CLI 版本已知无法稳定调用 gpt-5.5；请升级 Codex CLI 后再用 Codex 判断。"
    return status


def local_capabilities(*, force: bool = False) -> dict[str, Any]:
    global _CAPABILITY_CACHE
    now = time.time()
    if not force and _CAPABILITY_CACHE and now - _CAPABILITY_CACHE[0] < CAPABILITY_CACHE_SECONDS:
        return _CAPABILITY_CACHE[1]
    codex_path = shutil.which("codex")
    codex_status = codex_cli_status(codex_path)
    pdf_status = pdf_export_status()
    capabilities = {
        "local_model": local_model_status(),
        "transcriber": transcriber_status(),
        "review": {
            "codex_cli_ok": bool(codex_path),
            "codex_cli_path": codex_path or "",
            "codex_cli_version": codex_status.get("version", ""),
            "codex_cli_gpt55_ready": codex_status.get("gpt55_ready"),
            "codex_cli_warning": codex_status.get("warning", ""),
            "supported": ["local", "codex-cli", "gemini", "claude", "kimi", "openai-compatible", "mock"],
            "placeholders": ["manual-review-ui"],
            "api_key_envs": api_key_env_status(),
        },
        "extract": {
            "supported": ["local-qwen", "local-openai-compatible", "openai-compatible", "gemini", "claude", "kimi", "codex-cli-extract"],
            "api_key_envs": api_key_env_status(),
        },
        "report": {
            "formats": [{"id": "html", "available": True}, {"id": "pdf", "available": pdf_status["ok"]}],
            "renderers": [{"id": "local-html", "available": True}, {"id": "pdf-export", "available": pdf_status["ok"]}],
            "pdf": pdf_status,
        },
        "paths": {
            "desktop": str(Path.home() / "Desktop"),
            "downloads": str(Path.home() / "Downloads"),
            "documents": str(Path.home() / "Documents"),
            "webui_state": str(STATE_DIR),
            "project_root": str(PROJECT_ROOT),
        },
        "directory_picker": directory_picker_status(),
    }
    _CAPABILITY_CACHE = (now, capabilities)
    return capabilities


def _recommended_transcriber() -> str:
    return str(local_capabilities().get("transcriber", {}).get("recommended") or "auto")


def parse_report_targets(payload: dict[str, Any]) -> list[str]:
    raw_targets = payload.get("report_targets")
    values: list[str] = []
    if isinstance(raw_targets, list):
        values.extend(_clean_text(item) for item in raw_targets)
    elif isinstance(raw_targets, str):
        values.extend(part.strip() for part in raw_targets.split(","))
    legacy_target = payload.get("report_target")
    if isinstance(legacy_target, list):
        values.extend(_clean_text(item) for item in legacy_target)
    else:
        legacy_text = _clean_text(legacy_target)
        if legacy_text:
            values.extend(part.strip() for part in legacy_text.split(","))
    targets: list[str] = []
    for value in values:
        if not value:
            continue
        if value not in SUPPORTED_REPORT_TARGETS:
            raise ValueError("报告目标只支持 watch_decision/text_structure/knowledge_notes/viewpoint_breakdown/creation_review")
        if value not in targets:
            targets.append(value)
    return targets or [DEFAULT_REPORT_TARGET]


def build_cli_command(payload: dict[str, Any], *, report_target_override: str | None = None) -> list[str]:
    """Build a WatchBrief CLI command from WebUI form payload."""
    source_url = _clean_text(payload.get("source_url"))
    source_file = _clean_text(payload.get("source_file"))
    if bool(source_url) == bool(source_file):
        raise ValueError("必须填写一个视频/列表链接，或填写一个本地 URL 文件路径")

    analysis_mode = _clean_text(payload.get("analysis_mode")) or "standard"
    if analysis_mode not in SUPPORTED_ANALYSIS_MODES:
        raise ValueError("分析模式只支持 fast/standard/deep")
    selected_targets = parse_report_targets(payload)
    report_target = report_target_override or selected_targets[0]
    if report_target not in SUPPORTED_REPORT_TARGETS:
        raise ValueError("报告目标只支持 watch_decision/text_structure/knowledge_notes/viewpoint_breakdown/creation_review")

    report_format = _clean_text(payload.get("report_format")) or "html"
    if report_format not in SUPPORTED_REPORT_FORMATS:
        raise ValueError("当前 CLI 只生成 HTML；PDF 需要后续接入 HTML-to-PDF 后端")
    renderer = _clean_text(payload.get("renderer")) or "local-html"
    if renderer not in SUPPORTED_RENDERERS:
        raise ValueError("当前只支持本地 HTML renderer；其他 renderer 尚未接入")

    command = [python_executable(), str(CLI_PATH), "--analysis-mode", analysis_mode, "--report-target", report_target]
    scoring_profile = _clean_text(payload.get("scoring_profile")) or "standard"
    if scoring_profile not in SUPPORTED_SCORING_PROFILES:
        raise ValueError("评分规则只支持 standard/information-first/evidence-first/originality-first/watch-value-first/custom")
    scoring_weights = _clean_text(payload.get("scoring_weights"))
    if scoring_profile == "custom" and not scoring_weights:
        raise ValueError("选择自定义评分时，必须在高级选项填写四项评分权重")
    if source_file:
        command.extend(["--source-file", source_file])
    else:
        command.extend(["--source-url", source_url])

    output_mode = _clean_text(payload.get("output_mode")) or "default"
    if output_mode not in SUPPORTED_OUTPUT_MODES:
        raise ValueError("输出方式只支持 default/custom/diagnostic")
    output_dir = _clean_text(payload.get("output_dir"))
    if output_mode == "custom" and not output_dir:
        raise ValueError("选择自定义输出目录时必须填写 output_dir")
    if output_dir:
        command.extend(["--output-dir", output_dir])

    provider = _review_provider(payload)
    extract_provider = _extract_provider(payload)
    if scoring_profile != "standard" or scoring_weights:
        command.extend(["--scoring-profile", scoring_profile])
    if scoring_weights:
        command.extend(["--scoring-weights", scoring_weights])
    command.extend(["--review-provider", provider])
    if provider == "codex-cli":
        command.append("--enable-codex-review")
        codex_model = _clean_text(payload.get("codex_model")) or DEFAULT_CODEX_MODEL
        command.extend(["--codex-model", codex_model])
    elif provider == "mock":
        mock_response = _clean_text(payload.get("mock_review_response"))
        if not mock_response:
            raise ValueError("mock review 需要填写 mock_review_response JSON 文件路径")
        command.extend(["--mock-review-response", mock_response])
    elif provider in EXTERNAL_REVIEW_PROVIDERS:
        review_model = _clean_text(payload.get("review_model")) or DEFAULT_REVIEW_MODELS.get(provider, "")
        if provider == "openai-compatible" and not review_model:
            raise ValueError("OpenAI-compatible review 必须填写 review_model，例如 gemma-3、gpt-4.1 或你的服务模型名")
        if review_model:
            command.extend(["--review-model", review_model])
        review_api_base = _clean_text(payload.get("review_api_base")) or DEFAULT_API_BASES.get(provider, "")
        if provider == "openai-compatible" and not review_api_base:
            review_api_base = _clean_text(payload.get("qwen_api_base")) or DEFAULT_QWEN_BASE_URL
        if review_api_base:
            command.extend(["--review-api-base", review_api_base])
        review_api_key_env = _env_name(
            payload.get("review_api_key_env") or DEFAULT_API_KEY_ENVS.get(provider, ""),
            "review_api_key_env",
        )
        if review_api_key_env:
            command.extend(["--review-api-key-env", review_api_key_env])

    if provider == "codex-cli" or extract_provider == "codex-cli-extract":
        codex_home_root = _clean_text(payload.get("codex_home_root")) or DEFAULT_CODEX_HOME_ROOT
        if codex_home_root:
            command.extend(["--codex-home-root", codex_home_root])
        codex_account = _clean_text(payload.get("codex_account")) or DEFAULT_CODEX_ACCOUNT
        if codex_account:
            command.extend(["--codex-account", codex_account])

    qwen_api_base = _clean_text(payload.get("qwen_api_base"))
    if extract_provider == "local-qwen":
        qwen_model = _clean_text(payload.get("qwen_model")) or DEFAULT_QWEN_MODEL
        _validate_qwen_model(qwen_model)
        command.extend(["--qwen-model", qwen_model])
        if qwen_api_base:
            command.extend(["--qwen-api-base", qwen_api_base])
    else:
        command.extend(["--extract-provider", extract_provider])
        extract_model = _clean_text(payload.get("extract_model")) or DEFAULT_EXTRACT_MODELS.get(extract_provider, "")
        if extract_provider in {"local-openai-compatible", "openai-compatible"} and not extract_model:
            raise ValueError("OpenAI-compatible 提炼必须填写 extract_model，例如 gemma-3、llama、gpt-4.1 或你的服务模型名")
        if extract_model:
            command.extend(["--extract-model", extract_model])
        extract_api_base = _clean_text(payload.get("extract_api_base")) or DEFAULT_API_BASES.get(extract_provider, "")
        if extract_provider == "local-openai-compatible" and not extract_api_base:
            extract_api_base = qwen_api_base or DEFAULT_QWEN_BASE_URL
        if extract_provider == "openai-compatible" and not extract_api_base:
            raise ValueError("OpenAI-compatible 提炼必须填写 extract_api_base")
        if extract_api_base:
            command.extend(["--extract-api-base", extract_api_base])
        extract_api_key_env = _env_name(
            payload.get("extract_api_key_env") or DEFAULT_API_KEY_ENVS.get(extract_provider, ""),
            "extract_api_key_env",
        )
        if extract_api_key_env:
            command.extend(["--extract-api-key-env", extract_api_key_env])

    mode_defaults = ANALYSIS_MODE_DEFAULTS[analysis_mode]
    timeout = _positive_int(payload.get("timeout") or mode_defaults["timeout"], "timeout")
    if timeout:
        command.extend(["--timeout", timeout])
    qwen_timeout = _positive_int(payload.get("qwen_timeout"), "qwen_timeout")
    if qwen_timeout:
        command.extend(["--qwen-timeout", qwen_timeout])

    browser_auth = _clean_text(payload.get("browser_auth")) or "auto"
    if browser_auth not in SUPPORTED_BROWSER_AUTH:
        raise ValueError("登录态浏览器只支持 auto/chrome/safari/edge/none")
    if browser_auth == "none":
        command.append("--no-browser-auth")
    elif browser_auth != "auto":
        command.extend(["--cookies-from-browser", browser_auth])

    cookies_file = _clean_text(payload.get("cookies_file"))
    if cookies_file:
        command.extend(["--cookies-file", cookies_file])

    transcriber = _clean_text(payload.get("transcriber")) or "recommended"
    if transcriber not in SUPPORTED_TRANSCRIBERS:
        raise ValueError("转写器只支持 recommended/auto/mlx_audio/whisper")
    if transcriber == "recommended":
        transcriber = _recommended_transcriber()
    if transcriber != "auto":
        command.extend(["--transcriber", transcriber])
    mlx_model = _clean_text(payload.get("mlx_model"))
    if mlx_model:
        command.extend(["--mlx-model", mlx_model])
    whisper_model = _clean_text(payload.get("whisper_model"))
    if whisper_model:
        command.extend(["--whisper-model", whisper_model])
    if _truthy(payload.get("allow_whisper_fallback")):
        command.append("--allow-whisper-fallback")

    if _truthy(payload.get("force_reanalysis")) or (payload.get("force_reanalysis") is None and bool(mode_defaults["force_reanalysis"])):
        command.append("--force-reanalysis")
    if _truthy(payload.get("keep_debug_artifacts")) or (payload.get("keep_debug_artifacts") is None and bool(mode_defaults["keep_debug_artifacts"])):
        command.append("--keep-debug-artifacts")
    if _truthy(payload.get("diagnostic_run")) or output_mode == "diagnostic":
        command.append("--diagnostic-run")
    if _truthy(payload.get("open_output")) and not should_export_pdf(payload):
        command.append("--open-output")

    return command


def command_preview(payload: dict[str, Any]) -> dict[str, Any]:
    targets = parse_report_targets(payload)
    commands = [build_cli_command(payload, report_target_override=target) for target in targets]
    return {
        "command": commands[0],
        "commands": commands,
        "report_targets": targets,
        "cwd": str(PROJECT_ROOT),
        "pdf_export": should_export_pdf(payload),
        "pdf_engine": pdf_export_status().get("browser_path") or "",
    }


def _read_tasks() -> list[dict[str, Any]]:
    if not TASKS_PATH.exists():
        return []
    try:
        data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _task_log_manifest_path(log_path: Path) -> Path | None:
    if not log_path.exists():
        return None
    for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("manifest_path:"):
            value = line.split(":", 1)[1].strip()
            if value:
                return Path(value).expanduser()
            break
    return None


def task_progress_snapshot(task: dict[str, Any]) -> dict[str, Any]:
    status = str(task.get("status") or "")
    log_path_text = str(task.get("log_path") or "").strip()
    if not log_path_text:
        return {}
    log_path = Path(log_path_text).expanduser()
    manifest_path = _task_log_manifest_path(log_path)
    if not manifest_path or not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    total = int(manifest.get("total_count") or len(manifest.get("items") or []) or 0)
    completed = int(manifest.get("completed_count") or 0)
    failed = int(manifest.get("failed_count") or 0)
    skipped = int(manifest.get("skipped_count") or 0)
    finished = min(total, completed + failed + skipped) if total > 0 else 0
    percent = 100 if status == "completed" else (round((finished / total) * 100) if total > 0 else 0)
    active_title = ""
    active_label = ""
    items = manifest.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            item_status = str(item.get("status") or "")
            if item_status not in {"completed", "failed", "skipped"}:
                active_title = str(item.get("title") or "").strip()
                item_manifest_path = item.get("item_manifest_path")
                if item_manifest_path:
                    try:
                        item_manifest = json.loads(Path(str(item_manifest_path)).expanduser().read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        item_manifest = {}
                    steps = item_manifest.get("steps") if isinstance(item_manifest, dict) else []
                    if isinstance(steps, list):
                        for step in reversed(steps):
                            if isinstance(step, dict) and str(step.get("status") or "") not in {"completed", "skipped", "miss"}:
                                active_label = str(step.get("step") or "").strip()
                                break
                break
    if not active_label and status == "failed":
        active_label = "失败"
    elif not active_label and status == "completed":
        active_label = "报告完成"
    elif not active_label and status in {"running", "queued"}:
        active_label = "处理中"
    active_map = {
        "resolver": "解析链接",
        "metadata": "读取元数据",
        "subtitle_fetcher": "获取字幕",
        "audio_downloader": "下载音频",
        "transcriber": "转写音频",
        "transcript_quality": "检查转写",
        "local_extract": "内容理解",
        "codex_review_request": "观看判断",
        "report_cache": "检查缓存",
        "renderer": "渲染报告",
        "failed": "失败",
    }
    return {
        "manifest_path": str(manifest_path),
        "total": total,
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "finished": finished,
        "percent": max(0, min(100, int(percent))),
        "active_title": active_title,
        "active_label": active_map.get(active_label, active_label),
    }


def tasks_for_api(limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in _reconcile_stale_active_tasks()[:limit]:
        if not isinstance(task, dict):
            continue
        enriched = dict(task)
        progress = task_progress_snapshot(enriched)
        if progress:
            enriched["progress"] = progress
        rows.append(enriched)
    return rows


def _write_tasks(tasks: list[dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_PATH.write_text(json.dumps(tasks[:100], ensure_ascii=False, indent=2), encoding="utf-8")


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reconcile_stale_active_tasks(tasks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Mark running WebUI tasks as failed when their recorded process is gone.

    This keeps task history and the smoke matrix from showing days-old
    phantom runs as still active. Queued tasks without a pid are preserved:
    they may be waiting for a runner and should not be guessed as dead.
    """
    rows = tasks if tasks is not None else _read_tasks()
    changed = False
    now = time.time()
    for task in rows:
        if not isinstance(task, dict) or task.get("status") != "running":
            continue
        pid = int(task.get("pid") or 0)
        if pid and not _pid_is_running(pid):
            task.update({
                "status": "failed",
                "exit_code": None,
                "error": "WebUI 记录的任务进程已不存在，已自动标记为失败；请查看日志或重新运行。",
                "stale_pid": pid,
                "stale_detected_at": now,
                "updated_at": now,
            })
            changed = True
    if changed:
        _write_tasks(rows)
    return rows


def clear_tasks() -> dict[str, Any]:
    tasks = _reconcile_stale_active_tasks()
    kept = [task for task in tasks if task.get("status") in {"running", "queued"}]
    removed = len(tasks) - len(kept)
    _write_tasks(kept)
    return {"tasks": kept, "removed": removed, "kept_running": len(kept)}


SMOKE_MATRIX_SCENARIOS = [
    ("youtube-single-subtitles", "YouTube 单视频 / 有字幕", "验证字幕优先、无需音频下载、能完成 HTML。"),
    ("youtube-single-no-subtitles", "YouTube 单视频 / 无字幕", "验证 audio_downloader + MLX/Whisper 转写 + coverage gate。"),
    ("bilibili-single", "B 站单视频", "验证 Chrome→Safari 登录态、B 站字幕/音频 fallback。"),
    ("bilibili-list", "B 站列表", "验证列表展开、原始顺序、00-watch-order.html。"),
    ("xiaohongshu-single-video", "小红书单视频 note", "验证 /explore 标准化、敏感参数不进正式 HTML。"),
    ("xiaohongshu-board", "小红书专辑 / board", "验证视频 note、图文 skipped、原始顺序和失败隔离。"),
    ("long-video", "长视频 40 分钟以上", "验证 timeout、Qwen/Codex 稳定性和报告替代性。"),
    ("failure-login", "登录态失败场景", "验证人话错误、自救按钮和不泄露 cookie。"),
]


def classify_smoke_scenario(source: str, task: dict[str, Any] | None = None) -> str:
    """Map a WebUI task source to a conservative smoke-matrix scenario."""
    task = task or {}
    value = (source or task.get("source") or task.get("title") or "").lower()
    paths = task.get("watch_order_paths") or []
    is_bilibili_favlist = "favlist" in value or "fid=" in value or "ftype=create" in value
    is_list = bool(paths) or "--source-file" in value or value.endswith(".txt") or "/list/" in value or "playlist" in value or is_bilibili_favlist
    if "xiaohongshu.com" in value or "xhslink.com" in value:
        if "/board/" in value or "board" in value or is_list:
            return "xiaohongshu-board"
        return "xiaohongshu-single-video"
    if "bilibili.com" in value or "b23.tv" in value:
        if "/list/" in value or "playlist" in value or is_list:
            return "bilibili-list"
        return "bilibili-single"
    if "youtube.com" in value or "youtu.be" in value:
        if task.get("status") == "failed" and "login" in str(task.get("error", "")).lower():
            return "failure-login"
        return "youtube-single-subtitles"
    if task.get("status") == "failed" and any(token in str(task.get("error", "")).lower() for token in ("login", "cookie", "登录态")):
        return "failure-login"
    return "long-video" if str(task.get("analysis_mode", "")).lower() == "deep" else "youtube-single-subtitles"


def matrix_row_from_task(task: dict[str, Any]) -> dict[str, Any]:
    scenario_id = classify_smoke_scenario(str(task.get("source") or task.get("title") or ""), task)
    html_paths = task.get("html_paths") or []
    watch_paths = task.get("watch_order_paths") or []
    debug_paths = task.get("debug_paths") or []
    log_path = task.get("log_path") or ""
    output_path = (watch_paths or html_paths or debug_paths or [""])[0]
    status = task.get("status") or "not_run"
    matrix_status = "passed" if status == "completed" and (html_paths or watch_paths) else ("failed" if status == "failed" else status if status in {"running", "queued"} else "not_run")
    if matrix_status == "passed":
        last_result = "最近 WebUI 任务已生成正式报告。"
    elif matrix_status == "failed":
        last_result = str(task.get("error") or "最近 WebUI 任务失败，请打开日志查看。")
    elif matrix_status in {"running", "queued"}:
        last_result = "最近 WebUI 任务仍在运行。"
    else:
        last_result = "尚未验收"
    return {
        "id": scenario_id,
        "status": matrix_status,
        "last_result": last_result,
        "output_path": output_path,
        "log_path": log_path,
        "task_id": task.get("id") or "",
        "source": task.get("source") or task.get("title") or "",
        "updated_at": task.get("updated_at") or task.get("created_at") or "",
    }


def smoke_matrix_status() -> dict[str, Any]:
    """Return a conservative real-video acceptance matrix scaffold enriched by WebUI task history."""
    saved: dict[str, Any] = {}
    if SMOKE_MATRIX_PATH.exists():
        try:
            loaded = json.loads(SMOKE_MATRIX_PATH.read_text(encoding="utf-8"))
            saved = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            saved = {}
    inferred: dict[str, dict[str, Any]] = {}
    for task in sorted(_reconcile_stale_active_tasks(), key=lambda item: item.get("updated_at") or item.get("created_at") or 0, reverse=True):
        if not isinstance(task, dict):
            continue
        row = matrix_row_from_task(task)
        scenario_id = row["id"]
        if scenario_id not in inferred:
            inferred[scenario_id] = row
    items = []
    for scenario_id, title, goal in SMOKE_MATRIX_SCENARIOS:
        row = saved.get(scenario_id, {}) if isinstance(saved.get(scenario_id), dict) else {}
        auto = inferred.get(scenario_id, {})
        merged = {**auto, **{k: v for k, v in row.items() if v}}
        items.append({
            "id": scenario_id,
            "title": title,
            "goal": goal,
            "status": merged.get("status") or "not_run",
            "last_result": merged.get("last_result") or "尚未验收",
            "output_path": merged.get("output_path") or "",
            "log_path": merged.get("log_path") or "",
            "source": merged.get("source") or "",
            "updated_at": merged.get("updated_at") or "",
            "auto_filled": bool(auto and not row),
        })
    passed = sum(1 for item in items if item["status"] == "passed")
    failed = sum(1 for item in items if item["status"] == "failed")
    running = sum(1 for item in items if item["status"] in {"running", "queued"})
    return {
        "summary": f"真实视频验收矩阵：{passed}/{len(items)} 已通过，{failed} 个失败，{running} 个运行中。",
        "items": items,
        "generated_at": int(time.time()),
        "state_path": str(SMOKE_MATRIX_PATH),
        "auto_source": str(TASKS_PATH),
    }


def release_readiness_status() -> dict[str, Any]:
    """Return a local, no-side-effect release checklist for the WebUI productization work."""
    rollback_path = PROJECT_ROOT / "rollback" / "rollback_patch_20260504_184707.diff"
    checks = [
        {"id": "worklog", "name": "工作日志", "ok": (PROJECT_ROOT / "WORKLOG.md").exists(), "detail": str(PROJECT_ROOT / "WORKLOG.md")},
        {"id": "rollback", "name": "回退 patch", "ok": rollback_path.exists() and rollback_path.stat().st_size > 0, "detail": str(rollback_path)},
        {"id": "webui", "name": "WebUI 入口", "ok": (PROJECT_ROOT / "scripts" / "webui.py").exists(), "detail": "python3 scripts/webui.py --host 127.0.0.1 --port 8765"},
        {"id": "tests", "name": "测试命令", "ok": True, "detail": "python3 -m py_compile scripts/webui.py；python3 -m unittest discover -s tests -p test_webui.py -v；python3 -m unittest discover -s tests -p 'test_*.py'"},
    ]
    ready = all(item["ok"] for item in checks)
    return {
        "ready": ready,
        "summary": "发布整理清单：基础回退与记录已就绪。" if ready else "发布整理清单：仍有缺项需要补齐。",
        "checks": checks,
        "rollback_command": "git apply -R rollback/rollback_patch_20260504_184707.diff",
        "generated_at": int(time.time()),
    }


def _update_task(task_id: str, **updates: Any) -> None:
    tasks = _read_tasks()
    for task in tasks:
        if task.get("id") == task_id:
            task.update(updates)
            break
    _write_tasks(tasks)


def _get_task(task_id: str) -> dict[str, Any] | None:
    for task in _read_tasks():
        if task.get("id") == task_id:
            return task
    return None


def stop_task(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    if task.get("status") not in {"running", "queued"}:
        raise ValueError("任务不在运行中，不能停止")
    pid = int(task.get("pid") or 0)
    if not pid:
        raise ValueError("任务还没有记录进程号，请稍后刷新后再停止")
    try:
        if os.name != "nt":
            os.killpg(pid, signal.SIGTERM)
        else:  # pragma: no cover - Windows fallback
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        raise ValueError(f"没有权限停止任务：{exc}") from exc
    _update_task(
        task_id,
        status="cancelled",
        exit_code=-signal.SIGTERM,
        error="用户已从 WebUI 停止任务。",
        stopped_at=time.time(),
        updated_at=time.time(),
    )
    log_path = Path(str(task.get("log_path") or ""))
    if str(log_path):
        try:
            with log_path.open("ab") as log:
                log.write(b"\nwebui_task_stopped_by_user\n")
        except OSError:
            pass
    return _get_task(task_id) or task


def service_status() -> dict[str, Any]:
    capabilities = local_capabilities()
    local_model = capabilities["local_model"]
    review = capabilities["review"]
    return {
        "ok": True,
        "qwen": {"ok": bool(local_model.get("qwen_ok")), "label": f"OpenAI-compatible endpoint {local_model.get('base_url')}"},
        "codex": {"ok": bool(review.get("codex_cli_ok")), "label": "Codex CLI"},
        "local_model": local_model,
        "transcriber": capabilities["transcriber"],
        "review": review,
        "report": capabilities["report"],
        "paths": capabilities["paths"],
        "directory_picker": capabilities.get("directory_picker", directory_picker_status()),
        "project_root": str(PROJECT_ROOT),
        "time": int(time.time()),
    }


def _diagnostic_item(name: str, state: str, detail: str, action: str = "") -> dict[str, str]:
    return {"name": name, "state": state, "detail": detail, "action": action}


def environment_diagnostics(*, force: bool = True) -> dict[str, Any]:
    """Return a plain-language, token-safe environment diagnosis for WebUI users."""
    capabilities = local_capabilities(force=force)
    local_model = capabilities.get("local_model", {})
    transcriber = capabilities.get("transcriber", {})
    review = capabilities.get("review", {})
    report = capabilities.get("report", {})
    picker = capabilities.get("directory_picker", {})
    paths = capabilities.get("paths", {})
    api_keys = review.get("api_key_envs", {})
    cloud_ready = [
        row.get("env", key)
        for key, row in api_keys.items()
        if isinstance(row, dict) and row.get("configured")
    ]

    checks: list[dict[str, str]] = []
    if local_model.get("qwen_ok"):
        checks.append(_diagnostic_item("模型来源", "ok", "本地 Qwen 已就绪，可直接跑默认 Standard。"))
    elif local_model.get("ok"):
        checks.append(_diagnostic_item("模型来源", "warn", "本地 endpoint 在线，但没有 Qwen；可在高级设置选择本地通用模型。", "若坚持默认 local-qwen，请在 LM Studio 加载 Qwen-family 模型。"))
    elif review.get("codex_cli_ok"):
        checks.append(_diagnostic_item("模型来源", "ok", "未检测到本地 Qwen，但 Codex CLI 可用；可一键切无本地模型模式。"))
    elif cloud_ready:
        checks.append(_diagnostic_item("模型来源", "ok", "未检测到本地 Qwen，但已配置云模型环境变量：" + "、".join(cloud_ready)))
    else:
        checks.append(_diagnostic_item("模型来源", "fail", "没有检测到本地 Qwen、Codex CLI 或云模型环境变量。", "先配置其中一种：LM Studio Qwen、Codex CLI 登录态，或 Gemini/Claude/Kimi/OpenAI-compatible API key 环境变量。"))

    if transcriber.get("mlx_audio", {}).get("ok"):
        checks.append(_diagnostic_item("字幕 / 转写", "ok", "MLX-Audio 可用；有字幕时会优先用字幕，无字幕时可转写。"))
    elif transcriber.get("whisper", {}).get("ok"):
        checks.append(_diagnostic_item("字幕 / 转写", "warn", "未检测到 MLX-Audio，但 Whisper 可用；WebUI 会推荐 Whisper。"))
    else:
        checks.append(_diagnostic_item("字幕 / 转写", "warn", "未检测到 MLX-Audio 或 Whisper；有平台字幕的视频仍可能完成。", "若经常处理无字幕视频，请安装 MLX-Audio 或 Whisper。"))

    if review.get("codex_cli_ok"):
        codex_detail = "已检测到 codex 命令：" + str(review.get("codex_cli_path") or "")
        if review.get("codex_cli_version"):
            codex_detail += "；版本：" + str(review.get("codex_cli_version"))
        codex_warning = str(review.get("codex_cli_warning") or "")
        if codex_warning:
            checks.append(_diagnostic_item("Codex CLI", "warn", codex_detail + "。" + codex_warning, "若要使用 gpt-5.5 做 Codex 判断，请先升级 Codex CLI；本地规则/云模型路径不受影响。"))
        else:
            checks.append(_diagnostic_item("Codex CLI", "ok", codex_detail))
    else:
        checks.append(_diagnostic_item("Codex CLI", "warn", "未检测到 codex 命令；不影响本地 Qwen 或云模型路径。", "需要 Codex 路径时先安装并完成正规登录。"))

    if cloud_ready:
        checks.append(_diagnostic_item("云模型密钥", "ok", "已配置：" + "、".join(cloud_ready) + "。WebUI 只读取变量名状态，不展示 token。"))
    else:
        checks.append(_diagnostic_item("云模型密钥", "warn", "未检测到 Gemini / Claude / Kimi / OpenAI-compatible 环境变量。", "如果没有本地模型和 Codex，请至少配置一个云模型环境变量。"))

    checks.append(_diagnostic_item(
        "浏览器登录态",
        "warn",
        "需要平台登录时会读取本机 Chrome → Safari 登录态；不会展示 cookie。",
        "若遇到登录失败，请先在浏览器打开目标平台确认已登录。",
    ))

    checks.append(_diagnostic_item(
        "输出位置",
        "ok" if paths.get("desktop") else "warn",
        "默认输出：单视频桌面 HTML，列表桌面文件夹。Desktop=" + str(paths.get("desktop") or "未检测"),
        "也可以在新建任务页用“选择输出文件夹”。",
    ))

    checks.append(_diagnostic_item(
        "报告导出",
        "ok" if report.get("pdf", {}).get("ok") else "warn",
        "HTML 始终可用；PDF " + ("可用，浏览器=" + str(report.get("pdf", {}).get("browser_path") or "") if report.get("pdf", {}).get("ok") else "需要 Chrome / Edge / Chromium / Brave。"),
    ))

    checks.append(_diagnostic_item(
        "文件选择按钮",
        "ok" if picker.get("available") else "warn",
        "系统文件/文件夹选择框" + ("可用。" if picker.get("available") else "不可用；仍可手动输入绝对路径。"),
    ))

    blocking = [item for item in checks if item["state"] == "fail"]
    warnings = [item for item in checks if item["state"] == "warn"]
    return {
        "ok": not blocking,
        "summary": "可以开始正式任务。" if not blocking else "暂时不建议开始正式任务：先处理红色失败项。",
        "checks": checks,
        "fail_count": len(blocking),
        "warn_count": len(warnings),
        "generated_at": int(time.time()),
    }


def _option(value: str, label: str, *, selected: bool = False, disabled: bool = False) -> str:
    attrs = []
    if selected:
        attrs.append("selected")
    if disabled:
        attrs.append("disabled")
    return f'<option value="{html.escape(value)}" {" ".join(attrs)}>{html.escape(label)}</option>'


def render_report_target_cards() -> str:
    rows = []
    for target in REPORT_TARGETS:
        selected = target == DEFAULT_REPORT_TARGET
        rows.append(
            f'<label class="mode-card{" selected" if selected else ""}">'
            f'<input type="checkbox" name="report_targets" value="{html.escape(target)}"{" checked" if selected else ""} />'
            f'<strong>{html.escape(REPORT_TARGET_LABELS[target])}</strong>'
            f'<p>{html.escape(REPORT_TARGET_DESCRIPTIONS[target])}</p>'
            '</label>'
        )
    return "\n              ".join(rows)


def render_index_html() -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WatchBrief WebUI</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark">WB</span><div><strong>WatchBrief</strong><small>local report UI</small></div></div>
      <nav>
        <button class="nav-item active" data-view="welcome"><span>01</span>欢迎说明</button>
        <button class="nav-item" data-view="run"><span>02</span>新建任务</button>
        <button class="nav-item" data-view="models" data-root="settings"><span>03</span>高级设置</button>
        <button class="nav-item" data-view="diagnostics"><span>04</span>环境诊断</button>
        <button class="nav-item" data-view="history"><span>05</span>任务记录</button>
      </nav>
      <div class="status-box"><span class="status-dot"></span><small id="systemStatus">检测本机服务中</small></div>
    </aside>
    <main class="workspace">
      <button class="config-toggle" id="toggleInspector" type="button" aria-pressed="false" aria-label="隐藏当前配置" title="隐藏当前配置"><span class="config-toggle-icon" aria-hidden="true">›</span></button>
      <section class="view-panel" data-panel="welcome">
        <header class="page-head hero-head">
          <div>
            <p class="eyebrow">WELCOME</p>
            <h1>三步生成观看决策报告</h1>
            <p class="subtitle">像填表一样使用：先登录浏览器，再贴链接，最后点开始。</p>
          </div>
          <button class="primary" type="button" data-jump="run">开始新任务</button>
        </header>
        <div class="welcome-grid">
          <div class="welcome-card"><span>1</span><strong>先登录浏览器</strong><p>打开 Chrome，登录 YouTube、Bilibili 或小红书。WatchBrief 只在你的电脑借用登录状态，不会把密码或 cookie 写进报告。</p></div>
          <div class="welcome-card"><span>2</span><strong>粘贴链接</strong><p>在“新建任务”里粘贴单视频、播放列表或收藏页链接。不会用你的账号乱跑别的页面。</p></div>
          <div class="welcome-card"><span>3</span><strong>点开始运行</strong><p>默认生成桌面 HTML。选 PDF 时，会在 HTML 旁边再生成一个同名 PDF。</p></div>
        </div>
        <div class="starter-panel">
          <div class="step-title"><span>清单</span><strong>完全不懂代码也照着做</strong></div>
          <div class="kid-checklist">
            <div><b>第一步</b><strong>浏览器先登录</strong><p>你平时在哪个平台看视频，就先在 Chrome 里登录那个平台。WatchBrief 不保存你的密码，不展示 cookie。</p></div>
            <div><b>第二步</b><strong>选内容理解模型</strong><p>它先读懂字幕/转写，整理核心观点、方法、例子和关键专名；不生成 HTML。</p></div>
            <div><b>第三步</b><strong>选观看判断模型</strong><p>它判断视频整体价值、报告能否辅助替代、如果要看应该看哪一段。</p></div>
            <div><b>第四步</b><strong>选一个“耳朵”</strong><p>视频没字幕时才需要转写。检测到 MLX-Audio 就用 MLX-Audio；没有它但有 Whisper，就推荐 Whisper。</p></div>
            <div><b>第五步</b><strong>选放哪里和格式</strong><p>不懂就留空。HTML 最稳；PDF 会从 HTML 再打印生成。单视频默认放桌面，列表默认放桌面文件夹。</p></div>
            <div><b>第六步</b><strong>不要填 token</strong><p>这个页面只填环境变量名字，比如 GEMINI_API_KEY，不粘贴 token 内容。你的密钥留在本机环境变量里。</p></div>
          </div>
        </div>
      </section>
      <form id="taskForm">
        <section class="view-panel hidden" data-panel="run">
          <header class="page-head hero-head run-hero-head">
            <div>
              <p class="eyebrow">LOCAL-FIRST VIDEO REPORT</p>
              <h1>普通用户模式：贴链接就能开始</h1>
              <p class="subtitle">像 Google 一样：中间只放一个输入框；粘贴链接、拖入 txt，或点选文件。</p>
            </div>
          </header>
          <div class="starter-panel source-search-panel">
            <div class="step-title source-title"><span>01</span><strong>输入来源</strong></div>
            <div class="google-source-box" id="sourceDropZone">
              <textarea name="source_url" rows="4" placeholder="粘贴视频 / 列表 / 小红书 board 链接
也可以直接把一行一个链接的 txt 文件拖到这里" autocomplete="off"></textarea>
              <input type="hidden" name="source_file" />
              <div class="source-box-actions">
                <span id="sourceChoiceLabel">可粘贴单条链接；批量任务请拖入 txt，或选择 txt 文件。</span>
                <button type="button" id="chooseUrlFile">选择 txt 文件</button>
              </div>
            </div>
            <div class="source-submit-row">
              <button class="primary source-submit" type="submit">开始运行</button>
            </div>
          </div>
          <div class="output-choice-banner">
            <div><strong>输出位置</strong><span id="outputChoiceLabel">默认：单视频放桌面 HTML，列表放桌面文件夹。</span></div>
            <button type="button" id="chooseOutputDirMain">选择输出文件夹</button>
            <button type="button" id="resetOutputDefault">使用默认</button>
          </div>
          <details class="starter-panel compact advanced-block collapsed-choice-block">
            <summary><span>02</span><strong>报告目标</strong><small>可多选；默认观看决策，和分析模式分开</small></summary>
            <div class="mode-grid report-target-grid">
              {render_report_target_cards()}
            </div>
          </details>
          <details class="starter-panel compact advanced-block collapsed-choice-block">
            <summary><span>03</span><strong>分析模式</strong><small>默认 Standard，不懂就不用展开</small></summary>
            <div class="mode-grid">
              <label class="mode-card"><input type="radio" name="analysis_mode" value="fast" /><strong>Fast 快速筛选</strong><p>最快判断大概值不值得看；复用缓存，不保留 debug。</p></label>
              <label class="mode-card selected"><input type="radio" name="analysis_mode" value="standard" checked /><strong>Standard 标准报告</strong><p>默认推荐：结构化理解后生成观看决策；缓存命中时复用。</p></label>
              <label class="mode-card"><input type="radio" name="analysis_mode" value="deep" /><strong>Deep 深度分析</strong><p>重新分析、不走旧缓存，自动保留 debug，适合重要视频复核。</p></label>
            </div>
          </details>
          <details class="starter-panel compact scoring-panel advanced-block collapsed-choice-block">
            <summary><span>04</span><strong>我的评分规则</strong><small>默认均衡，不懂就不用展开</small></summary>
            <p class="plain-help">这里决定“视频整体价值评分”的算法权重。默认不用动；如果你更在意某种视频价值，可以改成自己的判定。</p>
            <div class="mode-grid scoring-grid">
              <label class="mode-card selected"><input type="radio" name="scoring_profile" value="standard" checked /><strong>默认均衡</strong><p>信息密度 20% / 论据质量 30% / 独创性 20% / 观看性价比 30%。</p></label>
              <label class="mode-card"><input type="radio" name="scoring_profile" value="information-first" /><strong>更看重信息量</strong><p>适合知识教程：信息密度权重提高到 40%。</p></label>
              <label class="mode-card"><input type="radio" name="scoring_profile" value="evidence-first" /><strong>更看重证据</strong><p>适合观点/科普：论据质量权重提高到 45%。</p></label>
              <label class="mode-card"><input type="radio" name="scoring_profile" value="originality-first" /><strong>更看重新鲜感</strong><p>适合访谈/创意内容：独创性权重提高到 40%。</p></label>
              <label class="mode-card"><input type="radio" name="scoring_profile" value="watch-value-first" /><strong>更看重原片体验</strong><p>适合演讲/纪录片：表达/观看体验权重提高到 50%。</p></label>
              <label class="mode-card"><input type="radio" name="scoring_profile" value="custom" /><strong>自定义权重</strong><p>在高级选项里填写四项权重；系统会归一化后确定最终分。</p></label>
            </div>
          </details>
          <details class="starter-panel compact advanced-block">
            <summary><span>高级选项</span><strong>运行方式、超时、debug</strong><small>不懂就保持收起</small></summary>
            <div class="grid two">
              <label class="field">运行模式<select name="diagnostic_run"><option value="">正式任务</option><option value="true">诊断 / 复现</option></select></label>
              <label class="field">任务超时<select name="timeout">{_option("600", "600 秒")}{_option("900", "900 秒", selected=True)}{_option("1200", "1200 秒")}</select></label>
              <label class="field span-two">自定义评分权重<input name="scoring_weights" placeholder="custom 时填写：information_density=0.2,evidence_quality=0.3,originality=0.2,watch_value=0.3" /></label>
            </div>
            <div class="switch-grid">
              <label><input type="checkbox" name="force_reanalysis" value="true" /> 强制重新分析</label>
              <label><input type="checkbox" name="keep_debug_artifacts" value="true" /> 保留 debug</label>
              <label><input type="checkbox" name="open_output" value="true" /> 完成后打开</label>
            </div>
            <section class="preview-block advanced-preview">
              <div class="section-title"><h2>高级：命令预览</h2><button type="button" id="previewCommand">刷新预览</button></div>
              <pre id="commandPreview">未生成</pre>
            </section>
          </details>
        </section>

        <section class="view-panel hidden" data-panel="models">
          <header class="page-head hero-head"><div><p class="eyebrow">ADVANCED SETTINGS</p><h1>高级设置：模型与账号</h1><p class="subtitle">普通用户不用改；只有要换模型、账号、endpoint 或 API 环境变量时才进来。</p></div><button class="primary" type="submit">开始运行</button></header>
          <div class="settings-tabs">
            <button class="settings-tab active" type="button" data-settings-view="models">模型与账号</button>
            <button class="settings-tab" type="button" data-settings-view="output">输出与登录态</button>
          </div>
          <div class="capability-grid">
            <div class="cap-card"><strong>本地模型</strong><span id="modelHint">检测中</span></div>
            <div class="cap-card"><strong>转写工具</strong><span id="transcriberHint">检测中</span></div>
            <div class="cap-card"><strong>观看判断</strong><span id="reviewHint">检测中</span></div>
          </div>
          <div class="notice explain-block"><strong>WatchBrief 不让模型直接写 HTML。</strong><br />内容理解模型先读字幕/转写，整理核心观点、方法、例子、限制和关键专名；观看判断模型再决定视频整体价值、报告能否辅助替代、建议看哪一段；最后由固定 HTML 模板生成报告。没有本地大模型时，可切到 Gemini、Claude、Kimi 或 OpenAI-compatible。</div>
          <div class="notice explain-block"><strong>小红书专辑会完整保留内容结构：</strong><br />1. 保留专辑标题和原始顺序；2. 每条 note 至少保留 note_id、标准 note_url、标题、作者和类型；3. 视频 note 进入单视频报告；4. 图文 note 标记为图文跳过，不算失败；5. raw metadata 只进 debug/manifest，不进正式 HTML。</div>
          <div class="grid two">
            <label class="field">内容理解模型<select name="extract_provider">
              {_option("local-qwen", "本地 Qwen，推荐有 LM Studio 的用户", selected=True)}
              {_option("local-openai-compatible", "本地通用模型，Gemma / Llama / Mistral 等")}
              {_option("openai-compatible", "OpenAI-compatible 云端接口")}
              {_option("gemini", "Gemini 云端理解")}
              {_option("claude", "Claude 云端理解")}
              {_option("kimi", "Kimi 云端理解")}
              {_option("codex-cli-extract", "Codex CLI 做内容理解")}
            </select></label>
            <div class="engine-note" id="extractEngineHint">选择内容理解模型后，模型名、endpoint 和 API key 环境变量会跟着切换。</div>
            <div class="subgrid span-two" data-extract-panel="qwen">
              <label class="field">本地 Qwen 模型<input name="qwen_model" value="{DEFAULT_QWEN_MODEL}" /></label>
              <label class="field">Qwen endpoint<input name="qwen_api_base" placeholder="{DEFAULT_QWEN_BASE_URL}" /></label>
              <label class="field">Qwen timeout<input name="qwen_timeout" placeholder="默认跟随任务超时" /></label>
            </div>
            <div class="subgrid span-two is-hidden" data-extract-panel="external">
              <label class="field">内容理解模型名<input name="extract_model" data-profiled="extract_model" placeholder="非 Qwen 时填写，例如 gemma-3 或 gemini-2.5-flash" /></label>
              <label class="field">内容理解 API endpoint<input name="extract_api_base" data-profiled="extract_api_base" placeholder="本地通用模型可留空，默认用 127.0.0.1:1234/v1" /></label>
              <label class="field span-two">内容理解 API key 环境变量<input name="extract_api_key_env" data-profiled="extract_api_key_env" placeholder="只填变量名，例如 GEMINI_API_KEY；不要填 token" /></label>
            </div>
            <label class="field">转写器<select name="transcriber">{_option("recommended", "推荐：读取本机后自动选择", selected=True)}{_option("auto", "auto：MLX-Audio")}{_option("mlx_audio", "MLX-Audio")}{_option("whisper", "Whisper")}</select></label>
            <label class="field">MLX-Audio 模型<input name="mlx_model" placeholder="默认 mlx-community/whisper-large-v3-turbo" /></label>
            <label class="field">Whisper 模型<input name="whisper_model" placeholder="base" /></label>
          </div>
          <label class="inline-check"><input type="checkbox" name="allow_whisper_fallback" value="true" /> 允许 MLX-Audio 不可用时回退 Whisper</label>
          <div class="grid two">
            <label class="field">观看判断模型<select name="review_provider">
              {_option("codex-cli", "Codex CLI 判断，推荐正式报告", selected=True)}
              {_option("local", "本地规则判断，无需账号；仅作降级")}
              {_option("gemini", "Gemini 判断")}
              {_option("claude", "Claude 判断")}
              {_option("kimi", "Kimi 判断")}
              {_option("openai-compatible", "OpenAI-compatible 判断")}
              {_option("mock", "Mock 测试用")}
            </select></label>
            <div class="engine-note" id="reviewEngineHint">选择观看判断模型后，模型、账号目录或 API key 环境变量会跟着切换。</div>
            <div class="subgrid span-two is-hidden" data-review-panel="codex">
              <label class="field">Codex 模型<input name="codex_model" value="{DEFAULT_CODEX_MODEL}" /></label>
              <label class="field">Codex 账号目录<input name="codex_home_root" value="{DEFAULT_CODEX_HOME_ROOT}" /></label>
              <label class="field">Codex 账号<input name="codex_account" value="{DEFAULT_CODEX_ACCOUNT}" /></label>
            </div>
            <div class="subgrid span-two is-hidden" data-review-panel="cloud">
              <label class="field">观看判断模型名<input name="review_model" data-profiled="review_model" placeholder="例如 gemini-2.5-flash / claude-sonnet-4-20250514 / kimi-k2.5" /></label>
              <label class="field">观看判断 API endpoint<input name="review_api_base" data-profiled="review_api_base" placeholder="OpenAI-compatible 时填写；本地可用 127.0.0.1:1234/v1" /></label>
              <label class="field span-two">观看判断 API key 环境变量<input name="review_api_key_env" data-profiled="review_api_key_env" placeholder="只填变量名，例如 ANTHROPIC_API_KEY；不要填 token" /></label>
            </div>
            <div class="subgrid span-two is-hidden" data-review-panel="mock">
              <label class="field span-two">Mock response JSON<input name="mock_review_response" placeholder="/path/to/sample_payload.json" /></label>
            </div>
          </div>
          <div class="notice">没有 Codex 账号就选“本地规则判断”。没有 Qwen 时，可以切到本地通用模型或云端模型；密钥只通过环境变量读取。HTML 始终由固定模板生成。</div>
        </section>

        <section class="view-panel hidden" data-panel="output">
          <header class="page-head hero-head"><div><p class="eyebrow">DELIVERY</p><h1>输出与登录态</h1><p class="subtitle">正式输出默认放到桌面。</p></div><button class="primary" type="submit">开始运行</button></header>
          <div class="settings-tabs">
            <button class="settings-tab" type="button" data-settings-view="models">模型与账号</button>
            <button class="settings-tab active" type="button" data-settings-view="output">输出与登录态</button>
          </div>
          <div class="grid two">
            <label class="field">输出方式<select name="output_mode">{_option("default", "正式默认：单视频桌面 HTML / 列表桌面文件夹", selected=True)}{_option("custom", "自定义最终目录")}{_option("diagnostic", "诊断临时目录")}</select></label>
            <label class="field">输出目录
              <span class="path-picker">
                <input name="output_dir" placeholder="自定义时填写最终目录；正式默认可留空" />
                <button type="button" id="chooseOutputDir">选择文件夹</button>
              </span>
            </label>
            <label class="field">报告格式<select name="report_format">{_option("html", "HTML", selected=True)}{_option("pdf", "PDF（同时保留 HTML）")}</select></label>
            <label class="field">渲染方式<select name="renderer">{_option("local-html", "本地 HTML renderer", selected=True)}{_option("pdf-export", "PDF export")}</select></label>
            <label class="field">登录态<select name="browser_auth">{_option("auto", "自动：Chrome → Safari", selected=True)}{_option("chrome", "Chrome")}{_option("safari", "Safari")}{_option("edge", "Edge")}{_option("none", "不使用登录态")}</select></label>
            <label class="field span-two">cookies.txt 路径<input name="cookies_file" placeholder="可选；只传路径，不读取或展示内容" /></label>
          </div>
          <div class="notice" id="outputHints">读取本机输出路径中</div>
          <div class="rule-strip">
            <span>单视频默认输出桌面 HTML</span>
            <span>列表默认输出桌面任务文件夹</span>
            <span>默认不打开浏览器</span>
          </div>
        </section>
      </form>

      <section class="view-panel hidden" data-panel="diagnostics">
        <header class="page-head hero-head">
          <div>
            <p class="eyebrow">ENVIRONMENT CHECK</p>
            <h1>环境诊断</h1>
            <p class="subtitle">把模型来源、转写、Codex、云模型环境变量、登录态、输出和 PDF 能力集中到一个独立页面；不读取、不展示 token。</p>
          </div>
          <button type="button" id="runDiagnostics">运行诊断</button>
        </header>
        <div class="diagnostics-page-grid">
          <div class="diagnostics-panel diagnostics-hero" id="diagnosticsPanel">
            <div><strong>一键环境诊断</strong><span>这里是独立诊断窗口，不再挤在新建任务表单里。诊断只看本机能力和环境变量配置状态，不会启动视频任务。</span></div>
            <button type="button" id="runDiagnosticsHero">重新诊断</button>
            <div id="diagnosticResults" class="diagnostic-results">还没运行诊断。</div>
          </div>
          <div class="starter-panel compact">
            <div class="step-title"><span>说明</span><strong>诊断结果怎么看</strong></div>
            <div class="kid-checklist compact-list">
              <div><b>ok</b><strong>可以用</strong><p>这项能力已经检测到，可以进入正式任务。</p></div>
              <div><b>warn</b><strong>提醒</strong><p>不是硬阻塞，但遇到平台登录、PDF 或云模型时可能需要处理。</p></div>
              <div><b>fail</b><strong>先修</strong><p>这类问题通常会导致任务无法完成，例如没有任何模型来源。</p></div>
            </div>
          </div>
        </div>
      </section>

      <section class="view-panel hidden" data-panel="history">
        <header class="page-head hero-head"><div><p class="eyebrow">HISTORY</p><h1>任务记录</h1><p class="subtitle">只记录 WebUI 启动的本地任务；运行中会自动刷新。</p></div><div class="header-actions"><button type="button" id="refreshTasks">刷新</button><button type="button" id="clearTasks">清空任务记录</button></div></header>
        <div class="rescue-panel dismissible-banner" id="historyRescuePanel">
          <div><strong>失败处理</strong><span>失败时先看日志；环境问题去诊断页，重要视频再用 Deep 复核。</span></div>
          <button type="button" id="historyDiagnostics">环境诊断</button>
          <button type="button" id="historyDeepRetry">用 Deep 重试</button>
          <button type="button" class="banner-close" data-dismiss="historyRescuePanel" aria-label="关闭失败处理提示" title="关闭">×</button>
        </div>
        <div id="taskList" class="task-list"><div class="empty">暂无任务</div></div>
      </section>


    </main>
    <aside class="inspector">
      <div class="inspector-head"><h2>当前配置</h2><span>可隐藏</span></div>
      <dl id="summary">
        <dt>源项目</dt><dd>{html.escape(str(PROJECT_ROOT))}</dd>
        <dt>内容理解</dt><dd id="summaryExtract">默认本地 Qwen；可切云模型</dd>
        <dt>观看判断</dt><dd id="summaryReview">本地规则；Codex / 云模型可切换</dd>
        <dt>转写器</dt><dd id="summaryTranscriber">读取本机后决定</dd>
        <dt>输出</dt><dd id="summaryReport">HTML；PDF 需本机浏览器导出</dd>
        <dt>HTML 生成</dt><dd>固定模板，不由模型自由生成</dd>
      </dl>
      <pre id="toast"></pre>
    </aside>
  </div>
  <script src="/app.js"></script>
</body>
</html>"""


STYLES_CSS = r"""
:root{
  --bg:#f4f7fb;
  --panel:#ffffff;
  --ink:#171b22;
  --muted:#667085;
  --line:#d8e0ea;
  --line-strong:#b8c7d9;
  --nav:#101820;
  --cyan:#0e7490;
  --green:#11845b;
  --red:#c2413f;
  --soft:#edf5f8;
  --blue:#1d4ed8;
}
*{box-sizing:border-box}
body{
  margin:0;
  min-height:100vh;
  background-color:var(--bg);
  background-image:
    linear-gradient(rgba(16,24,32,.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(16,24,32,.05) 1px, transparent 1px);
  background-size:28px 28px;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;
  color:var(--ink);
}
.app-shell{
  min-height:100vh;
  display:grid;
  grid-template-columns:228px minmax(0,1fr)310px;
}
.app-shell.inspector-hidden{
  grid-template-columns:228px minmax(0,1fr);
}
.app-shell.inspector-hidden .inspector{
  display:none;
}
.sidebar{
  background:var(--nav);
  color:#eef6f8;
  padding:18px;
  display:flex;
  flex-direction:column;
  gap:22px;
}
.brand{
  min-height:44px;
  display:flex;
  align-items:center;
  gap:10px;
}
.brand strong{display:block;font-size:16px}
.brand small{display:block;color:#9fb4c0;font-size:12px;margin-top:2px}
.brand-mark{
  width:36px;
  height:36px;
  border-radius:8px;
  background:#e8fbff;
  color:#0b3440;
  display:grid;
  place-items:center;
  font-weight:800;
}
nav{display:grid;gap:8px}
.nav-item{
  width:100%;
  min-height:42px;
  border:1px solid transparent;
  background:transparent;
  border-radius:8px;
  text-align:left;
  padding:0 10px;
  font:inherit;
  color:#b7c7d0;
  cursor:pointer;
  display:flex;
  align-items:center;
  gap:10px;
}
.nav-item span{
  width:28px;
  height:24px;
  border-radius:6px;
  display:grid;
  place-items:center;
  background:rgba(255,255,255,.08);
  color:#d6f7ff;
  font-size:12px;
  font-weight:800;
}
.nav-item.active{
  background:#eefbff;
  color:#0b3440;
  font-weight:800;
}
.nav-item.active span{background:#0e7490;color:#fff}
.status-box{
  margin-top:auto;
  border:1px solid rgba(255,255,255,.14);
  border-radius:8px;
  padding:12px;
  display:flex;
  gap:8px;
  align-items:center;
  color:#d5e5ec;
}
.status-dot{
  width:8px;
  height:8px;
  border-radius:50%;
  background:#22c55e;
  flex:0 0 auto;
}
.workspace{padding:24px;overflow:auto}
.config-toggle{
  position:fixed;
  right:310px;
  top:50%;
  transform:translate(50%,-50%);
  z-index:20;
  width:30px;
  height:56px;
  border:1px solid rgba(14,116,144,.24);
  border-right:0;
  border-radius:18px 0 0 18px;
  background:rgba(255,255,255,.94);
  color:#0e7490;
  font:inherit;
  font-size:24px;
  font-weight:900;
  padding:0;
  display:grid;
  place-items:center;
  cursor:pointer;
  box-shadow:0 14px 34px rgba(15,23,42,.14);
  backdrop-filter:blur(12px);
  transition:right .18s ease, transform .18s ease, background .18s ease, color .18s ease;
}
.config-toggle:hover{
  background:#0e7490;
  color:#fff;
}
.config-toggle-icon{
  display:block;
  line-height:1;
  transform:translateX(1px);
}
.app-shell.inspector-hidden .config-toggle{
  right:0;
  transform:translateY(-50%);
  border:1px solid rgba(14,116,144,.24);
  border-right:0;
}
.app-shell.inspector-hidden .config-toggle-icon{
  transform:rotate(180deg) translateX(-1px);
}
.inspector{
  border-left:1px solid var(--line);
  background:rgba(255,255,255,.88);
  padding:22px;
  overflow:auto;
}
.inspector-head{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  margin-bottom:14px;
}
.inspector-head h2{margin:0}
.inspector-head span{
  color:var(--muted);
  font-size:12px;
  font-weight:800;
}
.page-head{
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:18px;
  margin-bottom:18px;
}
.hero-head h1{
  margin:2px 0 4px;
  font-size:30px;
  line-height:1.15;
  letter-spacing:0;
}
.eyebrow{
  margin:0;
  color:var(--cyan);
  font-size:12px;
  font-weight:900;
  letter-spacing:0;
}
.subtitle{
  margin:0;
  color:var(--muted);
  font-size:14px;
}
.grid{display:grid;gap:14px}
.grid.two{grid-template-columns:repeat(2,minmax(0,1fr))}
.quick-status-grid,.capability-grid{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:10px;
  margin-bottom:14px;
}
.welcome-grid{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:12px;
  margin-bottom:14px;
}
.welcome-card{
  min-height:160px;
  padding:16px;
  display:grid;
  gap:10px;
  align-content:start;
}
.welcome-card span{
  width:34px;
  height:30px;
  border-radius:8px;
  display:grid;
  place-items:center;
  background:#e7f8fb;
  color:#0e7490;
  font-weight:900;
}
.welcome-card strong{font-size:17px}
.welcome-card p,.guide-grid p{margin:0;color:var(--muted);font-size:13px;line-height:1.55}
.guide-grid{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:12px;
}
.guide-grid div{
  border:1px solid var(--line);
  border-radius:8px;
  padding:12px;
  background:#fff;
}
.kid-checklist{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:12px;
}
.kid-checklist div{
  border:1px solid var(--line);
  border-radius:8px;
  background:#fff;
  padding:12px;
  display:grid;
  gap:6px;
}
.kid-checklist b{
  color:#0e7490;
  font-size:12px;
}
.kid-checklist strong{font-size:15px}
.kid-checklist p{
  margin:0;
  color:var(--muted);
  line-height:1.55;
  font-size:13px;
}
.settings-tabs{
  display:flex;
  gap:8px;
  margin-bottom:14px;
}
.settings-tab{
  min-height:38px;
  border:1px solid var(--line);
  border-radius:8px;
  background:#fff;
  color:var(--muted);
  font:inherit;
  font-weight:800;
  padding:0 12px;
  cursor:pointer;
}
.settings-tab.active{
  border-color:#0e7490;
  background:#e7f8fb;
  color:#0e7490;
}
.signal,.cap-card,.starter-panel,.preflight-panel,.preview-block,.task-list,.rule-strip span,.welcome-card{
  border:1px solid var(--line);
  border-radius:8px;
  background:rgba(255,255,255,.94);
  box-shadow:0 12px 30px rgba(15,23,42,.06);
}
.preflight-panel{
  margin:0 0 14px;
  padding:14px;
  border-radius:14px;
  border-color:#bbf7d0;
  background:linear-gradient(135deg,#f0fdf4,#ffffff);
}
.preflight-grid{
  display:grid;
  grid-template-columns:repeat(5,minmax(0,1fr));
  gap:10px;
}
.preflight-item{
  border:1px solid #dcfce7;
  border-radius:10px;
  background:#fff;
  padding:10px;
  display:grid;
  gap:6px;
}
.preflight-item b{font-size:12px;color:#166534}
.preflight-item span{font-size:12px;line-height:1.45;color:#475569}
.preflight-item.ok{border-color:#86efac;background:#f0fdf4}
.preflight-item.warn{border-color:#fed7aa;background:#fff7ed}
.preflight-item.fail{border-color:#fecaca;background:#fef2f2}
.preflight-item.fail b{color:#b91c1c}
.simple-mode-banner{
  margin:0 0 14px;
  border:1px solid #bae6fd;
  border-radius:14px;
  background:linear-gradient(135deg,#ecfeff,#f8fbff);
  color:#0f3443;
  padding:14px 16px;
  display:grid;
  grid-template-columns:auto minmax(0,1fr)120px;
  gap:12px;
  align-items:center;
  box-shadow:0 12px 30px rgba(15,23,42,.06);
}
.simple-mode-banner strong{font-size:15px;color:#0e7490}
.simple-mode-banner span{font-size:13px;line-height:1.5;color:#475569}
.simple-mode-banner button{
  min-height:36px;
  border:1px solid #0e7490;
  border-radius:8px;
  background:#fff;
  color:#0e7490;
  font:inherit;
  font-size:13px;
  font-weight:900;
  cursor:pointer;
}
.output-choice-banner,.no-local-model-banner,.rescue-panel,.diagnostics-panel{
  margin:0 0 14px;
  border:1px solid #c7d2fe;
  border-radius:14px;
  background:linear-gradient(135deg,#eef2ff,#ffffff);
  padding:14px 16px;
  display:grid;
  grid-template-columns:minmax(0,1fr)140px 100px;
  gap:10px;
  align-items:center;
  box-shadow:0 12px 30px rgba(15,23,42,.06);
}
.no-local-model-banner{
  border-color:#fed7aa;
  background:linear-gradient(135deg,#fff7ed,#ffffff);
  grid-template-columns:minmax(0,1fr)180px 140px;
}
.rescue-panel{
  border-color:#fecaca;
  background:linear-gradient(135deg,#fff1f2,#ffffff);
  grid-template-columns:minmax(0,1fr)110px 110px 140px 110px;
}
.diagnostics-panel{
  border-color:#bae6fd;
  background:linear-gradient(135deg,#eff6ff,#ffffff);
  grid-template-columns:minmax(0,1fr)140px;
}
.diagnostics-page-grid{
  display:grid;
  gap:18px;
}
.diagnostics-hero{
  align-items:start;
}
.compact-list{
  grid-template-columns:repeat(3,minmax(0,1fr));
}
.diagnostic-results{
  grid-column:1 / -1;
  display:grid;
  gap:8px;
  font-size:13px;
  color:#475569;
}
.diagnostic-row{
  border:1px solid #e2e8f0;
  border-radius:10px;
  padding:10px;
  background:#fff;
  display:grid;
  gap:4px;
}
.diagnostic-row.ok{border-color:#bbf7d0;background:#f0fdf4}
.diagnostic-row.warn{border-color:#fde68a;background:#fffbeb}
.diagnostic-row.fail{border-color:#fecaca;background:#fff1f2}
.diagnostic-row b{font-size:13px;color:#0f172a}
.diagnostic-row span{font-size:12px;color:#475569;line-height:1.45}
.diagnostic-row em{font-size:12px;color:#be123c;font-style:normal}
.output-choice-banner div,.no-local-model-banner div,.rescue-panel div,.diagnostics-panel div{display:grid;gap:5px}
.output-choice-banner strong{font-size:15px;color:#1d4ed8}
.no-local-model-banner strong{font-size:15px;color:#c2410c}
.rescue-panel strong{font-size:15px;color:#be123c}
.diagnostics-panel strong{font-size:15px;color:#0369a1}
.output-choice-banner span,.no-local-model-banner span,.rescue-panel span,.diagnostics-panel span{font-size:13px;line-height:1.5;color:#475569;overflow-wrap:anywhere}
.output-choice-banner button,.no-local-model-banner button,.rescue-panel button,.diagnostics-panel button{
  min-height:38px;
  border:1px solid #1d4ed8;
  border-radius:8px;
  background:#fff;
  color:#1d4ed8;
  font:inherit;
  font-size:13px;
  font-weight:900;
  cursor:pointer;
}
.no-local-model-banner button{border-color:#c2410c;color:#c2410c}
.rescue-panel button{border-color:#be123c;color:#be123c}
.diagnostics-panel button{border-color:#0369a1;color:#0369a1}
.dismissible-banner{position:relative;padding-right:52px}
.banner-close{
  position:absolute;
  top:10px;
  right:10px;
  width:30px;
  min-height:30px!important;
  border-radius:999px!important;
  padding:0!important;
  display:grid;
  place-items:center;
  background:#fff!important;
  color:#334155!important;
  border-color:#cbd5e1!important;
}
.output-choice-banner button:first-of-type{background:#1d4ed8;color:#fff}
.no-local-model-banner button:first-of-type{background:#c2410c;color:#fff}
.rescue-panel button:last-of-type{background:#be123c;color:#fff}
.diagnostics-panel button{background:#0369a1;color:#fff}
.plain-help{
  margin-top:10px;
  color:#64748b;
  font-size:12px;
  line-height:1.55;
}
.plain-help code{
  padding:1px 5px;
  border-radius:5px;
  background:#f1f5f9;
  color:#0f172a;
}
.source-file-picker{grid-template-columns:minmax(0,1fr)92px}
.signal{
  min-height:74px;
  padding:12px;
  display:grid;
  align-content:center;
  gap:6px;
}
.signal span,.cap-card strong{
  font-size:12px;
  color:var(--muted);
  font-weight:800;
}
.signal strong{
  font-size:15px;
  overflow-wrap:anywhere;
}
.cap-card{
  padding:12px;
  display:grid;
  gap:6px;
  min-height:80px;
}
.cap-card span{
  font-size:12px;
  line-height:1.45;
  color:var(--muted);
  overflow-wrap:anywhere;
}
.starter-panel{
  padding:14px;
  margin-bottom:14px;
}
.starter-panel.compact{padding-bottom:10px}
.step-title{
  display:flex;
  align-items:center;
  gap:10px;
  margin-bottom:12px;
}
.step-title span{
  width:30px;
  height:26px;
  border-radius:6px;
  display:grid;
  place-items:center;
  background:#e7f8fb;
  color:#0e7490;
  font-size:12px;
  font-weight:900;
}
.step-title strong{font-size:15px}
.advanced-block summary{
  list-style:none;
  display:flex;
  align-items:center;
  gap:10px;
  cursor:pointer;
}
.advanced-block summary::-webkit-details-marker{display:none}
.advanced-block summary span{
  border-radius:999px;
  background:#eef2ff;
  color:#1d4ed8;
  font-size:12px;
  font-weight:900;
  padding:5px 9px;
}
.advanced-block summary strong{font-size:15px}
.advanced-block summary small{margin-left:auto;color:var(--muted);font-size:12px;font-weight:800}
.advanced-block[open] summary{margin-bottom:12px}
.advanced-preview{border-style:dashed;background:#fcfdff;margin-top:12px}

.run-hero-head{margin-bottom:18px;text-align:center;justify-content:center}
.run-hero-head h1{font-size:34px;letter-spacing:-.03em}
.run-hero-head .subtitle{max-width:680px;margin:8px auto 0}
.source-search-panel{
  max-width:820px;
  margin:0 auto 16px;
  border-radius:24px;
  padding:22px;
  border-color:#dbeafe;
  background:linear-gradient(180deg,#ffffff,#f8fbff);
}
.source-title{justify-content:center;margin-bottom:12px}
.google-source-box{
  border:2px solid #dbeafe;
  border-radius:26px;
  background:#fff;
  padding:18px;
  box-shadow:0 18px 50px rgba(15,23,42,.10);
  transition:border-color .16s ease, box-shadow .16s ease, transform .16s ease;
}
.google-source-box.drag-over{
  border-color:var(--cyan);
  box-shadow:0 20px 58px rgba(14,116,144,.18);
  transform:translateY(-1px);
}
.google-source-box textarea{
  width:100%;
  min-height:118px;
  border:0;
  resize:vertical;
  outline:none;
  font:inherit;
  font-size:18px;
  line-height:1.55;
  color:var(--ink);
  background:transparent;
}
.google-source-box textarea::placeholder{color:#94a3b8}
.source-box-actions{
  display:flex;
  gap:12px;
  align-items:center;
  justify-content:space-between;
  border-top:1px solid #e2e8f0;
  padding-top:12px;
  margin-top:8px;
  color:#64748b;
  font-size:13px;
}
.source-box-actions button{
  min-height:38px;
  border:1px solid #cbd5e1;
  border-radius:999px;
  background:#101820;
  color:#fff;
  padding:0 16px;
  font:inherit;
  font-size:13px;
  font-weight:900;
  cursor:pointer;
  white-space:nowrap;
}
.source-submit-row{display:flex;justify-content:center;margin-top:18px}
.source-submit{min-width:180px;min-height:48px;border-radius:999px;font-size:16px}
.input-row{
  display:grid;
  grid-template-columns:minmax(0,1.4fr)42px minmax(0,1fr);
  gap:12px;
  align-items:end;
}
.or-label{
  height:44px;
  display:grid;
  place-items:center;
  color:var(--muted);
}
.field{
  display:grid;
  gap:7px;
  font-size:13px;
  color:var(--muted);
  font-weight:800;
}
.field input,.field select,.field textarea{
  width:100%;
  height:42px;
  border:1px solid var(--line);
  border-radius:8px;
  background:#fff;
  padding:0 11px;
  color:var(--ink);
  font:inherit;
  font-weight:500;
  outline:none;
}
.source-field input{height:48px;font-size:15px}
.field input:focus,.field select:focus,.field textarea:focus{
  border-color:var(--cyan);
  box-shadow:0 0 0 3px rgba(14,116,144,.12);
}
.path-picker{
  display:grid;
  grid-template-columns:minmax(0,1fr)112px;
  gap:8px;
}
.path-picker button{
  height:42px;
  border:1px solid var(--line-strong);
  border-radius:8px;
  background:#101820;
  color:#fff;
  font:inherit;
  font-size:13px;
  font-weight:900;
  cursor:pointer;
}
.field.wide{min-width:0}
.span-two{grid-column:span 2}
.primary,#refreshTasks,#clearTasks,#previewCommand{
  min-height:40px;
  border:0;
  border-radius:8px;
  background:var(--cyan);
  color:#fff;
  font-weight:800;
  padding:0 16px;
  cursor:pointer;
}
.primary{min-width:118px}
#refreshTasks,#clearTasks,#previewCommand{background:#18232d}
#clearTasks{background:#7f1d1d}
.header-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.view-panel{max-width:1040px}
.hidden{display:none}
.is-hidden{display:none!important}
.subgrid{
  grid-column:1/-1;
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:14px;
}
.engine-note{
  min-height:42px;
  border:1px solid #cbe9ef;
  border-radius:8px;
  background:#f0fbfd;
  color:#164e63;
  padding:10px 12px;
  font-size:13px;
  line-height:1.45;
}
.switch-grid{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:10px;
  margin-top:14px;
}
.switch-grid label,.inline-check{
  min-height:40px;
  border:1px solid var(--line);
  border-radius:8px;
  background:#fff;
  display:flex;
  align-items:center;
  gap:8px;
  padding:0 10px;
  color:var(--ink);
  font-size:13px;
}
.section-title{
  min-height:44px;
  border-bottom:1px solid var(--line);
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding:0 12px;
}
.section-title h2{font-size:15px;margin:0}
pre{white-space:pre-wrap;overflow:auto}
#commandPreview{
  min-height:92px;
  margin:0;
  padding:12px;
  color:#20303f;
  background:#fbfdff;
}
.notice{
  margin-top:14px;
  border:1px solid #cbe9ef;
  background:#f0fbfd;
  color:#164e63;
  border-radius:8px;
  padding:12px;
}
.rule-strip{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:10px;
  margin-top:14px;
}
.rule-strip span{
  padding:12px;
  color:var(--muted);
  font-size:13px;
  box-shadow:none;
}
.task-item{
  display:grid;
  grid-template-columns:1fr auto;
  gap:12px;
  align-items:start;
  padding:14px;
  border-bottom:1px solid var(--line);
}
.task-item:last-child{border-bottom:0}
.task-main{min-width:0;display:grid;gap:7px}
.task-error{color:var(--red);line-height:1.55}
.task-phases{
  display:flex;
  flex-wrap:wrap;
  gap:6px;
  margin-top:4px;
}
.phase{
  border:1px solid #dbeafe;
  border-radius:999px;
  padding:4px 8px;
  background:#f8fbff;
  color:#475569;
  font-size:12px;
  font-weight:800;
}
.phase.active{background:#e0f2fe;color:#0369a1;border-color:#7dd3fc}
.phase.done{background:#ecfdf5;color:#047857;border-color:#a7f3d0}
.phase.failed{background:#fee2e2;color:#b91c1c;border-color:#fecaca}
.task-actions{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin-top:2px;
}
.task-action{
  min-height:30px;
  border:1px solid var(--line);
  border-radius:999px;
  background:#fff;
  color:#334155;
  padding:6px 10px;
  font-size:12px;
  font-weight:900;
  text-decoration:none;
  cursor:pointer;
}
.task-action.primary{background:#0e7490;color:#fff;border-color:#0e7490}
.task-action.danger{background:#fee2e2;color:#991b1b;border-color:#fecaca}
.pill{
  border-radius:8px;
  padding:4px 8px;
  font-size:12px;
  background:#e8f7ef;
  color:var(--green);
  font-weight:800;
}
.pill.failed{background:#ffeceb;color:var(--red)}
.pill.running{background:#eaf6ff;color:#0e7490}
.empty{padding:18px;color:var(--muted);text-align:center}
dl{margin:0;display:grid;gap:12px}
dt{font-size:12px;color:var(--muted);font-weight:800}
dd{margin:0;font-size:13px;line-height:1.45;overflow-wrap:anywhere}

.mode-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.mode-card{border:1px solid var(--line);border-radius:14px;padding:14px;background:#fff;cursor:pointer;display:block}
.mode-card input{margin-right:6px}
.mode-card strong{display:block;margin:6px 0;color:var(--ink)}
.mode-card p{margin:0;color:var(--muted);font-size:13px;line-height:1.5}
.mode-card.selected{border-color:var(--blue);box-shadow:0 0 0 2px rgba(141,177,232,.18);background:#f8fbff}
.explain-block{line-height:1.7}
#toast{
  min-height:110px;
  margin-top:18px;
  border-radius:8px;
  background:#111827;
  color:#e5e7eb;
  padding:12px;
  font-size:12px;
}
@media(max-width:1050px){
  .app-shell{grid-template-columns:1fr}
  .sidebar,.inspector{border:0}
  .config-toggle{right:0;top:56%;transform:translateY(-50%)}
  .grid.two,.subgrid,.input-row,.path-picker,.switch-grid,.rule-strip,.capability-grid,.quick-status-grid,.welcome-grid,.guide-grid,.kid-checklist,.mode-grid,.simple-mode-banner,.output-choice-banner,.no-local-model-banner,.preflight-grid,.rescue-panel,.diagnostics-panel{grid-template-columns:1fr}
  .span-two{grid-column:auto}
}
"""


APP_JS = r"""
const $ = (s) => document.querySelector(s);
const PROJECT_ROOT_JS = '/Users/apple/Documents/New project/watchbrief_v5';
const toast = (msg) => { $('#toast').textContent = msg; };
let LAST_STATUS = null;
let TASK_REFRESH_TIMER = null;

const DEFAULT_EXTRACT_PROFILES = {
  'local-openai-compatible': {model:'', base:'http://127.0.0.1:1234/v1', env:'', hint:'本地 Gemma / Llama / Mistral：填 LM Studio 或 Ollama 暴露的模型名，endpoint 默认本机 1234。'},
  'openai-compatible': {model:'', base:'', env:'WATCHBRIEF_OPENAI_COMPATIBLE_API_KEY', hint:'OpenAI-compatible 云端或自建服务：模型名、endpoint、API key 环境变量必须成组填写。'},
  gemini: {model:'gemini-2.5-flash', base:'https://generativelanguage.googleapis.com/v1beta', env:'GEMINI_API_KEY', hint:'Gemini 提炼：模型和 GEMINI_API_KEY 环境变量一起使用。'},
  claude: {model:'claude-sonnet-4-20250514', base:'https://api.anthropic.com/v1', env:'ANTHROPIC_API_KEY', hint:'Claude 提炼：模型和 ANTHROPIC_API_KEY 环境变量一起使用。'},
  kimi: {model:'kimi-k2.5', base:'https://api.moonshot.ai/v1', env:'MOONSHOT_API_KEY', hint:'Kimi 提炼：模型和 MOONSHOT_API_KEY 环境变量一起使用。'},
  'codex-cli-extract': {model:'gpt-5.5', base:'', env:'', hint:'Codex CLI 做内容理解：使用下方 Codex 模型、账号目录和账号。'}
};

const DEFAULT_REVIEW_PROFILES = {
  local: {model:'', base:'', env:'', hint:'本地规则判断：不需要账号、不需要 API key，不调用 Codex 或云模型。'},
  'codex-cli': {model:'gpt-5.5', base:'', env:'', hint:'Codex CLI：使用 Codex 模型、账号目录和账号。'},
  gemini: {model:'gemini-2.5-flash', base:'https://generativelanguage.googleapis.com/v1beta', env:'GEMINI_API_KEY', hint:'Gemini 观看判断：使用 Gemini 模型和 GEMINI_API_KEY。'},
  claude: {model:'claude-sonnet-4-20250514', base:'https://api.anthropic.com/v1', env:'ANTHROPIC_API_KEY', hint:'Claude 观看判断：使用 Claude 模型和 ANTHROPIC_API_KEY。'},
  kimi: {model:'kimi-k2.5', base:'https://api.moonshot.ai/v1', env:'MOONSHOT_API_KEY', hint:'Kimi 观看判断：使用 Kimi 模型和 MOONSHOT_API_KEY。'},
  'openai-compatible': {model:'', base:'http://127.0.0.1:1234/v1', env:'WATCHBRIEF_OPENAI_COMPATIBLE_API_KEY', hint:'OpenAI-compatible 观看判断：模型、endpoint、API key 环境变量必须成组填写。'},
  mock: {model:'', base:'', env:'', hint:'Mock：只用于测试，需要选择一个本地 JSON 文件路径。'}
};

const ANALYSIS_MODE_PRESETS = {
  fast: {
    timeout:'600',
    forceReanalysis:false,
    keepDebug:false,
    hint:'Fast：快速筛选，优先速度；复用已验证缓存，不额外保留调试文件。'
  },
  standard: {
    timeout:'900',
    forceReanalysis:false,
    keepDebug:false,
    hint:'Standard：默认正式报告，结构化理解后再做观看判断。'
  },
  deep: {
    timeout:'1200',
    forceReanalysis:true,
    keepDebug:true,
    hint:'Deep：重新分析、不走旧报告缓存，并保留 debug 产物，适合重要视频复核。'
  }
};

const PROFILE_DEFAULTS = {
  extract_model: ['', 'gemini-2.5-flash', 'claude-sonnet-4-20250514', 'kimi-k2.5', 'gpt-5.5'],
  extract_api_base: ['', 'http://127.0.0.1:1234/v1', 'https://generativelanguage.googleapis.com/v1beta', 'https://api.anthropic.com/v1', 'https://api.moonshot.ai/v1'],
  extract_api_key_env: ['', 'GEMINI_API_KEY', 'ANTHROPIC_API_KEY', 'MOONSHOT_API_KEY', 'WATCHBRIEF_OPENAI_COMPATIBLE_API_KEY'],
  review_model: ['', 'gemini-2.5-flash', 'claude-sonnet-4-20250514', 'kimi-k2.5', 'gpt-5.5'],
  review_api_base: ['', 'http://127.0.0.1:1234/v1', 'https://generativelanguage.googleapis.com/v1beta', 'https://api.anthropic.com/v1', 'https://api.moonshot.ai/v1'],
  review_api_key_env: ['', 'GEMINI_API_KEY', 'ANTHROPIC_API_KEY', 'MOONSHOT_API_KEY', 'WATCHBRIEF_OPENAI_COMPATIBLE_API_KEY']
};

function formPayload() {
  const form = new FormData($('#taskForm'));
  const data = Object.fromEntries(form.entries());
  data.report_targets = form.getAll('report_targets').filter(Boolean);
  if (!data.report_targets.length) data.report_targets = ['watch_decision'];
  document.querySelectorAll('#taskForm input[type="checkbox"]').forEach((box) => {
    if (box.name === 'report_targets') return;
    data[box.name] = box.checked ? (box.value || 'true') : 'false';
  });
  data.report_target = data.report_targets[0] || 'watch_decision';
  return data;
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: {'Content-Type':'application/json'}, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function shellQuote(value) {
  const text = String(value);
  if (/^[A-Za-z0-9_/:=.,@%+-]+$/.test(text)) return text;
  return "'" + text.replaceAll("'", "'\\''") + "'";
}

function setProfileValue(name, value) {
  const input = document.querySelector(`[name="${name}"]`);
  if (!input) return;
  const defaults = PROFILE_DEFAULTS[name] || [''];
  if (!input.value || defaults.includes(input.value)) {
    input.value = value || '';
  }
}

function setVisible(selector, visible) {
  document.querySelectorAll(selector).forEach((node) => node.classList.toggle('is-hidden', !visible));
}

function applyAnalysisMode(mode, {forceTimeout = false, forceStrategy = false} = {}) {
  const preset = ANALYSIS_MODE_PRESETS[mode] || ANALYSIS_MODE_PRESETS.standard;
  document.querySelectorAll('[name="analysis_mode"]').forEach((input) => {
    input.closest('.mode-card')?.classList.toggle('selected', input.value === mode);
  });
  const timeout = document.querySelector('[name="timeout"]');
  const defaultTimeouts = Object.values(ANALYSIS_MODE_PRESETS).map((item) => item.timeout);
  if (timeout && (forceTimeout || !timeout.value || defaultTimeouts.includes(timeout.value))) {
    timeout.value = preset.timeout;
  }
  const forceBox = document.querySelector('[name="force_reanalysis"]');
  const debugBox = document.querySelector('[name="keep_debug_artifacts"]');
  if (forceStrategy) {
    if (forceBox) forceBox.checked = Boolean(preset.forceReanalysis);
    if (debugBox) debugBox.checked = Boolean(preset.keepDebug);
  }
  updatePreflight(LAST_STATUS || {});
  toast(preset.hint);
}

function applyReportTargets() {
  document.querySelectorAll('[name="report_targets"]').forEach((input) => {
    input.closest('.mode-card')?.classList.toggle('selected', input.checked);
  });
  previewCommand();
}

function applyScoringProfile(profile) {
  document.querySelectorAll('[name="scoring_profile"]').forEach((input) => {
    input.closest('.mode-card')?.classList.toggle('selected', input.value === profile);
  });
  if (profile === 'custom') {
    toast('已切到自定义评分：请在高级选项里填写四项权重。');
  } else {
    toast('已切换评分规则：' + profile);
  }
  previewCommand();
}

function applyEngineProfiles() {
  const extractProvider = document.querySelector('[name="extract_provider"]')?.value || 'local-qwen';
  const reviewProvider = document.querySelector('[name="review_provider"]')?.value || 'local';
  const extractProfile = DEFAULT_EXTRACT_PROFILES[extractProvider] || {};
  const reviewProfile = DEFAULT_REVIEW_PROFILES[reviewProvider] || DEFAULT_REVIEW_PROFILES.local;
  const extractIsQwen = extractProvider === 'local-qwen';
  const extractIsExternal = !extractIsQwen;
  const needsCodex = reviewProvider === 'codex-cli' || extractProvider === 'codex-cli-extract';

  setVisible('[data-extract-panel="qwen"]', extractIsQwen);
  setVisible('[data-extract-panel="external"]', extractIsExternal);
  setVisible('[data-review-panel="codex"]', needsCodex);
  setVisible('[data-review-panel="cloud"]', ['gemini', 'claude', 'kimi', 'openai-compatible'].includes(reviewProvider));
  setVisible('[data-review-panel="mock"]', reviewProvider === 'mock');

  if (extractIsExternal) {
    setProfileValue('extract_model', extractProfile.model || '');
    setProfileValue('extract_api_base', extractProfile.base || '');
    setProfileValue('extract_api_key_env', extractProfile.env || '');
  }
  if (['gemini', 'claude', 'kimi', 'openai-compatible'].includes(reviewProvider)) {
    setProfileValue('review_model', reviewProfile.model || '');
    setProfileValue('review_api_base', reviewProfile.base || '');
    setProfileValue('review_api_key_env', reviewProfile.env || '');
  }

  $('#extractEngineHint').textContent = extractIsQwen
    ? '本地 Qwen 内容理解：先读懂字幕/转写并输出结构化素材，不生成 HTML。'
    : (extractProfile.hint || '外部内容理解：模型、endpoint 和环境变量跟随引擎。');
  $('#reviewEngineHint').textContent = reviewProfile.hint || '观看判断模型会决定模型、账号目录和环境变量。';
}

async function previewCommand() {
  try {
    const data = await api('/api/preview', {method:'POST', body:JSON.stringify(formPayload())});
    const pdfNote = data.pdf_export
      ? `\nPDF 导出：${data.pdf_engine ? data.pdf_engine : '需要 Chrome / Edge / Chromium / Brave'}`
      : '';
    const commands = Array.isArray(data.commands) && data.commands.length ? data.commands : [data.command];
    const commandLines = commands.map((command, index) => (commands.length > 1 ? `# 报告目标 ${index + 1}/${commands.length}\n` : '') + command.map(shellQuote).join(' ')).join('\n\n');
    $('#commandPreview').textContent = 'cd ' + shellQuote(data.cwd) + '\n' + commandLines + pdfNote;
  } catch (error) {
    $('#commandPreview').textContent = error.message;
  }
}

async function chooseOutputDirectory() {
  try {
    toast('正在打开系统文件夹选择框...');
    const data = await api('/api/select-directory', {method:'POST', body:JSON.stringify({})});
    if (!data.selected || !data.path) {
      toast('未选择输出目录。需要自定义时，可以继续手动输入路径。');
      return;
    }
    const outputInput = document.querySelector('[name="output_dir"]');
    const outputMode = document.querySelector('[name="output_mode"]');
    outputInput.value = data.path;
    outputMode.value = 'custom';
    updateOutputChoiceLabel();
    updatePreflight(LAST_STATUS || {});
    toast('已选择输出目录：' + data.path);
    previewCommand();
  } catch (error) {
    toast('选择目录失败：' + error.message + '\n可以继续手动输入绝对路径。');
  }
}

async function chooseUrlFile() {
  try {
    toast('正在打开系统文件选择框...');
    const data = await api('/api/select-url-file', {method:'POST', body:JSON.stringify({})});
    if (!data.selected || !data.path) {
      toast('未选择 URL 文件。单条视频只填左侧链接即可。');
      return;
    }
    setSourceFile(data.path, '已选择 txt 文件：' + data.path);
    toast('已选择批量链接文件：' + data.path);
    previewCommand();
  } catch (error) {
    toast('选择 URL 文件失败：' + error.message + '\n可以继续手动输入 txt 文件绝对路径。');
  }

}

function setSourceFile(path, labelText) {
  const sourceFile = document.querySelector('[name="source_file"]');
  const sourceUrl = document.querySelector('[name="source_url"]');
  const label = $('#sourceChoiceLabel');
  if (sourceFile) sourceFile.value = path || '';
  if (path && sourceUrl) sourceUrl.value = '';
  if (label) label.textContent = labelText || (path ? '已选择 txt 文件：' + path : '可粘贴单条链接；批量任务请拖入 txt，或选择 txt 文件。');
}

async function createSourceFileFromText(content, filename = 'dragged-links.txt') {
  const data = await api('/api/source-text-file', {method:'POST', body:JSON.stringify({content, filename})});
  setSourceFile(data.path, `已读取 ${data.line_count} 行链接：${data.path}`);
  toast(`已读取 txt 文件：${data.line_count} 行链接`);
  previewCommand();
}

function setupSourceDropZone() {
  const box = $('#sourceDropZone');
  const sourceUrl = document.querySelector('[name="source_url"]');
  if (!box || !sourceUrl) return;
  sourceUrl.addEventListener('input', () => {
    if (sourceUrl.value.trim()) setSourceFile('', '已输入链接；如需批量任务，可拖入 txt 或选择文件。');
  });
  ['dragenter','dragover'].forEach((name) => {
    box.addEventListener(name, (event) => {
      event.preventDefault();
      box.classList.add('drag-over');
    });
  });
  ['dragleave','drop'].forEach((name) => {
    box.addEventListener(name, () => box.classList.remove('drag-over'));
  });
  box.addEventListener('drop', async (event) => {
    event.preventDefault();
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.txt')) {
      toast('请拖入 txt 文件；单条链接直接粘贴到大框里。');
      return;
    }
    try {
      const content = await file.text();
      await createSourceFileFromText(content, file.name);
    } catch (error) {
      toast('读取 txt 失败：' + error.message);
    }
  });
}

function updateOutputChoiceLabel() {
  const outputMode = document.querySelector('[name="output_mode"]')?.value || 'default';
  const outputDir = document.querySelector('[name="output_dir"]')?.value || '';
  const label = $('#outputChoiceLabel');
  if (!label) return;
  if (outputMode === 'custom' && outputDir) {
    label.textContent = '自定义输出到：' + outputDir;
  } else if (outputMode === 'diagnostic') {
    label.textContent = '诊断模式：输出到临时目录，只用于排查问题。';
  } else {
    label.textContent = '默认：单视频放桌面 HTML，列表放桌面文件夹。';
  }
}

function resetOutputDefault() {
  const outputInput = document.querySelector('[name="output_dir"]');
  const outputMode = document.querySelector('[name="output_mode"]');
  if (outputInput) outputInput.value = '';
  if (outputMode) outputMode.value = 'default';
  updateOutputChoiceLabel();
  updatePreflight(LAST_STATUS || {});
  toast('已恢复默认输出位置：单视频桌面 HTML；列表桌面文件夹。');
  previewCommand();
}

function setPreflightItem(id, state, text) {
  const item = document.querySelector(id);
  if (!item) return;
  item.classList.remove('ok', 'warn', 'fail');
  item.classList.add(state);
  const span = item.querySelector('span');
  if (span) span.textContent = text;
}

function currentAnalysisMode() {
  return document.querySelector('[name="analysis_mode"]:checked')?.value || 'standard';
}

function currentReportTargets() {
  const targets = Array.from(document.querySelectorAll('[name="report_targets"]:checked')).map((input) => input.value);
  return targets.length ? targets : ['watch_decision'];
}

function updatePreflight(status = LAST_STATUS || {}) {
  const localModel = status.local_model || {};
  const review = status.review || {};
  const transcriber = status.transcriber || {};
  const apiKeys = review.api_key_envs || {};
  const cloudReady = ['gemini', 'claude', 'kimi', 'openai_compatible'].filter((key) => apiKeys[key]?.configured).map((key) => apiKeys[key].env);
  if (localModel.qwen_ok) {
    setPreflightItem('#preflightModel', 'ok', '本地 Qwen 可用，默认走本机模型。');
  } else if (localModel.ok) {
    setPreflightItem('#preflightModel', 'warn', '本地通用模型可选；非 Qwen 需在高级设置明确选择。');
  } else if (review.codex_cli_ok) {
    setPreflightItem('#preflightModel', 'ok', '无本地模型也可用：Codex CLI 已检测到。');
  } else if (cloudReady.length) {
    setPreflightItem('#preflightModel', 'ok', '无本地模型也可用：已配置 ' + cloudReady.join(', ') + '。');
  } else {
    setPreflightItem('#preflightModel', 'fail', '缺模型来源：先配置本地 Qwen、Codex CLI 或云模型环境变量。');
  }
  setPreflightItem('#preflightTranscriber', transcriber.recommended ? 'ok' : 'warn', transcriber.recommendation_reason || '默认 auto；有字幕时不需要本地转写。');
  setPreflightItem('#preflightBrowser', 'warn', '平台要求登录时读取本机 Chrome / Safari 登录态；不会展示 cookie。');
  const outputMode = document.querySelector('[name="output_mode"]')?.value || 'default';
  const outputDir = document.querySelector('[name="output_dir"]')?.value || '';
  setPreflightItem('#preflightOutput', 'ok', outputMode === 'custom' && outputDir ? '自定义输出：' + outputDir : '默认输出：单视频桌面 HTML，列表桌面文件夹。');
  const mode = currentAnalysisMode();
  setPreflightItem('#preflightMode', mode === 'deep' ? 'warn' : 'ok', mode === 'deep' ? '当前 Deep：会更慢，适合复核或重试。' : '当前 ' + mode.charAt(0).toUpperCase() + mode.slice(1) + '；Hermes 默认 Standard。');
}

function selectOption(name, value) {
  const input = document.querySelector(`[name="${name}"]`);
  if (!input) return;
  input.value = value;
  input.dispatchEvent(new Event('change', {bubbles:true}));
}

function applyNoLocalModelMode() {
  const status = LAST_STATUS || {};
  const review = status.review || {};
  const apiKeys = review.api_key_envs || {};
  const firstCloud = ['gemini', 'claude', 'kimi', 'openai_compatible'].find((key) => apiKeys[key]?.configured);
  if (review.codex_cli_ok) {
    selectOption('extract_provider', 'codex-cli-extract');
    selectOption('review_provider', 'codex-cli');
    toast('已切到无本地模型模式：Codex CLI 做内容理解和观看判断；分析模式仍保持 Standard。');
  } else if (firstCloud) {
    const provider = firstCloud === 'openai_compatible' ? 'openai-compatible' : firstCloud;
    selectOption('extract_provider', provider);
    selectOption('review_provider', provider);
    toast('已切到无本地模型模式：使用 ' + provider + ' 云模型；请确认环境变量已配置。');
  } else {
    showPanel('models');
    toast('这台电脑没有本地模型，也没检测到 Codex CLI 或云模型环境变量。请先配置 Codex 登录或 Gemini / Claude / Kimi / OpenAI-compatible API key 环境变量。');
  }
  const standardMode = document.querySelector('[name="analysis_mode"][value="standard"]');
  if (standardMode) standardMode.checked = true;
  applyAnalysisMode('standard', {forceTimeout:true, forceStrategy:true});
  applyEngineProfiles();
  previewCommand();
}

function fileUrl(path) {
  return 'file://' + String(path).split('/').map((part) => encodeURIComponent(part)).join('/');
}

function clearSourceInputs() {
  const sourceUrl = document.querySelector('[name="source_url"]');
  const sourceFile = document.querySelector('[name="source_file"]');
  const sourceFilename = $('#sourceFilename');
  if (sourceUrl) sourceUrl.value = '';
  if (sourceFile) sourceFile.value = '';
  if (sourceFilename) sourceFilename.textContent = '未选择 txt 文件';
}

function taskProgressStrip(task) {
  const status = task.status || 'unknown';
  const progress = task.progress || {};
  const percent = Math.max(0, Math.min(100, Number(progress.percent || (status === 'completed' ? 100 : 0))));
  const total = Number(progress.total || 0);
  const finished = Number(progress.finished || 0);
  const detail = total ? `${finished}/${total} 个视频已处理` : label(status);
  const activeLabel = progress.active_label || (status === 'completed' ? '报告完成' : (status === 'failed' ? '失败' : '处理中'));
  const activeTitle = progress.active_title ? `：${escapeHtml(progress.active_title)}` : '';
  const fillClass = status === 'completed' ? 'completed' : (status === 'failed' ? 'failed' : '');
  return `<div class="task-progress"><div class="task-progress-header"><span class="task-progress-title">${escapeHtml(activeLabel)}${activeTitle}</span><span>${percent}%</span></div><div class="task-progress-bar"><div class="task-progress-fill ${fillClass}" style="width:${percent}%"></div></div><div class="task-progress-meta">${escapeHtml(detail)}${total ? `；成功 ${Number(progress.completed || 0)}，失败 ${Number(progress.failed || 0)}，跳过 ${Number(progress.skipped || 0)}` : ''}</div></div>`;
}

function taskActions(task) {
  const htmlPaths = Array.isArray(task.watch_order_paths) && task.watch_order_paths.length ? task.watch_order_paths : (Array.isArray(task.html_paths) ? task.html_paths : []);
  const buttons = [];
  if (htmlPaths.length) buttons.push(`<button class="task-action primary" type="button" data-action="open-path" data-path="${escapeHtml(htmlPaths[0])}">打开报告</button>`);
  if (Array.isArray(task.pdf_paths) && task.pdf_paths.length) buttons.push(`<button class="task-action" type="button" data-action="open-path" data-path="${escapeHtml(task.pdf_paths[0])}">打开 PDF</button>`);
  if (task.log_path) buttons.push(`<button class="task-action" type="button" data-action="open-path" data-path="${escapeHtml(task.log_path)}">查看日志</button>`);
  if (Array.isArray(task.debug_paths) && task.debug_paths.length) buttons.push(`<button class="task-action" type="button" data-action="open-path" data-path="${escapeHtml(task.debug_paths[0])}">打开调试目录</button>`);
  if (task.status === 'running' || task.status === 'queued') buttons.push(`<button class="task-action danger" type="button" data-action="stop-task" data-task-id="${escapeHtml(task.id || '')}">停止任务</button>`);
  if (task.status === 'failed') buttons.push('<button class="task-action" type="button" data-action="deep-retry">切到 Deep 后重试</button>');
  return buttons.length ? `<div class="task-actions">${buttons.join('')}</div>` : '';
}

function taskRow(task) {
  const status = task.status || 'unknown';
  const cls = status === 'failed' ? 'failed' : (status === 'running' ? 'running' : '');
  const source = escapeHtml(task.source || task.title || 'WatchBrief task');
  const time = new Date((task.created_at || Date.now() / 1000) * 1000).toLocaleString();
  const pdfs = Array.isArray(task.pdf_paths) && task.pdf_paths.length
    ? `<br><small>PDF：${task.pdf_paths.map(escapeHtml).join('；')}</small>`
    : '';
  const error = task.error ? `<br><small class="task-error">${escapeHtml(task.error)}</small>` : '';
  return `<div class="task-item"><div class="task-main"><strong>${source}</strong><br><small>${time}</small>${pdfs}${error}${taskProgressStrip(task)}${taskActions(task)}</div><span class="pill ${cls}">${label(status)}</span></div>`;
}


function label(status) {
  return {running:'运行中',completed:'已完成',failed:'失败',queued:'排队中',cancelled:'已停止'}[status] || status;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

async function loadTasks() {
  try {
    const data = await api('/api/tasks');
    $('#taskList').innerHTML = data.tasks.length ? data.tasks.map(taskRow).join('') : '<div class="empty">暂无任务</div>';
    const hasRunning = data.tasks.some((task) => task.status === 'running' || task.status === 'queued');
    if (hasRunning && !TASK_REFRESH_TIMER) {
      TASK_REFRESH_TIMER = setInterval(loadTasks, 5000);
      toast('任务运行中：任务记录页会每 5 秒自动刷新。');
    } else if (!hasRunning && TASK_REFRESH_TIMER) {
      clearInterval(TASK_REFRESH_TIMER);
      TASK_REFRESH_TIMER = null;
    }
  } catch (error) {
    toast('读取任务失败：' + error.message);
  }
}

async function openPath(path) {
  if (!path) return;
  try {
    await api('/api/open-path', {method:'POST', body:JSON.stringify({path})});
    toast('已打开：' + path);
  } catch (error) {
    toast('打开失败：' + error.message);
  }
}

async function stopTask(taskId) {
  if (!taskId) return;
  try {
    await api(`/api/tasks/${encodeURIComponent(taskId)}/stop`, {method:'POST', body:JSON.stringify({})});
    toast('已发送停止任务请求。');
    await loadTasks();
  } catch (error) {
    toast('停止任务失败：' + error.message);
  }
}

async function clearTasks() {
  try {
    const data = await api('/api/tasks/clear', {method:'POST', body:JSON.stringify({})});
    $('#taskList').innerHTML = (data.tasks || []).length ? (data.tasks || []).map(taskRow).join('') : '<div class="empty">暂无任务</div>';
    const kept = data.kept_running || 0;
    const removed = data.removed || 0;
    toast(kept ? `已清空 ${removed} 条历史记录，保留 ${kept} 条正在运行/排队任务。` : `已清空 ${removed} 条任务记录。`);
  } catch (error) {
    toast('清空任务记录失败：' + error.message);
  }
}

function dismissBanner(id) {
  const panel = document.getElementById(id);
  if (panel) panel.classList.add('hidden');
}


function selectDeepRetry() {
  showPanel('run');
  const deepMode = document.querySelector('[name="analysis_mode"][value="deep"]');
  if (deepMode) deepMode.checked = true;
  applyAnalysisMode('deep', {forceTimeout:true, forceStrategy:true});
  toast('已切到 Deep：会强制重新分析并保留 debug，适合失败后复核。请确认链接后重新开始。');
}

function renderDiagnostics(data) {
  const results = $('#diagnosticResults');
  if (!results) return;
  const checks = Array.isArray(data.checks) ? data.checks : [];
  results.innerHTML = `<div class="diagnostic-row ${data.ok ? 'ok' : 'fail'}"><b>${escapeHtml(data.summary || '环境诊断完成')}</b><span>失败 ${data.fail_count || 0} 项，提醒 ${data.warn_count || 0} 项。</span></div>` + checks.map((item) => {
    const action = item.action ? `<em>${escapeHtml(item.action)}</em>` : '';
    return `<div class="diagnostic-row ${escapeHtml(item.state || 'warn')}"><b>${escapeHtml(item.name || '检查项')}</b><span>${escapeHtml(item.detail || '')}</span>${action}</div>`;
  }).join('');
}

async function runDiagnostics({jumpToDiagnostics = true} = {}) {
  try {
    if (jumpToDiagnostics) showPanel('diagnostics');
    $('#diagnosticResults').textContent = '正在诊断本机环境...';
    const data = await api('/api/diagnostics?force=1');
    renderDiagnostics(data);
    await loadStatus();
    toast(data.ok ? '环境诊断完成：可以开始正式任务。' : '环境诊断完成：请先处理红色失败项。');
  } catch (error) {
    $('#diagnosticResults').textContent = '环境诊断失败：' + error.message;
    toast('环境诊断失败：' + error.message);
  }
}

async function loadStatus() {
  try {
    const status = await api('/api/status');
    LAST_STATUS = status;
    const localModel = status.local_model || {};
    const transcriber = status.transcriber || {};
    const review = status.review || {};
    const apiKeys = review.api_key_envs || {};
    const paths = status.paths || {};
    const modelText = localModel.qwen_ok
      ? `检测到 Qwen：${(localModel.qwen_models || []).slice(0, 2).join(', ')}`
      : (localModel.ok ? `endpoint 在线，可用模型：${(localModel.models || []).slice(0, 3).join(', ') || '未列出'}` : '未检测到本地 OpenAI-compatible endpoint');
    const transcriberText = transcriber.recommendation_reason || '未完成转写工具检测';
    const cloudReady = ['gemini', 'claude', 'kimi', 'openai_compatible'].filter((key) => apiKeys[key]?.configured).map((key) => apiKeys[key].env);
    const reviewText = review.codex_cli_ok
      ? `本地模式可用；Codex CLI 可选：${review.codex_cli_path}${cloudReady.length ? '；已配置：' + cloudReady.join(', ') : ''}`
      : `本地模式可用；${cloudReady.length ? '已配置：' + cloudReady.join(', ') : '未检测到云模型环境变量'}`;
    const codexLabel = review.codex_cli_ok ? (review.codex_cli_warning ? '需升级' : '可用') : '未检测到';
    $('#systemStatus').textContent = `模型 ${(localModel.qwen_ok || localModel.ok || cloudReady.length) ? '可配置' : '未就绪'} · 转写 ${transcriber.recommended || 'auto'} · Codex ${codexLabel}`;
    $('#modelHint').textContent = modelText;
    $('#transcriberHint').textContent = transcriberText;
    $('#reviewHint').textContent = reviewText;
    $('#summaryExtract').textContent = localModel.qwen_ok ? '本地 Qwen 可用；云模型可切换' : (localModel.ok ? '本地通用模型可选；云模型可切换' : '可配置 Gemini / Claude / Kimi / OpenAI-compatible');
    $('#summaryReview').textContent = review.codex_cli_ok ? '本地规则；Codex / 云模型可切换' : '本地规则；可配置云模型环境变量';
    $('#summaryTranscriber').textContent = transcriber.recommended || 'auto';
    $('#summaryReport').textContent = status.report?.pdf?.ok ? 'HTML / PDF 可用' : 'HTML；PDF 需要 Chrome / Edge / Chromium';
    $('#outputHints').textContent = `本机 Desktop：${paths.desktop || '未检测'}；WebUI 状态目录：${paths.webui_state || '未检测'}。正式默认输出不需要填写 output_dir。`;
    updatePreflight(status);
  } catch {
    $('#systemStatus').textContent = '状态检测失败';
  }
}

function showPanel(view) {
  document.querySelectorAll('.view-panel').forEach((panel) => panel.classList.add('hidden'));
  document.querySelector(`[data-panel="${view}"]`)?.classList.remove('hidden');
  document.querySelectorAll('.nav-item').forEach((item) => {
    const isSettings = item.dataset.root === 'settings' && ['models', 'output'].includes(view);
    item.classList.toggle('active', item.dataset.view === view || isSettings);
  });
  document.querySelectorAll('.settings-tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.settingsView === view);
  });
  if (view === 'history') loadTasks();
  if (view === 'diagnostics' && $('#diagnosticResults')?.textContent.includes('还没运行')) runDiagnostics({jumpToDiagnostics:false});
}

function toggleInspector() {
  const shell = document.querySelector('.app-shell');
  const hidden = shell.classList.toggle('inspector-hidden');
  const button = $('#toggleInspector');
  const labelText = hidden ? '显示当前配置' : '隐藏当前配置';
  button.setAttribute('aria-label', labelText);
  button.setAttribute('title', labelText);
  button.setAttribute('aria-pressed', hidden ? 'true' : 'false');
}

document.querySelectorAll('.nav-item').forEach((button) => {
  button.addEventListener('click', () => showPanel(button.dataset.view));
});

document.querySelectorAll('[data-jump]').forEach((button) => {
  button.addEventListener('click', () => showPanel(button.dataset.jump));
});

document.querySelectorAll('.settings-tab').forEach((button) => {
  button.addEventListener('click', () => showPanel(button.dataset.settingsView));
});

$('#toggleInspector').addEventListener('click', toggleInspector);
$('#previewCommand').addEventListener('click', previewCommand);
$('#refreshTasks').addEventListener('click', loadTasks);
$('#clearTasks').addEventListener('click', clearTasks);
$('#runDiagnostics').addEventListener('click', () => runDiagnostics({jumpToDiagnostics:false}));
$('#runDiagnosticsHero').addEventListener('click', () => runDiagnostics({jumpToDiagnostics:false}));
$('#historyDiagnostics').addEventListener('click', () => runDiagnostics({jumpToDiagnostics:true}));
$('#historyDeepRetry').addEventListener('click', selectDeepRetry);
document.querySelectorAll('[data-dismiss]').forEach((button) => {
  button.addEventListener('click', () => dismissBanner(button.dataset.dismiss));
});
$('#taskList').addEventListener('click', (event) => {
  const openTarget = event.target.closest('[data-action="open-path"]');
  if (openTarget) {
    openPath(openTarget.dataset.path);
    return;
  }
  const stopTarget = event.target.closest('[data-action="stop-task"]');
  if (stopTarget) {
    stopTask(stopTarget.dataset.taskId);
    return;
  }
  const target = event.target.closest('[data-action="deep-retry"]');
  if (target) selectDeepRetry();
});
$('#chooseOutputDir').addEventListener('click', chooseOutputDirectory);
$('#chooseOutputDirMain').addEventListener('click', chooseOutputDirectory);
$('#resetOutputDefault').addEventListener('click', resetOutputDefault);
$('#chooseUrlFile').addEventListener('click', chooseUrlFile);
setupSourceDropZone();
$('#taskForm').addEventListener('input', () => {
  applyEngineProfiles();
  updateOutputChoiceLabel();
  previewCommand();
});
document.querySelectorAll('[name="analysis_mode"]').forEach((radio) => {
  radio.addEventListener('change', () => {
    if (radio.checked) applyAnalysisMode(radio.value, {forceTimeout:true, forceStrategy:true});
    previewCommand();
  });
});
document.querySelectorAll('[name="report_targets"]').forEach((box) => {
  box.addEventListener('change', () => {
    if (!currentReportTargets().length) box.checked = true;
    applyReportTargets();
  });
});
document.querySelectorAll('[name="scoring_profile"]').forEach((radio) => {
  radio.addEventListener('change', () => {
    if (radio.checked) applyScoringProfile(radio.value);
  });
});

document.querySelectorAll('[name="extract_provider"], [name="review_provider"]').forEach((select) => {
  select.addEventListener('change', () => {
    applyEngineProfiles();
    previewCommand();
  });
});
$('#taskForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = await api('/api/tasks', {method:'POST', body:JSON.stringify(formPayload())});
    clearSourceInputs();
    previewCommand();
    showPanel('history');
    const count = Array.isArray(data.tasks) ? data.tasks.length : 1;
    toast(`任务已启动：${count} 个报告目标\n已打开任务记录页，可查看处理进度与报告按钮。\n日志：${data.task.log_path}`);
    loadTasks();
  } catch (error) {
    toast('提交失败：' + error.message);
  }
});

applyAnalysisMode(document.querySelector('[name="analysis_mode"]:checked')?.value || 'standard');
applyReportTargets();
applyScoringProfile(document.querySelector('[name="scoring_profile"]:checked')?.value || 'standard');
applyEngineProfiles();
updateOutputChoiceLabel();
loadStatus();
loadTasks();
loadReleaseReadiness();
previewCommand();
setInterval(loadTasks, 5000);
"""


class WatchBriefWebHandler(BaseHTTPRequestHandler):
    server_version = "WatchBriefWebUI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[webui] " + fmt % args + "\n")

    def _send(self, body: bytes, *, status: HTTPStatus = HTTPStatus.OK, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"), status=status, content_type="application/json; charset=utf-8")

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self._send(render_index_html().encode("utf-8"))
        elif path == "/styles.css":
            self._send(STYLES_CSS.encode("utf-8"), content_type="text/css; charset=utf-8")
        elif path == "/app.js":
            self._send(APP_JS.encode("utf-8"), content_type="application/javascript; charset=utf-8")
        elif path == "/api/status":
            self._json(service_status())
        elif path == "/api/diagnostics":
            query = urlparse(self.path).query
            self._json(environment_diagnostics(force="force=1" in query or "force=true" in query))
        elif path == "/api/tasks":
            self._json({"tasks": tasks_for_api()})
        elif path == "/api/release-readiness":
            self._json(release_readiness_status())
        else:
            self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        stop_match = re.fullmatch(r"/api/tasks/([A-Za-z0-9_-]+)/stop", path)
        if path not in {"/api/tasks", "/api/tasks/clear", "/api/preview", "/api/select-directory", "/api/select-url-file", "/api/source-text-file", "/api/open-path"} and not stop_match:
            self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            if stop_match:
                task = stop_task(stop_match.group(1))
                self._json({"task": task})
                return
            if path == "/api/tasks/clear":
                self._json(clear_tasks())
                return
            if path == "/api/select-directory":
                selected_path = choose_output_directory()
                self._json({"selected": bool(selected_path), "path": selected_path})
                return
            if path == "/api/select-url-file":
                selected_path = choose_url_file()
                self._json({"selected": bool(selected_path), "path": selected_path})
                return
            if path == "/api/source-text-file":
                self._json(create_source_text_file(self._read_json_body()))
                return
            if path == "/api/open-path":
                self._json(open_path_from_payload(self._read_json_body()))
                return
            payload = self._read_json_body()
            if path == "/api/preview":
                self._json(command_preview(payload))
                return
            tasks = start_tasks(payload)
            task = tasks[0]
        except Exception as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json({"task": task, "tasks": tasks}, status=HTTPStatus.CREATED)


def open_path_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_path = _clean_text(payload.get("path"))
    if not raw_path:
        raise ValueError("缺少要打开的路径")
    path = Path(raw_path).expanduser()
    if not path.exists():
        raise ValueError(f"路径不存在：{path}")
    open_local_paths([path])
    return {"opened": str(path)}


def create_source_text_file(payload: dict[str, Any]) -> dict[str, Any]:
    content = _clean_text(payload.get("content"))
    filename = _clean_text(payload.get("filename")) or "links.txt"
    if not content:
        raise ValueError("拖入的 txt 文件内容为空")
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        raise ValueError("txt 文件里没有可用链接")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).name).strip(".-") or "links.txt"
    if not safe_name.lower().endswith(".txt"):
        safe_name += ".txt"
    SOURCE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = SOURCE_UPLOAD_DIR / f"{int(time.time())}-{uuid.uuid4().hex[:8]}-{safe_name}"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(path), "line_count": len(lines)}


def summarize_task_failure(log_path: Path, exit_code: int) -> str:
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    lowered = log_text.lower()
    if "transcriber_unavailable" in lowered or "mlx-audio" in lowered and "unavailable" in lowered:
        return "转写工具不可用：请安装/配置 MLX-Audio，或在设置里选择 Whisper 并勾选允许回退。"
    if "local_qwen_timeout" in lowered or "qwen request timed out" in lowered:
        return "本地 Qwen 超时：请确认 LM Studio 模型已加载，或切到 Deep/增大 timeout 后重试。"
    if "connection refused" in lowered or "127.0.0.1:1234" in lowered and "refused" in lowered:
        return "本地模型 endpoint 没连上：请先启动 LM Studio 的 127.0.0.1:1234/v1 服务。"
    if "login_required" in lowered or "cookies" in lowered and "0 cookies" in lowered:
        return "平台登录态不可用：请先在浏览器登录目标平台，或在设置里切换 Chrome/Safari 登录态。"
    if "transcript_coverage_too_low" in lowered:
        return "字幕/转写覆盖率太低：WatchBrief 已停止生成正式报告，避免产出不完整结论。"
    tail = "\n".join(log_text.strip().splitlines()[-3:]).strip()
    return f"任务失败，退出码 {exit_code}。" + (f" 最近日志：{tail[:240]}" if tail else " 请打开日志查看原因。")


def extract_delivery_paths(log_path: Path) -> dict[str, list[str]]:
    """Extract user-facing output paths from a WebUI task log."""
    if not log_path.exists():
        return {"html_paths": [], "watch_order_paths": [], "debug_paths": []}
    html_paths: list[str] = []
    watch_order_paths: list[str] = []
    debug_paths: list[str] = []
    for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        for prefix, bucket in (
            ("HTML:", html_paths),
            ("Diagnostic HTML:", html_paths),
            ("Watch Order:", watch_order_paths),
            ("debug_artifacts:", debug_paths),
            ("diagnostic_artifacts:", debug_paths),
        ):
            if line.startswith(prefix):
                value = line[len(prefix):].strip()
                if value and value not in bucket:
                    bucket.append(value)
        if "[completed]" in line and " -> " in line:
            value = line.rsplit(" -> ", 1)[-1].strip()
            if value.endswith(".html") and value not in html_paths:
                html_paths.append(value)
    return {"html_paths": html_paths, "watch_order_paths": watch_order_paths, "debug_paths": debug_paths}


def start_tasks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for target in parse_report_targets(payload):
        target_payload = dict(payload)
        target_payload["report_target"] = target
        target_payload["report_targets"] = [target]
        command = build_cli_command(target_payload, report_target_override=target)
        tasks.append(start_task(target_payload, command))
    return tasks


def start_task(payload: dict[str, Any], command: list[str]) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    task_id = uuid.uuid4().hex[:12]
    log_path = LOG_DIR / f"{task_id}.log"
    source = _clean_text(payload.get("source_url")) or _clean_text(payload.get("source_file"))
    task = {
        "id": task_id,
        "title": source,
        "source": source,
        "status": "running",
        "created_at": time.time(),
        "updated_at": time.time(),
        "log_path": str(log_path),
        "report_format": _clean_text(payload.get("report_format")) or "html",
        "report_target": _clean_text(payload.get("report_target")) or DEFAULT_REPORT_TARGET,
        "report_target_label": REPORT_TARGET_LABELS.get(_clean_text(payload.get("report_target")) or DEFAULT_REPORT_TARGET, _clean_text(payload.get("report_target")) or DEFAULT_REPORT_TARGET),
        "pid": None,
    }
    _write_tasks([task, *_read_tasks()])

    def runner() -> None:
        with log_path.open("wb") as log:
            process = subprocess.Popen(command, cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=(os.name != "nt"))
            _update_task(task_id, pid=process.pid, updated_at=time.time())
            code = process.wait()
        current = _get_task(task_id) or {}
        if current.get("status") == "cancelled":
            return
        if code == 0 and should_export_pdf(payload):
            try:
                pdf_paths = export_log_htmls_to_pdf(log_path)
            except Exception as exc:
                with log_path.open("ab") as log:
                    log.write(f"\npdf_export_failed: {exc}\n".encode("utf-8"))
                _update_task(task_id, status="failed", exit_code=3, error=str(exc), updated_at=time.time())
                return
            with log_path.open("ab") as log:
                for pdf_path in pdf_paths:
                    log.write(f"PDF: {pdf_path}\n".encode("utf-8"))
            if _truthy(payload.get("open_output")):
                open_local_paths(pdf_paths)
            _update_task(
                task_id,
                status="completed",
                exit_code=0,
                pdf_paths=[str(path) for path in pdf_paths],
                **extract_delivery_paths(log_path),
                updated_at=time.time(),
            )
            return
        if code == 0:
            _update_task(task_id, status="completed", exit_code=0, **extract_delivery_paths(log_path), updated_at=time.time())
        else:
            _update_task(
                task_id,
                status="failed",
                exit_code=code,
                error=summarize_task_failure(log_path, code),
                updated_at=time.time(),
            )

    threading.Thread(target=runner, daemon=True).start()
    return task


def run_server(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), WatchBriefWebHandler)
    print(f"WatchBrief WebUI: http://{host}:{port}")
    server.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run WatchBrief V5 local WebUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
