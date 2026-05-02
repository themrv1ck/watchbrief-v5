#!/usr/bin/env python3
"""Codex review prompt assembly, response parsing, and explicit model adapter."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    from .prompts import ANALYZER_SYSTEM_PROMPT, NORMALIZED_PAYLOAD_CONTRACT, WATCHBRIEF_VERSION, build_review_user_prompt
    from ..scoring import SCORING_FORMULA_VERSION, apply_deterministic_scoring
    from ..stability import prompt_fingerprint
    from ..validator import WatchBriefValidationError, validate_normalized_report_payload
except ImportError:  # pragma: no cover - direct script execution
    ANALYZER_DIR = Path(__file__).resolve().parent
    SCRIPTS_DIR = Path(__file__).resolve().parents[1]
    for path in (ANALYZER_DIR, SCRIPTS_DIR):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from prompts import ANALYZER_SYSTEM_PROMPT, NORMALIZED_PAYLOAD_CONTRACT, WATCHBRIEF_VERSION, build_review_user_prompt
    from scoring import SCORING_FORMULA_VERSION, apply_deterministic_scoring
    from stability import prompt_fingerprint
    from validator import WatchBriefValidationError, validate_normalized_report_payload


ROOT = Path(__file__).resolve().parents[2]
SINGLE_VIDEO_SCHEMA_PATH = ROOT / "schemas" / "single_video_report.schema.json"
STRUCTURED_ASSESSMENT_REQUIRED_KEYS = ("信息密度", "论据质量", "独创性", "观看性价比")
CODEX_REVIEW_PROMPT_VERSION = "watchbrief_v5.codex_review_prompt.v3"


class CodexReviewError(ValueError):
    def __init__(self, message: str, reason_code: str = "review_error") -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass
class CodexReviewCallError(RuntimeError):
    reason_code: str
    message: str
    status_code: Optional[int] = None

    def __str__(self) -> str:
        return f"{self.reason_code}: {self.message}"


def build_review_request(local_extract_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(local_extract_payload, dict):
        raise CodexReviewError("local_extract_payload must be an object")
    if local_extract_payload.get("analysis_boundary", {}).get("does_not_generate_final_report_fields") is not True:
        raise CodexReviewError("local_extract_payload must declare final-report field boundary")

    user_prompt = build_review_user_prompt(local_extract_payload)
    codex_fingerprint = prompt_fingerprint(
        CODEX_REVIEW_PROMPT_VERSION,
        ANALYZER_SYSTEM_PROMPT,
        NORMALIZED_PAYLOAD_CONTRACT,
        CODEX_CLI_JSON_CONTRACT,
    )
    return {
        "request_version": "watchbrief_v5.codex_review_request.v1",
        "execution": "manual_or_later_phase_only",
        "model_call_allowed": False,
        "codex_prompt_version": CODEX_REVIEW_PROMPT_VERSION,
        "codex_prompt_fingerprint": codex_fingerprint,
        "stability_metadata": {
            "transcript_hash": str(local_extract_payload.get("transcript_hash") or ""),
            "qwen_model_id": str(local_extract_payload.get("qwen_extract", {}).get("_qwen_model") or ""),
            "qwen_prompt_version": str(local_extract_payload.get("analysis_boundary", {}).get("qwen_prompt_version") or ""),
            "qwen_prompt_fingerprint": str(local_extract_payload.get("analysis_boundary", {}).get("qwen_prompt_fingerprint") or ""),
            "codex_prompt_version": CODEX_REVIEW_PROMPT_VERSION,
            "codex_prompt_fingerprint": codex_fingerprint,
            "scoring_formula_version": SCORING_FORMULA_VERSION,
            "watchbrief_version": WATCHBRIEF_VERSION,
        },
        "messages": [
            {"role": "system", "content": ANALYZER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "expected_output": "normalized_report_payload",
        "contract": copy.deepcopy(NORMALIZED_PAYLOAD_CONTRACT),
    }


def _load_single_video_schema() -> dict[str, Any]:
    return json.loads(SINGLE_VIDEO_SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return True


def _validate_schema_node(value: Any, schema: dict[str, Any], path: str, issues: list[str]) -> None:
    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _schema_type_matches(value, expected_type):
        issues.append(f"{path} must be {expected_type}")
        return

    if "enum" in schema and value not in schema["enum"]:
        issues.append(f"{path} must be one of {schema['enum']}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            issues.append(f"{path} is shorter than minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            issues.append(f"{path} is longer than maxLength")
        if "pattern" in schema:
            import re
            if re.search(str(schema["pattern"]), value) is None:
                issues.append(f"{path} does not match required pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append(f"{path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            issues.append(f"{path} is above maximum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for field in required:
            if field not in value:
                issues.append(f"{path}.{field} is required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                issues.append(f"{path} has unexpected keys: {extras}")
        for key, child_value in value.items():
            if key in properties:
                _validate_schema_node(child_value, properties[key], f"{path}.{key}", issues)

    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            issues.append(f"{path} has too few items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            issues.append(f"{path} has too many items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_node(item, item_schema, f"{path}[{index}]", issues)


def validate_single_video_schema(payload: dict[str, Any]) -> dict[str, Any]:
    issues = collect_single_video_schema_errors(payload)
    if issues:
        raise CodexReviewError("; ".join(issues), reason_code="schema_invalid")
    return copy.deepcopy(payload)


def collect_single_video_schema_errors(payload: dict[str, Any]) -> list[str]:
    schema = _load_single_video_schema()
    issues: list[str] = []
    _validate_schema_node(payload, schema, "$", issues)
    return issues


def collect_live_review_contract_errors(payload: dict[str, Any]) -> list[str]:
    """Extra requirements for real model output that downstream list pages need."""
    structured = payload.get("structured_assessment")
    if not isinstance(structured, dict):
        return ["$.structured_assessment must be an object"]
    issues: list[str] = []
    for key in STRUCTURED_ASSESSMENT_REQUIRED_KEYS:
        value = structured.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(f"$.structured_assessment.{key} must be number")
        elif not 0 <= float(value) <= 10:
            issues.append(f"$.structured_assessment.{key} must be between 0 and 10")
    return issues


def extract_json_object_text(raw_response: Any) -> str:
    if isinstance(raw_response, dict):
        return json.dumps(raw_response, ensure_ascii=False)
    if not isinstance(raw_response, str):
        raise CodexReviewError("review response must be JSON string or object", reason_code="invalid_json")
    text = raw_response.strip()
    if not text:
        raise CodexReviewError("review response is empty", reason_code="invalid_json")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return text[index:index + end]
        raise CodexReviewError("review response must contain one JSON object", reason_code="invalid_json")
    if not isinstance(parsed, dict):
        raise CodexReviewError("review response JSON must be an object", reason_code="invalid_json")
    return text


def load_review_json_object(raw_response: Any) -> tuple[str, dict[str, Any]]:
    extracted = extract_json_object_text(raw_response)
    try:
        parsed = json.loads(extracted)
    except json.JSONDecodeError as exc:
        raise CodexReviewError(f"review response must be JSON: {exc}", reason_code="invalid_json") from exc
    if not isinstance(parsed, dict):
        raise CodexReviewError("review response JSON must be an object", reason_code="invalid_json")
    return extracted, parsed


def stringify_basis_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        return "；".join(f"{key}: {val}" for key, val in value.items())
    if isinstance(value, list):
        return "；".join(str(item) for item in value)
    return str(value)


def pre_schema_adapter(payload: dict[str, Any]) -> dict[str, Any]:
    adapted = copy.deepcopy(payload)

    score = adapted.get("replacement_score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        if 10 < score <= 100:
            adapted["replacement_score"] = round(float(score) / 10, 1)
        elif 0 <= score <= 10:
            adapted["replacement_score"] = round(float(score), 1)

    arrow_chain = adapted.get("arrow_chain")
    if isinstance(arrow_chain, str) and "→" in arrow_chain:
        adapted["arrow_chain"] = [part.strip() for part in arrow_chain.split("→") if part.strip()]

    watch_segments = adapted.get("watch_segments")
    if isinstance(watch_segments, list):
        fixed_segments = []
        for segment in watch_segments:
            if not isinstance(segment, dict):
                fixed_segments.append(segment)
                continue
            fixed_segment = copy.deepcopy(segment)
            if "title" not in fixed_segment and "label" in fixed_segment:
                fixed_segment["title"] = str(fixed_segment["label"])
            fixed_segment.pop("label", None)
            fixed_segments.append(fixed_segment)
        adapted["watch_segments"] = fixed_segments

    only_one_segment = adapted.get("only_one_segment")
    if isinstance(only_one_segment, dict):
        start = str(only_one_segment.get("start") or "").strip()
        end = str(only_one_segment.get("end") or "").strip()
        reason = str(only_one_segment.get("reason") or "").strip()
        if start and end:
            suffix = reason or "这一段已经覆盖全片核心。"
            adapted["only_one_segment"] = f"只选一段：{start} | {end}。{suffix}"

    score_basis = adapted.get("score_basis")
    if isinstance(score_basis, dict):
        fixed_basis = copy.deepcopy(score_basis)
        for key in ("information_density", "evidence_quality", "originality", "watch_value"):
            if key in fixed_basis and not isinstance(fixed_basis[key], str):
                fixed_basis[key] = stringify_basis_value(fixed_basis[key])
        adapted["score_basis"] = fixed_basis

    if "content_caveat" not in adapted:
        adapted["content_caveat"] = ""

    return adapted


def apply_stability_metadata(payload: dict[str, Any], metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if not metadata:
        return copy.deepcopy(payload)
    adapted = copy.deepcopy(payload)
    for key in (
        "transcript_hash",
        "qwen_model_id",
        "qwen_prompt_version",
        "qwen_prompt_fingerprint",
        "codex_model",
        "codex_prompt_version",
        "codex_prompt_fingerprint",
        "scoring_formula_version",
        "watchbrief_version",
    ):
        value = metadata.get(key)
        if value not in (None, ""):
            adapted[key] = str(value)
    return adapted


def deterministic_report_adapter(payload: dict[str, Any], metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        return apply_deterministic_scoring(apply_stability_metadata(payload, metadata))
    except ValueError as exc:
        raise CodexReviewError(str(exc), reason_code="schema_invalid") from exc


def parse_review_response(raw_response: Any, *, apply_adapter: bool = False, stability_metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if isinstance(raw_response, str):
        try:
            _, parsed = load_review_json_object(raw_response)
        except json.JSONDecodeError as exc:
            raise CodexReviewError(f"review response must be JSON: {exc}", reason_code="invalid_json") from exc
    elif isinstance(raw_response, dict):
        parsed = copy.deepcopy(raw_response)
    else:
        raise CodexReviewError("review response must be JSON string or object", reason_code="invalid_json")
    if apply_adapter:
        parsed = pre_schema_adapter(parsed)
    parsed = deterministic_report_adapter(parsed, stability_metadata)
    validate_single_video_schema(parsed)
    return validate_normalized_report_payload(parsed)


def extract_response_text(response_payload: dict[str, Any]) -> str:
    output_text = response_payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    chunks: list[str] = []
    for item in response_payload.get("output", []) if isinstance(response_payload.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    joined = "".join(chunks).strip()
    if joined:
        return joined
    raise CodexReviewCallError("empty_response", "model response did not contain text")


def normalize_review_provider(review_provider: str) -> str:
    if review_provider == "codex":
        return "codex-cli"
    return review_provider


def resolve_codex_home(
    *,
    codex_home: Optional[str] = None,
    codex_home_root: Optional[str] = None,
    codex_account: Optional[str] = None,
) -> Optional[Path]:
    if codex_home:
        return Path(codex_home).expanduser()
    if codex_home_root:
        root = Path(codex_home_root).expanduser()
        return root / codex_account if codex_account else root
    return None


def build_codex_cli_env(
    *,
    codex_home: Optional[str] = None,
    codex_home_root: Optional[str] = None,
    codex_account: Optional[str] = None,
    base_env: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    resolved_home = resolve_codex_home(
        codex_home=codex_home,
        codex_home_root=codex_home_root,
        codex_account=codex_account,
    )
    if resolved_home is not None:
        if not resolved_home.exists():
            raise CodexReviewCallError("auth_failed", f"CODEX_HOME does not exist: {resolved_home}")
        env["CODEX_HOME"] = str(resolved_home)
    return env


CODEX_CLI_JSON_CONTRACT = """Codex CLI JSON output hard contract:
- You must output exactly one JSON object.
- Do not output Markdown.
- Do not output explanations.
- Do not wrap the JSON in code fences.
- The JSON must fully conform to the WatchBrief V5 schema.

Field rules:
- replacement_score: number, 0 <= replacement_score <= 10, keep one decimal place. It is only your model-suggested reference; final output will be recomputed deterministically from structured_assessment.
- tag: string; must be exactly one of: 报告足够替代, 报告基本可替代, 只建议跳看, 值得补看, 建议完整看, 不推荐观看, 解析不足.
- one_line_brief: string; must start with “这期视频主要讲：”.
- watch_verdict: string; only says whether the report is enough, whether to watch the original video, and where to watch.
- watch_verdict must contain either an exact pipe time range copied from the primary watch segment, such as `20:44 | 32:34`, or a clear no-watch decision using 不必看 / 不用看 / 无需看 / 不推荐观看.
- If watch_segments contains a primary segment, copy that exact primary `start | end` into watch_verdict unless the decision is no-watch. Do not write vague phrases like “首选片段” without the actual time range.
- Time ranges in watch_verdict must use `start | end`. Never use `start - end`, `start 到 end`, `start 至 end`, or any non-pipe separator.
- highest_compression: string.
- path_table: object with problem, mechanism, turning_point, landing, all strings.
- arrow_chain: array of strings, 5 to 7 nodes. Never output a single string. Never output an array of objects.
- final_conclusion: string; only thematic conclusion. Never include viewing advice, score reasons, or content caveats.
- content_caveat: string. If none, output an empty string. Do not omit it.
- watch_segments: array. Each item must be an object with priority, start, end, title, reason.
- watch_segments[].priority: one of primary, optional, backup.
- watch_segments[].start and end: MM:SS strings.
- watch_segments[].title: required. primary title starts with 首选片段：, optional with 可选补看：, backup with 备选：.
- watch_segments: exactly one primary, at most one optional.
- watch_segments[].label is forbidden. Use title, not label.
- only_one_segment: string. It must match the primary start/end. Example: 只选一段：00:00 | 00:05。这一段已经覆盖全片核心。
- score_basis: object with four string fields: information_density, evidence_quality, originality, watch_value.
- score_basis values must be strings. Never output numbers, objects, arrays, or omit these fields.
- structured_assessment: object required for Watch Order pages with numeric fields 信息密度, 论据质量, 独创性, 观看性价比, each from 0 to 10.
- structured_assessment drives the final deterministic replacement_score using: information_density*0.2 + evidence_quality*0.3 + originality*0.2 + watch_value*0.3.
- score_trace is generated by deterministic validation. You may omit it; if you output it, it will be overwritten.
- transcript_hash, qwen_model_id, qwen_prompt_version, qwen_prompt_fingerprint, codex_model, codex_prompt_version, codex_prompt_fingerprint, scoring_formula_version, and watchbrief_version are generated by deterministic validation. You may omit them; if you output them, they will be overwritten.
- tag must be semantically consistent with the final score bands: below 4.0 means 报告足够替代 or 不推荐观看; 4.0-6.4 means 报告基本可替代 or 只建议跳看; 6.5-8.4 means 值得补看; 8.5+ means 建议完整看.
- Use qwen_extract.important_terms and qwen_extract.corrected_terms for names and terms. Do not guess person names independently. If uncertain, write 疑似某某 instead of a wrong name.
- confidence_note: string.
"""


def build_codex_cli_prompt(review_request: dict[str, Any]) -> str:
    prompt_override = review_request.get("_prompt_override")
    if isinstance(prompt_override, str) and prompt_override.strip():
        return prompt_override
    system = str(review_request["messages"][0]["content"])
    user = str(review_request["messages"][1]["content"])
    return (
        f"{system}\n\n"
        f"{user}\n\n"
        f"{CODEX_CLI_JSON_CONTRACT}\n"
        "Return only one valid JSON object that conforms to the requested normalized_report_payload contract."
    )


def build_codex_cli_retry_prompt(
    review_request: dict[str, Any],
    *,
    previous_json: str,
    schema_errors: list[str],
) -> str:
    system = str(review_request["messages"][0]["content"])
    errors = "\n".join(f"- {error}" for error in schema_errors)
    watch_verdict_hint = ""
    if any("watch_verdict" in error for error in schema_errors):
        watch_verdict_hint = (
            "\nwatch_verdict targeted correction:\n"
            "- If watch_verdict is the only invalid field, preserve every other field and only fix watch_verdict.\n"
            "- watch_verdict must include the exact primary watch_segments start/end as `start | end`, for example `20:44 | 32:34`.\n"
            "- If the judgment is no-watch, watch_verdict must explicitly say 原视频不必看 / 原视频不用看 / 原视频无需看 / 不推荐观看.\n"
            "- Do not write a vague phrase such as “首选片段” without the actual `start | end` time range.\n"
            "- Do not use `start - end`, `start 到 end`, or `start 至 end`.\n"
        )
    return (
        f"{system}\n\n"
        "Your previous JSON did not pass the WatchBrief V5 schema.\n"
        "只修 JSON，不改变视频判断，输出完整 JSON object。\n"
        "Only fix the JSON structure and field types. Do not change the video judgment.\n"
        "Output the complete JSON object again. Do not output Markdown or explanations.\n\n"
        "Schema error summary:\n"
        f"{errors}\n\n"
        "Required corrections include:\n"
        "- replacement_score must be between 0 and 10.\n"
        "- arrow_chain must be an array of strings.\n"
        "- watch_verdict must contain an exact `start | end` pipe time range copied from primary watch_segments, or a clear no-watch decision.\n"
        "- watch_segments[].title is required.\n"
        "- watch_segments[].label is not allowed.\n"
        "- only_one_segment must be a string.\n"
        "- score_basis.information_density must be a string.\n"
        "- score_basis.evidence_quality must be a string.\n"
        "- score_basis.originality must be a string.\n"
        "- score_basis.watch_value must be a string.\n\n"
        "- structured_assessment must be an object with 信息密度, 论据质量, 独创性, 观看性价比 numeric fields from 0 to 10.\n\n"
        f"{watch_verdict_hint}\n"
        f"{CODEX_CLI_JSON_CONTRACT}\n"
        "Previous JSON:\n"
        f"{previous_json}\n"
    )


def build_codex_cli_command(
    *,
    codex_bin: str = "codex",
    model: str = "gpt-5.5",
    output_last_message: Path,
    cwd: Path = ROOT,
) -> list[str]:
    return [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "-m",
        model,
        "-C",
        str(cwd),
        "-s",
        "read-only",
        "-o",
        str(output_last_message),
        "-",
    ]


def _combined_process_output(completed: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()


def _classify_codex_cli_failure(message: str, *, default: str = "model_error") -> str:
    lowered = message.lower()
    if "not logged in" in lowered or "login" in lowered and "codex login" in lowered:
        return "codex_not_logged_in"
    if "auth" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
        return "auth_failed"
    if "quota" in lowered or "rate limit" in lowered or "429" in lowered:
        return "quota_limited"
    return default


def ensure_codex_cli_ready(
    *,
    codex_bin: str = "codex",
    env: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> None:
    resolved = shutil.which(codex_bin)
    if resolved is None:
        raise CodexReviewCallError("codex_cli_missing", f"codex command not found: {codex_bin}")
    try:
        completed = subprocess.run(
            [resolved, "login", "status"],
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexReviewCallError("timeout", "codex login status timed out") from exc
    if completed.returncode != 0:
        output = _combined_process_output(completed) or "Codex CLI is not logged in"
        reason = _classify_codex_cli_failure(output, default="codex_not_logged_in")
        raise CodexReviewCallError(reason, output)


def call_codex_cli(
    review_request: dict[str, Any],
    *,
    model: str,
    timeout: int,
    codex_home: Optional[str] = None,
    codex_home_root: Optional[str] = None,
    codex_account: Optional[str] = None,
    codex_bin: str = "codex",
) -> str:
    env = build_codex_cli_env(
        codex_home=codex_home,
        codex_home_root=codex_home_root,
        codex_account=codex_account,
    )
    ensure_codex_cli_ready(codex_bin=codex_bin, env=env, timeout=min(timeout, 30))
    resolved_bin = shutil.which(codex_bin)
    if resolved_bin is None:
        raise CodexReviewCallError("codex_cli_missing", f"codex command not found: {codex_bin}")

    output_path = Path(os.environ.get("TMPDIR", "/tmp")) / f"watchbrief_codex_review_{os.getpid()}.json"
    command = build_codex_cli_command(
        codex_bin=resolved_bin,
        model=model,
        output_last_message=output_path,
    )
    try:
        completed = subprocess.run(
            command,
            input=build_codex_cli_prompt(review_request),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexReviewCallError("timeout", "codex exec timed out") from exc

    if completed.returncode != 0:
        output = _combined_process_output(completed) or f"codex exec failed with exit code {completed.returncode}"
        raise CodexReviewCallError(_classify_codex_cli_failure(output), output)

    if output_path.exists():
        text = output_path.read_text(encoding="utf-8").strip()
        output_path.unlink(missing_ok=True)
    else:
        text = (completed.stdout or "").strip()
    if not text:
        raise CodexReviewCallError("empty_response", "codex exec returned no final message")
    return text


def write_debug_text(debug_dir: Optional[Path], name: str, content: str) -> None:
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / name).write_text(content, encoding="utf-8")


def write_debug_json(debug_dir: Optional[Path], name: str, payload: Any) -> None:
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_debug_section(sections: list[str], title: str, body: str) -> str:
    sections.append(f"===== {title} =====\n{body}")
    return "\n\n".join(sections) + "\n"


def run_codex_review(
    local_extract_payload: dict[str, Any],
    *,
    enable_codex_review: bool = False,
    review_provider: str = "manual",
    model: str = "gpt-5.5",
    codex_model: Optional[str] = None,
    codex_home: Optional[str] = None,
    codex_home_root: Optional[str] = None,
    codex_account: Optional[str] = None,
    codex_bin: str = "codex",
    timeout: int = 120,
    transport: Any = call_codex_cli,
    max_schema_retries: int = 2,
    debug_dir: Optional[Path] = None,
) -> dict[str, Any]:
    provider = normalize_review_provider(review_provider)
    if not enable_codex_review or provider != "codex-cli":
        raise CodexReviewCallError("model_disabled", "explicit codex review enablement is required")

    review_request = build_review_request(local_extract_payload)
    stability_metadata = copy.deepcopy(review_request.get("stability_metadata") or {})
    stability_metadata["codex_model"] = str(codex_model or model)
    raw_sections: list[str] = []
    retry_sections: list[str] = []
    retry_prompt: Optional[str] = None
    last_schema_errors: list[str] = []

    for attempt in range(max_schema_retries + 1):
        request_for_attempt = copy.deepcopy(review_request)
        if retry_prompt:
            request_for_attempt["_prompt_override"] = retry_prompt
            write_debug_text(debug_dir, "retry_prompts.txt", append_debug_section(retry_sections, f"attempt {attempt + 1}", retry_prompt))

        raw_text = transport(
            request_for_attempt,
            model=codex_model or model,
            timeout=timeout,
            codex_home=codex_home,
            codex_home_root=codex_home_root,
            codex_account=codex_account,
            codex_bin=codex_bin,
        )
        if isinstance(raw_text, str) and not raw_text.strip():
            raise CodexReviewCallError("empty_response", "codex review returned no JSON text")
        raw_text_str = raw_text if isinstance(raw_text, str) else json.dumps(raw_text, ensure_ascii=False)
        write_debug_text(debug_dir, "codex_raw_response.txt", append_debug_section(raw_sections, f"attempt {attempt + 1}", raw_text_str))

        try:
            extracted_json, parsed = load_review_json_object(raw_text)
        except CodexReviewError as exc:
            if exc.reason_code == "invalid_json":
                raise CodexReviewCallError("invalid_json", str(exc)) from exc
            raise

        write_debug_text(debug_dir, "codex_extracted.json", extracted_json + "\n")
        try:
            adapted = deterministic_report_adapter(pre_schema_adapter(parsed), stability_metadata)
        except CodexReviewError as exc:
            if exc.reason_code != "schema_invalid":
                raise
            schema_errors = [str(exc)]
            last_schema_errors = schema_errors
            write_debug_json(debug_dir, "schema_errors.json", {"attempt": attempt + 1, "errors": schema_errors})
            if attempt < max_schema_retries:
                retry_prompt = build_codex_cli_retry_prompt(
                    review_request,
                    previous_json=json.dumps(parsed, ensure_ascii=False, indent=2),
                    schema_errors=schema_errors,
                )
                continue
            raise CodexReviewCallError("schema_invalid", "; ".join(last_schema_errors)) from exc
        write_debug_json(debug_dir, "codex_adapted.json", adapted)

        schema_errors = collect_single_video_schema_errors(adapted)
        schema_errors.extend(collect_live_review_contract_errors(adapted))
        if schema_errors:
            last_schema_errors = schema_errors
            write_debug_json(debug_dir, "schema_errors.json", {"attempt": attempt + 1, "errors": schema_errors})
            if attempt < max_schema_retries:
                retry_prompt = build_codex_cli_retry_prompt(
                    review_request,
                    previous_json=json.dumps(adapted, ensure_ascii=False, indent=2),
                    schema_errors=schema_errors,
                )
                continue
            raise CodexReviewCallError("schema_invalid", "; ".join(last_schema_errors))

        try:
            normalized = validate_normalized_report_payload(adapted)
        except WatchBriefValidationError as exc:
            validation_errors = [f"{issue.path}: {issue.message}" for issue in exc.issues]
            last_schema_errors = validation_errors
            write_debug_json(debug_dir, "schema_errors.json", {"attempt": attempt + 1, "errors": validation_errors})
            if attempt < max_schema_retries:
                retry_prompt = build_codex_cli_retry_prompt(
                    review_request,
                    previous_json=json.dumps(adapted, ensure_ascii=False, indent=2),
                    schema_errors=validation_errors,
                )
                continue
            raise CodexReviewCallError("validator_failed", "; ".join(validation_errors)) from exc

        write_debug_json(debug_dir, "normalized_payload.json", normalized)
        return normalized

    raise CodexReviewCallError("schema_invalid", "; ".join(last_schema_errors) or "schema validation failed")


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CodexReviewError("input JSON must be an object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Build, validate, or explicitly run WatchBrief V5 Codex review.")
    parser.add_argument("--local-extract", type=Path, help="Local extract JSON to turn into a review request.")
    parser.add_argument("--review-response", type=Path, help="Mock review response JSON to validate.")
    parser.add_argument("--review-provider", choices=("manual", "mock", "codex", "codex-cli"), default="manual")
    parser.add_argument("--enable-codex-review", action="store_true")
    parser.add_argument("--dry-run-review-request", action="store_true", help="Write review_request.json without calling a model.")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--codex-model", help="Codex CLI model. Overrides --model when set.")
    parser.add_argument("--codex-home", help="Explicit CODEX_HOME directory for Codex CLI login state.")
    parser.add_argument("--codex-home-root", help="Root containing Codex account directories.")
    parser.add_argument("--codex-account", help="Account folder under --codex-home-root.")
    parser.add_argument("--debug-dir", type=Path, help="Directory for Codex review debug artifacts.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.dry_run_review_request:
        if not args.local_extract or args.review_response:
            raise CodexReviewError("--dry-run-review-request requires --local-extract only")
        output = build_review_request(_load_json(args.local_extract))
    elif args.review_provider in {"codex", "codex-cli"} or args.enable_codex_review:
        if not args.local_extract or args.review_response:
            raise CodexReviewError("real codex review requires --local-extract only")
        output = run_codex_review(
            _load_json(args.local_extract),
            enable_codex_review=args.enable_codex_review,
            review_provider=args.review_provider,
            model=args.model,
            codex_model=args.codex_model,
            codex_home=args.codex_home,
            codex_home_root=args.codex_home_root,
            codex_account=args.codex_account,
            debug_dir=args.debug_dir,
            timeout=args.timeout,
        )
    elif bool(args.local_extract) == bool(args.review_response):
        raise CodexReviewError("provide exactly one of --local-extract or --review-response")
    else:
        if args.local_extract:
            output = build_review_request(_load_json(args.local_extract))
        else:
            output = parse_review_response(_load_json(args.review_response))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
