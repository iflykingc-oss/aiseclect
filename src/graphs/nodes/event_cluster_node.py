"""
事件聚类节点 - 同一事件多源报道合并
- 位置：heat_scorer 之后、content_cleaner 之前（用 heat_score 选代表）
- 算法：标题归一化 → (SequenceMatcher 相似度 OR bigram Jaccard 交集比) → 大于阈值合并到同簇
- 输出：每簇保留 heat_score 最高的一条为代表，其他 URL 挂到 related_urls
- 依赖：仅标准库 difflib，无新增第三方库

**为什么用两种相似度**：中文标题短，SequenceMatcher 对「A 发布 X」vs「X 正式上线」这种带前缀
差异的敏感度低。bigram Jaccard 在共享关键实体时会给高分，两者取 max 更 robust。
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import List, Set

from graphs.state import EventClusterInput, EventClusterOutput, ScoredMaterial

logger = logging.getLogger(__name__)

# SequenceMatcher 阈值（连续子串占比）
SEQ_THRESHOLD = 0.72
# bigram Jaccard 阈值（关键 2-gram 交集 / 较短集合）
BIGRAM_THRESHOLD = 0.55

# 归一化：去空格、常见标点、大小写
_PUNCT_RE = re.compile(r"[\s，。！？：；、\"\"''「」『』()（）\[\]【】<>《》\-—_·.…!?:;,\"']+")


def _normalize_title(title: str) -> str:
    if not title:
        return ""
    t = _PUNCT_RE.sub("", title)
    return t.lower()


def _bigrams(text: str) -> Set[str]:
    """字符级 2-gram。对短中文标题的关键实体覆盖比 SequenceMatcher 更稳。"""
    if len(text) < 2:
        return set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _similarity(a: str, b: str) -> float:
    """两种相似度取 max：连续子串比 + bigram 覆盖率（以较短集合为分母）。"""
    if not a or not b:
        return 0.0
    seq = difflib.SequenceMatcher(None, a, b).ratio()

    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return seq
    inter = len(ba & bb)
    # 用较短集合作分母：短标题被长标题「包含」时应视为强匹配
    min_size = min(len(ba), len(bb))
    coverage = inter / min_size if min_size else 0.0

    # 归一化到「与 SEQ_THRESHOLD 可比」的量纲：coverage >= BIGRAM_THRESHOLD 视为强匹配
    coverage_scaled = coverage * (SEQ_THRESHOLD / BIGRAM_THRESHOLD)
    return max(seq, min(coverage_scaled, 1.0))


def _cluster(materials: List[ScoredMaterial]) -> List[List[ScoredMaterial]]:
    """单遍贪心聚类：每条素材归入首个相似度超过阈值的已存在簇；否则起新簇。"""
    clusters: List[List[ScoredMaterial]] = []
    norm_titles: List[str] = []  # 每个 cluster 用「代表标题」的归一化形式做匹配

    for mat in materials:
        norm = _normalize_title(mat.title)
        if not norm:
            # 无标题不参与聚类，独立一簇
            clusters.append([mat])
            norm_titles.append("")
            continue

        matched_idx = -1
        best_ratio = 0.0
        for i, existing in enumerate(norm_titles):
            if not existing:
                continue
            r = _similarity(norm, existing)
            if r >= SEQ_THRESHOLD and r > best_ratio:
                best_ratio = r
                matched_idx = i

        if matched_idx >= 0:
            clusters[matched_idx].append(mat)
        else:
            clusters.append([mat])
            norm_titles.append(norm)

    return clusters


def _pick_representative(cluster: List[ScoredMaterial]) -> ScoredMaterial:
    """簇内选代表：heat_score 高优先，同分挑 content 更长（信息量更足）。"""
    return max(cluster, key=lambda m: (m.heat_score, len(m.content or ""), len(m.snippet or "")))


def event_cluster_node(state: EventClusterInput) -> EventClusterOutput:
    materials = state.scored_materials
    if not materials:
        return EventClusterOutput(scored_materials=[], total_after_score=0, clustered_count=0)

    clusters = _cluster(materials)
    representatives: List[ScoredMaterial] = []
    merged_titles: List[str] = []  # 用于 debug 日志

    for cluster in clusters:
        rep = _pick_representative(cluster)
        others = [m for m in cluster if m.url != rep.url]
        if others:
            merged_titles.append(f"「{rep.title[:30]}」← 合并 {len(others)} 条")
        rep_updated = rep.model_copy(update={
            "related_urls": [m.url for m in others],
            "cluster_size": len(cluster),
        })
        representatives.append(rep_updated)

    clustered_count = len(materials) - len(representatives)
    if clustered_count > 0:
        logger.info(f"事件聚类: {len(materials)} → {len(representatives)} 条 (合并 {clustered_count})")
        for t in merged_titles[:5]:
            logger.info(f"  {t}")
    else:
        logger.info(f"事件聚类: {len(materials)} 条，无重复事件")

    return EventClusterOutput(
        scored_materials=representatives,
        total_after_score=len(representatives),
        clustered_count=clustered_count,
    )
