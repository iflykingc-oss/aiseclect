"""
RSS 社区/补充采集
- 数据源 1: LearnPrompt ai-news-radar daily-brief.json（多源聚合精选故事，最有价值）
- 数据源 2: 量子位 RSS（https://www.qbitai.com/feed）- 国内 AI 媒体
- 数据源 3: 少数派 RSS（https://sspai.com/feed）- 工具/AI 应用
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import List
from html import unescape

import requests

from graphs.state import RSSCollectorInput, RSSCollectorOutput, RawMaterial
from tools.learnprompt_client import LearnPromptRadarClient

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss(xml_text: str, source_tag: str, limit: int) -> List[RawMaterial]:
    """通用 RSS 2.0 解析，返回 RawMaterial 列表。"""
    materials: List[RawMaterial] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"{source_tag} RSS 解析失败: {e}")
        return materials
    items = list(root.iter("item"))[:limit]
    for item in items:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        # description / content:encoded
        desc = item.findtext("description") or ""
        content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        content = content_el.text if content_el is not None else ""
        snippet = _strip_html(desc) or _strip_html(content)[:300]
        pub = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
        materials.append(
            RawMaterial(
                url=link.strip(),
                title=title,
                snippet=snippet[:500],
                content=_strip_html(content)[:2000],
                source=source_tag,
                publish_time=pub.strip() if pub else None,
                extra_data={},
            )
        )
    return materials


def _fetch_rss(url: str, source_tag: str, limit: int) -> List[RawMaterial]:
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": UA})
        if resp.status_code != 200:
            logger.warning(f"{source_tag} RSS HTTP {resp.status_code}")
            return []
        return _parse_rss(resp.content, source_tag, limit)
    except requests.RequestException as e:
        logger.warning(f"{source_tag} RSS 网络异常: {e}")
        return []


def rss_collector_node(state: RSSCollectorInput) -> RSSCollectorOutput:
    logger.info("RSS 社区/补充采集开始（LearnPrompt 精选 + 量子位 + 少数派）")
    materials: List[RawMaterial] = []
    seen: set = set()

    # 源 1: LearnPrompt 伯乐精选故事（最重要，多源聚合）
    radar = LearnPromptRadarClient()
    for story in radar.daily_brief()[: state.max_per_source]:
        url = story.primary_url or story.url
        if not url or url in seen:
            continue
        seen.add(url)
        snippet = f"多源聚合 · {story.source_count} 个独立信源"
        if story.source_names:
            snippet += f"（{', '.join(story.source_names[:3])}）"
        if story.importance_label:
            snippet += f"\n重要性: {story.importance_label}"
        materials.append(
            RawMaterial(
                url=url,
                title=story.title,
                snippet=snippet,
                content=f"关联 {story.item_count} 条报道",
                source="radar-daily-brief",
                publish_time=story.latest_at or story.earliest_at,
                extra_data={
                    "story_id": story.story_id,
                    "source_count": story.source_count,
                    "source_names": story.source_names,
                    "importance": story.importance_label,
                    "score": story.score,
                },
            )
        )

    # 源 2: 量子位（已验证 200，国内 AI 媒体头部）
    for m in _fetch_rss("https://www.qbitai.com/feed", "qbitai", state.max_per_source):
        if m.url in seen:
            continue
        seen.add(m.url)
        materials.append(m)

    # 源 3: 少数派（工具/AI 应用视角）
    for m in _fetch_rss("https://sspai.com/feed", "sspai", state.max_per_source):
        if m.url in seen:
            continue
        seen.add(m.url)
        materials.append(m)

    logger.info(f"RSS 社区: {len(materials)} 条")
    return RSSCollectorOutput(rss_materials=materials)