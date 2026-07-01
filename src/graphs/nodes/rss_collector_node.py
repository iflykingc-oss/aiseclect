"""
RSS采集节点
通过Web搜索模拟RSS订阅源采集
"""
import json
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import SearchClient
from graphs.state import RSSCollectorInput, RSSCollectorOutput, RawMaterial


def rss_collector_node(
    state: RSSCollectorInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> RSSCollectorOutput:
    """
    title: RSS采集
    desc: 从RSS订阅源采集AI相关资讯
    integrations: Web Search
    """
    ctx = runtime.context
    
    client = SearchClient(ctx=ctx)
    
    # 搜索RSS源常见站点
    response = client.search(
        query="AI technology news RSS feeds",
        search_type="web",
        count=15,
        need_url=True,
        sites="medium.com,substack.com,reddit.com/r/MachineLearning",
        need_summary=True,
        time_range="7d"  # 最近7天
    )
    
    materials: List[RawMaterial] = []
    
    if response.web_items:
        for item in response.web_items:
            material = RawMaterial(
                url=item.url,
                title=item.title,
                snippet=item.snippet or "",
                source="rss",
                publish_time=item.publish_time,
                extra_data={
                    "site_name": item.site_name,
                    "auth_info_level": item.auth_info_level,
                    "summary": item.summary
                }
            )
            materials.append(material)
    
    return RSSCollectorOutput(rss_materials=materials)