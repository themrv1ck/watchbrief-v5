# WatchBrief V5 正式使用手册 / 运行守则

本文是 WatchBrief V5 的正式使用手册。它面向日常运行，不是开发说明。

## 1. WatchBrief 是什么

WatchBrief 是视频观看决策报告生成器，不是单纯下载器，也不是普通摘要器。

它的目标是尽快、稳定、低摩擦地生成中文观看决策 HTML，帮助判断一个视频是否值得继续看、应该看哪里、报告能替代多少原视频观看成本。

支持输入：

- 单视频链接；
- 视频列表；
- 平台 playlist / list / board / 收藏页。

输出：

- 单视频 HTML；
- 列表任务的 `00-watch-order.html`；
- 列表中每条成功视频的独立 HTML。

## 2. 当前支持的平台

### YouTube

- 支持单视频。
- 支持小列表 / playlist。
- 字幕优先。
- 默认使用 Chrome 登录态，必要时 Safari 备选。
- 如果 YouTube 要求人工验证，先在 Chrome 打开目标视频，刷新页面，确认当前账号已登录，再运行 WatchBrief。
- 登录态失效时不要硬跑完整列表，先用单视频或只读字幕探测确认登录态恢复。

### Bilibili

- 支持单视频。
- 支持 `list/ml` 展开。
- 支持 WatchBrief 自己的 Bilibili subtitle provider。
- 字幕优先。
- 有平台字幕时不进入 `audio_downloader` / MLX-Audio。
- 只有明确无字幕时才允许 audio fallback。
- `login_required_for_subtitle` 不可静默转音频，必须先按登录态问题处理。
- 默认 Chrome 首选，Safari 备选。

### Xiaohongshu / 小红书

- 支持单视频。
- 支持 board / 收藏页。
- board 会区分 video note 和 image/text note。
- video note 进入视频处理链。
- image/text note 标记为 `non_video_note` 并跳过。
- 无平台字幕时允许 `audio_downloader` + MLX-Audio。
- 小红书 note URL 不稳定时，允许在内存态 refresh media URL 后重试。
- 不泄露 `xsec_token` / signed media URL。

## 3. 登录态规则

默认登录态策略：

```text
Chrome -> Safari
```

规则：

- 显式参数优先，例如 `--cookies-from-browser chrome` 或 `--cookies-from-browser safari`。
- 显式指定浏览器后，只使用该浏览器，不自动 fallback。
- 不保存、不打印、不展示 cookies。
- 不把 cookies 写入 HTML、manifest、WORKLOG。
- 小红书 `xsec_token` / signed media URL 不得出现在正式输出中。
- YouTube 如果 Chrome 页面显示未登录，但刷新后恢复登录，应先手动刷新确认，再运行 WatchBrief。

## 4. 模型规则

WatchBrief 默认 Qwen 模型：

```text
qwen3-30b-a3b-instruct-2507-mlx
```

默认 endpoint：

```text
http://127.0.0.1:1234/v1
```

可用环境变量覆盖：

```bash
export WATCHBRIEF_QWEN_MODEL="qwen3-30b-a3b-instruct-2507-mlx"
export WATCHBRIEF_QWEN_BASE_URL="http://127.0.0.1:1234/v1"
```

Codex review 默认使用：

```text
codex-cli / account2 / gpt-5.4
```

运行守则：

- WatchBrief 与 Hermes 都只是连接 LM Studio 的 `127.0.0.1:1234/v1` endpoint，不是运行在 `1234` 端口上。
- 两者通过 OpenAI-compatible 请求里的 `model` 字段选择模型。
- 不建议 WatchBrief 与 Hermes 本地模型重任务并发运行。
- 并发风险是共享本机统一内存和推理算力，不是端口冲突。

## 5. WebUI 本地界面

WatchBrief 提供一个标准库实现的本地 WebUI，入口：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
python3 scripts/webui.py --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

规则：

- WebUI 只负责本地任务提交和任务状态展示，底层仍调用 `scripts/cli.py`。
- 面向 GitHub 独立用户，不要求 Hermes / OpenClaw。
- 会读取本机能力：本地 Qwen endpoint / 模型列表、MLX-Audio、Whisper、Codex CLI、Desktop / Downloads / Documents 路径和 WebUI 状态目录。
- 支持自定义 Qwen 模型、Qwen endpoint、Qwen timeout、Codex 模型、Codex 账号目录、输出方式、输出目录、登录态浏览器、转写器、缓存和诊断选项。
- 当前真实可用的提炼模型后端是本地 Qwen / OpenAI-compatible endpoint；Codex / Gemini / Claude 作为提炼后端尚未接入。
- 转写器默认是“推荐”：检测到 MLX-Audio 时使用 CLI 默认 `auto`；没有 MLX-Audio 但有 Whisper 时生成 `--transcriber whisper`。
- 默认不传 `--output-dir`，沿用 WatchBrief 正式默认输出路径。
- 默认不传 `--open-output`，不会自动打开最终 HTML；用户显式勾选时才会传入。
- 默认登录态为 `auto`，即 CLI 的 Chrome -> Safari 策略；显式选择 Chrome/Safari/Edge 时才传 `--cookies-from-browser`。
- WebUI 默认 Codex 模型显示并传入 `gpt-5.4`；Codex 模型用于 Qwen 提炼后的 review / 评分一致性检查，不是 HTML renderer。
- 当前可运行的 review 引擎是 `codex-cli` 和 `mock`；Claude / Gemini / Kimi adapter 未接入。
- 当前正式报告格式是 HTML；PDF 导出和非 HTML renderer 未接入，界面不会伪装可用。
- WebUI 状态文件和日志写在 `~/.watchbrief/webui/`。
- WebUI 不接收、不保存、不转发明文 token。
- WebUI 不保存、不打印、不展示 cookies。
- 本地配置、密钥和构建产物不得提交；`.gitignore` 已排除 `.env`、`*.local.*`、`secrets/`、`*.token`、`node_modules/`、`dist/` 和 `target/`。

## 6. 正式输出规则

### 单视频

默认输出：

```text
~/Desktop/<视频标题>.html
```

规则：

- 不生成任务文件夹。
- 不生成 `00-watch-order.html`。
- 不自动打开浏览器。
- debug / payload / work 产物不混入正式输出目录。

### 列表 / playlist / board

默认输出：

```text
~/Desktop/<播放列表标题>/
```

文件夹第一层直接包含：

```text
00-watch-order.html
01-xxx.html
02-xxx.html
...
```

规则：

- 不进入 `WatchBrief-Runs`。
- 不生成二级 HTML 文件夹。
- 不在桌面根目录散落 per-video HTML。
- 不自动打开 `00-watch-order.html`。
- 如果同名目录存在，使用安全冲突后缀，例如 `-2`、`-3`，不覆盖已有目录。
- 列表输出保持原始列表顺序，HTML 文件名前缀 `01/02/03...` 不按评分重排。

## 7. WatchBrief-Runs 和 WatchBrief-Debug 规则

`WatchBrief-Runs` 只用于：

- smoke；
- cache 验收；
- 阶段测试；
- 临时复验；
- debug。

正式任务不默认进入 `WatchBrief-Runs`。

失败 debug 规则：

- 默认失败不再生成桌面 `WatchBrief-Debug`。
- 失败 debug 保留在系统临时目录，或保留在显式指定的 `--debug-dir`。
- CLI 应打印 `debug_artifacts` 路径。
- 历史 `WatchBrief-Debug` 不自动删除。

诊断 / 复现规则：

- 使用 `--diagnostic-run` 或 `--repro-run`。
- 默认写入系统临时目录，不写入正式桌面位置。
- 如果显式传 `--output-dir`，该目录就是最终诊断目录。
- 诊断运行生成的 HTML 是 diagnostic artifact，不算正式交付。

## 8. 字幕 / 音频 / 转写规则

- 字幕优先。
- 有字幕时不进入 `audio_downloader`。
- 无字幕时才允许 audio fallback。
- audio fallback 默认使用 MLX-Audio。
- Whisper 只有显式指定 `--transcriber whisper` 或 `--allow-whisper-fallback` 时才使用。
- MLX-Audio 缺失时应明确报错，不静默换 Whisper。
- transcript coverage gate 已启用。
- 低覆盖 transcript 不进入 Qwen / Codex / renderer。
- 低覆盖 transcript 不生成伪完成 HTML。

## 9. Transcript Coverage Gate 规则

如果满足以下条件，WatchBrief 会在 Qwen 前停止：

- 视频时长大于等于 `60` 秒；
- transcript 覆盖率过低，例如低于 `0.30`；
- 或 `segment_count` / `plain_text_char_count` 极低。

失败分类：

```text
stage=transcript_quality
reason_code=transcript_coverage_too_low
```

失败行为：

- 不进入 Qwen。
- 不进入 Codex。
- 不进入 renderer。
- 不生成正式 HTML。
- 列表中该条计入 `failed_count`。

debug / manifest 会记录：

- `video_duration_seconds`
- `transcript_first_start`
- `transcript_last_end`
- `transcript_covered_duration`
- `transcript_coverage_ratio`
- `transcript_segment_count`
- `transcript_plain_text_char_count`
- `transcript_source`
- `transcript_quality_reason`

## 10. 小红书 Board 规则

支持 board URL 识别：

```text
https://www.xiaohongshu.com/board/<board_id>
https://www.xiaohongshu.com/user/profile/<user_id>/board/<board_id>
query / fragment 中包含 /board/<board_id> 的分享链接
```

board resolver 会统计：

- note 总数；
- video note 数；
- image/text note 数；
- `skipped_count`；
- `non_video_count`。

处理规则：

- video note 进入视频处理链。
- image/text note 标记为 `status=skipped`、`reason_code=non_video_note`。
- `non_video_note` 不进入 audio / MLX-Audio / Qwen / Codex / renderer。
- `non_video_note` 不生成单视频 HTML。
- `non_video_note` 不计入 `failed_count`。
- 小红书 note URL 不稳定时，允许 media URL refresh。
- media URL refresh 只能在内存态用于下载，不写入正式输出。
- 不泄露 `xsec_token`。
- 不泄露 signed media URL。
- 默认 Chrome -> Safari fallback。

## 11. 推荐运行方式

以下命令都在本机项目目录执行：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
```

Hermes / Codex 默认运行 WatchBrief V5 时也使用这个独立项目目录。旧目录 `/Users/apple/Documents/New project/v1deodownload/watchbrief_v5` 不再作为入口、同步源或依赖路径。

### 单视频

```bash
python3 scripts/cli.py \
  --source-url "<视频链接>" \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-home-root ~/.watchbrief_codex \
  --codex-account account2 \
  --codex-model gpt-5.4 \
  --timeout 600 \
  --qwen-timeout 600
```

### 列表 / playlist / board

```bash
python3 scripts/cli.py \
  --source-url "<列表或board链接>" \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-home-root ~/.watchbrief_codex \
  --codex-account account2 \
  --codex-model gpt-5.4 \
  --timeout 600 \
  --qwen-timeout 600
```

### URL 文件

```bash
python3 scripts/cli.py \
  --source-file "/path/to/urls.txt" \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-home-root ~/.watchbrief_codex \
  --codex-account account2 \
  --codex-model gpt-5.4 \
  --timeout 600 \
  --qwen-timeout 600
```

### 诊断 / 复现

```bash
python3 scripts/cli.py \
  --source-url "<视频链接>" \
  --diagnostic-run \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-home-root ~/.watchbrief_codex \
  --codex-account account2 \
  --codex-model gpt-5.4 \
  --timeout 600 \
  --qwen-timeout 600
```

诊断命令会触发真实链路。只在需要复现问题时使用。

## 12. 常用覆盖参数

显式指定浏览器：

```bash
--cookies-from-browser chrome
--cookies-from-browser safari
```

禁用浏览器登录态：

```bash
--no-browser-auth
```

强制重新分析，不复用 report cache：

```bash
--force-reanalysis
```

保留 debug：

```bash
--debug-dir "/path/to/debug"
--keep-debug-artifacts
```

显式输出目录：

```bash
--output-dir "/path/to/final-output"
```

列表任务里，显式 `--output-dir` 就是最终列表目录，不是父目录。

## 13. 不要这样做

- 不要在登录态失效时硬跑完整列表。
- 不要把正式任务默认输出到 `WatchBrief-Runs`。
- 不要让诊断 HTML 冒充正式交付。
- 不要同时跑 WatchBrief 完整视频链路和 Hermes 本地模型重任务。
- 不要保存、打印、展示 cookies。
- 不要把小红书 `xsec_token` / signed media URL 写进 HTML、manifest 或 WORKLOG。
- 不要手动删除历史 `WatchBrief-Debug`，除非已经确认里面没有需要保留的诊断资料。

## 14. 自检命令

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"

python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_watchbrief_skill.py --strict-install
python3 /Users/apple/.hermes/skills/openclaw-imports/watchbrief_v5/scripts/check_watchbrief_skill.py --strict-install
```

阶段二十 A 写入本手册时，最后已知验收状态：

```text
375 tests OK, skipped=3
strict install PASS
Hermes / Codex 副本已同步
```
