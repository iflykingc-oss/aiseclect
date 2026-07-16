"""知乎 AI 话题采集节点

采集来源: https://www.zhihu.com/topic/19551275/hot (人工智能话题热榜)
内容类型: AI 讨论、问答、观点
预期产出: 10-15 条/日讨论型内容
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


def zhihu_ai_collector_node(state=None) -> List[StandardMaterial]:
    """知乎 AI 话题采集器

    注意：知乎有较强反爬机制，可能需要：
    1. Cookie 池（多账号轮换）
    2. 代理 IP
    3. 降低请求频率

    当前实现为基础版本，仅抓取公开可访问内容。

    Returns:
        List[StandardMaterial]: 采集到的素材列表
    """
    materials = []

    try:
        logger.info("开始采集知乎 AI 话题...")

        # 方案 1: 尝试 API（可能需要登录）
        try:
            resp = requests.get(
                ZHIHU_API_HOT,
                headers=HEADERS,
                params={"limit": 20},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])

                for item in items:
                    try:
                        target = item.get("target", {})
                        question = target.get("question", {})

                        title = question.get("title", "")
                        url = f"https://www.zhihu.com/question/{question.get('id', '')}"
                        excerpt = target.get("excerpt", "")

                        if not title:
                            continue

                        material = StandardMaterial(
                            url=url,
                            title=title,
                            snippet=excerpt[:300] if len(excerpt) > 300 else excerpt,
                            content=excerpt,
                            source="zhihu",
                            publish_time="",
                            category="AI 讨论",
                            extra_data={
                                "source_type": "api",
                                "source_name": "知乎 AI 话题",
                                "answer_count": question.get("answer_count", 0),
                                "follower_count": question.get("follower_count", 0),
                            }
                        )
                        materials.append(material)

                    except Exception as e:
                        logger.warning(f"知乎单条 API 解析失败: {e}")
                        continue

                if materials:
                    logger.info(f"知乎 API 采集完成: {len(materials)} 条")
                    return materials

        except Exception as e:
            logger.warning(f"知乎 API 采集失败，尝试网页抓取: {e}")

        # 方案 2: 网页抓取（备选）
        resp = requests.get(ZHIHU_AI_TOPIC, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = 'utf-8'

        soup = BeautifulSoup(resp.text, "html.parser")

        # 查找问题列表（需根据实际 HTML 调整）
        questions = soup.find_all("div", class_=re.compile(r"ContentItem"), limit=20)
        if not questions:
            questions = soup.find_all("h2", limit=20)

        if not questions:
            logger.warning(f"知乎页面未找到问题列表，可能需要登录。HTML 长度: {len(resp.text)}")
            return materials

        for q in questions:
            try:
                # 提取标题和链接
                link = q.find("a", href=re.compile(r"/question/\d+"))
                if not link:
                    continue

                title = link.get_text(strip=True)
                url = link.get("href")

                # 补全相对 URL
                if url and not url.startswith("http"):
                    url = "https://www.zhihu.com" + url

                # 提取摘要（如果有）
                excerpt_elem = q.find("span", class_=re.compile(r"RichText|excerpt"))
                excerpt = excerpt_elem.get_text(strip=True) if excerpt_elem else ""

                material = StandardMaterial(
                    url=url,
                    title=title,
                    snippet=excerpt[:300] if len(excerpt) > 300 else excerpt,
                    content=excerpt,
                    source="zhihu",
                    publish_time="",
                    category="AI 讨论",
                    extra_data={
                        "source_type": "web_scrape",
                        "source_name": "知乎 AI 话题"
                    }
                )
                materials.append(material)

            except Exception as e:
                logger.warning(f"知乎单条网页解析失败: {e}")
                continue

        logger.info(f"知乎网页采集完成: {len(materials)} 条")

    except Exception as e:
        logger.error(f"知乎采集失败: {e}")
        logger.info("提示：知乎采集可能需要配置 Cookie 或使用代理，详见文档")

    return materials


# 兼容性：作为 LangGraph 节点
def zhihu_node(state) -> dict:
    """LangGraph 节点包装"""
    materials = zhihu_ai_collector_node(state)
    existing = state.get("raw_materials", [])
    return {"raw_materials": existing + materials}
