#!/usr/bin/env python3
"""Browser-page YouTube transcript fallback providers.

This is a last-mile local fallback for cases where yt-dlp / youtube-transcript-api
are blocked by YouTube bot checks but the user-authorized browser tab can play
the video and exposes transcript segments in ``ytInitialData``.
"""

from __future__ import annotations

import json
import math
import platform
import re
import subprocess
from typing import Any

try:
    from ..acquisition_errors import SubtitleUnavailableError
    from ..transcript_source_adapter import clean_text, normalize_language, seconds_to_timestamp
    from .youtube_connect_provider import YouTubeConnectTranscript, extract_youtube_video_id
except ImportError:  # pragma: no cover - direct script execution
    from acquisition_errors import SubtitleUnavailableError
    from transcript_source_adapter import clean_text, normalize_language, seconds_to_timestamp
    from providers.youtube_connect_provider import YouTubeConnectTranscript, extract_youtube_video_id


SAFARI_TRANSCRIPT_APPLESCRIPT = r'''
on run argv
  set targetId to item 1 of argv
  tell application "Safari"
    repeat with wi from 1 to count of windows
      repeat with ti from 1 to count of tabs of window wi
        set u to URL of tab ti of window wi
        if u contains targetId then
          set jsClick to "(()=>{try{const candidates=Array.from(document.querySelectorAll('button, ytd-button-renderer, yt-button-shape, tp-yt-paper-button')); const button=candidates.find(el=>{const text=(el.innerText||el.textContent||el.getAttribute('aria-label')||'').trim(); const rect=el.getBoundingClientRect(); return rect.width>0 && rect.height>0 && /Transcript|transcript|Show transcript|转写文稿|内容转文字/.test(text);}); if(button){button.scrollIntoView({block:'center'}); button.click(); return 'clicked';} return 'no_visible_button';}catch(e){return 'click_error:'+String(e)}})()"
          do JavaScript jsClick in tab ti of window wi
          delay 2
          set jsExtract to "(()=>{try{const data=window.ytInitialData||{}; const pr=window.ytInitialPlayerResponse||{}; const title=pr?.videoDetails?.title||document.title||''; const durationSeconds=Number(pr?.videoDetails?.lengthSeconds||0); const channel=pr?.videoDetails?.author||''; const segments=[]; const seen=new Set(); function textFromRuns(value){ if(!value) return ''; if(typeof value.simpleText==='string') return value.simpleText; if(Array.isArray(value.runs)) return value.runs.map(r=>r.text||'').join(''); return ''; } function walk(o){ if(!o || typeof o!=='object') return; const r=o.transcriptSegmentRenderer; if(r){ const startMs=Number(r.startMs||0); const endMs=Number(r.endMs||0); const text=textFromRuns(r.snippet).trim(); const key=startMs+'-'+endMs+'-'+text; if(text && !seen.has(key)){seen.add(key); segments.push({start_ms:startMs,end_ms:endMs,text});} } for(const v of Object.values(o)){ if(v && typeof v==='object') walk(v); } } walk(data); segments.sort((a,b)=>(a.start_ms||0)-(b.start_ms||0)); return JSON.stringify({ok:true,title,channel,duration_seconds:durationSeconds,segments});}catch(e){return JSON.stringify({ok:false,error:String(e),stack:e.stack})}})()"
          set out to do JavaScript jsExtract in tab ti of window wi
          if out is missing value then return "{\"ok\":false,\"error\":\"missing_value\"}"
          return out as text
        end if
      end repeat
    end repeat
  end tell
  return "{\"ok\":false,\"error\":\"safari_tab_not_found\"}"
end run
'''

CHROME_TRANSCRIPT_APPLESCRIPT = r'''
on run argv
  set targetId to item 1 of argv
  tell application "Google Chrome"
    repeat with browserWindow in windows
      repeat with browserTab in tabs of browserWindow
        set u to URL of browserTab
        if u contains targetId then
          set jsClick to "(()=>{try{const candidates=Array.from(document.querySelectorAll('button, ytd-button-renderer, yt-button-shape, tp-yt-paper-button')); const button=candidates.find(el=>{const text=(el.innerText||el.textContent||el.getAttribute('aria-label')||'').trim(); const rect=el.getBoundingClientRect(); return rect.width>0 && rect.height>0 && /Transcript|transcript|Show transcript|转写文稿|内容转文字/.test(text);}); if(button){button.scrollIntoView({block:'center'}); button.click(); return 'clicked';} return 'no_visible_button';}catch(e){return 'click_error:'+String(e)}})()"
          execute browserTab javascript jsClick
          delay 2
          set jsExtract to "(()=>{try{const data=window.ytInitialData||{}; const pr=window.ytInitialPlayerResponse||{}; const title=pr?.videoDetails?.title||document.title||''; const durationSeconds=Number(pr?.videoDetails?.lengthSeconds||0); const channel=pr?.videoDetails?.author||''; const segments=[]; const seen=new Set(); function textFromRuns(value){ if(!value) return ''; if(typeof value.simpleText==='string') return value.simpleText; if(Array.isArray(value.runs)) return value.runs.map(r=>r.text||'').join(''); return ''; } function walk(o){ if(!o || typeof o!=='object') return; const r=o.transcriptSegmentRenderer; if(r){ const startMs=Number(r.startMs||0); const endMs=Number(r.endMs||0); const text=textFromRuns(r.snippet).trim(); const key=startMs+'-'+endMs+'-'+text; if(text && !seen.has(key)){seen.add(key); segments.push({start_ms:startMs,end_ms:endMs,text});} } for(const v of Object.values(o)){ if(v && typeof v==='object') walk(v); } } walk(data); segments.sort((a,b)=>(a.start_ms||0)-(b.start_ms||0)); return JSON.stringify({ok:true,title,channel,duration_seconds:durationSeconds,segments});}catch(e){return JSON.stringify({ok:false,error:String(e),stack:e.stack})}})()"
          set out to execute browserTab javascript jsExtract
          if out is missing value then return "{\"ok\":false,\"error\":\"missing_value\"}"
          return out as text
        end if
      end repeat
    end repeat
  end tell
  return "{\"ok\":false,\"error\":\"chrome_tab_not_found\"}"
end run
'''


def _run_osascript(script: str, *args: str, timeout: int = 30, label: str = "Browser") -> str:
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise SubtitleUnavailableError(f"{label} transcript fallback failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _segment_from_ms(row: dict[str, Any]) -> dict[str, str] | None:
    text = clean_text(row.get("text", ""))
    if not text:
        return None
    try:
        start_seconds = max(0, int(math.floor(float(row.get("start_ms") or 0) / 1000.0)))
        end_seconds = max(start_seconds + 1, int(math.ceil(float(row.get("end_ms") or 0) / 1000.0)))
    except (TypeError, ValueError):
        return None
    return {
        "start": seconds_to_timestamp(start_seconds),
        "end": seconds_to_timestamp(end_seconds),
        "text": text,
    }


def _transcript_from_browser_payload(
    url: str,
    payload: dict[str, Any],
    *,
    provider: str,
) -> YouTubeConnectTranscript:
    video_id = extract_youtube_video_id(url)
    segments = []
    for row in payload.get("segments") or []:
        if isinstance(row, dict):
            segment = _segment_from_ms(row)
            if segment:
                segments.append(segment)
    if not segments:
        raise SubtitleUnavailableError(f"{provider} transcript fallback returned no usable transcript segments")

    duration_seconds = int(payload.get("duration_seconds") or 0)
    if duration_seconds <= 0:
        last_end = segments[-1].get("end") if segments else ""
        parts = [int(part) for part in re.findall(r"\d+", str(last_end))]
        if len(parts) == 3:
            duration_seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            duration_seconds = parts[0] * 60 + parts[1]
        else:
            duration_seconds = 1

    return YouTubeConnectTranscript(
        provider=provider,
        source_platform="youtube",
        video_id=video_id,
        title=str(payload.get("title") or video_id),
        duration=seconds_to_timestamp(duration_seconds),
        playlist_title="",
        subtitle_kind="browser_transcript",
        subtitle_lang="zh-Hans",
        subtitle_format="yt_initial_data_transcript",
        segments=segments,
        plain_text=" ".join(segment["text"] for segment in segments),
        source_url=url,
    )


def _fetch_youtube_browser_transcript(url: str, *, script: str, provider: str, tab_error_label: str) -> YouTubeConnectTranscript:
    """Return transcript material from an already-open browser YouTube page."""
    if platform.system() != "Darwin":
        raise SubtitleUnavailableError(f"{tab_error_label} transcript fallback requires macOS")
    video_id = extract_youtube_video_id(url)
    if not video_id:
        raise SubtitleUnavailableError(f"{tab_error_label} transcript fallback requires a YouTube video id")

    raw = _run_osascript(script, video_id, timeout=45, label=tab_error_label)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SubtitleUnavailableError(f"{tab_error_label} transcript fallback returned invalid JSON") from exc
    if not payload.get("ok"):
        raise SubtitleUnavailableError(f"{tab_error_label} transcript unavailable: {payload.get('error') or 'unknown'}")
    return _transcript_from_browser_payload(url, payload, provider=provider)


def fetch_youtube_chrome_transcript(url: str) -> YouTubeConnectTranscript:
    """Return transcript material from an already-open Chrome YouTube page.

    The user must have authorized Chrome control for WatchBrief. This function is
    intentionally local/macOS-only and never reads browser cookies directly.
    """
    return _fetch_youtube_browser_transcript(
        url,
        script=CHROME_TRANSCRIPT_APPLESCRIPT,
        provider="chrome-transcript",
        tab_error_label="Chrome",
    )


def fetch_youtube_safari_transcript(url: str) -> YouTubeConnectTranscript:
    """Return transcript material from an already-open Safari YouTube page.

    The user must have authorized Safari control for WatchBrief. This function is
    intentionally local/macOS-only and never reads browser cookies directly.
    """
    return _fetch_youtube_browser_transcript(
        url,
        script=SAFARI_TRANSCRIPT_APPLESCRIPT,
        provider="safari-transcript",
        tab_error_label="Safari",
    )
