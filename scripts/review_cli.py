"""审核队列 CLI 工具

交互式审核待发布内容：
- 显示待审核草稿列表
- 支持 approve/reject/edit 操作
- 更新审核状态到 review_queue.json
- 记录反馈到 feedback_log.json

用法:
    python scripts/review_cli.py
"""
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
except ImportError:
    print("错误：需要安装 rich 库")
    print("运行: pip install rich")
    sys.exit(1)

console = Console()

REVIEW_QUEUE_FILE = Path("output/review_queue.json")
FEEDBACK_LOG_FILE = Path("output/feedback_log.json")


def load_review_queue() -> List[Dict]:
    """加载审核队列"""
    if not REVIEW_QUEUE_FILE.exists():
        return []

    try:
        with open(REVIEW_QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("items", [])
    except Exception as e:
        console.print(f"[red]加载审核队列失败: {e}[/red]")
        return []


def save_review_queue(items: List[Dict]):
    """保存审核队列"""
    REVIEW_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "updated_at": datetime.now().isoformat(),
        "total": len(items),
        "items": items
    }

    with open(REVIEW_QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_feedback(item: Dict, action: str, reason: str = ""):
    """记录审核反馈到日志"""
    FEEDBACK_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    feedback = {
        "timestamp": datetime.now().isoformat(),
        "article_id": item.get("unique_id", ""),
        "url": item.get("url", ""),
        "title": item.get("title", ""),
        "action": action,
        "reason": reason,
        "quality_score": item.get("quality_score", 0),
        "platform": item.get("platform", ""),
    }

    # 追加到日志文件
    logs = []
    if FEEDBACK_LOG_FILE.exists():
        try:
            with open(FEEDBACK_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []

    logs.append(feedback)

    with open(FEEDBACK_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

    console.print(f"[dim]反馈已记录到 {FEEDBACK_LOG_FILE}[/dim]")


def display_item(item: Dict, index: int):
    """显示单个待审核项"""
    console.print(f"\n[bold cyan]═══ 草稿 #{index + 1} ═══[/bold cyan]")
    console.print(f"[yellow]标题:[/yellow] {item.get('title', 'N/A')[:80]}")
    console.print(f"[yellow]来源:[/yellow] {item.get('source', 'N/A')}")
    console.print(f"[yellow]分数:[/yellow] {item.get('quality_score', 0):.0f}")
    console.print(f"[yellow]平台:[/yellow] {item.get('platform', 'N/A')}")
    console.print(f"[yellow]URL:[/yellow] {item.get('url', 'N/A')[:80]}")

    # 显示内容预览
    preview = item.get("tweet_preview", "")
    if preview:
        panel = Panel(
            preview[:500] + ("..." if len(preview) > 500 else ""),
            title="内容预览",
            border_style="green"
        )
        console.print(panel)

    # 显示质量问题
    notes = item.get("quality_notes", "")
    if notes and notes != "ok":
        console.print(f"[red]质量问题:[/red] {notes}")


def review_item(item: Dict, index: int) -> str:
    """审核单个项目，返回 action"""
    display_item(item, index)

    console.print("\n[bold]操作选项:[/bold]")
    console.print("  [green]a[/green] - Approve (通过)")
    console.print("  [red]r[/red] - Reject (拒绝)")
    console.print("  [yellow]e[/yellow] - Edit (编辑)")
    console.print("  [dim]s[/dim] - Skip (跳过)")
    console.print("  [dim]q[/dim] - Quit (退出)")

    action = Prompt.ask("\n选择操作", choices=["a", "r", "e", "s", "q"], default="s")

    if action == "a":
        return "approve"
    elif action == "r":
        reason = Prompt.ask("拒绝原因（可选）", default="")
        record_feedback(item, "reject", reason)
        return "reject"
    elif action == "e":
        console.print("[yellow]编辑功能待实现，当前标记为待编辑[/yellow]")
        return "edit"
    elif action == "q":
        return "quit"
    else:
        return "skip"


def main():
    """主函数"""
    console.print("[bold green]审核队列 CLI[/bold green]", style="bold")
    console.print()

    # 加载队列
    items = load_review_queue()

    if not items:
        console.print("[yellow]审核队列为空[/yellow]")
        console.print("\n提示：运行管道生成内容后，质量分数 60-80 的草稿会进入审核队列")
        return

    # 统计信息
    pending = [i for i in items if i.get("review_status") == "pending"]
    console.print(f"[cyan]待审核:[/cyan] {len(pending)} 条")
    console.print(f"[dim]总计:[/dim] {len(items)} 条")
    console.print()

    # 逐个审核
    approved_count = 0
    rejected_count = 0

    for i, item in enumerate(items):
        if item.get("review_status") != "pending":
            continue

        action = review_item(item, i)

        if action == "quit":
            console.print("\n[yellow]退出审核[/yellow]")
            break
        elif action == "approve":
            item["review_status"] = "approved"
            item["reviewed_at"] = datetime.now().isoformat()
            approved_count += 1
            record_feedback(item, "approve")
            console.print("[green]✓ 已通过[/green]")
        elif action == "reject":
            item["review_status"] = "rejected"
            item["reviewed_at"] = datetime.now().isoformat()
            rejected_count += 1
            console.print("[red]✗ 已拒绝[/red]")
        elif action == "edit":
            item["review_status"] = "needs_edit"
            item["reviewed_at"] = datetime.now().isoformat()
            console.print("[yellow]⚠ 待编辑[/yellow]")
        else:
            console.print("[dim]跳过[/dim]")

    # 保存更新
    save_review_queue(items)

    # 显示统计
    console.print(f"\n[bold]审核完成[/bold]")
    console.print(f"  通过: [green]{approved_count}[/green]")
    console.print(f"  拒绝: [red]{rejected_count}[/red]")
    console.print(f"\n审核队列已更新: {REVIEW_QUEUE_FILE}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]已取消[/yellow]")
        sys.exit(0)
