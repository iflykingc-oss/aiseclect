"""
Agent-Reach 采集节点（第 7 路，可选）

- 走 subprocess 调用 agent-reach CLI（不在就 graceful 返回空）
- 当前默认 platform=web，url 从 watchlist 关键词构造（占位）
- CLI 装包见 ~/.claude/skills/agent-reach/SKILL.md
"""
from __future__ import annotations

import logging
from typing import List

from graphs.state import (
    AgentReachCollectorInput,
    AgentReachCollectorOutput,
    RawMaterial,
)
from tools.agent_reach_collector import agent_reach_collector

logger = logging.getLogger(__name__)

# 默认行为：CLI 没装就返回空，不阻塞主流程
# 用户必须在 GraphInput/AgentReachCollectorInput.queries 里显式传入 URL 或关键词，
# 默认空 → 节点变成 no-op，不浪费一次 CLI 调用
DEFAULT_QUERIES: tuple = ()


def agent_reach_collector_node(state: AgentReachCollectorInput) -> AgentReachCollectorOutput:
    """调 agent-reach CLI 抓素材。CLI 不在时返回空（不报错）。"""
    items: List[RawMaterial] = []
    queries = list(state.queries or DEFAULT_QUERIES)
    platform = state.platform or "web"

    for q in queries:
        try:
            fetched = agent_reach_collector(platform=platform, url=q)
            items.extend(fetched)
        except Exception as e:
            logger.warning(f"agent_reach 抓取 {q} 失败: {e}")

    # 截断 max_per_source
    items = items[: state.max_per_source]
    logger.info(f"agent_reach 采集 {len(items)} 条 (platform={platform})")
    return AgentReachCollectorOutput(agent_reach_materials=items)