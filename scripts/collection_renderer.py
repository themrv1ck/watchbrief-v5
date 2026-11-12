"""Continuous reading for related episodes; content is supplied by analysis, never composed here."""

from __future__ import annotations

from html import escape
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


STYLE = Path(__file__).resolve().parents[1] / "templates" / "watchbefore-collection.css"
READING_THEME = STYLE.with_name("watchbefore-reading-theme.css")


def is_same_bvid_multipart(source_url: str, entries: list[dict[str, Any]]) -> bool:
    """Recognise legacy manifests by explicit episode URLs, never by similar titles."""
    source = urlsplit(source_url)
    match = re.fullmatch(r"/video/(BV[A-Za-z0-9]+)/?", source.path)
    if not match or not (source.hostname or "").endswith(".bilibili.com") or len(entries) < 2:
        return False
    if any(key in dict(parse_qsl(source.query)) for key in ("p", "page", "cid")):
        return False
    pages = []
    for entry in entries:
        parts = urlsplit(str(entry.get("url") or ""))
        page_match = re.fullmatch(r"/video/(BV[A-Za-z0-9]+)/?", parts.path)
        selectors = dict(parse_qsl(parts.query))
        page = selectors.get("p", "")
        if not page_match or page_match[1] != match[1] or not (parts.hostname or "").endswith(".bilibili.com") or not page.isdigit():
            return False
        pages.append(int(page))
    return sorted(pages) == list(range(1, len(entries) + 1))


def esc(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def safe_url(value: Any) -> str:
    url = str(value or "").strip()
    parts = urlsplit(url)
    return url if parts.scheme in ("http", "https") and parts.netloc else ""


def local_report(value: Any) -> str:
    name = str(value or "")
    if not name.endswith(".html") or name != Path(name).name or "\\" in name:
        return ""
    return quote(name)


def segment_url(url: Any, timestamp: str) -> str:
    source = safe_url(url)
    if not source:
        return ""
    parts = urlsplit(source)
    try:
        seconds = 0
        for component in timestamp.split(":"):
            seconds = seconds * 60 + int(component)
    except ValueError:
        return source
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "t"]
    query.append(("t", str(seconds)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def collection_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the manifest's order, including gaps, rather than joining successes first."""
    videos = data["videos"]
    by_file = {video.get("page_file"): video for video in videos}
    cards = data.get("watch_order_items")
    if cards is None:
        cards = [dict(type="video", pageFile=v.get("page_file")) for v in videos]
        if data.get("failures"):
            # Failure order cannot be reconstructed from a list of successes.
            raise ValueError("multipart failures require ordered watch_order_items")
    entries = []
    for index, card in enumerate(cards, 1):
        if card.get("type") == "video":
            video = by_file.get(card.get("pageFile"))
            if video is None:
                raise ValueError(f"multipart chapter {index} has no matching video payload")
            entries.append(dict(index=index, status="completed", video=video, title=video["title"]))
        else:
            entries.append(dict(index=index, status=card.get("type"), title=card.get("title"), error=card.get("summary")))
    if len(entries) != int(data["requested_count"]):
        raise ValueError("multipart ordered chapter count does not match requested_count")
    if sum(e["status"] == "completed" for e in entries) != int(data["completed_count"]):
        raise ValueError("multipart completed chapter count does not match completed_count")
    return entries


def validate_collection_summary(summary: Any, count: int) -> dict[str, Any] | None:
    if summary is None:
        return None
    if not isinstance(summary, dict):
        raise ValueError("collection_summary must be an object")
    for key in ("overview", "brief", "conclusion"):
        if not isinstance(summary.get(key), str) or not summary[key].strip():
            raise ValueError(f"collection_summary.{key} must be nonempty text")
    groups = summary.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("collection_summary.groups must be nonempty")
    next_index = 1
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("collection group must be an object")
        start, end = group.get("start"), group.get("end")
        if type(start) is not int or type(end) is not int or start != next_index or not start <= end <= count:
            raise ValueError("collection groups must cover chapters consecutively, without gaps or overlaps")
        for key in ("title", "summary"):
            if not isinstance(group.get(key), str) or not group[key].strip():
                raise ValueError(f"collection group {key} must be nonempty text")
        next_index = end + 1
    if next_index != count + 1:
        raise ValueError("collection groups must cover every requested chapter")
    return summary


def chapter_html(entry: dict[str, Any]) -> str:
    index = entry["index"]
    title = esc(entry["title"])
    if entry["status"] != "completed":
        return f'<article class="chapter missing" id="chapter-{index}"><div class="chapter-label">第 {index:02d} 集 · 内容缺失</div><h3>{title}</h3><p>本集未完成解析，此处保留原始位置，尚不能形成完整的合集总结。</p><details><summary>查看原因</summary><p>{esc(entry.get("error"))}</p></details></article>'
    video = entry["video"]
    overview = video.get("target_summary")
    if not isinstance(overview, str) or not overview.strip():
        raise ValueError(f"multipart chapter {index} is missing target_summary")
    beats = []
    for beat in video.get("reading_flow") or []:
        timestamp = f'{beat["start"]}–{beat["end"]}'
        href = segment_url(video.get("url"), beat["start"])
        beats.append(f'<div class="beat"><h4>{esc(beat["heading"])}</h4><p>{esc(beat["body"])}</p><a class="time" href="{esc(href)}" target="_blank" rel="noopener noreferrer">P{index:02d} · {esc(timestamp)} ↗</a></div>')
    details = f'<details class="chapter-detail"><summary>展开本集的具体内容</summary><div class="beats">{"".join(beats)}</div></details>' if beats else ""
    return f'<article class="chapter" id="chapter-{index}"><header class="chapter-heading"><span class="chapter-number">P{index:02d}</span><div><h3>{title}</h3><p class="chapter-meta">{esc(video.get("duration"))}</p></div></header><p class="chapter-summary">{esc(overview)}</p>{details}</article>'


def render_collection_html(data: dict[str, Any]) -> str:
    entries = collection_entries(data)
    count = len(entries)
    synthesis = validate_collection_summary(data.get("collection_summary"), count)
    complete = int(data["completed_count"]) == count
    groups = synthesis["groups"] if synthesis else [dict(start=1, end=count, title="分集内容", summary="")]
    title = data.get("playlist_title") or "视频合集"
    nav, sections = [], []
    for index, group in enumerate(groups, 1):
        span = f'P{group["start"]:02d}–P{group["end"]:02d}'
        nav.append(f'<a href="#part-{index}"><span>主题 {index:02d} · {esc(span)}</span><strong>{esc(group["title"])}</strong></a>')
        chapters = "".join(chapter_html(e) for e in entries[group["start"] - 1:group["end"]])
        lead = f'<p class="part-lead">{esc(group["summary"])}</p>' if group["summary"] else ""
        sections.append(f'<section class="part" id="part-{index}" aria-labelledby="part-title-{index}"><header class="part-header"><div class="part-eyebrow"><span class="part-sequence">主题 {index:02d}</span><span class="part-range">{esc(span)}</span></div><h2 id="part-title-{index}">{esc(group["title"])}</h2>{lead}</header><div class="part-chapters">{chapters}</div></section>')
    rows, caveats = [], []
    for entry in entries:
        index = entry["index"]
        if entry["status"] != "completed":
            rows.append(f'<tr><td>{index:02d}</td><th scope="row">{esc(entry["title"])}</th><td colspan="3">未完成解析</td></tr>')
            continue
        video = entry["video"]
        report = local_report(video.get("page_file"))
        original = safe_url(video.get("url"))
        links = (f'<a href="{esc(report)}">单集报告</a>' if report else "") + (f'<a href="{esc(original)}" target="_blank" rel="noopener noreferrer">原片 ↗</a>' if original else "")
        rows.append(f'<tr><td>{index:02d}</td><th scope="row"><a href="#chapter-{index}">{esc(entry["title"])}</a></th><td>{esc(video.get("duration"))}</td><td class="score">{float(video["replacement_score"]):.1f}</td><td class="row-links">{links}</td></tr>')
        if video.get("content_caveat"):
            caveats.append(f'<li><span>P{index:02d}</span> {esc(video["content_caveat"])}</li>')
    overview = f'<p class="overview">{esc(synthesis["overview"])}</p><div class="brief"><span>省流</span><p>{esc(synthesis["brief"])}</p></div>' if synthesis else '<p class="overview">以下按原片顺序连续呈现分集总结；尚未提供跨集主线总结。</p>'
    conclusion = f'<section class="conclusion" id="conclusion"><div class="part-label">合集主旨</div><p>{esc(synthesis["conclusion"])}</p></section>' if synthesis else ""
    warning = '<p class="coverage-warning" role="status">部分集数尚未解析，以下仅总结已完成的内容；缺失集数保留在原位置。</p>' if not complete else ""
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · 合集总结 · WatchBefore</title><style>{READING_THEME.read_text(encoding="utf-8")}{STYLE.read_text(encoding="utf-8")}</style></head>
<body><div class="page"><header class="topbar"><a class="brand" href="#overview">WatchBefore <span>合集</span></a><nav aria-label="主菜单"><a href="#overview">合集总结</a><a href="#reading">内容脉络</a><a href="#episodes">分集与评分</a></nav></header>
<main><header class="collection-hero" id="overview"><div class="eyebrow">COLLECTION / 连续阅读</div><h1>{esc(title)}</h1><p class="meta">共 {count} 集 · 已总结 {int(data["completed_count"])} 集 · 按原片顺序展开</p>{warning}{overview}</header>
<div class="reading-layout" id="reading"><aside class="contents"><nav aria-label="内容目录"><div class="contents-title">内容脉络</div>{"".join(nav)}<a href="#episodes" class="directory-link">分集与评分 ↓</a></nav></aside><div class="reading-body">{"".join(sections)}{conclusion}
<section class="episode-directory" id="episodes"><div class="part-label">分集索引</div><h2>分集与评分</h2><p class="directory-note">评分沿用各集原始报告，均为 10 分制。时间锚对应各集自己的时间轴。</p><div class="table-scroll" tabindex="0" role="region" aria-label="分集评分表"><table><thead><tr><th scope="col">集</th><th scope="col">内容</th><th scope="col">时长</th><th scope="col">评分</th><th scope="col">入口</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div><details class="boundaries"><summary>各集内容的适用边界</summary><ul>{"".join(caveats)}</ul></details></section>
</div></div></main><footer>WATCHBEFORE · 合集总结 <a href="{esc(safe_url(data.get("source_url")))}" target="_blank" rel="noopener noreferrer">打开原合集 ↗</a></footer></div></body></html>'''
