#!/usr/bin/env python3
"""Watch-order page rendering for WatchBrief V5 list runs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from .validator import validate_watch_order_payload, watch_order_density_percent
except ImportError:  # pragma: no cover - direct script execution
    from validator import validate_watch_order_payload, watch_order_density_percent

try:
    from .score_bands import band_config, recommendation_band_from_report
except ImportError:  # pragma: no cover
    from score_bands import band_config, recommendation_band_from_report


ROOT = Path(__file__).resolve().parents[1]
V4_WATCH_ORDER_TEMPLATE = ROOT / "references" / "00watch_order_v5.html"

BAND_DISPLAY_LABELS = {
    "strong": "建议完整看完",
    "medium": "值得补看",
    "low": "只建议跳看",
    "skip": "报告可替代",
    "failed": "解析失败",
    "skipped": "图文跳过",
}

BAND_DISPLAY_ORDER = ("strong", "medium", "low", "skip", "failed", "skipped")


def esc(value: Any) -> str:
    return escape(str(value), quote=True)


def script_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def reference_style() -> str:
    html = V4_WATCH_ORDER_TEMPLATE.read_text(encoding="utf-8")
    start = html.find("<style>")
    end = html.find("</style>", start)
    if start == -1 or end == -1:
        raise RuntimeError("V5 watch-order style block not found.")
    return html[start + len("<style>"):end].strip()


def filter_key(video: dict[str, Any]) -> str:
    return recommendation_band_from_report(video.get("tag"), video.get("replacement_score"))


def style_vars(key: str) -> str:
    config = band_config(key)
    return (
        f'--group-accent:{config.accent};'
        f'--group-bg:{config.bg};'
        f'--group-border:{config.border};'
        f'--group-accent-soft:{config.soft};'
        f'--group-accent-deep:{config.deep};'
    )


def badge_style(key: str) -> str:
    config = band_config(key)
    return f'border-color:{config.border};color:{config.accent};background:{config.bg};'


def theme_tags(video: dict[str, Any]) -> list[str]:
    return [part.strip() for part in str(video.get("topic") or "").split("/") if part.strip()][:2]


def segment_labels(video: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for segment in video.get("watch_segments", []):
        if not isinstance(segment, dict):
            continue
        start = str(segment.get("start") or "").strip()
        end = str(segment.get("end") or "").strip()
        if start and end:
            labels.append(f"{start} | {end}")
    return labels


def segment_labels_for_js(video: dict[str, Any]) -> list[str]:
    return segment_labels(video) or ["暂无重点片段"]


def summary_text(video: dict[str, Any]) -> str:
    for key in ("one_line_brief", "highest_compression", "final_conclusion"):
        value = str(video.get(key) or "").strip()
        if value:
            return value
    return "暂无摘要。"


def successful_card_item(video: dict[str, Any]) -> dict[str, Any]:
    key = filter_key(video)
    tags = theme_tags(video)
    return {
        "type": "video",
        "filterKey": key,
        "title": clean_text(video.get("title")) or "Untitled Video",
        "pageFile": clean_text(video.get("page_file")),
        "score": round(float(video.get("replacement_score") or 0), 1),
        "density": watch_order_density_percent(video),
        "label": BAND_DISPLAY_LABELS[key],
        "topic": tags[0] if tags else "",
        "topicDetail": tags[1] if len(tags) > 1 else "",
        "summary": summary_text(video),
        "segments": segment_labels_for_js(video),
    }


def successful_card_items(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [successful_card_item(video) for video in videos]


def failure_card_item(failure: dict[str, Any]) -> dict[str, Any]:
    title = clean_text(failure.get("title")) or clean_text(failure.get("url")) or "解析失败的视频"
    stage = clean_text(failure.get("stage")) or "pipeline"
    error = clean_text(failure.get("error")) or "unknown failure"
    return {
        "type": "failed",
        "filterKey": "failed",
        "title": title,
        "pageFile": "",
        "score": None,
        "density": 0,
        "label": BAND_DISPLAY_LABELS["failed"],
        "topic": stage,
        "topicDetail": "",
        "summary": f"{stage} · {error}",
        "segments": ["未生成可观看片段"],
    }


def failure_card_items(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [failure_card_item(failure) for failure in failures]


def skipped_card_item(skipped: dict[str, Any]) -> dict[str, Any]:
    title = clean_text(skipped.get("title")) or clean_text(skipped.get("url")) or "跳过的图文笔记"
    reason_code = clean_text(skipped.get("reason_code")) or "non_video_note"
    note_type = clean_text(skipped.get("note_media_kind")) or clean_text(skipped.get("note_type")) or "image_text_note"
    return {
        "type": "skipped",
        "filterKey": "skipped",
        "title": title,
        "pageFile": "",
        "score": None,
        "density": 0,
        "label": BAND_DISPLAY_LABELS["skipped"],
        "topic": reason_code,
        "topicDetail": note_type,
        "summary": "图文笔记或非视频内容，本阶段跳过，不进入视频下载、转写或模型分析链路。",
        "segments": ["未进入音频 / 转写 / 模型链路"],
    }


def skipped_card_items(skipped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [skipped_card_item(item) for item in skipped]


def display_generated_at(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return datetime.now().strftime("%Y-%m-%d %H:%M CST")
    if re.search(r"\b[A-Z]{2,4}\b", text):
        return text
    return f"{text} CST"


def source_platform_label(source_url: str, explicit: Any = "") -> str:
    explicit_text = clean_text(explicit)
    if explicit_text:
        return explicit_text
    host = urlparse(source_url).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    if "bilibili.com" in host:
        return "Bilibili"
    if "xiaohongshu.com" in host:
        return "小红书"
    return "视频平台"


def source_url_for_display(data: dict[str, Any]) -> str:
    source_url = clean_text(data.get("source_url"))
    if source_url:
        return source_url
    job_name = clean_text(data.get("job_name"))
    if job_name.startswith(("http://", "https://")):
        return job_name
    return ""


def watch_order_title(data: dict[str, Any]) -> str:
    playlist_title = clean_text(data.get("playlist_title"))
    if playlist_title:
        return f"{playlist_title} · 观看顺序"
    platform = source_platform_label(source_url_for_display(data), data.get("source_platform"))
    return f"{platform} 播放列表 · 观看顺序"


def source_meta_line(data: dict[str, Any]) -> str:
    source_url = source_url_for_display(data)
    platform = source_platform_label(source_url, data.get("source_platform"))
    source_kind = clean_text(data.get("source_kind"))
    source_subkind = clean_text(data.get("source_subkind"))
    kind = "小红书专辑" if source_subkind == "xiaohongshu_board" else "播放列表" if source_kind != "single" else "列表"
    count = int(data.get("requested_count") or 0)
    parts = [platform, kind, f"{count} 条{'note' if source_subkind == 'xiaohongshu_board' else '视频'}"]
    if source_subkind == "xiaohongshu_board":
        parts.append(f"视频 note {int(data.get('video_note_count') or 0)}")
        parts.append(f"图文跳过 {int(data.get('skipped_count') or data.get('non_video_count') or 0)}")
    return " · ".join(parts)


def source_link_html(source_url: Any) -> str:
    url = clean_text(source_url)
    if not url.startswith(("http://", "https://")):
        return ""
    return f' · <a href="{esc(url)}" target="_blank" rel="noopener noreferrer">打开原播放列表</a>'


def category_config_for_script() -> dict[str, dict[str, str]]:
    config: dict[str, dict[str, str]] = {}
    for key in ("all", *BAND_DISPLAY_ORDER):
        if key == "all":
            config[key] = {
                "label": "全部",
                "accent": "#ffffff",
                "soft": "#dbe2f0",
                "deep": "#b9c4da",
                "bg": "rgba(255,255,255,.045)",
                "border": "rgba(255,255,255,.14)",
            }
            continue
        if key == "skipped":
            band = band_config("failed")
            config[key] = {
                "label": BAND_DISPLAY_LABELS[key],
                "accent": band.accent,
                "soft": band.soft,
                "deep": band.deep,
                "bg": band.bg,
                "border": band.border,
            }
            continue
        band = band_config(key)
        config[key] = {
            "label": BAND_DISPLAY_LABELS[key],
            "accent": band.accent,
            "soft": band.soft,
            "deep": band.deep,
            "bg": band.bg,
            "border": band.border,
        }
    return config


def render_watch_order_script(items: list[dict[str, Any]], stats: dict[str, Any]) -> str:
    return f"""
<script>
const categoryConfig = {script_json(category_config_for_script())};
const filterOrder = {script_json(["all", *BAND_DISPLAY_ORDER])};
const items = {script_json(items)};
const watchOrderStats = {script_json(stats)};
let activeFilter = 'all';

function escapeHtml(value) {{
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({{
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }}[char]));
}}

function pad(num) {{
  return String(num).padStart(2, '0');
}}

function countFor(filterKey) {{
  if (filterKey === 'all') {{
    return items.length;
  }}
  return items.filter((item) => item.filterKey === filterKey || item.type === filterKey).length;
}}

function buildFilterChip(filterKey) {{
  const config = categoryConfig[filterKey];
  const activeClass = filterKey === activeFilter ? 'active' : '';
  return `
    <button
      class="filter-chip ${{activeClass}}"
      type="button"
      data-filter="${{filterKey}}"
      style="--filter-accent:${{config.accent}}; --filter-accent-soft:${{config.soft}}; --filter-bg:${{config.bg}}; --filter-border:${{config.border}};"
    >
      <span class="filter-text">${{config.label}}</span>
    </button>
  `;
}}

function buildBadges(item, config) {{
  const badges = [
    `<span class="badge badge-category" style="border-color:${{config.border}};color:${{config.accent}};background:${{config.bg}};">${{escapeHtml(item.label)}}</span>`
  ];
  if (item.topic) {{
    badges.push(`<span class="badge badge-topic">${{escapeHtml(item.topic)}}</span>`);
  }}
  if (item.topicDetail) {{
    badges.push(`<span class="badge badge-muted">${{escapeHtml(item.topicDetail)}}</span>`);
  }}
  return badges.join('');
}}

function buildClipPills(item) {{
  const segments = Array.isArray(item.segments) && item.segments.length ? item.segments : ['暂无重点片段'];
  return segments.map((segment) => {{
    const muted = item.type === 'failed' || item.type === 'skipped' || segment === '暂无重点片段' || segment === '未生成可观看片段';
    return `<span class="clip-pill${{muted ? ' clip-pill-muted' : ''}}">▶ ${{escapeHtml(segment)}}</span>`;
  }}).join('');
}}

function buildRankCard(item, index) {{
  const config = categoryConfig[item.filterKey] || categoryConfig.failed;
  const scoreBlock = item.type === 'failed' || item.type === 'skipped'
    ? '<div class="score-big">--</div><span class="score-denom">/ 10</span>'
    : `<div class="score-big">${{Number(item.score || 0).toFixed(1)}}</div><span class="score-denom">/ 10</span>`;
  const title = item.pageFile
    ? `<a href="${{escapeHtml(item.pageFile)}}">${{escapeHtml(item.title)}}</a>`
    : escapeHtml(item.title);
  return `
    <div
      class="rank-card"
      data-filter="${{escapeHtml(item.filterKey)}}"
      style="--group-accent:${{config.accent}}; --group-accent-soft:${{config.soft}}; --group-accent-deep:${{config.deep}}; --group-bg:${{config.bg}}; --group-border:${{config.border}};"
    >
      <div class="rank-card-top">
        <div class="rank-num">${{pad(index + 1)}}</div>
        <div class="rank-meta">
          <div class="rank-title">${{title}}</div>
          <div class="rank-badges">${{buildBadges(item, config)}}</div>
        </div>
        <div class="rank-score-box">${{scoreBlock}}</div>
      </div>
      <div class="score-bar-wrap">
        <div class="score-bar-track">
          <div class="score-bar-fill" style="width:${{Number(item.density || 0)}}%"></div>
        </div>
      </div>
      <div class="card-body">
        <div class="body-summary">${{escapeHtml(item.summary)}}</div>
        <div class="clips-row"><span class="clips-label">推荐观看片段</span>${{buildClipPills(item)}}</div>
      </div>
    </div>
  `;
}}

function buildEmptyState(filterKey) {{
  const config = categoryConfig[filterKey] || categoryConfig.all;
  return `
    <div class="empty-title">${{escapeHtml(config.label)}}：当前没有内容</div>
    <div class="empty-text">这一组当前没有匹配卡片，可以切回“全部”继续看。</div>
  `;
}}

function renderStats() {{
  const total = watchOrderStats.totalNotes ?? items.length;
  const completed = watchOrderStats.completedVideos ?? items.filter((item) => item.type === 'video').length;
  const failed = watchOrderStats.failedVideos ?? items.filter((item) => item.type === 'failed').length;
  const skipped = watchOrderStats.skippedNotes ?? items.filter((item) => item.type === 'skipped').length;
  document.getElementById('stat-total').textContent = total;
  document.getElementById('stat-success').textContent = completed;
  document.getElementById('stat-failed').textContent = skipped ? `${{failed}} / ${{skipped}}` : failed;
}}

function renderFilterBanner() {{
  const chips = filterOrder.map(buildFilterChip);
  const content = chips.length > 1
    ? `${{chips[0]}}<div class="filter-sep"></div>${{chips.slice(1).join('')}}`
    : chips.join('');
  document.getElementById('filter-banner').innerHTML = content;
}}

function renderList() {{
  const filtered = activeFilter === 'all'
    ? items
    : items.filter((item) => item.filterKey === activeFilter || item.type === activeFilter);
  document.getElementById('rank-list').innerHTML = filtered.map(buildRankCard).join('');
  const emptyState = document.getElementById('empty-state');
  if (filtered.length === 0) {{
    const config = categoryConfig[activeFilter] || categoryConfig.all;
    emptyState.hidden = false;
    emptyState.style.setProperty('--empty-accent', config.accent);
    emptyState.style.setProperty('--empty-bg', config.bg);
    emptyState.style.setProperty('--empty-border', config.border);
    emptyState.innerHTML = buildEmptyState(activeFilter);
  }} else {{
    emptyState.hidden = true;
    emptyState.removeAttribute('style');
    emptyState.innerHTML = '';
  }}
}}

document.getElementById('filter-banner').addEventListener('click', (event) => {{
  const chip = event.target.closest('[data-filter]');
  if (!chip) {{
    return;
  }}
  activeFilter = chip.dataset.filter;
  renderFilterBanner();
  renderList();
}});

renderStats();
renderFilterBanner();
renderList();
</script>""".strip()


def render_watch_order_html(payload: dict[str, Any]) -> str:
    data = validate_watch_order_payload(payload)
    requested = int(data["requested_count"])
    completed = int(data["completed_count"])
    failed = int(data["failed_count"])
    skipped = int(data.get("skipped_count") or len(data.get("skipped") or []))
    page_title = watch_order_title(data)
    generated_at = display_generated_at(data["generated_at"])
    meta_line = source_meta_line(data)
    ordered_items = data.get("watch_order_items")
    items = ordered_items if isinstance(ordered_items, list) else (
        successful_card_items(data["videos"])
        + failure_card_items(data["failures"])
        + skipped_card_items(data.get("skipped") or [])
    )
    stat_total_label = "总 note 数" if clean_text(data.get("source_subkind")) == "xiaohongshu_board" else "总视频数"
    stat_success_label = "视频成功" if clean_text(data.get("source_subkind")) == "xiaohongshu_board" else "解析成功"
    stat_failed_label = "失败 / 跳过" if skipped else "解析失败"
    stats = {
        "totalNotes": requested,
        "completedVideos": completed,
        "failedVideos": failed,
        "skippedNotes": skipped,
        "videoNotes": int(data.get("video_note_count") or completed + failed),
        "nonVideoNotes": int(data.get("non_video_count") or skipped),
    }
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(page_title)}</title>
<style>
{reference_style()}
</style>
</head>
<body>
<div class="page">
  <div class="topbar">
    <div>
      <div class="page-title">Video Intelligence</div>
      <div class="page-heading">{esc(page_title)}</div>
    </div>
    <div class="topbar-right">
      {esc(generated_at)}<br>
      {esc(meta_line)}{source_link_html(source_url_for_display(data))}
    </div>
  </div>

  <div class="stats-row">
    <div class="stat-box"><div class="stat-label">{esc(stat_total_label)}</div><div class="stat-val blue" id="stat-total">{requested}</div></div>
    <div class="stat-box"><div class="stat-label">{esc(stat_success_label)}</div><div class="stat-val ok" id="stat-success">{completed}</div></div>
    <div class="stat-box"><div class="stat-label">{esc(stat_failed_label)}</div><div class="stat-val red" id="stat-failed">{failed}{f" / {skipped}" if skipped else ""}</div></div>
  </div>

  <div class="section-block">
    <div class="section-head">
      <div class="section-label">播放列表顺序 · 原始顺序</div>
      <div class="section-line"></div>
    </div>
    <div class="filter-banner" id="filter-banner"></div>
    <div id="rank-list"></div>
    <div class="empty-state" id="empty-state" hidden></div>
  </div>

  <div class="footer">
    <span>WATCHBRIEF · WATCH ORDER</span>
    <span>{esc(generated_at)}</span>
  </div>
</div>
{render_watch_order_script(items, stats)}
</body>
</html>"""


def build_watch_order_payload(manifest: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    videos: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    watch_order_items: list[dict[str, Any]] = []
    for item in manifest.get("items", []):
        if item.get("status") == "completed":
            payload_path = Path(str(item["payload_path"]))
            video = json.loads(payload_path.read_text(encoding="utf-8"))
            video["page_file"] = Path(str(item["html_path"])).name
            videos.append(video)
            watch_order_items.append(successful_card_item(video))
        elif item.get("status") == "failed":
            error = item.get("error", {}) if isinstance(item.get("error"), dict) else {}
            failure = {
                "title": str(item.get("title") or "Untitled Video"),
                "url": str(item.get("url") or error.get("url") or ""),
                "stage": str(error.get("stage") or "pipeline"),
                "error": str(error.get("error") or error.get("reason_code") or "unknown failure"),
            }
            failures.append(failure)
            watch_order_items.append(failure_card_item(failure))
        elif item.get("status") == "skipped":
            skipped_item = {
                "title": str(item.get("title") or "Untitled Video"),
                "url": str(item.get("url") or ""),
                "stage": str(item.get("stage") or "resolver"),
                "reason_code": str(item.get("reason_code") or "skipped"),
                "error": str(item.get("error") or item.get("reason_code") or "skipped"),
                "note_type": str(item.get("note_type") or ""),
                "note_media_kind": str(item.get("note_media_kind") or ""),
            }
            skipped.append(skipped_item)
            watch_order_items.append(skipped_card_item(skipped_item))
    return {
        "job_name": str(manifest.get("source_url") or output_dir.name),
        "playlist_title": str(manifest.get("playlist_title") or ""),
        "source_url": str(manifest.get("source_url") or ""),
        "source_kind": str(manifest.get("source_kind") or ""),
        "source_subkind": str(manifest.get("source_subkind") or ""),
        "source_platform": str(manifest.get("source_platform") or ""),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M CST"),
        "requested_count": int(manifest.get("total_count") or len(videos) + len(failures)),
        "completed_count": int(manifest.get("completed_count") or len(videos)),
        "failed_count": int(manifest.get("failed_count") or len(failures)),
        "skipped_count": int(manifest.get("skipped_count") or len(skipped)),
        "video_note_count": int(manifest.get("video_note_count") or len(videos) + len(failures)),
        "non_video_count": int(manifest.get("non_video_count") or len(skipped)),
        "videos": videos,
        "failures": failures,
        "skipped": skipped,
        "watch_order_items": watch_order_items,
    }


def write_watch_order(manifest: dict[str, Any], output_dir: Path) -> Path:
    payload = build_watch_order_payload(manifest, output_dir)
    html = render_watch_order_html(payload)
    output_path = output_dir / "00-watch-order.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Render WatchBrief V5 watch-order page from a list manifest.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    path = write_watch_order(manifest, args.output_dir)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
