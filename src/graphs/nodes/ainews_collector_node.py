"""
AI News 雷达采集（技术新闻 + 论文）
- 数据源 1: aihot.virxact.com REST API category=ai-models + category=paper
- 数据源 2: LearnPrompt ai-news-radar data/latest-24h.json（24h AI 强相关消息补充）
"""
from __future__ import annotations

import logging
from typing import List

from graphs.state import AINewsCollectorInput, AINewsCollectorOutput, RawMaterial
from tools.aihot_client import AIHotClient
from tools.learnprompt_client import LearnPromptRadarClient

logger = logging.getLogger(__name__)


def ainews_collector_node(state: AINewsCollectorInput) -> AINewsCollectorOutput:
    logger.info("AI News 雷达采集开始（aihot 模型/论文 + LearnPrompt 24h）")
    materials: List[RawMaterial] = []
    seen_urls: set = set()

    # 源 1: AIHOT - ai-models + paper（卡兹克精编）
    try:
        aihot = AIHotClient()
        for cat in ("ai-models", "paper"):
            try:
                resp = aihot.list_items(mode="selected", category=cat, take=state.max_per_source)
                for item in resp.items[: state.max_per_source]:
                    url = item.permalink or item.url
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    materials.append(
                        RawMaterial(
                            url=url,
                            title=item.title,
                            snippet=item.summary or "",
                            content=item.summary or "",
                            source=f"aihot-{cat}",
                            publish_time=item.published_at,
                            extra_data={
                                "original_url": item.url,
                                "source_name": item.source,
                                "category": cat,
                                "score": item.score,
                            },
                        )
                    )
            except Exception as e:
                logger.warning(f"AIHOT {cat} 采集失败: {e}")
    except Exception as e:
        logger.warning(f"AIHOT 客户端初始化失败: {e}")

    # 源 2: LearnPrompt ai-news-radar - 24h 全量
    try:
        radar = LearnPromptRadarClient()
        radar_items = radar.latest_24h()
        for item in radar_items[: state.max_per_source * 2]:
            url = item.url
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            materials.append(
                RawMaterial(
                    url=url,
                    title=item.title_zh or item.title,
                    snippet=item.title_en or "",
                    content="",
                    source=f"radar-{item.site_id}",
                    publish_time=item.published_at,
                    extra_data={
                        "site_name": item.site_name,
                        "ai_score": item.ai_score,
                        "ai_label": item.ai_label,
                        "source_tier": item.source_tier_label,
                    },
                )
            )
    except Exception as e:
        logger.warning(f"LearnPrompt Radar 采集失败: {e}")

    logger.info(f"AI News: {len(materials)} 条（aihot + learnprompt-radar）")
    return AINewsCollectorOutput(ainews_materials=materials)