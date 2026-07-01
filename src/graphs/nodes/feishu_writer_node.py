"""
飞书表格写入节点
将推文草稿写入飞书多维表格
"""
import json
import logging
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import FeishuWriterInput, FeishuWriterOutput, TweetDraft
from cozeloop.decorator import observe
from coze_workload_identity import Client
import requests

logger = logging.getLogger(__name__)


def feishu_writer_node(
    state: FeishuWriterInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> FeishuWriterOutput:
    """
    title: 飞书表格写入
    desc: 将推文草稿写入飞书多维表格，包含唯一ID、链接、标题、分类、热度评分、处理状态等字段
    integrations: Feishu Base
    """
    ctx = runtime.context
    
    added_record_ids: List[str] = []
    
    if not state.tweet_drafts:
        return FeishuWriterOutput(
            added_record_ids=added_record_ids,
            added_count=0
        )
    
    # 获取飞书凭证
    client_identity = Client()
    access_token = ""
    
    try:
        access_token = client_identity.get_integration_credential("integration-feishu-base")
    except Exception as e:
        logger.warning(f"飞书凭证获取失败，跳过写入操作: {str(e)}")
        # 如果凭证缺失，返回空结果但不中断流程
        return FeishuWriterOutput(
            added_record_ids=added_record_ids,
            added_count=0
        )
    
    if not access_token:
        logger.warning("飞书凭证为空，跳过写入操作")
        return FeishuWriterOutput(
            added_record_ids=added_record_ids,
            added_count=0
        )
    
    try:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        
        base_url = "https://open.larkoffice.com/open-apis"
        
        # 批量新增记录
        add_url = f"{base_url}/bitable/v1/apps/{state.feishu_app_token}/tables/{state.feishu_table_id}/records/batch_create"
        
        # 构建记录列表
        records = []
        for draft in state.tweet_drafts:
            record = {
                "fields": {
                    "唯一ID": draft.unique_id,
                    "链接": draft.url,
                    "标题": draft.title,
                    "分类": draft.category,
                    "热度评分": draft.heat_score,
                    "推文内容": draft.tweet_content,
                    "独立观点": draft.viewpoint,
                    "处理状态": draft.status
                }
            }
            records.append(record)
        
        # 调用飞书API批量写入
        @observe
        def add_feishu_records():
            resp = requests.post(
                add_url,
                headers=headers,
                json={"records": records},
                timeout=30
            )
            return resp.json()
        
        add_result = add_feishu_records()
        
        if add_result.get("code") == 0:
            # 提取新增记录ID
            created_records = add_result.get("data", {}).get("records", [])
            for record in created_records:
                record_id = record.get("record_id", "")
                if record_id:
                    added_record_ids.append(record_id)
        else:
            logger.error(f"飞书表格写入失败: {add_result.get('msg')}")
    
    except Exception as e:
        logger.error(f"飞书表格写入异常: {str(e)}")
    
    return FeishuWriterOutput(
        added_record_ids=added_record_ids,
        added_count=len(added_record_ids)
    )