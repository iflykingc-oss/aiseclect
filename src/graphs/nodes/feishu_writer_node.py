"""
飞书表格写入节点
将推文草稿写入飞书多维表格，并获取表格共享链接
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
    desc: 将推文草稿写入飞书多维表格，并获取表格共享链接用于通知
    integrations: Feishu Base
    """
    ctx = runtime.context
    
    added_record_ids: List[str] = []
    feishu_table_url = ""
    
    if not state.tweet_drafts:
        return FeishuWriterOutput(
            added_record_ids=added_record_ids,
            added_count=0,
            feishu_table_url=feishu_table_url
        )
    
    # 获取飞书凭证
    client_identity = Client()
    access_token = ""
    
    try:
        access_token = client_identity.get_integration_credential("integration-feishu-base")
    except Exception as e:
        logger.warning(f"飞书凭证获取失败，跳过写入操作: {str(e)}")
        return FeishuWriterOutput(
            added_record_ids=added_record_ids,
            added_count=0,
            feishu_table_url=feishu_table_url
        )
    
    if not access_token:
        logger.warning("飞书凭证为空，跳过写入操作")
        return FeishuWriterOutput(
            added_record_ids=added_record_ids,
            added_count=0,
            feishu_table_url=feishu_table_url
        )
    
    try:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        
        base_url = "https://open.larkoffice.com/open-apis"
        
        # 1. 先获取表格的共享链接
        get_url_api = f"{base_url}/bitable/v1/apps/{state.feishu_app_token}"
        
        @observe
        def get_table_info():
            try:
                resp = requests.get(
                    get_url_api,
                    headers=headers,
                    timeout=30
                )
                return resp.json()
            except requests.exceptions.RequestException as e:
                logger.error(f"获取表格信息网络异常: {str(e)}")
                return {"code": -1, "msg": str(e)}
        
        table_info = get_table_info()
        
        if table_info.get("code") == 0:
            # 尝试从表格信息中获取URL
            app_info = table_info.get("data", {}).get("app", {})
            # 飞书表格可能没有直接的URL字段，需要构建
            # 如果有shared_url字段，使用它
            if "shared_url" in app_info:
                feishu_table_url = app_info["shared_url"]
            else:
                # 否则尝试获取第一条记录的共享链接作为替代
                logger.info("表格信息中无shared_url，将在写入记录后获取记录链接")
        
        # 2. 批量新增记录（使用with_shared_url获取记录链接）
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
        
        # 调用飞书API批量写入（带with_shared_url参数）
        @observe
        def add_feishu_records():
            try:
                resp = requests.post(
                    add_url,
                    headers=headers,
                    json={
                        "records": records,
                        "with_shared_url": True  # 获取记录的共享链接
                    },
                    timeout=30
                )
                return resp.json()
            except requests.exceptions.RequestException as e:
                logger.error(f"写入记录网络异常: {str(e)}")
                return {"code": -1, "msg": str(e)}
        
        add_result = add_feishu_records()
        
        if add_result.get("code") == 0:
            # 提取新增记录ID和共享链接
            created_records = add_result.get("data", {}).get("records", [])
            for record in created_records:
                record_id = record.get("record_id", "")
                if record_id:
                    added_record_ids.append(record_id)
                
                # 如果表格URL仍为空，使用第一条记录的共享链接
                if not feishu_table_url and "shared_url" in record:
                    feishu_table_url = record.get("shared_url", "")
                    logger.info(f"使用第一条记录的共享链接作为表格URL: {feishu_table_url}")
            
            logger.info(f"飞书表格写入成功: {len(added_record_ids)}条记录")
        else:
            error_msg = add_result.get("msg", "未知错误")
            logger.error(f"飞书表格写入失败: {error_msg}")
    
    except Exception as e:
        logger.error(f"飞书表格写入异常: {str(e)}")
    
    # 如果最终仍无表格链接，返回空字符串（通知节点会处理）
    if not feishu_table_url:
        logger.warning("未能获取飞书表格共享链接")
    
    return FeishuWriterOutput(
        added_record_ids=added_record_ids,
        added_count=len(added_record_ids),
        feishu_table_url=feishu_table_url
    )