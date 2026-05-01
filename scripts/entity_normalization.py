"""Deterministic entity normalization for WatchBrief V5."""

from __future__ import annotations

import copy
from typing import Any


ENTITY_CANONICALS = {
    "叔本华 / Schopenhauer": (
        "叔本华",
        "叔奔华",
        "叔本化",
        "叔本花",
        "舒本华",
        "Schopenhauer",
        "Arthur Schopenhauer",
    ),
    "尼采 / Nietzsche": (
        "尼采",
        "尼彩",
        "尼才",
        "Nietzsche",
        "Friedrich Nietzsche",
    ),
    "柏拉图 / Plato": (
        "柏拉图",
        "柏腊图",
        "Plato",
    ),
    "萨特 / Sartre": (
        "萨特",
        "沙特",
        "Sartre",
        "Jean-Paul Sartre",
    ),
    "阿兰·德波顿 / Alain de Botton": (
        "阿兰·德波顿",
        "阿兰德波顿",
        "阿兰·德波东",
        "阿兰德波东",
        "Alain de Botton",
    ),
}


def clean_entity_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_entity_text(value: Any) -> tuple[str, list[str], list[str]]:
    text = clean_entity_text(value)
    corrections: list[str] = []
    important_terms: list[str] = []
    for canonical, aliases in ENTITY_CANONICALS.items():
        original_text = text
        if canonical in original_text:
            important_terms.append(canonical)
            continue
        found = False
        for alias in aliases:
            if alias and alias in original_text:
                found = True
                if alias != canonical and alias != canonical.split(" / ")[0] and alias != canonical.split(" / ")[1]:
                    text = text.replace(alias, canonical)
                    corrections.append(f"{alias} -> {canonical}")
                elif alias != canonical:
                    text = text.replace(alias, canonical)
        if found:
            important_terms.append(canonical)
    return text, important_terms, corrections


def normalize_entity_list(values: Any) -> tuple[list[str], list[str], list[str]]:
    if not isinstance(values, list):
        return [], [], []
    normalized: list[str] = []
    important_terms: list[str] = []
    corrections: list[str] = []
    for item in values:
        text, found_terms, found_corrections = normalize_entity_text(item)
        if text:
            normalized.append(text)
        important_terms.extend(found_terms)
        corrections.extend(found_corrections)
    return normalized, important_terms, corrections


def normalize_important_terms(values: Any) -> tuple[list[str], list[str]]:
    if not isinstance(values, list):
        return [], []
    normalized: list[str] = []
    corrections: list[str] = []
    for item in values:
        text, found_terms, found_corrections = normalize_entity_text(item)
        normalized.extend(found_terms or ([text] if text else []))
        corrections.extend(found_corrections)
    return normalized, corrections


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean_entity_text(value)
        if "->" in cleaned:
            left, right = [part.strip() for part in cleaned.split("->", 1)]
            if left == right:
                continue
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def normalize_qwen_extract_entities(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(payload)
    important_terms, corrections_from_terms = normalize_important_terms(normalized.get("important_terms"))
    corrected_terms, found_from_corrections, corrections_from_corrections = normalize_entity_list(normalized.get("corrected_terms"))
    normalized["important_terms"] = dedupe_preserve_order(important_terms + found_from_corrections)
    normalized["corrected_terms"] = dedupe_preserve_order(corrected_terms + corrections_from_terms + corrections_from_corrections)
    return normalized
