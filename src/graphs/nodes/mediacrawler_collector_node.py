"""
MediaCrawler 采集节点（第 13 路，可选 CLI）

通过 subprocess 调 MediaCrawler CLI（不在则 graceful 返回空）。
覆盖：小红书 / 抖音 / 快手 / B站 / 微博 / 百度贴吧 / 知乎

风险评估见 MEDIACRAWLER_EVAL.md — 仅作可选 collector，未启用时节点 no-op。
"""
from __future__ import annotations

import logging
from typing import List

from graphs.state import (
    MediaCrawlerCollectorInput,
    MediaCrawlerCollectorOutput,
    RawMaterial,
)
from tools.mediacrawler import SUPPORTED_PLATFORMS, fetch_topics

logger = logging.getLogger(__name__)

# 默认关注平台（AI 相关：xhs 主推 + 微博热搜 + 知乎讨论）
DEFAULT_PLATFORMS = ("xhs", "wb", "zhihu")
DEFAULT_KEYWORDS: tuple = ("ai", "llm", "claude", "gpt")


def mediacrawler_collector_node(state: MediaCrawlerCollectorInput) -> MediaCrawlerCollectorOutput:
    """调 MediaCrawler CLI 抓多平台热点。CLI 不在时返回空。

    每个平台单独调用一次（CLI 一次只跑一个平台）。
    """
    items: List[RawMaterial] = []
    platforms = list(state.platforms or DEFAULT_PLATFORMS)
    keywords = list(state.keywords or DEFAULT_KEYWORDS)
    per_platform_limit = max(1, state.max_per_source // max(1, len(platforms)))

    for platform in platforms:
        if platform not in SUPPORTED_PLATFORMS:
            logger.debug(f"MediaCrawler: 跳过不支持的平台 {platform}")
            continue
        try:
            fetched = fetch_topics(
                platform=platform,
                keywords=keywords,
                max_results=per_platform_limit,
            )
        except Exception as e:
            logger.warning(f"MediaCrawler {platform} 采集失败: {type(e).__name__}: {e}")
            continue

        for it in fetched[:per_platform_limit]:
            items.append(
                RawMaterial(
                    url=it.url,
                    title=it.title or "",
                    snippet=(it.content or "")[:300],
                    content=it.content or "",
                    source=f"mediacrawler-{it.platform}",
                    extra_data={
                        "platform": it.platform,
                        "author": it.author,
                        "likes": it.likes,
                        "comments": it.comments,
                        "mediacrawler_keys": it.extra_data.get("raw_keys", []),
                    },
                )
            )

    items = items[: state.max_per_source]
    logger.info(f"MediaCrawler 采集 {len(items)} 条 (platforms={platforms})")
    return MediaCrawlerCollectorOutput(mediacrawler_materials=items)