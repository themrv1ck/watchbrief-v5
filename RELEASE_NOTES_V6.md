# WatchBefore v6 Release Notes

## v6 新增

- 教程操作衔接：用户明确要求照视频操作时，skill 从转写和画面证据继续到环境检查、授权范围内的实际执行、备份与结果验收；普通报告仍不自动操作。
- 单视频摘要改为连续阅读版式，合集增加跨集主线与分集导读；报告评分采用信息密度 35%、证据质量 35%、话题兴趣 30%。
- 统一兴趣画像从真实观看历史与已分析的视频内容建立；只把实际观看秒数大于零的记录作为兴趣证据。
- MiMo-VL 画面分析支持逐条自动判定，也可显式开启或关闭；转写质量门槛先于画面分析。
- B 站多 P 和小红书 board 进入各自的逐条处理链；WebUI 增加模型、转写、输出和诊断配置。
- 新报告写入 `watchbrief_version=watchbrief_v6`；skill id、源码目录和旧命令入口仍保留 `watchbrief_v5` 兼容名。

## 支持平台

- YouTube：单视频、小列表 / playlist，字幕优先。
- Bilibili：单视频、`list/ml` 展开、小列表，Bilibili subtitle provider 字幕优先。
- Xiaohongshu / 小红书：单视频、board / 收藏页，区分 video note 和 image/text note。

## 已通过验收

- YouTube 单视频。
- YouTube 小列表。
- Bilibili 单视频。
- Bilibili `list/ml` 展开。
- Bilibili 小列表。
- 小红书单视频。
- 小红书 board 小样本。
- 小红书 board 14 条全量。
- transcript coverage gate。
- 英文术语中文化。
- 正式输出路径。
- `WatchBrief-Debug` 不污染桌面。
- `WatchBrief-Runs` 仅用于测试 / 复验 / debug。
- Chrome -> Safari 登录态策略。
- Hermes / Codex 副本同步。
- MiMo-VL 官方 BF16 本地环境预检与真实多帧严格 JSON 推理。

## 可选 MiMo-VL 画面理解

- 支持逐条自动判定；`--visual-analysis` 和 `--no-visual-analysis` 可显式覆盖。
- 确定性抽取完整时间轴首尾和中间帧，默认最大间隔 10 秒。
- 长视频固定最多 6 帧一批，列表任务复用一个常驻 MiMo-VL worker。
- 只接受官方 BF16 模型和 `mlx-vlm==0.5.0`；批次 JSON 无效时会拆小重试，单帧最多再试一次，仍要求最终逐帧输出为严格 JSON。图片槽位、帧覆盖或证据 hash 不匹配时失败。
- 画面可见事实进入 Qwen 提炼和 Codex review；模型不能把视觉推断写成音频原话。
- 视觉证据身份进入最终 payload、validator 和 report cache identity。
- WebUI 新增显式开关、根目录/间隔/超时设置和环境诊断。

## 默认模型

- Qwen local extract 默认模型：`qwen3-30b-a3b-instruct-2507-mlx`。
- 默认 endpoint：`http://127.0.0.1:1234/v1`。
- 可覆盖环境变量：`WATCHBRIEF_QWEN_MODEL`、`WATCHBRIEF_QWEN_BASE_URL`。
- Codex review 默认运行方式：`codex-cli / account2 / gpt-5.4`。

## 登录态策略

- 默认浏览器登录态顺序：Chrome -> Safari。
- 显式 `--cookies-from-browser` 优先，显式指定后不自动 fallback。
- 不保存、不打印、不展示 cookies。
- 不把 cookies 写入 HTML、manifest 或 WORKLOG。
- 小红书 `xsec_token` 和 signed media URL 不进入正式输出。

## 输出路径规则

- 单视频正式任务默认输出到 `~/Desktop/<视频标题>.html`。
- 单视频不生成任务文件夹，不生成 `00-watch-order.html`。
- 列表 / playlist / board 默认输出到 `~/Desktop/<播放列表标题>/`。
- 列表目录第一层直接包含 `00-watch-order.html` 和 `01/02/03...` 单视频 HTML。
- 列表输出保持原始顺序，不按评分重排文件名前缀。
- 正式任务不默认进入 `WatchBrief-Runs`。
- 默认不自动打开浏览器。

## Debug 规则

- 成功任务默认清理临时 debug / work 产物。
- 失败任务默认把 debug 留在系统临时目录，并打印 `debug_artifacts`。
- 默认失败不再创建桌面 `WatchBrief-Debug`。
- 历史 `WatchBrief-Debug` 不自动删除。
- 诊断 / 复现使用 `--diagnostic-run` 或 `--repro-run`。

## Transcript Coverage Gate

- 60 秒及以上视频，如果最终 transcript 覆盖率低于 `0.30`，或 segment/text 证据极低，失败为：

```text
stage=transcript_quality
reason_code=transcript_coverage_too_low
```

- 失败后不进入 Qwen。
- 不进入 Codex。
- 不进入 renderer。
- 不生成正式 HTML。
- 列表中该条计入 `failed_count`。

## 小红书 Board 支持

- 支持 `/board/<board_id>` 和 user profile 下的 board URL。
- board resolver 统计 note 总数、video note 数、image/text note 数。
- video note 进入视频处理链。
- image/text note 标记为 `non_video_note` 并跳过。
- `non_video_note` 不进入 audio / MiMo MLX / Qwen / Codex / renderer。
- `skipped_count` 与 `non_video_count` 会写入 manifest / Watch Order。
- 小红书 note URL 不稳定时允许 media URL refresh。
- media URL refresh 只在内存态用于下载，不写入正式输出。

## 已知限制

- WatchBrief 与 Hermes 共用本机 LM Studio endpoint 时，不建议并发运行两个本地模型重任务。
- 并发风险来自 Mac 统一内存和推理算力，不是端口冲突。
- YouTube 登录态失效或 bot check 时，需要先手动在 Chrome 完成验证。
- 小红书 board 实时读取依赖当前浏览器登录态和页面可访问性。
- 无字幕视频需要音频下载和本机 MiMo MLX，可用性取决于平台资源和本机环境。

## 正式运行注意事项

- 正式任务不要传 `--output-dir` 到 `WatchBrief-Runs`。
- 诊断 HTML 不算正式交付。
- 不提交 cookies、`xsec_token`、signed URL、debug 私密文件或桌面 HTML 产物。
- 不提交 `.venv-mlx`、`__pycache__`、`*.pyc`、音频 / 视频 / 字幕运行产物。
- 运行命令和完整守则见 `USAGE_V6.md`。
