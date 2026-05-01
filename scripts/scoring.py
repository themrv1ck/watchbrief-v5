"""Deterministic replacement score rules for WatchBrief V5."""

from __future__ import annotations

import copy
from typing import Any


SCORING_FORMULA_VERSION = "watchbrief_v5.scoring_formula.v1"
SCORING_FORMULA = "information_density*0.2 + evidence_quality*0.3 + originality*0.2 + watch_value*0.3"
SCORE_TRACE_FINAL_SOURCE = "deterministic_formula"

STRUCTURED_TO_INTERNAL = {
    "信息密度": "information_density",
    "论据质量": "evidence_quality",
    "独创性": "originality",
    "观看性价比": "watch_value",
}

WEIGHTS = {
    "information_density": 0.2,
    "evidence_quality": 0.3,
    "originality": 0.2,
    "watch_value": 0.3,
}


def clamp_dimension(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("score dimension must be numeric")
    return min(10.0, max(0.0, float(value)))


def round_score(value: float) -> float:
    return round(float(value) + 1e-8, 1)


def extract_score_dimensions(structured_assessment: Any) -> dict[str, float]:
    if not isinstance(structured_assessment, dict):
        raise ValueError("structured_assessment must be an object")
    dimensions: dict[str, float] = {}
    for source_key, internal_key in STRUCTURED_TO_INTERNAL.items():
        dimensions[internal_key] = clamp_dimension(structured_assessment.get(source_key))
    return dimensions


def compute_replacement_score(dimensions: dict[str, float]) -> float:
    return round_score(sum(float(dimensions[key]) * WEIGHTS[key] for key in WEIGHTS))


def tag_for_score(score: float, current_tag: Any = "") -> str:
    tag = str(current_tag or "").strip()
    if score < 4.0:
        return tag if tag in {"报告足够替代", "不推荐观看"} else "报告足够替代"
    if score < 6.5:
        return tag if tag in {"报告基本可替代", "只建议跳看"} else "只建议跳看"
    if score < 8.5:
        return "值得补看"
    return "建议完整看"


def build_score_trace(payload: dict[str, Any]) -> dict[str, Any]:
    dimensions = extract_score_dimensions(payload.get("structured_assessment"))
    computed = compute_replacement_score(dimensions)
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
        "formula": SCORING_FORMULA,
        "computed_replacement_score": computed,
        "model_suggested_score": model_suggested_score,
        "final_score_source": SCORE_TRACE_FINAL_SOURCE,
        "warnings": warnings,
    }


def apply_deterministic_scoring(payload: dict[str, Any]) -> dict[str, Any]:
    adapted = copy.deepcopy(payload)
    score_trace = build_score_trace(adapted)
    final_score = float(score_trace["computed_replacement_score"])
    adapted["score_trace"] = score_trace
    adapted["replacement_score"] = final_score
    adapted["tag"] = tag_for_score(final_score, adapted.get("tag"))
    return adapted
