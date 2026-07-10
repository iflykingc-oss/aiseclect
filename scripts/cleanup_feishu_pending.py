"""
删除飞书表格中处理状态为「待审核」的旧记录。

用法：
  python scripts/cleanup_feishu_pending.py --dry-run
  python scripts/cleanup_feishu_pending.py --confirm
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from feishu_bitable import FeishuClient  # noqa: E402


def _normalize_status(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("value") or "").strip()
    if isinstance(value, list) and value:
        return _normalize_status(value[0])
    return ""


def _resolve_app_token(client: FeishuClient, app_token: str, page_id: str, is_wiki: bool) -> str:
    if app_token:
        return app_token
    if is_wiki and page_id:
        resolved = client.get_wiki_app_token(page_id) or ""
        if resolved:
            return resolved
    return ""


def main() -> int:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="删除飞书待审核旧记录")
    p.add_argument("--confirm", action="store_true", help="真正删除；不加则只 dry-run")
    p.add_argument("--app-token", default=os.getenv("FEISHU_APP_TOKEN", ""))
    p.add_argument("--table-id", default=os.getenv("FEISHU_TABLE_ID", ""))
    p.add_argument("--page-id", default=os.getenv("FEISHU_PAGE_ID", ""))
    p.add_argument("--no-wiki", action="store_true")
    args = p.parse_args()

    client = FeishuClient()
    app_token = _resolve_app_token(client, args.app_token, args.page_id, not args.no_wiki)
    if not app_token or not args.table_id:
        raise SystemExit("缺少 app_token/page_id 或 table_id")

    records = client.list_records(app_token, args.table_id)
    pending_ids = []
    status_counts: dict[str, int] = {}
    for rec in records:
        fields = rec.get("fields") or {}
        status = _normalize_status(fields.get("处理状态")) or "(空)"
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "待审核" and rec.get("record_id"):
            pending_ids.append(rec["record_id"])

    print(f"总记录: {len(records)}")
    print(f"状态分布: {status_counts}")
    print(f"待删除 待审核: {len(pending_ids)}")
    if not args.confirm:
        print("dry-run：未删除。加 --confirm 才会删除。")
        return 0

    deleted = client.batch_delete_records(app_token, args.table_id, pending_ids)
    print(f"已删除: {deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
