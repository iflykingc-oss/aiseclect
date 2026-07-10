"""
Tavily 搜索客户端（通用全网搜索）
- 文档: https://docs.tavily.com/
- API key 通过环境变量 TAVILY_API_KEY 传入
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

TAVILY_ENDPOINT = "https://api.tavily.com/search"


@dataclass
class TavilyItem:
    url: str
    title: str = ""
    snippet: str = ""
    content: str = ""
    raw_content: str = ""
    score: float = 0.0
    published_date: Optional[str] = None
    site_name: str = ""


@dataclass
class TavilyResponse:
    query: str
    items: List[TavilyItem] = field(default_factory=list)
    answer: str = ""


class TavilyClient:
    # 占位符 / 明显无效的 key（避免每次启动都打 401 刷屏）
    _PLACEHOLDER_KEYS = frozenset({
        "", "tvly-DEV-PLACEHOLDER-replace-with-real-key",
        "your-key-here", "changeme",
    })

    def __init__(self, api_key: Optional[str] = None, timeout: int = 8):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        if self.api_key in self._PLACEHOLDER_KEYS:
            logger.info("TAVILY_API_KEY 未配置或为占位符，Tavily 搜索跳过（用 feedgrab/rss 等替代）")
            self.api_key = ""
        elif not self.api_key:
            logger.warning("TAVILY_API_KEY 未配置，Tavily 搜索将全部返回空结果")
        self.timeout = timeout
        # 短路：第一次遇到 401/403 后不再重试，避免刷屏 + 阻塞整轮流程
        self._disabled: bool = False
        self._disable_reason: str = ""

    def search(
        self,
        query: str,
        max_results: int = 10,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        topic: str = "news",
        days: Optional[int] = None,
        include_raw_content: bool = True,
    ) -> TavilyResponse:
        """执行一次 Tavily 搜索。失败时返回空 results，不抛异常。短路机制：第一次 401/403 后整个客户端停用。"""
        if not self.api_key or self._disabled:
            return TavilyResponse(query=query)

        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "topic": topic,
            "include_answer": False,
            "include_raw_content": include_raw_content,
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains
        if days is not None:
            payload["days"] = days

        try:
            resp = requests.post(TAVILY_ENDPOINT, json=payload, timeout=self.timeout)
            if resp.status_code == 401 or resp.status_code == 403:
                # API key 失效 / 权限不够：立即短路整个 Tavily 调用，后续 query 直接返回空
                self._disabled = True
                self._disable_reason = f"HTTP {resp.status_code}"
                logger.error(
                    f"Tavily HTTP {resp.status_code}（API key 失效或权限不足），"
                    f"短路后续所有 Tavily 调用: {resp.text[:150]}"
                )
                return TavilyResponse(query=query)
            if resp.status_code != 200:
                logger.error(f"Tavily HTTP {resp.status_code}: {resp.text[:200]}")
                return TavilyResponse(query=query)
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"Tavily 网络异常: {e}")
            return TavilyResponse(query=query)
        except ValueError as e:
            logger.error(f"Tavily 响应解析失败: {e}")
            return TavilyResponse(query=query)

        items: List[TavilyItem] = []
        for raw in data.get("results", []) or []:
            url = raw.get("url", "")
            if not url:
                continue
            site = ""
            try:
                from urllib.parse import urlparse

                site = urlparse(url).netloc.replace("www.", "")
            except Exception:
                pass
            items.append(
                TavilyItem(
                    url=url,
                    title=raw.get("title", "") or "",
                    snippet=raw.get("content", "") or "",
                    content=raw.get("content", "") or "",
                    raw_content=raw.get("raw_content", "") or "",
                    score=float(raw.get("score", 0.0) or 0.0),
                    published_date=raw.get("published_date"),
                    site_name=site,
                )
            )
        return TavilyResponse(query=query, items=items)
