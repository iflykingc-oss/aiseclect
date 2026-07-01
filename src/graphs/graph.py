"""
AI资讯采集生成X中文短推文主图编排
实现从多渠道素材采集到人工审核管理的闭环工作流
"""
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from graphs.state import (
    GlobalState,
    GraphInput,
    GraphOutput,
    
    # 采集节点
    AIHotCollectorInput,
    AINewsCollectorInput,
    RSSCollectorInput,
    TavilyCollectorInput,
    GitHubCollectorInput,
    
    # 数据处理节点
    MaterialMergeInput,
    DedupFilterInput,
    HeatScorerInput,
    ContentCleanerInput,
    TweetGeneratorInput,
    
    # 飞书集成节点
    FeishuWriterInput,
    FeishuNotifierInput
)

# 导入所有节点函数
from graphs.nodes.aihot_collector_node import aihot_collector_node
from graphs.nodes.ainews_collector_node import ainews_collector_node
from graphs.nodes.rss_collector_node import rss_collector_node
from graphs.nodes.tavily_collector_node import tavily_collector_node
from graphs.nodes.github_collector_node import github_collector_node

from graphs.nodes.material_merge_node import material_merge_node
from graphs.nodes.dedup_filter_node import dedup_filter_node
from graphs.nodes.heat_scorer_node import heat_scorer_node
from graphs.nodes.content_cleaner_node import content_cleaner_node
from graphs.nodes.tweet_generator_node import tweet_generator_node

from graphs.nodes.feishu_writer_node import feishu_writer_node
from graphs.nodes.feishu_notifier_node import feishu_notifier_node


# 创建主图
builder = StateGraph(
    GlobalState,
    input_schema=GraphInput,
    output_schema=GraphOutput
)


# ========== 添加节点 ==========

# 1. 采集节点（5个并行）
builder.add_node("aihot_collector", aihot_collector_node)
builder.add_node("ainews_collector", ainews_collector_node)
builder.add_node("rss_collector", rss_collector_node)
builder.add_node("tavily_collector", tavily_collector_node)
builder.add_node("github_collector", github_collector_node)

# 2. 素材合并节点（汇聚点）
builder.add_node("material_merge", material_merge_node)

# 3. 去重过滤节点
builder.add_node("dedup_filter", dedup_filter_node)

# 4. AI热度打分节点（Agent节点）
builder.add_node(
    "heat_scorer",
    heat_scorer_node,
    metadata={
        "type": "agent",
        "llm_cfg": "config/heat_scorer_llm_cfg.json"
    }
)

# 5. 网页精读清洗节点
builder.add_node("content_cleaner", content_cleaner_node)

# 6. 推文生成节点（Agent节点）
builder.add_node(
    "tweet_generator",
    tweet_generator_node,
    metadata={
        "type": "agent",
        "llm_cfg": "config/tweet_generator_llm_cfg.json"
    }
)

# 7. 飞书表格写入节点
builder.add_node("feishu_writer", feishu_writer_node)

# 8. 飞书机器人通知节点
builder.add_node("feishu_notifier", feishu_notifier_node)


# ========== 设置入口点 ==========

# 设置多个入口点实现并行采集
builder.set_entry_point("aihot_collector")


# ========== 添加边（并行采集 -> 汇聚 -> 串行处理）==========

# 采集节点并行执行
builder.add_edge("aihot_collector", "material_merge")
builder.add_edge("ainews_collector", "material_merge")
builder.add_edge("rss_collector", "material_merge")
builder.add_edge("tavily_collector", "material_merge")
builder.add_edge("github_collector", "material_merge")

# 汇聚节点等待所有采集节点完成
# 注意：LangGraph会自动等待所有入边完成后再执行汇聚节点

# 串行处理流程
builder.add_edge("material_merge", "dedup_filter")
builder.add_edge("dedup_filter", "heat_scorer")
builder.add_edge("heat_scorer", "content_cleaner")
builder.add_edge("content_cleaner", "tweet_generator")
builder.add_edge("tweet_generator", "feishu_writer")
builder.add_edge("feishu_writer", "feishu_notifier")
builder.add_edge("feishu_notifier", END)


# ========== 编译图 ==========

main_graph = builder.compile()