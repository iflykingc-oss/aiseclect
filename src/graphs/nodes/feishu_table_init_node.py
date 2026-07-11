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
from feishu_bitable import FeishuClient

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
    {"name": "小红书标题", "type": 1},
    {"name": "小红书内容", "type": 1},
    {"name": "小红书标签", "type": 1},
    {"name": "配图提示词", "type": 1},
    {"name": "素材来源", "type": 1},
    {"name": "发布平台", "type": 3},              # 单选：X+小红书 / 仅X
    # 状态字段合并：LLM 写入默认「待审核」，运营手动改为 已发布 / 需修改 / 驳回
    {"name": "处理状态", "type": 3},              # 单选：待审核 / 已发布 / 需修改 / 驳回
    {"name": "审核备注", "type": 1},              # 自由文本，运营写修改建议 / 发布链接等
    {"name": "起号定位", "type": 1},              # 小红书内容支柱 / 起号方向
    {"name": "笔记结构", "type": 1},
    {"name": "标题模板", "type": 1},
    {"name": "搜索分", "type": 2},
    {"name": "收藏分", "type": 2},
    {"name": "新手分", "type": 2},
    {"name": "系列分", "type": 2},
    {"name": "起号备注", "type": 1},
    {"name": "创建时间", "type": 5},
]

# 已废弃字段（老"通用XXX" 冗余组 + 8 个独立诊断列 + 上一版短暂引入的元信息列），init 阶段硬删
OBSOLETE_FIELDS: List[str] = [
    "通用标题",
    "通用内容",
    "通用标签",
    "发现原因",
    "评分理由",
    "平台判断理由",
    "Hook类型",
    "内容角度",
    "质量备注",
    "X质量分",
    "小红书质量分",
    "元信息",
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

    # 硬删废弃字段
    try:
        removed = client.remove_fields(feishu_app_token, feishu_table_id, OBSOLETE_FIELDS)
        if removed:
            logger.info(f"已删除废弃字段: {removed}")
    except Exception as e:
        logger.warning(f"删除废弃字段失败（不阻塞主流程）: {e}")

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
