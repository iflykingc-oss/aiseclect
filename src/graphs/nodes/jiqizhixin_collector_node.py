"""机器之心数据采集节点

采集来源: https://www.jiqizhixin.com/
内容类型: AI 模型、研究、应用、产业新闻
预期产出: 10-15 条/日高质量中文 AI 内容
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List

import feedparser
import requests
from bs4 import BeautifulSoup

from graphs.state import StandardMaterial

logger = logging.getLogger(__name__)

# 机器之心 RSS Feed（备选方案：API 或网页抓取）
JIQIZHIXIN_RSS = "https://www.jiqizhixin.com/rss"
JIQIZHIXIN_API = "https://www.jiqizhixin.com/api/articles"  # 备选
JIQIZHIXIN_WEB = "https://www.jiqizhixin.com/"  # 网页抓取备选

# AI 关键词过滤（继承自 NewsNow）
AI_KEYWORDS = (
    "ai", "大模型", "llm", "gpt", "chatgpt", "claude", "gemini", "智能",
    "machine learning", "deep learning", "神经网络", "transformer",
    "agent", "智能体", "prompt", "微调", "训练", "推理",
    "sora", "midjourney", "stable diffusion", "生成式", "aigc",
    "openai", "anthropic", "google ai", "deepmind", "百度", "阿里",
    "腾讯", "字节", "智谱", "月之暗面", "minimax",
)


def _match_ai_keywords(text: str) -> bool:
    """检查文本是否包含 AI 关键词"""
    if not text:
        return False
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in AI_KEYWORDS)


def jiqizhixin_collector_node(state=None) -> List[StandardMaterial]:
    """机器之心采集器

    Returns:
        List[StandardMaterial]: 采集到的素材列表
    """
    materials = []

    try:
        logger.info("开始采集机器之心 RSS...")

        # 获取 RSS feed
        feed = feedparser.parse(JIQIZHIXIN_RSS)

        if not feed.entries:
            logger.warning("机器之心 RSS 返回空结果")
            return materials

        for entry in feed.entries[:20]:  # 限制最多 20 条
            try:
                title = entry.get("title", "").strip()
                url = entry.get("link", "").strip()
                summary = entry.get("summary", "").strip()

                # 清理 HTML 标签
                if summary:
                    soup = BeautifulSoup(summary, "html.parser")
                    summary = soup.get_text().strip()

                # 发布时间
                published = entry.get("published_parsed")
                publish_time = ""
                if published:
                    try:
                        publish_time = datetime(*published[:6]).isoformat()
                    except Exception:
                        pass

                # AI 关键词过滤
                full_text = f"{title} {summary}"
                if not _match_ai_keywords(full_text):
                    logger.debug(f"机器之心: 非 AI 内容跳过 - {title[:30]}")
                    continue

                # 构造素材
                material = StandardMaterial(
                    url=url,
                    title=title,
                    snippet=summary[:300] if len(summary) > 300 else summary,
                    content=summary,
                    source="jiqizhixin",
                    publish_time=publish_time,
                    category="AI 资讯",
                    extra_data={
                        "source_type": "rss",
                        "source_name": "机器之心"
                    }
                )
                materials.append(material)

            except Exception as e:
                logger.warning(f"机器之心单条解析失败: {e}")
                continue

        logger.info(f"机器之心采集完成: {len(materials)} 条")

    except Exception as e:
        logger.error(f"机器之心采集失败: {e}")

    return materials


# 兼容性：作为 LangGraph 节点
def jiqizhixin_node(state) -> dict:
    """LangGraph 节点包装"""
    materials = jiqizhixin_collector_node(state)
    existing = state.get("raw_materials", [])
    return {"raw_materials": existing + materials}
