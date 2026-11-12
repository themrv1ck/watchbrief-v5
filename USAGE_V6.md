# WatchBefore 正式使用手册 / 运行守则

本文是 WatchBefore v6 的正式使用手册。它面向日常运行，不是开发说明。内部源码目录和 skill id 暂时仍保留 `watchbrief_v5`，用于兼容 Codex / Hermes 已验证链路。

## 1. WatchBefore 是什么

WatchBefore 是观看前的视频内容理解工具，不是单纯下载器，也不是普通摘要器。

它的目标是尽快、稳定、低摩擦地生成中文内容报告：先讲清视频主要内容、信息密度、证据质量和补看入口，再用你的统一兴趣画像判断“可能喜欢”或“暂时无法判断”。WatchTrace 只采集行为样本，个人话题兴趣必须由 WatchBefore 批量分析样本内容后进入 30% 评分。

支持输入：

- 单视频链接；
- 视频列表；
- 平台 playlist / list / board / 收藏页。

输出：

- 单视频 HTML；
- 列表任务的 `00-watch-order.html`；
- 列表中每条成功视频的独立 HTML。

用户明确要求按教程动手时，WatchBefore skill 会在内容分析后继续使用实际工具操作。它先把视频讲述和画面证据映射到当前设备的真实状态，再执行已授权步骤并回读结果；普通报告不自动修改设备或账户。详见 [教程操作衔接](references/tutorial-execution.md)。

推荐 CLI 入口：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
python3 scripts/watchbefore.py --source-url "<video_url>"
```

正式运行会自动绑定 `~/.watchbrief/preferences/seth.yaml`，因此日常命令不再需要手动传画像参数：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
python3 scripts/watchbefore.py \
  --source-url "<video_url>"
```

画像缺失、损坏或 schema 错误时，WatchBefore 会在进入视频 pipeline 前直接停止。`--preference-profile` 仍可显式替换为另一份合法画像。系统只记录 profile 的路径、schema 和 hash，不会把观看历史明文发给 review 模型。只有 WatchBefore 批量分析样本后由本地 Qwen 生成的统一兴趣领域会进入 `话题兴趣` 30% 分数；观看时长和观看量作为匹配证据，不输出“正向 / 负向 / 稍后再看”主题，也不会按作者推断偏好。

默认单视频模板规则以 [最新模板规范](templates/WATCHBEFORE_TEMPLATE_RULES.md)（2026-11-02 用户确认）为准。保留顶部主菜单和可见评分；左侧原摘要位置显示一段式“视频总结”，右侧评分 banner 与左侧等高，分数、评价标签和一句“省流”在上方紧凑排列。正文“视频到底讲了什么？”用 2 到 5 个具体节点概括内容，节点后归纳“视频主旨”；正文“值得补看”保留，四类参考项在补充内容中按需展开。生产 renderer 已于 2026-11-02 接入该版式，并以 7 条真实列表报告核验结构、来源覆盖率、顺序和入口文件；验收范围见最新模板规范。画像判断、历史观看依据、完整时间线及纠偏反馈继续遵守既有内容与证据约束。

把历史观看事实升级为统一兴趣画像的命令：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
python3 scripts/preferences/build_content_profile.py \
  --events ~/.watchbrief/preferences/events.jsonl \
  --reports "/path/to/watchbefore/reports/**/*.json" \
  --output ~/.watchbrief/preferences/seth.yaml
```

兴趣证据使用所有实际观看秒数大于 0、且已有字幕或音频转写内容报告的历史视频；稍后再看记录不参与。领域和内容形态按累计观看秒数优先、观看记录数量次优先排序；输出统一兴趣领域、细分关注点、匹配关键词、内容形态与可审计的观看时长指标，不包含正向、负向或稍后再看主题。

兼容入口仍可用：

```bash
python3 scripts/cli.py --source-url "<video_url>"
```

## 2. 当前支持的平台

### YouTube

- 支持单视频。
- 支持小列表 / playlist。
- 字幕优先。
- 默认使用 Chrome 登录态，必要时 Safari 备选。
- 如果 YouTube 要求人工验证，先在 Chrome 打开目标视频，刷新页面，确认当前账号已登录，再运行 WatchBefore。
- 登录态失效时不要硬跑完整列表，先用单视频或只读字幕探测确认登录态恢复。

### Bilibili

- 支持单视频。
- 支持 `list/ml` 展开。
- 同一 BV 的多 P 默认按顺序全部展开；显式 `p=N` / `page=N` / `cid=...` 只分析选中集。每集使用自己的 CID、标题、时长和内容，越界不回退第一集。详见 [多 P 测试案例](docs/bilibili-multipart-cases.md)。
- 支持 WatchBefore 自己的 Bilibili subtitle provider。
- 字幕优先。
- 有平台字幕时不进入 `audio_downloader` / MLX-Audio。
- 只有明确无字幕时才允许 audio fallback。
- 用户明确要求下载音频转写时，可显式传 `--force-audio-transcription`；不得默认开启。
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
- YouTube 如果 Chrome 页面显示未登录，但刷新后恢复登录，应先手动刷新确认，再运行 WatchBefore。

## 4. 模型规则

WatchBefore 默认 Qwen 模型：

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
codex-cli / account2 / gpt-5.5
```

MiMo-VL 画面理解是可选证据层，默认按每条视频的转写内容自动判定；也可强制开启或关闭。它补足界面操作、图表和屏幕文字，不替代字幕/ASR、Qwen 提炼或 Codex 判断。具体线索阈值、30 分钟自动预算和无旁白限制见 [MiMo-VL 画面证据开启规则](docs/mimo-vl-activation-rules.md)。强制开启：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
python3 scripts/watchbefore.py \
  --source-url "<video_url>" \
  --visual-analysis
```

默认根目录是 `/Users/apple/Documents/MiMo-VL-7B-RL-2508`；只接受 Apple Silicon、官方 BF16 四分片和 `mlx-vlm==0.5.0`。模型文件约 16.6 GB，实测峰值统一内存约 17.6 GB，建议 32 GB 或更高内存。可以用 `WATCHBRIEF_MIMO_VL_ROOT` 或 `--mimo-vl-root` 指定同结构安装。

运行规则：

- 确定性覆盖完整时轴，包含首帧和末帧；默认最大抽帧间隔 10 秒。
- 长视频按最多 6 帧一批进入同一个常驻 MiMo-VL 进程；完整列表任务也不重复加载模型。
- 模型只返回附件中直接可见的事实；全局帧号和时间戳由 WatchBefore 写入，模型不能改写。
- 模型结果必须是严格 JSON，图片槽位必须与输入逐项一致；批次 JSON 无效时可拆成较小图片批次重试，单帧最多再试一次，仍无效即失败。不做字段猜测、Markdown 修复或静默补帧。
- 自动判定命中或强制开启后，视觉链任一环节失败就停止该条，不退回纯转写。
- 画面证据 hash 进入 Qwen/Codex stability metadata 和 report cache identity；关闭视觉时保持原有纯转写行为。

WebUI 的“高级设置 -> 模型与账号”提供自动、强制开启、关闭三档和环境状态。自动档只在单条内容命中线索后加载 MiMo-VL。

没有本地 Qwen 时：

- 本地已有 Gemma / Llama / Mistral 等 OpenAI-compatible 模型，可以选择 `local-openai-compatible`，填写模型名和 endpoint。
- 其他 OpenAI-compatible 云端或自建服务，可以选择 `openai-compatible`，填写模型名、endpoint 和 API key 环境变量名。
- 没有本地大模型，可以选择 Gemini / Claude / Kimi / Codex CLI。
- 云模型密钥只通过环境变量读取，例如 `GEMINI_API_KEY`、`ANTHROPIC_API_KEY`、`MOONSHOT_API_KEY`。
- WebUI 和 CLI 不接收明文 token，不把 token 写入配置、HTML、manifest 或 WORKLOG。

运行守则：

- WatchBefore 与 Hermes 都只是连接 LM Studio 的 `127.0.0.1:1234/v1` endpoint，不是运行在 `1234` 端口上。
- 两者通过 OpenAI-compatible 请求里的 `model` 字段选择模型。
- 不建议 WatchBefore 与 Hermes 本地模型重任务并发运行。
- 并发风险是共享本机统一内存和推理算力，不是端口冲突。

## 5. WebUI 本地界面

WatchBefore 提供一个标准库实现的本地 WebUI，入口：

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
- WebUI 首页是欢迎说明书，按“先登录浏览器 -> 粘贴链接 -> 点开始运行”的傻瓜流程说明。
- 首页配置清单会解释每个设置的目的：浏览器登录态、本地模型、转写工具、输出位置、HTML/PDF 和 token 边界。
- 右侧“当前配置”面板可以隐藏或显示，避免第一页被配置内容占满。
- “设置”是二级菜单，包含“模型与账号”和“输出与登录态”。
- 会读取本机能力：本地 OpenAI-compatible endpoint / 模型列表、Qwen 模型、本机 MiMo 4-bit 转写环境、Codex CLI、Desktop / Downloads / Documents 路径、云模型 API key 环境变量是否存在，以及 WebUI 状态目录。
- 支持自定义 Qwen 模型、Qwen endpoint、Qwen timeout、非 Qwen 本地模型名、外部 API endpoint、API key 环境变量名、Codex 模型、Codex 账号目录、输出方式、输出目录、登录态浏览器、转写器、缓存和诊断选项。
- 当前真实可用的提炼模型后端是 `local-qwen`、`local-openai-compatible`、`openai-compatible`、`gemini`、`claude`、`kimi` 和 `codex-cli-extract`。
- 转写器默认是“推荐”：检测到本机 MiMo 4-bit runtime 时使用 CLI 默认 `auto`；没有 MiMo 时只提示配置 MiMo，不自动生成 `--transcriber whisper`。
- 默认不传 `--output-dir`，沿用 WatchBefore 正式默认输出路径。
- 默认不传 `--open-output`，不会自动打开最终 HTML；用户显式勾选时才会传入。
- 默认登录态为 `auto`，即 CLI 的 Chrome -> Safari 策略；显式选择 Chrome/Safari/Edge 时才传 `--cookies-from-browser`。
- WebUI 默认 review 引擎是 `local` 本地模式：不需要 Codex 账号，不调用 Codex，只基于提炼结果和确定性规则生成报告。
- 用户显式选择 `codex-cli` 时，Codex 模型默认显示并传入 `gpt-5.5`；Codex 模型用于 Qwen 提炼后的 review / 评分一致性检查，不是 HTML renderer。
- 当前可运行的 review 引擎是 `local`、`codex-cli`、`gemini`、`claude`、`kimi`、`openai-compatible` 和 `mock`。
- Gemini / Claude / Kimi / OpenAI-compatible 只读取环境变量里的 API key；WebUI 只填写环境变量名字，例如 `GEMINI_API_KEY`，不要粘贴 token 内容。
- 当前正式报告基础格式是 HTML。
- PDF 导出已接入 WebUI：先生成 HTML，再用本机 Chrome / Edge / Chromium / Brave 的 headless print 生成同名 PDF。
- 如果本机没有可用浏览器，可设置 `WATCHBRIEF_PDF_BROWSER=/path/to/chrome-like-browser`。
- 非 HTML renderer 未接入，界面不会伪装可用。
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

- 不进入 `WatchBefore-Runs`。
- 不生成二级 HTML 文件夹。
- 不在桌面根目录散落 per-video HTML。
- 不自动打开 `00-watch-order.html`。
- 如果同名目录存在，使用安全冲突后缀，例如 `-2`、`-3`，不覆盖已有目录。
- 列表输出保持原始列表顺序，HTML 文件名前缀 `01/02/03...` 不按评分重排。

## 7. WatchBefore-Runs 和 WatchBefore-Debug 规则

`WatchBefore-Runs` 只用于：

- smoke；
- cache 验收；
- 阶段测试；
- 临时复验；
- debug。

正式任务不默认进入 `WatchBefore-Runs`。

失败 debug 规则：

- 默认失败不再生成桌面 `WatchBefore-Debug`。
- 失败 debug 保留在系统临时目录，或保留在显式指定的 `--debug-dir`。
- CLI 应打印 `debug_artifacts` 路径。
- 历史 `WatchBefore-Debug` 不自动删除。

诊断 / 复现规则：

- 使用 `--diagnostic-run` 或 `--repro-run`。
- 默认写入系统临时目录，不写入正式桌面位置。
- 如果显式传 `--output-dir`，该目录就是最终诊断目录。
- 诊断运行生成的 HTML 是 diagnostic artifact，不算正式交付。

## 8. 字幕 / 音频 / 转写规则

- 字幕优先。
- 有字幕时不进入 `audio_downloader`。
- 无字幕时才允许 audio fallback。
- audio fallback 默认使用本机 MiMo MLX。
- Whisper 只有显式指定 `--transcriber whisper` 或 `--allow-whisper-fallback` 时才使用。
- MiMo runtime 缺失时应明确报错，不静默换 Whisper。
- transcript coverage gate 已启用。
- 低覆盖 transcript 不进入 Qwen / Codex / renderer。
- 低覆盖 transcript 不生成伪完成 HTML。

## 9. Transcript Coverage Gate 规则

如果满足以下条件，WatchBefore 会在 Qwen 前停止：

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
- `non_video_note` 不进入 audio / MiMo MLX / Qwen / Codex / renderer。
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

Hermes / Codex 默认运行 WatchBefore v6 时也使用这个独立项目目录。旧目录 `/Users/apple/Documents/New project/v1deodownload/watchbrief_v5` 不再作为入口、同步源或依赖路径。

### 单视频

```bash
python3 scripts/cli.py \
  --source-url "<视频链接>" \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-home-root ~/.watchbrief_codex \
  --codex-account account2 \
  --codex-model gpt-5.5 \
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
  --codex-model gpt-5.5 \
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
  --codex-model gpt-5.5 \
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
  --codex-model gpt-5.5 \
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

显式输出父目录：

```bash
--output-dir "/path/to/output-parent"
```

正式列表任务里，显式 `--output-dir` 是父目录；最终交付目录仍是：

```text
/path/to/output-parent/<播放列表标题>/
```

诊断任务显式 `--output-dir` 时，该目录本身才是最终诊断目录。

## 13. 不要这样做

- 不要在登录态失效时硬跑完整列表。
- 不要把正式任务默认输出到 `WatchBefore-Runs`。
- 不要让诊断 HTML 冒充正式交付。
- 不要同时跑 WatchBefore 完整视频链路和 Hermes 本地模型重任务。
- 不要保存、打印、展示 cookies。
- 不要把小红书 `xsec_token` / signed media URL 写进 HTML、manifest 或 WORKLOG。
- 不要手动删除历史 `WatchBefore-Debug`，除非已经确认里面没有需要保留的诊断资料。

## 14. 自检命令

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"

python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_watchbrief_skill.py --strict-install
python3 /Users/apple/.hermes/shared/skills/watchbrief_v5/scripts/check_watchbrief_skill.py
```

阶段二十 A 写入本手册时，最后已知验收状态：

```text
375 tests OK, skipped=3
strict install PASS
Hermes / Codex 副本已同步
```
