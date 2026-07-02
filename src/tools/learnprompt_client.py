"""
LearnPrompt ai-news-radar 静态数据客户端
- 项目: https://github.com/LearnPrompt/ai-news-radar
- 在线页面: https://learnprompt.github.io/ai-news-radar/
- 数据源: GitHub Pages 上的 data/*.json（每 30 分钟由 GitHub Actions 更新）
- 公开读取，零鉴权，无需 LLM Key
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

BASE_URL = "https://learnprompt.github.io/ai-news-radar"
TIMEOUT = 30


@dataclass
class RadarItem:
    id: str
    site_id: str
    site_name: str
    title: str
    url: str
    published_at: Optional[str] = None
    ai_score: float = 0.0
    ai_label: Optional[str] = None
    source_tier_label: Optional[str] = None
    title_zh: Optional[str] = None
    title_en: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RadarStory:
    story_id: str
    title: str
    url: str
    primary_url: Optional[str] = None
    source_name: Optional[str] = None
    source_count: int = 0
    source_names: List[str] = field(default_factory=list)
    item_count: int = 0
    score: float = 0.0
    importance_label: Optional[str] = None
    category: Optional[str] = None
    earliest_at: Optional[str] = None
    latest_at: Optional[str] = None
    items: List[Dict[str, Any]] = field(default_factory=list)


class LearnPromptRadarClient:
    def __init__(self, timeout: int = TIMEOUT):
        self.timeout = timeout
        self._cache_dir = Path(os.getenv("AISECLECT_OUTPUT_DIR", "output") + "/learnprompt_cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_etag: Dict[str, str] = {}

    def _fetch(self, name: str) -> Optional[Dict[str, Any]]:
        """读取 data/{name}.json，带 ETag 缓存。"""
        url = f"{BASE_URL}/data/{name}.json"
        cache_path = self._cache_dir / f"{name}.json"
        headers = {"User-Agent": "Mozilla/5.0 (aiseclect-bot)"}
        if cache_path.exists() and name in self._last_etag:
            headers["If-None-Match"] = self._last_etag[name]
        try:
            resp = requests.get(url, timeout=self.timeout, headers=headers)
        except requests.RequestException as e:
            logger.warning(f"LearnPrompt {name} 网络异常，回退本地缓存: {e}")
            return self._read_cache(name)
        if resp.status_code == 304 and cache_path.exists():
            logger.debug(f"LearnPrompt {name} 304 命中本地缓存")
            return self._read_cache(name)
        if resp.status_code != 200:
            logger.warning(f"LearnPrompt {name} HTTP {resp.status_code}，回退本地缓存")
            return self._read_cache(name)
        try:
            data = resp.json()
        except ValueError:
            return self._read_cache(name)
        # 写缓存 + ETag
        try:
            cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.debug(f"LearnPrompt 缓存写入失败: {e}")
        etag = resp.headers.get("ETag")
        if etag:
            self._last_etag[name] = etag
        return data

    def _read_cache(self, name: str) -> Optional[Dict[str, Any]]:
        cache_path = self._cache_dir / f"{name}.json"
        if not cache_path.exists():
            return None
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ---------- 公开方法 ----------

    def latest_24h(self) -> List[RadarItem]:
        """data/latest-24h.json —— AI 强相关消息（已过滤）。"""
        data = self._fetch("latest-24h")
        if not data:
            return []
        items: List[RadarItem] = []
        for raw in data.get("items", []) or []:
            if not isinstance(raw, dict):
                continue
            try:
                items.append(
                    RadarItem(
                        id=raw.get("id", ""),
                        site_id=raw.get("site_id", ""),
                        site_name=raw.get("site_name", "") or raw.get("source", ""),
                        title=raw.get("title", "") or raw.get("title_zh", "") or "",
                        url=raw.get("url", ""),
                        published_at=raw.get("published_at"),
                        ai_score=float(raw.get("ai_score", 0) or 0),
                        ai_label=raw.get("ai_label"),
                        source_tier_label=raw.get("source_tier_label"),
                        title_zh=raw.get("title_zh"),
                        title_en=raw.get("title_en"),
                        extra={k: v for k, v in raw.items() if k not in {
                            "id", "site_id", "site_name", "source", "title", "url",
                            "published_at", "ai_score", "ai_label", "source_tier_label",
                            "title_zh", "title_en",
                        }},
                    )
                )
            except Exception as e:
                logger.debug(f"LearnPrompt item parse failed: {e}")
        return items

    def daily_brief(self) -> List[RadarStory]:
        """data/daily-brief.json —— 伯乐精选故事线（多源聚合后的 top story）。"""
        data = self._fetch("daily-brief")
        if not data:
            return []
        return [self._parse_story(s) for s in (data.get("items") or []) if isinstance(s, dict)]

    def stories_merged(self, top: Optional[int] = None) -> List[RadarStory]:
        """data/stories-merged.json —— 全量故事合并池。"""
        data = self._fetch("stories-merged")
        if not data:
            return []
        raw_list = data.get("stories") or []
        if top:
            raw_list = raw_list[:top]
        return [self._parse_story(s) for s in raw_list if isinstance(s, dict)]

    def source_status(self) -> Dict[str, Any]:
        """data/source-status.json —— 源健康状态。"""
        data = self._fetch("source-status")
        return data or {}

    @staticmethod
    def _parse_story(raw: Dict[str, Any]) -> RadarStory:
        return RadarStory(
            story_id=raw.get("story_id", ""),
            title=raw.get("title", ""),
            url=raw.get("url", ""),
            primary_url=raw.get("primary_url"),
            source_name=raw.get("source_name") or raw.get("source"),
            source_count=int(raw.get("source_count", 0) or 0),
            source_names=list(raw.get("source_names") or []),
            item_count=int(raw.get("item_count", 0) or 0),
            score=float(raw.get("score", 0) or 0),
            importance_label=raw.get("importance_label"),
            category=raw.get("category"),
            earliest_at=raw.get("earliest_at"),
            latest_at=raw.get("latest_at"),
            items=list(raw.get("items") or []),
        )