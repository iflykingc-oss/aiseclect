"""
Last30days 采集节点（第 9 路，可选）

- 通过 subprocess 调 last30days CLI（不在则 graceful 返回空）
- 把 CLI 返回的话题转成统一 RawMaterial，source 标为 last30days-{platform}
- 没启用 CLI 时与 feedgrab 行为一致：节点变成 no-op，不阻塞主流程
"""
from __future__ import annotations

import logging
from typing import List

from graphs.state import (
    Last30daysCollectorInput,
    Last30daysCollectorOutput,
    RawMaterial,
)
from tools.last30days import fetch_topics

logger = logging.getLogger(__name__)

# 默认关注 AI 相关话题（与全项目 AI 主题闸门一致）
DEFAULT_QUERIES: tuple = ("ai", "llm", "claude", "gpt", "openai")


def last30days_collector_node(state: Last30daysCollectorInput) -> Last30daysCollectorOutput:
    """调 last30days CLI 抓过去 30 天热点。CLI 不在时返回空。"""
    items: List[RawMaterial] = []
    queries = list(state.queries or DEFAULT_QUERIES)

    try:
        fetched = fetch_topics(queries=queries, max_results=state.max_per_source)
    except Exception as e:
        logger.warning(f"last30days 采集失败: {type(e).__name__}: {e}")
        fetched = []

    for it in fetched[: state.max_per_source]:
        # source 标 last30days-{platform} 让 _category_from_raw 能区分
        source = f"last30days-{it.platform}" if it.platform else "last30days"
        items.append(
            RawMaterial(
                url=it.url,
                title=it.title or "",
                snippet=it.snippet or "",
                content="",  # 内容靠 content_enricher 后续抓
                source=source,
                extra_data={
                    "engagement": it.engagement,
                    "platform": it.platform,
                    "last30days_keys": it.extra_data.get("raw_keys", []),
                },
            )
        )

    logger.info(f"last30days 采集 {len(items)} 条 (queries={queries})")
    return Last30daysCollectorOutput(last30days_materials=items)