"""量子位数据采集节点

采集来源: https://www.qbitai.com/
内容类型: AI 产业新闻、技术解读、公司动态
预期产出: 8-12 条/日 AI 产业新闻
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List

import requests
from bs4 import BeautifulSoup

from graphs.state import StandardMaterial

logger = logging.getLogger(__name__)

# 量子位首页（使用主页而非 /latest）
QBITAI_HOME = "https://www.qbitai.com/"

# AI 关键词过滤
AI_KEYWORDS = (
    "ai", "大模型", "llm", "gpt", "chatgpt", "claude", "gemini", "智能",
    "machine learning", "deep learning", "神经网络", "transformer",
    "agent", "智能体", "prompt", "微调", "训练", "推理",
    "sora", "midjourney", "stable diffusion", "生成式", "aigc",
    "openai", "anthropic", "google ai", "deepmind", "百度", "阿里",
    "腾讯", "字节", "智谱", "月之暗面", "minimax",
)

# User-Agent 避免反爬
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _match_ai_keywords(text: str) -> bool:
    """检查文本是否包含 AI 关键词"""
    if not text:
        return False
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in AI_KEYWORDS)


def qbitai_collector_node(state=None) -> List[StandardMaterial]:
    """量子位采集器

    Returns:
        List[StandardMaterial]: 采集到的素材列表
    """
    materials = []

    try:
        logger.info("开始采集量子位...")

        # 请求首页
        resp = requests.get(QBITAI_HOME, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = 'utf-8'

        soup = BeautifulSoup(resp.text, "html.parser")

        # 查找文章列表（需要根据实际 HTML 结构调整）
        articles = soup.find_all("article", limit=20)
        if not articles:
            # 备选：查找 class 包含 post/item 的元素
            articles = soup.find_all(class_=re.compile(r"post|item|article"), limit=20)

        if not articles:
            logger.warning(f"量子位页面未找到文章列表，HTML 长度: {len(resp.text)}")
            return materials

        for article in articles:
            try:
                # 提取标题和链接
                title_elem = article.find(["h1", "h2", "h3", "a"])
                if not title_elem:
                    continue

                title = title_elem.get_text(strip=True)
                url = title_elem.get("href") or article.find("a").get("href")

                # 补全相对 URL
                if url and not url.startswith("http"):
                    url = "https://www.qbitai.com" + url

                # 提取摘要
                summary_elem = article.find(["p", "div"], class_=re.compile(r"excerpt|summary|desc"))
                summary = summary_elem.get_text(strip=True) if summary_elem else ""

                # AI 关键词过滤
                full_text = f"{title} {summary}"
                if not _match_ai_keywords(full_text):
                    logger.debug(f"量子位: 非 AI 内容跳过 - {title[:30]}")
                    continue

                # 构造素材
                material = StandardMaterial(
                    url=url,
                    title=title,
                    snippet=summary[:300] if len(summary) > 300 else summary,
                    content=summary,
                    source="qbitai",
                    publish_time="",  # 量子位页面通常不显示时间
                    category="AI 资讯",
                    extra_data={
                        "source_type": "web_scrape",
                        "source_name": "量子位"
                    }
                )
                materials.append(material)

            except Exception as e:
                logger.warning(f"量子位单条解析失败: {e}")
                continue

        logger.info(f"量子位采集完成: {len(materials)} 条")

    except Exception as e:
        logger.error(f"量子位采集失败: {e}")

    return materials


# 兼容性：作为 LangGraph 节点
def qbitai_node(state) -> dict:
    """LangGraph 节点包装"""
    materials = qbitai_collector_node(state)
    existing = state.get("raw_materials", [])
    return {"raw_materials": existing + materials}
