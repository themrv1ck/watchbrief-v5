# WatchBrief V5 Contract

Formal usage and runbook rules are documented in `USAGE_V5.md`.

## Product Positioning

WatchBrief V5 generates fixed-format Chinese viewing-decision reports for videos.

The final visible product must stay compatible with the established V4 report structure and watch-order structure.

## Phase 2 Boundary

Phase 2 must not connect to real video infrastructure.

Allowed:

- local mock payloads
- golden JSON samples
- schema checks
- validator checks
- deterministic helper checks

Forbidden:

- real URL resolving
- subtitle fetching
- audio downloading
- transcription
- model calls
- pipeline edits
- renderer implementation

## Phase 3 Boundary

Phase 3 adds only pure rendering on top of Phase 2.

Allowed:

- render validated local mock payloads
- generate golden HTML from golden payloads
- test that rendered HTML keeps the V4 page skeleton
- test that renderer fails before rendering invalid payloads

Forbidden:

- real URL resolving
- subtitle fetching
- audio downloading
- transcription
- model calls
- Codex final review
- pipeline edits

Renderer must call validator first. Missing or invalid fields must fail. Renderer must not fill, repair, rewrite, infer, or generate report fields.

## Phase 4 Boundary

Phase 4 adds only analyzer prompt scaffolding and deterministic mock-testable analysis helpers.

Allowed:

- analyzer prompt text
- local extraction from already-provided mock transcript segments
- Codex review request assembly
- Codex review response parsing against validator
- mock tests

Forbidden:

- real URL resolving
- subtitle fetching
- audio downloading
- transcription
- model calls
- Codex final review execution
- pipeline edits
- renderer structure changes

`codex_review.py` may build a prompt packet and parse a provided JSON response. It must not call Codex, OpenAI, local LLMs, subprocess model runners, browsers, network clients, or shell commands.

## Phase 5 Boundary

Phase 5 adds only local transcript source adapters.

Allowed:

- read existing local subtitle files
- read existing local transcription JSON files
- read existing local transcription TXT files
- normalize transcript material into timestamp segments
- validate timestamp format, language, and transcript_quality
- test with local fixtures

Forbidden:

- real URL resolving
- yt-dlp
- subtitle downloading
- MP3 downloading
- real transcription
- model calls
- Codex final review execution
- full video_pipeline.py
- renderer structure changes

The adapter output is transcript material. It is not a final report payload and must not contain final report fields.

## Phase 6 Boundary

Phase 6 adds only acquisition-layer modules.

Allowed:

- URL resolver for single video URLs and list URLs
- platform subtitle fetcher
- audio downloader that is allowed only after subtitle fetch is unavailable
- transcriber wrapper that converts audio into timestamped transcript material
- mock tests
- opt-in real smoke tests

Forbidden:

- full video_pipeline.py
- end-to-end HTML generation from real video
- analyzer/model execution
- Codex final review execution
- renderer changes
- validator changes
- schema changes
- HTML template changes

All acquisition results must be converted into `watchbrief_v5.transcript_material.v1`. Failures must be classified as platform restriction, subtitle unavailable, audio download failed, transcribe failed, or format conversion failed where applicable.

### Browser Auth And Bilibili Resolver

With user authorization, browser login state is a normal acquisition path for target video URLs. When `--cookies-from-browser` is not provided, WatchBrief must use the shared browser order `chrome -> safari`: resolver, YouTube subtitle_fetcher, Bilibili subtitle provider, and audio_downloader all receive the same attempt list. If Chrome clearly fails due to unreadable cookies, extracted zero cookies, login-required subtitle, platform restriction, or browser cookie access error, the same stage may retry Safari. Explicit `--cookies-from-browser chrome`, `--cookies-from-browser safari`, or `--cookies-from-browser edge` overrides the default and must not fallback to another browser. The system may also use an explicit cookies file, but must not print, store, or expose cookie contents.

YouTube subtitle rules:

- YouTube subtitle selection must not request translated `zh-Hans` / `zh` subtitles before original English subtitles.
- Manual English subtitles are preferred over automatic English captions.
- Automatic English captions are preferred over translated subtitles.
- English candidates include `en`, `en-US`, `en-GB`, `en-orig`, and actual `en-*` entries exposed by the platform.
- Subtitle candidates must be tried one by one. A failed candidate records its failure and the fetcher continues to the next candidate.
- audio_downloader may run only after all usable subtitle candidates fail.
- item_manifest records the subtitle probe, candidate list, candidate failures, selected language/kind/format, fetch reason, and whether audio_downloader was skipped due to subtitles.

YouTube Connect transcript fallback lives at `scripts/providers/youtube_connect_provider.py`. When a single-video YouTube resolver fails with `platform_restriction`, bot check, sign-in restriction, or login verification, and the URL still contains a valid video id, the pipeline may try this fallback before failing the item. If fallback succeeds, it must produce `watchbrief_v5.transcript_material.v1`, set `transcript_fallback_provider=youtube-connect`, set `transcript_fallback_success=true`, and continue through local_extract, Codex review, validator, and renderer. It must skip audio_downloader and MLX/Whisper transcription. If fallback fails, the original resolver failure remains authoritative.

The YouTube Connect fallback is not a full replacement for the primary resolver. It does not expand playlists, handle cookies, write SRT/VTT files, or guarantee full metadata. It may set title to video_id and duration to the transcript-derived duration when richer metadata is unavailable.

YouTube manual verification rules:

- If failure text contains bot check, sign-in restriction, login verification, or `platform_restriction`, the CLI may prompt the operator to manually verify in Chrome.
- The only browser action allowed for this flow is opening Chrome to the target video URL.
- The system must not click CAPTCHA, bypass verification, save cookies, print cookies, or expose cookie contents.
- After the user completes verification, the read-only probe is `yt-dlp --cookies-from-browser chrome --skip-download --list-subs <url>`.

Bilibili resolver rules:

- resolver must pass authorized cookies options into metadata/list `yt-dlp` calls.
- the default browser cookies order is Chrome first, Safari second unless the caller explicitly chooses one browser.
- resolver requests must include Bilibili Referer and a normal User-Agent.
- HTTP 412 at resolver must be classified as `bilibili_412_blocked`, not generic `resolver_failed`.
- `https://www.bilibili.com/list/ml...` must be classified as a list source. The resolver must expand list entries through `yt-dlp --flat-playlist` first, and may use the Bilibili favorite-list API or page initial state as a structured fallback. It must not report success by processing only the current query `bvid`; expansion failure is `bilibili_list_expansion_failed`.
- CLI `--source-file` mode must resolve each URL with the normal resolver and the same browser-auth options before item processing. It must not create placeholder video items with `duration=未知` or `date=未知` when source metadata is available.
- If authorized browser/cookies source is available, resolver may try page metadata fallback through `__INITIAL_STATE__`, `__playinfo__`, or official metadata APIs.
- resolver fallback only extracts metadata; it must never download audio.
- resolver, subtitle_fetcher, Bilibili subtitle provider, and audio_downloader debug records may store `cookies_browser_attempts`, `selected_cookies_browser`, `cookies_fallback_reason`, `cookies_source`, `fallback_method`, `fallback_success`, and `reason_code`, but never cookie values.
- If resolver provides `bvid/cid`, audio_downloader may use the official Bilibili playurl API to fetch `dash.audio` before trying page `__playinfo__` fallback.
- audio_downloader debug records may store `method`, `cookies_source`, `audio_url_source`, `fallback_success`, and `playurl_status`, but never cookie values or raw media URLs.

### Transcriber Strategy

Current behavior:

- The canonical WatchBrief V5 project root is `/Users/apple/Documents/New project/watchbrief_v5`; Hermes and Codex default runs must invoke `python3 scripts/cli.py ...` from that directory.
- `--transcriber auto` is the default.
- `auto` means MLX-Audio first.
- Whisper is allowed only when the user explicitly passes `--transcriber whisper`, or explicitly passes `--allow-whisper-fallback` and MLX-Audio is unavailable or fails.
- The pipeline must not silently fall back from MLX-Audio to Whisper.
- MLX-Audio Python lookup must prefer `WATCHBRIEF_MLX_AUDIO_PYTHON`, the canonical standalone project `.venv-mlx/bin/python`, the current skill-root `.venv-mlx/bin/python`, the parent project-root `.venv-mlx/bin/python`, and Hermes installed skill `.venv-mlx/bin/python` before considering the current Python executable. It must not depend on the legacy `v1deodownload` skill `.venv-mlx`.
- If MLX-Audio is unavailable and fallback is not explicitly allowed, the acquisition layer fails with `transcriber_unavailable` and a message that lists checked Python candidates.

### Local Qwen Extract Strategy

`local_extract.py` must call a local Qwen-family model through an OpenAI-compatible LM Studio endpoint before building the Codex review request.

Rules:

- The endpoint defaults to `http://127.0.0.1:1234/v1`.
- The endpoint can be changed with `WATCHBRIEF_QWEN_BASE_URL`; `WATCHBRIEF_QWEN_API_BASE` and `--qwen-api-base` remain supported.
- The default model is `qwen3-30b-a3b-instruct-2507-mlx`.
- The model can be explicitly overridden with `WATCHBRIEF_QWEN_MODEL` or `--qwen-model`.
- WatchBrief and Hermes are clients of the LM Studio `http://127.0.0.1:1234/v1` endpoint. They select different local models through the request `model` field; they do not run on port `1234`.
- Do not run the full WatchBrief video pipeline concurrently with Hermes local-model heavy tasks. The risk is shared Mac unified memory and compute, not a port conflict.
- Non-Qwen model ids are rejected.
- If no Qwen-family model is available, fail with `local_qwen_unavailable`.
- Qwen output is an intermediate JSON only. It must not include `replacement_score`, `tag`, `watch_verdict`, `final_conclusion`, `watch_segments`, or HTML.
- Qwen output must include `important_terms` and `corrected_terms`; local deterministic entity normalization must standardize clear aliases for `叔本华 / Schopenhauer`、`尼采 / Nietzsche`、`柏拉图 / Plato`、`萨特 / Sartre`、`阿兰·德波顿 / Alain de Botton`.
- Qwen local_extract timeout defaults to the CLI `--timeout` value and can be overridden with `--qwen-timeout`.
- Qwen request timeout must fail as `stage=local_extract`, `reason_code=local_qwen_timeout`; it must not be wrapped as generic `pipeline_failed`.
- Qwen non-JSON or malformed JSON must fail as `local_qwen_invalid_response`.
- Qwen intermediate JSON that does not satisfy the local_extract intermediate contract must fail as `local_extract_invalid_output`.
- The item manifest must record `local_extract` started/completed/failed state with `qwen_base_url`, Qwen model setting, and timeout.

### Transcript Coverage Gate

Before `local_extract`, every transcript material must pass a transcript quality gate. The gate records `video_duration_seconds`, `transcript_first_start`, `transcript_last_end`, `transcript_covered_duration`, `transcript_coverage_ratio`, `transcript_segment_count`, `transcript_plain_text_char_count`, `transcript_source`, and `transcript_quality_reason` in `item_manifest.json`.

Rules:

- If `video_duration_seconds >= 60` and final transcript `transcript_coverage_ratio < 0.30`, fail as `stage=transcript_quality`, `reason_code=transcript_coverage_too_low`.
- If duration is present and final transcript has extremely low segment or text count, fail with the same reason code before Qwen.
- If duration is missing for platform subtitle sources `subtitle_bcc`, `subtitle_srt`, `subtitle_vtt`, or `youtube_connect`, fail with `transcript_quality_reason=video_duration_missing_for_quality_gate`; segment count and text count are not valid substitutes for coverage ratio.
- If a platform subtitle source `subtitle_bcc`, `subtitle_srt`, or `subtitle_vtt` ends clearly beyond video duration, using the looser threshold of `video_duration_seconds * 1.2` or `video_duration_seconds + 20`, reject that subtitle with `transcript_quality_reason=subtitle_timeline_exceeds_video_duration` and retry through `audio_downloader -> transcriber`. The audio transcript must then pass the same quality gate before Qwen.
- If a platform subtitle source `subtitle_bcc`, `subtitle_srt`, or `subtitle_vtt` is too short or too sparse while duration is known, reject that subtitle and retry through `audio_downloader -> transcriber` instead of failing before audio fallback. The audio transcript must then pass the same quality gate before Qwen.
- If duration is missing for full-audio transcript material, use segment count and plain text character count checks before Qwen.
- Videos shorter than 60 seconds are not failed solely because their coverage ratio is below 0.30.
- This gate applies to platform subtitles, YouTube transcript fallback, Bilibili subtitle provider output, ASR output, and future Xiaohongshu audio transcripts.
- On failure, the item must not enter Qwen local_extract, Codex review, validator, renderer, or formal HTML generation. In list runs, the item is counted in `failed_count`, and Watch Order may show it as a failed item.

### Xiaohongshu Board Resolver

- Xiaohongshu board URLs must be detected before generic yt-dlp resolution. Supported forms include `/board/<board_id>`, `/user/profile/<user_id>/board/<board_id>`, and query or fragment values that contain `/board/<board_id>`.
- Board resolution returns `source_kind=list` and `source_subkind=xiaohongshu_board`; the board title should be used as the formal list delivery folder name when available.
- The resolver reads board notes from `/api/sns/web/v1/board/note` first and falls back to `window.__INITIAL_STATE__.board.boardFeedsMap` when the API is unavailable.
- Browser auth follows the common acquisition policy: explicit browser source wins; otherwise try `chrome -> safari`. Cookie values must never be printed, saved as standalone files, or rendered into HTML.
- Each note item records note id, canonical note URL, title, author, note type, video/image-text classification, cover, duration if available, and raw metadata in debug-only fields.
- Video notes enter the normal single-video pipeline. Image/text notes are recorded as `status=skipped`, `reason_code=non_video_note`, and must not enter audio download, transcription, Qwen, Codex, validation, or single-video rendering.
- Skipped image/text notes must not increment `failed_count`. Watch Order may display skipped notes and must expose skipped/non-video counts for board runs.
- Board fetch failures use `reason_code=xiaohongshu_board_resolver_failed`; readable empty boards use `reason_code=xiaohongshu_board_empty`.

### Transcript Language Handling

- If the source transcript is Chinese, analyze the Chinese transcript directly.
- If the source transcript is English, analyze the English transcript directly.
- If the source transcript is mixed, analyze the mixed transcript directly.
- Do not translate the whole transcript before analysis.
- `transcript_material.language` must normalize to `zh`, `en`, `mixed`, or `unknown`.
- Qwen reads the original transcript text and outputs Chinese structured extraction.
- Final HTML remains Chinese.
- English product names, tools, people, and terms may stay in English inside Chinese explanations.

## Phase 7 Boundary

Phase 7 adds only orchestration.

Allowed:

- resolver to transcript acquisition orchestration
- transcript material to local_extract
- local_extract to Codex review request or local rules review
- mock/manual/local review response parsing
- validator-backed renderer call
- single video pipeline
- list framework that processes items strictly one by one
- per-video HTML, payload, and manifest writes
- cleanup only after HTML write succeeds

Forbidden:

- real Codex review execution
- cloud model calls
- prefetching subtitles, audio, or transcripts for a full list
- merging list transcripts into one analysis blob
- renderer changes
- validator changes
- schema changes
- HTML template changes

`video_pipeline.py` requires a mock/manual/local review response provider. Without that provider the pipeline must fail before rendering instead of calling a model.
For list sources, `video_pipeline.py` writes `00-watch-order.html` only after all per-video HTML attempts are finished.

## Phase 8 Boundary

Phase 8 adds only the real Codex CLI review adapter.

Allowed:

- explicit Codex CLI review adapter inside `codex_review.py`
- keep the existing mock/manual/local review path
- dry-run review request output for manual testing
- classify live review failures as `codex_cli_missing`, `codex_not_logged_in`, `auth_failed`, `quota_limited`, `timeout`, `empty_response`, `invalid_json`, `schema_invalid`, `validator_failed`, or `model_error`
- validate model output by schema, then validator, before any renderer receives it

Forbidden:

- implicit model calls
- real model calls unless explicitly enabled by `--enable-codex-review` with `--review-provider codex-cli` or legacy alias `--review-provider codex`
- renderer changes
- validator changes
- schema changes
- HTML template changes
- pipeline order changes
- cleanup rule changes

The renderer must never receive raw model output. Raw model text must first parse as JSON, then pass the single-video schema, then pass the V5 validator. If `final_conclusion`, `tag`, `topic`, `watch_segments`, or `only_one_segment` violates the V5 contract, the live review fails instead of falling back in renderer.

## Phase 10 Boundary

Phase 10 runs a real small-list end-to-end smoke test.

Allowed:

- 2-4 video list smoke input
- strict one-by-one list processing
- real acquisition, transcription, Codex review, schema validation, validator, per-video rendering
- `00-watch-order.html` generation after all per-video HTML attempts
- work-log recording

Forbidden:

- merging list transcripts into one analysis blob
- prefetching all subtitles, audio, or transcripts
- changing the single-video renderer contract
- changing validator rules to let invalid model output pass
- changing schema or HTML template structure to satisfy one smoke run

## Renderer Input

The future renderer may only accept `normalized_report_payload`.

Required fields:

- `title`
- `url`
- `channel`
- `duration`
- `date`
- `topic`
- `replacement_score`
- `tag`
- `one_line_brief`
- `watch_verdict`
- `highest_compression`
- `path_table`
- `arrow_chain`
- `final_conclusion`
- `content_caveat`
- `watch_segments`
- `only_one_segment`
- `score_basis`
- `structured_assessment`
- `score_trace`
- `transcript_hash`
- `qwen_model_id`
- `qwen_prompt_version`
- `qwen_prompt_fingerprint`
- `codex_model`
- `codex_prompt_version`
- `codex_prompt_fingerprint`
- `scoring_formula_version`
- `watchbrief_version`
- `confidence_note`

## Non-Negotiable Rules

- `replacement_score` means remaining value in watching the original video after reading the report.
- `replacement_score` must be recomputed locally from `structured_assessment`: `information_density*0.2 + evidence_quality*0.3 + originality*0.2 + watch_value*0.3`.
- Codex-provided `replacement_score` is only retained as `score_trace.model_suggested_score`; the final visible score must come from deterministic scoring.
- `tag` and `watch_verdict` must be semantically consistent with the final deterministic score. High score cannot say the report is enough; low score cannot recommend full watching.
- Validated reports are cached outside delivery folders under `~/.watchbrief/cache/reports/` by default.
- The report cache key must include at least `transcript_hash`, `qwen_model_id`, `qwen_prompt_fingerprint`, `codex_model`, `codex_prompt_fingerprint`, `scoring_formula_version`, and `watchbrief_version`.
- Cache hits must reuse the previously validated `normalized_payload`, skip Codex review, write the current run's `normalized_payload.json`, render HTML normally, and record `cache_hit`, `cache_key`, and `cached_payload_path` in `item_manifest.json`.
- `--force-reanalysis` must bypass report cache and run Codex review again.
- CLI is the formal delivery boundary for output directory policy.
- Single-video CLI runs without `--output-dir` must deliver only one HTML file to `~/Desktop/` and must not create a task folder or `00-watch-order.html`.
- List CLI runs without `--output-dir` must deliver to one folder under Desktop. If a playlist/list title is available, the folder must be `~/Desktop/<playlist title>/`; if that path already exists, use `-2`, `-3`, and later numeric suffixes without overwriting. If no playlist/list title is available, fallback to `~/Desktop/watch-YYYYMMDD-HHMMSS/`. The folder must contain only `00-watch-order.html` and per-video HTML files.
- List delivery order must preserve the original playlist/item order. `00-watch-order.html` and per-video HTML filename prefixes `01/02/03...` must not be reordered by `replacement_score`.
- List CLI runs with explicit `--output-dir <dir>` must treat `<dir>` as the final delivery folder, not as a parent folder. `00-watch-order.html` and all per-video HTML files must be direct children of `<dir>`.
- CLI runs must not open final HTML or Watch Order output by default. Opening output is allowed only when an explicit output-opening flag is passed.
- Debug, payload, audio, transcript, manifest, and work artifacts must not be mixed into formal delivery folders by default.
- Successful CLI runs must clean default temporary debug/work artifacts unless `--debug-dir` or `--keep-debug-artifacts` is explicit.
- Failed CLI runs without `--debug-dir` must retain diagnostics in the temporary debug directory and print the `debug_artifacts` path; they must not create `~/Desktop/WatchBrief-Debug/` by default.
- Formal user tasks and default diagnostic tasks must not default to `~/Desktop/WatchBrief-Runs/`.
- Phase acceptance, smoke, cache verification, debug, and temporary rechecks may use an explicit `--output-dir <retained diagnostic directory>` when artifacts need to be kept. That folder is the final delivery folder and must not contain another HTML task folder.
- Diagnosis, repro, debug, and issue-investigation runs must use `--diagnostic-run` or `--repro-run` unless an explicit diagnostic `--output-dir` is provided.
- Diagnostic runs without explicit `--output-dir` must default to a system temporary directory, not to the formal single-video Desktop HTML location, formal list task folder, or `~/Desktop/WatchBrief-Runs/`.
- Diagnostic runs must store manifest, payload, and debug artifacts under the diagnostic output directory's `_debug/` folder by default. Any generated HTML is a diagnostic artifact, not a formal delivery.
- Diagnostic runs with explicit `--output-dir <dir>` must still treat `<dir>` as the final diagnostic output directory and must not create a nested HTML task folder.
- Single-video `date` must be the source video publish date, not report generation time, current system date, file creation time, or pipeline runtime. Source priority is `publish_date`, `release_date`, `upload_date`, `timestamp`, then other explicit platform publish fields.
- Single-video `duration` must be the source video duration and must render in Chinese natural format such as `45秒`, `12分03秒`, `44分58秒`, or `1小时02分03秒`.
- Missing publish date or duration may display `未知` only when source metadata is truly absent. The metadata step must record `publish_date_missing` / `publish_date_source` and `duration_missing` / `duration_source`.
- `tag` is a recommendation/replacement label, not a topic.
- `topic` is content metadata, not scoring evidence.
- `final_conclusion` is only thematic closure, not viewing advice.
- `content_caveat` holds limitations and boundaries.
- `watch_segments` has one `primary`, at most one `optional`, and optional `backup`.
- `only_one_segment` must match the `primary` time range.
- `watch_segments` time cards use a vertical separator layout: `start`, `|`, `end`.
- natural Chinese sentences such as `watch_verdict` and `only_one_segment` may render `start - end` for readability without changing the normalized payload.
- `content_caveat` must not render as a hero-side verdict banner; if shown, render it as a low-priority report note.
- renderer must not create, repair, or derive content fields.
