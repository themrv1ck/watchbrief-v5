---
name: watchbrief_v5
description: WatchBrief V5 是视频观看决策报告生成器。它不是下载器，也不是普通摘要器。目标是生成固定结构的中文报告与可观看顺序页，判断报告替代性与原视频剩余观看价值。
---

# WatchBrief V5

## 这是什么

- 外部可发现的 Skill 名称是 `watchbrief_v5`，显示名是 `WatchBrief`。
- 旧入口 `v1deodownload` 已废弃，不再作为 WatchBrief V5 的回退入口或安装依赖。
- 正式使用手册是 `USAGE_V5.md`，日常运行守则以该文件为准。
- 默认运行入口是独立项目目录 `/Users/apple/Documents/New project/watchbrief_v5`。Hermes / Codex 调用 WatchBrief V5 时必须先进入这个目录再运行 `python3 scripts/cli.py ...`。
- 旧目录 `/Users/apple/Documents/New project/v1deodownload/watchbrief_v5` 不再作为 WatchBrief V5 的入口、同步源或依赖路径。

## 关键边界

- 只处理“观看决策”输出，不替代下载器或转写器的独立职责。
- 只允许使用固定字段契约输出，renderer 只做填充，不做内容创作。
- 不出现旧模块：要点提炼 / 可执行动作清单 / 完整笔记。
- 不改视觉模板结构：`references/video_report_v5.html` 与 `references/00watch_order_v5.html` 冻结为字段占位模板源。

## 本地 WebUI

- WebUI 入口是 `python3 scripts/webui.py --host 127.0.0.1 --port 8765`。
- WebUI 只构建并启动 `scripts/cli.py` 命令，不绕过 CLI、pipeline、validator 或 renderer。
- WebUI 面向 GitHub 独立用户，不要求 Hermes / OpenClaw；它会读取本机 Qwen endpoint / 模型列表、MLX-Audio、Whisper、Codex CLI、Desktop / Downloads / Documents 路径和 WebUI 状态目录。
- WebUI 首页必须是欢迎说明页，用傻瓜式清单解释浏览器登录态、本地模型、转写工具、输出位置、HTML/PDF 和 token 边界；右侧“当前配置”面板必须可隐藏/显示。
- 模型、账号、输出、登录态等配置放在“设置”二级菜单。
- 可配置项包括提炼模型后端、Qwen 模型、Qwen endpoint、Codex 模型、Codex 账号目录、输出方式、输出目录、登录态浏览器、转写器、缓存和诊断选项。
- 当前真实可用的提炼后端是本地 Qwen；Codex / Gemini / Claude 提炼适配器未接入时必须禁用或明确报错。
- WebUI 的推荐转写器规则：检测到 MLX-Audio 时保持 CLI 默认 `auto`；没有 MLX-Audio 但有 Whisper CLI 时生成 `--transcriber whisper`。
- 当前可运行 review 引擎是 `local`、`codex-cli` 和 `mock`。`local` 是无 Codex 账号的本地规则模式，不调用 Codex；Claude / Gemini / Kimi 和非 HTML renderer 未接入时必须明确报错，不允许伪装成功。
- PDF 导出由 WebUI 后处理完成：CLI 先生成 HTML，WebUI 再用本机 Chrome / Edge / Chromium / Brave headless print 生成同名 PDF；没有可用浏览器时必须明确报错。
- WebUI 不接收、不保存、不转发明文 token；本地配置、密钥和构建产物不得提交。

## 真实 Codex Review

真实 review 走 Codex CLI 登录态，不强制 `OPENAI_API_KEY`。必须显式开启：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"

python3 scripts/cli.py \
  --source-url '<video_url>' \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-model gpt-5.4
```

独立账号目录：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"

python3 scripts/cli.py \
  --source-url '<video_url>' \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-home-root ~/.watchbrief_codex \
  --codex-account default \
  --codex-model gpt-5.4
```

## 转写与本地 Qwen

- 默认转写器是 `--transcriber auto`。
- `auto` 表示 MLX-Audio 优先，且不会静默 fallback 到 Whisper。
- Whisper 只有显式 `--transcriber whisper` 或显式 `--allow-whisper-fallback` 时才允许使用。
- MLX-Audio Python 优先查找 `WATCHBRIEF_MLX_AUDIO_PYTHON`，再查找独立项目根目录 `/Users/apple/Documents/New project/watchbrief_v5/.venv-mlx/bin/python`、当前 skill 根目录 `.venv-mlx/bin/python`、父级项目根目录 `.venv-mlx/bin/python`、Hermes 安装副本里的 `.venv-mlx/bin/python`，不依赖系统 `python3` 作为首选路径，也不再依赖旧 `v1deodownload` skill 的 `.venv-mlx`。
- MLX-Audio 不可用且未允许 fallback 时，失败为 `transcriber_unavailable`，错误信息必须列出已检查的 Python 候选路径。
- `local_extract.py` 必须使用本地 Qwen-family 模型，不允许非 Qwen 模型。
- Qwen 默认模型固定为 `qwen3-30b-a3b-instruct-2507-mlx`；只有显式传 `WATCHBRIEF_QWEN_MODEL` 或 `--qwen-model` 时才覆盖。
- Qwen endpoint 默认 `http://127.0.0.1:1234/v1`，优先用 `WATCHBRIEF_QWEN_BASE_URL` 覆盖；`WATCHBRIEF_QWEN_API_BASE` 和 `--qwen-api-base` 继续可用。
- 当前正式规则是 WatchBrief 和 Hermes 都作为客户端连接 LM Studio 的 `http://127.0.0.1:1234/v1` endpoint，通过请求里的 `model` 字段选择不同模型；两者不是运行在 `1234` 端口上。
- 不建议并发运行 WatchBrief 完整视频链路和 Hermes 本地模型重任务；风险是共享 Mac 统一内存和算力，不是端口冲突。
- Qwen local_extract timeout 默认等于 `--timeout`；可用 `--qwen-timeout` 单独覆盖。Qwen 超时必须分类为 `local_qwen_timeout`，不允许泛化成 `pipeline_failed`。
- 进入 Qwen 前必须通过 transcript coverage gate。60 秒及以上视频如果最终 transcript 覆盖率低于 30%，或 transcript 段数/正文字符数明显过低，必须失败为 `stage=transcript_quality`、`reason_code=transcript_coverage_too_low`，不得进入 Qwen、Codex、validator、renderer，也不得生成正式 HTML。
- 平台字幕来源 `subtitle_bcc` / `subtitle_srt` / `subtitle_vtt` / `youtube_connect` 必须有视频总时长才能通过 coverage gate；如果总时长缺失，必须失败为 `transcript_quality_reason=video_duration_missing_for_quality_gate`，不得用段数或字数替代覆盖率证明。
- 平台字幕来源 `subtitle_bcc` / `subtitle_srt` / `subtitle_vtt` 如果最后时间明显超过视频总时长（超过 `1.2x` 或 `+20秒` 中更宽松的阈值），或覆盖率/段数/正文字符数明显不足，必须丢弃该字幕，记录 `transcript_quality:fallback_to_audio`，重新进入 audio_downloader -> MLX-Audio 转写路径；转写结果仍必须再次通过 coverage gate，失败则停在 `transcript_quality`。
- coverage gate 必须在 `item_manifest.json` 记录 `video_duration_seconds`、`transcript_first_start`、`transcript_last_end`、`transcript_covered_duration`、`transcript_coverage_ratio`、`transcript_segment_count`、`transcript_plain_text_char_count`、`transcript_source`、`transcript_quality_reason`。
- 英文 transcript 直接读英文原文，不先整篇翻译；Qwen 输出中文结构化提炼，最终 HTML 仍为中文。
- local_extract 会确定性规范化明确专名别名：叔本华 / Schopenhauer、尼采 / Nietzsche、柏拉图 / Plato、萨特 / Sartre、阿兰·德波顿 / Alain de Botton；只替换明确别名，不猜测新人名。
- Codex 输出的 `replacement_score` 只作为 `score_trace.model_suggested_score`。最终分数由 `structured_assessment` 按 `information_density*0.2 + evidence_quality*0.3 + originality*0.2 + watch_value*0.3` 计算。
- payload 必须带稳定元信息：`transcript_hash`、`qwen_model_id`、`qwen_prompt_version`、`qwen_prompt_fingerprint`、`codex_model`、`codex_prompt_version`、`codex_prompt_fingerprint`、`scoring_formula_version`、`watchbrief_version`。
- 高分不能显示“报告足够替代”，低分不能显示“建议完整看”；冲突必须在 payload / validator / retry 层解决，renderer 不兜底。
- 默认启用 report cache，目录是 `~/.watchbrief/cache/reports/`。同一 `transcript_hash`、Qwen/Codex 模型、prompt fingerprint、评分公式和 WatchBrief 版本命中时，直接复用上一次通过验证的 `normalized_payload`，不再调用 Codex review。需要重新分析时显式传 `--force-reanalysis`。

## YouTube 字幕策略

- YouTube 默认先抓英文原字幕，不先请求 `zh-Hans` / `zh` 翻译字幕。
- 优先级：manual English subtitles → automatic English captions → configured non-English subtitles → translated subtitles → audio fallback。
- 英文候选包括 `en`、`en-US`、`en-GB`、`en-orig` 和平台实际返回的 `en-*`。
- 字幕候选必须逐个尝试；单个候选失败只记录到 `subtitle_candidate_failures`，不能直接让整条视频进入音频转写。
- 有可用字幕时，item_manifest 必须记录 `audio_downloader_skipped_due_to_subtitle=true`。
- YouTube 单视频 resolver 如果因 `platform_restriction`、bot check、sign-in restriction 或 login verification 失败，但 URL 能解析出 video_id，则尝试 `scripts/providers/youtube_connect_provider.py` 的 transcript fallback。
- YouTube Connect fallback 成功后必须继续 `local_extract -> Codex review -> validator -> renderer`，并跳过 audio_downloader / MLX-Audio。item_manifest 记录 `resolver_failed=true`、`resolver_reason_code=platform_restriction`、`transcript_fallback_provider=youtube-connect`、`transcript_fallback_success=true`。
- YouTube Connect fallback 失败时保留原 resolver 失败，不伪装成功。
- 如果 YouTube 要求确认不是机器人，只能打开 Chrome 到目标视频页让用户手动验证；不得自动点击验证码、不得绕过验证、不得保存或打印 cookies。验证后用 `yt-dlp --cookies-from-browser chrome --skip-download --list-subs <url>` 做只读复测。

## B 站登录态

用户授权后，WatchBrief 可以使用本机 Chrome / Safari / Edge 登录态作为正常采集路径，只用于用户提供的目标视频或列表。默认登录态顺序是 Chrome 首选、Safari 备选；CLI 不传 `--cookies-from-browser` 时，resolver、YouTube subtitle_fetcher、B 站字幕 provider 和 audio_downloader 都必须接收同一个 `chrome -> safari` 尝试列表。Chrome 如果明确出现 cookies 不可读、Extracted 0 cookies、login_required_for_subtitle、platform_restriction 或 browser cookie access error，当前阶段才会尝试 Safari。显式传 `--cookies-from-browser chrome` / `safari` / `edge` 时，以显式值为准，不自动 fallback。不要打印、保存或展示 cookies 内容。

B 站 resolver 阶段必须接收浏览器登录态参数，并在 metadata 请求里传给 `yt-dlp`。如果 resolver 遇到 412，返回 `bilibili_412_blocked` 或更细 B 站 reason_code；已授权时可以进入官方页面 metadata fallback，但 resolver 只解析 metadata，不下载音频。

B 站 `--source-file` 模式必须对文件里的每个 URL 复用正常 resolver，并传入同一套浏览器登录态参数；不得直接把条目写成 `duration=未知` / `date=未知` 的占位 metadata 后进入字幕分析。

B 站 `https://www.bilibili.com/list/ml...` 必须识别为列表，优先用 `yt-dlp --flat-playlist` 展开，必要时再用 B 站收藏列表 API / 页面初始数据补充。展开失败必须返回 `bilibili_list_expansion_failed`，不能退化成只处理 URL query 里的当前 `bvid`。列表标题可用时要作为正式桌面任务文件夹名。

字幕不可用后，audio_downloader 可以复用 resolver 提供的 `bvid/cid` 调 B 站 playurl API 获取 `dash.audio`，再下载并转成标准 WAV。成功时 item_manifest 记录 `method=bilibili_playurl_api`、`cookies_source`、`audio_url_source`、`fallback_success=true`。

## 小红书 board / 专辑

- `xiaohongshu.com/board/...`、`xiaohongshu.com/user/profile/.../board/...`，以及 URL query / fragment 中能解析出 `/board/<board_id>` 的小红书链接，必须由小红书 board 专用 resolver 处理，不允许先交给普通 yt-dlp 单视频 resolver 失败。
- board resolver 返回 `source_kind=list`、`source_subkind=xiaohongshu_board`，标题优先用 board / 专辑标题，正式输出目录按列表规则使用该标题。
- board note 列表优先读取 `/api/sns/web/v1/board/note`，API 不可用时回退读取页面 `window.__INITIAL_STATE__` 中的 board feeds；页面和 API 都不可读时失败为 `xiaohongshu_board_resolver_failed`，可读但为空时失败为 `xiaohongshu_board_empty`。
- 小红书 board resolver 使用统一浏览器登录态策略：显式 `--cookies-from-browser` 优先；未显式传参时按 `chrome -> safari` 尝试。实现只读取 Cookie header 发请求，不打印、不保存、不展示 cookie 内容。
- 每条 note 至少保留 `note_id`、标准 `note_url`、标题、作者、`note_type`、是否视频、是否图文、封面、时长和 raw metadata；raw metadata 只能进 debug / manifest，不进入正式 HTML。
- 视频 note 构造标准 `/explore/<note_id>` URL，进入现有单视频 pipeline；图文或非视频 note 标记为 `status=skipped`、`reason_code=non_video_note`，不进入 `audio_downloader`、MLX-Audio、Qwen、Codex 或 renderer。
- WatchOrder 对 board 输出显示总 note 数、视频成功数、失败数和图文跳过数；图文 note 可作为 skipped 卡片显示，但不能计入 `failed_count`，也不能伪装成视频解析失败。

```bash
python3 scripts/cli.py \
  --source-file '<bilibili_urls.txt>' \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-model gpt-5.4 \
  --timeout 600
```

也支持 `--cookies-file '<cookies.txt>'` 和 `--no-browser-auth`。

## 输出目录

- 单视频默认正式交付只在 Desktop 输出一个 HTML，不生成 `00-watch-order.html`。
- 列表默认正式交付优先使用播放列表标题：`~/Desktop/<播放列表标题>/`。如果同名文件夹或文件已存在，不覆盖，使用 `-2`、`-3` 递增后缀；没有播放列表标题时 fallback 到 `~/Desktop/watch-YYYYMMDD-HHMMSS/`。目录里只放 `00-watch-order.html` 和每条视频 HTML。
- 列表输出必须保持播放列表原始顺序；`00-watch-order.html` 和单视频 HTML 文件名前缀 `01/02/03...` 都不得按评分重排。
- 显式 `--output-dir` 是最终 HTML 的父目录或列表交付目录，不是任务工作目录；列表产物必须直接写在该目录，不能再创建次级任务文件夹。
- 默认只打印最终 HTML / Watch Order 路径，不自动打开浏览器；只有显式 `--open-output` 才打开最终输出。
- manifest、payloads、Qwen/Codex 调试文件默认写入临时 debug 目录，成功后清理；失败时保留在临时目录并打印 `debug_artifacts` 路径，不迁移到 Desktop。
- 需要保留调试产物时，使用 `--debug-dir` 或 `--keep-debug-artifacts`；需要固定临时工作目录时使用 `--work-dir`。
- 正式用户任务和默认诊断任务都不要写入 `~/Desktop/WatchBrief-Runs/`；Hermes 正式提炼命令也不要默认传 `--output-dir` 到这个目录。
- 阶段验收、smoke、cache 复测、debug 和临时复验如需长期保留，可以显式传 `--output-dir <保留目录>`，该目录本身就是最终交付目录，不要再套二级任务文件夹。
- 当用户说“诊断”、“复现”、“debug”、“查问题”或阶段性排障时，Hermes/CLI 必须使用 `--diagnostic-run` 或 `--repro-run`，不要使用正式桌面默认输出位置。
- 诊断运行未显式传 `--output-dir` 时默认写入系统临时目录，manifest/payload/debug 写入该目录 `_debug/`；如果诊断运行生成 HTML，必须当作 diagnostic artifact，不是正式报告。
- 诊断运行显式传 `--output-dir` 时，尊重该目录，但 WORKLOG 必须标明是诊断输出还是正式输出。

## 单视频日期与时长

- `date` 必须来自视频发布日期，优先使用 `publish_date`、`release_date`、`upload_date`、`timestamp` 或平台明确发布时间字段；不得用报告生成时间、当前系统时间或文件时间。
- `duration` 必须来自视频真实时长，统一显示为中文自然格式，例如 `44分58秒`、`1小时02分03秒`。
- metadata 缺失时才显示 `未知`，并在 `item_manifest.json` 的 metadata step 记录 `publish_date_missing` / `publish_date_source` 和 `duration_missing` / `duration_source`。
