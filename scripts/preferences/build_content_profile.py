#!/usr/bin/env python3
"""Build one unified WatchBefore interest profile from analyzed watch history."""

from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable

try:
    from scripts.analyzer.local_extract import (
        LocalQwenError,
        choose_qwen_model,
        configured_qwen_api_base,
        extract_message_text,
        http_json,
    )
except ModuleNotFoundError:  # direct script execution from outside the repository root
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.analyzer.local_extract import (
        LocalQwenError,
        choose_qwen_model,
        configured_qwen_api_base,
        extract_message_text,
        http_json,
    )


DEFAULT_EVENTS = Path("~/.watchbrief/preferences/events.jsonl").expanduser()
DEFAULT_OUTPUT = Path("~/.watchbrief/preferences/seth.yaml").expanduser()
DEFAULT_DEBUG_DIR = Path("~/.watchbrief/preferences/interest-synthesis").expanduser()
PROFILE_SCHEMA_VERSION = "watchbefore.profile.v2"
VALID_EVENT_SCHEMAS = {"watchbefore.event.v1", "watchfilter.event.v1"}
ANALYSIS_SOURCE_PRIORITY = {
    "normalized_payload": 3,
    "local_extract": 2,
    "chunked_local_extract": 1,
}
MIN_TAXONOMY_SUPPORT = 3
CLASSIFICATION_BATCH_SIZE = 24
TAXONOMY_MAX_OBSERVATIONS = 240
SYNTHESIS_TOPIC_MAX_CHARS = 180
SUBTOPIC_MAX_TOPICS_PER_DOMAIN = 30

TAXONOMY_SYSTEM_PROMPT = """你是 WatchBefore 的个人兴趣分类体系设计器。

输入只包含实际观看秒数大于 0 的历史观看内容。
content_topic 来自字幕/转写内容分析，不是网页标题；稍后再看记录已排除。watched_seconds 是累计观看时间，watch_count 是观看记录数量。

请设计一套互不重叠、可用于后续逐条分类的统一兴趣体系。
必须遵守：
1. domains 只描述用户反复关注的内容领域；不得输出正向、负向、稍后再看、待观察等行为桶。
2. 每个 domain 应覆盖至少 3 条输入，且不同 domain 的定义边界必须清楚，不能把同一大类拆成多个近义领域。
3. 判断主次时同时以累计 watched_seconds 和覆盖的视频数量为核心；观看时间更长、覆盖视频更多的领域应排在前面。单条超长视频不能独自形成领域。
4. 必须覆盖输入中反复出现的不同兴趣；科技、影视、体育、旅行、健康、军事等只有在各自确有至少 3 条证据时才分别建类，不要为了少建类把它们硬塞到近邻领域。
5. 单个人名、频道名、节目名、产品名或一条视频事件不能直接成为 domain。
6. content_preferences 只描述内容形态，例如深度机制讲解、真实案例、技术实操、长访谈；不得重复 domains。
7. 不引用 observation id，不在 description 中写证据编号，不使用作者或外部知识补结论。
8. 严格按 JSON Schema 输出纯 JSON；不要 Markdown，不要解释文字。
"""

TAXONOMY_REFINEMENT_SYSTEM_PROMPT = """你是 WatchBefore 的兴趣分类体系审查器。

请根据同一批真实兴趣证据，审查并重写 draft_taxonomy。
必须遵守：
1. 禁止父类与子类并列，禁止近义领域并列。例如一个“科技与人工智能”大类不能再和“AI内容工具”子类同时存在；“体育文化”不能再与“运动训练”并列。
2. 各 domain 粒度应大致一致、边界明确。过宽的兜底大类必须拆成互斥范围，过窄的子类必须合并到上位领域并保留为 subtopics。
3. 保留输入中至少出现 3 次的真实兴趣，不为了减少数量删除旅行、健康、体育、文化等独立领域。
4. domain 数量必须为 6 到 10 个。content_preferences 只描述内容形态，不能重复兴趣领域。
5. 不引用 observation id，不使用作者或外部知识补结论。
6. 严格按 JSON Schema 输出纯 JSON；不要 Markdown，不要解释文字。
"""

SUBTOPIC_SYSTEM_PROMPT = """你是 WatchBefore 的兴趣细分关注点提炼器。

输入已经按统一兴趣领域完成逐条归类。你只能根据每个领域 assigned_topics 中的真实内容主题，提炼该领域反复出现的细分关注点。
必须遵守：
1. 每个领域输出 3 到 8 个简洁 subtopics；关注点应概括多条证据，不能复制整段视频摘要。
2. 每个领域同时输出 3 到 12 个 keywords，供新视频做确定性匹配。关键词应是该领域反复出现的短术语、稳定工具名或主题短语，例如 AI、Hermes Agent、F1；不能是整句话。
3. 不得使用其他领域的内容，不得补充外部知识。
4. 单个人名、频道名、节目名或单条事件不能成为关注点或关键词，除非它在该领域多条 assigned_topics 中反复出现。
5. 不输出观看状态、正负向、稍后再看或是否推荐。
6. 严格按 JSON Schema 输出纯 JSON；不要 Markdown，不要解释文字。
"""

TAXONOMY_SCHEMA: dict[str, Any] = {
    "name": "watchbefore_interest_taxonomy",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["domains", "content_preferences"],
        "properties": {
            "domains": {
                "type": "array",
                "minItems": 6,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "description", "subtopics"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "description": {"type": "string", "minLength": 1},
                        "subtopics": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 10,
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
            "content_preferences": {
                "type": "array",
                "minItems": 2,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "description"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "description": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
}
TAXONOMY_REFINEMENT_SCHEMA = copy.deepcopy(TAXONOMY_SCHEMA)
TAXONOMY_REFINEMENT_SCHEMA["name"] = "watchbefore_interest_taxonomy_refined"
TAXONOMY_REFINEMENT_SCHEMA["schema"]["properties"]["domains"]["maxItems"] = 10


class InterestSynthesisError(ValueError):
    """Raised when local interest synthesis is unavailable or invalid."""


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.expanduser().read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict) and item.get("schema_version") in VALID_EVENT_SCHEMAS and item.get("url"):
            events.append(item)
    return events


def url_key(value: Any) -> str:
    return str(value or "").strip().split("#", 1)[0]


def load_report_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for pattern in patterns:
        expanded = [Path(item) for item in glob.glob(str(Path(pattern).expanduser()), recursive=True)]
        direct = Path(pattern).expanduser()
        if direct.exists():
            expanded.append(direct)
        for path in expanded:
            if path.is_dir():
                expanded.extend(path.rglob("*.json"))
                continue
            if path.suffix.lower() != ".json":
                continue
            key = str(path.resolve())
            if key not in seen:
                paths.append(path)
                seen.add(key)
    return paths


def load_reports(paths: list[Path]) -> list[dict[str, Any]]:
    reports_by_url: dict[str, dict[str, Any]] = {}
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidate = report_candidate(payload, path=path)
        if not candidate:
            continue
        key = url_key(candidate.get("url"))
        existing = reports_by_url.get(key)
        if not existing or int(candidate.get("_source_priority", 0)) > int(existing.get("_source_priority", 0)):
            reports_by_url[key] = candidate
    return list(reports_by_url.values())


def report_candidate(payload: Any, *, path: Path | None = None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("normalized_payload"), dict):
        payload = payload["normalized_payload"]
    if isinstance(payload, dict) and payload.get("url") and payload.get("topic"):
        return {
            **payload,
            "_analysis_source": "normalized_payload",
            "_source_priority": ANALYSIS_SOURCE_PRIORITY["normalized_payload"],
        }

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    qwen_extract = payload.get("qwen_extract") if isinstance(payload.get("qwen_extract"), dict) else {}
    url = metadata.get("url")
    main_axis = " ".join(str(qwen_extract.get("main_axis") or "").split()).strip()
    raw_terms = qwen_extract.get("important_terms", [])
    if not isinstance(raw_terms, list):
        raw_terms = []
    important_terms = [
        " ".join(str(item or "").split()).strip()
        for item in raw_terms
        if " ".join(str(item or "").split()).strip()
    ]
    topic_parts = [item for item in [main_axis, *important_terms] if item]
    if url and topic_parts:
        return {
            "url": url,
            "topic": " / ".join(topic_parts),
            "_analysis_source": "local_extract",
            "_source_priority": ANALYSIS_SOURCE_PRIORITY["local_extract"],
        }

    if payload.get("item_manifest_version") == "watchbrief_v5.pipeline_item.v1" and payload.get("url") and path:
        chunk_topic = chunked_topic_from_payload_dir(path.parent)
        if chunk_topic:
            return {
                "url": payload["url"],
                "topic": chunk_topic,
                "_analysis_source": "chunked_local_extract",
                "_source_priority": ANALYSIS_SOURCE_PRIORITY["chunked_local_extract"],
            }
    return None


def chunked_topic_from_payload_dir(payload_dir: Path) -> str:
    chunk_dir = payload_dir / "local_extract_chunks"
    if not chunk_dir.exists():
        return ""
    themes: list[str] = []
    terms: list[str] = []
    seen: set[str] = set()
    for path in sorted(chunk_dir.glob("chunk_*_adapted.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for value in [payload.get("core_theme")]:
            text = " ".join(str(value or "").split()).strip()
            key = normalize_topic(text)
            if text and key not in seen:
                themes.append(text)
                seen.add(key)
        raw_terms = payload.get("important_terms", [])
        if not isinstance(raw_terms, list):
            raw_terms = []
        for value in raw_terms:
            text = " ".join(str(value or "").split()).strip()
            key = normalize_topic(text)
            if text and key not in seen:
                terms.append(text)
                seen.add(key)
    return " / ".join([*themes[:8], *terms[:12]])


def normalize_topic(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def classification_terms(value: Any) -> list[str]:
    raw_items = re.split(r"[/／|,，、;；>\n]+", str(value or ""))
    terms: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        item = " ".join(raw_item.split()).strip()
        key = normalize_topic(item)
        if item and key and key not in seen:
            terms.append(item)
            seen.add(key)
    return terms


def compact_content_topic(value: Any, *, limit: int = SYNTHESIS_TOPIC_MAX_CHARS) -> str:
    """Bound synthesis prompts without introducing non-content metadata."""
    topic = " ".join(str(value or "").split()).strip()
    return topic if len(topic) <= limit else f"{topic[:limit].rstrip()}…"


def taxonomy_observation_sample(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use the highest-engagement content evidence to design the shared taxonomy.

    Every observation is still classified below; this only bounds the initial
    taxonomy-design request, which otherwise can exceed the local model context.
    """
    ranked = sorted(
        observations,
        key=lambda item: (
            int(item.get("watched_seconds") or 0),
            int(item.get("watch_count") or 0),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )[:TAXONOMY_MAX_OBSERVATIONS]
    return [{**item, "content_topic": compact_content_topic(item.get("content_topic"))} for item in ranked]


def compact_assigned_topics(
    assigned_topics: dict[str, list[str]],
    observations: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
) -> dict[str, list[str]]:
    observation_by_id = {str(item.get("id") or ""): item for item in observations}
    by_domain: dict[str, list[dict[str, Any]]] = {name: [] for name in assigned_topics}
    for assignment in assignments:
        observation = observation_by_id.get(str(assignment.get("id") or ""))
        if not observation:
            continue
        for domain_name in assignment.get("domain_names") or []:
            if domain_name in by_domain:
                by_domain[domain_name].append(observation)
    compacted: dict[str, list[str]] = {}
    for name, rows in by_domain.items():
        ranked = sorted(
            rows,
            key=lambda item: (
                int(item.get("watched_seconds") or 0),
                int(item.get("watch_count") or 0),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )[:SUBTOPIC_MAX_TOPICS_PER_DOMAIN]
        compacted[name] = [compact_content_topic(item.get("content_topic")) for item in ranked]
    return compacted


def _number(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def history_observations_by_url(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate real watch time and watch count per URL; exclude watch-later rows."""
    observations: dict[str, dict[str, Any]] = {}
    for event in events:
        if str(event.get("source") or "") != "history":
            continue
        key = url_key(event.get("url"))
        if not key:
            continue
        watched_seconds = int(_number(event.get("watched_seconds")))
        row = observations.setdefault(
            key,
            {
                "watch_ratio": 0.0,
                "watched_seconds": 0,
                "duration_seconds": 0,
                "watch_count": 0,
            },
        )
        row["watch_ratio"] = max(
            float(row["watch_ratio"]),
            min(1.0, _number(event.get("watch_ratio"))),
        )
        row["watched_seconds"] = int(row["watched_seconds"]) + watched_seconds
        row["duration_seconds"] = max(
            int(row["duration_seconds"]),
            int(_number(event.get("duration_seconds"))),
        )
        if watched_seconds > 0:
            row["watch_count"] = int(row["watch_count"]) + 1
    return observations


def build_synthesis_observations(
    events: list[dict[str, Any]],
    reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    history = history_observations_by_url(events)
    matched = [
        report
        for report in sorted(reports, key=lambda item: url_key(item.get("url")))
        if url_key(report.get("url")) in history
    ]
    observations: list[dict[str, Any]] = []
    for index, report in enumerate(matched, start=1):
        watched = history[url_key(report.get("url"))]
        observations.append(
            {
                "id": f"r{index:03d}",
                "content_topic": " ".join(str(report.get("topic") or "").split()).strip(),
                **watched,
            }
        )
    return observations


def select_interest_evidence(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use every analyzed video with real watch time; weight is preserved in the row."""
    return [
        observation
        for observation in observations
        if int(observation.get("watched_seconds") or 0) > 0
    ]


def _call_local_qwen_structured_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    selected_model: str,
    api_base: str | None,
    timeout: int,
    urlopen_func: Any,
    debug_request_path: Path | None = None,
    debug_response_path: Path | None = None,
) -> dict[str, Any]:
    request_payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": 8192,
        "reasoning_effort": "none",
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": schema,
        },
    }
    if debug_request_path:
        debug_request_path.parent.mkdir(parents=True, exist_ok=True)
        debug_request_path.write_text(
            json.dumps(request_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    response = http_json(
        f"{configured_qwen_api_base(api_base)}/chat/completions",
        method="POST",
        payload=request_payload,
        urlopen_func=urlopen_func,
        timeout=timeout,
    )
    raw_content = extract_message_text(response)
    if debug_response_path:
        debug_response_path.parent.mkdir(parents=True, exist_ok=True)
        debug_response_path.write_text(raw_content, encoding="utf-8")
    try:
        parsed = json.loads(raw_content.strip())
    except json.JSONDecodeError as exc:
        raise InterestSynthesisError(f"local Qwen returned non-strict JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InterestSynthesisError("local Qwen structured response must be one JSON object")
    return parsed


def _validate_taxonomy(
    payload: dict[str, Any],
    *,
    min_domains: int = 6,
    max_domains: int = 12,
) -> dict[str, Any]:
    if set(payload) != {"domains", "content_preferences"}:
        raise InterestSynthesisError("taxonomy keys must be exactly domains and content_preferences")
    raw_domains = payload.get("domains")
    if not isinstance(raw_domains, list) or not min_domains <= len(raw_domains) <= max_domains:
        raise InterestSynthesisError(
            f"taxonomy domains must contain {min_domains} to {max_domains} items"
        )
    domains: list[dict[str, Any]] = []
    domain_names: set[str] = set()
    for index, raw in enumerate(raw_domains):
        path = f"taxonomy.domains[{index}]"
        if not isinstance(raw, dict) or set(raw) != {"name", "description", "subtopics"}:
            raise InterestSynthesisError(f"{path} has invalid keys")
        name = _clean_string(raw.get("name"), f"{path}.name")
        name_key = normalize_topic(name)
        if name_key in domain_names:
            raise InterestSynthesisError(f"{path}.name duplicates another domain")
        domain_names.add(name_key)
        description = _clean_string(raw.get("description"), f"{path}.description")
        if re.search(r"\br\d{3}\b", description, flags=re.IGNORECASE):
            raise InterestSynthesisError(f"{path}.description must not contain evidence ids")
        raw_subtopics = raw.get("subtopics")
        if not isinstance(raw_subtopics, list) or not 1 <= len(raw_subtopics) <= 10:
            raise InterestSynthesisError(f"{path}.subtopics must contain 1 to 10 strings")
        domains.append(
            {
                "name": name,
                "description": description,
                "subtopics": [_clean_string(item, f"{path}.subtopics[]") for item in raw_subtopics],
            }
        )

    raw_preferences = payload.get("content_preferences")
    if not isinstance(raw_preferences, list) or not 2 <= len(raw_preferences) <= 6:
        raise InterestSynthesisError("taxonomy content_preferences must contain 2 to 6 items")
    preferences: list[dict[str, str]] = []
    preference_names: set[str] = set()
    for index, raw in enumerate(raw_preferences):
        path = f"taxonomy.content_preferences[{index}]"
        if not isinstance(raw, dict) or set(raw) != {"name", "description"}:
            raise InterestSynthesisError(f"{path} has invalid keys")
        name = _clean_string(raw.get("name"), f"{path}.name")
        name_key = normalize_topic(name)
        if name_key in preference_names:
            raise InterestSynthesisError(f"{path}.name duplicates another content preference")
        preference_names.add(name_key)
        preferences.append(
            {
                "name": name,
                "description": _clean_string(raw.get("description"), f"{path}.description"),
            }
        )
    return {"domains": domains, "content_preferences": preferences}


def _classification_schema(
    observation_ids: list[str],
    domain_names: list[str],
    preference_names: list[str],
) -> dict[str, Any]:
    assignment_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["domain_name", "content_preference_names"],
        "properties": {
            "domain_name": {
                "type": "string",
                "enum": [*domain_names, "unclassified"],
            },
            "content_preference_names": {
                "type": "array",
                "maxItems": 1,
                "items": {"type": "string", "enum": preference_names},
            },
        },
    }
    return {
        "name": "watchbefore_interest_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["assignments"],
            "properties": {
                "assignments": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": observation_ids,
                    "properties": {
                        observation_id: copy.deepcopy(assignment_schema)
                        for observation_id in observation_ids
                    },
                }
            },
        },
    }


def _validate_assignments(
    payload: dict[str, Any],
    observations: list[dict[str, Any]],
    domain_names: set[str],
    preference_names: set[str],
) -> list[dict[str, Any]]:
    if set(payload) != {"assignments"} or not isinstance(payload.get("assignments"), dict):
        raise InterestSynthesisError("classification response must contain only assignments")
    expected_ids = [str(item["id"]) for item in observations]
    assignments = payload["assignments"]
    if set(assignments) != set(expected_ids):
        raise InterestSynthesisError("classification must return exactly the requested report ids")
    validated: list[dict[str, Any]] = []
    for evidence_id in expected_ids:
        raw = assignments[evidence_id]
        path = f"assignments.{evidence_id}"
        expected_keys = {
            "domain_name",
            "content_preference_names",
        }
        if not isinstance(raw, dict) or set(raw) != expected_keys:
            raise InterestSynthesisError(f"{path} has invalid keys")
        domain_name = _clean_string(raw.get("domain_name"), f"{path}.domain_name")
        raw_preferences = raw.get("content_preference_names")
        if not isinstance(raw_preferences, list) or len(raw_preferences) > 1:
            raise InterestSynthesisError(f"{path}.content_preference_names must contain at most 1 item")
        preferences = [_clean_string(item, f"{path}.content_preference_names[]") for item in raw_preferences]
        if domain_name != "unclassified" and domain_name not in domain_names:
            raise InterestSynthesisError(f"{path}.domain_name contains an invalid label")
        if domain_name == "unclassified":
            domains: list[str] = []
        else:
            domains = [domain_name]
        if len(preferences) != len(set(preferences)) or set(preferences) - preference_names:
            raise InterestSynthesisError(f"{path}.content_preference_names contains invalid labels")
        validated.append(
            {
                "id": evidence_id,
                "domain_names": domains,
                "content_preference_names": preferences,
            }
        )
    return validated


def _subtopic_schema(domain_names: list[str]) -> dict[str, Any]:
    return {
        "name": "watchbefore_interest_subtopics",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["domain_subtopics"],
            "properties": {
                "domain_subtopics": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": domain_names,
                    "properties": {
                        domain_name: {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["subtopics", "keywords"],
                            "properties": {
                                "subtopics": {
                                    "type": "array",
                                    "minItems": 3,
                                    "maxItems": 8,
                                    "items": {"type": "string", "minLength": 1},
                                },
                                "keywords": {
                                    "type": "array",
                                    "minItems": 3,
                                    "maxItems": 12,
                                    "items": {"type": "string", "minLength": 1},
                                },
                            },
                        }
                        for domain_name in domain_names
                    },
                }
            },
        },
    }


def _validate_subtopics(
    payload: dict[str, Any],
    domain_names: list[str],
) -> dict[str, dict[str, list[str]]]:
    if set(payload) != {"domain_subtopics"} or not isinstance(
        payload.get("domain_subtopics"), dict
    ):
        raise InterestSynthesisError("subtopic response must contain only domain_subtopics")
    raw_subtopics = payload["domain_subtopics"]
    if set(raw_subtopics) != set(domain_names):
        raise InterestSynthesisError("subtopic response must contain every domain exactly once")
    validated: dict[str, dict[str, list[str]]] = {}
    for domain_name in domain_names:
        raw_profile = raw_subtopics[domain_name]
        if not isinstance(raw_profile, dict) or set(raw_profile) != {"subtopics", "keywords"}:
            raise InterestSynthesisError(
                f"domain_subtopics.{domain_name} must contain subtopics and keywords"
            )
        values = raw_profile.get("subtopics")
        keywords = raw_profile.get("keywords")
        if not isinstance(values, list) or not 3 <= len(values) <= 8:
            raise InterestSynthesisError(
                f"domain_subtopics.{domain_name}.subtopics must contain 3 to 8 items"
            )
        if not isinstance(keywords, list) or not 3 <= len(keywords) <= 12:
            raise InterestSynthesisError(
                f"domain_subtopics.{domain_name}.keywords must contain 3 to 12 items"
            )
        cleaned = [
            _clean_string(item, f"domain_subtopics.{domain_name}.subtopics[]")
            for item in values
        ]
        cleaned_keywords = [
            _clean_string(item, f"domain_subtopics.{domain_name}.keywords[]")
            for item in keywords
        ]
        if len({normalize_topic(item) for item in cleaned}) != len(cleaned):
            raise InterestSynthesisError(
                f"domain_subtopics.{domain_name}.subtopics contains duplicate items"
            )
        if len({normalize_topic(item) for item in cleaned_keywords}) != len(cleaned_keywords):
            raise InterestSynthesisError(
                f"domain_subtopics.{domain_name}.keywords contains duplicate items"
            )
        validated[domain_name] = {
            "subtopics": cleaned,
            "keywords": cleaned_keywords,
        }
    return validated


def _confidence_for_support(count: int) -> str:
    if count >= 12:
        return "high"
    if count >= 6:
        return "medium"
    return "low"


def _engagement_metrics(
    evidence_ids: list[str],
    observation_by_id: dict[str, dict[str, Any]],
    total_watched_seconds: int,
) -> dict[str, Any]:
    watched_seconds = sum(
        int(observation_by_id[evidence_id].get("watched_seconds") or 0)
        for evidence_id in evidence_ids
    )
    watch_count = sum(
        int(observation_by_id[evidence_id].get("watch_count") or 0)
        for evidence_id in evidence_ids
    )
    return {
        "watched_seconds": watched_seconds,
        "watched_hours": round(watched_seconds / 3600, 2),
        "watch_count": watch_count,
        "watch_time_share": (
            round(watched_seconds / total_watched_seconds, 6)
            if total_watched_seconds > 0
            else 0.0
        ),
    }


def _assemble_interest_synthesis(
    taxonomy: dict[str, Any],
    assignments: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    observation_by_id = {str(item["id"]): item for item in observations}
    total_watched_seconds = sum(
        int(item.get("watched_seconds") or 0) for item in observations
    )
    domain_evidence: dict[str, list[str]] = {
        str(item["name"]): [] for item in taxonomy["domains"]
    }
    preference_evidence: dict[str, list[str]] = {
        str(item["name"]): [] for item in taxonomy["content_preferences"]
    }
    for assignment in assignments:
        evidence_id = str(assignment["id"])
        for name in assignment["domain_names"]:
            domain_evidence[name].append(evidence_id)
        for name in assignment["content_preference_names"]:
            preference_evidence[name].append(evidence_id)

    unsupported_domains = [
        name for name, evidence_ids in domain_evidence.items() if len(evidence_ids) < MIN_TAXONOMY_SUPPORT
    ]
    if unsupported_domains:
        raise InterestSynthesisError(
            f"taxonomy domains lack {MIN_TAXONOMY_SUPPORT} classified evidence rows: {unsupported_domains}"
        )

    domains: list[dict[str, Any]] = []
    for item in taxonomy["domains"]:
        evidence_ids = domain_evidence[item["name"]]
        engagement = _engagement_metrics(
            evidence_ids,
            observation_by_id,
            total_watched_seconds,
        )
        domains.append(
            {
                **item,
                "confidence": _confidence_for_support(len(evidence_ids)),
                "evidence_count": len(evidence_ids),
                "evidence_ids": evidence_ids,
                **engagement,
            }
        )
    domains.sort(
        key=lambda item: (
            int(item["watched_seconds"]),
            int(item["watch_count"]),
            int(item["evidence_count"]),
        ),
        reverse=True,
    )

    preferences: list[dict[str, Any]] = []
    for item in taxonomy["content_preferences"]:
        evidence_ids = preference_evidence[item["name"]]
        if len(evidence_ids) < MIN_TAXONOMY_SUPPORT:
            continue
        preferences.append(
            {
                **item,
                "confidence": _confidence_for_support(len(evidence_ids)),
                "evidence_count": len(evidence_ids),
                "evidence_ids": evidence_ids,
                **_engagement_metrics(
                    evidence_ids,
                    observation_by_id,
                    total_watched_seconds,
                ),
            }
        )
    if len(preferences) < 2:
        raise InterestSynthesisError("fewer than two content preferences have sufficient evidence")
    preferences.sort(
        key=lambda item: (
            int(item["watched_seconds"]),
            int(item["watch_count"]),
            int(item["evidence_count"]),
        ),
        reverse=True,
    )
    domain_summary = "、".join(item["name"] for item in domains[:4])
    preference_summary = "、".join(item["name"] for item in preferences[:3])
    return {
        "summary": f"你的主要兴趣集中在：{domain_summary}；常偏好的内容形态是：{preference_summary}。",
        "domains": domains,
        "content_preferences": preferences,
    }


def call_local_qwen_interest_synthesis(
    observations: list[dict[str, Any]],
    *,
    model_id: str | None = None,
    api_base: str | None = None,
    timeout: int = 240,
    urlopen_func: Any = urllib.request.urlopen,
    debug_dir: Path | None = None,
) -> dict[str, Any]:
    selected_model = choose_qwen_model(
        model_id=model_id,
        api_base=api_base,
        urlopen_func=urlopen_func,
        timeout=min(timeout, 30),
    )
    debug_root = debug_dir.expanduser() if debug_dir else None
    taxonomy_payload = _call_local_qwen_structured_json(
        system_prompt=TAXONOMY_SYSTEM_PROMPT,
        user_prompt=(
            "请从以下兴趣证据中设计统一分类体系。\n\n"
            + json.dumps({"observations": taxonomy_observation_sample(observations)}, ensure_ascii=False, separators=(",", ":"))
        ),
        schema=TAXONOMY_SCHEMA,
        selected_model=selected_model,
        api_base=api_base,
        timeout=timeout,
        urlopen_func=urlopen_func,
        debug_request_path=debug_root / "taxonomy.request.json" if debug_root else None,
        debug_response_path=debug_root / "taxonomy.response.txt" if debug_root else None,
    )
    draft_taxonomy = _validate_taxonomy(taxonomy_payload)
    refined_taxonomy_payload = _call_local_qwen_structured_json(
        system_prompt=TAXONOMY_REFINEMENT_SYSTEM_PROMPT,
        user_prompt=json.dumps(
            {
                "draft_taxonomy": {
                    "domains": [
                        {"name": item["name"], "description": item["description"]}
                        for item in draft_taxonomy["domains"]
                    ],
                    "content_preferences": draft_taxonomy["content_preferences"],
                },
                "observations": taxonomy_observation_sample(observations),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        schema=TAXONOMY_REFINEMENT_SCHEMA,
        selected_model=selected_model,
        api_base=api_base,
        timeout=timeout,
        urlopen_func=urlopen_func,
        debug_request_path=(
            debug_root / "taxonomy-refinement.request.json" if debug_root else None
        ),
        debug_response_path=(
            debug_root / "taxonomy-refinement.response.txt" if debug_root else None
        ),
    )
    taxonomy = _validate_taxonomy(refined_taxonomy_payload, max_domains=10)
    domain_names = [item["name"] for item in taxonomy["domains"]]
    preference_names = [item["name"] for item in taxonomy["content_preferences"]]
    taxonomy_for_prompt = {
        "domains": [
            {"name": item["name"], "description": item["description"]}
            for item in taxonomy["domains"]
        ],
        "content_preferences": taxonomy["content_preferences"],
    }
    classification_system_prompt = """你是 WatchBefore 的逐条兴趣证据分类器。

必须把每条 observation 独立对照给定 taxonomy 分类。
每条只能选择一个主 domain；没有清楚匹配就用 unclassified。错误归类比不归类更糟。
仅在人物、品牌、广告或旁支词与某领域有关，但视频主话题无关时，必须 unclassified。
每条最多归入 1 个 content preference；只有 content_terms 明确展示内容形态时才归类，纯术语列表必须留空。
不得新增、改写或合并标签；不得因为编号相近而归类；只依据 content_topic 的实际语义。
assignments 必须以每个 observation 的 id 为对象键；结构中已经固定了全部键，不得遗漏或新增。
严格输出纯 JSON。
"""
    assignments: list[dict[str, Any]] = []
    for batch_index, start in enumerate(range(0, len(observations), CLASSIFICATION_BATCH_SIZE), start=1):
        batch = observations[start:start + CLASSIFICATION_BATCH_SIZE]
        batch_ids = [str(item["id"]) for item in batch]
        classification_batch = [
            {
                "id": item["id"],
                "content_terms": classification_terms(item.get("content_topic")),
            }
            for item in batch
        ]
        classification_payload = _call_local_qwen_structured_json(
            system_prompt=classification_system_prompt,
            user_prompt=json.dumps(
                {"taxonomy": taxonomy_for_prompt, "observations": classification_batch},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            schema=_classification_schema(batch_ids, domain_names, preference_names),
            selected_model=selected_model,
            api_base=api_base,
            timeout=timeout,
            urlopen_func=urlopen_func,
            debug_request_path=(
                debug_root / f"classification-{batch_index:03d}.request.json"
                if debug_root
                else None
            ),
            debug_response_path=(
                debug_root / f"classification-{batch_index:03d}.response.txt"
                if debug_root
                else None
            ),
        )
        assignments.extend(
            _validate_assignments(
                classification_payload,
                batch,
                set(domain_names),
                set(preference_names),
            )
        )

    observation_by_id = {str(item["id"]): item for item in observations}
    assigned_topics: dict[str, list[str]] = {name: [] for name in domain_names}
    for assignment in assignments:
        for domain_name in assignment["domain_names"]:
            assigned_topics[domain_name].append(
                str(observation_by_id[assignment["id"]]["content_topic"])
            )
    insufficient_domains = [
        domain_name
        for domain_name, topics in assigned_topics.items()
        if len(topics) < MIN_TAXONOMY_SUPPORT
    ]
    if insufficient_domains:
        raise InterestSynthesisError(
            f"taxonomy domains lack {MIN_TAXONOMY_SUPPORT} classified evidence rows: "
            f"{insufficient_domains}"
        )
    compacted_topics = compact_assigned_topics(assigned_topics, observations, assignments)
    subtopic_payload = _call_local_qwen_structured_json(
        system_prompt=SUBTOPIC_SYSTEM_PROMPT,
        user_prompt=json.dumps(
            {
                "domains": taxonomy_for_prompt["domains"],
                "assigned_topics": compacted_topics,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        schema=_subtopic_schema(domain_names),
        selected_model=selected_model,
        api_base=api_base,
        timeout=timeout,
        urlopen_func=urlopen_func,
        debug_request_path=debug_root / "subtopics.request.json" if debug_root else None,
        debug_response_path=debug_root / "subtopics.response.txt" if debug_root else None,
    )
    domain_details = _validate_subtopics(subtopic_payload, domain_names)
    for item in taxonomy["domains"]:
        item.update(domain_details[item["name"]])
    return _assemble_interest_synthesis(taxonomy, assignments, observations)


def _clean_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not " ".join(value.split()).strip():
        raise InterestSynthesisError(f"{path} must be a non-empty string")
    return " ".join(value.split()).strip()


def _validate_evidence_ids(value: Any, valid_ids: set[str], path: str) -> list[str]:
    if not isinstance(value, list) or len(value) < 2:
        raise InterestSynthesisError(f"{path} must contain at least two evidence ids")
    evidence_ids = [_clean_string(item, f"{path}[]") for item in value]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise InterestSynthesisError(f"{path} contains duplicate evidence ids")
    unknown = sorted(set(evidence_ids) - valid_ids)
    if unknown:
        raise InterestSynthesisError(f"{path} contains unknown evidence ids: {unknown}")
    return evidence_ids


def validate_interest_synthesis(
    payload: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    required = {"summary", "domains", "content_preferences"}
    if set(payload) != required:
        raise InterestSynthesisError(f"interest synthesis keys must be exactly {sorted(required)}")
    valid_ids = {str(item["id"]) for item in observations}
    observation_by_id = {str(item["id"]): item for item in observations}
    total_watched_seconds = sum(
        int(item.get("watched_seconds") or 0) for item in observations
    )
    summary = _clean_string(payload.get("summary"), "summary")
    raw_domains = payload.get("domains")
    if not isinstance(raw_domains, list) or not 1 <= len(raw_domains) <= 12:
        raise InterestSynthesisError("domains must contain 1 to 12 items")
    domains: list[dict[str, Any]] = []
    domain_names: set[str] = set()
    for index, raw in enumerate(raw_domains):
        path = f"domains[{index}]"
        expected = {
            "name",
            "description",
            "subtopics",
            "keywords",
            "confidence",
            "evidence_ids",
        }
        allowed = expected | {
            "evidence_count",
            "watched_seconds",
            "watched_hours",
            "watch_count",
            "watch_time_share",
        }
        if not isinstance(raw, dict) or not expected <= set(raw) or set(raw) - allowed:
            raise InterestSynthesisError(f"{path} keys must be exactly {sorted(expected)}")
        name = _clean_string(raw.get("name"), f"{path}.name")
        name_key = normalize_topic(name)
        if name_key in domain_names:
            raise InterestSynthesisError(f"{path}.name duplicates another domain")
        domain_names.add(name_key)
        subtopics_value = raw.get("subtopics")
        if not isinstance(subtopics_value, list) or not 1 <= len(subtopics_value) <= 10:
            raise InterestSynthesisError(f"{path}.subtopics must contain 1 to 10 strings")
        subtopics = [_clean_string(item, f"{path}.subtopics[]") for item in subtopics_value]
        keywords_value = raw.get("keywords")
        if not isinstance(keywords_value, list) or not 3 <= len(keywords_value) <= 12:
            raise InterestSynthesisError(f"{path}.keywords must contain 3 to 12 strings")
        keywords = [_clean_string(item, f"{path}.keywords[]") for item in keywords_value]
        confidence = _clean_string(raw.get("confidence"), f"{path}.confidence")
        if confidence not in {"high", "medium", "low"}:
            raise InterestSynthesisError(f"{path}.confidence must be high, medium, or low")
        evidence_ids = _validate_evidence_ids(raw.get("evidence_ids"), valid_ids, f"{path}.evidence_ids")
        domains.append(
            {
                "name": name,
                "description": _clean_string(raw.get("description"), f"{path}.description"),
                "subtopics": subtopics,
                "keywords": keywords,
                "confidence": confidence,
                "evidence_count": len(evidence_ids),
                "evidence_ids": evidence_ids,
                **_engagement_metrics(
                    evidence_ids,
                    observation_by_id,
                    total_watched_seconds,
                ),
            }
        )

    raw_preferences = payload.get("content_preferences")
    if not isinstance(raw_preferences, list) or len(raw_preferences) > 8:
        raise InterestSynthesisError("content_preferences must be an array with at most 8 items")
    preferences: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_preferences):
        path = f"content_preferences[{index}]"
        expected = {"name", "description", "confidence", "evidence_ids"}
        allowed = expected | {
            "evidence_count",
            "watched_seconds",
            "watched_hours",
            "watch_count",
            "watch_time_share",
        }
        if not isinstance(raw, dict) or not expected <= set(raw) or set(raw) - allowed:
            raise InterestSynthesisError(f"{path} keys must be exactly {sorted(expected)}")
        confidence = _clean_string(raw.get("confidence"), f"{path}.confidence")
        if confidence not in {"high", "medium", "low"}:
            raise InterestSynthesisError(f"{path}.confidence must be high, medium, or low")
        evidence_ids = _validate_evidence_ids(raw.get("evidence_ids"), valid_ids, f"{path}.evidence_ids")
        preferences.append(
            {
                "name": _clean_string(raw.get("name"), f"{path}.name"),
                "description": _clean_string(raw.get("description"), f"{path}.description"),
                "confidence": confidence,
                "evidence_count": len(evidence_ids),
                "evidence_ids": evidence_ids,
                **_engagement_metrics(
                    evidence_ids,
                    observation_by_id,
                    total_watched_seconds,
                ),
            }
        )
    domains.sort(
        key=lambda item: (
            int(item["watched_seconds"]),
            int(item["watch_count"]),
            int(item["evidence_count"]),
        ),
        reverse=True,
    )
    preferences.sort(
        key=lambda item: (
            int(item["watched_seconds"]),
            int(item["watch_count"]),
            int(item["evidence_count"]),
        ),
        reverse=True,
    )
    domain_summary = "、".join(item["name"] for item in domains[:4])
    preference_summary = "、".join(item["name"] for item in preferences[:3])
    return {
        "summary": (
            f"按累计观看时间与观看量计算，你的主要兴趣集中在：{domain_summary}；"
            f"常偏好的内容形态是：{preference_summary}。"
        ) if domains else summary,
        "domains": domains,
        "content_preferences": preferences,
    }


SynthesisProvider = Callable[[list[dict[str, Any]]], dict[str, Any]]


def build_content_profile(
    events: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    *,
    synthesis_provider: SynthesisProvider | None = None,
) -> str:
    observations = build_synthesis_observations(events, reports)
    interest_evidence = select_interest_evidence(observations)
    source_counts: Counter[str] = Counter(
        str(report.get("_analysis_source") or "unknown") for report in reports
    )
    history = history_observations_by_url(events)
    history_watched_seconds = sum(
        int(item.get("watched_seconds") or 0) for item in history.values()
    )
    analyzed_watched_seconds = sum(
        int(item.get("watched_seconds") or 0) for item in interest_evidence
    )
    catalog_sha256 = hashlib.sha256(
        json.dumps(interest_evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if interest_evidence:
        provider = synthesis_provider or call_local_qwen_interest_synthesis
        synthesis = validate_interest_synthesis(provider(interest_evidence), interest_evidence)
        interest_profile = {
            "status": "ready",
            "basis": "watchbefore_reports_and_history_engagement",
            "summary": synthesis["summary"],
            "domains": synthesis["domains"],
            "content_preferences": synthesis["content_preferences"],
            "note": (
                "画像使用所有实际观看秒数大于 0、且已有字幕或音频转写分析的历史视频；"
                "领域按累计观看时间优先、观看量次优先排序。稍后再看记录已排除，"
                "不输出正向、负向或稍后再看主题，也不使用作者级推断。"
            ),
        }
    else:
        interest_profile = {
            "status": "insufficient_content_analysis",
            "basis": "watchbefore_reports_and_history_engagement",
            "summary": "没有同时具备历史观看事实和 WatchBefore 内容报告的样本，暂时不能生成兴趣画像。",
            "domains": [],
            "content_preferences": [],
            "note": "没有内容证据时不从标题、作者或稍后再看记录猜测兴趣。",
        }

    output = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "source": "watchbefore_interest_synthesis",
        "behavior_summary": {
            "total_events": len(events),
            "history_events": sum(1 for event in events if str(event.get("source") or "") == "history"),
            "watch_later_events_excluded": sum(
                1 for event in events if str(event.get("source") or "") == "watch_later"
            ),
            "analyzed_reports": len(reports),
            "matched_history_reports": len(observations),
            "interest_evidence_reports": len(interest_evidence),
            "history_positive_watch_videos": sum(
                1 for item in history.values() if int(item.get("watched_seconds") or 0) > 0
            ),
            "history_watched_seconds": history_watched_seconds,
            "history_watched_hours": round(history_watched_seconds / 3600, 2),
            "analyzed_watched_seconds": analyzed_watched_seconds,
            "analyzed_watched_hours": round(analyzed_watched_seconds / 3600, 2),
            "analyzed_watch_time_coverage": (
                round(analyzed_watched_seconds / history_watched_seconds, 6)
                if history_watched_seconds > 0
                else 0.0
            ),
            "normalized_payload_reports": source_counts.get("normalized_payload", 0),
            "local_extract_reports": source_counts.get("local_extract", 0),
            "chunked_local_extract_reports": source_counts.get("chunked_local_extract", 0),
        },
        "evidence_catalog_sha256": catalog_sha256,
        "interest_profile": interest_profile,
        "watch_strategy": {
            "current_rule": "报告先讲清视频内容；最终看不看由用户决定。",
            "scoring_rule": "信息密度 35% / 论据质量 35% / 话题兴趣 30%。",
        },
    }
    # JSON is a strict YAML 1.2 subset. Keeping one canonical encoding avoids a
    # second permissive parser while preserving the established seth.yaml path.
    return json.dumps(output, ensure_ascii=False, indent=2) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build one unified WatchBefore interest profile from analyzed watch history."
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--reports", nargs="+", required=True, help="Report JSON files, directories, or glob patterns.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--qwen-api-base")
    parser.add_argument("--qwen-model")
    parser.add_argument("--qwen-timeout", type=int, default=240)
    parser.add_argument("--debug-dir", type=Path, default=DEFAULT_DEBUG_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events = load_events(args.events)
    reports = load_reports(load_report_paths(args.reports))

    def provider(observations: list[dict[str, Any]]) -> dict[str, Any]:
        return call_local_qwen_interest_synthesis(
            observations,
            model_id=args.qwen_model,
            api_base=args.qwen_api_base,
            timeout=args.qwen_timeout,
            debug_dir=args.debug_dir,
        )

    try:
        profile = build_content_profile(events, reports, synthesis_provider=provider)
    except (InterestSynthesisError, LocalQwenError) as exc:
        raise SystemExit(f"interest_profile_failed: {exc}") from exc
    args.output.expanduser().parent.mkdir(parents=True, exist_ok=True)
    args.output.expanduser().write_text(profile, encoding="utf-8")
    parsed = json.loads(profile)
    summary = parsed["behavior_summary"]
    print(f"profile_path: {args.output.expanduser()}")
    print(f"events: {summary['total_events']}")
    print(f"reports: {summary['analyzed_reports']}")
    print(f"matched_history_reports: {summary['matched_history_reports']}")
    print(f"interest_domains: {len(parsed['interest_profile']['domains'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
