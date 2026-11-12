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


def approved_template_style() -> str:
    html = (ROOT / "templates" / "watchbefore-summary-approved.html").read_text(encoding="utf-8")
    return html.split("<style>", 1)[1].split("</style>", 1)[0].strip()


def reading_theme_style() -> str:
    return (ROOT / "templates" / "watchbefore-reading-theme.css").read_text(encoding="utf-8")


def display_style_patch() -> str:
    return """
:root{--ink:var(--white);--line:var(--border);--mono:'Space Mono',monospace;--accent:var(--teal)}
.report-note{
  margin:18px 0 0;
  color:var(--muted);
  font-size:13px;
  line-height:1.7;
}
.report-note strong{color:var(--ink);font-weight:800}
.preference-fit{
  padding:14px 16px;
  border-radius:20px;
  border:1px solid var(--blue-border);
  background:var(--blue-bg);
  color:var(--text);
  font-size:14px;
  line-height:1.75;
}
.preference-fit strong{color:var(--white);font-weight:800}
.section-scope{border-left:3px solid var(--teal);background:linear-gradient(135deg,var(--teal-bg) 0%,var(--surface) 72%)}
.section-scope h2{color:var(--teal)}
.content-scope{margin:0;color:var(--text);font-size:18px;line-height:1.85}
.watch-time .time-sep{display:block;color:inherit}
.phase-list{display:grid;gap:14px}
.phase-item{display:grid;grid-template-columns:132px minmax(0,1fr);gap:16px;align-items:start;padding:14px 0;border-top:1px solid var(--line)}
.phase-item:first-child{border-top:0}
.phase-time{font-family:var(--mono);font-weight:800;color:var(--accent);line-height:1.5}
.phase-copy h3{margin:0 0 6px;font-size:16px}
.phase-copy p{margin:0;color:var(--muted);line-height:1.75}
.combined-target-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;grid-column:span 12}
.target-section h3{margin:0 0 10px;font-size:17px}
.target-section ul{margin:0;padding-left:20px;color:var(--text);line-height:1.75}
.target-section li+li{margin-top:8px}
.target-section.t-green{border-left-color:var(--green);background:linear-gradient(135deg,var(--green-bg) 0%,var(--surface) 72%)}
.target-section.t-green h3{color:var(--green)}
.target-section.t-purple{border-left-color:var(--purple);background:linear-gradient(135deg,var(--purple-bg) 0%,var(--surface) 72%)}
.target-section.t-purple h3{color:var(--purple)}
.target-section.t-blue{border-left-color:var(--blue);background:linear-gradient(135deg,var(--blue-bg) 0%,var(--surface) 72%)}
.target-section.t-blue h3{color:var(--blue)}
.target-section.t-red{border-left-color:var(--red);background:linear-gradient(135deg,var(--red-bg) 0%,var(--surface) 72%)}
.target-section.t-red h3{color:var(--red)}
@media(max-width:760px){.combined-target-grid{grid-template-columns:1fr}}
@media(max-width:760px){.phase-item{grid-template-columns:1fr}.phase-time{font-size:13px}}

/* Keep the reference palette while giving the summary a comfortable reading width. */
main{max-width:820px}
.reading-hero{display:block;margin-bottom:34px}
.reading-hero .hero-main{padding:4px 4px 0;border:0;background:transparent;box-shadow:none}
.reading-kicker{margin-bottom:12px;color:var(--teal);font-size:14px;font-weight:800;letter-spacing:.04em}
.reading-hero h1{max-width:720px;margin-bottom:14px;font-size:42px;line-height:1.28;text-wrap:balance;overflow-wrap:anywhere}
.reading-meta{display:flex;flex-wrap:wrap;gap:8px 14px;margin-bottom:24px;color:var(--muted);font-size:14px;line-height:1.7}
.reading-meta strong{color:var(--text);font-weight:700}
.reading-meta a{color:var(--teal);font-weight:800;text-decoration:none}
.reading-meta a:hover{text-decoration:underline}
.reading-meta a:focus-visible,.reader-index summary:focus-visible{outline:2px solid var(--blue);outline-offset:4px;border-radius:4px}
.reading-score{color:var(--dim)}
.reading-score strong{color:var(--muted)}
.reading-hero .hero-summary{max-width:760px;padding:0;border:0;border-radius:0;background:transparent}
.reading-hero .summary-label{margin-bottom:9px;color:var(--teal);font-size:12px;letter-spacing:.12em}
.reading-hero .hero-summary p{margin:0;color:var(--text);font-size:19px;line-height:1.9}
.reading-hero .hero-summary strong{color:var(--white)}
.reading-flow{grid-column:span 12;max-width:720px;margin:0 auto;padding:8px 0 16px}
.reading-flow h2,.story-closing h2{margin:0 0 16px;color:var(--white);font-size:26px;line-height:1.35}
.reading-flow .section-note,.story-closing .section-note{margin-bottom:10px;color:var(--teal)}
.story-intro{margin:0 0 28px;padding:20px 24px;border-left:3px solid var(--blue);border-radius:0 18px 18px 0;background:var(--blue-bg);color:var(--text);font-size:18px;line-height:1.9;letter-spacing:.01em;overflow-wrap:anywhere}
.story-beats{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}
.story-beat{display:grid;grid-template-columns:38px minmax(0,1fr);gap:18px;padding:22px 0;border-bottom:1px solid var(--line)}
.story-marker{padding-top:2px;color:var(--teal);font-family:var(--mono);font-size:14px;font-weight:800;line-height:1.5}
.story-copy h3{margin:0 0 8px;color:var(--white);font-size:19px;line-height:1.5;text-wrap:pretty;overflow-wrap:anywhere}
.story-copy p{margin:0;color:var(--text);font-size:17px;line-height:1.9;overflow-wrap:anywhere}
.story-copy time{display:block;margin-top:10px;color:var(--muted);font-family:var(--num);font-size:13px;font-weight:700}
.story-closing{grid-column:span 12;max-width:760px;margin:0 auto;padding:30px 34px;border:1px solid rgba(126,184,240,.18);border-radius:28px;background:linear-gradient(135deg,rgba(126,184,240,.08),rgba(19,22,31,.72) 70%)}
.story-core{margin:0 0 16px;color:#e2f0ff;font-size:21px;line-height:1.85;font-weight:800}
.story-final{margin:0;color:var(--muted);font-size:17px;line-height:1.9}
.reader-index{grid-column:span 12;padding:0;overflow:hidden;border-radius:24px;background:rgba(19,22,31,.72)}
.reader-index summary{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px 24px;cursor:pointer;color:var(--white);font-size:17px;font-weight:800;list-style:none}
.reader-index summary::-webkit-details-marker{display:none}
.reader-index summary::after{content:"展开";flex:0 0 auto;color:var(--teal);font-family:var(--mono);font-size:12px;font-weight:700;letter-spacing:.06em}
.reader-index[open] summary{border-bottom:1px solid var(--line)}
.reader-index[open] summary::after{content:"收起"}
.reader-index-body{padding:22px 24px 26px}
.analysis-index{border-color:rgba(167,139,250,.22)}
.analysis-index summary::after{color:var(--purple)}
.source-index{border-color:rgba(79,209,197,.22)}
.source-index summary::after{color:var(--teal)}
.report-facts{border-color:rgba(255,255,255,.10)}
.report-facts summary::after{color:var(--muted)}
.reference-sections{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:26px 34px}
.reader-reference h3{margin:0 0 10px;color:var(--white);font-size:17px}
.reader-reference ul{margin:0;padding-left:20px;color:var(--text);font-size:16px;line-height:1.8}
.reader-reference li+li{margin-top:8px}
.reader-reference.reader-boundaries h3{color:#f7a8a8}
.reader-reference.reader-actions h3{color:var(--blue)}
.reader-index .phase-list{gap:0}
.reader-index .phase-item{padding:18px 0}
.logic-index-copy{display:grid;gap:12px}
.logic-line{margin:0;color:var(--text);font-size:16px;line-height:1.8}
.logic-line strong{color:var(--purple)}
.logic-chain{margin-top:6px;color:#d9caff;font-size:15px;line-height:1.9}
.report-facts-body{display:grid;gap:16px}
.report-facts-score{margin:0;color:var(--muted);font-size:16px;line-height:1.8}
.report-facts-score strong{color:var(--white)}
.section-watch{max-width:760px;margin:0 auto;padding:0;border-left:3px solid var(--action-green)}
.section-watch .section-note{color:var(--action-green)}
.section-watch .watch-intro{margin:-4px 0 18px;color:var(--muted);font-size:16px;line-height:1.8}
@media(max-width:760px){
  .reading-hero h1{font-size:31px}.story-intro{padding:17px 18px;font-size:17px}.story-beat{grid-template-columns:32px minmax(0,1fr);gap:12px;padding:18px 0}.story-copy h3{font-size:18px}.story-copy p{font-size:16px}.reader-index summary{padding:18px 20px}.reader-index-body{padding:20px}.reference-sections{grid-template-columns:1fr}
}

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
    side_verdict_style = f'style="color:var(--text); border-color:{cfg.border}; background:{cfg.bg};"'
    return score_num, hero_side, score_box, tag_style, side_verdict_style


def original_video_pill(report: dict[str, Any]) -> str:
    return (
        f'<a class="pill blue" href="{esc(report["url"])}" target="_blank" rel="noreferrer">'
        "<strong>原视频：</strong>点击查看</a>"
    )


def render_preference_fit(report: dict[str, Any]) -> str:
    relevance = report.get("personal_relevance")
    if not isinstance(relevance, dict):
        return (
            '<div class="preference-fit" data-personal-match="profile-not-bound">'
            "<strong>画像判断：</strong>未绑定个人画像，此结果不能用于判断你是否会喜欢。"
            "</div>"
        )
    return (
        '<div class="preference-fit" data-personal-match="'
        f'{esc(relevance["match_level"])}">'
        f'<strong>画像判断：</strong>{esc(relevance["summary"])}'
        "</div>"
    )


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


def render_long_content_breakdown(report: dict[str, Any]) -> str:
    phases = report.get("long_content_breakdown")
    if not isinstance(phases, list) or not phases:
        return ""
    rows = []
    for phase in phases:
        rows.append(
            '<div class="phase-item">'
            f'<div class="phase-time">{esc(phase["start"])} - {esc(phase["end"])}</div>'
            '<div class="phase-copy">'
            f'<h3>{esc(phase["title"])}</h3>'
            f'<p>{esc(phase["summary"])}</p>'
            '</div>'
            '</div>'
        )
    return f"""
    <details class="reader-index source-index panel col-12">
      <summary>想按时间核对全片，再展开完整索引</summary>
      <div class="reader-index-body">
        <div class="phase-list">
          {"".join(rows)}
        </div>
      </div>
    </details>
"""


def render_content_scope(report: dict[str, Any]) -> str:
    return f"""
    <div class="panel section section-scope col-12">
      <div class="section-note">内容范围</div>
      <h2>这期实际讲了什么？</h2>
      <p class="content-scope">{esc(report["target_summary"])}</p>
    </div>
"""


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


def render_default_content_sections(report: dict[str, Any]) -> str:
    sections = report.get("target_sections")
    if not isinstance(sections, dict):
        return ""
    specs = (
        ("direct_statements", "直接结论", "t-green"),
        ("key_points", "具体观点", "t-purple"),
        ("practical_takeaways", "可直接采用", "t-blue"),
        ("boundaries", "注意边界", "t-red"),
    )
    if tuple(sections.keys()) != tuple(key for key, _label, _tone in specs):
        return ""
    references = []
    for key, label, tone in specs:
        points = sections[key]
        if not isinstance(points, list) or not points:
            return ""
        extra_class = ""
        if key == "practical_takeaways":
            extra_class = " reader-actions"
        elif key == "boundaries":
            extra_class = " reader-boundaries"
        references.append(
            f'<section class="reader-reference{extra_class}">'
            f"<h3>{esc(label)}</h3>"
            f"<ul>{render_target_section_points(points)}</ul>"
            "</section>"
        )
    return f"""
    <details class="reader-index analysis-index panel col-12" id="supporting">
      <summary>补充内容与边界</summary>
      <div class="reader-index-body reference-sections">
        {"".join(references)}
      </div>
    </details>
"""


def render_reading_flow(report: dict[str, Any]) -> str:
    rows = []
    for index, section in enumerate(report["reading_flow"], start=1):
        rows.append(
            '<li><span class="beat-number" aria-hidden="true">'
            f'{index:02d}</span><div>'
            f'<h3>{esc(section["heading"])}</h3>'
            f'<p>{esc(section["body"])}</p>'
            f'<time>{esc(section["start"])} — {esc(section["end"])}</time>'
            '</div></li>'
        )
    return f"""
    <article class="panel section summary-panel col-12" id="summary">
      <div class="section-note">内容总结</div>
      <h2>视频到底讲了什么？</h2>
      <ol class="summary-beats">{"".join(rows)}</ol>
      <div class="final-conclusion"><strong>视频主旨：</strong>{esc(report["final_conclusion"])}</div>
    </article>
"""


def render_report_facts(report: dict[str, Any]) -> str:
    return f"""
    <details class="reader-index report-facts panel col-12">
      <summary>报告信息与边界</summary>
      <div class="reader-index-body report-facts-body">
        <p class="report-facts-score"><strong>内容价值：</strong>{esc(score_text(report))} / 10 · {esc(display_tag(report))}</p>
        <p class="report-facts-score"><strong>一句话：</strong>{esc(report["one_line_brief"])}</p>
        <p class="report-facts-score"><strong>视频主旨：</strong>{esc(report["highest_compression"])}</p>
        <p class="report-facts-score"><strong>结尾主张：</strong>{esc(report["final_conclusion"])}</p>
        <p class="report-facts-score"><strong>补看说明：</strong>{esc(display_natural_time_ranges(report["watch_verdict"]))}</p>
        {render_preference_fit(report)}
        {render_report_note(report)}
      </div>
    </details>
"""


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
{reading_theme_style()}
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
    report_band = score_band(report)
    score_num, side_style, box_style, tag_style, verdict_style = score_styles(report)
    # The field keeps its schema prefix; the visible answer omits that label.
    brief = report["one_line_brief"].removeprefix("这期视频主要讲：").strip()
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(report["title"])} · WatchBefore</title>
<style>
{reference_style()}
{display_style_patch()}
{approved_template_style()}
.summary-beats time{{display:block;margin-top:8px;color:var(--muted);font:12px/1.6 var(--num)}}
.section-watch{{max-width:none;margin:0;padding:24px}}
.hero-main,.hero-side,.summary-panel{{min-width:0;overflow-wrap:anywhere}}
{reading_theme_style()}
</style>
</head>
<body>
<main>
  <header class="report-top-menu"><div class="eyebrow">视频速览 · WatchBefore</div><nav aria-label="报告目录"><a href="#summary">视频总结</a><a href="#watch">值得补看</a><a href="#supporting">补充内容</a></nav></header>
  <section class="hero score-{report_band}" data-score-band="{report_band}">
    <div class="panel hero-main">
      <h1>{esc(report["title"])}</h1>
      <div class="meta-row">
        <span class="pill"><strong>频道：</strong>{esc(report["channel"])}</span>
        <span class="pill"><strong>时长：</strong>{esc(report["duration"])}</span>
        <span class="pill"><strong>日期：</strong>{esc(report["date"])}</span>
        <span class="pill blue"><strong>主题：</strong>{esc(report["topic"])}</span>
        <span class="pill blue"><strong>报告模式：</strong>视频总结</span>
        {original_video_pill(report)}
      </div>
      <div class="hero-summary"><div class="summary-label">视频总结</div><p>{esc(report["target_summary"])}</p></div>
    </div>
    <aside class="panel hero-side" {side_style}>
      <div class="score-top">
        <div class="score-box" {box_style}><div class="score-label">video value score</div>{score_num}</div>
        <div class="tag" {tag_style}>{esc(display_tag(report))}</div>
      </div>
      <p class="side-verdict" {verdict_style}><strong>省流</strong><span>{esc(brief)}</span></p>
    </aside>
  </section>
  <section class="grid">
    {render_reading_flow(report)}
    <section class="panel section section-watch col-12" id="watch">
      <div class="section-note">原片对应片段</div>
      <h2>值得补看</h2>
      <div class="watch-list">{render_watch_segments(report)}</div>
      <div class="only-one">{esc(display_natural_time_ranges(report["only_one_segment"]))}</div>
    </section>
    {render_default_content_sections(report)}
    {render_long_content_breakdown(report)}
    {render_report_facts(report)}
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
