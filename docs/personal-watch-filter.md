# WatchBefore Personal Filter Design

## 结论

产品名统一为 `WatchBefore`。它表示“看之前先理解内容”，不是替用户做最终观看决定。

第一版不重命名现有仓库目录、不改稳定 pipeline、不新建数据库。先做浏览器行为采集和本地偏好文件，让 WatchBefore 用真实观看行为学习用户的观影策略。

## 命名边界

| 名称 | 职责 |
| --- | --- |
| `WatchBefore` | 面向用户的观看前内容理解与个人时间过滤产品 |
| `watchbrief_v5` | 现有内部 skill id / 源码目录 / 兼容入口 |
| `WatchTrace` | 浏览器插件，只采集观看行为，不做内容理解 |
| `watch_profile` | 从行为和 WatchBefore 分析结果归纳出来的用户偏好文件 |

不建议把现有 `watchbrief_v5` 目录立即整体改名。当前代码已经稳定承载视频采集、转写、提炼、评分和 HTML 输出，路径级改名会产生大量无业务收益的 churn。

## 第一版目标

让系统先回答这个问题：

> 这个视频到底讲什么，内容本身的信息密度和证据质量如何，如果我要补原片应该先看哪里？

个人偏好进入后，再回答：

> 这类内容为什么可能适合我，或者为什么可能不适合我？

第一版不替用户做最终观看决定，不做通用推荐系统。

## 五步法约束

| 步骤 | 决定 |
| --- | --- |
| 质疑需求 | 需求成立：当前报告更像通用观看决策，不够贴近个人兴趣 |
| 删除 | 不做录屏、录音、弹幕分析、评论分析、复杂推荐流 |
| 简化 | 插件只导出 B 站历史和稍后再看行为 |
| 提速 | WatchBefore 复用现有内容分析链路批量分析样本 |
| 自动化 | 等人工确认样本有效后，再自动更新偏好文件 |

## 浏览器插件职责

插件只采集事实，不判断内容好坏。

必须采集：

| 字段 | 说明 |
| --- | --- |
| `platform` | 固定为 `bilibili` |
| `url` | 视频 URL |
| `bvid` | B 站视频 ID，能取到就写 |
| `title` | 视频标题 |
| `author` | UP 主 |
| `duration_seconds` | 视频总时长 |
| `watched_seconds` | 已观看时长 |
| `watch_ratio` | 已观看比例，0 到 1 |
| `source` | `history` 或 `watch_later` |
| `observed_at` | 采集时间，ISO 8601 |

暂不采集：

| 数据 | 原因 |
| --- | --- |
| 弹幕 | 噪音大，第一版无必要 |
| 评论 | 权限和噪音都更高 |
| 录屏 | 隐私成本高，且重复 WatchBefore 内容链路 |
| 录音 | 没必要，WatchBefore 已有转写路径 |
| 全网页内容 | 权限过大，容易污染数据 |

## 行为事件格式

文件路径：

```text
~/.watchbrief/preferences/events.jsonl
```

每行一个 JSON：

```json
{"schema_version":"watchbefore.event.v1","platform":"bilibili","url":"https://www.bilibili.com/video/BV...","bvid":"BV...","title":"示例标题","author":"示例UP主","duration_seconds":1800,"watched_seconds":1700,"watch_ratio":0.94,"source":"history","observed_at":"2026-06-14T20:30:00+08:00"}
```

稍后再看但未观看时：

```json
{"schema_version":"watchbefore.event.v1","platform":"bilibili","url":"https://www.bilibili.com/video/BV...","bvid":"BV...","title":"示例标题","author":"示例UP主","duration_seconds":2400,"watched_seconds":0,"watch_ratio":0,"source":"watch_later","observed_at":"2026-06-14T20:31:00+08:00"}
```

## 行为分类

第一版使用透明规则，不让模型猜。

| 分类 | 规则 |
| --- | --- |
| `completed` | `watch_ratio >= 0.9` |
| `partial` | `0.2 <= watch_ratio < 0.9` |
| `bounce` | `watch_ratio < 0.05` 且来自历史 |
| `queued` | `source == watch_later` |
| `potential_interest` | `queued` 或 `partial` |
| `strong_interest_sample` | 同类视频多次 `completed` |
| `weak_interest_sample` | 同类视频多次 `bounce` |

这些分类只是训练信号，不等于最终价值判断。最终解释必须结合 WatchBefore 对视频内容的分析。

## WatchBefore 新增能力

当前已落地三个命令能力：

```bash
python3 tools/watchtrace/export_bilibili_events.py \
  --browser arc \
  --output ~/.watchbrief/preferences/events.jsonl
```

```bash
python3 scripts/preferences/build_profile.py \
  --events ~/.watchbrief/preferences/events.jsonl \
  --output ~/.watchbrief/preferences/seth.yaml
```

第一条从浏览器登录态导出 B 站历史和稍后再看行为。第二条只生成“尚无内容结论”的画像骨架；它不会从行为桶猜兴趣。

第三条把偏好档案身份绑定到视频分析链路：

```bash
python3 scripts/cli.py \
  --source-url "<video_url>" \
  --preference-profile ~/.watchbrief/preferences/seth.yaml
```

当前第三条会记录 profile 路径、schema 和 hash，不把观看历史明文写进 review request。只有 WatchBefore 批量分析样本后由本地 Qwen 聚合出的统一兴趣领域会进入 `话题兴趣` 30% 分数；不再用作者级行为推断偏好。

下一阶段再接入人工纠偏能力：

```bash
python3 scripts/preferences/update_profile.py \
  --profile ~/.watchbrief/preferences/seth.yaml \
  --feedback ~/.watchbrief/preferences/feedback.jsonl
```

人工纠偏用于慢慢更新偏好。

## 偏好文件格式

文件路径：

```text
~/.watchbrief/preferences/seth.yaml
```

结构：

```json
{
  "schema_version": "watchbefore.profile.v2",
  "source": "watchbefore_interest_synthesis",
  "behavior_summary": {
    "history_events": 0,
    "watch_later_events_excluded": 0,
    "matched_history_reports": 0
  },
  "interest_profile": {
    "status": "ready",
    "basis": "watchbefore_reports_and_history_engagement",
    "summary": "用户长期关注的内容类型概述。",
    "domains": [
      {
        "name": "稳定兴趣领域",
        "description": "这个领域具体吸引用户的部分。",
        "subtopics": ["细分关注点"],
        "keywords": ["用于匹配新视频的短术语"],
        "confidence": "high",
        "evidence_count": 3,
        "evidence_ids": ["r001", "r002", "r003"]
      }
    ],
    "content_preferences": [
      {
        "name": "偏好的内容形态",
        "description": "例如深度讲解、真实案例或技术实操。",
        "confidence": "medium",
        "evidence_count": 2,
        "evidence_ids": ["r001", "r002"]
      }
    ]
  }
}
```

文件沿用 `seth.yaml` 路径，但内容使用 JSON；JSON 是 YAML 1.2 的严格子集，可避免宽松 YAML 解析造成歧义。

## WatchBefore 报告新增字段

不要替换现有 `watch_verdict`，新增个人层字段：

```json
{
  "personal_decision": "watch_segments",
  "personal_reason": "与本地 agent 工作流相关，但报告已覆盖主要框架，只需要补看产品演示片段。",
  "preference_matches": ["本地自动化", "AI agent 落地"],
  "preference_mismatches": ["泛趋势判断"],
  "recommended_action": "只看 12:30 | 18:40"
}
```

现有内容评分继续保留，但评分重点应收敛到视频内容本身的信息密度、证据质量、准确性边界。个人偏好不能只靠观看时长，必须引用内容分析结果和偏好文件。

## 插件实现边界

第一版插件不使用框架结论。实现前再确认是否使用：

| 选项 | 适用情况 |
| --- | --- |
| 原生 Manifest V3 | 最短路径，适合只采集 B 站页面 DOM |
| Plasmo | 如果后续要做复杂弹窗、设置页和跨浏览器分发 |
| Tampermonkey 脚本 | 最快验证，但不像正式插件 |

第一版建议先用原生 Manifest V3 或 Tampermonkey 原型，不引入复杂前端框架。

## 验收标准

第一版完成后，用 50 条 B 站历史和 30 条稍后再看验证：

| 指标 | 通过标准 |
| --- | --- |
| 数据采集 | 能导出 URL、标题、UP 主、时长、观看比例、来源 |
| 内容分析 | WatchBefore 能批量分析样本视频 |
| 偏好归纳 | 能生成 `seth.yaml`，兴趣领域和细分关注点均绑定真实内容报告编号 |
| 新视频判断 | 新视频输出个人决策：完整看、只看片段、跳过、以后再看 |
| 人工体验 | 看完报告后 30 秒内能决定怎么处理该视频 |

## 下一步

P0 原型已落地为 Tampermonkey 脚本：

```text
tools/watchtrace/bilibili_watchtrace.user.js
```

使用方式：

1. 浏览器安装 Tampermonkey。
2. 在 Tampermonkey 新建脚本。
3. 粘贴 `tools/watchtrace/bilibili_watchtrace.user.js` 的全部内容并保存。
4. 打开 B 站历史记录或稍后再看页面。
5. 点击右下角“导出 WatchBefore 数据”。
6. 得到 `watchbefore-bilibili-YYYY-MM-DD.jsonl`，同时内容会复制到剪贴板。

也可以不通过浏览器按钮，直接从本机浏览器登录态导出并生成 WatchBefore 偏好档案：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
python3 tools/watchtrace/export_bilibili_events.py --browser arc --output ~/.watchbrief/preferences/events.jsonl
python3 scripts/preferences/build_profile.py --events ~/.watchbrief/preferences/events.jsonl --output ~/.watchbrief/preferences/seth.yaml
```

本地测试命令：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
node tools/watchtrace/test_bilibili_watchtrace.js
PYTHONPATH=tests python3 -m unittest tests/test_watchtrace_tampermonkey.py tests/test_watchtrace_exporter.py tests/test_watchbefore_profile.py
python3 scripts/check_watchbrief_skill.py
```

当前已把 WatchBefore 默认评分改为：信息密度 30%、论据质量 40%、独创性 10%、话题兴趣 20%。`--preference-profile` 只接收统一兴趣画像；必须先让 WatchBefore 批量分析历史观看视频，再用真实观看比例和观看时长校准领域置信度。稍后再看记录不参与兴趣推断。

P0 不实现自动学习，不做路径级重命名。
