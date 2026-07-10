"""langgraph DAG 编排（可选，需要 `pip install langgraph`）

工厂函数 build_collect_graph() 返回通用化的采集图：

    [fan-in: collectors...]
        ↓
    material_merge
        ↓
    dedup_filter
        ↓
    heat_scorer
        ↓
    (cluster → enrich → clean)   ← 可选中间节点
        ↓
    sink (你的写入节点，例如飞书 Bitable)

用法：
    from collect_pipeline.dag import build_collect_graph

    graph = build_collect_graph(
        collectors={"aihot": aihot_fn, "github": gh_fn},
        scorer=my_scorer,
        dedup_path="./dedup_state.json",
    )
    out = graph.invoke({"max_per_source": 10, "min_heat_score": 18.0})
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from .dedup import CrossRunDedup
from .models import (
    RawMaterial,
    ScoredMaterial,
    StandardMaterial,
)

logger = logging.getLogger(__name__)


def _check_langgraph():
    """延迟导入 langgraph，没装就给清晰报错。"""
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
        return StateGraph, END
    except ImportError as e:
        raise ImportError(
            "collect_pipeline.dag 需要 langgraph，请 `pip install langgraph` 后再使用。"
            "如果你不需要 DAG 编排，直接用 collect_pipeline.Pipeline 即可。"
        ) from e


# ---------- 节点函数（无状态、可独立调用）----------


def _state_get(state, key: str, default=None):
    """兼容 pydantic 模型和 dict 两种 state。"""
    if hasattr(state, key):
        return getattr(state, key, default)
    if isinstance(state, dict):
        return state.get(key, default)
    return default


def make_collector_node(name: str, fn: Callable[[], List[RawMaterial]]):
    """生成一个 collector 节点函数。返回 Input/Output 类型一致的 dict。"""
    def node(state) -> dict:
        try:
            items = fn() or []
            max_n = _state_get(state, "max_per_source", 50) or 50
            items = items[:max_n]
        except Exception as e:
            logger.error(f"[{name}] 采集失败: {e}")
            items = []
        # 不写 per_source_counts（langgraph 不支持多 collector 并写同一 key）
        return {f"{name}_materials": items}

    node.__name__ = f"collector_{name}"
    return node


def make_merge_node(source_keys: List[str]):
    """合并多个 collector 输出为 merged_materials。"""
    def node(state) -> dict:
        merged: List[StandardMaterial] = []
        for k in source_keys:
            items = _state_get(state, k, []) or []
            for m in items:
                merged.append(
                    StandardMaterial(
                        url=m.url,
                        title=m.title,
                        snippet=m.snippet,
                        content=m.content,
                        source=m.source,
                        publish_time=m.publish_time,
                        extra_data=m.extra_data,
                    )
                )
        return {"merged_materials": merged, "total_collected": len(merged)}

    node.__name__ = "material_merge"
    return node


def make_dedup_node(dedup: CrossRunDedup):
    """用 dedup 过滤已见 URL（含 batch 内重复），返回新素材 + 统计。"""
    def node(state) -> dict:
        merged = _state_get(state, "merged_materials", []) or []
        if _state_get(state, "clear_dedup", False):
            dedup.clear()
        new_urls = dedup.filter_new(m.url for m in merged)
        new_set = set(new_urls)
        # 同一 URL 在 batch 内只保留首次；后续算 batch 内重复
        kept: List = []
        seen_in_batch: set = set()
        for m in merged:
            if m.url not in new_set:
                continue
            if m.url in seen_in_batch:
                continue
            kept.append(m)
            seen_in_batch.add(m.url)
        dedup.add(new_urls)
        return {
            "deduplicated_materials": kept,
            "duplicates_count": len(merged) - len(kept),
            "total_after_dedup": len(kept),
        }

    node.__name__ = "dedup_filter"
    return node


def make_score_node(scorer: Callable[[List[StandardMaterial]], List[ScoredMaterial]]):
    """调用外部 scorer。scorer 返回值需与 input 同长度（按 url 对齐）。"""
    def node(state) -> dict:
        deduped = _state_get(state, "deduplicated_materials", []) or []
        scored = scorer(deduped)
        # 若 scorer 返回空（失败），按 0 分兜底
        if not scored and deduped:
            scored = [
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
                    score_reason="scorer 未返回结果",
                )
                for m in deduped
            ]
        min_score = _state_get(state, "min_heat_score", 0.0) or 0.0
        return {
            "scored_materials": scored,
            "total_after_score": sum(1 for s in scored if s.heat_score >= min_score),
        }

    node.__name__ = "heat_scorer"
    return node


def make_sink_node(sink: Callable[[dict], dict]):
    """终态节点：把 scored_materials 喂给 sink（如飞书写入）。"""
    def node(state: dict) -> dict:
        return sink(state)

    node.__name__ = "sink"
    return node


# ---------- 工厂：组装 DAG ----------


def build_collect_graph(
    collectors: Dict[str, Callable[[], List[RawMaterial]]],
    scorer: Callable[[List[StandardMaterial]], List[ScoredMaterial]],
    sink: Optional[Callable[[dict], dict]] = None,
    dedup: Optional[CrossRunDedup] = None,
    *,
    state_schema: Optional[type] = None,
    graph_input: Optional[type] = None,
):
    """组装一个 langgraph StateGraph。

    Args:
        collectors: {节点名: 采集函数}，多路并行
        scorer: 接受 List[StandardMaterial]，返回 List[ScoredMaterial]
        sink: 终态处理（飞书写入等），签名 (state: dict) -> dict
        dedup: CrossRunDedup 实例（默认 new 一个）
        state_schema / graph_input: 可选自定义 State pydantic 模型

    Returns:
        编译后的 langgraph 可执行图
    """
    StateGraph, END = _check_langgraph()

    if not collectors:
        raise ValueError("collectors 不能为空")

    dedup = dedup or CrossRunDedup()
    source_keys = [f"{name}_materials" for name in collectors]

    # 默认 state/input（用户可替换为自己的 pydantic 类）
    if state_schema is None or graph_input is None:
        # 用 TypedDict 动态定义 state，包含所有 collector 的 output key
        from typing import TypedDict

        def _make_state_cls():
            attrs = {
                "max_per_source": int,
                "min_heat_score": float,
                "clear_dedup": bool,
                "merged_materials": list,
                "deduplicated_materials": list,
                "scored_materials": list,
                "total_collected": int,
                "duplicates_count": int,
                "total_after_dedup": int,
                "total_after_score": int,
                "per_source_counts": dict,
            }
            for name in collectors:
                attrs[f"{name}_materials"] = list
            return TypedDict("_DefaultState", attrs)  # type: ignore[misc]

        state_schema = state_schema or _make_state_cls()
        graph_input = graph_input or state_schema

    builder = StateGraph(state_schema, input_schema=graph_input)

    # 1. collector 节点
    for name, fn in collectors.items():
        builder.add_node(name, make_collector_node(name, fn))

    # 2. merge 节点（fan-in）
    builder.add_node("material_merge", make_merge_node(source_keys))

    # 3. dedup 节点
    builder.add_node("dedup_filter", make_dedup_node(dedup))

    # 4. score 节点
    builder.add_node("heat_scorer", make_score_node(scorer))

    # 5. sink 节点（可选）
    has_sink = sink is not None
    if has_sink:
        builder.add_node("sink", make_sink_node(sink))

    # 边：所有 collector 并行 fan-in 到 material_merge
    # langgraph 不支持 set_entry_point/add_edge 接受列表，用 START 节点 + 多边扇出
    from langgraph.graph import START  # type: ignore
    for name in collectors:
        builder.add_edge(START, name)  # 每个 collector 都是入口
    for name in collectors:
        builder.add_edge(name, "material_merge")  # fan-in 到 merge
    builder.add_edge("material_merge", "dedup_filter")
    builder.add_edge("dedup_filter", "heat_scorer")
    builder.add_edge("heat_scorer", "sink" if has_sink else END)
    if has_sink:
        builder.add_edge("sink", END)

    graph = builder.compile()
    logger.info(f"collect_pipeline DAG 构建完成：{len(collectors)} 路 collector → merge → dedup → score → {'sink' if has_sink else 'END'}")
    return graph