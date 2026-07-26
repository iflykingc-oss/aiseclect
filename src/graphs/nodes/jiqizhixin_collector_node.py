"""机器之心数据采集节点（第 10 路）

采集来源: https://www.jiqizhixin.com/
内容类型: AI 模型、研究、应用、产业新闻
预期产出: 10-15 条/日高质量中文 AI 内容

签名已迁移至 LangGraph 节点约定：(state: JiqizhixinCollectorInput) -> JiqizhixinCollectorOutput
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List

import feedparser
import requests
from bs4 import BeautifulSoup

from graphs.state import (
    JiqizhixinCollectorInput,
    JiqizhixinCollectorOutput,
    RawMaterial,
)

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


def _fetch_rss() -> List[dict]:
    """尝试 RSS 抓取。失败返回 []。"""
    try:
        feed = feedparser.parse(JIQIZHIXIN_RSS)
        if not feed.entries:
            logger.debug("机器之心 RSS 返回空")
            return []
        return [dict(e) for e in feed.entries[:20]]
    except Exception as e:
        logger.debug(f"机器之心 RSS 抓取失败: {type(e).__name__}: {e}")
        return []


def _entry_to_raw(entry: dict) -> RawMaterial | None:
    """把 RSS entry 转成 RawMaterial，过滤非 AI 内容。"""
    title = (entry.get("title") or "").strip()
    url = (entry.get("link") or "").strip()
    if not title or not url:
        return None
    summary = (entry.get("summary") or "").strip()
    if summary:
        try:
            soup = BeautifulSoup(summary, "html.parser")
            summary = soup.get_text().strip()
        except Exception:
            pass

    if not _match_ai_keywords(f"{title} {summary}"):
        return None

    publish_time = ""
    published = entry.get("published_parsed")
    if published:
        try:
            publish_time = datetime(*published[:6]).isoformat()
        except Exception:
            pass

    return RawMaterial(
        url=url,
        title=title,
        snippet=summary[:300],
        content=summary,
        source="jiqizhixin",
        publish_time=publish_time or None,
        extra_data={
            "source_type": "rss",
            "source_name": "机器之心",
            "category_zh": "AI 资讯",
        },
    )


def jiqizhixin_collector_node(state: JiqizhixinCollectorInput) -> JiqizhixinCollectorOutput:
    """机器之心采集器。RSS 失败时 graceful 返回空，不抛异常。"""
    materials: List[RawMaterial] = []
    try:
        logger.info("开始采集机器之心 RSS...")
        entries = _fetch_rss()
        for entry in entries:
            mat = _entry_to_raw(entry)
            if mat is not None:
                materials.append(mat)
        # 限制条数
        materials = materials[: state.max_per_source]
        logger.info(f"机器之心采集完成: {len(materials)} 条")
    except Exception as e:
        logger.error(f"机器之心采集失败: {type(e).__name__}: {e}")
    return JiqizhixinCollectorOutput(jiqizhixin_materials=materials)


# 兼容旧调用：保留 list 风格的入口（方便单测与外部脚本）
def jiqizhixin_collector_legacy(state=None) -> List[dict]:
    """旧签名兼容层：返回 dict 列表而非 Pydantic 模型。已废弃，新代码请用 jiqizhixin_collector_node。"""
    out = jiqizhixin_collector_node(JiqizhixinCollectorInput(max_per_source=20))
    return [m.model_dump() for m in out.jiqizhixin_materials]