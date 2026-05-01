"""Validated normalized report cache for WatchBrief V5."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

try:
    from .stability import stable_sha256
    from .validator import validate_normalized_report_payload
except ImportError:  # pragma: no cover
    from stability import stable_sha256
    from validator import validate_normalized_report_payload


REPORT_CACHE_VERSION = "watchbrief_v5.report_cache.v1"
REPORT_CACHE_ENV = "WATCHBRIEF_REPORT_CACHE_DIR"
DEFAULT_REPORT_CACHE_DIR = Path.home() / ".watchbrief" / "cache" / "reports"
REPORT_CACHE_KEY_FIELDS = (
    "transcript_hash",
    "qwen_model_id",
    "qwen_prompt_fingerprint",
    "codex_model",
    "codex_prompt_fingerprint",
    "scoring_formula_version",
    "watchbrief_version",
)


def report_cache_dir(path: Optional[Any] = None) -> Path:
    if path:
        return Path(path).expanduser()
    configured = os.environ.get(REPORT_CACHE_ENV)
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_REPORT_CACHE_DIR


def build_report_cache_identity(review_request: dict[str, Any], *, codex_model: str) -> dict[str, str]:
    metadata = review_request.get("stability_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    identity = {
        "transcript_hash": str(metadata.get("transcript_hash") or ""),
        "qwen_model_id": str(metadata.get("qwen_model_id") or ""),
        "qwen_prompt_fingerprint": str(metadata.get("qwen_prompt_fingerprint") or ""),
        "codex_model": str(codex_model or metadata.get("codex_model") or ""),
        "codex_prompt_fingerprint": str(metadata.get("codex_prompt_fingerprint") or review_request.get("codex_prompt_fingerprint") or ""),
        "scoring_formula_version": str(metadata.get("scoring_formula_version") or ""),
        "watchbrief_version": str(metadata.get("watchbrief_version") or ""),
    }
    missing = [field for field in REPORT_CACHE_KEY_FIELDS if not identity.get(field)]
    if missing:
        raise ValueError(f"report cache identity missing fields: {missing}")
    return identity


def report_cache_key(identity: dict[str, Any]) -> str:
    material = {
        "cache_version": REPORT_CACHE_VERSION,
        "identity": {field: str(identity.get(field) or "") for field in REPORT_CACHE_KEY_FIELDS},
    }
    return stable_sha256(material)


def report_cache_path(cache_dir: Path, cache_key: str) -> Path:
    return cache_dir / f"{cache_key}.json"


def read_cached_report(cache_dir: Path, identity: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Path, str]:
    cache_key = report_cache_key(identity)
    path = report_cache_path(cache_dir, cache_key)
    if not path.exists():
        return None, path, cache_key
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict):
            return None, path, cache_key
        if envelope.get("cache_version") != REPORT_CACHE_VERSION:
            return None, path, cache_key
        if envelope.get("identity") != {field: str(identity.get(field) or "") for field in REPORT_CACHE_KEY_FIELDS}:
            return None, path, cache_key
        payload = envelope.get("normalized_payload")
        if not isinstance(payload, dict):
            return None, path, cache_key
        return validate_normalized_report_payload(payload), path, cache_key
    except Exception:
        return None, path, cache_key


def write_cached_report(cache_dir: Path, identity: dict[str, Any], normalized_payload: dict[str, Any]) -> tuple[Path, str]:
    payload = validate_normalized_report_payload(normalized_payload)
    cache_key = report_cache_key(identity)
    path = report_cache_path(cache_dir, cache_key)
    cache_dir.mkdir(parents=True, exist_ok=True)
    envelope = {
        "cache_version": REPORT_CACHE_VERSION,
        "cache_key": cache_key,
        "identity": {field: str(identity.get(field) or "") for field in REPORT_CACHE_KEY_FIELDS},
        "normalized_payload": copy.deepcopy(payload),
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(cache_dir), delete=False) as handle:
        temp_path = Path(handle.name)
        json.dump(envelope, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)
    return path, cache_key
