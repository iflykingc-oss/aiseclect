"""
MediaCrawler 客户端（第 13 路，可选 CLI）

GitHub: https://github.com/NanmiCoder/MediaCrawler
支持平台：小红书 / 抖音 / 快手 / B站 / 微博 / 百度贴吧 / 知乎

风险评估见 MEDIACRAWLER_EVAL.md：
- 需要登录（QR 码或 cookie）
- 国内合规风险，README 自述「不保证不违法」
- Playwright 重依赖
- 建议仅作可选采集器，不接主路径

设计原则：
- 与 last30days / feedgrab 一致：subprocess 调用 CLI，不在则 graceful 返回空
- MEDIACRAWLER_BIN 环境变量指定二进制路径（默认 `mediacrawler`）
- 输出 JSON 数组；解析失败返回空（不抛异常）
- 与 aiseclect 已有 feedgrab 重叠（小红书 / 知乎 / yt），用户需自决
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

DEFAULT_TIMEOUT = 120  # 单次调用超时（秒）— Playwright 启动慢
SUPPORTED_PLATFORMS = ("xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu")


@dataclass
class MediaCrawlerItem:
    platform: str = ""
    title: str = ""
    url: str = ""
    content: str = ""
    author: str = ""
    likes: int = 0
    comments: int = 0
    extra_data: dict = field(default_factory=dict)


def _resolve_bin() -> Optional[str]:
    """解析二进制路径。优先 MEDIACRAWLER_BIN 环境变量。"""
    bin_env = os.getenv("MEDIACRAWLER_BIN", "").strip()
    if bin_env:
        return bin_env
    return shutil.which("mediacrawler")


def _parse_json_output(stdout: str) -> List[dict]:
    """解析 CLI stdout 为 dict 列表。"""
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
        for key in ("items", "results", "data", "posts"):
            v = data.get(key)
            if isinstance(v, list):
                return [item for item in v if isinstance(item, dict)]
    return []


def _normalize(raw: dict, idx: int) -> Optional[MediaCrawlerItem]:
    """dict → MediaCrawlerItem。无 URL 的丢弃。"""
    url = str(raw.get("url") or raw.get("link") or "").strip()
    if not url:
        return None
    title = str(raw.get("title") or raw.get("desc") or "").strip() or f"mediacrawler-{idx}"
    platform = str(raw.get("platform") or "").strip().lower()
    content = str(raw.get("content") or raw.get("text") or "").strip()[:1000]
    try:
        likes = int(raw.get("likes") or raw.get("liked_count") or 0)
    except (TypeError, ValueError):
        likes = 0
    try:
        comments = int(raw.get("comments") or raw.get("comment_count") or 0)
    except (TypeError, ValueError):
        comments = 0
    return MediaCrawlerItem(
        platform=platform,
        title=title,
        url=url,
        content=content,
        author=str(raw.get("author") or raw.get("user") or "").strip(),
        likes=likes,
        comments=comments,
        extra_data={"raw_keys": list(raw.keys())[:20]},
    )


def fetch_topics(
    platform: str = "xhs",
    keywords: Optional[List[str]] = None,
    max_results: int = 20,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[MediaCrawlerItem]:
    """调 MediaCrawler CLI 抓指定平台的热点。

    Args:
        platform: 目标平台（xhs/dy/ks/bili/wb/tieba/zhihu）
        keywords: 搜索关键词列表（空 → 用 CLI 默认）
        max_results: 上限
        timeout: 子进程超时（秒）

    Returns:
        List[MediaCrawlerItem]，CLI 不在 / 失败时为 []
    """
    if platform not in SUPPORTED_PLATFORMS:
        logger.warning(f"MediaCrawler 不支持平台 {platform}，跳过")
        return []

    bin_path = _resolve_bin()
    if not bin_path:
        logger.debug("MediaCrawler CLI 未安装（设 MEDIACRAWLER_BIN 指向自定义二进制）")
        return []

    # CLI 调用约定（参考 README）：
    # mediacrawler --platform xhs --keywords "ai,工具" --type search --max 20 --output json
    cmd = [bin_path, "--platform", platform, "--type", "search", "--max", str(max_results), "--output", "json"]
    if keywords:
        cmd.extend(["--keywords", ",".join(keywords[:5])])

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
        logger.warning(f"MediaCrawler 子进程超时 ({timeout}s)")
        return []
    except FileNotFoundError:
        logger.warning(f"MediaCrawler 二进制不存在: {bin_path}")
        return []
    except Exception as e:
        logger.warning(f"MediaCrawler 子进程异常: {type(e).__name__}: {e}")
        return []

    if proc.returncode != 0:
        logger.warning(f"MediaCrawler 退出码 {proc.returncode}: {proc.stderr[:200]}")
        return []

    raw_items = _parse_json_output(proc.stdout)
    items: List[MediaCrawlerItem] = []
    for idx, raw in enumerate(raw_items):
        norm = _normalize(raw, idx)
        if norm is not None:
            # platform 字段兜底（CLI 不返回时用入参）
            if not norm.platform:
                norm.platform = platform
            items.append(norm)
    return items[:max_results]


def is_available() -> bool:
    """运行时检查：MediaCrawler CLI 是否可用。"""
    return _resolve_bin() is not None