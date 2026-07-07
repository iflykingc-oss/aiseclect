"""
长驻调度器：每 N 小时跑一次工作流
- 默认 4 小时一次（可配）
- 第一次启动会立刻跑一次
- 单次失败不影响下一轮
- Ctrl+C 优雅退出
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# 让脚本独立运行时也能 import src 包
_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from dotenv import load_dotenv

from graphs.state import GraphInput
from graphs.graph import main_graph
from tools.tweet_writer import write_tweets

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("scheduler")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="aiseclect 定时调度器")
    p.add_argument("--interval-hours", type=float, default=4.0, help="间隔小时数（默认 4）")
    p.add_argument("--max-per-source", type=int, default=6)
    p.add_argument("--min-heat-score", type=float, default=30.0)
    p.add_argument("--clear-dedup", action="store_true")
    p.add_argument("--no-feishu", dest="write_to_feishu", action="store_false")
    p.add_argument("--no-local", dest="write_to_local", action="store_false")
    p.set_defaults(write_to_feishu=True, write_to_local=True)
    p.add_argument("--feishu-app-token", default=os.getenv("FEISHU_APP_TOKEN", ""))
    p.add_argument("--feishu-table-id", default=os.getenv("FEISHU_TABLE_ID", ""))
    p.add_argument("--feishu-page-id", default=os.getenv("FEISHU_PAGE_ID", ""))
    p.add_argument("--feishu-domain", default=os.getenv("FEISHU_DOMAIN", "my.feishu.cn"))
    p.add_argument("--no-wiki", action="store_true")
    return p.parse_args()


async def _run_once(args: argparse.Namespace) -> None:
    inp = GraphInput(
        feishu_app_token=args.feishu_app_token,
        feishu_table_id=args.feishu_table_id,
        feishu_page_id=args.feishu_page_id,
        feishu_domain=args.feishu_domain,
        is_wiki_embed=not args.no_wiki,
        max_per_source=args.max_per_source,
        min_heat_score=args.min_heat_score,
        clear_dedup=args.clear_dedup,
        write_to_feishu=args.write_to_feishu,
        write_to_local=args.write_to_local,
    )
    state = inp.model_dump()
    try:
        result = await main_graph.ainvoke(state)
        drafts = result.get("tweet_drafts") if isinstance(result, dict) else None
        if args.write_to_local and isinstance(drafts, list) and drafts:
            write_tweets(drafts)
        n = len(drafts) if isinstance(drafts, list) else 0
        logger.info(f"本轮结束: 生成 {n} 条推文")
    except Exception as e:
        logger.exception(f"本轮失败: {e}")


def main() -> int:
    load_dotenv()
    args = _parse_args()
    interval_sec = args.interval_hours * 3600

    stop_flag = {"stop": False}

    def _on_signal(signum, _frame):
        logger.info(f"收到信号 {signum}，准备退出...")
        stop_flag["stop"] = True

    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    logger.info(f"调度器启动: 每 {args.interval_hours} 小时一次，按 Ctrl+C 退出")

    # 立即跑一次
    next_run = datetime.now()
    while not stop_flag["stop"]:
        now = datetime.now()
        if now >= next_run:
            logger.info(f"====== 开始新一轮 {now.isoformat(timespec='seconds')} ======")
            try:
                asyncio.run(_run_once(args))
            except KeyboardInterrupt:
                stop_flag["stop"] = True
                break
            next_run = now + timedelta(seconds=interval_sec)
            logger.info(f"下一轮: {next_run.isoformat(timespec='seconds')}")

        # 每 30 秒醒一次检查
        for _ in range(30):
            if stop_flag["stop"]:
                break
            time.sleep(1)

    logger.info("调度器已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
