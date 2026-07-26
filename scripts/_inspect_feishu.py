"""一次性脚本：诊断飞书 Bitable 表的当前状态。
- 加载 .env 而非硬编码密钥
- 输出：总记录数 + 状态分布 + 分类分布 + 创建时间分布 + 前 10 条最新记录
"""
import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(REPO_ROOT / "src"))

# 验证关键 env 都在
required = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_PAGE_ID", "FEISHU_TABLE_ID"]
missing = [k for k in required if not os.getenv(k)]
if missing:
    print(f"ERROR: 缺少 env: {missing}", file=sys.stderr)
    sys.exit(1)

# Wiki 模式：先用 page_id 解析 app_token
from feishu_bitable import FeishuClient

client = FeishuClient()
app_token = client.get_wiki_app_token(os.environ["FEISHU_PAGE_ID"])
table_id = os.environ["FEISHU_TABLE_ID"]
records = list(client.list_records(app_token, table_id))
print(f"=== 飞书表 {app_token[:8]}... / {table_id} ===")
print(f"总记录数: {len(records)}")

statuses = Counter()
categories = Counter()
sources = Counter()
for r in records:
    f = r.get("fields", {})
    s = f.get("处理状态", "")
    if isinstance(s, dict):
        s = s.get("name") or s.get("text") or ""
    if isinstance(s, list) and s:
        first = s[0]
        s = first.get("name") or first.get("text") if isinstance(first, dict) else str(first)
    statuses[str(s) or "(空)"] += 1

    cat = f.get("分类", "")
    if isinstance(cat, dict):
        cat = cat.get("name") or cat.get("text") or ""
    if isinstance(cat, list) and cat:
        first = cat[0]
        cat = first.get("name") or first.get("text") if isinstance(first, dict) else str(first)
    categories[str(cat) or "(空)"] += 1

    src = f.get("素材来源", "")
    if isinstance(src, dict):
        src = src.get("text") or ""
    sources[str(src) or "(空)"] += 1

print("\n--- 处理状态分布 ---")
for k, v in statuses.most_common(10):
    print(f"  {k}: {v}")

print("\n--- 分类分布 (top 10) ---")
for k, v in categories.most_common(10):
    print(f"  {k}: {v}")

print("\n--- 素材来源分布 (top 10) ---")
for k, v in sources.most_common(10):
    print(f"  {k}: {v}")

print(f"\n--- 最新 10 条记录 ---")
for r in records[:10]:
    f = r.get("fields", {})
    title = f.get("标题", "")
    if isinstance(title, dict):
        title = title.get("text") or title.get("name") or ""
    if isinstance(title, list) and title:
        first = title[0]
        title = first.get("text") if isinstance(first, dict) else str(first)
    created = f.get("创建时间", "")
    print(f"  - {str(title)[:60]}  (创建时间={created})")