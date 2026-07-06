"""
极简 flow runner
- 加载 .env
- 构建图，跑一次
- 落盘（可选）+ 打印汇总
- 触发飞书写入（在图内）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

# 让 `python -m src.main` 时 src/ 也在 import 路径上
_SRC_DIR = str(Path(__file__).resolve().parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# Windows 下强制 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from graphs.state import GraphInput, GraphOutput
from graphs.graph import main_graph
from tools.feishu_notifier import get_notifier
from tools.tweet_writer import write_tweets

console = Console(force_terminal=True, legacy_windows=False)
logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _build_input(args: argparse.Namespace) -> GraphInput:
    payload: Dict[str, Any] = {
        "feishu_app_token": args.feishu_app_token,
        "feishu_table_id": args.feishu_table_id,
        "feishu_page_id": args.feishu_page_id,
        "feishu_domain": args.feishu_domain,
        "is_wiki_embed": not args.no_wiki,
        "max_per_source": args.max_per_source,
        "min_heat_score": args.min_heat_score,
        "clear_dedup": args.clear_dedup,
        "write_to_feishu": args.write_to_feishu,
        "write_to_local": args.write_to_local,
    }
    return GraphInput(**payload)


def _assemble_output(result: Any) -> GraphOutput:
    """从图最终 state 提取 GraphOutput（兼容 dict / Pydantic），统计字段从 list 长度计算兜底。"""
    if isinstance(result, GraphOutput):
        return result
    if hasattr(result, "model_dump"):
        state = result.model_dump()
    elif isinstance(result, dict):
        state = result
    else:
        try:
            state = dict(result)
        except Exception:
            return GraphOutput()

    # 兜底统计（langgraph 状态合并对标量字段不稳定，直接从 list 长度算）
    total_collected = state.get("total_collected") or 0
    if not total_collected:
        for k in ("merged_materials", "aihot_materials", "ainews_materials",
                  "rss_materials", "tavily_materials", "github_materials"):
            v = state.get(k)
            if isinstance(v, list):
                total_collected = max(total_collected, len(v))
    # 优先用 total_collected（material_merge 设过），其次用 merged_materials 长度
    if not total_collected and isinstance(state.get("merged_materials"), list):
        total_collected = len(state["merged_materials"])

    total_after_dedup = state.get("total_after_dedup")
    if not total_after_dedup and isinstance(state.get("deduplicated_materials"), list):
        total_after_dedup = len(state["deduplicated_materials"])

    tweet_drafts = state.get("tweet_drafts") or []
    total_tweets = state.get("total_tweets") or len(tweet_drafts) if isinstance(tweet_drafts, list) else 0

    return GraphOutput(
        total_collected=total_collected or 0,
        total_after_dedup=total_after_dedup or 0,
        total_tweets=total_tweets,
        tweet_drafts=tweet_drafts if isinstance(tweet_drafts, list) else [],
        reject_events=state.get("reject_events") if isinstance(state.get("reject_events"), list) else [],
        output_path=state.get("output_path") or "",
        feishu_table_url=state.get("feishu_table_url") or "",
        feishu_record_ids=state.get("feishu_record_ids") or [],
        message=state.get("feishu_init_message") or state.get("run_message") or "done",
    )


def _print_summary(out: GraphOutput) -> None:
    table = Table(title="aiseclect 运行结果", show_lines=False)
    table.add_column("指标", style="cyan")
    table.add_column("数值", justify="right")
    table.add_row("采集总数", str(out.total_collected))
    table.add_row("去重后", str(out.total_after_dedup))
    table.add_row("推文草稿", str(out.total_tweets))
    table.add_row("飞书写入条数", str(len(out.feishu_record_ids)))
    table.add_row("本地文件", out.output_path or "(未生成)")
    table.add_row("飞书表格链接", out.feishu_table_url or "(未写入)")
    console.print(table)
    if out.tweet_drafts:
        console.print("\n[bold]推文预览（前 3 条）：[/bold]")
        for i, t in enumerate(out.tweet_drafts[:3], 1):
            console.print(f"\n[cyan]#{i}[/cyan] [bold]{t.title}[/bold]  (热 {t.heat_score:.0f})")
            console.print(f"  X: {t.tweet_content}")
            if t.platform in ("X+小红书", "X+通用内容"):
                console.print(f"  小红书: {t.other_title} | {t.other_content[:80]}")
                console.print(f"  配图提示词: {t.image_prompt[:100]}")
            else:
                console.print("  小红书: (仅X，未生成)")


async def _run(args: argparse.Namespace) -> GraphOutput:
    notifier = get_notifier()
    inp = _build_input(args)
    console.print(f"[bold green]启动工作流[/bold green]  输入: {inp.model_dump_json()}")
    state: Dict[str, Any] = inp.model_dump()
    try:
        result = await main_graph.ainvoke(state)
    except Exception as e:
        logger.exception("工作流主图异常")
        notifier.interactive(
            title="💥 aiseclect 主图崩溃",
            lines=[f"**异常**: `{type(e).__name__}`", f"**信息**: {str(e)[:500]}"],
            color="red",
        )
        raise

    out = _assemble_output(result)

    if args.write_to_local and (out.tweet_drafts or out.reject_events):
        path = write_tweets(out.tweet_drafts, rejects=out.reject_events)
        out.output_path = str(path)

    # 关键告警点
    if out.total_collected == 0:
        notifier.zero_materials("采集")
    elif out.total_after_dedup == 0:
        notifier.zero_materials("去重后")
    elif args.write_to_feishu and not out.feishu_record_ids and out.tweet_drafts:
        # 本地有推文但飞书一条没写进去 → 告警
        notifier.feishu_write_zero(drafted=len(out.tweet_drafts))

    # 跑完汇总
    if notifier.enabled:
        notifier.run_summary(
            total_collected=out.total_collected,
            total_after_dedup=out.total_after_dedup,
            total_tweets=out.total_tweets,
            feishu_written=len(out.feishu_record_ids),
            feishu_url=out.feishu_table_url,
        )

    return out


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="aiseclect flow runner")
    p.add_argument("--max-per-source", type=int, default=10)
    p.add_argument("--min-heat-score", type=float, default=40.0)
    p.add_argument("--clear-dedup", action="store_true")
    p.add_argument("--no-feishu", dest="write_to_feishu", action="store_false")
    p.add_argument("--no-local", dest="write_to_local", action="store_false")
    p.set_defaults(write_to_feishu=True, write_to_local=True)
    p.add_argument("--feishu-app-token", default=os.getenv("FEISHU_APP_TOKEN", ""))
    p.add_argument("--feishu-table-id", default=os.getenv("FEISHU_TABLE_ID", ""))
    p.add_argument("--feishu-page-id", default=os.getenv("FEISHU_PAGE_ID", ""))
    p.add_argument("--feishu-domain", default=os.getenv("FEISHU_DOMAIN", "my.feishu.cn"))
    p.add_argument("--no-wiki", action="store_true", help="非 Wiki 内嵌模式（独立表格）")
    return p.parse_args()


def main() -> int:
    load_dotenv()
    _setup_logging()
    args = _parse_args()
    try:
        out = asyncio.run(_run(args))
    except KeyboardInterrupt:
        console.print("[yellow]已中断[/yellow]")
        return 130
    _print_summary(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
