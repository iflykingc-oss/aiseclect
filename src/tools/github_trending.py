"""
GitHub Trending 采集
- 使用 GitHub Search API（无需鉴权也可调用，但限流严格；带 GITHUB_TOKEN 更稳）
- 文档: https://docs.github.com/en/rest/search
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

GH_SEARCH_ENDPOINT = "https://api.github.com/search/repositories"


@dataclass
class GitHubRepo:
    url: str
    name: str = ""
    full_name: str = ""
    description: str = ""
    stars: int = 0
    language: str = ""
    topics: List[str] = field(default_factory=list)
    pushed_at: Optional[str] = None
    created_at: Optional[str] = None
    site_name: str = "github.com"


class GitHubTrendingClient:
    def __init__(self, token: Optional[str] = None, timeout: int = 30):
        self.token = token or os.getenv("GITHUB_TOKEN", "")
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aiseclect-bot",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def trending(
        self,
        keywords: str = "AI OR llm OR agent",
        days: int = 30,
        max_results: int = 15,
        language: Optional[str] = None,
    ) -> List[GitHubRepo]:
        """按 stars 倒序拉取最近 N 天内有 push、关键字匹配的仓库。"""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        query_parts = [keywords, f"pushed:>={since}"]
        if language:
            query_parts.append(f"language:{language}")
        q = " ".join(query_parts)

        params = {
            "q": q,
            "sort": "stars",
            "order": "desc",
            "per_page": min(max_results, 50),
        }
        try:
            resp = requests.get(
                GH_SEARCH_ENDPOINT,
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                logger.error(f"GitHub Search HTTP {resp.status_code}: {resp.text[:200]}")
                return []
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"GitHub Search 网络异常: {e}")
            return []
        except ValueError as e:
            logger.error(f"GitHub Search 响应解析失败: {e}")
            return []

        repos: List[GitHubRepo] = []
        for raw in data.get("items", []) or []:
            html_url = raw.get("html_url", "")
            if not html_url:
                continue
            repos.append(
                GitHubRepo(
                    url=html_url,
                    name=raw.get("name", ""),
                    full_name=raw.get("full_name", ""),
                    description=raw.get("description", "") or "",
                    stars=int(raw.get("stargazers_count", 0) or 0),
                    language=raw.get("language", "") or "",
                    topics=list(raw.get("topics", []) or []),
                    pushed_at=raw.get("pushed_at"),
                    created_at=raw.get("created_at"),
                )
            )
        return repos
