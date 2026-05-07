"""Deterministic video-value score rules for WatchBrief V5."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any


SCORING_FORMULA_VERSION = "watchbrief_v5.video_value_formula.v2"
SCORING_FORMULA = "information_density*0.2 + evidence_quality*0.3 + originality*0.2 + watch_value*0.3"
SCORE_TRACE_FINAL_SOURCE = "deterministic_formula"

STRUCTURED_TO_INTERNAL = {
    "信息密度": "information_density",
    "论据质量": "evidence_quality",
    "独创性": "originality",
    "观看性价比": "watch_value",
}

SCORE_DIMENSIONS = ("information_density", "evidence_quality", "originality", "watch_value")
WEIGHTS = {
    "information_density": 0.2,
    "evidence_quality": 0.3,
    "originality": 0.2,
    "watch_value": 0.3,
}
SCORING_PROFILES: dict[str, dict[str, float]] = {
    "standard": WEIGHTS,
    "information-first": {
        "information_density": 0.4,
        "evidence_quality": 0.25,
        "originality": 0.15,
        "watch_value": 0.2,
    },
    "evidence-first": {
        "information_density": 0.15,
        "evidence_quality": 0.45,
        "originality": 0.15,
        "watch_value": 0.25,
    },
    "originality-first": {
        "information_density": 0.15,
        "evidence_quality": 0.25,
        "originality": 0.4,
        "watch_value": 0.2,
    },
    "watch-value-first": {
        "information_density": 0.15,
        "evidence_quality": 0.2,
        "originality": 0.15,
        "watch_value": 0.5,
    },
}
FORMULA_RE = re.compile(
    r"^information_density\*(?P<information_density>\d+(?:\.\d+)?) \+ "
    r"evidence_quality\*(?P<evidence_quality>\d+(?:\.\d+)?) \+ "
    r"originality\*(?P<originality>\d+(?:\.\d+)?) \+ "
    r"watch_value\*(?P<watch_value>\d+(?:\.\d+)?)$"
)


def clamp_dimension(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("score dimension must be numeric")
    return min(10.0, max(0.0, float(value)))


def round_score(value: float) -> float:
    return round(float(value) + 1e-8, 1)


def normalize_scoring_weights(weights: dict[str, Any]) -> dict[str, float]:
    if not isinstance(weights, dict):
        raise ValueError("scoring weights must be an object")
    missing = [key for key in SCORE_DIMENSIONS if key not in weights]
    extra = [key for key in weights if key not in SCORE_DIMENSIONS]
    if missing or extra:
        raise ValueError(f"scoring weights must contain exactly {', '.join(SCORE_DIMENSIONS)}")
    parsed: dict[str, float] = {}
    for key in SCORE_DIMENSIONS:
        value = weights[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"scoring weight {key} must be numeric")
        if float(value) < 0:
            raise ValueError(f"scoring weight {key} must not be negative")
        parsed[key] = float(value)
    total = sum(parsed.values())
    if total <= 0:
        raise ValueError("scoring weights must sum to a positive number")
    normalized = {key: parsed[key] / total for key in SCORE_DIMENSIONS}
    return normalized


def parse_scoring_weights(value: str) -> dict[str, float]:
    """Parse `information_density=0.2,evidence_quality=0.3,...` into normalized weights."""
    text = str(value or "").strip()
    if not text:
        return dict(WEIGHTS)
    parts = [part.strip() for part in text.split(",") if part.strip()]
    parsed: dict[str, float] = {}
    for part in parts:
        if "=" not in part:
            raise ValueError("scoring weights must use key=value pairs separated by commas")
        key, raw_value = [item.strip() for item in part.split("=", 1)]
        if key not in SCORE_DIMENSIONS:
            raise ValueError(f"unsupported scoring weight key: {key}")
        try:
            parsed[key] = float(raw_value)
        except ValueError as exc:
            raise ValueError(f"scoring weight {key} must be numeric") from exc
    return normalize_scoring_weights(parsed)


def weights_for_profile(profile: str) -> dict[str, float]:
    key = str(profile or "standard").strip() or "standard"
    if key not in SCORING_PROFILES:
        raise ValueError(f"unsupported scoring profile: {key}")
    return dict(SCORING_PROFILES[key])


def formula_for_weights(weights: dict[str, Any]) -> str:
    normalized = normalize_scoring_weights(weights)
    return " + ".join(f"{key}*{normalized[key]:.6g}" for key in SCORE_DIMENSIONS)


def weights_from_formula(formula: str) -> dict[str, float]:
    text = str(formula or "").strip()
    if not text:
        return dict(WEIGHTS)
    if text == SCORING_FORMULA:
        return dict(WEIGHTS)
    match = FORMULA_RE.match(text)
    if not match:
        raise ValueError("score_trace.formula is not a supported deterministic scoring formula")
    return normalize_scoring_weights({key: float(match.group(key)) for key in SCORE_DIMENSIONS})


def formula_version_for_weights(weights: dict[str, Any]) -> str:
    normalized = normalize_scoring_weights(weights)
    if all(abs(normalized[key] - WEIGHTS[key]) < 1e-9 for key in SCORE_DIMENSIONS):
        return SCORING_FORMULA_VERSION
    material = json.dumps({key: round(normalized[key], 8) for key in SCORE_DIMENSIONS}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"watchbrief_v5.scoring_formula.custom.{digest}"


def scoring_weights_from_payload(payload: dict[str, Any]) -> dict[str, float]:
    score_trace = payload.get("score_trace")
    if isinstance(score_trace, dict) and score_trace.get("formula"):
        return weights_from_formula(str(score_trace.get("formula") or ""))
    return dict(WEIGHTS)


def extract_score_dimensions(structured_assessment: Any) -> dict[str, float]:
    if not isinstance(structured_assessment, dict):
        raise ValueError("structured_assessment must be an object")
    dimensions: dict[str, float] = {}
    for source_key, internal_key in STRUCTURED_TO_INTERNAL.items():
        dimensions[internal_key] = clamp_dimension(structured_assessment.get(source_key))
    return dimensions


def compute_replacement_score(dimensions: dict[str, float], weights: dict[str, Any] | None = None) -> float:
    effective_weights = normalize_scoring_weights(weights or WEIGHTS)
    return round_score(sum(float(dimensions[key]) * effective_weights[key] for key in SCORE_DIMENSIONS))


def tag_for_score(score: float, current_tag: Any = "") -> str:
    tag = str(current_tag or "").strip()
    if score < 5.0:
        return "不推荐观看"
    if score <= 6.5:
        return "只建议跳看"
    if score <= 8.5:
        return "值得补看"
    return "建议完整看"


def build_score_trace(payload: dict[str, Any], weights: dict[str, Any] | None = None) -> dict[str, Any]:
    dimensions = extract_score_dimensions(payload.get("structured_assessment"))
    effective_weights = normalize_scoring_weights(weights or scoring_weights_from_payload(payload))
    formula = formula_for_weights(effective_weights)
    computed = compute_replacement_score(dimensions, effective_weights)
    existing_trace = payload.get("score_trace")
    model_score = existing_trace.get("model_suggested_score") if isinstance(existing_trace, dict) else payload.get("replacement_score")
    model_suggested_score = round_score(float(model_score)) if isinstance(model_score, (int, float)) and not isinstance(model_score, bool) else None
    warnings: list[str] = []
    if model_suggested_score is not None and abs(model_suggested_score - computed) > 0.5:
        warnings.append(
            f"model_suggested_score differs from deterministic score by {abs(model_suggested_score - computed):.1f}"
        )
    return {
        "information_density": round_score(dimensions["information_density"]),
        "evidence_quality": round_score(dimensions["evidence_quality"]),
        "originality": round_score(dimensions["originality"]),
        "watch_value": round_score(dimensions["watch_value"]),
        "formula": formula,
        "score_semantics": "video_overall_value",
        "computed_replacement_score": computed,
        "model_suggested_score": model_suggested_score,
        "final_score_source": SCORE_TRACE_FINAL_SOURCE,
        "warnings": warnings,
    }


def apply_deterministic_scoring(payload: dict[str, Any], weights: dict[str, Any] | None = None) -> dict[str, Any]:
    adapted = copy.deepcopy(payload)
    effective_weights = normalize_scoring_weights(weights or scoring_weights_from_payload(adapted))
    score_trace = build_score_trace(adapted, effective_weights)
    final_score = float(score_trace["computed_replacement_score"])
    adapted["score_trace"] = score_trace
    adapted["replacement_score"] = final_score
    adapted["tag"] = tag_for_score(final_score, adapted.get("tag"))
    adapted["scoring_formula_version"] = formula_version_for_weights(effective_weights)
    return adapted
