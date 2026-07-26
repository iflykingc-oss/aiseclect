"""
Firecrawl 抓取客户端（干净 Markdown 提取）
- 文档: https://docs.firecrawl.dev/
- API key 通过环境变量 FIRECRAWL_API_KEY 传入
- 与 content_enricher_node 集成：先用 Firecrawl 拿 markdown，失败/无 key 降级到 requests + 正则清洗

设计要点：
- 避免把 firecrawl-py 列为硬依赖：SDK 不在时只 import requests 路径
- 占位 key 探测：未配置或明显无效时直接 raise _FirecrawlDisabled，跳过整段
- 单一职责：只做 scrape → markdown，不做分类/打分
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
DEFAULT_TIMEOUT = 10  # 单 URL 超时（秒），比 requests 路径长，因为 markdown 提取更慢


class _FirecrawlDisabled(Exception):
    """Firecrawl 未配置 / key 不可用 → 调用方应降级到 requests 路径。"""


@dataclass
class FirecrawlResult:
    url: str
    markdown: str = ""
    metadata_title: str = ""
    metadata_description: str = ""
    success: bool = False
    error: str = ""


def _is_placeholder_key(key: str) -> bool:
    """占位 key（未配置或明显无效）检测，避免每次启动都打 401 刷屏。"""
    if not key:
        return True
    k = key.strip().lower()
    if k.startswith("fc-xxx") or "placeholder" in k or "replace-with-real" in k:
        return True
    # 真实 key 通常以 fc- 开头 + 长度 >= 20
    if k.startswith("fc-") and len(k) >= 20:
        return False
    # 允许其他格式（自部署 Firecrawl）但要求至少 16 字符
    return len(k) < 16


def scrape_url(url: str, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT) -> FirecrawlResult:
    """抓单个 URL 的 markdown。失败或未配置时返回 success=False + error。

    调用方约定：
    - 未配置 key → 走 requests 兜底
    - HTTP 401/403 → 走 requests 兜底（key 错）
    - HTTP 5xx / 网络异常 → 走 requests 兜底
    - HTTP 200 但 markdown 为空 → 也视为失败，兜底
    """
    key = api_key or os.getenv("FIRECRAWL_API_KEY", "")
    if _is_placeholder_key(key):
        return FirecrawlResult(url=url, success=False, error="FIRECRAWL_API_KEY not configured")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "timeout": timeout * 1000,  # Firecrawl 单位是 ms
    }

    try:
        resp = requests.post(
            FIRECRAWL_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return FirecrawlResult(url=url, success=False, error=f"network: {type(e).__name__}")

    if resp.status_code != 200:
        return FirecrawlResult(url=url, success=False, error=f"http {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        return FirecrawlResult(url=url, success=False, error="invalid json")

    if not data.get("success", False):
        return FirecrawlResult(url=url, success=False, error=str(data.get("error", "unknown"))[:100])

    md = (data.get("data") or {}).get("markdown", "") or ""
    meta = (data.get("data") or {}).get("metadata") or {}
    if not md.strip():
        return FirecrawlResult(url=url, success=False, error="empty markdown")

    return FirecrawlResult(
        url=url,
        markdown=md[:20000],  # 单 URL 上限 20k 字符，避免 LLM 上下文爆炸
        metadata_title=str(meta.get("title", "") or ""),
        metadata_description=str(meta.get("description", "") or ""),
        success=True,
    )


def is_available() -> bool:
    """运行时检查：Firecrawl 是否可用（key 已配置且非占位）。"""
    return not _is_placeholder_key(os.getenv("FIRECRAWL_API_KEY", ""))