"""
飞书标记回拉脚本（2026-07-27）

用途：扫描飞书 Bitable 表，把「处理状态」变化回拉到 editorial_memory.json。
- 「已发布」→ memory 标为 success（角度被采纳）
- 「驳回」→ memory 标为 reject（角度被拒绝）
- 「待审核」超 N 天 → 自动改「驳回」（防止积压）
- 默认 dry-run，加 --confirm 才落盘

不是主流水线的一部分，由用户手动或单独 cron 调用。
用法：
    python scripts/pull_feishu_feedback.py --days 7                # dry-run
    python scripts/pull_feishu_feedback.py --days 7 --confirm      # 实际改
    python scripts/pull_feishu_feedback.py --max-records 50        # 只看前 50 条
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(REPO_ROOT / "src"))

from collect_pipeline.editorial_memory import (
    DEFAULT_MAX_ITEMS,
    MemoryItem,
    append_to_memory,
    load_memory,
    save_memory,
)
from feishu_bitable import FeishuClient

STATUS_AUTO_REJECT_DAYS = 7  # 待审核 超 N 天自动驳回


def _resolve_app_token(client: FeishuClient) -> str:
    page_id = os.getenv("FEISHU_PAGE_ID", "")
    if not page_id:
        return os.getenv("FEISHU_APP_TOKEN", "")
    return client.get_wiki_app_token(page_id) or os.getenv("FEISHU_APP_TOKEN", "")


def _extract_text(field_value) -> str:
    if isinstance(field_value, dict):
        return str(field_value.get("text") or field_value.get("name") or "")
    if isinstance(field_value, list) and field_value:
        first = field_value[0]
        return str(first.get("text") if isinstance(first, dict) else first)
    return str(field_value or "")


def _normalize_status(raw: str) -> str:
    s = str(raw or "").strip()
    if s in ("待审核", "已发布", "需修改", "驳回"):
        return s
    return "待审核"


def scan_feishu(client: FeishuClient, app_token: str, table_id: str, max_records: int = 0) -> List[dict]:
    """列出飞书表里所有记录 + 状态 + 创建时间"""
    out = []
    for i, rec in enumerate(client.list_records(app_token, table_id)):
        if max_records and i >= max_records:
            break
        f = rec.get("fields") or {}
        out.append({
            "record_id": rec.get("record_id", ""),
            "url": _extract_text(f.get("链接")),
            "title": _extract_text(f.get("标题")),
            "status": _normalize_status(_extract_text(f.get("处理状态"))),
            "created_at_ms": f.get("创建时间") or 0,
            "source": _extract_text(f.get("素材来源")),
            "category": _extract_text(f.get("分类")),
            "platform": _extract_text(f.get("发布平台")),
        })
    return out


def compute_actions(records: List[dict], now_ms: int, auto_reject_days: int) -> List[dict]:
    """根据状态和创建时间，决定每个记录要做什么动作。"""
    cutoff_ms = now_ms - auto_reject_days * 86400 * 1000
    actions = []
    for r in records:
        if r["status"] == "待审核" and r["created_at_ms"] and r["created_at_ms"] < cutoff_ms:
            actions.append({**r, "action": "auto_reject", "reason": f"待审核超 {auto_reject_days} 天"})
        elif r["status"] in ("已发布", "驳回", "需修改"):
            actions.append({**r, "action": "memory_mark", "memory_status": r["status"]})
    return actions


def apply_actions(actions: List[dict], client: FeishuClient, app_token: str, table_id: str, memory_path: str, confirm: bool) -> Dict[str, int]:
    """对每个 action 执行：auto_reject 改飞书状态；memory_mark 写编辑记忆。"""
    stats = Counter()
    memory_items = []
    record_updates = []
    for a in actions:
        if a["action"] == "auto_reject":
            record_updates.append({
                "record_id": a["record_id"],
                "fields": {"处理状态": "驳回"},
            })
            stats["auto_reject"] += 1
        elif a["action"] == "memory_mark":
            if a.get("url"):
                memory_items.append(MemoryItem(
                    unique_id=a["record_id"] or a.get("url", ""),
                    title=a.get("title") or a.get("url", ""),
                    source=a.get("source", ""),
                    category=a.get("category", ""),
                    platform=a.get("platform", ""),
                    quality_score=0.0,
                    pillar="",
                ))
                # 把状态写到 extra_data 字段（repurpose 一下）
                memory_items[-1].created_at = (a.get("created_at_ms", 0) or 0) / 1000
            stats[f"memory_{a['memory_status']}"] += 1

    if not confirm:
        return dict(stats)

    # 1) 改飞书状态
    for upd in record_updates:
        try:
            client.update_record(app_token, table_id, upd["record_id"], upd["fields"])
        except Exception as e:
            print(f"  ! update_record 失败 {upd['record_id']}: {e}")

    # 2) 写编辑记忆
    if memory_items:
        existing = load_memory(memory_path, max_items=1000)
        for it in memory_items:
            existing.append(it)
        # 重写
        save_memory(existing, memory_path)

    return dict(stats)


def main() -> int:
    p = argparse.ArgumentParser(description="从飞书表回拉处理状态到编辑记忆")
    p.add_argument("--days", type=int, default=STATUS_AUTO_REJECT_DAYS,
                   help=f"待审核超 N 天自动驳回（默认 {STATUS_AUTO_REJECT_DAYS}）")
    p.add_argument("--max-records", type=int, default=0, help="只扫描前 N 条（0=全部）")
    p.add_argument("--memory-path", default="output/editorial_memory.json",
                   help="editorial_memory.json 路径")
    p.add_argument("--confirm", action="store_true", help="实际落盘（默认 dry-run）")
    args = p.parse_args()

    required = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_PAGE_ID", "FEISHU_TABLE_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"ERROR: 缺 env: {missing}", file=sys.stderr)
        return 1

    client = FeishuClient()
    app_token = _resolve_app_token(client)
    if not app_token:
        print("ERROR: 无法解析 app_token", file=sys.stderr)
        return 1
    table_id = os.environ["FEISHU_TABLE_ID"]

    print(f"[{'DRY-RUN' if not args.confirm else 'APPLY'}] 扫描 {table_id} ...")
    records = scan_feishu(client, app_token, table_id, max_records=args.max_records)
    print(f"扫描到 {len(records)} 条记录")

    # 状态分布
    statuses = Counter(r["status"] for r in records)
    print("\n状态分布:")
    for k, v in statuses.most_common():
        print(f"  {k}: {v}")

    now_ms = int(time.time() * 1000)
    actions = compute_actions(records, now_ms=now_ms, auto_reject_days=args.days)
    print(f"\n将执行 {len(actions)} 个动作:")
    action_kinds = Counter(a["action"] + ":" + a.get("memory_status", a.get("reason", "")) for a in actions)
    for k, v in action_kinds.most_common():
        print(f"  {k}: {v}")

    if not args.confirm:
        print("\n[DRY-RUN] 加 --confirm 实际落盘")
        return 0

    stats = apply_actions(
        actions, client, app_token, table_id,
        memory_path=args.memory_path, confirm=True,
    )
    print(f"\n完成: {dict(stats)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())