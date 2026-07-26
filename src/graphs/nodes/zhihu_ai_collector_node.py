"""知乎 AI 话题采集节点（第 12 路）

采集来源: https://www.zhihu.com/topic/19551275/hot (人工智能话题热榜)
内容类型: AI 讨论、问答、观点
预期产出: 10-15 条/日讨论型内容

注意：知乎有较强反爬机制，可能需要：
1. Cookie 池（多账号轮换）
2. 代理 IP
3. 降低请求频率

当前实现为基础版本，仅抓取公开可访问内容。

签名已迁移至 LangGraph 节点约定：(state: ZhihuAICollectorInput) -> ZhihuAICollectorOutput
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from graphs.state import (
    ZhihuAICollectorInput,
    ZhihuAICollectorOutput,
    RawMaterial,
)

logger = logging.getLogger(__name__)

# 知乎 AI 话题热榜
ZHIHU_AI_TOPIC = "https://www.zhihu.com/topic/19551275/hot"
ZHIHU_API_HOT = "https://www.zhihu.com/api/v4/topics/19551275/feeds/top_activity"

# Headers 模拟浏览器（知乎有较强反爬）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.zhihu.com/",
}


def _fetch_api(max_items: int) -> List[dict]:
    """尝试 API 抓取。失败返回 []。"""
    try:
        resp = requests.get(
            ZHIHU_API_HOT,
            headers=HEADERS,
            params={"limit": max_items},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        items = data.get("data", [])
        return [item for item in items if isinstance(item, dict)]
    except Exception as e:
        logger.debug(f"知乎 API 抓取失败: {type(e).__name__}: {e}")
        return []


def _api_item_to_raw(item: dict) -> RawMaterial | None:
    """API 返回的 item → RawMaterial。"""
    try:
        target = item.get("target") or {}
        question = target.get("question") or {}
        title = (question.get("title") or "").strip()
        qid = question.get("id")
        if not title or not qid:
            return None
        url = f"https://www.zhihu.com/question/{qid}"
        excerpt = (target.get("excerpt") or "").strip()
        return RawMaterial(
            url=url,
            title=title,
            snippet=excerpt[:300] if excerpt else "",
            content=excerpt,
            source="zhihu",
            publish_time=None,
            extra_data={
                "source_type": "api",
                "source_name": "知乎 AI 话题",
                "category_zh": "AI 讨论",
                "answer_count": question.get("answer_count", 0),
                "follower_count": question.get("follower_count", 0),
            },
        )
    except Exception as e:
        logger.debug(f"知乎 API 单条解析失败: {type(e).__name__}: {e}")
        return None


def _fetch_web() -> List[dict]:
    """网页抓取备选路径。返回 BS4 元素列表（不强类型）。"""
    try:
        resp = requests.get(ZHIHU_AI_TOPIC, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        questions = soup.find_all("div", class_=re.compile(r"ContentItem"), limit=20)
        if not questions:
            questions = soup.find_all("h2", limit=20)
        return questions
    except Exception as e:
        logger.debug(f"知乎网页抓取失败: {type(e).__name__}: {e}")
        return []


def _web_question_to_raw(q) -> RawMaterial | None:
    """网页版 question 元素 → RawMaterial。"""
    try:
        link = q.find("a", href=re.compile(r"/question/\d+"))
        if not link:
            return None
        title = link.get_text(strip=True)
        url = link.get("href", "")
        if not title or not url:
            return None
        if not url.startswith("http"):
            url = "https://www.zhihu.com" + url
        excerpt_elem = q.find("span", class_=re.compile(r"RichText|excerpt"))
        excerpt = excerpt_elem.get_text(strip=True) if excerpt_elem else ""
        return RawMaterial(
            url=url,
            title=title,
            snippet=excerpt[:300] if excerpt else "",
            content=excerpt,
            source="zhihu",
            publish_time=None,
            extra_data={
                "source_type": "web_scrape",
                "source_name": "知乎 AI 话题",
                "category_zh": "AI 讨论",
            },
        )
    except Exception as e:
        logger.debug(f"知乎网页单条解析失败: {type(e).__name__}: {e}")
        return None


def zhihu_ai_collector_node(state: ZhihuAICollectorInput) -> ZhihuAICollectorOutput:
    """知乎 AI 话题采集器。先试 API，失败再降级到网页。失败时返回空。"""
    materials: List[RawMaterial] = []
    try:
        logger.info("开始采集知乎 AI 话题...")

        # 路径 1：API
        api_items = _fetch_api(max_items=20)
        for item in api_items:
            mat = _api_item_to_raw(item)
            if mat is not None:
                materials.append(mat)
        if materials:
            logger.info(f"知乎 API 采集完成: {len(materials)} 条")
            materials = materials[: state.max_per_source]
            return ZhihuAICollectorOutput(zhihu_ai_materials=materials)

        # 路径 2：网页抓取（API 无数据时备选）
        questions = _fetch_web()
        if not questions:
            logger.debug("知乎网页也未找到问题列表（可能需要登录）")
            return ZhihuAICollectorOutput(zhihu_ai_materials=[])

        for q in questions:
            mat = _web_question_to_raw(q)
            if mat is not None:
                materials.append(mat)
        materials = materials[: state.max_per_source]
        logger.info(f"知乎网页采集完成: {len(materials)} 条")
    except Exception as e:
        logger.error(f"知乎采集失败: {type(e).__name__}: {e}")
    return ZhihuAICollectorOutput(zhihu_ai_materials=materials)


# 兼容旧调用
def zhihu_ai_collector_legacy(state=None) -> List[dict]:
    """已废弃：新代码请用 zhihu_ai_collector_node。"""
    out = zhihu_ai_collector_node(ZhihuAICollectorInput(max_per_source=20))
    return [m.model_dump() for m in out.zhihu_ai_materials]