"""
推文草稿本地落盘
- 输出文件: ./output/tweets_YYYYMMDD_HHMMSS.json
- 每条 draft 携带 generated_at 时间戳
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import List

from graphs.state import TweetDraft

logger = logging.getLogger(__name__)


def _output_dir() -> Path:
    import os

    return Path(os.getenv("AISECLECT_OUTPUT_DIR", "output"))


def write_tweets(drafts: List[TweetDraft], prefix: str = "tweets") -> Path:
    """把推文草稿写到 ./output/{prefix}_YYYYMMDD_HHMMSS.json。返回文件路径。"""
    out_dir = _output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{prefix}_{ts}.json"

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload = {
        "generated_at": generated_at,
        "count": len(drafts),
        "tweets": [d.model_dump() for d in drafts],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"推文草稿已写入: {path} ({len(drafts)} 条)")
    return path


def summarize(drafts: List[TweetDraft]) -> str:
    """控制台汇总：分类分布、字符数。"""
    if not drafts:
        return "未生成任何推文草稿"
    lines = [f"共生成 {len(drafts)} 条推文草稿："]
    by_cat: dict[str, int] = {}
    over_x = 0
    over_xhs = 0
    for d in drafts:
        by_cat[d.category] = by_cat.get(d.category, 0) + 1
        if len(d.tweet_content) > 280:
            over_x += 1
        if len(d.xiaohongshu_content) > 300:
            over_xhs += 1
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        lines.append(f"  - {cat}: {n}")
    lines.append(f"X 平台超 280 字符: {over_x} 条")
    lines.append(f"小红书超 300 字: {over_xhs} 条")
    return "\n".join(lines)
