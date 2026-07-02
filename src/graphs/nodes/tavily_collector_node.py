"""
综合采集（已替换为 AIHOT hot-topics）
- 原 Tavily 综合搜索在国内被墙，改用 AIHOT 的多源热度排序
- 数据源: aihot.virxact.com /api/public/hot-topics
- 该端点按"多源报道数 + 时间衰减"排序，回答"现在 AI 圈最热是什么"
"""
from __future__ import annotations

import logging
from typing import List

from graphs.state import TavilyCollectorInput, TavilyCollectorOutput, RawMaterial
from tools.aihot_client import AIHotClient

logger = logging.getLogger(__name__)


def tavily_collector_node(state: TavilyCollectorInput) -> TavilyCollectorOutput:
    logger.info("AIHOT hot-topics 采集开始（多源热度排序）")
    client = AIHotClient()
    hot = client.hot_topics(take=max(20, state.max_per_source))
    materials: List[RawMaterial] = []
    for item in hot[: state.max_per_source]:
        url = item.get("permalink") or item.get("url") or ""
        if not url:
            continue
        title = item.get("title", "")
        source_count = item.get("sourceCount", 0)
        source_names = item.get("sourceNames", []) or []
        snippet = f"🔥 {source_count} 个独立信源在报道"
        if source_names:
            snippet += f"\n来源: {', '.join(source_names[:5])}"
        materials.append(
            RawMaterial(
                url=url,
                title=title,
                snippet=snippet,
                content="",
                source="aihot-hot",
                publish_time=item.get("latestAt"),
                extra_data={
                    "source_count": source_count,
                    "source_names": source_names,
                    "original_url": item.get("url"),
                    "source_name": item.get("source"),
                },
            )
        )
    logger.info(f"AIHOT hot-topics: {len(materials)} 条")
    return TavilyCollectorOutput(tavily_materials=materials)