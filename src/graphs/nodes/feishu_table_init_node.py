"""
飞书表格初始化节点
自动创建飞书多维表格和数据表，以及所需的所有字段
"""

import json
import logging
import os
import requests
from typing import Dict, List, Optional, Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from cozeloop.decorator import observe
from coze_workload_identity import Client

from graphs.state import FeishuTableInitInput, FeishuTableInitOutput

logger = logging.getLogger(__name__)


class FeishuTableInitializer:
    """飞书多维表格初始化器"""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.access_token: Optional[str] = None
        self.base_url = "https://open.feishu.cn/open-apis"
        
    def get_access_token(self) -> str:
        """获取飞书多维表格的租户访问令牌"""
        try:
            client = Client()
            credential = client.get_integration_credential("integration-feishu-base")
            return credential
        except Exception as e:
            logger.error(f"获取飞书凭证失败: {e}")
            return ""
    
    def _headers(self) -> dict:
        """构建请求头"""
        token = self.access_token or ""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
    
    @observe
    def _request(self, method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None) -> dict:
        """发送飞书API请求"""
        try:
            url = f"{self.base_url}{path}"
            headers = self._headers()
            resp = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=self.timeout)
            resp.raise_for_status()
            resp_data = resp.json()
            if resp_data.get("code") != 0:
                logger.error(f"飞书API错误: {resp_data}")
                return {"success": False, "error": resp_data}
            return {"success": True, "data": resp_data.get("data", {})}
        except requests.exceptions.HTTPError as e:
            logger.error(f"飞书API HTTP错误: {e}")
            return {"success": False, "error": str(e)}
        except requests.exceptions.RequestException as e:
            logger.error(f"飞书API请求异常: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"飞书API未知异常: {e}")
            return {"success": False, "error": str(e)}
    
    def create_base(self, name: str = "AI资讯采集") -> Dict[str, Any]:
        """创建多维表格Base"""
        body = {"name": name, "time_zone": "Asia/Shanghai"}
        result = self._request("POST", "/bitable/v1/apps", json_body=body)
        return result
    
    def create_table(self, app_token: str, table_name: str = "推文草稿") -> Dict[str, Any]:
        """创建数据表"""
        path = f"/bitable/v1/apps/{app_token}/tables"
        body = {"table_name": table_name}
        result = self._request("POST", path, json_body=body)
        return result
    
    def list_fields(self, app_token: str, table_id: str) -> Dict[str, Any]:
        """列出数据表字段"""
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        result = self._request("GET", path)
        return result
    
    def add_field(self, app_token: str, table_id: str, field_name: str, field_type: int, 
                  property: Optional[dict] = None, description: Optional[str] = None) -> Dict[str, Any]:
        """添加字段"""
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        field_body: Dict[str, Any] = {
            "field_name": field_name,
            "type": field_type
        }
        if property:
            field_body["property"] = property
        if description:
            field_body["description"] = description
        
        result = self._request("POST", path, json_body=field_body)
        return result
    
    def create_required_fields(self, app_token: str, table_id: str) -> List[str]:
        """创建所需的字段"""
        fields_created: List[str] = []
        
        # 定义所需字段
        required_fields: List[Dict[str, Any]] = [
            {
                "field_name": "唯一ID",
                "type": 1,  # 文本
                "description": "推文唯一标识，用于去重"
            },
            {
                "field_name": "链接",
                "type": 1,  # 文本
                "description": "原文URL"
            },
            {
                "field_name": "标题",
                "type": 1,  # 文本
                "description": "资讯标题"
            },
            {
                "field_name": "分类",
                "type": 3,  # 单选
                "property": {
                    "options": [
                        {"name": "AI技术"},
                        {"name": "产品发布"},
                        {"name": "行业动态"},
                        {"name": "开源项目"},
                        {"name": "学术研究"},
                        {"name": "其他"}
                    ]
                },
                "description": "内容分类"
            },
            {
                "field_name": "热度评分",
                "type": 2,  # 数字
                "description": "AI评分（0-100）"
            },
            {
                "field_name": "推文内容",
                "type": 1,  # 文本
                "description": "生成的推文（280字符内）"
            },
            {
                "field_name": "独立观点",
                "type": 1,  # 文本
                "description": "推文中的观点提炼"
            },
            {
                "field_name": "处理状态",
                "type": 3,  # 单选
                "property": {
                    "options": [
                        {"name": "待审核"},
                        {"name": "待发布"},
                        {"name": "已发布"},
                        {"name": "已归档"}
                    ]
                },
                "description": "审核状态"
            },
            {
                "field_name": "创建时间",
                "type": 5,  # 日期
                "description": "记录创建时间"
            }
        ]
        
        # 先检查已有字段
        existing_fields_result = self.list_fields(app_token, table_id)
        existing_field_names: List[str] = []
        
        if existing_fields_result.get("success"):
            fields_data = existing_fields_result.get("data", {})
            items = fields_data.get("items", [])
            if isinstance(items, list):
                existing_field_names = [f.get("field_name", "") for f in items if isinstance(f, dict)]
        
        # 添加缺失字段
        for field_def in required_fields:
            field_name = field_def.get("field_name", "")
            if field_name not in existing_field_names:
                result = self.add_field(
                    app_token=app_token,
                    table_id=table_id,
                    field_name=field_name,
                    field_type=field_def.get("type", 1),
                    property=field_def.get("property"),
                    description=field_def.get("description")
                )
                
                if result.get("success"):
                    fields_created.append(field_name)
                    logger.info(f"字段 '{field_name}' 创建成功")
                else:
                    logger.warning(f"字段 '{field_name}' 创建失败: {result.get('error')}")
        
        return fields_created


def feishu_table_init_node(state: FeishuTableInitInput, config: RunnableConfig, runtime: Runtime[Context]) -> FeishuTableInitOutput:
    """
    title: 飞书表格初始化
    desc: 自动创建飞书多维表格和数据表，以及所需的字段（唯一ID、链接、标题、分类、热度评分、推文内容、独立观点、处理状态、创建时间）
    integrations: 飞书多维表格
    
    功能：
    1. 如果没有提供app_token，自动创建新的多维表格Base
    2. 如果没有提供table_id，自动创建新的数据表
    3. 检查字段是否存在，自动补充缺失字段
    4. 返回表格信息给后续节点使用
    """
    ctx = runtime.context
    
    logger.info("开始飞书表格初始化...")
    
    # 获取飞书凭证
    initializer = FeishuTableInitializer()
    initializer.access_token = initializer.get_access_token()
    
    if not initializer.access_token:
        logger.warning("飞书凭证未授权，跳过表格初始化")
        return FeishuTableInitOutput(
            app_token=state.feishu_app_token or "",
            table_id=state.feishu_table_id or "",
            fields_created=[],
            init_success=False,
            message="飞书多维表格集成未授权，请先在平台完成授权"
        )
    
    # 初始化结果
    app_token: str = state.feishu_app_token or ""
    table_id: str = state.feishu_table_id or ""
    fields_created: List[str] = []
    init_success: bool = True
    message: str = "飞书表格初始化成功"
    
    try:
        # 步骤1：如果没有app_token，创建新的Base
        if not app_token:
            logger.info("创建新的多维表格Base...")
            result = initializer.create_base(name="AI资讯采集推文库")
            
            if result.get("success"):
                data = result.get("data", {})
                app_token = data.get("app", {}).get("app_token", "")
                if isinstance(app_token, str) and app_token:
                    logger.info(f"Base创建成功，app_token: {app_token}")
                    message += f"，创建新表格: {app_token}"
                else:
                    init_success = False
                    message = "创建Base失败：无法获取app_token"
            else:
                init_success = False
                message = f"创建Base失败: {result.get('error')}"
        
        # 步骤2：如果没有table_id，创建新的数据表
        if init_success and app_token and not table_id:
            logger.info("创建新的数据表...")
            result = initializer.create_table(app_token=app_token, table_name="推文草稿")
            
            if result.get("success"):
                data = result.get("data", {})
                table_id = data.get("table_id", "")
                if isinstance(table_id, str) and table_id:
                    logger.info(f"数据表创建成功，table_id: {table_id}")
                    message += f"，创建新数据表: {table_id}"
                else:
                    init_success = False
                    message = "创建数据表失败：无法获取table_id"
            else:
                init_success = False
                message = f"创建数据表失败: {result.get('error')}"
        
        # 步骤3：创建所需字段
        if init_success and app_token and table_id:
            logger.info("检查并创建所需字段...")
            fields_created = initializer.create_required_fields(app_token=app_token, table_id=table_id)
            
            if len(fields_created) > 0:
                logger.info(f"成功创建 {len(fields_created)} 个字段: {fields_created}")
                message += f"，创建 {len(fields_created)} 个字段"
            else:
                logger.info("所有字段已存在，无需创建")
                message += "，字段已就绪"
        
    except Exception as e:
        logger.error(f"飞书表格初始化异常: {e}")
        init_success = False
        message = f"初始化异常: {str(e)}"
    
    logger.info(f"飞书表格初始化完成: {message}")
    
    return FeishuTableInitOutput(
        app_token=app_token,
        table_id=table_id,
        fields_created=fields_created,
        init_success=init_success,
        message=message
    )