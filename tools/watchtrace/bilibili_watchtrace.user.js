// ==UserScript==
// @name         WatchTrace Bilibili Exporter
// @namespace    https://watchbrief.local/watchtrace
// @version      0.1.0
// @description  Export Bilibili watch history and watch-later behavior for WatchBefore.
// @match        https://www.bilibili.com/*
// @match        https://space.bilibili.com/*
// @match        https://t.bilibili.com/*
// @grant        GM_download
// @grant        GM_setClipboard
// @grant        GM_xmlhttpRequest
// @connect      api.bilibili.com
// @run-at       document-idle
// ==/UserScript==

(function initWatchTrace(globalObject) {
  "use strict";

  const SCHEMA_VERSION = "watchbefore.event.v1";
  const HISTORY_ENDPOINT = "https://api.bilibili.com/x/web-interface/history/cursor";
  const WATCH_LATER_ENDPOINT = "https://api.bilibili.com/x/v2/history/toview";
  const MAX_HISTORY_PAGES = 10;
  const HISTORY_PAGE_SIZE = 30;

  function cleanText(value) {
    return String(value ?? "").replace(/\s+/g, " ").trim();
  }

  function toNumber(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
      const normalized = value.replace(/,/g, "").trim();
      if (normalized && Number.isFinite(Number(normalized))) return Number(normalized);
    }
    return null;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function parseDurationToSeconds(value) {
    const numeric = toNumber(value);
    if (numeric !== null) return Math.max(0, Math.round(numeric));

    const text = cleanText(value);
    if (!text) return 0;

    const colonMatch = text.match(/(?:(\d{1,2}):)?(\d{1,2}):(\d{2})/);
    if (colonMatch) {
      const hours = Number(colonMatch[1] || 0);
      const minutes = Number(colonMatch[2] || 0);
      const seconds = Number(colonMatch[3] || 0);
      return hours * 3600 + minutes * 60 + seconds;
    }

    const shortColonMatch = text.match(/\b(\d{1,2}):(\d{2})\b/);
    if (shortColonMatch) {
      return Number(shortColonMatch[1]) * 60 + Number(shortColonMatch[2]);
    }

    let total = 0;
    const hour = text.match(/(\d+(?:\.\d+)?)\s*(?:小时|时|h)/i);
    const minute = text.match(/(\d+(?:\.\d+)?)\s*(?:分钟|分|m)/i);
    const second = text.match(/(\d+(?:\.\d+)?)\s*(?:秒|s)/i);
    if (hour) total += Number(hour[1]) * 3600;
    if (minute) total += Number(minute[1]) * 60;
    if (second) total += Number(second[1]);
    return Math.max(0, Math.round(total));
  }

  function bvidFromUrl(url) {
    const text = String(url || "");
    const match = text.match(/\/video\/(BV[0-9A-Za-z]+)/i) || text.match(/\b(BV[0-9A-Za-z]+)\b/i);
    return match ? match[1] : "";
  }

  function canonicalVideoUrl(raw) {
    const text = String(raw || "");
    const bvid = bvidFromUrl(text);
    if (bvid) return `https://www.bilibili.com/video/${bvid}`;
    if (/^https?:\/\//i.test(text)) return text.split("?")[0].split("#")[0];
    return "";
  }

  function secondsFromProgress(progress, durationSeconds) {
    const progressNumber = toNumber(progress);
    const duration = Math.max(0, Math.round(toNumber(durationSeconds) || 0));
    if (progressNumber === null) return 0;
    if (progressNumber < 0 && duration > 0) return duration;
    if (duration > 0) return clamp(Math.round(progressNumber), 0, duration);
    return Math.max(0, Math.round(progressNumber));
  }

  function watchRatio(watchedSeconds, durationSeconds) {
    const duration = Math.max(0, Math.round(toNumber(durationSeconds) || 0));
    if (!duration) return 0;
    return Number(clamp((toNumber(watchedSeconds) || 0) / duration, 0, 1).toFixed(4));
  }

  function parseProgressFromText(text, durationSeconds) {
    const value = cleanText(text);
    const duration = Math.max(0, Math.round(toNumber(durationSeconds) || 0));
    if (!value) return { watched_seconds: 0, watch_ratio: 0 };
    if (/已看完|看完|100\s*%/.test(value)) {
      return { watched_seconds: duration, watch_ratio: duration ? 1 : 0 };
    }

    const percent = value.match(/(\d+(?:\.\d+)?)\s*%/);
    if (percent && duration) {
      const ratio = clamp(Number(percent[1]) / 100, 0, 1);
      const watched = Math.round(duration * ratio);
      return { watched_seconds: watched, watch_ratio: Number(ratio.toFixed(4)) };
    }

    const slash = value.match(/((?:(?:\d{1,2}:)?\d{1,2}:)?\d{1,2})\s*\/\s*((?:(?:\d{1,2}:)?\d{1,2}:)?\d{1,2})/);
    if (slash) {
      const watched = parseDurationToSeconds(slash[1]);
      const parsedDuration = parseDurationToSeconds(slash[2]) || duration;
      return { watched_seconds: secondsFromProgress(watched, parsedDuration), watch_ratio: watchRatio(watched, parsedDuration) };
    }

    const reached = value.match(/(?:看到|观看至|已观看|进度)\s*((?:(?:\d{1,2}:)?\d{1,2}:)?\d{1,2})/);
    if (reached) {
      const watched = parseDurationToSeconds(reached[1]);
      return { watched_seconds: secondsFromProgress(watched, duration), watch_ratio: watchRatio(watched, duration) };
    }

    return { watched_seconds: 0, watch_ratio: 0 };
  }

  function normalizeApiItem(item, source, observedAt) {
    const history = item && typeof item.history === "object" ? item.history : {};
    const bvid = cleanText(item.bvid || history.bvid || item.param || "");
    const url = canonicalVideoUrl(item.uri || item.url || (bvid ? `https://www.bilibili.com/video/${bvid}` : ""));
    const durationSeconds = parseDurationToSeconds(item.duration || item.page_duration || item.total_duration);
    const watchedSeconds = source === "watch_later" ? 0 : secondsFromProgress(item.progress, durationSeconds);
    const normalized = {
      schema_version: SCHEMA_VERSION,
      platform: "bilibili",
      url,
      bvid: bvid || bvidFromUrl(url),
      title: cleanText(item.title || item.long_title || item.name),
      author: cleanText(item.author_name || item.author || item.owner?.name || item.upper?.name),
      duration_seconds: durationSeconds,
      watched_seconds: watchedSeconds,
      watch_ratio: source === "watch_later" ? 0 : watchRatio(watchedSeconds, durationSeconds),
      source,
      observed_at: observedAt,
    };
    return isUsableEvent(normalized) ? normalized : null;
  }

  function normalizeDomItem(raw, source, observedAt) {
    const durationSeconds = parseDurationToSeconds(raw.duration_text || raw.duration_seconds);
    const progress = parseProgressFromText(raw.progress_text || "", durationSeconds);
    const watchedSeconds = source === "watch_later" ? 0 : progress.watched_seconds;
    const normalized = {
      schema_version: SCHEMA_VERSION,
      platform: "bilibili",
      url: canonicalVideoUrl(raw.url),
      bvid: raw.bvid || bvidFromUrl(raw.url),
      title: cleanText(raw.title),
      author: cleanText(raw.author),
      duration_seconds: durationSeconds,
      watched_seconds: watchedSeconds,
      watch_ratio: source === "watch_later" ? 0 : progress.watch_ratio,
      source,
      observed_at: observedAt,
    };
    return isUsableEvent(normalized) ? normalized : null;
  }

  function isUsableEvent(event) {
    return Boolean(event && event.url && event.title);
  }

  function dedupeEvents(events) {
    const seen = new Set();
    const output = [];
    for (const event of events) {
      if (!event || !event.url) continue;
      const key = `${event.source}:${event.bvid || event.url}`;
      if (seen.has(key)) continue;
      seen.add(key);
      output.push(event);
    }
    return output;
  }

  function jsonl(events) {
    return events.map((event) => JSON.stringify(event)).join("\n") + (events.length ? "\n" : "");
  }

  async function fetchJson(url) {
    if (typeof GM_xmlhttpRequest === "function") {
      return new Promise((resolve, reject) => {
        GM_xmlhttpRequest({
          method: "GET",
          url,
          anonymous: false,
          withCredentials: true,
          responseType: "json",
          onload: (response) => {
            if (response.status < 200 || response.status >= 300) {
              reject(new Error(`HTTP ${response.status} ${url}`));
              return;
            }
            if (response.response && typeof response.response === "object") {
              resolve(response.response);
              return;
            }
            try {
              resolve(JSON.parse(response.responseText || "{}"));
            } catch (error) {
              reject(new Error(`Invalid JSON from ${url}: ${error.message}`));
            }
          },
          onerror: () => reject(new Error(`Request failed ${url}`)),
          ontimeout: () => reject(new Error(`Request timed out ${url}`)),
        });
      });
    }

    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} ${url}`);
    }
    return response.json();
  }

  function listFromApiPayload(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.data)) return payload.data;
    if (Array.isArray(payload?.data?.list)) return payload.data.list;
    if (Array.isArray(payload?.data?.items)) return payload.data.items;
    return [];
  }

  function assertBilibiliApiOk(payload, url) {
    const code = toNumber(payload?.code);
    if (code !== null && code !== 0) {
      throw new Error(`Bilibili API ${code}: ${cleanText(payload?.message || "unknown error")} (${url})`);
    }
  }

  async function fetchHistoryEvents(options = {}) {
    const observedAt = options.observedAt || new Date().toISOString();
    const fetcher = options.fetchJson || fetchJson;
    const maxPages = Math.max(1, Math.round(options.maxPages || MAX_HISTORY_PAGES));
    let max = 0;
    let viewAt = 0;
    const events = [];

    for (let page = 0; page < maxPages; page += 1) {
      const url = `${HISTORY_ENDPOINT}?ps=${HISTORY_PAGE_SIZE}&max=${encodeURIComponent(max)}&view_at=${encodeURIComponent(viewAt)}&business=archive`;
      const payload = await fetcher(url);
      assertBilibiliApiOk(payload, url);
      const list = listFromApiPayload(payload);
      events.push(...list.map((item) => normalizeApiItem(item, "history", observedAt)).filter(Boolean));

      const cursor = payload?.data?.cursor || {};
      const nextMax = toNumber(cursor.max);
      const nextViewAt = toNumber(cursor.view_at);
      if (!list.length || !nextMax || nextMax === max) break;
      max = nextMax;
      viewAt = nextViewAt || 0;
    }

    return events;
  }

  async function fetchWatchLaterEvents(options = {}) {
    const observedAt = options.observedAt || new Date().toISOString();
    const fetcher = options.fetchJson || fetchJson;
    const url = `${WATCH_LATER_ENDPOINT}?ps=100`;
    const payload = await fetcher(url);
    assertBilibiliApiOk(payload, url);
    return listFromApiPayload(payload).map((item) => normalizeApiItem(item, "watch_later", observedAt)).filter(Boolean);
  }

  function nearestText(element, selectors) {
    for (const selector of selectors) {
      const node = element.querySelector(selector);
      const text = cleanText(node?.textContent || node?.getAttribute?.("title"));
      if (text) return text;
    }
    return "";
  }

  function extractDomEvents(documentObject, source = "history", observedAt = new Date().toISOString()) {
    if (!documentObject?.querySelectorAll) return [];
    const cards = Array.from(documentObject.querySelectorAll("li, .history-record, .history-card, .bili-video-card, .video-card, .fav-video-list__item"));
    const rawItems = [];

    for (const card of cards) {
      const link = card.querySelector?.('a[href*="/video/BV"], a[href*="bilibili.com/video/BV"]');
      const href = link?.href || link?.getAttribute?.("href") || "";
      const url = canonicalVideoUrl(href);
      if (!url) continue;
      rawItems.push({
        url,
        title: cleanText(link?.getAttribute?.("title")) || nearestText(card, [".title", ".bili-video-card__info--tit", ".history-record__title", ".video-name", "h3", "a"]),
        author: nearestText(card, [".name", ".up-name", ".bili-video-card__info--author", ".history-record__author", ".author"]),
        duration_text: nearestText(card, [".duration", ".bili-video-card__stats__duration", ".history-record__duration", ".length"]),
        progress_text: nearestText(card, [".progress", ".history-record__progress", ".watch-progress", ".lastplay-time"]),
      });
    }

    return dedupeEvents(rawItems.map((item) => normalizeDomItem(item, source, observedAt)).filter(Boolean));
  }

  async function collectEvents(options = {}) {
    const observedAt = options.observedAt || new Date().toISOString();
    const errors = [];
    let events = [];

    try {
      events = events.concat(await fetchHistoryEvents({ ...options, observedAt }));
    } catch (error) {
      errors.push(`history_api: ${error.message}`);
    }

    try {
      events = events.concat(await fetchWatchLaterEvents({ ...options, observedAt }));
    } catch (error) {
      errors.push(`watch_later_api: ${error.message}`);
    }

    if (!events.length && options.documentObject) {
      events = events.concat(extractDomEvents(options.documentObject, "history", observedAt));
    }

    return { events: dedupeEvents(events), errors };
  }

  function downloadText(filename, text) {
    const blob = new Blob([text], { type: "application/x-ndjson;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    if (typeof GM_download === "function") {
      GM_download({ url, name: filename, saveAs: true, onload: () => URL.revokeObjectURL(url) });
      return;
    }
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function ensurePanel() {
    if (!globalObject.document || globalObject.document.getElementById("watchtrace-panel")) return;
    const panel = globalObject.document.createElement("div");
    panel.id = "watchtrace-panel";
    panel.style.cssText = [
      "position:fixed",
      "right:18px",
      "bottom:18px",
      "z-index:2147483647",
      "background:#111827",
      "color:#fff",
      "border:1px solid rgba(255,255,255,.2)",
      "border-radius:8px",
      "box-shadow:0 12px 30px rgba(0,0,0,.28)",
      "font:13px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
      "padding:10px",
      "width:220px",
    ].join(";");
    panel.innerHTML = [
      '<button id="watchtrace-export" style="width:100%;border:0;border-radius:6px;background:#22c55e;color:#052e16;font-weight:700;padding:8px 10px;cursor:pointer;">导出 WatchBefore 数据</button>',
      '<div id="watchtrace-status" style="margin-top:8px;line-height:1.45;color:#d1d5db;">历史页或稍后再看页打开后点击。</div>',
    ].join("");
    globalObject.document.body.appendChild(panel);

    globalObject.document.getElementById("watchtrace-export").addEventListener("click", async () => {
      const status = globalObject.document.getElementById("watchtrace-status");
      status.textContent = "正在读取 B 站历史和稍后再看...";
      try {
        const result = await collectEvents({ documentObject: globalObject.document });
        const output = jsonl(result.events);
        if (!result.events.length && result.errors.length) {
          status.textContent = `没有导出数据：${result.errors.join("；")}。请确认当前浏览器已登录 B 站，并在历史记录或稍后再看页面重试。`;
          globalObject.WatchTraceLastExport = result;
          return;
        }
        if (typeof GM_setClipboard === "function") GM_setClipboard(output, "text");
        downloadText(`watchbefore-bilibili-${new Date().toISOString().slice(0, 10)}.jsonl`, output);
        status.textContent = `已导出 ${result.events.length} 条。${result.errors.length ? "部分接口失败，已尽量保留可见页面数据。" : "已复制到剪贴板并下载文件。"}`;
        globalObject.WatchTraceLastExport = result;
      } catch (error) {
        status.textContent = `导出失败：${error.message}`;
      }
    });
  }

  const api = {
    SCHEMA_VERSION,
    HISTORY_ENDPOINT,
    WATCH_LATER_ENDPOINT,
    parseDurationToSeconds,
    parseProgressFromText,
    normalizeApiItem,
    normalizeDomItem,
    extractDomEvents,
    fetchHistoryEvents,
    fetchWatchLaterEvents,
    collectEvents,
    jsonl,
    dedupeEvents,
    assertBilibiliApiOk,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    globalObject.WatchTrace = api;
    ensurePanel();
  }
})(typeof unsafeWindow !== "undefined" ? unsafeWindow : typeof window !== "undefined" ? window : globalThis);
