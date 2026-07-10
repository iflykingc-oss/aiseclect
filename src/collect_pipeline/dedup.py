"""跨 run URL 去重（线程安全 + 文件持久化 + 插入顺序保留）

用法：
    dedup = CrossRunDedup(path="./output/dedup_state.json")
    new_urls = dedup.filter_new(["https://a", "https://b", "https://a"])
    # → ["https://a", "https://b"]   ← 第一次出现的保留，重复的跳过
    dedup.add(new_urls)
    dedup.save()  # 持久化到文件
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

MAX_URL_HISTORY_DEFAULT = 5000


class CrossRunDedup:
    """跨 run 持久化去重状态。

    特性：
    - 线程安全（threading.Lock）
    - 插入顺序保留（OrderedDict），FIFO 截断按插入顺序而非 set 迭代顺序
    - filter_new 移除输入列表内的重复 + 已见 URL，仅保留首次出现
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        max_history: Optional[int] = None,
    ):
        self.path = Path(path or os.getenv("COLLECT_PIPELINE_DEDUP_PATH", "dedup_state.json"))
        self.max_history = max_history or int(os.getenv("COLLECT_PIPELINE_DEDUP_MAX", MAX_URL_HISTORY_DEFAULT))
        self._lock = threading.Lock()
        self._seen: "OrderedDict[str, None]" = OrderedDict()  # 保留插入顺序
        self._last_run: str = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self._seen = OrderedDict((u, None) for u in (data.get("seen_urls") or []) if isinstance(u, str))
            self._last_run = data.get("last_run", "")
            logger.info(f"加载去重状态: {len(self._seen)} 条历史 URL ({self.path})")
        except (OSError, ValueError) as e:
            logger.warning(f"去重状态文件损坏，忽略: {e}")
            self._seen = OrderedDict()

    def save(self) -> None:
        """原子写入：先写 .tmp 再 rename，避免半截文件。"""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # FIFO 截断：保留最新 max_history 条（按插入顺序）
            while len(self._seen) > self.max_history:
                self._seen.popitem(last=False)
            payload = {
                "seen_urls": list(self._seen.keys()),
                "last_run": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)

    def known(self) -> Set[str]:
        """返回当前已知 URL 集合的副本。"""
        with self._lock:
            return set(self._seen.keys())

    def add(self, urls: Iterable[str]) -> None:
        """批量添加 URL（去重 + 过滤空值 + 保留首次插入顺序）。"""
        with self._lock:
            for u in urls:
                if isinstance(u, str) and u and u not in self._seen:
                    self._seen[u] = None

    def filter_new(self, urls: Iterable[str]) -> List[str]:
        """保留首次出现的 URL（移除输入内的重复 + 过滤已见）。"""
        new: List[str] = []
        with self._lock:
            for u in urls:
                if not isinstance(u, str) or not u:
                    continue
                if u in self._seen:
                    continue
                if u in new:  # 同一 batch 内去重
                    continue
                new.append(u)
        return new

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._seen)

    @property
    def last_run(self) -> str:
        return self._last_run