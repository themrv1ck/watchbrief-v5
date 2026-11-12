# WatchBefore

WatchBefore 是“观看前”的视频内容理解与时间过滤工具。它先告诉你视频到底讲什么、内容信息密度和证据质量如何、如果要补原片应先看哪里；是否值得花时间看，由你基于报告和个人偏好决定。

当前版本：**v6**。正式使用手册见 [USAGE_V6.md](USAGE_V6.md)。日常运行、输出目录、登录态、模型和平台规则以该文件为准。

## 命名与兼容

- 产品显示名：`WatchBefore`
- 推荐命令入口：`python3 scripts/watchbefore.py ...`
- 兼容调用名：`$watchbrief_v5`
- 旧入口 `$v1deodownload` 已废弃，不再作为 WatchBefore 的回退入口或安装依赖。
- `watchbrief_v5` 仍是当前内部 skill id 和源码目录；Hermes / Codex 副本只作为运行副本。不要为了改名打断稳定 pipeline。

## 看完教程后帮用户操作

用户明确要求“照视频帮我做”时，WatchBefore v6 会把转写与必要画面作为操作证据，继续核对当前设备、版本、源码和可用工具，完成已授权的配置、安装与验收。普通内容报告不会自动修改用户环境。执行规则见 [教程操作衔接](references/tutorial-execution.md)；如果工具无法完成物理触摸、账号验证等步骤，只把这些具体动作交给用户。

## 推荐命令（按你当前目录执行）

Hermes / Codex 默认运行入口是独立项目目录：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
```

以下命令都默认在这个目录执行。旧目录 `/Users/apple/Documents/New project/v1deodownload/watchbrief_v5` 不再作为入口、同步源或依赖路径。

### 0) 本地 WebUI

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"
python3 scripts/webui.py --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

WebUI 面向从 GitHub 下载后独立使用的用户，不要求 Hermes / OpenClaw。首页是欢迎说明书，按“先登录浏览器 -> 粘贴链接 -> 点开始运行”的傻瓜流程说明；右侧“当前配置”面板可以隐藏或显示；模型、账号、输出、登录态放在“设置”二级菜单。它会读取本机能力并给出默认建议：本地 OpenAI-compatible endpoint 与模型列表、可用 Qwen 模型、本机 MiMo 4-bit 转写环境、Codex CLI、Desktop / Downloads / Documents 路径和 WebUI 状态目录。没有 MiMo 时，WebUI 只提示配置 MiMo，不自动推荐 Whisper。提炼后端现在支持 `local-qwen`、`local-openai-compatible`、`openai-compatible`、`gemini`、`claude`、`kimi` 和 `codex-cli-extract`；review 引擎支持 `local`、`codex-cli`、`gemini`、`claude`、`kimi`、`openai-compatible` 和 `mock`。`local` 是无 Codex 账号的本地规则模式，不调用 Codex。Gemini / Claude / Kimi / OpenAI-compatible 只读取环境变量里的 API key，WebUI 只填写环境变量名字，不接收、不保存、不转发明文 token。PDF 导出已接入：CLI 仍先生成 HTML，WebUI 再用本机 Chrome / Edge / Chromium / Brave 的 headless print 生成同名 PDF。

安全提交规则：

- 不提交 `.env`、`*.local.*`、`secrets/`、`*.token`、`*.secret`、`*.pem`、`*.key`。
- 不提交 `node_modules/`、`dist/`、`target/`、`build/`、`.cache/`。
- 不提交本机 MiMo MLX runtime、`__pycache__` 或音频 / 视频 / 字幕运行产物。

### 1) 单视频开发链路测试（仅限 mock，不代表真实报告）

下面命令只验证 pipeline、validator 和 renderer，内容来自固定 golden JSON，不能用于判断真实视频。正式报告必须使用后面的真实 review 命令。

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"

python3 scripts/cli.py \
  --source-url "https://example.com/your-video" \
  --mock-review-response golden/sample_payload_heartflow.json
```

同一命令也可以用 WatchBefore 入口运行：

```bash
python3 scripts/watchbefore.py \
  --source-url "https://example.com/your-video" \
  --mock-review-response golden/sample_payload_heartflow.json
```

单视频未显式传 `--output-dir` 时，最终只把 HTML 交付到 Desktop；显式传 `--output-dir` 时，它表示最终 HTML 的父目录，不是任务工作目录。manifest、payload、Codex/Qwen 调试文件会写入临时 debug 目录，成功后默认清理。需要保留诊断产物时传 `--debug-dir` 或 `--keep-debug-artifacts`。

### 2) URL 文件（多条）

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"

python3 scripts/cli.py \
  --source-file /path/to/urls.txt \
  --mock-review-response golden/sample_payload_heartflow.json
```

`urls.txt` 每行一个地址，空行与 `#` 注释会跳过。未显式传 `--output-dir` 时，有平台列表标题就交付到 `~/Desktop/<播放列表标题>/`；如果同名已存在，用 `-2`、`-3` 递增后缀；没有标题时交付到 `~/Desktop/watch-YYYYMMDD-HHMMSS/`。正式列表任务显式传 `--output-dir` 时，该目录是列表交付父目录；最终文件夹仍必须使用播放列表标题或 `watch-YYYYMMDD-HHMMSS`。

### 3) 列表 URL（支持平台列表解析）

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"

python3 scripts/cli.py \
  --source-url "https://www.bilibili.com/..." \
  --mock-review-response watchbrief_v5/golden/sample_payload_heartflow.json
```

如果平台解析结果是列表，会自动创建一个列表交付文件夹；未显式传 `--output-dir` 时放在 Desktop，显式传 `--output-dir` 时放在该父目录下。`00-watch-order.html` 和每条视频 HTML 必须直接位于播放列表标题文件夹内。

### 4) 手动 review 复核（可选）

用于只看 review request / review response 的构建与验证（不跑完整视频链路）：

```bash
python3 scripts/analyzer/codex_review.py \
  --local-extract /tmp/local_extract.json \
  --dry-run-review-request \
  --output /tmp/review_request.json

python3 scripts/analyzer/codex_review.py \
  --review-response /tmp/review_response.json \
  --output /tmp/normalized_report_payload.json
```

### 5) 启用真实 Codex CLI Review

```bash
python3 scripts/cli.py \
  --source-url "https://example.com/your-video" \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-model gpt-5.5 \
  --timeout 600
```

使用独立 Codex CLI 账号目录：

```bash
python3 scripts/cli.py \
  --source-url "https://example.com/your-video" \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-home-root ~/.watchbrief_codex \
  --codex-account default \
  --codex-model gpt-5.5 \
  --timeout 600
```

真实调用走本机 `codex exec` 登录态，不强制 `OPENAI_API_KEY`。模型输出会先 JSON 解析，再过 schema，再过 validator，任意一层失败会中断该条。

最终 `replacement_score` 不采用 Codex 自由分数。Codex 的分数只作为 `score_trace.model_suggested_score` 记录；WatchBefore 默认按新公式计算：

```text
information_density*0.35 + evidence_quality*0.35 + topic_interest*0.3
```

默认单视频 HTML 是一篇短导读：首屏 `target_summary` 先说明视频会怎样推进，模型生成的 `reading_flow` 用 2 到 5 段带读者走完整条内容线，随后用 `视频到底讲了什么？` 收束。评分、画像判断、`直接结论 / 具体观点 / 可直接采用 / 注意边界`、完整时间线和溯源信息均为按需展开的索引；`值得补看` 和纠偏反馈保持可见。长视频的 `reading_flow` 必须用 3 到 5 段覆盖完整时间线；renderer 不自己编写、补全或拼接正文。

WatchBefore 正式运行默认且强制加载下面这份个人画像：

```bash
python3 scripts/watchbefore.py \
  --source-url "https://example.com/your-video"
```

默认路径固定为 `~/.watchbrief/preferences/seth.yaml`。画像缺失、损坏或 schema 不符时直接停止，不生成未绑定画像的正式报告；`--preference-profile` 只用于显式替换为另一份合法画像。WatchBefore 只绑定 profile 的路径、schema 和 hash，不把观看历史明文塞进 review request。个人话题兴趣来自统一兴趣画像中的领域和细分关注点；观看时长和观看量进入匹配证据，不会变成“正向 / 负向 / 稍后再看”主题，也不使用作者级推断。默认 HTML 顶部显示紧凑的“画像判断”：命中时写“可能喜欢”并列出该领域的历史观看时长、观看量和占比；未命中时只写“暂时无法判断”，不会反推成“不喜欢”。

从历史观看事实升级为统一兴趣画像，需要先批量跑样本视频，拿到 WatchBefore 内容报告，再交给本地 Qwen 聚合：

```bash
python3 scripts/preferences/build_content_profile.py \
  --events ~/.watchbrief/preferences/events.jsonl \
  --reports "/path/to/watchbefore/reports/**/*.json" \
  --output ~/.watchbrief/preferences/seth.yaml
```

统一画像只使用真实历史观看记录中实际观看秒数大于 0、且已有字幕或音频转写内容报告的视频；`watch_later` 完全排除。领域先由本地 Qwen 建立并去重，再用固定报告 ID 逐条分类；每个领域至少需要 3 份内容报告支持。领域和内容形态按累计观看秒数优先、观看记录数量次优先排序，并输出 `watched_seconds / watched_hours / watch_count / watch_time_share`。画像输出 `domains / subtopics / keywords / content_preferences`，不再生成行为桶主题。

最终 payload 会写入稳定元信息：`transcript_hash`、`qwen_model_id`、`qwen_prompt_version`、`qwen_prompt_fingerprint`、`codex_model`、`codex_prompt_version`、`codex_prompt_fingerprint`、`scoring_formula_version`、`watchbrief_version`。正式运行的 review request 还会记录 `preference_profile_schema_version` 和 `preference_profile_sha256`。高分不能显示“报告足够替代”，低分不能显示“建议完整看”；这类冲突在 validator / retry 层处理，不由 renderer 兜底。

通过 schema 和 validator 的最终报告会写入 report cache，默认目录：

```text
~/.watchbrief/cache/reports/
```

cache key 至少包含：`transcript_hash`、`qwen_model_id`、`qwen_prompt_fingerprint`、`codex_model`、`codex_prompt_fingerprint`、`scoring_formula_version`、`watchbrief_version`。传入 `--preference-profile` 时，也会绑定 profile hash。同一转写、同一模型、同一 prompt、同一评分公式和同一偏好档案再次运行时，默认复用上一次通过验证的 `normalized_payload`，不重新调用 Codex review，但仍会重新渲染当前 HTML。需要强制重分析时使用：

```bash
--force-reanalysis
```

### 6) 转写和本地 Qwen 提取

默认转写器是本机 MiMo MLX runtime：

```bash
--transcriber auto
```

`auto` 只表示“使用 MiMo MLX”，不会静默 fallback 到 Whisper。WatchBefore 会优先查找 `WATCHBRIEF_MLX_AUDIO_PYTHON`，再查找 `/Users/apple/Documents/MiMo-V2.5-ASR-MLX/.venv/bin/python`，最后才检查当前 Python；不再查找 WatchBefore 项目或 Hermes skill 内的 `.venv-mlx`。MiMo runtime 不可用且没有显式允许 fallback 时，会失败为：

```text
transcriber_unavailable: MiMo MLX unavailable. Checked Python candidates: ...
```

Whisper 只有两种情况会用：

```bash
--transcriber whisper
```

或者：

```bash
--allow-whisper-fallback
```

WebUI 的“推荐转写器”会先检测本机 MiMo runtime：可用时保持 CLI 默认 `auto`；不可用时仍保持 `auto` 并提示配置 MiMo，不自动生成 `--transcriber whisper`。Whisper 只保留为手动显式选项。

#### 可选 MiMo-VL 画面理解

纯转写看不到界面操作、图表和屏幕文字。WatchBefore 默认按每条视频转写中的画面依赖线索[自动判定](docs/mimo-vl-activation-rules.md)是否启用 MiMo-VL；也可显式强制开启。当前流程先检查字幕/转写覆盖率，再分析画面；完全无旁白或低覆盖率视频不能仅靠 MiMo-VL 生成正式报告：

```bash
cd "/Users/apple/Documents/New project/watchbrief_v5"

python3 scripts/watchbefore.py \
  --source-url "<video_url>" \
  --visual-analysis \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-model gpt-5.5
```

默认环境是 `/Users/apple/Documents/MiMo-VL-7B-RL-2508`，也可通过 `WATCHBRIEF_MIMO_VL_ROOT` 或 `--mimo-vl-root` 显式指定。运行时严格校验 Apple Silicon、官方 BF16 四分片和 `mlx-vlm==0.5.0`；模型文件约 16.6 GB，实测峰值统一内存约 17.6 GB，建议 32 GB 或更高内存。

自动模式只在单条视频命中线索且不超过 30 分钟时开启。开启后按完整时间轴确定性抽帧，包含首帧和末帧，默认最大间隔 10 秒；长视频按固定小批次送入同一个常驻模型进程，不丢帧、不重复加载模型。MiMo-VL 只提供画面可见事实，不能证明音频说过什么。视频获取、抽帧、模型 JSON、帧覆盖或证据哈希任一失败都会停止该条，不会静默退回纯转写。

可调整参数：

```bash
--visual-max-frame-gap-seconds 10
--visual-max-tokens 4096
--visual-timeout 900
```

成功后 `visual_evidence.json` 会记录每帧时间戳、图像哈希、可见文字、动作、限制和总证据哈希；该哈希进入 Qwen/Codex prompt、最终 payload 与 report cache identity，避免画面版报告误命中纯转写缓存。

#### 本地 Qwen 提取

本地提取层会调用 LM Studio 里的 Qwen-family 模型。默认 endpoint：

```text
http://127.0.0.1:1234/v1
```

可配置：

```bash
export WATCHBRIEF_QWEN_BASE_URL="http://127.0.0.1:1234/v1"
export WATCHBRIEF_QWEN_MODEL="qwen3-30b-a3b-instruct-2507-mlx"
```

如果没有指定模型，WatchBefore 默认使用 `qwen3-30b-a3b-instruct-2507-mlx`。`local-qwen` 提炼路径可以用 `WATCHBRIEF_QWEN_MODEL` 或 `--qwen-model` 显式覆盖；非 Qwen 模型会被拒绝。需要 Gemma / Llama / Mistral 等非 Qwen 本地模型时，使用显式外部提炼路径：

```bash
python3 scripts/cli.py \
  --source-url "https://example.com/your-video" \
  --extract-provider local-openai-compatible \
  --extract-model "gemma-3-local" \
  --extract-api-base "http://127.0.0.1:1234/v1" \
  --review-provider local \
  --timeout 600
```

需要 Gemini / Claude / Kimi 时，不要在命令里粘贴 token，只导出环境变量后传变量名：

```bash
export GEMINI_API_KEY="..."

python3 scripts/cli.py \
  --source-url "https://example.com/your-video" \
  --extract-provider gemini \
  --extract-api-key-env GEMINI_API_KEY \
  --review-provider gemini \
  --review-api-key-env GEMINI_API_KEY \
  --timeout 600
```

当前正式规则是 WatchBefore 和 Hermes 都作为客户端连接 LM Studio 的 `http://127.0.0.1:1234/v1` endpoint，通过请求里的 `model` 字段选择不同模型。两者不是运行在 `1234` 端口上，不存在客户端抢端口问题；风险是并发重任务共享本机统一内存和推理算力，因此不建议同时跑 WatchBefore 完整视频链路和 Hermes 本地模型重任务。

local_extract 会做确定性专名规范化，只处理明确别名，不做猜测。当前固定规范化：`叔本华 / Schopenhauer`、`尼采 / Nietzsche`、`柏拉图 / Plato`、`萨特 / Sartre`、`阿兰·德波顿 / Alain de Botton`；结果写入 `important_terms` / `corrected_terms` 并传给 Codex review。

Qwen local_extract timeout 默认等于 CLI 的 `--timeout`。需要单独调整本地 Qwen 时使用：

```bash
--qwen-timeout 600
```

Qwen timeout 会分类为 `stage=local_extract`、`reason_code=local_qwen_timeout`，不会再泛化成 `pipeline_failed`。

### 7) YouTube 字幕优先级与 YouTube Connect fallback

YouTube 字幕抓取优先使用英文原字幕，不先请求 `zh-Hans` / `zh` 机器翻译字幕。当前顺序：

```text
manual English subtitles -> automatic English captions -> configured non-English subtitles -> translated subtitles -> audio fallback
```

英文候选包含 `en`、`en-US`、`en-GB`、`en-orig` 和实际字幕列表中的 `en-*`。每个候选逐个下载；单个候选失败只记录到 `subtitle_candidate_failures`，不会直接拖死整条视频。只有全部字幕候选失败，才进入 audio_downloader / MiMo MLX。

item_manifest 会记录 `subtitle_probe_attempted`、`subtitle_candidates`、`subtitle_candidate_failures`、`selected_subtitle_lang`、`selected_subtitle_kind`、`selected_subtitle_format`、`subtitle_fetch_reason` 和 `audio_downloader_skipped_due_to_subtitle`。

YouTube Connect transcript fallback 入口在：

```text
scripts/providers/youtube_connect_provider.py
```

当 YouTube 单视频 resolver 因 `platform_restriction`、bot check、sign-in restriction 或 login verification 失败，但 URL 仍能解析出 video_id 时，pipeline 会尝试 YouTube Connect transcript fallback。fallback 成功后会构造标准 `watchbrief_v5.transcript_material.v1`，继续进入 `local_extract -> Codex review -> validator -> renderer`；不会进入 `audio_downloader` 或 MiMo MLX。fallback 成功时，item_manifest 会记录：

```text
resolver_failed=true
resolver_reason_code=platform_restriction
transcript_fallback_provider=youtube-connect
transcript_fallback_success=true
```

fallback 失败时保留原 resolver 失败，不伪装成功。这个 fallback 只处理单视频 transcript，不替代 playlist expansion、cookies handling、SRT/VTT 文件输出或完整 metadata。

如果失败原因显示 YouTube 要求确认不是机器人，CLI 只会打印人工验证指引和只读复测命令。允许的浏览器动作只有：

```bash
open -a "Google Chrome" "https://www.youtube.com/watch?v=VIDEO_ID"
```

用户手动完成验证后，再运行：

```bash
yt-dlp --cookies-from-browser chrome --skip-download --list-subs "https://www.youtube.com/watch?v=VIDEO_ID"
```

WatchBefore 不自动点击验证码，不绕过验证，不保存、不打印、不展示 cookies。

### 8) B 站字幕 provider / 登录态 / 音频 fallback

WatchBefore 的 B 站字幕路径由 `scripts/bilibili_content_provider.py` 负责。它不复制 Bilibili Evolved、Bilibili Obsidian Clipper、BilibiliDown、BBDown 或 yutto 源码，只按公开项目共同验证过的接口流程实现 WatchBefore 自己的 provider 边界。

接口顺序：

```text
B 站 URL / BV / AV
-> x/web-interface/view 获取 bvid / aid / cid / pages / 标题 / UP 主 / 时长
-> x/player/wbi/v2 获取 data.subtitle.subtitles
-> x/player/v2 fallback
-> subtitle_url
-> BCC JSON body
-> watchbrief_v5.transcript_material.v1
```

字幕成功时，item_manifest 会记录 `provider=bilibili_content_provider`、`source_api=player-wbi-v2` 或 `player-v2`、`selected_subtitle_lang`、`selected_subtitle_kind`、`selected_subtitle_format=bcc`。如果字幕时间轴正常，`audio_downloader` 标记为 skipped；如果字幕最后时间明显超过视频总时长（超过 `1.2x` 或 `+20秒` 中更宽松的阈值），该字幕会被丢弃，并重新进入音频下载 / MiMo MLX 转写路径。

用户明确要求用音频正文重新校准时，可传 `--force-audio-transcription`。该参数会在 item_manifest 记录 `force_audio_transcription=true`，明确跳过平台字幕并进入 `audio_downloader -> MiMo MLX`；它不能由默认流程或失败兜底自动开启。

如果 B 站返回 `need_login_subtitle=true`，或字幕轨存在但没有 `subtitle_url`，错误会明确分类为：

```text
login_required_for_subtitle
```

这个状态不会静默当作“无字幕”，也不会进入 audio_downloader。只有明确 `no_subtitle_available` 时，才允许进入音频下载 / MiMo MLX 分支。

用户授权后，WatchBefore 可以把本机浏览器登录态作为正常采集路径。默认登录态顺序是 Chrome 首选、Safari 备选；CLI 不传 `--cookies-from-browser` 时，会把 `chrome -> safari` 统一传给 resolver、YouTube subtitle_fetcher、B 站字幕 provider 和 audio_downloader。Chrome 如果明确出现 cookies 不可读、Extracted 0 cookies、login_required_for_subtitle、platform_restriction 或 browser cookie access error，当前阶段才会尝试 Safari。显式传 `--cookies-from-browser chrome` / `safari` / `edge` 时，以显式值为准，不自动 fallback。B 站 resolver 和字幕 provider 都只使用用户本人授权的目标视频登录态，不打印、不保存、不展示 cookies 内容。B 站 resolver 阶段会把授权的浏览器来源传给 `yt-dlp`，并带 B 站 Referer / User-Agent；如果 resolver 遇到 412，会尝试官方页面 metadata fallback。字幕不可用后，audio_downloader 才下载音频；如果 resolver 已拿到 `bvid/cid`，audio_downloader 会优先用官方 playurl API 获取 `dash.audio`，再下载并转成标准 WAV。若 playurl API 不可用，再尝试页面 `__playinfo__` / `__INITIAL_STATE__` fallback。

B 站 `https://www.bilibili.com/list/ml...` 会按列表处理，不会只取 URL query 里的当前 `bvid`。展开优先使用 `yt-dlp --flat-playlist`，必要时用 B 站收藏列表 API 或页面初始数据补充；失败时返回 `bilibili_list_expansion_failed`。能获取列表标题时，正式列表任务的桌面文件夹优先使用该标题。

`--source-file` 中的每个 URL 会先走同一套 resolver 和浏览器登录态，拿到真实 metadata 后再进入字幕和质量门禁；不会把文件里的 URL 直接包装成 `duration=未知` 的占位条目。

```bash
python3 scripts/cli.py \
  --source-file /path/to/bilibili_urls.txt \
  --review-provider codex-cli \
  --enable-codex-review \
  --codex-home-root ~/.watchbrief_codex \
  --codex-account account2 \
  --codex-model gpt-5.5 \
  --timeout 600
```

也可以使用 Netscape cookies 文件：

```bash
python3 scripts/cli.py \
  --source-file /path/to/bilibili_urls.txt \
  --mock-review-response watchbrief_v5/golden/sample_payload_heartflow.json \
  --cookies-file /path/to/cookies.txt
```

可用参数：

```bash
--cookies-from-browser chrome   # 显式只用 Chrome，不 fallback
--cookies-from-browser safari
--cookies-from-browser edge
--cookies-file /path/to/cookies.txt
--no-browser-auth
```

WatchBefore 不打印 cookies 内容，不把 cookies 写入报告；manifest / item_manifest 只记录 `cookies_browser_attempts`、`selected_cookies_browser`、`cookies_fallback_reason`、授权来源和 fallback 是否成功。

B 站音频失败会细分为：

```text
login_required_for_subtitle
no_subtitle_available
bilibili_subtitle_api_failed
bilibili_subtitle_download_failed
bilibili_subtitle_json_invalid
bilibili_playurl_api_failed
bilibili_playurl_api_forbidden
bilibili_playurl_api_no_dash_audio
bilibili_audio_url_not_found
bilibili_audio_download_failed
bilibili_audio_convert_failed
```

### 9) 小红书 board / 专辑 resolver

小红书专辑链接不会交给普通 `yt-dlp` 单视频 resolver。以下形式会先识别为 board/list：

```text
https://www.xiaohongshu.com/board/<board_id>
https://www.xiaohongshu.com/user/profile/<user_id>/board/<board_id>
query / fragment 中包含 /board/<board_id> 的分享链接
```

resolver 返回：

```text
source_kind=list
source_subkind=xiaohongshu_board
```

读取顺序：

```text
/api/sns/web/v1/board/note -> window.__INITIAL_STATE__.board.boardFeedsMap
```

如果页面/API 需要登录态，沿用统一 cookie 策略：显式 `--cookies-from-browser` 优先；默认 `chrome -> safari`。实现只把浏览器 cookie 读成请求头，不打印、不保存、不展示 cookie 内容。

note 分类：

- video note：构造标准 `/explore/<note_id>` URL，交给现有单视频 pipeline。
- image/text note：标记 `status=skipped`、`reason_code=non_video_note`，不进入 audio_downloader、MiMo MLX、Qwen、Codex 或 renderer。

失败分类：

```text
xiaohongshu_board_resolver_failed
xiaohongshu_board_empty
```

WatchOrder 对 board 会显示总 note 数、视频 note 数和图文跳过数；图文 note 不计入 `failed_count`，也不会生成单视频 HTML。

第三方参考项目和许可证记录见 `ACKNOWLEDGEMENTS.md`。本阶段引用范围：

- Bilibili Evolved — MIT，参考 B 站字幕接口流程。
- Bilibili Obsidian Clipper — MIT，参考 BCC 字幕处理思路。
- BilibiliDown — Apache-2.0，参考字幕下载 / SRT 转换思路。
- BBDown — MIT，参考 CLI 行为和字幕验证。
- yutto — GPL-3.0-only，只作为行为观察，不复制源码。

## 输出位置

- 单视频默认正式交付：`~/Desktop/<safe-title>.html`，不生成 `00-watch-order.html`。
- 单视频显式传 `--output-dir <dir>` 时，`<dir>` 是最终 HTML 的父目录；manifest、payloads、work/debug 默认进临时目录，成功后清理。
- 列表/文件输入默认正式交付：优先 `~/Desktop/<播放列表标题>/`；同名已存在时使用 `~/Desktop/<播放列表标题>-2/`、`-3/` 递增后缀；没有播放列表标题时 fallback 到 `~/Desktop/watch-YYYYMMDD-HHMMSS/`。目录里只放每条视频 HTML 和 `00-watch-order.html`。
- 列表输出必须保持播放列表原始顺序；`00-watch-order.html` 和单视频 HTML 文件名前缀 `01/02/03...` 都不得按评分重排。
- 正式列表显式传 `--output-dir <dir>` 时，`<dir>` 是列表交付父目录；最终目录必须是 `<dir>/<播放列表标题>/`，同名已存在时递增为 `<播放列表标题>-2/`、`-3/`。
- CLI 默认只打印最终 HTML / Watch Order 路径，不自动打开浏览器；只有显式传 `--open-output` 时才打开最终输出。
- debug / payload / audio / transcript 不混入正式交付目录；成功后默认清理。
- 失败时默认保留诊断产物在临时 debug 目录，并打印 `debug_artifacts` 路径；不默认写入 `~/Desktop/WatchBefore-Debug/`。
- 保留调试产物：传 `--debug-dir <debug-dir>` 或 `--keep-debug-artifacts`；需要固定临时工作目录时传 `--work-dir <work-dir>`。
- 正式用户任务和默认诊断任务都不要写入 `~/Desktop/WatchBefore-Runs/`；Hermes 正式提炼命令也不要默认传 `--output-dir` 到这个目录。
- 阶段验收、smoke、cache 复测、debug 和临时复验如需长期保留，可以显式传 `--output-dir <保留目录>`；正式列表任务会在该目录下创建播放列表标题文件夹，诊断任务才把该目录本身作为最终诊断目录。
- 诊断、复现、debug、查问题任务必须使用 `--diagnostic-run` 或 `--repro-run`。未显式传 `--output-dir` 时，CLI 默认写入系统临时目录，并把 manifest/payload/debug 放在该目录的 `_debug/` 下；如果生成 HTML，只算 diagnostic artifact，不算正式交付。
- 诊断任务显式传 `--output-dir` 时，该目录仍被视为最终诊断目录，不能再套二级 HTML 文件夹；WORKLOG 必须标明这是诊断输出，不是正式输出。

## 日期与时长

- 单视频页的日期只能来自视频发布日期，优先级为 `publish_date`、`release_date`、`upload_date`、`timestamp`、平台明确发布时间字段；不能使用报告生成时间、当前系统时间或文件时间。
- `upload_date=YYYYMMDD` 会格式化为 `YYYY-MM-DD`；`timestamp` / B 站发布时间会转换为 `YYYY-MM-DD`。
- 单视频页的时长只能来自视频真实 `duration`，支持秒数、float 秒数、`MM:SS`、`HH:MM:SS`，统一显示为 `45秒`、`12分03秒`、`1小时02分03秒` 这类中文自然格式。
- 只有 metadata 真缺失时才显示 `未知`；对应缺失原因写入 `item_manifest.json` 的 metadata step：`publish_date_missing` / `publish_date_source`、`duration_missing` / `duration_source`。

## 失败分类与恢复方式

| 分类 | 来源 | 处理方式 |
|---|---|---|
| `resolver` | 输入源无法解析/列表解析失败 | 检查链接类型、网络、平台限制；B 站 412 会明确返回 `bilibili_412_blocked`，不会泛化成普通 resolver 失败 |
| `subtitle_fetcher` | 字幕不可得或抓取受限 | 进入音频转写分支，确认音频下载可用 |
| `audio_downloader` | 下载失败/转码失败 | 检查 yt-dlp、输出目录权限与音频码率兼容性 |
| `transcriber` | 转写失败 / `transcriber_unavailable` | 默认需要本机 MiMo MLX；没有 MiMo 时先配置 `/Users/apple/Documents/MiMo-V2.5-ASR-MLX/.venv/bin/python`，Whisper 只作为显式手动选项 |
| `transcript_quality` | `transcript_coverage_too_low` | 最终转写覆盖率不足，停止在 Qwen 前；检查 ASR 是否只覆盖片头或极少内容。平台字幕时间轴明显超过视频时长、覆盖太短或内容太少时会先丢弃字幕并改走音频转写 |
| `visual_preflight` / `visual_acquisition` / `visual_analysis` | MiMo-VL 环境、视频、抽帧、严格 JSON 或帧覆盖失败 | 自动判定命中或强制开启时可能出现；按错误检查 MiMo-VL 根目录、官方 BF16 分片、`mlx-vlm==0.5.0`、FFmpeg 或视频访问，不允许自动跳过画面证据 |
| `local_extract` | `local_qwen_unavailable` / non_qwen_model_rejected / local_qwen_invalid_response / external_extract_model_missing / api_key_missing | `local-qwen` 检查 LM Studio 和 Qwen-family 模型；非 Qwen 本地模型改用 `--extract-provider local-openai-compatible`；云模型检查 API key 环境变量是否已设置 |
| `codex_review` | codex_cli_missing/codex_not_logged_in/auth/quota/timeout/empty/invalid_json/schema/validator/model | 检查 Codex CLI 是否安装、是否登录、账号目录是否正确、额度、超时和返回 JSON；mock 只能单独验证开发链路，不能代替失败的真实报告 |
| `validator` | 字段、分段、tag/topic、结论污染 | 修正模型输出结构后重跑该条 |
| `renderer` | 渲染前校验失败 | 模块不允许兜底，先修 payload 后重试 |

## 自检

```bash
python3 scripts/check_watchbrief_skill.py
python3 scripts/check_watchbrief_skill.py --strict-install
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 让 Codex / Hermes 发现新技能（可选）

```bash
mkdir -p ~/.codex/skills
rsync -a watchbrief_v5/ /Users/apple/.codex-accounts/accounts/default/codex-home/skills/watchbrief_v5/
python3 scripts/check_watchbrief_skill.py --strict-install
```

`watchbrief_v5` 是当前唯一入口；旧 `v1deodownload` 不再作为 strict install 条件或运行回退。

## 说明

V5 与 V4 同步输出约束：
- 不改合同（`CONTRACT_V6.md`）与字段职责。
- 不改模板结构与视觉骨架。
- 不改 analyzer / validator / schema 的核心逻辑（已固定）。
