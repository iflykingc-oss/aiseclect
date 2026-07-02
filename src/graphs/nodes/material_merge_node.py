"""
素材合并节点 - 5 路 → 标准化列表
"""
from __future__ import annotations

import logging
from typing import List

from graphs.state import (
    MaterialMergeInput,
    MaterialMergeOutput,
    RawMaterial,
    StandardMaterial,
)

logger = logging.getLogger(__name__)


_CATEGORY_BY_SOURCE = {
    "github": "开源项目",
    "aihot": "行业热点",
    "ainews": "技术突破",
    "rss": "社区动态",
    "tavily": "综合资讯",
}


def material_merge_node(state: MaterialMergeInput) -> MaterialMergeOutput:
    all_materials: List[RawMaterial] = []
    all_materials.extend(state.aihot_materials)
    all_materials.extend(state.ainews_materials)
    all_materials.extend(state.rss_materials)
    all_materials.extend(state.tavily_materials)
    all_materials.extend(state.github_materials)

    merged: List[StandardMaterial] = []
    for raw in all_materials:
        if not raw.url:
            continue
        merged.append(
            StandardMaterial(
                url=raw.url,
                title=raw.title or "",
                snippet=raw.snippet or "",
                content=raw.content or "",
                source=raw.source or "",
                publish_time=raw.publish_time,
                category=_CATEGORY_BY_SOURCE.get(raw.source, "未分类"),
            )
        )

    logger.info(f"合并: {len(merged)} 条")
    return MaterialMergeOutput(merged_materials=merged, total_collected=len(merged))
