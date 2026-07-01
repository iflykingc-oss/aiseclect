"""
去重过滤节点
比对飞书表格历史链接，过滤重复素材
"""
import json
import logging
from typing import List, Set
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import DedupFilterInput, DedupFilterOutput, StandardMaterial
from cozeloop.decorator import observe
from coze_workload_identity import Client
import requests

logger = logging.getLogger(__name__)


def dedup_filter_node(
    state: DedupFilterInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> DedupFilterOutput:
    """
    title: 去重过滤
    desc: 比对飞书表格历史链接，过滤重复素材
    integrations: Feishu Base
    """
    ctx = runtime.context
    
    # 1. 从飞书表格查询历史链接（搜索记录）
    # 使用飞书多维表格API的search_record方法
    from cozeloop.decorator import observe
    from coze_workload_identity import Client
    import requests
    
    client_identity = Client()
    
    # 获取飞书表格历史链接
    historical_urls: Set[str] = set()
    
    # 尝试获取飞书凭证（防御性编程）
    access_token = ""
    try:
        access_token = client_identity.get_integration_credential("integration-feishu-base")
    except Exception as e:
        logger.warning(f"飞书凭证获取失败，将仅进行内存去重: {str(e)}")
        # 如果凭证缺失，仅进行内存去重（同批次URL去重）
        seen_urls: Set[str] = set()
        deduplicated_materials: List[StandardMaterial] = []
        duplicates_count = 0
        
        for material in state.merged_materials:
            if material.url in seen_urls:
                duplicates_count += 1
            else:
                deduplicated_materials.append(material)
                seen_urls.add(material.url)
        
        return DedupFilterOutput(
            deduplicated_materials=deduplicated_materials,
            duplicates_count=duplicates_count,
            new_count=len(deduplicated_materials)
        )
    
    # 如果有凭证，尝试查询飞书表格历史链接
    if access_token:
        try:
            # 构建飞书API请求
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=utf-8"
            }
            
            base_url = "https://open.larkoffice.com/open-apis"
            
            # 搜索所有记录以获取历史链接
            search_url = f"{base_url}/bitable/v1/apps/{state.feishu_app_token}/tables/{state.feishu_table_id}/records/search"
            
            # 查询所有记录（只获取链接字段）
            search_body = {
                "field_names": ["链接"],  # 只查询链接字段
                "page_size": 500  # 最大500条
            }
            
            @observe
            def search_feishu_records():
                resp = requests.post(
                    search_url,
                    headers=headers,
                    json=search_body,
                    timeout=30
                )
                return resp.json()
            
            search_result = search_feishu_records()
            
            if search_result.get("code") == 0:
                items = search_result.get("data", {}).get("items", [])
                for item in items:
                    fields = item.get("fields", {})
                    url_value = fields.get("链接", "")
                    if url_value and isinstance(url_value, str):
                        historical_urls.add(url_value)
            else:
                # 如果查询失败，记录错误但不中断流程
                logger.warning(f"飞书表格查询失败: {search_result.get('msg')}")
        
        except Exception as e:
            logger.error(f"飞书表格查询异常: {str(e)}")
            # 异常情况下也继续执行（仅做内存去重）
    
    # 2. 去重过滤
    deduplicated_materials: List[StandardMaterial] = []
    duplicates_count = 0
    
    for material in state.merged_materials:
        if material.url in historical_urls:
            duplicates_count += 1
            continue
        else:
            deduplicated_materials.append(material)
            historical_urls.add(material.url)  # 防止同批次重复
    
    return DedupFilterOutput(
        deduplicated_materials=deduplicated_materials,
        duplicates_count=duplicates_count,
        new_count=len(deduplicated_materials)
    )