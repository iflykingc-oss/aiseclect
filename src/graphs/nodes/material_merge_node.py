"""
素材合并节点
合并5路采集节点的素材并进行标准化处理
"""
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import (
    MaterialMergeInput,
    MaterialMergeOutput,
    RawMaterial,
    StandardMaterial
)


def material_merge_node(
    state: MaterialMergeInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> MaterialMergeOutput:
    """
    title: 素材合并
    desc: 合并各采集源的素材并进行标准化处理
    """
    ctx = runtime.context
    
    # 合并所有采集源的素材
    all_materials: List[RawMaterial] = []
    all_materials.extend(state.aihot_materials)
    all_materials.extend(state.ainews_materials)
    all_materials.extend(state.rss_materials)
    all_materials.extend(state.tavily_materials)
    all_materials.extend(state.github_materials)
    
    # 标准化处理
    merged_materials: List[StandardMaterial] = []
    
    for raw in all_materials:
        # 根据来源自动分类
        category = "未分类"
        if raw.source == "github":
            category = "开源项目"
        elif raw.source == "aihot":
            category = "行业热点"
        elif raw.source == "ainews":
            category = "技术突破"
        elif raw.source == "rss":
            category = "社区动态"
        elif raw.source == "tavily":
            category = "综合资讯"
        
        standard = StandardMaterial(
            url=raw.url,
            title=raw.title,
            snippet=raw.snippet,
            source=raw.source,
            publish_time=raw.publish_time,
            category=category,
            content=None  # 清洗节点会填充
        )
        merged_materials.append(standard)
    
    return MaterialMergeOutput(
        merged_materials=merged_materials,
        total_count=len(merged_materials)
    )