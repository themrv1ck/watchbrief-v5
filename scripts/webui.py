#!/usr/bin/env python3
"""Local WebUI for WatchBrief V5.

The server is standard-library only. It builds safe CLI commands and keeps the
pipeline contract in scripts/cli.py as the single execution path.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import socket
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = PROJECT_ROOT / "scripts" / "cli.py"
STATE_DIR = Path.home() / ".watchbrief" / "webui"
TASKS_PATH = STATE_DIR / "tasks.json"
LOG_DIR = STATE_DIR / "logs"

DEFAULT_QWEN_MODEL = "qwen3-30b-a3b-instruct-2507-mlx"
DEFAULT_QWEN_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_CODEX_MODEL = "gpt-5.4"
DEFAULT_CODEX_ACCOUNT = "account2"
DEFAULT_CODEX_HOME_ROOT = "~/.watchbrief_codex"

SUPPORTED_REVIEW_PROVIDERS = {"codex-cli", "mock"}
UNSUPPORTED_REVIEW_PROVIDERS = {"claude", "kimi", "manual"}
SUPPORTED_REPORT_FORMATS = {"html"}
SUPPORTED_RENDERERS = {"local-html"}
SUPPORTED_BROWSER_AUTH = {"auto", "chrome", "safari", "edge", "none"}
SUPPORTED_TRANSCRIBERS = {"auto", "mlx_audio", "whisper"}


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


def _review_provider(payload: dict[str, Any]) -> str:
    provider = _clean_text(payload.get("review_provider") or payload.get("account_provider")) or "codex-cli"
    if provider in UNSUPPORTED_REVIEW_PROVIDERS:
        raise ValueError(f"{provider} 评审适配器尚未接入；当前 WebUI 可运行 codex-cli 或 mock")
    if provider not in SUPPORTED_REVIEW_PROVIDERS:
        raise ValueError("review_provider 只支持 codex-cli 或 mock")
    return provider


def build_cli_command(payload: dict[str, Any]) -> list[str]:
    """Build a WatchBrief CLI command from WebUI form payload."""
    source_url = _clean_text(payload.get("source_url"))
    source_file = _clean_text(payload.get("source_file"))
    if bool(source_url) == bool(source_file):
        raise ValueError("必须填写一个视频/列表链接，或填写一个本地 URL 文件路径")

    report_format = _clean_text(payload.get("report_format")) or "html"
    if report_format not in SUPPORTED_REPORT_FORMATS:
        raise ValueError("当前 CLI 只生成 HTML；PDF 需要后续接入 HTML-to-PDF 后端")
    renderer = _clean_text(payload.get("renderer")) or "local-html"
    if renderer not in SUPPORTED_RENDERERS:
        raise ValueError("当前只支持本地 HTML renderer；其他 renderer 尚未接入")

    command = [python_executable(), str(CLI_PATH)]
    if source_file:
        command.extend(["--source-file", source_file])
    else:
        command.extend(["--source-url", source_url])

    output_dir = _clean_text(payload.get("output_dir"))
    if output_dir:
        command.extend(["--output-dir", output_dir])

    provider = _review_provider(payload)
    command.extend(["--review-provider", provider])
    if provider == "codex-cli":
        command.append("--enable-codex-review")
        codex_model = _clean_text(payload.get("codex_model")) or DEFAULT_CODEX_MODEL
        command.extend(["--codex-model", codex_model])
        codex_home_root = _clean_text(payload.get("codex_home_root")) or DEFAULT_CODEX_HOME_ROOT
        if codex_home_root:
            command.extend(["--codex-home-root", codex_home_root])
        codex_account = _clean_text(payload.get("codex_account")) or DEFAULT_CODEX_ACCOUNT
        if codex_account:
            command.extend(["--codex-account", codex_account])
    elif provider == "mock":
        mock_response = _clean_text(payload.get("mock_review_response"))
        if not mock_response:
            raise ValueError("mock review 需要填写 mock_review_response JSON 文件路径")
        command.extend(["--mock-review-response", mock_response])

    qwen_model = _clean_text(payload.get("qwen_model")) or DEFAULT_QWEN_MODEL
    _validate_qwen_model(qwen_model)
    command.extend(["--qwen-model", qwen_model])

    qwen_api_base = _clean_text(payload.get("qwen_api_base"))
    if qwen_api_base:
        command.extend(["--qwen-api-base", qwen_api_base])

    timeout = _positive_int(payload.get("timeout"), "timeout")
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

    transcriber = _clean_text(payload.get("transcriber")) or "auto"
    if transcriber not in SUPPORTED_TRANSCRIBERS:
        raise ValueError("转写器只支持 auto/mlx_audio/whisper")
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

    if _truthy(payload.get("force_reanalysis")):
        command.append("--force-reanalysis")
    if _truthy(payload.get("keep_debug_artifacts")):
        command.append("--keep-debug-artifacts")
    if _truthy(payload.get("diagnostic_run")):
        command.append("--diagnostic-run")
    if _truthy(payload.get("open_output")):
        command.append("--open-output")

    return command


def command_preview(payload: dict[str, Any]) -> dict[str, Any]:
    command = build_cli_command(payload)
    return {"command": command, "cwd": str(PROJECT_ROOT)}


def _read_tasks() -> list[dict[str, Any]]:
    if not TASKS_PATH.exists():
        return []
    try:
        data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _write_tasks(tasks: list[dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_PATH.write_text(json.dumps(tasks[:100], ensure_ascii=False, indent=2), encoding="utf-8")


def _update_task(task_id: str, **updates: Any) -> None:
    tasks = _read_tasks()
    for task in tasks:
        if task.get("id") == task_id:
            task.update(updates)
            break
    _write_tasks(tasks)


def service_status() -> dict[str, Any]:
    qwen_ok = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        qwen_ok = sock.connect_ex(("127.0.0.1", 1234)) == 0
    return {
        "ok": True,
        "qwen": {"ok": qwen_ok, "label": "OpenAI-compatible endpoint 127.0.0.1:1234"},
        "codex": {"ok": shutil.which("codex") is not None, "label": "Codex CLI"},
        "project_root": str(PROJECT_ROOT),
        "time": int(time.time()),
    }


def _option(value: str, label: str, *, selected: bool = False, disabled: bool = False) -> str:
    attrs = []
    if selected:
        attrs.append("selected")
    if disabled:
        attrs.append("disabled")
    return f'<option value="{html.escape(value)}" {" ".join(attrs)}>{html.escape(label)}</option>'


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
      <div class="brand"><span class="brand-mark">WB</span><strong>WatchBrief</strong></div>
      <nav>
        <button class="nav-item active" data-view="run">新建任务</button>
        <button class="nav-item" data-view="models">模型与账号</button>
        <button class="nav-item" data-view="output">输出与登录态</button>
        <button class="nav-item" data-view="history">任务记录</button>
      </nav>
      <div class="status-box"><span class="status-dot"></span><small id="systemStatus">检测本机服务中</small></div>
    </aside>
    <main class="workspace">
      <form id="taskForm">
        <section class="view-panel" data-panel="run">
          <header class="page-head">
            <h1>新建观看决策任务</h1>
            <button class="primary" type="submit">开始运行</button>
          </header>
          <div class="input-row">
            <label class="field wide">视频 / 列表 / board 链接<input name="source_url" placeholder="https://..." autocomplete="off" /></label>
            <span class="or-label">或</span>
            <label class="field">URL 文件路径<input name="source_file" placeholder="/Users/apple/Desktop/urls.txt" /></label>
          </div>
          <div class="grid two">
            <label class="field">运行模式<select name="diagnostic_run"><option value="">正式任务</option><option value="true">诊断 / 复现</option></select></label>
            <label class="field">任务超时<select name="timeout">{_option("600", "600 秒", selected=True)}{_option("900", "900 秒")}{_option("1200", "1200 秒")}</select></label>
          </div>
          <div class="switch-grid">
            <label><input type="checkbox" name="force_reanalysis" value="true" /> 强制重新分析</label>
            <label><input type="checkbox" name="keep_debug_artifacts" value="true" /> 保留 debug artifacts</label>
            <label><input type="checkbox" name="open_output" value="true" /> 完成后打开输出</label>
          </div>
          <section class="preview-block">
            <div class="section-title"><h2>命令预览</h2><button type="button" id="previewCommand">刷新预览</button></div>
            <pre id="commandPreview">未生成</pre>
          </section>
        </section>

        <section class="view-panel hidden" data-panel="models">
          <header class="page-head"><h1>模型与账号</h1><button class="primary" type="submit">开始运行</button></header>
          <div class="grid two">
            <label class="field">本地 Qwen 模型<input name="qwen_model" value="{DEFAULT_QWEN_MODEL}" /></label>
            <label class="field">Qwen endpoint<input name="qwen_api_base" placeholder="{DEFAULT_QWEN_BASE_URL}" /></label>
            <label class="field">Qwen timeout<input name="qwen_timeout" placeholder="默认跟随任务超时" /></label>
            <label class="field">转写器<select name="transcriber">{_option("auto", "auto：MLX-Audio", selected=True)}{_option("mlx_audio", "MLX-Audio")}{_option("whisper", "Whisper")}</select></label>
            <label class="field">MLX-Audio 模型<input name="mlx_model" placeholder="默认 mlx-community/whisper-large-v3-turbo" /></label>
            <label class="field">Whisper 模型<input name="whisper_model" placeholder="base" /></label>
          </div>
          <label class="inline-check"><input type="checkbox" name="allow_whisper_fallback" value="true" /> 允许 MLX-Audio 不可用时回退 Whisper</label>
          <div class="grid two">
            <label class="field">Review 引擎<select name="review_provider">
              {_option("codex-cli", "Codex CLI", selected=True)}
              {_option("mock", "Mock JSON")}
              {_option("claude", "Claude：未接入", disabled=True)}
              {_option("kimi", "Kimi：未接入", disabled=True)}
            </select></label>
            <label class="field">Codex 模型<input name="codex_model" value="{DEFAULT_CODEX_MODEL}" /></label>
            <label class="field">Codex 账号目录<input name="codex_home_root" value="{DEFAULT_CODEX_HOME_ROOT}" /></label>
            <label class="field">Codex 账号<input name="codex_account" value="{DEFAULT_CODEX_ACCOUNT}" /></label>
            <label class="field span-two">Mock response JSON<input name="mock_review_response" placeholder="/path/to/sample_payload.json" /></label>
          </div>
          <div class="notice">WebUI 不接收 API token。Claude / Kimi 需要后端 adapter 后再开放。</div>
        </section>

        <section class="view-panel hidden" data-panel="output">
          <header class="page-head"><h1>输出与登录态</h1><button class="primary" type="submit">开始运行</button></header>
          <div class="grid two">
            <label class="field">输出目录<input name="output_dir" placeholder="留空：使用正式默认输出路径" /></label>
            <label class="field">报告格式<select name="report_format">{_option("html", "HTML", selected=True)}{_option("pdf", "PDF：未接入", disabled=True)}</select></label>
            <label class="field">渲染方式<select name="renderer">{_option("local-html", "本地 HTML renderer", selected=True)}{_option("pdf-export", "PDF export：未接入", disabled=True)}</select></label>
            <label class="field">登录态<select name="browser_auth">{_option("auto", "自动：Chrome → Safari", selected=True)}{_option("chrome", "Chrome")}{_option("safari", "Safari")}{_option("edge", "Edge")}{_option("none", "不使用登录态")}</select></label>
            <label class="field span-two">cookies.txt 路径<input name="cookies_file" placeholder="可选；只传路径，不读取或展示内容" /></label>
          </div>
          <div class="rule-strip">
            <span>单视频默认输出桌面 HTML</span>
            <span>列表默认输出桌面任务文件夹</span>
            <span>默认不打开浏览器</span>
          </div>
        </section>
      </form>

      <section class="view-panel hidden" data-panel="history">
        <header class="page-head"><h1>任务记录</h1><button type="button" id="refreshTasks">刷新</button></header>
        <div id="taskList" class="task-list"><div class="empty">暂无任务</div></div>
      </section>
    </main>
    <aside class="inspector">
      <h2>当前配置</h2>
      <dl id="summary">
        <dt>源项目</dt><dd>{html.escape(str(PROJECT_ROOT))}</dd>
        <dt>默认 Qwen</dt><dd>{DEFAULT_QWEN_MODEL}</dd>
        <dt>默认 review</dt><dd>Codex CLI / {DEFAULT_CODEX_MODEL}</dd>
        <dt>输出格式</dt><dd>HTML</dd>
      </dl>
      <pre id="toast"></pre>
    </aside>
  </div>
  <script src="/app.js"></script>
</body>
</html>"""


STYLES_CSS = r"""
:root{--bg:#f5f6f8;--panel:#ffffff;--ink:#20242c;--muted:#626a78;--line:#dfe3ea;--blue:#2563eb;--green:#11845b;--red:#c2413f;--soft:#eef2f7}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;color:var(--ink)}.app-shell{min-height:100vh;display:grid;grid-template-columns:220px minmax(0,1fr)300px}.sidebar{border-right:1px solid var(--line);background:#fff;padding:18px;display:flex;flex-direction:column;gap:20px}.brand{height:40px;display:flex;align-items:center;gap:10px}.brand-mark{width:34px;height:34px;border-radius:8px;background:var(--ink);color:#fff;display:grid;place-items:center;font-weight:800}.nav-item{width:100%;height:38px;border:0;background:transparent;border-radius:8px;text-align:left;padding:0 10px;font:inherit;color:var(--muted);cursor:pointer}.nav-item.active{background:var(--soft);color:var(--ink);font-weight:700}.status-box{margin-top:auto;border:1px solid var(--line);border-radius:8px;padding:12px;display:flex;gap:8px;align-items:center}.status-dot{width:8px;height:8px;border-radius:50%;background:var(--green)}.workspace{padding:22px;overflow:auto}.inspector{border-left:1px solid var(--line);background:#fff;padding:20px;overflow:auto}.page-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}.page-head h1{margin:0;font-size:24px;letter-spacing:0}.grid{display:grid;gap:14px}.grid.two{grid-template-columns:repeat(2,minmax(0,1fr))}.input-row{display:grid;grid-template-columns:minmax(0,1.4fr)42px minmax(0,1fr);gap:12px;align-items:end}.or-label{height:40px;display:grid;place-items:center;color:var(--muted)}.field{display:grid;gap:7px;font-size:13px;color:var(--muted);font-weight:700}.field input,.field select{width:100%;height:40px;border:1px solid var(--line);border-radius:8px;background:#fff;padding:0 10px;color:var(--ink);font:inherit;font-weight:500}.field.wide{min-width:0}.span-two{grid-column:span 2}.primary,#refreshTasks,#previewCommand{height:38px;border:0;border-radius:8px;background:var(--blue);color:#fff;font-weight:700;padding:0 14px;cursor:pointer}#refreshTasks,#previewCommand{background:#1f2937}.view-panel{max-width:980px}.hidden{display:none}.switch-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:14px 0}.switch-grid label,.inline-check{height:38px;border:1px solid var(--line);border-radius:8px;background:#fff;display:flex;align-items:center;gap:8px;padding:0 10px;color:var(--ink);font-size:13px}.preview-block,.task-list{margin-top:18px;border:1px solid var(--line);border-radius:8px;background:#fff}.section-title{height:44px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 12px}.section-title h2{font-size:15px;margin:0}pre{white-space:pre-wrap;overflow:auto}#commandPreview{min-height:92px;margin:0;padding:12px;color:#263244;background:#fbfcfe}.notice{margin-top:14px;border:1px solid #f0d8a8;background:#fff8eb;color:#76520e;border-radius:8px;padding:12px}.rule-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:14px}.rule-strip span{border:1px solid var(--line);border-radius:8px;padding:12px;background:#fff;color:var(--muted);font-size:13px}.task-item{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center;padding:12px;border-bottom:1px solid var(--line)}.task-item:last-child{border-bottom:0}.pill{border-radius:999px;padding:4px 8px;font-size:12px;background:#e8f7ef;color:var(--green);font-weight:700}.pill.failed{background:#ffeceb;color:var(--red)}.pill.running{background:#eaf0ff;color:var(--blue)}.empty{padding:18px;color:var(--muted);text-align:center}dl{margin:0;display:grid;gap:12px}dt{font-size:12px;color:var(--muted);font-weight:700}dd{margin:0;font-size:13px;line-height:1.45;overflow-wrap:anywhere}#toast{min-height:96px;margin-top:18px;border-radius:8px;background:#111827;color:#e5e7eb;padding:12px;font-size:12px}@media(max-width:1050px){.app-shell{grid-template-columns:1fr}.sidebar,.inspector{border:0}.grid.two,.input-row,.switch-grid,.rule-strip{grid-template-columns:1fr}.span-two{grid-column:auto}}
"""


APP_JS = r"""
const $ = (s) => document.querySelector(s);
const toast = (msg) => { $('#toast').textContent = msg; };

function formPayload() {
  const data = Object.fromEntries(new FormData($('#taskForm')).entries());
  document.querySelectorAll('#taskForm input[type="checkbox"]').forEach((box) => {
    if (!box.checked) delete data[box.name];
  });
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

async function previewCommand() {
  try {
    const data = await api('/api/preview', {method:'POST', body:JSON.stringify(formPayload())});
    $('#commandPreview').textContent = 'cd ' + shellQuote(data.cwd) + '\n' + data.command.map(shellQuote).join(' ');
  } catch (error) {
    $('#commandPreview').textContent = error.message;
  }
}

function taskRow(task) {
  const status = task.status || 'unknown';
  const cls = status === 'failed' ? 'failed' : (status === 'running' ? 'running' : '');
  const source = escapeHtml(task.source || task.title || 'WatchBrief task');
  const time = new Date((task.created_at || Date.now() / 1000) * 1000).toLocaleString();
  return `<div class="task-item"><div><strong>${source}</strong><br><small>${time}</small></div><span class="pill ${cls}">${label(status)}</span></div>`;
}

function label(status) {
  return {running:'运行中',completed:'已完成',failed:'失败',queued:'排队中'}[status] || status;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

async function loadTasks() {
  try {
    const data = await api('/api/tasks');
    $('#taskList').innerHTML = data.tasks.length ? data.tasks.map(taskRow).join('') : '<div class="empty">暂无任务</div>';
  } catch (error) {
    toast('读取任务失败：' + error.message);
  }
}

async function loadStatus() {
  try {
    const status = await api('/api/status');
    $('#systemStatus').textContent = `Qwen ${status.qwen.ok ? '在线' : '未检测到'} · Codex ${status.codex.ok ? '可用' : '未检测到'}`;
  } catch {
    $('#systemStatus').textContent = '状态检测失败';
  }
}

document.querySelectorAll('.nav-item').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach((item) => item.classList.remove('active'));
    document.querySelectorAll('.view-panel').forEach((panel) => panel.classList.add('hidden'));
    button.classList.add('active');
    document.querySelector(`[data-panel="${button.dataset.view}"]`)?.classList.remove('hidden');
    if (button.dataset.view === 'history') loadTasks();
  });
});

$('#previewCommand').addEventListener('click', previewCommand);
$('#refreshTasks').addEventListener('click', loadTasks);
$('#taskForm').addEventListener('input', previewCommand);
$('#taskForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = await api('/api/tasks', {method:'POST', body:JSON.stringify(formPayload())});
    toast(`任务已启动：${data.task.id}\n日志：${data.task.log_path}`);
    loadTasks();
  } catch (error) {
    toast('提交失败：' + error.message);
  }
});

loadStatus();
loadTasks();
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
        elif path == "/api/tasks":
            self._json({"tasks": _read_tasks()[:20]})
        else:
            self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/api/tasks", "/api/preview"}:
            self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_body()
            command = build_cli_command(payload)
            if path == "/api/preview":
                self._json({"command": command, "cwd": str(PROJECT_ROOT)})
                return
            task = start_task(payload, command)
        except Exception as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json({"task": task}, status=HTTPStatus.CREATED)


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
    }
    _write_tasks([task, *_read_tasks()])

    def runner() -> None:
        with log_path.open("wb") as log:
            process = subprocess.Popen(command, cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT)
            code = process.wait()
        _update_task(task_id, status="completed" if code == 0 else "failed", exit_code=code, updated_at=time.time())

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
