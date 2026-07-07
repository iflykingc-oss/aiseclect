"""
AI HOT 客户端（卡兹克的中文 AI 资讯精选）
- 文档: https://aihot.virxact.com/agent
- 匿名只读，无需 token
- 必须带 User-Agent（默认 curl UA 被 nginx 黑名单 → 403）
- 单 IP 限流 600 r/min（burst 40）
- 支持 ETag/304 缓存：跨 run 持久化 ./output/aihot_etag.json
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

AIHOT_BASE = "https://aihot.virxact.com"
DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
VALID_CATEGORIES = {"ai-models", "ai-products", "industry", "paper", "tip"}


@dataclass
class AIHotItem:
    id: str
    title: str
    url: str
    permalink: str
    source: str
    published_at: Optional[str] = None
    title_en: Optional[str] = None
    summary: Optional[str] = None
    category: Optional[str] = None
    score: Optional[int] = None
    selected: bool = False


@dataclass
class AIHotResponse:
    count: int = 0
    has_next: bool = False
    next_cursor: Optional[str] = None
    items: List[AIHotItem] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


class AIHotClient:
    def __init__(self, timeout: int = 8, ua: Optional[str] = None):
        self.timeout = timeout
        self.ua = ua or os.getenv("AIHOT_USER_AGENT", DEFAULT_UA)
        self._etag_cache_path = Path(
            os.getenv("AISECLECT_OUTPUT_DIR", "output") + "/aihot_etag.json"
        )
        self._etag_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._etag_cache: Dict[str, str] = self._load_etag_cache()
        # 短路：第一次网络异常（connect timeout / DNS / refused）后停用整个 client，
        # 后续 3 个 collector 节点（aihot / ainews / tavily）都直接返回空，不再空等 30s × N
        self._network_disabled: bool = False
        self._network_disable_reason: str = ""

    def _load_etag_cache(self) -> Dict[str, str]:
        if not self._etag_cache_path.exists():
            return {}
        try:
            with open(self._etag_cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_etag_cache(self) -> None:
        try:
            with open(self._etag_cache_path, "w", encoding="utf-8") as f:
                json.dump(self._etag_cache, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"ETag 缓存保存失败: {e}")

    @staticmethod
    def _cache_key(endpoint: str, params: Dict[str, Any]) -> str:
        items = sorted(params.items())
        return f"{endpoint}?{'&'.join(f'{k}={v}' for k, v in items if v is not None)}"

    def _request(self, path: str, params: Dict[str, Any]) -> Optional[requests.Response]:
        url = f"{AIHOT_BASE}{path}"
        cache_key = self._cache_key(path, params)
        headers = {"User-Agent": self.ua}
        if cache_key in self._etag_cache:
            headers["If-None-Match"] = self._etag_cache[cache_key]
        # 短路：网络挂了直接返回 None，不再耗时
        if self._network_disabled:
            return None
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            self._network_disabled = True
            self._network_disable_reason = str(e)[:120]
            logger.error(
                f"AIHOT 网络异常（短路后续调用）: {e}；"
                f"原因: {self._network_disable_reason}"
            )
            return None
        if resp.status_code == 304:
            logger.debug(f"AIHOT 304: {cache_key}")
            return resp
        if resp.status_code == 429:
            logger.warning("AIHOT 触发限流（429），退避 2s")
            time.sleep(2.0)
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                logger.error(f"AIHOT 重试失败: {e}")
                return None
            if resp.status_code == 200:
                self._store_etag(cache_key, resp)
                return resp
            return None
        if resp.status_code != 200:
            logger.error(f"AIHOT HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        self._store_etag(cache_key, resp)
        return resp

    def _store_etag(self, cache_key: str, resp: requests.Response) -> None:
        etag = resp.headers.get("ETag")
        if etag:
            self._etag_cache[cache_key] = etag
            self._save_etag_cache()

    @staticmethod
    def _parse_item(raw: Dict[str, Any]) -> Optional[AIHotItem]:
        if not isinstance(raw, dict):
            return None
        try:
            return AIHotItem(
                id=raw.get("id", ""),
                title=raw.get("title", "") or "",
                url=raw.get("url", "") or "",
                permalink=raw.get("permalink", "") or "",
                source=raw.get("source", "") or "",
                published_at=raw.get("publishedAt"),
                title_en=raw.get("title_en"),
                summary=raw.get("summary"),
                category=raw.get("category"),
                score=raw.get("score") if isinstance(raw.get("score"), int) else None,
                selected=bool(raw.get("selected", False)),
            )
        except Exception as e:
            logger.warning(f"AIHOT item parse failed: {e}")
            return None

    # ---------- 端点 ----------

    def list_items(
        self,
        mode: str = "selected",
        category: Optional[str] = None,
        since: Optional[str] = None,
        take: int = 50,
        cursor: Optional[str] = None,
        q: Optional[str] = None,
        max_pages: int = 1,
    ) -> AIHotResponse:
        """GET /api/public/items。返回首个响应（支持翻页到 max_pages）。"""
        if category and category not in VALID_CATEGORIES:
            raise ValueError(f"invalid category: {category}")
        params: Dict[str, Any] = {"mode": mode, "take": take}
        if category:
            params["category"] = category
        if since:
            params["since"] = since
        if cursor:
            params["cursor"] = cursor
        if q:
            params["q"] = q

        agg = AIHotResponse()
        current_cursor = cursor
        for page in range(max_pages):
            if current_cursor:
                params["cursor"] = current_cursor
            resp = self._request("/api/public/items", params)
            if resp is None or resp.status_code != 200:
                break
            try:
                data = resp.json()
            except ValueError:
                break
            agg.raw = data
            agg.count = data.get("count", 0)
            agg.has_next = bool(data.get("hasNext", False))
            agg.next_cursor = data.get("nextCursor")
            for raw_item in data.get("items", []) or []:
                parsed = self._parse_item(raw_item)
                if parsed:
                    agg.items.append(parsed)
            if not agg.has_next or not agg.next_cursor:
                break
            current_cursor = agg.next_cursor
            time.sleep(0.2)  # 翻页礼貌间隔
        return agg

    def hot_topics(self, take: int = 20) -> List[Dict[str, Any]]:
        """GET /api/public/hot-topics。返回原始 items（带 sourceCount/sourceNames）。"""
        resp = self._request("/api/public/hot-topics", {"take": take})
        if resp is None or resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        return data.get("items", []) or []

    def daily(self, date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """GET /api/public/daily[/YYYY-MM-DD]。"""
        path = f"/api/public/daily/{date}" if date else "/api/public/daily"
        resp = self._request(path, {})
        if resp is None or resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None