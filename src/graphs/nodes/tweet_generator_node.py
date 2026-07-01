"""
推文生成节点
使用大模型生成280字符内的中文推文草稿
"""
import os
import json
import time
import random
import logging
from typing import List
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import LLMClient
from langchain_core.messages import HumanMessage
from graphs.state import TweetGeneratorInput, TweetGeneratorOutput, ScoredMaterial, TweetDraft

logger = logging.getLogger(__name__)


def tweet_generator_node(
    state: TweetGeneratorInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> TweetGeneratorOutput:
    """
    title: 推文生成
    desc: 使用大模型生成280字符内的中文推文草稿（包含独立观点）
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
            "content": mat.content or mat.snippet,
            "source": mat.source,
            "category": mat.category,
            "heat_score": mat.heat_score
        })
    
    materials_json = json.dumps(materials_data, ensure_ascii=False, indent=2)
    
    # 使用jinja2模板渲染用户提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render({"materials_json": materials_json})
    
    # 调用LLM生成推文
    client = LLMClient(ctx=ctx)
    
    messages = [
        HumanMessage(content=sp),  # 系统提示词
        HumanMessage(content=user_prompt)  # 用户提示词
    ]
    
    # 使用配置中的模型参数（启用思考模式）
    response = client.invoke(
        messages=messages,
        model=llm_config.get("model", "doubao-seed-2-0-pro-260215"),
        temperature=llm_config.get("temperature", 0.7),
        top_p=llm_config.get("top_p", 0.95),
        max_completion_tokens=llm_config.get("max_completion_tokens", 8192),
        thinking=llm_config.get("thinking", "enabled")
    )
    
    # 解析LLM返回的推文草稿
    tweet_drafts: List[TweetDraft] = []
    
    try:
        # 处理response.content
        content_text = ""
        if isinstance(response.content, str):
            content_text = response.content.strip()
        elif isinstance(response.content, list):
            for item in response.content:
                if isinstance(item, str):
                    content_text += item
                elif isinstance(item, dict) and item.get("type") == "text":
                    content_text += item.get("text", "")
        
        # 提取JSON部分
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
        
        tweet_results = json.loads(json_str)
        
        # 构建推文草稿对象
        for tweet_data in tweet_results:
            # 如果没有unique_id，生成一个
            unique_id = tweet_data.get("unique_id", "")
            if not unique_id:
                timestamp = int(time.time())
                rand_num = random.randint(1000, 9999)
                unique_id = f"tweet_{timestamp}_{rand_num}"
            
            tweet_draft = TweetDraft(
                unique_id=unique_id,
                url=tweet_data.get("url", ""),
                title=tweet_data.get("title", ""),
                category=tweet_data.get("category", "未分类"),
                heat_score=float(tweet_data.get("heat_score", 0)),
                tweet_content=tweet_data.get("tweet_content", ""),
                viewpoint=tweet_data.get("viewpoint", ""),
                xiaohongshu_title=tweet_data.get("xiaohongshu_title", ""),
                xiaohongshu_content=tweet_data.get("xiaohongshu_content", ""),
                xiaohongshu_tags=tweet_data.get("xiaohongshu_tags", []),
                status="待审核"
            )
            
            # 验证字符数（X平台280字符限制）
            content_length = len(tweet_draft.tweet_content)
            if content_length > 280:
                # 截断并添加省略号
                tweet_draft.tweet_content = tweet_draft.tweet_content[:277] + "..."
            
            # 验证小红书内容长度（200-300字）
            xiaohongshu_length = len(tweet_draft.xiaohongshu_content)
            if xiaohongshu_length > 300:
                tweet_draft.xiaohongshu_content = tweet_draft.xiaohongshu_content[:297] + "..."
            
            tweet_drafts.append(tweet_draft)
    
    except Exception as e:
        logger.error(f"推文结果解析失败: {str(e)}")
        # 如果解析失败，为每个素材生成默认推文
        for mat in state.materials:
            timestamp = int(time.time())
            rand_num = random.randint(1000, 9999)
            unique_id = f"tweet_{timestamp}_{rand_num}"
            
            # 使用标题和摘要生成简单推文
            simple_tweet = f"{mat.title[:200]} {mat.snippet[:60]}"
            if len(simple_tweet) > 280:
                simple_tweet = simple_tweet[:277] + "..."
            
            tweet_draft = TweetDraft(
                unique_id=unique_id,
                url=mat.url,
                title=mat.title,
                category=mat.category,
                heat_score=mat.heat_score,
                tweet_content=simple_tweet,
                viewpoint="自动生成（解析失败）",
                xiaohongshu_title=f"AI资讯：{mat.title[:50]}",
                xiaohongshu_content=f"💡 {mat.title}\n\n{mat.snippet}\n\n#AI #人工智能 #科技资讯",
                xiaohongshu_tags=["AI", "人工智能", "科技资讯"],
                status="待审核"
            )
            tweet_drafts.append(tweet_draft)
    
    # 统计小红书内容数量
    xiaohongshu_count = len([t for t in tweet_drafts if t.xiaohongshu_content])
    
    return TweetGeneratorOutput(
        tweet_drafts=tweet_drafts,
        total_count=len(tweet_drafts),
        xiaohongshu_count=xiaohongshu_count
    )