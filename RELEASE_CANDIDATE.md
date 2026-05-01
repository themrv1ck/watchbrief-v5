# WatchBrief V5 Release Candidate

## 当前状态

- 版本：`watchbrief_v5` clean rewrite（非 V4 补丁）。
- 冻结标识：`RC`。
- 关系说明：V4 (`v1deodownload`) 保持不变，不删除、不替换、不引导回滚风险；V5 与 V4 并存。
- 目标：保持 V4 的 UI/输出结构与外部行为不变，重写内部代码与契约保证稳定。

## 本次冻结检查要点

- 单元测试：`python3 -m unittest discover -s tests -p 'test_*.py'`
  - 总计：`118`
  - 通过：`116`
  - 失败：`0`
  - 跳过：`2`
- 自检脚本：
  - `python3 scripts/check_watchbrief_skill.py`（PASS）
  - `python3 scripts/check_watchbrief_skill.py --strict-install`（PASS）
- Golden 复检：使用 `watchbrief_v5/golden/sample_payload_*.json` 渲染结果（按 V4 冻结模板结构）核对；未出现旧模块名（要点提炼 / 可执行动作清单 / 完整笔记）。

## 回归任务结果（阶段十二）

### 真实单视频 smoke（阶段十二旧真实 Codex 路径）

- 命令：
  - `python3 scripts/cli.py --source-url 'http://127.0.0.1:8765/focus_steps.mp4' --output-dir /Users/apple/Desktop/watchbrief_v5_phase12_single_smoke_real --review-provider codex --enable-codex-review --model gpt-5.4`
- 结果：`pipeline_failed`
- 失败分类：`auth_failed`
- 失败原因：旧实现误要求 OpenAI API Key，而不是使用 Codex CLI 登录态。
- 产物：`manifest.json` 生成且失败记录完整；未生成单视频 HTML（符合失败预期）。
- 阶段十二点二后该路径已废弃；真实 review 改为 Codex CLI 登录态：`--review-provider codex-cli --enable-codex-review --codex-model gpt-5.4`。

### 真实小列表 smoke（3 条，本地短视频列表）

- 命令：
  - `python3 scripts/cli.py --source-file /Users/apple/Desktop/watchbrief_v5_phase10_local_media/list.m3u --output-dir /Users/apple/Desktop/watchbrief_v5_phase12_list_smoke_real --review-provider codex --enable-codex-review --model gpt-5.4`
- 结果：`completed=0 failed=3`
- 每条 item 均失败于 `pipeline` 阶段，分类同为 `pipeline_failed` + `auth_failed`
- `00-watch-order.html` 仍输出并包含失败原因与 `解析失败` 灰色分组。
- 阶段十二点二后真实列表复测应使用 Codex CLI provider，不再使用 API Key provider。

### mock 路径视觉与排序复核（非在线模型）

- 已执行：
  - `watchbrief_v5_stage12_single_smoke_mock`：single mock 成功，HTML/normalized_payload/review_request/manifest 完整。
  - `watchbrief_v5_stage12_list_smoke_mock`：3 条全部成功，单独生成 3 个 HTML 与 `00-watch-order.html`，不出现旧模块。
  - `watchbrief_v5_phase12_list_smoke_scores`：3 条不同分值（1.2 / 5.3 / 8.1）验证色带与顺序。
- `1.2 / 5.3 / 8.1` 映射规则与展示一致：
  - `<5.0` → `skip` 红 `#A24A42`
  - `5.0~<8.0` → `low` 黄 `#f6c90e`
  - `>=8.0` → `strong` 绿 `#b8de7f`

## 不变项保留

- 不改 `analyzer` / `validator` / `schema` / `renderer` / `pipeline` 顺序。
- 不改 UI、区块结构、字段契约。
- 不改 V4 现有契约流程（单视频、列表逐条处理、失败不中断）。

## 下一步

- 当前按冻结结果维持观察状态，不进行阶段十以后入口迁移或可见契约变更。
- 待 Codex CLI 登录态复测可用后补一次真实单视频 + 真实 3 条列表完整成功 smoke，并作为正式切换前最后验收。
