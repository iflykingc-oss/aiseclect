"""
Tavily搜索采集节点
使用Tavily搜索引擎采集AI资讯
"""
import json
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import SearchClient
from graphs.state import TavilyCollectorInput, TavilyCollectorOutput, RawMaterial


def tavily_collector_node(
    state: TavilyCollectorInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> TavilyCollectorOutput:
    """
    title: Tavily搜索采集
    desc: 使用Tavily搜索引擎采集最新AI资讯
    integrations: Web Search
    """
    ctx = runtime.context
    
    client = SearchClient(ctx=ctx)
    
    # 使用Tavily进行深度搜索（模拟）
    response = client.web_search_with_summary(
        query="artificial intelligence latest developments 2024",
        count=20
    )
    
    materials: List[RawMaterial] = []
    
    if response.web_items:
        for item in response.web_items:
            material = RawMaterial(
                url=item.url,
                title=item.title,
                snippet=item.snippet or "",
                source="tavily",
                publish_time=item.publish_time,
                extra_data={
                    "site_name": item.site_name,
                    "auth_info_level": item.auth_info_level,
                    "summary": item.summary,
                    "global_summary": response.summary
                }
            )
            materials.append(material)
    
    return TavilyCollectorOutput(materials=materials)