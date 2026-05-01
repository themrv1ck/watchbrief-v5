"""Shared score and recommendation color bands for WatchBrief V5 renderers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BandConfig:
    label: str
    accent: str
    soft: str
    deep: str
    bg: str
    border: str


BAND_CONFIG: dict[str, BandConfig] = {
    "strong": BandConfig(
        label="强烈推荐",
        accent="#b8de7f",
        soft="#d7ecb1",
        deep="#8bb85a",
        bg="rgba(184,222,127,.09)",
        border="rgba(184,222,127,.22)",
    ),
    "medium": BandConfig(
        label="中等片段可取",
        accent="#ab96e5",
        soft="#d4caf3",
        deep="#8873c8",
        bg="rgba(171,150,229,.09)",
        border="rgba(171,150,229,.22)",
    ),
    "low": BandConfig(
        label="少量片段可取",
        accent="#f6c90e",
        soft="#ffe891",
        deep="#c49a05",
        bg="rgba(246,201,14,.08)",
        border="rgba(246,201,14,.22)",
    ),
    "skip": BandConfig(
        label="不值得看",
        accent="#A24A42",
        soft="#f7dad6",
        deep="#c94d4d",
        bg="rgba(240,112,112,.12)",
        border="rgba(240,112,112,.28)",
    ),
    "failed": BandConfig(
        label="解析失败",
        accent="#9ca3af",
        soft="#d1d5db",
        deep="#6b7280",
        bg="rgba(156,163,175,.09)",
        border="rgba(156,163,175,.24)",
    ),
}

SKIP_TAGS = {"报告足够替代", "不推荐观看"}
LOW_TAGS = {"报告基本可替代", "只建议跳看"}


def _score_value(score: Any) -> float:
    return float(score or 0)


def score_band_from_replacement_score(score: Any) -> str:
    value = _score_value(score)
    if value < 5.0:
        return "skip"
    if value < 8.0:
        return "low"
    return "strong"


def recommendation_band_from_report(tag: Any, score: Any) -> str:
    tag_text = str(tag or "")
    value = _score_value(score)
    if tag_text == "解析不足":
        return "failed"
    if tag_text in SKIP_TAGS:
        return "skip"
    if value < 5.0:
        return "skip"
    if tag_text in LOW_TAGS:
        return "low"
    if tag_text == "值得补看":
        return "medium"
    if tag_text == "建议完整看":
        return "strong"
    return score_band_from_replacement_score(value)


def band_config(band: str) -> BandConfig:
    return BAND_CONFIG[band]
