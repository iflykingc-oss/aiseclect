"""句式节奏变化模块 - 多层人性化管道的第一层

基于 2026 年 AI 人性化最佳实践：
- 变化句子长度（混合短句 5-15 字 + 长句 20-30 字）
- 避免重复开头模式
- 增加词汇多样性
- 平台特定语气适配（小红书 vs 微博）

目标：降低 AI 检测率 70%，提升互动率 30-50%
"""
from __future__ import annotations

import random
import re
from typing import List, Tuple


def vary_sentence_rhythm(text: str, target_platform: str = "general") -> str:
    """句式节奏变化 - 避免机械重复模式

    Args:
        text: 原始文本
        target_platform: 目标平台 ("xiaohongshu", "weibo", "general")

    Returns:
        调整节奏后的文本
    """
    if not text or len(text.strip()) < 20:
        return text

    # 按句子切分（保留标点）
    sentences = re.split(r'([。！？\n]+)', text)
    processed = []

    for i, segment in enumerate(sentences):
        if not segment.strip():
            processed.append(segment)
            continue

        # 标点符号直接保留
        if segment in ('。', '！', '？', '\n', '！！', '？？'):
            processed.append(segment)
            continue

        # 句子长度分析
        sentence_len = len(segment)

        # 过长句子（>35字）拆分
        if sentence_len > 35 and '，' in segment:
            parts = segment.split('，')
            if len(parts) >= 2:
                # 随机插入短句分隔
                mid = len(parts) // 2
                segment = '，'.join(parts[:mid]) + '。' + '，'.join(parts[mid:])

        # 过短连续句子（<8字）合并
        if i > 0 and sentence_len < 8 and len(processed) > 1:
            prev = processed[-2] if len(processed) >= 2 else ""
            if isinstance(prev, str) and len(prev) < 8 and prev not in ('。', '！', '？', '\n'):
                # 合并到前一句
                processed[-2] = prev + '，' + segment
                continue

        processed.append(segment)

    return ''.join(processed)


def add_platform_voice(text: str, platform: str) -> str:
    """平台特定语气适配

    Args:
        text: 原始文本
        platform: "xiaohongshu" | "weibo" | "general"

    Returns:
        适配平台语气的文本
    """
    if platform == "xiaohongshu":
        return _add_xiaohongshu_voice(text)
    elif platform == "weibo":
        return _add_weibo_voice(text)
    else:
        return text


def _add_xiaohongshu_voice(text: str) -> str:
    """小红书语气适配

    特征：
    - 开头可用：姐妹们！今天发现... / 分享一个... / 记录一下...
    - emoji 策略性使用（不是每句结尾）
    - 1-2 句为一段（避免大段落）
    """
    lines = text.split('\n')
    processed_lines = []

    for i, line in enumerate(lines):
        if not line.strip():
            processed_lines.append(line)
            continue

        # 第一行：避免"我"开头，改为更自然的引入
        if i == 0 and line.startswith('我'):
            # "我发现" → "今天发现"
            line = re.sub(r'^我发现', '今天发现', line)
            line = re.sub(r'^我觉得', '感觉', line)
            line = re.sub(r'^我认为', '个人觉得', line)

        # 拆分长段落（>80字）
        if len(line) > 80 and '。' in line:
            sentences = line.split('。')
            # 每 1-2 句一段
            chunks = []
            for j in range(0, len(sentences), 2):
                chunk = '。'.join(sentences[j:j+2])
                if chunk.strip():
                    chunks.append(chunk + ('。' if not chunk.endswith('。') else ''))
            processed_lines.extend(chunks)
        else:
            processed_lines.append(line)

    return '\n'.join(processed_lines)


def _add_weibo_voice(text: str) -> str:
    """微博语气适配

    特征：
    - 短小精悍（50-120 字理想）
    - 话题标签放结尾
    - 疑问句钩子优先
    """
    # 微博限制：保持简短
    if len(text) > 140:
        # 截取前 120 字 + "..."
        sentences = re.split(r'[。！？]', text)
        result = []
        current_len = 0
        for s in sentences:
            if current_len + len(s) > 120:
                break
            result.append(s)
            current_len += len(s)
        text = '。'.join(result) + '...'

    # 移除多余换行（微博单段）
    text = text.replace('\n\n', '\n').replace('\n', ' ')

    return text


def humanize_rhythm(text: str, platform: str = "general") -> Tuple[str, dict]:
    """综合人性化处理

    Args:
        text: 原始文本
        platform: 目标平台

    Returns:
        (处理后文本, 元数据字典)
    """
    original_len = len(text)

    # 第一步：节奏变化
    text = vary_sentence_rhythm(text, platform)

    # 第二步：平台语气
    text = add_platform_voice(text, platform)

    metadata = {
        "original_length": original_len,
        "processed_length": len(text),
        "platform": platform,
        "rhythm_adjusted": True
    }

    return text, metadata
