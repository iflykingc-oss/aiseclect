"""
跨 run 持久化去重状态。
- 默认存到 ./output/dedup_state.json
- 保存历史 URL 集合 + 上次运行时间
- 线程安全用 threading.Lock（langgraph 节点同步执行，足够）
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Iterable, Set

logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path(os.getenv("AISECLECT_OUTPUT_DIR", "output"))
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "dedup_state.json"
MAX_URL_HISTORY = int(os.getenv("AISECLECT_DEDUP_MAX", "5000"))


class DedupState:
    def __init__(self, path: Path = DEFAULT_STATE_FILE):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._seen: Set[str] = set()
        self._last_run: str = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self._seen = {u for u in (data.get("seen_urls") or []) if isinstance(u, str)}
            self._last_run = data.get("last_run", "")
            logger.info(f"加载去重状态: {len(self._seen)} 条历史 URL")
        except (OSError, ValueError) as e:
            logger.warning(f"去重状态文件损坏，忽略: {e}")
            self._seen = set()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 控制历史集合大小，超出时截断
            seen_list = list(self._seen)
            if len(seen_list) > MAX_URL_HISTORY:
                seen_list = seen_list[-MAX_URL_HISTORY:]
                self._seen = set(seen_list)
            payload = {
                "seen_urls": seen_list,
                "last_run": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            tmp = self.path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp.replace(self.path)

    def known(self) -> Set[str]:
        return set(self._seen)

    def add(self, urls: Iterable[str]) -> None:
        with self._lock:
            for u in urls:
                if isinstance(u, str) and u:
                    self._seen.add(u)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()
