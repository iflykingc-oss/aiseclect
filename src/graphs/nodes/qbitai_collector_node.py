"""量子位数据采集节点（第 11 路）

采集来源: https://www.qbitai.com/
内容类型: AI 产业新闻、技术解读、公司动态
预期产出: 8-12 条/日 AI 产业新闻

签名已迁移至 LangGraph 节点约定：(state: QbitaiCollectorInput) -> QbitaiCollectorOutput
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from graphs.state import (
    QbitaiCollectorInput,
    QbitaiCollectorOutput,
    RawMaterial,
)

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


def _article_to_raw(article) -> RawMaterial | None:
    """把 BS4 article element 转 RawMaterial。"""
    try:
        title_elem = article.find(["h1", "h2", "h3", "a"])
        if not title_elem:
            return None
        title = title_elem.get_text(strip=True)
        url = title_elem.get("href") or (article.find("a") or {}).get("href") if article.find("a") else None
        if not title or not url:
            return None
        if not url.startswith("http"):
            url = "https://www.qbitai.com" + url

        summary_elem = article.find(["p", "div"], class_=re.compile(r"excerpt|summary|desc"))
        summary = summary_elem.get_text(strip=True) if summary_elem else ""

        if not _match_ai_keywords(f"{title} {summary}"):
            return None

        return RawMaterial(
            url=url,
            title=title,
            snippet=summary[:300] if summary else "",
            content=summary,
            source="qbitai",
            publish_time=None,
            extra_data={
                "source_type": "web_scrape",
                "source_name": "量子位",
                "category_zh": "AI 资讯",
            },
        )
    except Exception as e:
        logger.debug(f"量子位单条解析失败: {type(e).__name__}: {e}")
        return None


def qbitai_collector_node(state: QbitaiCollectorInput) -> QbitaiCollectorOutput:
    """量子位采集器。失败时 graceful 返回空。"""
    materials: List[RawMaterial] = []
    try:
        logger.info("开始采集量子位...")
        resp = requests.get(QBITAI_HOME, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 查找文章列表（按优先级尝试多种 selector）
        articles = soup.find_all("article", limit=20)
        if not articles:
            articles = soup.find_all(class_=re.compile(r"post|item|article"), limit=20)

        if not articles:
            logger.debug(f"量子位页面未找到文章列表，HTML 长度: {len(resp.text)}")
            return QbitaiCollectorOutput(qbitai_materials=[])

        for article in articles:
            mat = _article_to_raw(article)
            if mat is not None:
                materials.append(mat)

        materials = materials[: state.max_per_source]
        logger.info(f"量子位采集完成: {len(materials)} 条")
    except Exception as e:
        logger.error(f"量子位采集失败: {type(e).__name__}: {e}")
    return QbitaiCollectorOutput(qbitai_materials=materials)


# 兼容旧调用
def qbitai_collector_legacy(state=None) -> List[dict]:
    """已废弃：新代码请用 qbitai_collector_node。"""
    out = qbitai_collector_node(QbitaiCollectorInput(max_per_source=20))
    return [m.model_dump() for m in out.qbitai_materials]