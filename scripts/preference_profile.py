#!/usr/bin/env python3
"""Load a unified WatchBefore interest profile and match report topics."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PROFILE_SCHEMA_VERSION = "watchbefore.profile.v2"
PERSONAL_RELEVANCE_BASIS = "watchtrace_bilibili_profile"
NEUTRAL_TOPIC_INTEREST_SCORE = 5.0
CONFIDENCE_TOPIC_SCORES = {
    "high": 8.5,
    "medium": 7.5,
    "low": 6.5,
}


def _clean_string(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _clean_string(item))]


def _coerce_domains(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    domains: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _clean_string(item.get("name"))
        subtopics = _clean_string_list(item.get("subtopics"))
        keywords = _clean_string_list(item.get("keywords"))
        confidence = _clean_string(item.get("confidence"))
        if not name or not subtopics or not keywords or confidence not in CONFIDENCE_TOPIC_SCORES:
            continue
        domains.append(
            {
                "name": name,
                "description": _clean_string(item.get("description")),
                "subtopics": subtopics,
                "keywords": keywords,
                "confidence": confidence,
                "evidence_count": int(item.get("evidence_count") or 0),
                "evidence_ids": _clean_string_list(item.get("evidence_ids")),
                "watched_seconds": int(item.get("watched_seconds") or 0),
                "watched_hours": float(item.get("watched_hours") or 0),
                "watch_count": int(item.get("watch_count") or 0),
                "watch_time_share": float(item.get("watch_time_share") or 0),
            }
        )
    return domains


def _coerce_content_preferences(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    preferences: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _clean_string(item.get("name"))
        confidence = _clean_string(item.get("confidence"))
        if not name or confidence not in CONFIDENCE_TOPIC_SCORES:
            continue
        preferences.append(
            {
                "name": name,
                "description": _clean_string(item.get("description")),
                "confidence": confidence,
                "evidence_count": int(item.get("evidence_count") or 0),
                "evidence_ids": _clean_string_list(item.get("evidence_ids")),
                "watched_seconds": int(item.get("watched_seconds") or 0),
                "watched_hours": float(item.get("watched_hours") or 0),
                "watch_count": int(item.get("watch_count") or 0),
                "watch_time_share": float(item.get("watch_time_share") or 0),
            }
        )
    return preferences


def _coerce_interest_profile(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "status": _clean_string(raw.get("status")),
        "basis": _clean_string(raw.get("basis")),
        "summary": _clean_string(raw.get("summary")),
        "domains": _coerce_domains(raw.get("domains")),
        "content_preferences": _coerce_content_preferences(raw.get("content_preferences")),
        "note": _clean_string(raw.get("note")),
    }


def load_preference_profile(profile_path: Path) -> dict[str, Any]:
    path = profile_path.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"preference profile not found: {path}")
    if not path.is_file():
        raise ValueError(f"preference profile is not a file: {path}")
    content = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "preference profile must use the canonical watchbefore.profile.v2 JSON/YAML encoding"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError("preference profile must be an object")
    schema_version = _clean_string(parsed.get("schema_version"))
    if schema_version != PROFILE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported preference profile schema: {schema_version or '<missing>'}; "
            f"expected {PROFILE_SCHEMA_VERSION}"
        )
    return {
        "path": str(path),
        "schema_version": schema_version,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "bytes": len(content.encode("utf-8")),
        "behavior_summary": (
            parsed.get("behavior_summary") if isinstance(parsed.get("behavior_summary"), dict) else {}
        ),
        "interest_profile": _coerce_interest_profile(parsed.get("interest_profile")),
    }


def normalize_topic(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def topic_terms_from_value(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = [str(item) for item in value]
    else:
        raw_items = re.split(r"[/／|,，、;；>\n]+", str(value or ""))
    terms: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = " ".join(str(item or "").split()).strip()
        key = normalize_topic(text)
        if text and key and key not in seen:
            terms.append(text)
            seen.add(key)
    return terms


def _topic_term_matches(profile_topic: str, report_term: str) -> bool:
    """Match topic phrases while keeping short Latin tokens on word boundaries."""
    profile_key = normalize_topic(profile_topic)
    report_key = normalize_topic(report_term)
    if not profile_key or not report_key:
        return False
    if profile_key == report_key:
        return True
    if re.fullmatch(r"[a-z0-9]+", profile_key):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(profile_key)}(?![a-z0-9])", report_key))
    return profile_key in report_key or report_key in profile_key


def _matched_domain_terms(domain: dict[str, Any], report_terms: list[str]) -> list[str]:
    candidates = [
        str(domain.get("name") or ""),
        *domain.get("keywords", []),
        *domain.get("subtopics", []),
    ]
    matches: list[str] = []
    for candidate in candidates:
        if candidate and any(_topic_term_matches(candidate, report_term) for report_term in report_terms):
            matches.append(candidate)
    return list(dict.fromkeys(matches))


def _domain_history_summary(domain: dict[str, Any]) -> str:
    watched_hours = float(domain.get("watched_hours") or 0)
    watch_count = int(domain.get("watch_count") or 0)
    watch_time_share = max(0.0, float(domain.get("watch_time_share") or 0))
    return (
        f"历史观看 {watched_hours:.2f} 小时、{watch_count} 条，"
        f"占已分析观看时长 {watch_time_share * 100:.2f}%"
    )


def build_personal_relevance(report_payload: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    interest_profile = _coerce_interest_profile(profile.get("interest_profile"))
    report_terms = topic_terms_from_value(report_payload.get("topic"))
    profile_status = str(interest_profile.get("status") or "").strip()
    domains = interest_profile.get("domains", [])

    matches: list[tuple[dict[str, Any], list[str]]] = []
    for domain in domains:
        terms = _matched_domain_terms(domain, report_terms)
        if terms:
            matches.append((domain, terms))

    if profile_status != "ready" or not domains:
        status = "insufficient_profile"
        match_level = "insufficient_interest_profile"
        topic_interest_score = NEUTRAL_TOPIC_INTEREST_SCORE
        summary = "画像判断：暂时无法判断是否喜欢。当前画像缺少基于历史观看内容生成的统一兴趣领域。"
        evidence = ["没有使用作者、标题或稍后再看记录猜测兴趣。"]
    elif matches:
        status = "matched"
        match_level = "interest_domain_match"
        matches.sort(
            key=lambda item: (
                CONFIDENCE_TOPIC_SCORES.get(str(item[0].get("confidence")), 0),
                int(item[0].get("watched_seconds") or 0),
                int(item[0].get("watch_count") or 0),
                int(item[0].get("evidence_count") or 0),
            ),
            reverse=True,
        )
        topic_interest_score = max(
            CONFIDENCE_TOPIC_SCORES[str(domain["confidence"])] for domain, _terms in matches
        )
        names = [str(domain["name"]) for domain, _terms in matches[:3]]
        primary_domain = matches[0][0]
        summary = (
            f"画像判断：可能喜欢。当前内容命中你的兴趣领域：{'、'.join(names)}；"
            f"其中「{primary_domain['name']}」{_domain_history_summary(primary_domain)}。"
        )
        evidence = [
            f"{domain['name']}：{'、'.join(terms[:3])}；{_domain_history_summary(domain)}"
            for domain, terms in matches[:6]
        ]
    else:
        status = "no_match"
        match_level = "no_interest_domain_match"
        topic_interest_score = NEUTRAL_TOPIC_INTEREST_SCORE
        summary = "画像判断：暂时无法判断是否喜欢。当前内容没有命中已确认的兴趣领域。"
        evidence = ["未命中兴趣领域名称或其细分关注点。"]

    return {
        "status": status,
        "basis": PERSONAL_RELEVANCE_BASIS,
        "match_level": match_level,
        "topic_interest_score": topic_interest_score,
        "summary": summary,
        "evidence": evidence[:6],
        "caveat": "兴趣画像只来自真实历史观看事实与 WatchBefore 内容报告；不使用作者级推断，也不替你做最终观看决定。",
        "profile_schema_version": str(profile.get("schema_version") or PROFILE_SCHEMA_VERSION),
        "profile_sha256": str(profile.get("sha256") or ""),
    }
