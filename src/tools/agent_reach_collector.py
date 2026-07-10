"""
Agent-Reach 采集器（aiseclect 第 7 路）
- 通过 subprocess 调用 agent-reach CLI
- graceful degradation：CLI 不在就返回空列表（不阻塞主流程）
- 支持 platform: web / youtube / github / twitter search / reddit search 等
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import List, Optional

from graphs.state import RawMaterial

logger = logging.getLogger(__name__)

AGENT_REACH_TIMEOUT = 60  # 60s 超时（YouTube 字幕可能较慢）


def _agent_reach_available() -> bool:
    """检查 agent-reach CLI 是否在 PATH 里。"""
    try:
        result = subprocess.run(
            ["agent-reach", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _call_agent_reach(platform: str, *args: str) -> Optional[str]:
    """调用 agent-reach CLI，返回 stdout。失败/超时/不存在返回 None。"""
    try:
        result = subprocess.run(
            ["agent-reach", platform, *args],
            capture_output=True, text=True, timeout=AGENT_REACH_TIMEOUT,
        )
        if result.returncode != 0:
            logger.warning(f"agent-reach {platform} 退出码 {result.returncode}: {result.stderr[:200]}")
            return None
        return result.stdout
    except FileNotFoundError:
        logger.info("agent-reach CLI 不在 PATH，跳过（请 `pip install git+https://github.com/Panniantong/Agent-Reach.git`）")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"agent-reach {platform} 超时（>{AGENT_REACH_TIMEOUT}s）")
        return None


def _parse_output(raw: str) -> List[RawMaterial]:
    """解析 agent-reach 输出为 RawMaterial 列表。
    支持 JSON 数组 或「标题\\nURL」逐行格式。
    """
    if not raw:
        return []
    raw = raw.strip()

    # 优先尝试 JSON
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [
                RawMaterial(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    source="agent_reach",
                    snippet=item.get("summary", "") or item.get("snippet", ""),
                    extra_data=item.get("metadata", {}) or {},
                )
                for item in data
                if isinstance(item, dict) and item.get("url")
            ]
    except json.JSONDecodeError:
        pass

    # 回退：逐行解析（格式：标题\\tURL 或 标题\\nURL）
    items: List[RawMaterial] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t") if "\t" in line else line.split(" ", 1)
        if len(parts) < 2:
            continue
        title, url = parts[0].strip(), parts[1].strip()
        if not url.startswith(("http://", "https://")):
            continue
        items.append(RawMaterial(title=title, url=url, source="agent_reach"))
    return items


def agent_reach_collector(
    platform: str = "web",
    query: Optional[str] = None,
    url: Optional[str] = None,
) -> List[RawMaterial]:
    """主入口。

    Args:
        platform: agent-reach 子命令（web/youtube/github/twitter/reddit/bili/rss 等）
        query: 搜索关键词（twitter/reddit search 模式）
        url: 单个 URL（web/youtube 单条模式）

    Returns:
        RawMaterial 列表；CLI 不可用时返回 []
    """
    if not _agent_reach_available():
        return []

    if url:
        raw = _call_agent_reach(platform, url)
    elif query:
        raw = _call_agent_reach(platform, "search", query)
    else:
        raw = _call_agent_reach(platform)

    items = _parse_output(raw or "")
    logger.info(f"agent_reach[{platform}] 抓到 {len(items)} 条")
    return items