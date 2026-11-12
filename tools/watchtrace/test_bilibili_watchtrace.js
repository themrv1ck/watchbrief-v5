const assert = require("assert");
const WatchTrace = require("./bilibili_watchtrace.user.js");

class FakeNode {
  constructor({ text = "", title = "", href = "" } = {}) {
    this.textContent = text;
    this.href = href;
    this._title = title;
  }

  getAttribute(name) {
    if (name === "title") return this._title;
    if (name === "href") return this.href;
    return "";
  }
}

class FakeCard {
  constructor(selectors) {
    this.selectors = selectors;
  }

  querySelector(selector) {
    if (selector.includes('a[href*="/video/BV"]')) return this.selectors.link || null;
    return this.selectors[selector] || null;
  }
}

class FakeDocument {
  constructor(cards) {
    this.cards = cards;
  }

  querySelectorAll(selector) {
    assert(selector.includes("li"));
    return this.cards;
  }
}

async function testDurationAndProgressParsing() {
  assert.equal(WatchTrace.parseDurationToSeconds("1:02:03"), 3723);
  assert.equal(WatchTrace.parseDurationToSeconds("12:34"), 754);
  assert.equal(WatchTrace.parseDurationToSeconds("1小时2分钟3秒"), 3723);

  assert.deepEqual(WatchTrace.parseProgressFromText("已看完", 1200), {
    watched_seconds: 1200,
    watch_ratio: 1,
  });
  assert.deepEqual(WatchTrace.parseProgressFromText("看到 05:00", 1200), {
    watched_seconds: 300,
    watch_ratio: 0.25,
  });
  assert.deepEqual(WatchTrace.parseProgressFromText("25%", 1200), {
    watched_seconds: 300,
    watch_ratio: 0.25,
  });
}

async function testApiNormalization() {
  const item = {
    title: "真实 Agent 落地演示",
    author_name: "Seth Lab",
    duration: 1800,
    progress: 1700,
    history: { bvid: "BV1abc123456" },
  };
  const event = WatchTrace.normalizeApiItem(item, "history", "2026-06-14T20:30:00+08:00");
  assert.equal(event.schema_version, "watchbefore.event.v1");
  assert.equal(event.platform, "bilibili");
  assert.equal(event.url, "https://www.bilibili.com/video/BV1abc123456");
  assert.equal(event.bvid, "BV1abc123456");
  assert.equal(event.title, "真实 Agent 落地演示");
  assert.equal(event.author, "Seth Lab");
  assert.equal(event.duration_seconds, 1800);
  assert.equal(event.watched_seconds, 1700);
  assert.equal(event.watch_ratio, 0.9444);
  assert.equal(event.source, "history");

  const watchLater = WatchTrace.normalizeApiItem(
    {
      title: "稍后再看的视频",
      owner: { name: "UP 主" },
      duration: 2400,
      progress: 1200,
      bvid: "BV1later1234",
    },
    "watch_later",
    "2026-06-14T20:31:00+08:00",
  );
  assert.equal(watchLater.watched_seconds, 0);
  assert.equal(watchLater.watch_ratio, 0);
  assert.equal(watchLater.source, "watch_later");
}

async function testCollectEventsFromMockedBilibiliApis() {
  const calls = [];
  async function fetchJson(url) {
    calls.push(url);
    if (url.includes("/history/cursor") && url.includes("max=0")) {
      return {
        data: {
          list: [
            {
              title: "看完的技术视频",
              author_name: "A",
              duration: 100,
              progress: -1,
              history: { bvid: "BV1done00001" },
            },
            {
              title: "只看一半的访谈",
              author_name: "B",
              duration: 200,
              progress: 80,
              history: { bvid: "BV1half00002" },
            },
          ],
          cursor: { max: 1, view_at: 2 },
        },
      };
    }
    if (url.includes("/history/cursor")) {
      return { data: { list: [], cursor: { max: 1, view_at: 2 } } };
    }
    if (url.includes("/history/toview")) {
      return {
        data: {
          list: [
            {
              title: "稍后再看 AI 视频",
              owner: { name: "C" },
              duration: 300,
              progress: 0,
              bvid: "BV1later0003",
            },
          ],
        },
      };
    }
    throw new Error(`Unexpected URL ${url}`);
  }

  const result = await WatchTrace.collectEvents({
    fetchJson,
    observedAt: "2026-06-14T20:30:00+08:00",
    maxPages: 3,
  });

  assert.equal(result.errors.length, 0);
  assert.equal(calls.some((url) => url.includes("/history/cursor")), true);
  assert.equal(calls.some((url) => url.includes("/history/toview")), true);
  assert.equal(result.events.length, 3);
  assert.equal(result.events[0].watch_ratio, 1);
  assert.equal(result.events[1].watch_ratio, 0.4);
  assert.equal(result.events[2].source, "watch_later");

  const output = WatchTrace.jsonl(result.events);
  assert.equal(output.trim().split("\n").length, 3);
  assert.deepEqual(JSON.parse(output.trim().split("\n")[0]).bvid, "BV1done00001");
}

async function testBilibiliLoginErrorIsNotSilentSuccess() {
  const result = await WatchTrace.collectEvents({
    fetchJson: async () => ({ code: -101, message: "账号未登录", ttl: 1 }),
    observedAt: "2026-06-14T20:35:00+08:00",
    maxPages: 1,
  });

  assert.equal(result.events.length, 0);
  assert.equal(result.errors.length, 2);
  assert(result.errors[0].includes("账号未登录"));
}

async function testDomFallbackExtraction() {
  const cards = [
    new FakeCard({
      link: new FakeNode({
        title: "DOM 标题",
        href: "https://www.bilibili.com/video/BV1dom000001/?spm_id_from=333",
      }),
      ".name": new FakeNode({ text: "DOM UP" }),
      ".duration": new FakeNode({ text: "10:00" }),
      ".progress": new FakeNode({ text: "看到 02:30" }),
    }),
  ];
  const events = WatchTrace.extractDomEvents(new FakeDocument(cards), "history", "2026-06-14T20:40:00+08:00");
  assert.equal(events.length, 1);
  assert.equal(events[0].url, "https://www.bilibili.com/video/BV1dom000001");
  assert.equal(events[0].title, "DOM 标题");
  assert.equal(events[0].author, "DOM UP");
  assert.equal(events[0].duration_seconds, 600);
  assert.equal(events[0].watched_seconds, 150);
  assert.equal(events[0].watch_ratio, 0.25);
}

async function run() {
  await testDurationAndProgressParsing();
  await testApiNormalization();
  await testCollectEventsFromMockedBilibiliApis();
  await testBilibiliLoginErrorIsNotSilentSuccess();
  await testDomFallbackExtraction();
  console.log("WatchTrace Tampermonkey tests passed");
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
