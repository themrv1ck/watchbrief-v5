# WatchBrief V5 Release

## 阶段十三状态

- WatchBrief V5 并行发布完成。
- Skill 名称：`watchbrief_v5`。
- 显示名：`WatchBrief`。
- Codex / Hermes 均可发现 `watchbrief_v5` / `WatchBrief`。
- V4 (`v1deodownload`) 仍保留，可回退；本次未删除、未覆盖 V4 正式副本。

## 安装位置

- 项目源目录：`/Users/apple/Documents/New project/watchbrief_v5/`
- Codex：`/Users/apple/.codex/skills/watchbrief_v5/`
- Hermes：`/Users/apple/.hermes/skills/openclaw-imports/watchbrief_v5/`
- V4 Codex 回退目录：`/Users/apple/.codex/skills/v1deodownload/`
- V4 Hermes 回退目录：`/Users/apple/.hermes/skills/openclaw-imports/v1deodownload/`

## 验收结果

- `python3 scripts/check_watchbrief_skill.py`：PASS
- `python3 scripts/check_watchbrief_skill.py --strict-install`：PASS
- Codex 安装副本 strict 自检：PASS
- Hermes 安装副本自检：PASS
- 新会话发现性检查：`FOUND watchbrief_v5 WatchBrief`
- 全量单测：`Ran 140 tests in 0.048s`，`OK (skipped=2)`

## 发布后真实单视频验收

- 输出目录：`/Users/apple/Desktop/watchbrief_v5_phase13_single_acceptance/`
- 结果：阻断，未完成最终 HTML 交付。
- 阻断原因：Codex CLI usage limit。
- Codex CLI 返回：`You've hit your usage limit`
- 建议复跑时间：额度恢复后；本轮 CLI 提示为 `Apr 27th, 2026 12:03 AM`。
- 已生成：`manifest.json`、`local_extract.json`、`review_request.json`、`item_manifest.json`、Codex 调试产物。
- 未生成：单视频 HTML、`normalized_payload.json`。
- 阻断证据保留：`/Users/apple/Desktop/watchbrief_v5_phase13_single_acceptance/phase13_acceptance_check.json`

## account2 复测记录

- Codex CLI 独立账号目录：`~/.watchbrief_codex/account2`
- 登录状态：`Logged in using ChatGPT`
- 本轮未使用 `OPENAI_API_KEY`。
- 本轮未修改代码、renderer、validator、schema、pipeline 或模板。
- 输出目录：`/Users/apple/Desktop/watchbrief_v5_phase13_single_acceptance_retry/`
- 结果：阻断，未完成最终 HTML 交付。
- `manifest.json`：`completed_count=0`、`failed_count=1`
- 已生成：`review_request.json`、`local_extract.json`、`item_manifest.json`
- 未生成：单视频 HTML、`normalized_payload.json`
- 错误分类：`codex_timeout`
- pipeline 错误：`pipeline_failed`
- 错误信息：`timeout: codex exec timed out`
- 阻断证据：`/Users/apple/Desktop/watchbrief_v5_phase13_single_acceptance_retry/phase13_account2_acceptance_check.json`

## 阶段十三最终验收

- 状态：通过。
- 推荐真实 Codex timeout：`--timeout 600`
- 输出目录：`/Users/apple/Desktop/watchbrief_v5_phase13_single_acceptance_retry_timeout600`
- `manifest.json`：`completed_count=1`、`failed_count=0`
- 单视频 HTML：`01-focus_steps.html`
- `normalized_payload.json`：已生成
- `final_conclusion`：通过，只做主题收束
- `tag` 固定枚举：通过
- `topic` 贴合视频：通过
- `watch_segments` primary 唯一：通过
- `only_one_segment` 匹配 primary：通过
- 时间段竖杠：通过
- 旧模块未出现：通过
- 检查结果：`/Users/apple/Desktop/watchbrief_v5_phase13_single_acceptance_retry_timeout600/phase13_timeout600_acceptance_check.json`

## 当前结论

- WatchBrief V5 已完成并行发布。
- V4 仍保留，可回退。
- 旧入口暂不切换。
- 真实单视频最终验收已通过；稳定观察几天后，再决定是否让 `v1deodownload` 指向 WatchBrief。
