"""
AIHOT雷达采集节点
通过Web搜索技能采集AIHOT平台的最新资讯
"""
import json
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import SearchClient
from graphs.state import AIHotCollectorInput, AIHotCollectorOutput, RawMaterial


def aihot_collector_node(
    state: AIHotCollectorInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> AIHotCollectorOutput:
    """
    title: AIHOT雷达采集
    desc: 从AIHOT平台采集最新AI行业资讯
    integrations: Web Search
    """
    ctx = runtime.context
    
    # 使用Web搜索技能搜索AIHOT相关资讯
    client = SearchClient(ctx=ctx)
    
    # 搜索AI热点资讯（使用特定站点过滤）
    response = client.search(
        query="AI artificial intelligence latest news trends",
        search_type="web",
        count=20,
        need_url=True,
        sites="ai-bot.cn,aihot.net,36kr.com,techcrunch.com",
        need_summary=True,
        time_range="1d"  # 最近1天
    )
    
    materials: List[RawMaterial] = []
    
    if response.web_items:
        for item in response.web_items:
            material = RawMaterial(
                url=item.url,
                title=item.title,
                snippet=item.snippet or "",
                source="aihot",
                publish_time=item.publish_time,
                extra_data={
                    "site_name": item.site_name,
                    "auth_info_level": item.auth_info_level,
                    "summary": item.summary
                }
            )
            materials.append(material)
    
    return AIHotCollectorOutput(aihot_materials=materials)