"""
网页精读清洗节点
对高分素材进行深度清洗，提取原文核心内容
"""
import json
import logging
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import ContentCleanerInput, ContentCleanerOutput, ScoredMaterial

logger = logging.getLogger(__name__)


def content_cleaner_node(
    state: ContentCleanerInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ContentCleanerOutput:
    """
    title: 网页精读清洗
    desc: 深度清洗素材内容，提取原文核心信息
    integrations: Web Search
    """
    ctx = runtime.context
    
    # 使用Web搜索技能获取详细内容
    from coze_coding_dev_sdk import SearchClient
    
    client = SearchClient(ctx=ctx)
    
    cleaned_materials: List[ScoredMaterial] = []
    
    for material in state.materials:
        # 获取素材的详细内容（如果已有summary则使用）
        content = None
        
        # 尝试获取完整内容
        try:
            # 使用搜索API获取该URL的详细内容
            response = client.search(
                query=material.title,
                search_type="web",
                count=1,
                need_content=True,
                sites=material.url.split('/')[2] if '/' in material.url else "",
                need_url=True
            )
            
            if response.web_items and len(response.web_items) > 0:
                item = response.web_items[0]
                content = item.content or item.snippet or material.snippet
            else:
                # 如果无法获取详细内容，使用原有snippet
                content = material.snippet
        
        except Exception as e:
            logger.warning(f"获取内容失败: {material.url}, {str(e)}")
            content = material.snippet
        
        # 更新素材内容
        cleaned_material = ScoredMaterial(
            url=material.url,
            title=material.title,
            snippet=material.snippet,
            source=material.source,
            publish_time=material.publish_time,
            content=content,
            category=material.category,
            heat_score=material.heat_score,
            score_reason=material.score_reason
        )
        cleaned_materials.append(cleaned_material)
    
    return ContentCleanerOutput(cleaned_materials=cleaned_materials)