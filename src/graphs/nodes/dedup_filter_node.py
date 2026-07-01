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
    desc: 比对飞书表格历史链接，过滤重复素材（飞书集成未授权时自动降级为内存去重）
    integrations: Feishu Base
    """
    ctx = runtime.context
    
    # 内存去重辅助函数（在飞书集成不可用时使用）
    def memory_dedup(materials: List[StandardMaterial]) -> DedupFilterOutput:
        seen_urls: Set[str] = set()
        deduplicated: List[StandardMaterial] = []
        duplicates = 0
        
        for material in materials:
            if material.url in seen_urls:
                duplicates += 1
            else:
                deduplicated.append(material)
                seen_urls.add(material.url)
        
        return DedupFilterOutput(
            deduplicated_materials=deduplicated,
            duplicates_count=duplicates,
            new_count=len(deduplicated)
        )
    
    # 1. 尝试获取飞书凭证
    client_identity = Client()
    access_token = ""
    
    try:
        access_token = client_identity.get_integration_credential("integration-feishu-base")
    except Exception as e:
        logger.warning(f"飞书凭证获取失败（集成未授权），将仅进行内存去重: {str(e)}")
        return memory_dedup(state.merged_materials)
    
    if not access_token:
        logger.warning("飞书凭证为空（集成未配置），将仅进行内存去重")
        return memory_dedup(state.merged_materials)
    
    # 2. 如果有凭证，尝试查询飞书表格历史链接
    historical_urls: Set[str] = set()
    
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
            try:
                resp = requests.post(
                    search_url,
                    headers=headers,
                    json=search_body,
                    timeout=30
                )
                return resp.json()
            except requests.exceptions.RequestException as e:
                logger.error(f"飞书API调用网络异常: {str(e)}")
                return {"code": -1, "msg": str(e)}
        
        search_result = search_feishu_records()
        
        # 检查API响应状态
        if search_result.get("code") != 0:
            error_msg = search_result.get("msg", "未知错误")
            logger.warning(f"飞书表格查询失败（code={search_result.get('code')}）: {error_msg}")
            # API调用失败时，降级为内存去重
            return memory_dedup(state.merged_materials)
        
        # 提取历史链接
        items = search_result.get("data", {}).get("items", [])
        for item in items:
            fields = item.get("fields", {})
            url_value = fields.get("链接", "")
            if url_value and isinstance(url_value, str):
                historical_urls.add(url_value)
        
        logger.info(f"成功获取飞书表格历史链接: {len(historical_urls)}条")
    
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            logger.warning("飞书API调用401未授权，将仅进行内存去重")
        else:
            logger.error(f"飞书API调用HTTP错误: {str(e)}")
        return memory_dedup(state.merged_materials)
    
    except Exception as e:
        logger.error(f"飞书表格查询异常: {str(e)}")
        return memory_dedup(state.merged_materials)
    
    # 3. 使用历史链接进行去重过滤
    deduplicated_materials: List[StandardMaterial] = []
    duplicates_count = 0
    
    for material in state.merged_materials:
        if material.url in historical_urls:
            duplicates_count += 1
            continue
        else:
            deduplicated_materials.append(material)
            historical_urls.add(material.url)  # 防止同批次重复
    
    logger.info(f"去重完成: 原始{len(state.merged_materials)}条，去重后{len(deduplicated_materials)}条，重复{duplicates_count}条")
    
    return DedupFilterOutput(
        deduplicated_materials=deduplicated_materials,
        duplicates_count=duplicates_count,
        new_count=len(deduplicated_materials)
    )