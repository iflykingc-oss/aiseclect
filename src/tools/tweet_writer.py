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


def write_tweets(
    drafts: List[TweetDraft | dict[str, Any]],
    prefix: str = "tweets",
    rejects: List[dict[str, Any]] | None = None,
) -> Path:
    """把推文草稿写到 ./output/{prefix}_YYYYMMDD_HHMMSS.json，同时写质量/拒绝报告。"""
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

    rejects = rejects or []

    report_path = out_dir / f"quality_report_{ts}.json"
    report = {
        "generated_at": generated_at,
        "count": len(drafts),
        "items": [
            {
                "unique_id": d.get("unique_id"),
                "url": d.get("url"),
                "title": d.get("title"),
                "source": d.get("source"),
                "category": d.get("category"),
                "heat_score": d.get("heat_score"),
                "score_reason": d.get("score_reason"),
                "discovery_reason": d.get("discovery_reason"),
                "platform": d.get("platform"),
                "platform_reason": d.get("platform_reason"),
                "content_angle": d.get("content_angle"),
                "hook_type": d.get("hook_type"),
                "x_quality_score": d.get("x_quality_score"),
                "xhs_quality_score": d.get("xhs_quality_score"),
                "quality_notes": d.get("quality_notes"),
                "tweet_preview": (d.get("tweet_content") or "")[:120],
                "xhs_title": d.get("other_title"),
            }
            for d in payload["tweets"]
        ],
    }
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    reject_path = out_dir / f"reject_report_{ts}.json"

    # 按 reject_kind 聚合分布（替代旧的 stage_stats）。保留 stage_stats 作为向后兼容别名。
    kind_stats: dict[str, int] = {}
    source_stats: dict[str, int] = {}
    reason_counter: dict[str, int] = {}
    for item in rejects:
        kind = str(item.get("reject_kind") or "unknown")
        kind_stats[kind] = kind_stats.get(kind, 0) + 1
        src = str(item.get("source") or "unknown")
        source_stats[src] = source_stats.get(src, 0) + 1
        # 高频 reject_reason（前 60 字符归一化，便于 prompt 优化时定位）
        reason_key = (str(item.get("reason") or "")).strip()
        if "首次失败:" in reason_key:
            reason_key = reason_key.split("首次失败:", 1)[1].strip()
        if "; 修复后失败:" in reason_key:
            reason_key = reason_key.split("; 修复后失败:", 1)[0].strip()
        reason_key = reason_key[:60].strip()
        if reason_key:
            reason_counter[reason_key] = reason_counter.get(reason_key, 0) + 1

    top_reject_reasons = sorted(
        reason_counter.items(), key=lambda kv: -kv[1]
    )[:10]

    reject_payload = {
        "generated_at": generated_at,
        "count": len(rejects),
        "items": rejects,
        "kind_stats": kind_stats,
        "source_stats": source_stats,
        "top_reject_reasons": [
            {"reason": r, "count": c} for r, c in top_reject_reasons
        ],
        "stage_stats": kind_stats,  # 向后兼容别名
    }
    with reject_path.open("w", encoding="utf-8") as f:
        json.dump(reject_payload, f, ensure_ascii=False, indent=2)

    logger.info(f"推文草稿已写入: {path} ({len(drafts)} 条)")
    logger.info(f"质量报告已写入: {report_path}")
    logger.info(
        f"拒绝报告已写入: {reject_path} ({len(rejects)} 条, "
        f"分布 {', '.join(f'{k}={v}' for k, v in sorted(kind_stats.items(), key=lambda kv: -kv[1]))})"
    )
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
        if len(d.get("other_content") or "") > 450:
            over_other += 1
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        lines.append(f"  - {cat}: {n}")
    lines.append(f"仅X: {only_x} 条")
    lines.append(f"X+小红书: {general} 条")
    lines.append(f"X 平台超 280 字符: {over_x} 条")
    lines.append(f"小红书正文超 450 字: {over_other} 条")
    lines.append(f"小红书缺配图提示词: {missing_image_prompt} 条")
    return "\n".join(lines)
