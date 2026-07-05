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
from typing import Any, List

from graphs.state import TweetDraft

logger = logging.getLogger(__name__)


def _output_dir() -> Path:
    import os

    return Path(os.getenv("AISECLECT_OUTPUT_DIR", "output"))


def _draft_dump(draft: TweetDraft | dict[str, Any]) -> dict[str, Any]:
    if hasattr(draft, "model_dump"):
        return draft.model_dump()
    if isinstance(draft, dict):
        return draft
    return dict(draft)


def write_tweets(drafts: List[TweetDraft | dict[str, Any]], prefix: str = "tweets") -> Path:
    """把推文草稿写到 ./output/{prefix}_YYYYMMDD_HHMMSS.json。返回文件路径。"""
    out_dir = _output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{prefix}_{ts}.json"

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload = {
        "generated_at": generated_at,
        "count": len(drafts),
        "tweets": [_draft_dump(d) for d in drafts],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"推文草稿已写入: {path} ({len(drafts)} 条)")
    return path


def summarize(drafts: List[TweetDraft | dict[str, Any]]) -> str:
    """控制台汇总：分类分布、字符数、平台分布。"""
    if not drafts:
        return "未生成任何推文草稿"
    items = [_draft_dump(d) for d in drafts]
    lines = [f"共生成 {len(items)} 条内容草稿："]
    by_cat: dict[str, int] = {}
    only_x = 0
    general = 0
    over_x = 0
    over_other = 0
    missing_image_prompt = 0
    for d in items:
        cat = d.get("category") or "未分类"
        by_cat[cat] = by_cat.get(cat, 0) + 1
        platform = d.get("platform") or ""
        if platform == "仅X":
            only_x += 1
        else:
            general += 1
            if not d.get("image_prompt"):
                missing_image_prompt += 1
        if len(d.get("tweet_content") or "") > 280:
            over_x += 1
        if len(d.get("other_content") or "") > 550:
            over_other += 1
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        lines.append(f"  - {cat}: {n}")
    lines.append(f"仅X: {only_x} 条")
    lines.append(f"X+通用内容: {general} 条")
    lines.append(f"X 平台超 280 字符: {over_x} 条")
    lines.append(f"通用内容超 550 字: {over_other} 条")
    lines.append(f"通用内容缺配图提示词: {missing_image_prompt} 条")
    return "\n".join(lines)
