"""
Skill 草稿质量评分模块 (P2-1)

评估维度：
1. 完整性 (completeness) - 步骤数量、长度、关键词丰富度
2. 复用性 (reusability) - 关键词和步骤的通用程度
3. 成功信号 (success_signal) - 工具调用成功率、对话状态
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


# 一次性/具体词汇，降低复用性得分
_ONE_TIME_KEYWORDS = {
    "今天", "昨天", "明天", "刚才", "现在", "当前",
    "test", "temp", "tmp", "debug", "试试", "测试一下",
}


@dataclass
class SkillScore:
    """评分结果"""
    completeness: float = 0.0    # 0-100
    reusability: float = 0.0     # 0-100
    success_signal: float = 0.0  # 0-100
    total: float = 0.0           # 加权总分 0-100
    reasons: list[str] = None    # 扣分/加分原因

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


def _count_steps(steps: list[str]) -> int:
    """统计有效步骤数"""
    return len([s for s in steps if s.strip()])


def _avg_step_length(steps: list[str]) -> float:
    """步骤平均字符数"""
    valid = [s for s in steps if s.strip()]
    if not valid:
        return 0.0
    return sum(len(s) for s in valid) / len(valid)


def _keyword_genericity(keywords: list[str]) -> float:
    """关键词通用性评分 0-100

    扣分因素：
    - 包含数字/日期
    - 包含文件路径
    - 包含一次性词汇
    - 长度太长（过于具体）
    """
    if not keywords:
        return 0.0

    score = 100.0
    penalties = []

    for kw in keywords:
        kw_lower = kw.lower().strip()

        # 包含数字
        if re.search(r"\d{2,}", kw_lower):
            score -= 8
            penalties.append(f"'{kw}'含数字")

        # 包含路径
        if "/" in kw_lower or "\\" in kw_lower:
            score -= 10
            penalties.append(f"'{kw}'含路径")

        # 一次性词汇
        if kw_lower in _ONE_TIME_KEYWORDS:
            score -= 12
            penalties.append(f"'{kw}'为临时性词汇")

        # 过长
        if len(kw) > 15:
            score -= 5
            penalties.append(f"'{kw}'过长")

    return max(0.0, min(100.0, score)), penalties


def _step_genericity(steps: list[str]) -> float:
    """步骤通用性评分 0-100

    扣分因素：
    - 包含具体文件名/路径
    - 包含具体数值
    - 步骤过于简短（<10字符）
    """
    if not steps:
        return 0.0

    score = 100.0
    penalties = []

    for step in steps:
        step_lower = step.lower().strip()
        if not step_lower:
            continue

        # 包含具体路径
        if re.search(r"[a-z]:\\|/[a-z]+/", step_lower):
            score -= 8
            penalties.append(f"步骤含路径")

        # 包含具体数字ID
        if re.search(r"\b\d{6,}\b", step_lower):
            score -= 5
            penalties.append(f"步骤含长数字")

        # 过于简短
        if len(step.strip()) < 10:
            score -= 6
            penalties.append(f"步骤过短")

    return max(0.0, min(100.0, score)), penalties


def score_skill_draft(
    draft: dict[str, Any],
    tool_calls: list[dict[str, Any]] = None,
    tool_results: list[dict[str, Any]] = None,
    assistant_text: str = "",
    conversation_ended_normally: bool = True,
) -> SkillScore:
    """
    评估 skill 草稿质量。

    Args:
        draft: 草稿数据，包含 name, intent_pattern, steps, source_prompt
        tool_calls: 对话中的工具调用列表
        tool_results: 对话中的工具结果列表
        assistant_text: 助手最终回复文本
        conversation_ended_normally: 对话是否正常结束

    Returns:
        SkillScore 评分结果
    """
    reasons = []
    steps = draft.get("steps", [])
    keywords = draft.get("intent_pattern", [])

    # === 完整性评分 ===
    completeness = 0.0

    # 步骤数量 (40分)
    step_count = _count_steps(steps)
    if step_count >= 5:
        completeness += 40
    elif step_count >= 3:
        completeness += 30
    elif step_count >= 1:
        completeness += 15
    else:
        reasons.append("无有效步骤")

    # 步骤平均长度 (30分)
    avg_len = _avg_step_length(steps)
    if avg_len >= 40:
        completeness += 30
    elif avg_len >= 20:
        completeness += 20
    elif avg_len >= 10:
        completeness += 10
    else:
        reasons.append("步骤描述过短")

    # 关键词丰富度 (30分)
    kw_count = len(keywords)
    if kw_count >= 5:
        completeness += 30
    elif kw_count >= 3:
        completeness += 22
    elif kw_count >= 1:
        completeness += 12
    else:
        reasons.append("无关键词")

    # === 复用性评分 ===
    reusability = 0.0

    # 关键词通用性 (50分)
    kw_score, kw_penalties = _keyword_genericity(keywords)
    reusability += kw_score * 0.5
    reasons.extend(kw_penalties[:3])  # 最多记录3个

    # 步骤通用性 (50分)
    step_score, step_penalties = _step_genericity(steps)
    reusability += step_score * 0.5
    reasons.extend(step_penalties[:3])

    # === 成功信号评分 ===
    success_signal = 50.0  # 基准分

    # 工具调用成功率 (30分)
    if tool_calls and tool_results:
        total_calls = len(tool_calls)
        success_calls = sum(
            1 for r in tool_results
            if not r.get("error") and r.get("result")
        )
        if total_calls > 0:
            success_rate = success_calls / total_calls
            success_signal += success_rate * 30
            if success_rate < 0.5:
                reasons.append(f"工具成功率低({success_rate:.0%})")
    else:
        success_signal += 15  # 无工具调用，给中等分

    # 对话正常结束 (20分)
    if conversation_ended_normally:
        success_signal += 20
    else:
        success_signal -= 10
        reasons.append("对话未正常结束")

    # 回复质量 (简单检查)
    if assistant_text and len(assistant_text.strip()) > 50:
        success_signal += 10
    elif not assistant_text.strip():
        success_signal -= 5
        reasons.append("无有效回复")

    # 限制范围
    completeness = max(0.0, min(100.0, completeness))
    reusability = max(0.0, min(100.0, reusability))
    success_signal = max(0.0, min(100.0, success_signal))

    # 加权总分
    total = completeness * 0.4 + reusability * 0.35 + success_signal * 0.25

    return SkillScore(
        completeness=round(completeness, 1),
        reusability=round(reusability, 1),
        success_signal=round(success_signal, 1),
        total=round(total, 1),
        reasons=reasons,
    )


def is_worth_saving(score: SkillScore, threshold: float = 60.0) -> bool:
    """判断草稿是否值得保存"""
    return score.total >= threshold


def get_score_label(score: float) -> str:
    """获取分数等级标签"""
    if score >= 85:
        return "优秀"
    elif score >= 70:
        return "良好"
    elif score >= 55:
        return "一般"
    elif score >= 40:
        return "较差"
    else:
        return "不合格"
