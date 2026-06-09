#!/usr/bin/env python3
"""Pure HTML rendering for WatchBrief V5 normalized payloads."""

from __future__ import annotations

import argparse
import json
import re
from html import escape
from pathlib import Path
from typing import Any

try:
    from .report_targets import DEFAULT_REPORT_TARGET, normalize_report_target, report_target_label, report_target_section_specs
    from .validator import validate_renderer_input
except ImportError:  # pragma: no cover - direct script execution
    from report_targets import DEFAULT_REPORT_TARGET, normalize_report_target, report_target_label, report_target_section_specs
    from validator import validate_renderer_input

try:
    from .score_bands import band_config, score_band_from_replacement_score
except ImportError:  # pragma: no cover
    from score_bands import band_config, score_band_from_replacement_score


ROOT = Path(__file__).resolve().parents[1]
SINGLE_TEMPLATE = ROOT / "references" / "video_report_v5.html"


def esc(value: Any) -> str:
    return escape(str(value), quote=True)


def reference_html() -> str:
    return SINGLE_TEMPLATE.read_text(encoding="utf-8")


def reference_style() -> str:
    html = reference_html()
    start = html.find("<style>")
    end = html.find("</style>", start)
    if start == -1 or end == -1:
        raise RuntimeError("V5 single-video style block not found.")
    return html[start + len("<style>"):end].strip()


def display_style_patch() -> str:
    return """
.report-note{
  margin:18px 0 0;
  color:var(--muted);
  font-size:13px;
  line-height:1.7;
}
.report-note strong{color:var(--ink);font-weight:800}
.watch-time .time-sep{display:block;color:inherit}
""".strip()


def target_style_patch() -> str:
    return """
.target-hero-copy{margin:18px 0 0;color:var(--ink);font-size:18px;line-height:1.8}
.target-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
.target-section h3{margin:0 0 10px;font-size:17px}
.target-section ul{margin:0;padding-left:20px;color:var(--ink);line-height:1.75}
.target-section li+li{margin-top:8px}
@media(max-width:760px){.target-grid{grid-template-columns:1fr}}
""".strip()


def feedback_suffix() -> str:
    html = reference_html()
    start = html.find('<div class="feedback-launcher">')
    end = html.rfind("</body>")
    if start == -1 or end == -1:
        raise RuntimeError("V5 single-video feedback block not found.")
    return html[start:end].strip()


def score_text(report: dict[str, Any]) -> str:
    return f'{float(report["replacement_score"]):.1f}'


def score_band(report: dict[str, Any]) -> str:
    return score_band_from_replacement_score(report["replacement_score"])


def display_tag(report: dict[str, Any]) -> str:
    """Render legacy recommendation tags as neutral content-value bands."""
    tag = str(report.get("tag") or "")
    neutral = {
        "报告足够替代": "内容提炼较完整",
        "报告基本可替代": "内容提炼基本完整",
        "只建议跳看": "内容价值一般",
        "值得补看": "内容价值较高",
        "建议完整看": "内容价值很高",
        "不推荐观看": "内容价值偏低",
        "解析不足": "解析不足",
    }
    return neutral.get(tag, tag)


def score_styles(report: dict[str, Any]) -> tuple[str, str, str, str, str]:
    cfg = band_config(score_band(report))
    score_num = (
        f'<span class="score-num" style="color:{cfg.accent}">'
        f'{score_text(report)} <small>/ 10</small>'
        f"</span>"
    )
    hero_side = (
        'style="'
        f'border-left:3px solid {cfg.accent}; '
        f'background:linear-gradient(135deg,{cfg.bg} 0%,var(--surface) 72%); '
        f'--single-band-accent:{cfg.accent}; '
        f'--single-band-bg:{cfg.bg}; '
        f'--single-band-border:{cfg.border}; '
        f'--single-band-soft:{cfg.soft}; '
        f'--single-band-deep:{cfg.deep}"'
    )
    score_box = (
        'style="'
        f'background:{cfg.bg}; '
        f'border-color:{cfg.border}; '
        f'--score-band-accent:{cfg.accent}; '
        f'--score-band-bg:{cfg.bg}; '
        f'--score-band-border:{cfg.border}"'
    )
    tag_style = f'style="color:{cfg.accent}; border-color:{cfg.border}; background:{cfg.bg};"'
    side_verdict_style = f'style="color:#ffffff; border-color:{cfg.border}; background:{cfg.bg};"'
    return score_num, hero_side, score_box, tag_style, side_verdict_style


def original_video_pill(report: dict[str, Any]) -> str:
    return (
        f'<a class="pill blue" href="{esc(report["url"])}" target="_blank" rel="noreferrer">'
        "<strong>原视频：</strong>点击查看</a>"
    )


def render_arrow_chain(report: dict[str, Any]) -> str:
    return ' <span class="arrow-token">→</span> '.join(esc(node) for node in report["arrow_chain"])


def display_natural_time_ranges(value: Any) -> str:
    return re.sub(r"(\d{1,2}:\d{2})\s*\|\s*(\d{1,2}:\d{2})", r"\1 - \2", str(value))


def render_watch_time(segment: dict[str, Any]) -> str:
    return (
        f'<span>{esc(segment["start"])}</span>'
        '<span class="time-sep">|</span>'
        f'<span>{esc(segment["end"])}</span>'
    )


def render_watch_segments(report: dict[str, Any]) -> str:
    rows = []
    for segment in report["watch_segments"]:
        rows.append(
            '<div class="watch-item">'
            f'<div class="watch-time">{render_watch_time(segment)}</div>'
            '<div>'
            f'<h3>{esc(segment["title"])}</h3>'
            f'<p>{esc(segment["reason"])}</p>'
            '</div>'
            '</div>'
        )
    return "\n        ".join(rows)


def render_report_note(report: dict[str, Any]) -> str:
    caveat = str(report.get("content_caveat") or "")
    if not caveat:
        return ""
    return f'\n      <p class="report-note"><strong>说明：</strong>{esc(caveat)}</p>'


def render_target_section_points(points: Any) -> str:
    rows = points if isinstance(points, list) else []
    return "\n".join(f"<li>{esc(point)}</li>" for point in rows)


def render_target_sections(report: dict[str, Any]) -> str:
    target = normalize_report_target(report.get("report_target"))
    sections = report.get("target_sections") if isinstance(report.get("target_sections"), dict) else {}
    rows = []
    for key, label in report_target_section_specs(target):
        rows.append(
            '<div class="panel section target-section">'
            f"<h3>{esc(label)}</h3>"
            f"<ul>{render_target_section_points(sections.get(key))}</ul>"
            "</div>"
        )
    return "\n        ".join(rows)


def render_target_video_html(report: dict[str, Any]) -> str:
    target = normalize_report_target(report.get("report_target"))
    label = report_target_label(target)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(report["title"])} · {esc(label)} · WatchBrief V5</title>
<style>
{reference_style()}
{display_style_patch()}
{target_style_patch()}
</style>
</head>
<body>
<main>
  <div class="eyebrow">视频速览 · {esc(label)}</div>

  <section class="hero target-report" data-report-target="{esc(target)}">
    <div class="panel hero-main">
      <h1>{esc(report["title"])}</h1>
      <div class="meta-row">
        <span class="pill"><strong>频道：</strong>{esc(report["channel"])}</span>
        <span class="pill"><strong>时长：</strong>{esc(report["duration"])}</span>
        <span class="pill"><strong>日期：</strong>{esc(report["date"])}</span>
        <span class="pill blue"><strong>主题：</strong>{esc(report["topic"])}</span>
        <span class="pill blue"><strong>报告模式：</strong>{esc(label)}</span>
        {original_video_pill(report)}
      </div>
      <p class="target-hero-copy">{esc(report["target_summary"])}</p>
    </div>
  </section>

  <section class="target-grid">
        {render_target_sections(report)}
  </section>
</main>

{feedback_suffix()}
</body>
</html>
"""


def render_single_video_html(payload: dict[str, Any]) -> str:
    report = validate_renderer_input(payload)
    if normalize_report_target(report.get("report_target")) != DEFAULT_REPORT_TARGET:
        return render_target_video_html(report)
    path_table = report["path_table"]
    report_band = score_band(report)
    score_num, hero_side_style, score_box_style, tag_style, side_verdict_style = score_styles(report)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(report["title"])} · WatchBrief V5</title>
<style>
{reference_style()}
{display_style_patch()}
</style>
</head>
<body>
<main>
  <div class="eyebrow">视频速览 · 主要内容与补看入口</div>

  <section class="hero score-{report_band}" data-score-band="{report_band}">
    <div class="panel hero-main">
      <h1>{esc(report["title"])}</h1>
      <div class="meta-row">
        <span class="pill"><strong>频道：</strong>{esc(report["channel"])}</span>
        <span class="pill"><strong>时长：</strong>{esc(report["duration"])}</span>
        <span class="pill"><strong>日期：</strong>{esc(report["date"])}</span>
        <span class="pill blue"><strong>主题：</strong>{esc(report["topic"])}</span>
        {original_video_pill(report)}
      </div>
      <div class="hero-summary">
        <div class="summary-label">one-line brief</div>
        <p><strong>一句话总结：</strong>{esc(report["one_line_brief"])}</p>
      </div>
    </div>

    <aside class="panel hero-side" {hero_side_style}>
      <div class="score-box" {score_box_style}>
        <div class="score-label">video value score</div>
        {score_num}
      </div>
      <div class="tag" {tag_style}>{esc(display_tag(report))}</div>
      <p class="side-verdict" {side_verdict_style}><strong>补看入口：</strong>{esc(display_natural_time_ranges(report["watch_verdict"]))}</p>
    </aside>
  </section>

  <section class="grid">
    <div class="panel section section-chain col-12">
      <div class="section-note">what the video is actually saying</div>
      <h2>视频到底讲了什么？</h2>
      <div class="core-extract">
        <div class="core-extract-label">highest compression · 核心提炼</div>
        <p>{esc(report["highest_compression"])}</p>
      </div>
      <div class="path-table">
        <div class="path-row">
          <div class="path-label">问题</div>
          <div class="path-copy">{esc(path_table["problem"])}</div>
        </div>
        <div class="path-row">
          <div class="path-label">机制</div>
          <div class="path-copy">{esc(path_table["mechanism"])}</div>
        </div>
        <div class="path-row">
          <div class="path-label">转折</div>
          <div class="path-copy">{esc(path_table["turning_point"])}</div>
        </div>
        <div class="path-row">
          <div class="path-label">落点</div>
          <div class="path-copy">{esc(path_table["landing"])}</div>
        </div>
      </div>
      <div class="mini-arrow-chain">{render_arrow_chain(report)}</div>
      <div class="final-conclusion"><strong>结论：</strong>{esc(report["final_conclusion"])}</div>
    </div>

    <div class="panel section section-watch col-12">
      <div class="section-note">where to jump for source context</div>
      <h2>如果要补原片，先看哪里？</h2>
      <div class="watch-list">
        {render_watch_segments(report)}
      </div>
      <div class="only-one">{esc(display_natural_time_ranges(report["only_one_segment"]))}</div>{render_report_note(report)}
    </div>
  </section>
</main>

{feedback_suffix()}
</body>
</html>
"""


def render_payload_file(input_path: Path, output_path: Path) -> None:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    html = render_single_video_html(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a WatchBrief V5 normalized mock payload to HTML.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render_payload_file(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
