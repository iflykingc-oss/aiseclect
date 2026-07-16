"""
AIHOT 雷达采集（卡兹克的中文 AI 资讯精选）
- 数据源: aihot.virxact.com REST API /api/public/items?mode=selected
- 精编候选池，覆盖 ai-models / ai-products / industry / paper / tip 全分类
"""
from __future__ import annotations

import logging
from typing import List

from graphs.state import AIHotCollectorInput, AIHotCollectorOutput, RawMaterial
from tools.aihot_client import AIHotClient

logger = logging.getLogger(__name__)

# category 中文映射（来自 /api/public/daily section label）
CATEGORY_ZH = {
    "ai-models": "模型发布",
    "ai-products": "产品发布",
    "industry": "行业动态",
    "paper": "论文研究",
    "tip": "技巧与观点",
}


def aihot_collector_node(state: AIHotCollectorInput) -> AIHotCollectorOutput:
    logger.info("AIHOT 雷达采集开始（卡兹克 ai-news-radar 同源精编）")
    materials: List[RawMaterial] = []

    try:
        client = AIHotClient()
        resp = client.list_items(mode="selected", take=max(20, state.max_per_source))
        for item in resp.items[: state.max_per_source]:
            if not item.url:
                continue
            materials.append(
                RawMaterial(
                    url=item.permalink or item.url,  # 站内 permalink 优先（中文翻译 + 无墙）
                    title=item.title,
                    snippet=item.summary or "",
                    content=item.summary or "",
                    source="aihot",
                    publish_time=item.published_at,
                    extra_data={
                        "original_url": item.url,
                        "source_name": item.source,
                        "category": item.category,
                        "category_zh": CATEGORY_ZH.get(item.category or "", "未分类"),
                        "score": item.score,
                        "selected": item.selected,
                    },
                )
            )
        logger.info(f"AIHOT 雷达: {len(materials)} 条（精选池共 {resp.count}）")
    except Exception as e:
        logger.warning(f"AIHOT 采集失败: {e}")

    return AIHotCollectorOutput(aihot_materials=materials)