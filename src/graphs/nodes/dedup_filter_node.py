"""
去重过滤节点 - 跨 run 持久化（./output/dedup_state.json）+ 内存去重
"""
from __future__ import annotations

import logging
from typing import List, Set

from graphs.state import DedupFilterInput, DedupFilterOutput, StandardMaterial
from tools.dedup_state import DedupState

logger = logging.getLogger(__name__)


def _normalize_url(url: str) -> str:
    """去 query 参数中的 utm_* / ref 等追踪字段，保留 path。"""
    from urllib.parse import urlparse, parse_qsl, urlunparse

    try:
        u = urlparse(url)
    except Exception:
        return url
    qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
          if not k.lower().startswith("utm_") and k.lower() not in ("ref", "ref_source")]
    return urlunparse((u.scheme, u.netloc, u.path.rstrip("/"), u.params, "&".join(f"{k}={v}" for k, v in qs), ""))


def dedup_filter_node(state: DedupFilterInput) -> DedupFilterOutput:
    state_obj = DedupState()
    if state.clear_dedup:
        state_obj.clear()
        logger.info("已清空历史去重状态")

    seen: Set[str] = {u for u in state_obj.known()}
    deduplicated: List[StandardMaterial] = []
    duplicates = 0

    for mat in state.merged_materials:
        key = _normalize_url(mat.url)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        deduplicated.append(mat)

    # 持久化新增的 URL
    new_keys = [_normalize_url(m.url) for m in deduplicated]
    state_obj.add(new_keys)
    state_obj.save()

    logger.info(f"去重: 原始 {len(state.merged_materials)} / 新增 {len(deduplicated)} / 重复 {duplicates}")
    return DedupFilterOutput(
        deduplicated_materials=deduplicated,
        duplicates_count=duplicates,
        new_count=len(deduplicated),
        total_after_dedup=len(deduplicated),
    )
