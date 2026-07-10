"""
collect_pipeline demo（独立可运行）

展示两种用法：
1) Pipeline.run()  — 顺序执行，不依赖 langgraph
2) build_collect_graph() — langgraph DAG 编排

用法：
    python -m collect_pipeline.examples.demo
"""
from __future__ import annotations

import logging
import random
import sys
from pathlib import Path

# 让 src/ 在 import 路径上
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from collect_pipeline import (  # noqa: E402
    Collector,
    CrossRunDedup,
    Pipeline,
    RawMaterial,
    ScoredMaterial,
    StandardMaterial,
    persist_materials,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("collect_pipeline.demo")


# ---------- Mock 采集器 ----------

SAMPLE_URLS_A = [
    "https://example.com/ai-news-1",
    "https://example.com/ai-news-2",
    "https://example.com/ai-news-3",
]
SAMPLE_URLS_B = [
    "https://example.com/dev-news-1",
    "https://example.com/ai-news-1",  # 与 a 重叠
    "https://example.com/dev-news-2",
]


def collector_a() -> list[RawMaterial]:
    """模拟一个采集源（每次返回固定 URL 列表）。"""
    return [
        RawMaterial(url=u, title=f"[aihot] {u}", snippet="AI news snippet", source="aihot")
        for u in SAMPLE_URLS_A
    ]


def collector_b() -> list[RawMaterial]:
    """模拟另一个采集源（含重叠 URL）。"""
    return [
        RawMaterial(url=u, title=f"[github] {u}", snippet="dev news", source="github")
        for u in SAMPLE_URLS_B
    ]


# ---------- Mock 打分器 ----------

KEYWORDS_HIGH = {"ai-news-1", "ai-news-2"}


def mock_scorer(materials: list[StandardMaterial]) -> list[ScoredMaterial]:
    """URL 含关键词打分 80，否则随机 0-60。"""
    scored: list[ScoredMaterial] = []
    for m in materials:
        base = 80.0 if any(k in m.url for k in KEYWORDS_HIGH) else random.uniform(0, 60)
        scored.append(
            ScoredMaterial(
                url=m.url,
                title=m.title,
                snippet=m.snippet,
                content=m.content,
                source=m.source,
                publish_time=m.publish_time,
                category=m.category,
                extra_data=m.extra_data,
                heat_score=base,
                score_reason=f"mock: {'keyword hit' if base >= 70 else 'low signal'}",
            )
        )
    return scored


# ---------- 主流程 ----------


def demo_pipeline() -> None:
    """方式 1：Pipeline.run() — 不依赖 langgraph"""
    logger.info("=" * 50)
    logger.info("Demo 1: Pipeline.run() (顺序)")
    logger.info("=" * 50)

    dedup = CrossRunDedup(path=Path("output/demo_dedup.json"))
    dedup.clear()  # demo 重置

    pipeline = Pipeline(
        collectors=[
            Collector("aihot", collector_a),
            Collector("github", collector_b),
        ],
        dedup=dedup,
        scorer=mock_scorer,
        min_score=60.0,
    )

    result = pipeline.run()
    pipeline.save_dedup()

    logger.info(f"采集 {result.total_collected} 条 → 去重 {result.total_after_dedup} 条 → 打分 {len(result.scored)} 条")
    logger.info(f"耗时 {result.elapsed_seconds:.3f}s")
    logger.info(f"per_source: {result.per_source_counts}")

    # 落盘
    path = persist_materials(
        result.scored,
        output_dir=Path("output"),
        filename="demo_scored.json",
    )
    logger.info(f"落盘 → {path}")

    # 二次运行验证去重
    logger.info("-" * 30)
    logger.info("二次运行（验证去重生效）")
    result2 = pipeline.run()
    pipeline.save_dedup()
    logger.info(f"第二次：采集 {result2.total_collected} 条 → 去重后 {result2.total_after_dedup} 条（应等于 0）")


def demo_dag() -> None:
    """方式 2：build_collect_graph() — langgraph DAG"""
    logger.info("=" * 50)
    logger.info("Demo 2: build_collect_graph() (langgraph DAG)")
    logger.info("=" * 50)

    try:
        from collect_pipeline.dag import build_collect_graph
    except ImportError as e:
        logger.warning(f"langgraph 未装，跳过 DAG demo: {e}")
        return

    dedup = CrossRunDedup(path=Path("output/demo_dedup_dag.json"))
    dedup.clear()

    # 自定义 sink：只打印高分
    def sink(state) -> dict:
        # langgraph state 可能是 pydantic 或 dict
        items = getattr(state, "scored_materials", None)
        if items is None and isinstance(state, dict):
            items = state.get("scored_materials", [])
        high = [s for s in (items or []) if s.heat_score >= 70]
        logger.info(f"[sink] 高分 {len(high)} 条：")
        for s in high[:5]:
            logger.info(f"  - {s.title[:60]} (score={s.heat_score:.0f})")
        return {"sink_message": f"已记录 {len(high)} 条高分"}

    graph = build_collect_graph(
        collectors={"aihot": collector_a, "github": collector_b},
        scorer=mock_scorer,
        sink=sink,
        dedup=dedup,
    )

    out = graph.invoke({"max_per_source": 50, "min_heat_score": 60.0})
    logger.info(f"DAG 输出 keys: {list(out.keys())}")
    dedup.save()


if __name__ == "__main__":
    Path("output").mkdir(exist_ok=True)
    demo_pipeline()
    print()
    demo_dag()