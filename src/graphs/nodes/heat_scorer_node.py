"""
AI热度打分节点
使用大模型对素材进行热度评分
"""
import os
import json
import logging
from typing import List
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import LLMClient
from langchain_core.messages import HumanMessage
from graphs.state import HeatScorerInput, HeatScorerOutput, StandardMaterial, ScoredMaterial

logger = logging.getLogger(__name__)


def heat_scorer_node(
    state: HeatScorerInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> HeatScorerOutput:
    """
    title: AI热度打分
    desc: 使用大模型评估素材热度并输出评分结果
    integrations: LLM
    """
    ctx = runtime.context
    
    # 读取LLM配置文件
    cfg_file = os.path.join(
        os.getenv("COZE_WORKSPACE_PATH"),
        config['metadata']['llm_cfg']
    )
    
    with open(cfg_file, 'r', encoding='utf-8') as fd:
        _cfg = json.load(fd)
    
    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", "")
    up = _cfg.get("up", "")
    
    # 准备素材数据（转换为JSON）
    materials_data = []
    for mat in state.materials:
        materials_data.append({
            "url": mat.url,
            "title": mat.title,
            "snippet": mat.snippet,
            "source": mat.source,
            "publish_time": mat.publish_time,
            "category": mat.category
        })
    
    materials_json = json.dumps(materials_data, ensure_ascii=False, indent=2)
    
    # 使用jinja2模板渲染用户提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render({"materials_json": materials_json})
    
    # 调用LLM进行评分
    client = LLMClient(ctx=ctx)
    
    messages = [
        HumanMessage(content=sp),  # 系统提示词作为第一条消息
        HumanMessage(content=user_prompt)  # 用户提示词
    ]
    
    # 使用配置中的模型参数
    response = client.invoke(
        messages=messages,
        model=llm_config.get("model", "doubao-seed-2-0-lite-260215"),
        temperature=llm_config.get("temperature", 0.3),
        top_p=llm_config.get("top_p", 0.95),
        max_completion_tokens=llm_config.get("max_completion_tokens", 4096)
    )
    
    # 解析LLM返回的评分结果
    scored_materials: List[ScoredMaterial] = []
    high_score_count = 0
    
    try:
        # 处理response.content（可能是str或list）
        content_text = ""
        if isinstance(response.content, str):
            content_text = response.content.strip()
        elif isinstance(response.content, list):
            # 如果是list，提取文本部分
            for item in response.content:
                if isinstance(item, str):
                    content_text += item
                elif isinstance(item, dict) and item.get("type") == "text":
                    content_text += item.get("text", "")
        
        # 提取JSON部分（去除可能的额外文本）
        if "```json" in content_text:
            json_start = content_text.find("```json") + 7
            json_end = content_text.find("```", json_start)
            json_str = content_text[json_start:json_end].strip()
        elif "[" in content_text:
            json_start = content_text.find("[")
            json_end = content_text.rfind("]") + 1
            json_str = content_text[json_start:json_end]
        else:
            json_str = content_text
        
        score_results = json.loads(json_str)
        
        # 匹配素材和评分结果
        for i, mat in enumerate(state.materials):
            # 查找对应的评分结果
            score_data = None
            for result in score_results:
                if result.get("url") == mat.url:
                    score_data = result
                    break
            
            if score_data:
                heat_score = float(score_data.get("heat_score", 0))
                score_reason = score_data.get("score_reason", "")
                
                if heat_score >= 60:
                    high_score_count += 1
                
                scored_mat = ScoredMaterial(
                    url=mat.url,
                    title=mat.title,
                    snippet=mat.snippet,
                    source=mat.source,
                    publish_time=mat.publish_time,
                    content=mat.content,
                    category=mat.category,
                    heat_score=heat_score,
                    score_reason=score_reason
                )
                scored_materials.append(scored_mat)
            else:
                # 如果没有评分结果，使用默认值
                scored_mat = ScoredMaterial(
                    url=mat.url,
                    title=mat.title,
                    snippet=mat.snippet,
                    source=mat.source,
                    publish_time=mat.publish_time,
                    content=mat.content,
                    category=mat.category,
                    heat_score=0.0,
                    score_reason="未评分"
                )
                scored_materials.append(scored_mat)
    
    except Exception as e:
        logger.error(f"评分结果解析失败: {str(e)}")
        # 如果解析失败，所有素材使用默认评分
        for mat in state.materials:
            scored_mat = ScoredMaterial(
                url=mat.url,
                title=mat.title,
                snippet=mat.snippet,
                source=mat.source,
                publish_time=mat.publish_time,
                content=mat.content,
                category=mat.category,
                heat_score=0.0,
                score_reason=f"解析失败: {str(e)}"
            )
            scored_materials.append(scored_mat)
    
    return HeatScorerOutput(
        scored_materials=scored_materials,
        high_score_count=high_score_count
    )