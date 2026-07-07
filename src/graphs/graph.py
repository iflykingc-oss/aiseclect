"""
工作流主图编排
- 飞书初始化 → 5 路并行采集 → 合并 → 去重 → 打分 → 事件聚类 → 清洗 → 推文生成 → 飞书写入
"""
from langgraph.graph import StateGraph, END

from graphs.state import (
    GlobalState,
    GraphInput,
    GraphOutput,
    FeishuTableInitInput,
    FeishuTableInitOutput,
    FeishuWriterInput,
    FeishuWriterOutput,
    AIHotCollectorInput,
    AIHotCollectorOutput,
    AINewsCollectorInput,
    AINewsCollectorOutput,
    RSSCollectorInput,
    RSSCollectorOutput,
    TavilyCollectorInput,
    TavilyCollectorOutput,
    GitHubCollectorInput,
    GitHubCollectorOutput,
    NewsNowCollectorInput,
    NewsNowCollectorOutput,
    MaterialMergeInput,
    MaterialMergeOutput,
    DedupFilterInput,
    DedupFilterOutput,
    HeatScorerInput,
    HeatScorerOutput,
    EventClusterInput,
    EventClusterOutput,
    ContentEnricherInput,
    ContentEnricherOutput,
    ContentCleanerInput,
    ContentCleanerOutput,
    TweetGeneratorInput,
    TweetGeneratorOutput,
)

from graphs.nodes.feishu_table_init_node import feishu_table_init_node
from graphs.nodes.aihot_collector_node import aihot_collector_node
from graphs.nodes.ainews_collector_node import ainews_collector_node
from graphs.nodes.rss_collector_node import rss_collector_node
from graphs.nodes.tavily_collector_node import tavily_collector_node
from graphs.nodes.github_collector_node import github_collector_node
from graphs.nodes.newsnow_collector_node import newsnow_collector_node
from graphs.nodes.material_merge_node import material_merge_node
from graphs.nodes.dedup_filter_node import dedup_filter_node
from graphs.nodes.heat_scorer_node import heat_scorer_node
from graphs.nodes.event_cluster_node import event_cluster_node
from graphs.nodes.content_enricher_node import content_enricher_node
from graphs.nodes.content_cleaner_node import content_cleaner_node
from graphs.nodes.tweet_generator_node import tweet_generator_node
from graphs.nodes.feishu_writer_node import feishu_writer_node


def _select(state: GlobalState, key: str):
    """从 GlobalState 取对应节点需要的输入子集。"""
    cls_map = {
        "feishu_table_init": FeishuTableInitInput,
        "aihot_collector": AIHotCollectorInput,
        "ainews_collector": AINewsCollectorInput,
        "rss_collector": RSSCollectorInput,
        "tavily_collector": TavilyCollectorInput,
        "github_collector": GitHubCollectorInput,
        "newsnow_collector": NewsNowCollectorInput,
        "material_merge": MaterialMergeInput,
        "dedup_filter": DedupFilterInput,
        "heat_scorer": HeatScorerInput,
        "event_cluster": EventClusterInput,
        "content_enricher": ContentEnricherInput,
        "content_cleaner": ContentCleanerInput,
        "tweet_generator": TweetGeneratorInput,
        "feishu_writer": FeishuWriterInput,
    }
    cls = cls_map[key]
    # 从 GlobalState 抽取该节点 Input 需要的字段
    data = {f: getattr(state, f, None) for f in cls.model_fields.keys()}
    return cls(**{k: v for k, v in data.items() if v is not None or k in cls.model_fields})


def _wrap(node_name, fn):
    """包装节点函数：从 GlobalState 抽取 Input → 调 fn → 返回 dict（langgraph 合并到 state）"""
    def wrapped(state: GlobalState):
        out = fn(_select(state, node_name))
        # 兼容 Pydantic 模型 / dict 两种返回
        if hasattr(out, "model_dump"):
            return out.model_dump(exclude_none=False)
        if isinstance(out, dict):
            return out
        return out
    wrapped.__name__ = node_name
    return wrapped


builder = StateGraph(GlobalState, input_schema=GraphInput)

# 节点
builder.add_node("feishu_table_init", _wrap("feishu_table_init", feishu_table_init_node))
builder.add_node("aihot_collector", _wrap("aihot_collector", aihot_collector_node))
builder.add_node("ainews_collector", _wrap("ainews_collector", ainews_collector_node))
builder.add_node("rss_collector", _wrap("rss_collector", rss_collector_node))
builder.add_node("tavily_collector", _wrap("tavily_collector", tavily_collector_node))
builder.add_node("github_collector", _wrap("github_collector", github_collector_node))
builder.add_node("newsnow_collector", _wrap("newsnow_collector", newsnow_collector_node))
builder.add_node("material_merge", _wrap("material_merge", material_merge_node))
builder.add_node("dedup_filter", _wrap("dedup_filter", dedup_filter_node))
builder.add_node("heat_scorer", _wrap("heat_scorer", heat_scorer_node))
builder.add_node("event_cluster", _wrap("event_cluster", event_cluster_node))
builder.add_node("content_enricher", _wrap("content_enricher", content_enricher_node))
builder.add_node("content_cleaner", _wrap("content_cleaner", content_cleaner_node))
builder.add_node("tweet_generator", _wrap("tweet_generator", tweet_generator_node))
builder.add_node("feishu_writer", _wrap("feishu_writer", feishu_writer_node))

# 边
builder.set_entry_point("feishu_table_init")
builder.add_edge("feishu_table_init", "aihot_collector")
builder.add_edge("feishu_table_init", "ainews_collector")
builder.add_edge("feishu_table_init", "rss_collector")
builder.add_edge("feishu_table_init", "tavily_collector")
builder.add_edge("feishu_table_init", "github_collector")
builder.add_edge("feishu_table_init", "newsnow_collector")

builder.add_edge(
    ["aihot_collector", "ainews_collector", "rss_collector", "tavily_collector", "github_collector", "newsnow_collector"],
    "material_merge",
)

builder.add_edge("material_merge", "dedup_filter")
builder.add_edge("dedup_filter", "heat_scorer")
builder.add_edge("heat_scorer", "event_cluster")
builder.add_edge("event_cluster", "content_enricher")
builder.add_edge("content_enricher", "content_cleaner")
builder.add_edge("content_cleaner", "tweet_generator")
builder.add_edge("tweet_generator", "feishu_writer")
builder.add_edge("feishu_writer", END)

main_graph = builder.compile()
