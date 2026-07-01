"""
飞书表格写入节点 - 将推文草稿写入飞书多维表格
"""
import json
import os
import logging
import requests
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from cozeloop.decorator import observe
from coze_workload_identity import Client
from graphs.state import FeishuWriterInput, FeishuWriterOutput, TweetDraft

# 初始化日志
logger = logging.getLogger(__name__)


def feishu_writer_node(state: FeishuWriterInput, config: RunnableConfig, runtime: Runtime[Context]) -> FeishuWriterOutput:
    """
    title: 飞书表格写入
    desc: 将生成的推文草稿写入飞书多维表格，并返回表格共享链接
    integrations: Feishu Base
    """
    ctx = runtime.context
    
    # 结果初始化
    added_record_ids: List[str] = []
    feishu_table_url: str = ""
    
    logger.info(f"飞书写入节点启动，草稿数量: {len(state.tweet_drafts)}")
    
    # 如果没有草稿，直接返回
    if not state.tweet_drafts:
        logger.info("无推文草稿，跳过写入操作")
        return FeishuWriterOutput(
            added_record_ids=added_record_ids,
            added_count=0,
            feishu_table_url=feishu_table_url
        )
    
    # 检查飞书表格标识
    if not state.feishu_table_id:
        logger.warning("飞书表格ID为空，跳过写入操作")
        return FeishuWriterOutput(
            added_record_ids=added_record_ids,
            added_count=0,
            feishu_table_url=feishu_table_url
        )
    
    # 获取飞书凭证
    try:
        client_identity = Client()
        access_token = client_identity.get_integration_credential("integration-feishu-base")
        logger.info("飞书凭证获取成功")
    except Exception as e:
        logger.warning(f"飞书凭证获取失败（集成未授权）: {str(e)}")
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
        
        # Wiki内嵌表格处理：如果feishu_app_token为空，使用feishu_page_id作为标识
        app_token = state.feishu_app_token if state.feishu_app_token else state.feishu_page_id
        
        if not app_token:
            logger.warning("飞书App Token和Page ID都为空，跳过写入操作")
            return FeishuWriterOutput(
                added_record_ids=added_record_ids,
                added_count=0,
                feishu_table_url=feishu_table_url
            )
        
        # 1. 先获取表格的共享链接（如果是独立表格）
        if not state.is_wiki_embed:
            get_url_api = f"{base_url}/bitable/v1/apps/{app_token}"
            
            @observe
            def get_table_info():
                try:
                    resp = requests.get(
                        get_url_api,
                        headers=headers,
                        timeout=30
                    )
                    # 先检查响应状态码
                    if resp.status_code != 200:
                        logger.error(f"获取表格信息HTTP错误: {resp.status_code}")
                        return {"code": resp.status_code, "msg": f"HTTP {resp.status_code}: {resp.text[:200]}"}
                    
                    # 检查响应内容是否为JSON
                    content_type = resp.headers.get('Content-Type', '')
                    if 'application/json' not in content_type:
                        logger.error(f"飞书API返回非JSON响应: {content_type}")
                        return {"code": -1, "msg": f"非JSON响应: {content_type}, 内容: {resp.text[:200]}"}
                    
                    return resp.json()
                except json.JSONDecodeError as e:
                    logger.error(f"获取表格信息JSON解析错误: {str(e)}")
                    return {"code": -1, "msg": f"JSON解析错误: {str(e)}"}
                except requests.exceptions.RequestException as e:
                    logger.error(f"获取表格信息网络异常: {str(e)}")
                    return {"code": -1, "msg": str(e)}
            
            table_info = get_table_info()
            
            if table_info.get("code") == 0:
                # 尝试从表格信息中获取URL
                app_info = table_info.get("data", {}).get("app", {})
                if "shared_url" in app_info:
                    feishu_table_url = app_info["shared_url"]
                    logger.info(f"获取到表格共享链接: {feishu_table_url}")
                else:
                    logger.info("表格信息中无shared_url，将在写入记录后获取记录链接")
            else:
                logger.warning(f"获取表格信息失败: {table_info.get('msg', '未知错误')}")
        
        # 2. 批量新增记录（使用with_shared_url获取记录链接）
        add_url = f"{base_url}/bitable/v1/apps/{app_token}/tables/{state.feishu_table_id}/records/batch_create"
        
        # 构建记录列表（包含双平台内容）
        records = []
        for draft in state.tweet_drafts:
            record = {
                "fields": {
                    "唯一ID": draft.unique_id,
                    "链接": draft.url,
                    "标题": draft.title,
                    "分类": draft.category,
                    "热度评分": draft.heat_score,
                    "推文内容": draft.tweet_content,  # X平台内容
                    "独立观点": draft.viewpoint,
                    "小红书标题": draft.xiaohongshu_title,  # 小红书标题
                    "小红书内容": draft.xiaohongshu_content,  # 小红书内容
                    "小红书标签": ", ".join(draft.xiaohongshu_tags) if draft.xiaohongshu_tags else "",  # 小红书标签（逗号分隔）
                    "处理状态": draft.status
                }
            }
            records.append(record)
        
        logger.info(f"准备写入{len(records)}条记录到飞书表格")
        
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
                
                # 先检查响应状态码
                if resp.status_code != 200:
                    logger.error(f"写入记录HTTP错误: {resp.status_code}")
                    # 如果是401未授权，明确提示
                    if resp.status_code == 401:
                        logger.error("飞书集成未授权（401），请在平台完成飞书多维表格集成授权")
                    return {"code": resp.status_code, "msg": f"HTTP {resp.status_code}: {resp.text[:200]}"}
                
                # 检查响应内容是否为JSON
                content_type = resp.headers.get('Content-Type', '')
                if 'application/json' not in content_type:
                    logger.error(f"飞书API返回非JSON响应: {content_type}")
                    return {"code": -1, "msg": f"非JSON响应: {content_type}, 内容: {resp.text[:200]}"}
                
                return resp.json()
            except json.JSONDecodeError as e:
                logger.error(f"写入记录JSON解析错误: {str(e)}")
                return {"code": -1, "msg": f"JSON解析错误: {str(e)}"}
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
    
    # 如果最终仍无表格链接，构建Wiki链接（如果是Wiki内嵌表格）
    if not feishu_table_url:
        if state.is_wiki_embed and state.feishu_page_id:
            feishu_table_url = f"https://{state.feishu_domain}/wiki/{state.feishu_page_id}?table={state.feishu_table_id}"
            logger.info(f"构建Wiki表格链接: {feishu_table_url}")
        else:
            logger.warning("未能获取飞书表格共享链接")
    
    return FeishuWriterOutput(
        added_record_ids=added_record_ids,
        added_count=len(added_record_ids),
        feishu_table_url=feishu_table_url
    )