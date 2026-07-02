"""
飞书表格初始化节点
- 解析 Wiki 节点 → app_token
- 确保目标表存在所需字段（缺失则补建）
- 字段名/类型参考原版 feishu_table_init_node
"""
from __future__ import annotations

import logging
from typing import List, Optional

from graphs.state import FeishuTableInitInput, FeishuTableInitOutput
from tools.feishu_client import FeishuClient

logger = logging.getLogger(__name__)

# 字段定义: name -> type (1=文本 2=数字 3=单选 5=日期 7=复选 15=URL 17=附件)
# 注: 单选字段的选项需要在飞书 UI 手动添加，本脚本只创建字段
REQUIRED_FIELDS: List[dict] = [
    {"name": "唯一ID", "type": 1},
    {"name": "链接", "type": 15},                # URL 类型
    {"name": "标题", "type": 1},
    {"name": "分类", "type": 3},                  # 单选
    {"name": "热度评分", "type": 2},
    {"name": "推文内容", "type": 1},
    # 「独立观点」字段已废弃：内心 OS 已融进推文内容。为兼容历史数据不主动删除该列，但不再确保它存在。
    {"name": "小红书标题", "type": 1},
    {"name": "小红书内容", "type": 1},
    {"name": "小红书标签", "type": 1},
    {"name": "发布平台", "type": 3},              # 单选：X+小红书 / 仅X
    {"name": "处理状态", "type": 3},              # 单选：待审核 / 已通过 / 已驳回 / 已发布
    {"name": "人工审核结果", "type": 3},          # 单选：通过 / 需修改 / 驳回（人工填）
    {"name": "审核备注", "type": 1},              # 人工审核时的批注
    {"name": "创建时间", "type": 5},
]


def feishu_table_init_node(state: FeishuTableInitInput) -> FeishuTableInitOutput:
    """解析 token、补建字段。不做表/库的新建（用户已提供表）。"""
    feishu_app_token: str = state.feishu_app_token or ""
    feishu_table_id: str = state.feishu_table_id or ""
    feishu_page_id: str = state.feishu_page_id or ""
    is_wiki_embed: bool = state.is_wiki_embed
    feishu_domain: str = state.feishu_domain
    fields_created: List[str] = []
    init_success = False
    message = ""

    if not state.write_to_feishu:
        return FeishuTableInitOutput(
            feishu_app_token=feishu_app_token,
            feishu_table_id=feishu_table_id,
            feishu_page_id=feishu_page_id,
            is_wiki_embed=is_wiki_embed,
            feishu_domain=feishu_domain,
            feishu_fields_created=[],
            feishu_init_success=True,
            feishu_init_message="飞书写入已禁用，跳过初始化",
        )

    try:
        client = FeishuClient()
    except Exception as e:
        return FeishuTableInitOutput(
            feishu_init_success=False,
            feishu_init_message=f"飞书客户端初始化失败: {e}",
        )

    if is_wiki_embed:
        if not feishu_table_id:
            return FeishuTableInitOutput(
                feishu_init_success=False,
                feishu_init_message="Wiki 内嵌表格缺少 table_id（从 Wiki 链接的 ?table=xxx 读取）",
            )
        if not feishu_app_token:
            if not feishu_page_id:
                return FeishuTableInitOutput(
                    feishu_init_success=False,
                    feishu_init_message="Wiki 内嵌表格缺少 page_id（Wiki 链接里 node token）",
                )
            try:
                feishu_app_token = client.get_wiki_app_token(feishu_page_id) or ""
            except Exception as e:
                return FeishuTableInitOutput(
                    feishu_init_success=False,
                    feishu_init_message=f"从 Wiki 节点获取 app_token 失败: {e}",
                )
            if not feishu_app_token:
                return FeishuTableInitOutput(
                    feishu_init_success=False,
                    feishu_init_message="Wiki 节点返回的 app_token 为空",
                )
    else:
        if not feishu_app_token or not feishu_table_id:
            return FeishuTableInitOutput(
                feishu_init_success=False,
                feishu_init_message="独立表格模式需要同时提供 feishu_app_token 和 feishu_table_id",
            )

    # 补建字段
    try:
        fields_created = client.ensure_fields(feishu_app_token, feishu_table_id, REQUIRED_FIELDS)
    except Exception as e:
        return FeishuTableInitOutput(
            feishu_app_token=feishu_app_token,
            feishu_table_id=feishu_table_id,
            feishu_page_id=feishu_page_id,
            is_wiki_embed=is_wiki_embed,
            feishu_domain=feishu_domain,
            feishu_init_success=False,
            feishu_init_message=f"补建字段失败: {e}",
        )

    init_success = True
    if fields_created:
        message = f"飞书表格就绪，新建 {len(fields_created)} 个字段: {fields_created}"
    else:
        message = "飞书表格就绪，所有字段已存在"
    logger.info(message)

    return FeishuTableInitOutput(
        feishu_app_token=feishu_app_token,
        feishu_table_id=feishu_table_id,
        feishu_page_id=feishu_page_id,
        is_wiki_embed=is_wiki_embed,
        feishu_domain=feishu_domain,
        feishu_fields_created=fields_created,
        feishu_init_success=init_success,
        feishu_init_message=message,
    )
