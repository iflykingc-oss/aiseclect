"""
GitHub Trending采集节点
采集GitHub上热门AI项目
"""
import json
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import SearchClient
from graphs.state import GitHubCollectorInput, GitHubCollectorOutput, RawMaterial


def github_collector_node(
    state: GitHubCollectorInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> GitHubCollectorOutput:
    """
    title: GitHub Trending采集
    desc: 采集GitHub上热门AI相关项目
    integrations: Web Search
    """
    ctx = runtime.context
    
    client = SearchClient(ctx=ctx)
    
    # 搜索GitHub热门AI项目
    response = client.search(
        query="AI machine learning deep learning trending repositories",
        search_type="web",
        count=15,
        need_url=True,
        sites="github.com",
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
                source="github",
                publish_time=item.publish_time,
                extra_data={
                    "site_name": item.site_name,
                    "auth_info_level": item.auth_info_level,
                    "summary": item.summary
                }
            )
            materials.append(material)
    
    return GitHubCollectorOutput(github_materials=materials)