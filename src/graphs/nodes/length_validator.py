"""字数预检与智能截断模块 - P1 优化

根据竞品分析（Autoposting.ai/Blabla.ai），实现：
1. 生成前字数限制（通过 prompt 明确告知 LLM）
2. 生成后智能截断（保留关键点 + CTA）

目标：超长率从 40% 降到 <5%
"""
from __future__ import annotations

import re
from typing import Tuple


# 平台字数限制
PLATFORM_LIMITS = {
    "xiaohongshu": {"min": 120, "max": 450, "ideal": 350},
    "weibo": {"min": 50, "max": 140, "ideal": 100},
    "twitter": {"min": 50, "max": 280, "ideal": 200},
    "x": {"min": 50, "max": 380, "ideal": 260},
}


def validate_length(text: str, platform: str = "x") -> Tuple[bool, str, int]:
    """验证文本长度是否符合平台要求

    Args:
        text: 待验证文本
        platform: 平台标识 ("x", "xiaohongshu", "weibo")

    Returns:
        (是否通过, 原因, 实际字数)
    """
    if not text:
        return False, "文本为空", 0

    actual_len = len(text)
    limits = PLATFORM_LIMITS.get(platform, PLATFORM_LIMITS["x"])

    if actual_len < limits["min"]:
        return False, f"{platform} 内容过短 ({actual_len} < {limits['min']} 字)", actual_len

    if actual_len > limits["max"]:
        return False, f"{platform} 内容超长 ({actual_len} > {limits['max']} 字)", actual_len

    return True, "ok", actual_len


def smart_truncate(text: str, max_length: int, preserve_structure: bool = True) -> str:
    """智能截断 - 优先在段落/句子边界截断

    Args:
        text: 原始文本
        max_length: 最大字数
        preserve_structure: 是否保留结构（段落/句子完整性）

    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text

    if not preserve_structure:
        # 硬截断
        return text[:max_length].rstrip() + "..."

    # 优先级 1：段落边界（\n\n）
    if "\n\n" in text:
        paragraphs = text.split("\n\n")
        result = []
        current_len = 0

        for para in paragraphs:
            if current_len + len(para) <= max_length * 0.9:  # 保留 10% 缓冲
                result.append(para)
                current_len += len(para) + 2  # +2 for \n\n
            else:
                break

        if result and current_len > max_length * 0.7:  # 至少保留 70%
            return "\n\n".join(result)

    # 优先级 2：句子边界（。！？）
    sentences = re.split(r'([。！？\n])', text)
    result = []
    current_len = 0

    for i in range(0, len(sentences), 2):
        if i + 1 < len(sentences):
            sentence = sentences[i] + sentences[i + 1]
        else:
            sentence = sentences[i]

        if current_len + len(sentence) <= max_length * 0.9:
            result.append(sentence)
            current_len += len(sentence)
        else:
            break

    if result and current_len > max_length * 0.7:
        return ''.join(result)

    # 优先级 3：硬截断（保底）
    return text[:max_length].rstrip() + "..."


def generate_with_length_constraint(
    platform: str,
    material_text: str,
    current_draft: str = ""
) -> dict:
    """生成带字数约束的 prompt 指导

    Args:
        platform: 目标平台
        material_text: 素材文本
        current_draft: 当前草稿（如果是修复阶段）

    Returns:
        包含 prompt 提示和元数据的字典
    """
    limits = PLATFORM_LIMITS.get(platform, PLATFORM_LIMITS["x"])

    prompt_hint = (
        f"请生成 {platform} 内容，严格控制在 {limits['min']}-{limits['max']} 字。"
        f"目标长度 {limits['ideal']} 字左右最佳。"
        f"生成时逐句累计字数，接近目标立即收尾。"
    )

    if current_draft:
        current_len = len(current_draft)
        if current_len > limits['max']:
            prompt_hint = (
                f"上一版 {current_len} 字超过 {limits['max']} 字上限。"
                f"请删减到 {limits['ideal']} 字以内，保留核心事实、判断和可执行项。"
                f"删减优先级：冗余形容词 > 相似句合并 > 保留要点。"
            )

    return {
        "prompt_hint": prompt_hint,
        "target_length": limits['ideal'],
        "min_length": limits['min'],
        "max_length": limits['max'],
        "platform": platform
    }


def post_generation_check(draft: dict, strict: bool = True) -> Tuple[dict, list]:
    """生成后字数检查与自动修复

    Args:
        draft: 包含 tweet_content, other_content 等字段的草稿
        strict: 严格模式（超长直接截断）

    Returns:
        (修复后的 draft, 修复日志列表)
    """
    fixes = []

    # X 内容检查
    tweet = draft.get("tweet_content", "")
    if tweet:
        ok, reason, actual_len = validate_length(tweet, "x")
        if not ok:
            if strict and actual_len > PLATFORM_LIMITS["x"]["max"]:
                draft["tweet_content"] = smart_truncate(tweet, PLATFORM_LIMITS["x"]["max"])
                fixes.append(f"X 内容超长 {actual_len} 字，已截断到 {len(draft['tweet_content'])} 字")
            else:
                fixes.append(f"X 内容长度异常: {reason}")

    # 小红书内容检查
    xhs_content = draft.get("other_content", "")
    if xhs_content:
        ok, reason, actual_len = validate_length(xhs_content, "xiaohongshu")
        if not ok:
            if strict and actual_len > PLATFORM_LIMITS["xiaohongshu"]["max"]:
                draft["other_content"] = smart_truncate(xhs_content, PLATFORM_LIMITS["xiaohongshu"]["max"])
                fixes.append(f"小红书内容超长 {actual_len} 字，已截断到 {len(draft['other_content'])} 字")
            else:
                fixes.append(f"小红书内容长度异常: {reason}")

    return draft, fixes
