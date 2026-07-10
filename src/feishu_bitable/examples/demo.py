"""
飞书 Bitable 写入示例（独立可运行）

用法：
    # Wiki 内嵌表格（推荐）
    export FEISHU_APP_ID=cli_xxx
    export FEISHU_APP_SECRET=xxx
    export FEISHU_PAGE_ID=xxx  # Wiki 节点 token
    export FEISHU_TABLE_ID=tblxxx
    python -m feishu_bitable.examples.demo

    # 独立 Bitable app
    export FEISHU_APP_TOKEN=bascnxxx
    python -m feishu_bitable.examples.demo --no-wiki
"""
from __future__ import annotations

import argparse
import logging
import sys

from feishu_bitable import FeishuClient, FeishuField

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("feishu_bitable.demo")


# 字段定义：1=文本 2=数字 3=单选 5=日期 15=URL
SCHEMA = [
    {"name": "标题", "type": 1},
    {"name": "链接", "type": 15},
    {"name": "热度", "type": 2},
    {"name": "分类", "type": 3},
    {"name": "创建时间", "type": 5},
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-wiki", action="store_true", help="用独立 Bitable app 而非 Wiki 节点")
    parser.add_argument("--app-token", help="独立 Bitable app_token（与 --no-wiki 配合）")
    parser.add_argument("--table-id", help="table_id（独立模式必填）")
    args = parser.parse_args()

    client = FeishuClient()

    # 1. 解析 app_token
    if args.no_wiki:
        if not args.app_token or not args.table_id:
            logger.error("--no-wiki 模式必须指定 --app-token 和 --table-id")
            return 2
        app_token = args.app_token
        table_id = args.table_id
    else:
        import os
        wiki_token = os.getenv("FEISHU_PAGE_ID", "")
        table_id = os.getenv("FEISHU_TABLE_ID", "")
        if not wiki_token or not table_id:
            logger.error("FEISHU_PAGE_ID / FEISHU_TABLE_ID 未配置")
            return 2
        app_token = client.get_wiki_app_token(wiki_token)
        if not app_token:
            logger.error("从 Wiki 节点反查 app_token 失败")
            return 1

    logger.info(f"app_token={app_token[:8]}... table_id={table_id}")

    # 2. 列出当前字段
    fields: list[FeishuField] = client.list_fields(app_token, table_id)
    logger.info(f"当前字段数: {len(fields)}")
    for f in fields:
        logger.info(f"  - {f.field_name} (type={f.type})")

    # 3. 自动建字段（仅缺什么建什么）
    created = client.ensure_fields(app_token, table_id, SCHEMA)
    if created:
        logger.info(f"新建字段: {created}")
    else:
        logger.info("所有字段已存在，无需新建")

    # 4. 写入 1 条示例记录
    records = [
        {
            "fields": {
                "标题": "feishu_bitable 抽包测试",
                "链接": {"text": "GitHub", "link": "https://example.com"},
                "热度": 99,
                "分类": "测试",
                "创建时间": 1720000000000,  # 毫秒时间戳
            }
        }
    ]
    written = client.batch_create_records(app_token, table_id, records)
    logger.info(f"写入 {len(written)} 条；首个 record_id={written[0].get('record_id') if written else 'N/A'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())