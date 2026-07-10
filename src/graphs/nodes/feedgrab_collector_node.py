"""
FeedGrab 采集节点（第 8 路，可选）

- 走 subprocess 调用 feedgrab CLI（不在就 graceful 返回空）
- 默认 platform=mpweixin-id（微信公众号单篇）
- 用户可在 GraphInput/FeedgrabCollectorInput.queries 里传关键词/用户名/URL
"""
from __future__ import annotations

import logging
from typing import List

from graphs.state import (
    FeedgrabCollectorInput,
    FeedgrabCollectorOutput,
    RawMaterial,
)
from tools.feedgrab_collector import feedgrab_collector

logger = logging.getLogger(__name__)

DEFAULT_QUERIES: tuple = ()  # 默认空 → 节点变成 no-op


def feedgrab_collector_node(state: FeedgrabCollectorInput) -> FeedgrabCollectorOutput:
    """调 feedgrab CLI 抓素材。CLI 不在时返回空（不报错）。"""
    items: List[RawMaterial] = []
    queries = list(state.queries or DEFAULT_QUERIES)
    platform = state.platform or "mpweixin-id"

    # queries 可以是 URL / 关键词 / 用户名，逐个丢给 feedgrab
    for q in queries:
        try:
            fetched = feedgrab_collector(platform, q, max_results=state.max_per_source)
            items.extend(fetched)
        except Exception as e:
            logger.warning(f"feedgrab 抓取 {q} 失败: {e}")

    items = items[: state.max_per_source]
    logger.info(f"feedgrab 采集 {len(items)} 条 (platform={platform})")
    return FeedgrabCollectorOutput(feedgrab_materials=items)