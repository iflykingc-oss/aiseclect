"""
Last30days 客户端（第 9 路采集，可选 CLI）
- GitHub: https://github.com/mvanhorn/last30days-skill
- 把过去 30 天 X / Reddit / Hacker News / YouTube 上大家真在聊的东西抓回来，按真实互动量排序
- 通过 subprocess 调用 last30days CLI（不在则 graceful 返回空）
- LAST30DAYS_BIN 环境变量可自定义二进制路径（默认 `last30days`）

设计原则（与 feedgrab 一致）：
- CLI 不在 / 失败 → 返回空列表（不抛异常，不阻塞主流程）
- 单进程超时 60s，避免阻塞节点整体 30s 预算
- 输出尝试解析 JSON；JSON 解析失败时按行兜底（取首列当标题）
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60  # 单次调用超时（秒）


@dataclass
class Last30daysItem:
    title: str = ""
    url: str = ""
    platform: str = ""  # x | reddit | hackernews | youtube
    engagement: float = 0.0  # 真实互动量（具体语义由 CLI 决定）
    snippet: str = ""
    extra_data: dict = field(default_factory=dict)


def _resolve_bin() -> Optional[str]:
    """解析二进制路径。优先 LAST30DAYS_BIN 环境变量，其次 PATH 查找。"""
    bin_env = os.getenv("LAST30DAYS_BIN", "").strip()
    if bin_env:
        return bin_env
    return shutil.which("last30days")


def _parse_json_output(stdout: str) -> List[dict]:
    """尝试把 stdout 当 JSON 解析。兼容顶层数组、顶层对象含 items/results/data 字段。"""
    stdout = (stdout or "").strip()
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
    except ValueError:
        return []

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "results", "data", "topics"):
            v = data.get(key)
            if isinstance(v, list):
                return [item for item in v if isinstance(item, dict)]
    return []


def _normalize_item(raw: dict, idx: int) -> Optional[Last30daysItem]:
    """把 CLI 返回的 dict 标准化成 Last30daysItem。无 URL 的丢弃。"""
    url = str(raw.get("url") or raw.get("link") or raw.get("permalink") or "").strip()
    if not url:
        return None
    title = str(raw.get("title") or raw.get("name") or "").strip() or f"last30days-{idx}"
    snippet = str(raw.get("snippet") or raw.get("description") or raw.get("summary") or "").strip()
    platform = str(raw.get("platform") or raw.get("source") or raw.get("network") or "").strip().lower()
    # 互动量字段命名随 CLI 而异，兼容多种
    eng = raw.get("engagement") or raw.get("score") or raw.get("points") or raw.get("likes") or raw.get("upvotes") or 0
    try:
        engagement = float(eng)
    except (TypeError, ValueError):
        engagement = 0.0
    return Last30daysItem(
        title=title,
        url=url,
        platform=platform,
        engagement=engagement,
        snippet=snippet[:500],
        extra_data={"raw_keys": list(raw.keys())[:20]},  # 调试用，记录 CLI 实际返回字段
    )


def fetch_topics(
    queries: Optional[List[str]] = None,
    max_results: int = 20,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[Last30daysItem]:
    """调 last30days CLI 抓热点。CLI 不在 → 返回 []。

    Args:
        queries: 传给 CLI 的查询列表（话题标签 / 关键词）。空 → 让 CLI 用默认策略。
        max_results: 单次调用上限（透传给 CLI `--limit` / `--max` / `--top`，具体看版本）
        timeout: 子进程超时（秒）

    Returns:
        List[Last30daysItem]，失败 / 无 CLI 时为空列表
    """
    bin_path = _resolve_bin()
    if not bin_path:
        logger.debug("last30days CLI 未安装或不在 PATH（设 LAST30DAYS_BIN 指向自定义二进制）")
        return []

    cmd = [bin_path]
    # 尝试传 query 参数（兼容性：--query / --topic 都试试）
    if queries:
        cmd.extend(["--query", ",".join(queries[:5])])
    cmd.extend(["--limit", str(max_results)])
    # 优先 JSON 输出
    cmd.append("--json")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"last30days 子进程超时 ({timeout}s)")
        return []
    except FileNotFoundError:
        logger.warning(f"last30days 二进制不存在: {bin_path}")
        return []
    except Exception as e:
        logger.warning(f"last30days 子进程异常: {type(e).__name__}: {e}")
        return []

    if proc.returncode != 0:
        logger.warning(f"last30days 退出码 {proc.returncode}: {proc.stderr[:200]}")
        return []

    raw_items = _parse_json_output(proc.stdout)
    items: List[Last30daysItem] = []
    for idx, raw in enumerate(raw_items):
        norm = _normalize_item(raw, idx)
        if norm is not None:
            items.append(norm)
    return items[:max_results]


def is_available() -> bool:
    """运行时检查：last30days CLI 是否可用。"""
    return _resolve_bin() is not None