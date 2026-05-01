"""Stable fingerprints and transcript hashes for WatchBrief V5."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_sha256(value: Any) -> str:
    if isinstance(value, str):
        blob = value
    else:
        blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def transcript_hash_from_segments(segments: list[dict[str, Any]]) -> str:
    return stable_sha256(segments)


def prompt_fingerprint(*parts: Any) -> str:
    return stable_sha256(list(parts))
