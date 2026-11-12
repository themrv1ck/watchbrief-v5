"""Prompt text for WatchBrief V5 analyzer review.

This module only stores and assembles prompt text. It does not call models.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    from .local_review import build_watch_segments
    from ..report_targets import DEFAULT_REPORT_TARGET, normalize_report_target, report_target_contract
    from ..validator import REQUIRED_REPORT_FIELDS, VALID_TAGS
except ImportError:  # pragma: no cover - direct script execution
    SCRIPTS_DIR = Path(__file__).resolve().parents[1]
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from local_review import build_watch_segments
    from report_targets import DEFAULT_REPORT_TARGET, normalize_report_target, report_target_contract
    from validator import REQUIRED_REPORT_FIELDS, VALID_TAGS


WATCHBRIEF_VERSION = "watchbrief_v6"
QWEN_LOCAL_EXTRACT_PROMPT_VERSION = "watchbrief_v5.qwen_local_extract_prompt.v6"

TERM_LOCALIZATION_RULES = """中文化与专有名词规则：
- 最终报告必须以中文为主，不能大量裸露英文领域术语。
- 英文视频标题、频道名、品牌名、产品名、人名、工具名不要硬翻译。
- 领域术语要自然中文化，营销、产品、创作者领域的通用术语要尽量译成自然中文。
- 如果保留某个英文词确实有助于识别概念，第一次出现写“中文（English）”，后续只用中文。
- 只把无法自然翻译的原始专名放进 important_terms；不要把普通领域术语当成必须裸露的英文专名。
- 示例：personal system product -> 个人方法系统产品；education product -> 教育型产品 / 知识产品；big burning problem -> 强痛点 / 核心痛点；desired outcome -> 目标结果；time frame -> 实现周期；offer -> 产品承诺 / 销售主张；landing page -> 落地页；social proof -> 信任背书 / 社会证明；CTA -> 行动号召 / 行动按钮；features and benefits -> 功能与收益；cohort -> 共学营 / 训练营；e-book -> 电子书；software -> 软件产品。
"""

LONG_CONTENT_BREAKDOWN_RULES = """长内容阶段拆分规则：
- 适用范围：视频时长超过 45 分钟，并且内容类型是播客、访谈、演讲、讲座、课程、公开课、研讨会、工作坊、培训或类似需要按阶段理解的长内容。
- 最终 normalized_report_payload 适用时必须输出 long_content_breakdown；Qwen local_extract 中间结果适用时输出 phase_outline，并且不得输出 long_content_breakdown。
- long_content_breakdown 是全片阶段拆分，不是补看入口，不得替代 watch_segments。
- long_content_breakdown / phase_outline 每项必须包含 start、end、title、summary；start/end 使用 MM:SS 或 H:MM:SS 原始时间码。
- 每个阶段应按时间顺序覆盖一个相对独立的讲解部分，summary 要具体说明这一部分讲了什么、展开了哪些对象/观点/方法/案例/边界。
- 最终报告不适用时不要输出 long_content_breakdown；中间提炼不适用时 phase_outline 输出空数组。
"""

READING_FLOW_RULES = """视频内容总结规则：
- 最终 normalized_report_payload 必须输出 reading_flow，作为读者默认看到的内容总结。先让读者知道视频具体讲了什么，再考虑评分、可信度或补看入口。
- reading_flow 是 2 到 5 个按实际内容组织的要点；每项只能有 start、end、heading、body 四个字段，时间码必须来自输入证据。时间码用于溯源，不要求把正文写成分幕故事。每项必须 start < end，后一项 start 必须大于或等于前一项 end，禁止重叠（包括一秒重叠）；选择输入证据支持的分界点，输出前逐项核对。
- heading 直接点明该段讲的对象或事件；body 写出具体信息：人物/产品/问题、发生的事或提出的做法、重要例子及结果。优先保留能区分这支视频与同主题其他视频的细节。
- 每个要点聚焦一件主要事情，用 1 到 3 句讲清楚；短视频优先保留最关键的经过和结果，删掉寒暄、重复和“视频先讲、接着讲”这类目录语言。
- 对讲解、访谈、评论，明确区分视频作者的主张与已证实的事实；对剧情、探访、记录类内容，交代主要经过和结果。不要把没有给出的方法改写成建议。
- 正文不写视频质量研判、论证打分、观看建议，也不强行套成“问题—机制—转折—落点”。不重复拼接 path_table、arrow_chain、target_sections 或 long_content_breakdown。
- 不得编造原文没有的观点、金句、建议或因果；不得写模板、卡片、报告、字段等生成过程语言。
- 长内容的 reading_flow 必须有 3 到 5 段，并从第一阶段覆盖到最后阶段，保留明显不同的次级主题，不只概括开头。
- target_summary 是开场的 1 到 2 句总览：直接说明视频的主题、核心内容和最终谈到的结果；不写“这支视频先……再……”式的目录，也不重复 reading_flow 的段落正文。
"""

ANALYZER_SYSTEM_PROMPT = """你是 WatchBefore 的视频内容总结编辑。

你的首要任务是依据原始语言转写证据和明确存在时的 MiMo-VL 画面证据，写出读者能直接了解视频内容的中文总结。你还需生成兼容评分和补看功能的 normalized_report_payload。

硬规则：
1. 最终报告必须是中文。
2. 不要整篇翻译后再分析；先理解原始语言内容，再输出中文总结。
3. replacement_score 只表示：视频内容与话题兴趣综合评分，来自信息密度、论据质量、话题兴趣三项，不是“看完报告后原视频还剩多少观看价值”，也不是替用户做观看决定。
4. topic 不能直接影响 replacement_score；只能根据视频内容证据、表达、论证和实际价值评分。
5. tag 只能从固定推荐/替代标签里选，不能写主题词。
6. one_line_brief 用于右侧“省流”：保留 schema 要求的“这期视频主要讲：”前缀，前缀后只用一句简短、直接的中文回答视频核心问题（例如 HALO 有什么用：保护车手）。不得拼接多句或用分号堆砌段落；不得写补看建议、评分理由。完整内容总览写在 target_summary。
7. watch_verdict 只说明报告覆盖的内容和对应原片时间点，不替读者判断原视频要不要看。
8. highest_compression 必须是具体核心洞察，不能压成空泛主题词。
9. final_conclusion 只回答“这期视频最终想让人记住什么”，不能写观看建议、评分理由、视频评价或局限。
10. content_caveat 专门承接局限、边界和论证弱点。
11. watch_segments 只能有一个 primary，最多一个 optional，backup 只有真实价值才输出。
12. only_one_segment 必须和 primary 的 start/end 完全一致。
13. 所有显示型时间段必须使用 `start | end`。
14. 不要重新引入“要点提炼 / 可执行动作清单 / 完整笔记”。
15. 最终报告必须以中文为主，领域术语要自然中文化；保留英文只限标题、频道名、品牌名、产品名、人名、工具名或确实无法自然翻译的原词。
16. 如果保留英文术语有必要，第一次写成“中文（English）”，后续只用中文。
17. 默认报告首先是视频内容总结：覆盖主要内容、具体细节和结果；评分、论证边界与补看入口是辅助信息，不得占据正文。
18. watch_segments 的 primary 必须选“理解入口片段”：读者看这一段后，应能知道视频主要对象是什么、它解决什么问题、通过什么路径/机制达到什么效果。不要只选信息密度最高但不能作为入口的细分段落；隐私、部署、限制、商业化等后段信息通常只能作为 optional/backup，除非它同时讲清对象、路径和效果。
19. watch_verdict 和 only_one_segment 只提供“补看入口”。可以给出片段入口，但不能把话说成系统替用户决定要不要看；如果使用“建议看”一类表述，语义只能是“如果用户要补看，建议从这段进入”，不是“你应该观看/不观看”。
20. 长播客、访谈、演讲、课程等超过 45 分钟的视频必须额外按阶段拆分到 long_content_breakdown。
21. visual_evidence 只证明抽帧中直接可见的主体、场景、屏幕操作、图表、字幕和动作；不得把画面观察写成“作者说过”或“已经听到”。转写与画面冲突时，语言内容以转写为准，动作、对象和界面状态以画面为准，并在 content_caveat 说明冲突。
22. reading_flow 是报告的主阅读正文。它必须由内容证据直接写成具体的内容总结，不能把已有字段改名、拼接或重新排版。
""" + "\n" + TERM_LOCALIZATION_RULES + "\n" + LONG_CONTENT_BREAKDOWN_RULES + "\n" + READING_FLOW_RULES


SCORING_RUBRIC = """replacement_score / structured_assessment 评分标尺：
- 这个分数由信息密度、论据质量、话题兴趣三项综合得出。
- 这个分数不是观看决定；系统不能替用户决定看不看。最终报告只负责把主要内容、关键信息和补看入口讲清楚，决策权留给用户。
- 最终 replacement_score 会由 structured_assessment 三项确定性加权计算；你必须认真校准三项，不要把所有普通视频都压到 0-3。
- 三项含义：
  1. 信息密度：单位时间内提供的有效观点、方法、例子、机制和可迁移判断的密度。
  2. 论据质量：也就是内容可信度，是否给出清楚证据、例子、推理链、反例、边界或可验证依据。
  3. 话题兴趣：用户是否对这一类内容主题感兴趣。没有 WatchBefore 内容兴趣画像时固定给 5.0，不根据作者、标题或热度猜。
- 0-10 锚点：
  - 0-2：视频整体价值很低；内容空泛、证据不足、重复严重，或转写/内容不足以支持判断。
  - 3-4：整体价值偏低；有少量信息，但多数是常识、铺垫、重复或表达价值弱。
  - 5-6：中等价值；有明确信息和部分例子，但证据或话题兴趣有限。
  - 7-8：高价值；信息、论证、案例密度或个人话题兴趣较强。
  - 9-10：极高价值；信息、证据和个人话题兴趣都很强。
- 校准规则：
  - 不要因为主题普通就自动低分；只看视频证据。
  - 不要因为报告写得清楚就压低分数；报告可替代性只能影响观看建议，不能决定视频整体价值分。
  - 如果 transcript 很短、只有导言、寒暄或纯导航，低分合理；如果有具体方法、案例、演示、访谈张力或强表达，不能给 0-2。
"""

NORMALIZED_PAYLOAD_CONTRACT = {
    "required_fields": list(REQUIRED_REPORT_FIELDS),
    "optional_fields": {
        "long_content_breakdown": "仅在视频超过 45 分钟且属于播客、访谈、演讲、课程或类似长内容时输出；数组项包含 start、end、title、summary。",
    },
    "tag_enum": list(VALID_TAGS),
    "path_table_keys": ["problem", "mechanism", "turning_point", "landing"],
    "score_basis_keys": ["information_density", "evidence_quality", "topic_interest"],
    "watch_segment_priorities": ["primary", "optional", "backup"],
    "structured_assessment_rubric": SCORING_RUBRIC,
}


QWEN_LOCAL_EXTRACT_FIELDS = {
    "cleaned_understanding": "对转写纠错后的具体内容总览，写清讲述对象、主要内容和结果；不要写视频质量研判。",
    "main_axis": "这段内容最核心想讲什么。",
    "core_claims": "作者真正反复表达的核心观点数组。",
    "conditions": "达成某状态或目标的前提条件数组；没有就空数组。",
    "methods": "作者提出的可执行做法数组；优先保留原文方法，再做必要归纳。",
    "examples": "作者用来说明观点的例子数组；不要把例子误当结论。",
    "caveats": "边界、限制、注意事项数组。",
    "original_quotes": "贴近原意的原话金句数组，可轻微纠错。",
    "refined_quotes": "基于原文主旨提炼出的表达数组，但不能偏离原意。",
    "transcript_quality_note": "说明 ASR 是否有明显错误，是否影响主旨判断。",
    "language": "zh / en / mixed / unknown。",
    "important_terms": "关键专名、产品名、人名、工具名数组，保留原文；普通领域术语不要裸露英文，优先写中文译名或 中文（English）一次。",
    "corrected_terms": "疑似 ASR 错误的专名纠错数组；每项写成 原词 -> 修正词；不确定时写 疑似某某。",
    "phase_outline": "如果视频超过 45 分钟且是播客、访谈、演讲、课程或类似长内容，输出阶段拆分数组；每项包含 start、end、title、summary。不适用时输出空数组。",
    "reading_flow": "按实际内容组织的 2 到 5 个中文要点；每项只含 start、end、heading、body。每个要点用 1 到 3 句写清对象、经过/主张、重要例子和结果；不写质量研判或观看建议。",
}


QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT = """你现在要充当“视频转写内容提炼编辑与画面证据编辑”。

你的输入必有一段视频或音频的转写文本，可能是中文、英文或中英混合；也可能附带经过哈希绑定的 MiMo-VL 全时轴抽帧证据。
转写文本可能存在 ASR 错误、断句不自然、同音错字、口语重复、赘述、语序混乱等问题。
你的任务是先理解，再把视频具体讲述的内容、过程、例子和结果准确提炼出来。

视觉证据规则：
- visual_evidence 只证明抽帧中直接可见的主体、场景、屏幕操作、图表、字幕和动作。
- 不得把画面观察写成视频音轨说过的话，不得根据画面补造声音、口播或因果关系。
- 转写与画面冲突时，语言内容以转写为准；动作、对象、界面和图表状态以画面为准，并把冲突写进 caveats。
- 没有 visual_evidence 时保持纯转写流程，不得猜测画面。

如果输入是英文：
- 直接阅读英文原文。
- 不要先整篇翻译。
- 输出中文结构化提炼。
- 标题、频道名、品牌名、产品名、人名、工具名可保留英文原文。
- 营销、产品、创作者领域的通用术语要自然中文化；必要时第一次写“中文（English）”，后续只用中文。

工作流程：
1. 语义纠错与还原
2. 实体名纠错：哲学、人名、书名、工具名等专名要统一；本类内容重点留意 叔本华 / Schopenhauer、尼采 / Nietzsche、柏拉图 / Plato、萨特 / Sartre、阿兰·德波顿 / Alain de Botton。
3. 区分内容类型：定义 / 观点 / 条件 / 方法 / 例子 / 提醒
4. 抽主轴
5. 输出中间 JSON

禁止：
- 逐字机械照搬
- 按时间顺序流水账复述
- 编造原文没有的观点
- 把例子误写成结论
- 输出空泛鸡汤
- 为了凑结构强行造条件或金句
- 把英文全文先翻译再分析
- 把不确定的人名硬写成确定名字；不确定时写“疑似某某”
- 输出 final_conclusion
- 输出 watch_verdict
- 输出 replacement_score
- 输出 long_content_breakdown
- 输出最终 HTML
""" + "\n" + TERM_LOCALIZATION_RULES + "\n" + LONG_CONTENT_BREAKDOWN_RULES + "\n" + READING_FLOW_RULES


def build_qwen_local_extract_user_prompt(local_extract_seed: dict[str, Any]) -> str:
    language = str(local_extract_seed.get("transcript", {}).get("language") or "unknown")
    language_note = ""
    if language == "en":
        language_note = (
            "\n英文 transcript 规则：Read the original English transcript directly. "
            "Do not first translate the whole transcript. Output the extracted analysis in Chinese. 输出中文结构化提炼。"
            "领域术语要自然中文化；保留英文只限标题、频道名、品牌名、产品名、人名、工具名或无法自然翻译的原词。"
            "必要时第一次写“中文（English）”，后续只用中文。\n"
        )
    elif language == "mixed":
        language_note = "\n中英混合 transcript 规则：直接阅读原始混合文本，输出中文结构化提炼；领域术语要自然中文化，保留英文只限专名或无法自然翻译的原词，必要时第一次写“中文（English）”。\n"
    return (
        "请根据下面的视频内容证据输出 Qwen local_extract intermediate JSON。\n"
        "只能输出 JSON 对象，不要 Markdown，不要解释。\n"
        "这是中间提炼结果，不是最终 WatchBrief V5 报告。\n"
        "不得输出 replacement_score、tag、watch_verdict、final_conclusion、watch_segments、long_content_breakdown 或 HTML。\n"
        "important_terms 必须保留关键专名；corrected_terms 必须记录疑似 ASR 专名纠错，没有就输出空数组。\n"
        "如果存在 visual_evidence，只能把其中直接可见的事实用于补充主体、动作、界面、图表和画面文字；不得把它改写成听到的口播。\n"
        f"{TERM_LOCALIZATION_RULES}\n"
        f"{language_note}\n"
        "必须包含这些字段：\n"
        f"{json.dumps(QWEN_LOCAL_EXTRACT_FIELDS, ensure_ascii=False, indent=2)}\n\n"
        "视频内容证据：\n"
        f"{json.dumps(local_extract_seed, ensure_ascii=False, indent=2)}"
    )


def build_target_report_prompt(report_target: str) -> str:
    target = normalize_report_target(report_target)
    if target == DEFAULT_REPORT_TARGET:
        contract = report_target_contract("content_brief")
        return (
            "\n\n默认 WatchBefore 模板阅读流契约：\n"
            f"{json.dumps(contract['target_sections'], ensure_ascii=False, indent=2)}\n"
            "默认报告仍然是 watch_decision，不要把 report_target 改成 content_brief。\n"
            "你必须额外输出 target_summary、reading_flow 和 target_sections。target_summary 是正文开场总览：直接说清视频具体讲了哪些内容和最终结果；长视频还要点出显著的次级主题。不得把“默认模板、内容卡片、报告、字段”等生成说明写进 target_summary。\n"
            "reading_flow 是页面的主正文，必须遵守视频内容总结规则：写具体内容、例子与结果，不写视频质量研判或观看决定。\n"
            "target_sections 只能使用 direct_statements、key_points、practical_takeaways、boundaries 这四个固定 key，顺序不能变；它们只做读后速查，不重复代替正文。\n"
            "每个 key 的值必须是中文字符串数组，1 到 6 条，必须来自视频内容证据；没有明确可照做内容时，写明“原文没有明确可直接采用的方法”，不得编造。\n"
        )
    contract = report_target_contract(target)
    return (
        "\n\nreport_target 目标模式补充契约：\n"
        f"{json.dumps(contract, ensure_ascii=False, indent=2)}\n"
        "你仍然必须输出 baseline normalized_report_payload 的全部 required_fields，用于稳定校验、评分和兼容旧报告链路。\n"
        f"同时必须输出 report_target = {target}、target_summary、reading_flow 和 target_sections。\n"
        "target_sections 只能使用上面列出的固定 key；每个 key 的值必须是中文字符串数组，1 到 6 条，不得编造原文没有的信息。\n"
        "非观看决策目标的最终页面会优先展示 target_summary 与 target_sections；watch_verdict/watch_segments 只作为兼容字段保留。\n"
    )


REVIEW_QWEN_EXTRACT_FIELDS = (
    "cleaned_understanding",
    "main_axis",
    "core_claims",
    "conditions",
    "methods",
    "examples",
    "caveats",
    "quotes",
    "original_quotes",
    "refined_quotes",
    "transcript_quality_note",
    "language",
    "important_terms",
    "corrected_terms",
    "phase_outline",
    "reading_flow",
    "_qwen_model",
)

REVIEW_TRANSCRIPT_SUMMARY_FIELDS = (
    "language",
    "quality",
    "source",
    "segment_count",
    "char_count",
    "has_timestamps",
    "video_duration_seconds",
    "first_start",
    "last_end",
    "covered_duration",
    "coverage_ratio",
    "plain_text_char_count",
)

REVIEW_CHUNKED_SUMMARY_FIELDS = (
    "chunk_count",
    "successful_chunk_count",
    "failed_chunk_count",
    "success_coverage",
    "min_success_coverage",
    "trigger_reason",
    "request_bytes",
    "total_chars",
)


def _compact_text(value: Any, *, limit: int = 1200) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"…[truncated {len(text) - limit} chars]"


def _compact_string_list(values: Any, *, item_limit: int = 24, text_limit: int = 800) -> list[Any]:
    if not isinstance(values, list):
        return []
    compacted: list[Any] = []
    for item in values[:item_limit]:
        if isinstance(item, str):
            compacted.append(_compact_text(item, limit=text_limit))
        else:
            compacted.append(item)
    if len(values) > item_limit:
        compacted.append(f"[truncated {len(values) - item_limit} items]")
    return compacted


def _compact_time_windows(windows: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    if not isinstance(windows, list):
        return []
    compacted: list[dict[str, Any]] = []
    for window in windows[:limit]:
        if not isinstance(window, dict):
            continue
        item: dict[str, Any] = {}
        for key in ("start", "end", "label", "title", "summary"):
            if key in window:
                item[key] = window[key]
        if "excerpt" in window:
            item["excerpt_preview"] = _compact_text(window.get("excerpt"), limit=500)
        compacted.append(item)
    if len(windows) > limit:
        compacted.append({"truncated_window_count": len(windows) - limit})
    return compacted


def _chunk_status_summary(chunks: Any) -> dict[str, int]:
    if not isinstance(chunks, list):
        return {}
    summary: dict[str, int] = {}
    for chunk in chunks:
        status = "unknown"
        if isinstance(chunk, dict):
            status = str(chunk.get("status") or "unknown")
        summary[status] = summary.get(status, 0) + 1
    return summary


def build_compact_review_payload(local_extract_payload: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded payload used by final review prompts.

    local_extract payloads may contain thousands of transcript segments and full
    chunk debug records. Final review only needs source metadata, transcript
    quality/count metadata, Qwen's reduced understanding, boundary metadata, and
    chunked extraction status. Raw segment/chunk arrays are intentionally omitted
    to keep Codex CLI prompts inside context limits.
    """
    if not isinstance(local_extract_payload, dict):
        return {}
    transcript = local_extract_payload.get("transcript") if isinstance(local_extract_payload.get("transcript"), dict) else {}
    compact_transcript = {key: transcript[key] for key in REVIEW_TRANSCRIPT_SUMMARY_FIELDS if key in transcript}
    if "excerpt" in transcript:
        compact_transcript["excerpt_preview"] = _compact_text(transcript.get("excerpt"), limit=1600)
    if "segments" in transcript:
        compact_transcript["segments_omitted"] = True
        if "segment_count" not in compact_transcript and isinstance(transcript.get("segments"), list):
            compact_transcript["segment_count"] = len(transcript["segments"])

    qwen_extract = local_extract_payload.get("qwen_extract") if isinstance(local_extract_payload.get("qwen_extract"), dict) else {}
    compact_qwen: dict[str, Any] = {}
    for key in REVIEW_QWEN_EXTRACT_FIELDS:
        if key not in qwen_extract:
            continue
        value = qwen_extract[key]
        if isinstance(value, str):
            compact_qwen[key] = _compact_text(value, limit=5000 if key == "cleaned_understanding" else 1800)
        elif isinstance(value, list):
            compact_qwen[key] = _compact_string_list(value)
        else:
            compact_qwen[key] = value

    chunked = local_extract_payload.get("chunked_local_extract") if isinstance(local_extract_payload.get("chunked_local_extract"), dict) else {}
    compact_chunked = {key: chunked[key] for key in REVIEW_CHUNKED_SUMMARY_FIELDS if key in chunked}
    if "reduce" in chunked and isinstance(chunked.get("reduce"), dict):
        compact_chunked["reduce"] = {
            key: value
            for key, value in chunked["reduce"].items()
            if key in {"status", "reason_code", "error"}
        }
    if "chunks" in chunked:
        compact_chunked["chunks_omitted"] = True
        compact_chunked["chunk_status_summary"] = _chunk_status_summary(chunked.get("chunks"))

    compact_payload: dict[str, Any] = {}
    for key in ("extract_version", "transcript_hash"):
        if key in local_extract_payload:
            compact_payload[key] = local_extract_payload[key]
    compact_payload["metadata"] = local_extract_payload.get("metadata", {}) if isinstance(local_extract_payload.get("metadata"), dict) else {}
    compact_payload["transcript"] = compact_transcript
    visual_evidence = (
        local_extract_payload.get("visual_evidence")
        if isinstance(local_extract_payload.get("visual_evidence"), dict)
        else {}
    )
    if visual_evidence:
        visual_analysis = (
            visual_evidence.get("analysis")
            if isinstance(visual_evidence.get("analysis"), dict)
            else {}
        )
        compact_payload["visual_evidence"] = {
            "visual_evidence_hash": str(visual_evidence.get("visual_evidence_hash") or ""),
            "evidence_version": str(visual_evidence.get("evidence_version") or ""),
            "prompt_version": str(visual_evidence.get("prompt_version") or ""),
            "provider": visual_evidence.get("provider", {}),
            "frame_count": len(visual_evidence.get("frames") or []),
            "sampling": visual_evidence.get("sampling", {}),
            "overall_visual_summary": _compact_text(
                visual_analysis.get("overall_visual_summary"),
                limit=2400,
            ),
            "visual_caveats": _compact_string_list(
                visual_analysis.get("visual_caveats"),
                item_limit=24,
                text_limit=800,
            ),
        }
    if local_extract_payload.get("time_windows"):
        compact_payload["time_windows"] = _compact_time_windows(local_extract_payload.get("time_windows"))
    try:
        candidates = build_watch_segments(
            local_extract_payload,
            qwen_extract,
            compact_payload["metadata"] if isinstance(compact_payload["metadata"], dict) else {},
        )
    except Exception:
        candidates = []
    if candidates:
        compact_payload["watch_segment_candidates"] = candidates
    compact_payload["qwen_extract"] = compact_qwen
    compact_payload["analysis_boundary"] = local_extract_payload.get("analysis_boundary", {}) if isinstance(local_extract_payload.get("analysis_boundary"), dict) else {}
    if compact_chunked:
        compact_payload["chunked_local_extract"] = compact_chunked
    return compact_payload


def build_review_user_prompt(local_extract_payload: dict[str, Any], *, report_target: str = DEFAULT_REPORT_TARGET) -> str:
    """Build the user prompt for a future model call without executing it."""
    review_payload = build_compact_review_payload(local_extract_payload)
    prompt = (
        "请根据下面的 compact local_extract_payload 生成一个 normalized_report_payload。\n"
        "输入已为 review 阶段压缩：完整 transcript.segments、超长 transcript.excerpt、chunked_local_extract.chunks 等原文/调试大数组已省略；请基于 metadata、transcript summary、qwen_extract 与 chunked status 进行最终判断，不要要求完整原文。\n"
        "输入里可能包含 qwen_extract，这是本地 Qwen 对转写与可选画面证据的中间提炼；你可以参考其中 core_claims、methods、caveats、quotes、important_terms、corrected_terms、phase_outline、reading_flow，但最终字段仍必须遵守 V5 schema 和 validator。\n"
        "如果 compact payload 含 visual_evidence，它只能证明抽帧中直接可见的主体、动作、界面、图表和画面文字；不得写成已经听到的口播。转写与画面冲突时按证据类型分别处理并说明边界。\n"
        "默认报告视角：你不是替用户决定原视频要不要看，而是提炼出用户判断前最需要知道的信息。先让读者明白视频主要讲什么、核心逻辑是什么、哪些信息足以支撑自己的判断；补看建议只是入口导航。\n"
        "watch_verdict 写法：必须是内容导读和入口导航。可以给出补看片段，但不要替用户决定要不要看；如果使用“建议看”一类表述，必须限定为“如果用户要补看，建议从这段进入”。推荐写法：报告已提炼出产品对象、核心路径和边界；如果用户要补看原片，入口是 11:28 | 14:03。\n"
        "如果输入里有 watch_segment_candidates，它们是从完整转写中按“理解入口、对象定义、核心路径、机制解释、效果说明、边界补充”等信号预筛出的候选片段；watch_segments 应优先从这些候选里选择，不要因为 transcript excerpt 从 00:00 开始就默认选择开头。\n"
        "片段选择规则：primary 必须是回答“看哪一段最容易知道这个视频主要讲的是什么”所需的最短充分片段。起点放在对象、问题或核心机制开始被实质解释的位置，终点放在理解核心结论所需的最后一个关键信息结束处；不得为了上下文完整而包含可删除的钩子、寒暄、重复案例或收尾。产品/技术/原理/观点类视频优先选能同时覆盖对象/产品是什么、它解决什么问题、通过什么路径或机制产生什么效果的段落。optional 只在它提供 primary 没有的关键证据或边界时输出，backup 只在 primary 存在真实替代入口时输出。\n"
        f"{LONG_CONTENT_BREAKDOWN_RULES}\n"
        f"{READING_FLOW_RULES}\n"
        "专名、人名、书名和工具名必须优先使用 qwen_extract.important_terms 与 qwen_extract.corrected_terms；不要自己重新猜人名。不确定就写“疑似某某”。\n"
        f"{TERM_LOCALIZATION_RULES}\n"
        "中文字段包括 topic、one_line_brief、watch_verdict、highest_compression、path_table、arrow_chain、final_conclusion、content_caveat、reading_flow、watch_segments、long_content_breakdown、score_basis、confidence_note，都必须优先使用自然中文表达。\n"
        "英文视频标题、频道名、品牌名、产品名可按原文保留；普通营销 / 产品 / 创作者术语不得成串裸露英文。\n"
        "只能输出 JSON 对象，不要输出 Markdown，不要添加解释。\n\n"
        "normalized_report_payload 契约：\n"
        f"{json.dumps(NORMALIZED_PAYLOAD_CONTRACT, ensure_ascii=False, indent=2)}\n\n"
        "compact_local_extract_payload：\n"
        f"{json.dumps(review_payload, ensure_ascii=False, indent=2)}"
    )
    return prompt + build_target_report_prompt(report_target)
