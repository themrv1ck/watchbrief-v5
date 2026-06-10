#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from .watchbrief_codex_state import (
        collect_watchbrief_codex_status,
        read_current_watchbrief_codex_account,
        switch_watchbrief_codex_account,
        watchbrief_codex_root,
        write_watchbrief_codex_status,
    )
except ImportError:  # pragma: no cover
    from watchbrief_codex_state import (
        collect_watchbrief_codex_status,
        read_current_watchbrief_codex_account,
        switch_watchbrief_codex_account,
        watchbrief_codex_root,
        write_watchbrief_codex_status,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="WatchBrief Codex account status/switch helper for SmallCola integration.")
    parser.add_argument("--root", help="WatchBrief Codex account root. Default: ~/.watchbrief_codex")
    parser.add_argument("--probe", action="store_true", help="Run a real 'codex exec Return exactly OK' probe. May consume a small amount of quota.")
    parser.add_argument("--write-status", action="store_true", help="Write ~/.codex-accounts/watchbrief-status.json for SmallCola to read.")
    parser.add_argument("--json", action="store_true", help="Print JSON output. Kept for CLI compatibility; output is JSON by default.")
    sub = parser.add_subparsers(dest="command")
    status = sub.add_parser("status", help="Print WatchBrief Codex status as JSON")
    status.add_argument("--probe", dest="status_probe", action="store_true", help="Run a real 'codex exec Return exactly OK' probe.")
    status.add_argument("--write-status", dest="status_write_status", action="store_true", help="Write ~/.codex-accounts/watchbrief-status.json for SmallCola to read.")
    status.add_argument("--json", dest="status_json", action="store_true", help="Print JSON output. Kept for compatibility.")
    sub.add_parser("current", help="Print the current WatchBrief Codex account name")
    switch = sub.add_parser("switch", help="Switch WatchBrief current Codex account")
    switch.add_argument("account")
    switch.add_argument("--probe", dest="switch_probe", action="store_true", help="Probe the target account after switching.")
    args = parser.parse_args()

    root = watchbrief_codex_root(args.root)
    try:
        if args.command in {None, "status"}:
            do_probe = bool(args.probe or getattr(args, "status_probe", False))
            do_write = bool(args.write_status or getattr(args, "status_write_status", False))
            status = collect_watchbrief_codex_status(root=root, probe=do_probe)
            if do_write:
                status["status_path"] = str(write_watchbrief_codex_status(status))
        elif args.command == "current":
            print(read_current_watchbrief_codex_account(root))
            return 0
        elif args.command == "switch":
            do_probe = bool(args.probe or getattr(args, "switch_probe", False))
            status = switch_watchbrief_codex_account(args.account, root=root, probe=do_probe)
        else:  # pragma: no cover
            parser.error("unknown command")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
