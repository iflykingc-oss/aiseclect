"""Pipeline 顺序执行器（不依赖 langgraph）

简单场景下用 Pipeline.run() 一行跑完：
    pipeline = Pipeline(
        collectors=[Collector("aihot", aihot_fn), Collector("github", gh_fn)],
        dedup=CrossRunDedup(),
        scorer=my_scorer,
    )
    result = pipeline.run()

需要 DAG 风格（并行 / 条件分支 / 断点）时改用 dag 子模块。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .dedup import CrossRunDedup
from .models import RawMaterial, ScoredMaterial, StandardMaterial

logger = logging.getLogger(__name__)


# 采集器签名：Callable[[], List[RawMaterial]]
CollectorFn = Callable[[], List[RawMaterial]]

# 打分器签名：Callable[[List[StandardMaterial]], List[ScoredMaterial]]
ScorerFn = Callable[[List[StandardMaterial]], List[ScoredMaterial]]


@dataclass
class Collector:
    """一个数据源采集器。"""
    name: str
    fn: CollectorFn
    enabled: bool = True


@dataclass
class PipelineResult:
    """流水线执行结果。"""
    raw: List[RawMaterial] = field(default_factory=list)
    merged: List[StandardMaterial] = field(default_factory=list)
    deduplicated: List[StandardMaterial] = field(default_factory=list)
    scored: List[ScoredMaterial] = field(default_factory=list)

    # 统计
    total_collected: int = 0
    duplicates_count: int = 0
    total_after_dedup: int = 0
    total_after_score: int = 0

    # 元数据
    elapsed_seconds: float = 0.0
    per_source_counts: Dict[str, int] = field(default_factory=dict)


class Pipeline:
    """采集 → 合并 → 去重 → 打分 的顺序执行器。

    各阶段函数签名：
        collector: () -> List[RawMaterial]
        scorer: (List[StandardMaterial]) -> List[ScoredMaterial]
    """

    def __init__(
        self,
        collectors: List[Collector],
        dedup: Optional[CrossRunDedup] = None,
        scorer: Optional[ScorerFn] = None,
        max_per_source: int = 50,
        min_score: float = 0.0,
    ):
        self.collectors = collectors
        self.dedup = dedup or CrossRunDedup()
        self.scorer = scorer
        self.max_per_source = max_per_source
        self.min_score = min_score

    def _run_collectors(self) -> List[RawMaterial]:
        """串行调用每个 collector。失败不中断主流程。"""
        out: List[RawMaterial] = []
        for c in self.collectors:
            if not c.enabled:
                continue
            try:
                items = c.fn() or []
                # 截断单源条数
                items = items[: self.max_per_source]
                out.extend(items)
                logger.info(f"[{c.name}] 采集 {len(items)} 条")
            except Exception as e:
                logger.error(f"[{c.name}] 采集失败: {e}")
        return out

    @staticmethod
    def _merge(raws: List[RawMaterial]) -> List[StandardMaterial]:
        """合并多路采集结果为标准格式。"""
        merged: List[StandardMaterial] = []
        for r in raws:
            merged.append(
                StandardMaterial(
                    url=r.url,
                    title=r.title,
                    snippet=r.snippet,
                    content=r.content,
                    source=r.source,
                    publish_time=r.publish_time,
                    extra_data=r.extra_data,
                )
            )
        return merged

    def _run_dedup(self, merged: List[StandardMaterial]) -> tuple[List[StandardMaterial], int]:
        """用 dedup 过滤已见过的 URL（含 batch 内重复），返回 (new_materials, duplicates_count)。"""
        new_urls = self.dedup.filter_new(m.url for m in merged)
        new_set = set(new_urls)
        # 同一 URL 在 batch 内只保留首次出现的；后续算 batch 内重复
        kept: List[StandardMaterial] = []
        seen_in_batch: set[str] = set()
        for m in merged:
            if m.url not in new_set:
                continue
            if m.url in seen_in_batch:
                continue
            kept.append(m)
            seen_in_batch.add(m.url)
        duplicates = len(merged) - len(kept)
        self.dedup.add(new_urls)
        return kept, duplicates

    def _run_score(self, deduped: List[StandardMaterial]) -> List[ScoredMaterial]:
        if not self.scorer:
            # 无 scorer 时所有素材 heat_score=0
            return [
                ScoredMaterial(
                    url=m.url,
                    title=m.title,
                    snippet=m.snippet,
                    content=m.content,
                    source=m.source,
                    publish_time=m.publish_time,
                    category=m.category,
                    extra_data=m.extra_data,
                    heat_score=0.0,
                    score_reason="无 scorer 配置",
                )
                for m in deduped
            ]
        return self.scorer(deduped)

    def run(self) -> PipelineResult:
        """同步顺序跑完整流水线。"""
        t0 = time.time()
        result = PipelineResult()

        # 1. 采集
        result.raw = self._run_collectors()
        result.total_collected = len(result.raw)
        # 统计 per_source
        for m in result.raw:
            result.per_source_counts[m.source] = result.per_source_counts.get(m.source, 0) + 1

        # 2. 合并
        result.merged = self._merge(result.raw)

        # 3. 去重
        result.deduplicated, result.duplicates_count = self._run_dedup(result.merged)
        result.total_after_dedup = len(result.deduplicated)

        # 4. 打分
        result.scored = self._run_score(result.deduplicated)
        # 按 min_score 过滤（保留原 scored 列表全量，便于上层再用；这里仅计数）
        result.total_after_score = sum(1 for s in result.scored if s.heat_score >= self.min_score)

        result.elapsed_seconds = time.time() - t0
        logger.info(
            f"流水线完成: 采 {result.total_collected} → 去重 {result.total_after_dedup} "
            f"({result.duplicates_count} 重) → 打分 {len(result.scored)} 条 "
            f"({result.elapsed_seconds:.2f}s)"
        )
        return result

    def save_dedup(self) -> None:
        """把去重状态写盘。Pipeline.run() 不会自动 save（避免覆盖中间态）。"""
        self.dedup.save()