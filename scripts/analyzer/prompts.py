"""Prompt text for WatchBrief V5 analyzer review.

This module only stores and assembles prompt text. It does not call models.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    from ..report_targets import DEFAULT_REPORT_TARGET, normalize_report_target, report_target_contract
    from ..validator import REQUIRED_REPORT_FIELDS, VALID_TAGS
except ImportError:  # pragma: no cover - direct script execution
    SCRIPTS_DIR = Path(__file__).resolve().parents[1]
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from report_targets import DEFAULT_REPORT_TARGET, normalize_report_target, report_target_contract
    from validator import REQUIRED_REPORT_FIELDS, VALID_TAGS


WATCHBRIEF_VERSION = "watchbrief_v5"
QWEN_LOCAL_EXTRACT_PROMPT_VERSION = "watchbrief_v5.qwen_local_extract_prompt.v2"

TERM_LOCALIZATION_RULES = """中文化与专有名词规则：
- 最终报告必须以中文为主，不能大量裸露英文领域术语。
- 英文视频标题、频道名、品牌名、产品名、人名、工具名不要硬翻译。
- 领域术语要自然中文化，营销、产品、创作者领域的通用术语要尽量译成自然中文。
- 如果保留某个英文词确实有助于识别概念，第一次出现写“中文（English）”，后续只用中文。
- 只把无法自然翻译的原始专名放进 important_terms；不要把普通领域术语当成必须裸露的英文专名。
- 示例：personal system product -> 个人方法系统产品；education product -> 教育型产品 / 知识产品；big burning problem -> 强痛点 / 核心痛点；desired outcome -> 目标结果；time frame -> 实现周期；offer -> 产品承诺 / 销售主张；landing page -> 落地页；social proof -> 信任背书 / 社会证明；CTA -> 行动号召 / 行动按钮；features and benefits -> 功能与收益；cohort -> 共学营 / 训练营；e-book -> 电子书；software -> 软件产品。
"""

ANALYZER_SYSTEM_PROMPT = """你是 WatchBrief V5 的视频观看决策分析器。

你的任务不是普通摘要，也不是下载器。你只能基于输入里的原始语言转写证据，生成一个 normalized_report_payload。

硬规则：
1. 最终报告必须是中文。
2. 不要整篇翻译后再分析；先理解原始语言内容，再输出中文判断。
3. replacement_score 只表示：这个视频本身的整体价值评分，不是“看完报告后原视频还剩多少观看价值”。
4. topic 不能直接影响 replacement_score；只能根据视频内容证据、表达、论证和实际价值评分。
5. tag 只能从固定推荐/替代标签里选，不能写主题词。
6. one_line_brief 只讲视频主要讲了什么，必须以“这期视频主要讲：”开头。
7. watch_verdict 只讲报告够不够、原视频要不要看、如果看，看哪段。
8. highest_compression 必须是具体核心洞察，不能压成空泛主题词。
9. final_conclusion 只回答“这期视频最终想让人记住什么”，不能写观看建议、评分理由、视频评价或局限。
10. content_caveat 专门承接局限、边界和论证弱点。
11. watch_segments 只能有一个 primary，最多一个 optional，backup 只有真实价值才输出。
12. only_one_segment 必须和 primary 的 start/end 完全一致。
13. 所有显示型时间段必须使用 `start | end`。
14. 不要重新引入“要点提炼 / 可执行动作清单 / 完整笔记”。
15. 最终报告必须以中文为主，领域术语要自然中文化；保留英文只限标题、频道名、品牌名、产品名、人名、工具名或确实无法自然翻译的原词。
16. 如果保留英文术语有必要，第一次写成“中文（English）”，后续只用中文。
""" + "\n" + TERM_LOCALIZATION_RULES


SCORING_RUBRIC = """replacement_score / structured_assessment 评分标尺：
- 这个分数是“视频整体价值评分”，不是“读完报告后原视频还剩多少观看价值”。
- 最终 replacement_score 会由 structured_assessment 四项确定性加权计算；你必须认真校准四项，不要把所有普通视频都压到 0-3。
- 四项含义：
  1. 信息密度：原视频单位时间内提供的有效观点、方法、例子、机制和可迁移判断的密度。
  2. 论据质量：原视频是否给出清楚证据、例子、推理链、反例、边界或可验证依据。
  3. 独创性：原视频是否有非模板化的新角度、新组合、少见经验、具体框架或独特表达；不是“世界首创”才给高分。
  4. 观看性价比：原视频作为音视频作品本身的表达/观看/聆听价值；包括表达感染力、叙事节奏、演示、案例细节、上下文、情绪张力、视觉/操作过程、声音质感和原作者口吻。
- 0-10 锚点：
  - 0-2：视频整体价值很低；内容空泛、证据不足、重复严重，或转写/内容不足以支持判断。
  - 3-4：整体价值偏低；有少量信息，但多数是常识、铺垫、重复或表达价值弱。
  - 5-6：中等价值；有明确信息和部分例子/表达亮点，但独创性、证据或观看体验有限。
  - 7-8：高价值；观点、论证、案例密度或表达体验较强，值得认真看/听关键段。
  - 9-10：极高价值；内容、证据、独创性和表达体验都很强，值得完整看/听。
- 校准规则：
  - 不要因为主题普通就自动低分；只看视频证据。
  - 不要因为报告写得清楚就压低分数；报告可替代性只能影响观看建议，不能决定视频整体价值分。
  - 如果 transcript 很短、只有导言、寒暄或纯导航，低分合理；如果有具体方法、案例、演示、访谈张力或强表达，不能给 0-2。
"""

NORMALIZED_PAYLOAD_CONTRACT = {
    "required_fields": list(REQUIRED_REPORT_FIELDS),
    "tag_enum": list(VALID_TAGS),
    "path_table_keys": ["problem", "mechanism", "turning_point", "landing"],
    "score_basis_keys": ["information_density", "evidence_quality", "originality", "watch_value"],
    "watch_segment_priorities": ["primary", "optional", "backup"],
    "structured_assessment_rubric": SCORING_RUBRIC,
}


QWEN_LOCAL_EXTRACT_FIELDS = {
    "cleaned_understanding": "对转写文本语义纠错后的理解摘要。",
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
}


QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT = """你现在要充当“视频转写内容提炼编辑”。

你的输入是一段视频或音频的转写文本，可能是中文、英文或中英混合。
转写文本可能存在 ASR 错误、断句不自然、同音错字、口语重复、赘述、语序混乱等问题。
你的任务不是机械复述，而是先理解，再提炼，再结构化表达。

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
- 输出最终 HTML
""" + "\n" + TERM_LOCALIZATION_RULES


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
        "请根据下面的转写材料输出 Qwen local_extract intermediate JSON。\n"
        "只能输出 JSON 对象，不要 Markdown，不要解释。\n"
        "这是中间提炼结果，不是最终 WatchBrief V5 报告。\n"
        "不得输出 replacement_score、tag、watch_verdict、final_conclusion、watch_segments 或 HTML。\n"
        "important_terms 必须保留关键专名；corrected_terms 必须记录疑似 ASR 专名纠错，没有就输出空数组。\n"
        f"{TERM_LOCALIZATION_RULES}\n"
        f"{language_note}\n"
        "必须包含这些字段：\n"
        f"{json.dumps(QWEN_LOCAL_EXTRACT_FIELDS, ensure_ascii=False, indent=2)}\n\n"
        "转写材料：\n"
        f"{json.dumps(local_extract_seed, ensure_ascii=False, indent=2)}"
    )


def build_target_report_prompt(report_target: str) -> str:
    target = normalize_report_target(report_target)
    if target == DEFAULT_REPORT_TARGET:
        return ""
    contract = report_target_contract(target)
    return (
        "\n\nreport_target 目标模式补充契约：\n"
        f"{json.dumps(contract, ensure_ascii=False, indent=2)}\n"
        "你仍然必须输出 baseline normalized_report_payload 的全部 required_fields，用于稳定校验、评分和兼容旧报告链路。\n"
        f"同时必须输出 report_target = {target}、target_summary 和 target_sections。\n"
        "target_sections 只能使用上面列出的固定 key；每个 key 的值必须是中文字符串数组，1 到 6 条，不得编造原文没有的信息。\n"
        "非观看决策目标的最终页面会优先展示 target_summary 与 target_sections；watch_verdict/watch_segments 只作为兼容字段保留。\n"
    )


def build_review_user_prompt(local_extract_payload: dict[str, Any], *, report_target: str = DEFAULT_REPORT_TARGET) -> str:
    """Build the user prompt for a future model call without executing it."""
    prompt = (
        "请根据下面的 local_extract_payload 生成一个 normalized_report_payload。\n"
        "输入里可能包含 qwen_extract，这是本地 Qwen 对转写内容的中间提炼；你可以参考其中 core_claims、methods、caveats、quotes、important_terms、corrected_terms，但最终字段仍必须遵守 V5 schema 和 validator。\n"
        "专名、人名、书名和工具名必须优先使用 qwen_extract.important_terms 与 qwen_extract.corrected_terms；不要自己重新猜人名。不确定就写“疑似某某”。\n"
        f"{TERM_LOCALIZATION_RULES}\n"
        "中文字段包括 topic、one_line_brief、watch_verdict、highest_compression、path_table、arrow_chain、final_conclusion、content_caveat、watch_segments、score_basis、confidence_note，都必须优先使用自然中文表达。\n"
        "英文视频标题、频道名、品牌名、产品名可按原文保留；普通营销 / 产品 / 创作者术语不得成串裸露英文。\n"
        "只能输出 JSON 对象，不要输出 Markdown，不要添加解释。\n\n"
        "normalized_report_payload 契约：\n"
        f"{json.dumps(NORMALIZED_PAYLOAD_CONTRACT, ensure_ascii=False, indent=2)}\n\n"
        "local_extract_payload：\n"
        f"{json.dumps(local_extract_payload, ensure_ascii=False, indent=2)}"
    )
    return prompt + build_target_report_prompt(report_target)
