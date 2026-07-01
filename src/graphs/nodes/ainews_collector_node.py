"""
AI-News雷达采集节点
通过Web搜索技能采集AI新闻平台的资讯
"""
import json
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import SearchClient
from graphs.state import AINewsCollectorInput, AINewsCollectorOutput, RawMaterial


def ainews_collector_node(
    state: AINewsCollectorInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> AINewsCollectorOutput:
    """
    title: AI-News雷达采集
    desc: 从AI新闻平台采集最新行业资讯
    integrations: Web Search
    """
    ctx = runtime.context
    
    client = SearchClient(ctx=ctx)
    
    # 搜索AI新闻（专注于新闻类站点）
    response = client.search(
        query="AI news machine learning deep learning breakthrough",
        search_type="web",
        count=20,
        need_url=True,
        sites="venturebeat.com,arxiv.org,openai.com,blog.google",
        need_summary=True,
        time_range="3d"  # 最近3天
    )
    
    materials: List[RawMaterial] = []
    
    if response.web_items:
        for item in response.web_items:
            material = RawMaterial(
                url=item.url,
                title=item.title,
                snippet=item.snippet or "",
                source="ainews",
                publish_time=item.publish_time,
                extra_data={
                    "site_name": item.site_name,
                    "auth_info_level": item.auth_info_level,
                    "summary": item.summary
                }
            )
            materials.append(material)
    
    return AINewsCollectorOutput(materials=materials)