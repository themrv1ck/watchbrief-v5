from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_WATCHBRIEF_CODEX_HOME_ROOT = "~/.watchbrief_codex"
DEFAULT_WATCHBRIEF_CODEX_ACCOUNT = "account2"
DEFAULT_WATCHBRIEF_PROJECT_ROOT = "/Users/apple/Documents/New project/watchbrief_v5"


def real_user_home() -> Path:
    home = Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    user = os.environ.get("USER") or os.environ.get("LOGNAME")
    if ".hermes/profiles" in str(home) and user:
        candidate = Path("/Users") / user
        if candidate.exists():
            return candidate
    return home


SMALLCOLA_STATUS_PATH = real_user_home() / ".codex-accounts" / "watchbrief-status.json"


def watchbrief_codex_root(value: str | None = None) -> Path:
    raw = value or os.environ.get("WATCHBRIEF_CODEX_HOME_ROOT") or DEFAULT_WATCHBRIEF_CODEX_HOME_ROOT
    if raw.startswith("~/") or raw == "~":
        return real_user_home() / raw.removeprefix("~/") if raw != "~" else real_user_home()
    return Path(raw).expanduser()


def current_file(root: Path | None = None) -> Path:
    return (root or watchbrief_codex_root()) / "current"


def list_watchbrief_codex_accounts(root: Path | None = None) -> list[str]:
    root = root or watchbrief_codex_root()
    if not root.exists():
        return []
    accounts: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in {"tmp", "cache", "logs"}:
            continue
        if (child / "auth.json").exists() or (child / "config.toml").exists() or (child / "state_5.sqlite").exists():
            accounts.append(child.name)
    return accounts


def read_current_watchbrief_codex_account(root: Path | None = None) -> str:
    root = root or watchbrief_codex_root()
    accounts = list_watchbrief_codex_accounts(root)
    env_account = os.environ.get("WATCHBRIEF_CODEX_ACCOUNT", "").strip()
    if env_account:
        return env_account
    current = current_file(root)
    if current.exists():
        value = current.read_text(encoding="utf-8").strip()
        if value and value in accounts:
            return value
    if DEFAULT_WATCHBRIEF_CODEX_ACCOUNT in accounts:
        return DEFAULT_WATCHBRIEF_CODEX_ACCOUNT
    return accounts[0] if accounts else DEFAULT_WATCHBRIEF_CODEX_ACCOUNT


def watchbrief_codex_home(root: Path | None = None, account: str | None = None) -> Path:
    root = root or watchbrief_codex_root()
    account = account or read_current_watchbrief_codex_account(root)
    return root / account


def _stat(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    st = path.stat()
    return {"path": str(path), "size": st.st_size, "mtime": int(st.st_mtime)}


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob("*.jsonl"))


def collect_watchbrief_codex_status(*, root: Path | None = None, probe: bool = False, timeout: int = 20) -> dict[str, Any]:
    root = root or watchbrief_codex_root()
    account = read_current_watchbrief_codex_account(root)
    accounts = list_watchbrief_codex_accounts(root)
    home = watchbrief_codex_home(root, account)
    status: dict[str, Any] = {
        "kind": "watchbrief_codex_status",
        "updated_at": int(time.time()),
        "root": str(root),
        "current_account": account,
        "accounts": accounts,
        "home": str(home),
        "home_exists": home.exists(),
        "auth_exists": (home / "auth.json").exists(),
        "config_exists": (home / "config.toml").exists(),
        "state_exists": (home / "state_5.sqlite").exists(),
        "logs_exists": (home / "logs_2.sqlite").exists(),
        "sessions_count": _count_jsonl(home / "sessions"),
        "archived_sessions_count": _count_jsonl(home / "archived_sessions"),
        "files": {
            name: _stat(home / name)
            for name in ["auth.json", "config.toml", "state_5.sqlite", "logs_2.sqlite", "session_index.jsonl"]
        },
    }
    status["ready"] = bool(status["home_exists"] and status["auth_exists"] and status["config_exists"])
    status["reason"] = "ok" if status["ready"] else "missing_home_or_auth_or_config"
    if probe:
        status["probe"] = probe_watchbrief_codex(home, timeout=timeout)
        status["ready"] = bool(status["ready"] and status["probe"].get("ok"))
        if not status["probe"].get("ok"):
            status["reason"] = status["probe"].get("reason", "probe_failed")
    return status


def probe_watchbrief_codex(home: Path, *, timeout: int = 20) -> dict[str, Any]:
    env = dict(os.environ)
    env["HOME"] = str(real_user_home())
    env["CODEX_HOME"] = str(home)
    env["PATH"] = f"{real_user_home() / 'bin'}:{env.get('PATH', '')}"
    project_root = Path(os.environ.get("WATCHBRIEF_PROJECT_ROOT", DEFAULT_WATCHBRIEF_PROJECT_ROOT)).expanduser()
    cwd = project_root if project_root.exists() else real_user_home()
    try:
        completed = subprocess.run(
            ["codex", "exec", "Return exactly OK"],
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout", "cwd": str(cwd)}
    except FileNotFoundError:
        return {"ok": False, "reason": "codex_not_found", "cwd": str(cwd)}
    combined = f"{completed.stdout}\n{completed.stderr}"
    lower = combined.lower()
    if completed.returncode == 0 and "ok" in lower:
        return {"ok": True, "reason": "ok", "cwd": str(cwd), "returncode": completed.returncode}
    for marker in ["refresh_token_reused", "token_expired", "401", "not logged in", "login", "not inside a trusted directory"]:
        if marker in lower:
            return {"ok": False, "reason": marker, "returncode": completed.returncode, "cwd": str(cwd)}
    return {"ok": False, "reason": "codex_probe_failed", "returncode": completed.returncode, "cwd": str(cwd)}


def write_watchbrief_codex_status(status: dict[str, Any], path: Path | None = None) -> Path:
    path = path or SMALLCOLA_STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def switch_watchbrief_codex_account(account: str, *, root: Path | None = None, probe: bool = False) -> dict[str, Any]:
    root = root or watchbrief_codex_root()
    if not account or "/" in account or account in {".", ".."}:
        raise ValueError(f"invalid account: {account!r}")
    home = root / account
    if not home.is_dir():
        raise FileNotFoundError(f"WatchBrief Codex account not found: {home}")
    root.mkdir(parents=True, exist_ok=True)
    target = current_file(root)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(account + "\n", encoding="utf-8")
    tmp.replace(target)
    status = collect_watchbrief_codex_status(root=root, probe=probe)
    write_watchbrief_codex_status(status)
    return status
