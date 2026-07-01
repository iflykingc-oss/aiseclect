"""
飞书表格初始化节点 - 自动创建飞书多维表格和所需字段
"""
import json
import logging
import requests
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from cozeloop.decorator import observe
from coze_workload_identity import Client
from graphs.state import FeishuTableInitInput, FeishuTableInitOutput

logger = logging.getLogger(__name__)


class FeishuTableInitializer:
    """飞书多维表格初始化器"""
    
    def __init__(self):
        self.access_token: str = ""
    
    def get_access_token(self) -> str:
        """获取飞书多维表格的访问令牌"""
        try:
            client = Client()
            self.access_token = client.get_integration_credential("integration-feishu-base")
            return self.access_token
        except Exception as e:
            logger.warning(f"飞书凭证获取失败（集成未授权）: {str(e)}")
            return ""
    
    def _headers(self) -> dict:
        """构建请求头"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=utf-8"
        }
    
    @observe
    def get_wiki_node_info(self, token: str) -> dict:
        """查询Wiki节点信息，获取嵌入的多维表格app_token"""
        try:
            # 飞书Wiki API：获取Wiki节点信息
            resp = requests.get(
                f"https://open.larkoffice.com/open-apis/wiki/v2/spaces/get_node?token={token}",
                headers=self._headers(),
                timeout=30
            )
            result = self._safe_json_parse(resp)
            
            if result.get("code") == 0:
                node = result.get("data", {}).get("node", {})
                # 检查是否是Bitable类型的节点
                if node.get("obj_type") == "bitable":
                    # Wiki内嵌表格的obj_token就是app_token
                    app_token = node.get("obj_token", "")
                    logger.info(f"从Wiki节点获取到表格app_token: {app_token}")
                    return {"success": True, "app_token": app_token}
                else:
                    logger.warning(f"Wiki节点类型不是bitable: {node.get('obj_type')}")
                    return {"success": False, "error": "节点类型不是多维表格"}
            else:
                logger.error(f"查询Wiki节点失败: {result.get('msg', '未知错误')}")
                return {"success": False, "error": result.get("msg", "未知错误")}
        except Exception as e:
            logger.error(f"查询Wiki节点异常: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def _safe_json_parse(self, resp) -> dict:
        """安全的JSON解析，处理非JSON响应"""
        # 先检查响应状态码
        if resp.status_code != 200:
            logger.error(f"飞书API HTTP错误: {resp.status_code}")
            if resp.status_code == 401:
                logger.error("飞书集成未授权（401），请在平台完成飞书多维表格集成授权")
            return {"code": resp.status_code, "msg": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        
        # 检查响应内容是否为JSON
        content_type = resp.headers.get('Content-Type', '')
        if 'application/json' not in content_type:
            logger.error(f"飞书API返回非JSON响应: {content_type}")
            return {"code": -1, "msg": f"非JSON响应: {content_type}, 内容: {resp.text[:200]}"}
        
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            logger.error(f"飞书API JSON解析错误: {str(e)}")
            return {"code": -1, "msg": f"JSON解析错误: {str(e)}"}
    
    @observe
    def create_base(self, name: str) -> dict:
        """创建多维表格Base"""
        try:
            resp = requests.post(
                "https://open.larkoffice.com/open-apis/bitable/v1/apps",
                headers=self._headers(),
                json={"name": name},
                timeout=30
            )
            result = self._safe_json_parse(resp)
            
            if result.get("code") == 0:
                return {"success": True, "data": result.get("data")}
            else:
                return {"success": False, "error": result.get("msg", "未知错误")}
        except Exception as e:
            logger.error(f"创建Base异常: {str(e)}")
            return {"success": False, "error": str(e)}
    
    @observe
    def create_table(self, app_token: str, table_name: str) -> dict:
        """创建数据表"""
        try:
            resp = requests.post(
                f"https://open.larkoffice.com/open-apis/bitable/v1/apps/{app_token}/tables",
                headers=self._headers(),
                json={"table_name": table_name},
                timeout=30
            )
            result = self._safe_json_parse(resp)
            
            if result.get("code") == 0:
                return {"success": True, "data": result.get("data")}
            else:
                return {"success": False, "error": result.get("msg", "未知错误")}
        except Exception as e:
            logger.error(f"创建数据表异常: {str(e)}")
            return {"success": False, "error": str(e)}
    
    @observe
    def list_fields(self, app_token: str, table_id: str) -> List[str]:
        """获取数据表的现有字段"""
        try:
            resp = requests.get(
                f"https://open.larkoffice.com/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                headers=self._headers(),
                timeout=30
            )
            result = self._safe_json_parse(resp)
            
            if result.get("code") == 0:
                items = result.get("data", {}).get("items", [])
                return [item.get("field_name", "") for item in items if isinstance(item, dict)]
            else:
                logger.error(f"获取字段列表失败: {result.get('msg', '未知错误')}")
                return []
        except Exception as e:
            logger.error(f"获取字段列表异常: {str(e)}")
            return []
    
    @observe
    def add_field(self, app_token: str, table_id: str, field_name: str, field_type: int) -> bool:
        """添加字段到数据表"""
        try:
            resp = requests.post(
                f"https://open.larkoffice.com/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                headers=self._headers(),
                json={"field_name": field_name, "type": field_type},
                timeout=30
            )
            result = self._safe_json_parse(resp)
            
            if result.get("code") == 0:
                logger.info(f"字段 '{field_name}' 创建成功")
                return True
            else:
                logger.error(f"字段 '{field_name}' 创建失败: {result.get('msg', '未知错误')}")
                return False
        except Exception as e:
            logger.error(f"添加字段异常: {str(e)}")
            return False
    
    def create_required_fields(self, app_token: str, table_id: str) -> List[str]:
        """创建所需的字段（如果不存在）"""
        # 标准字段定义（名称和类型）
        required_fields = [
            ("唯一ID", 1),      # 文本
            ("链接", 1),         # 文本
            ("标题", 1),         # 文本
            ("分类", 3),         # 单选
            ("热度评分", 2),     # 数字
            ("推文内容", 1),     # 文本
            ("独立观点", 1),     # 文本
            ("处理状态", 3),     # 单选
            ("创建时间", 5),     # 日期
        ]
        
        # 获取现有字段
        existing_fields = self.list_fields(app_token=app_token, table_id=table_id)
        logger.info(f"现有字段: {existing_fields}")
        
        # 创建缺失字段
        created_fields: List[str] = []
        for field_name, field_type in required_fields:
            if field_name not in existing_fields:
                if self.add_field(app_token=app_token, table_id=table_id, field_name=field_name, field_type=field_type):
                    created_fields.append(field_name)
        
        return created_fields


def feishu_table_init_node(state: FeishuTableInitInput, config: RunnableConfig, runtime: Runtime[Context]) -> FeishuTableInitOutput:
    """
    title: 飞书表格初始化
    desc: 自动创建飞书多维表格和数据表，并添加所需的字段
    integrations: Feishu Base
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
            page_id=state.feishu_page_id or "",
            is_wiki_embed=state.is_wiki_embed,
            feishu_domain=state.feishu_domain,
            fields_created=[],
            init_success=False,
            message="飞书多维表格集成未授权，请先在平台完成授权"
        )
    
    # 初始化结果
    app_token: str = state.feishu_app_token or ""
    table_id: str = state.feishu_table_id or ""
    page_id: str = state.feishu_page_id or ""
    is_wiki_embed: bool = state.is_wiki_embed
    feishu_domain: str = state.feishu_domain
    fields_created: List[str] = []
    init_success: bool = True
    message: str = "飞书表格初始化成功"
    
    try:
        # Wiki内嵌表格处理（跳过表格创建步骤）
        if is_wiki_embed:
            logger.info(f"检测到Wiki内嵌表格模式，page_id: {page_id}, table_id: {table_id}")
            
            if not table_id:
                init_success = False
                message = "Wiki内嵌表格缺少table_id，请在Wiki页面中打开表格并获取完整链接"
                logger.error(message)
            else:
                # Wiki内嵌表格需要app_token来操作字段
                if not app_token:
                    logger.info("Wiki内嵌表格缺少app_token，尝试从Wiki页面获取...")
                    # 使用page_id（Wiki节点token）查询Wiki页面信息
                    wiki_result = initializer.get_wiki_node_info(token=page_id)
                    
                    if wiki_result.get("success"):
                        app_token = wiki_result.get("app_token", "")
                        logger.info(f"成功从Wiki页面获取app_token: {app_token}")
                        message += f"，自动获取app_token: {app_token}"
                    else:
                        init_success = False
                        message = f"无法从Wiki页面获取app_token: {wiki_result.get('error')}"
                        logger.error(message)
                
                # 创建字段（已获取app_token）
                if init_success and app_token:
                    logger.info(f"开始为Wiki内嵌表格创建字段，app_token: {app_token}, table_id: {table_id}")
                    fields_created = initializer.create_required_fields(app_token=app_token, table_id=table_id)
                    
                    if len(fields_created) > 0:
                        logger.info(f"成功创建 {len(fields_created)} 个字段: {fields_created}")
                        message += f"，创建 {len(fields_created)} 个字段"
                    else:
                        logger.info("所有字段已存在，无需创建")
                        message += "，字段已就绪"
        else:
            # 独立多维表格处理（正常流程）
            logger.info("检测到独立多维表格模式")
            
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
            
            # 步骤3：创建所需的字段
            if init_success and app_token and table_id:
                logger.info(f"开始创建字段，app_token: {app_token}, table_id: {table_id}")
                fields_created = initializer.create_required_fields(app_token=app_token, table_id=table_id)
                
                if len(fields_created) > 0:
                    logger.info(f"成功创建 {len(fields_created)} 个字段: {fields_created}")
                    message += f"，创建 {len(fields_created)} 个字段"
                else:
                    logger.info("所有字段已存在，无需创建")
                    message += "，字段已就绪"
    
    except Exception as e:
        logger.error(f"飞书表格初始化异常: {str(e)}")
        init_success = False
        message = f"飞书表格初始化异常: {str(e)}"
    
    return FeishuTableInitOutput(
        app_token=app_token,
        table_id=table_id,
        page_id=page_id,
        is_wiki_embed=is_wiki_embed,
        feishu_domain=feishu_domain,
        fields_created=fields_created,
        init_success=init_success,
        message=message
    )