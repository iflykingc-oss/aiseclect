"""
轻量级编辑记忆（2026-07-26）

不追踪发布后表现（项目硬约束），只追踪内部 pipeline 信号：
- recent_articles[]: 最近 N 条成功生成的草稿（标题 / 来源 / pillar / platform / quality_score / timestamp）
- recent_angle_titles[]: 最近 N 个角度（用于 prompt 注入，避免重复角度）

实现：FIFO JSON 文件，文件级锁，并发安全。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = "output/editorial_memory.json"
DEFAULT_MAX_ITEMS = 50
_LOCK = threading.Lock()


@dataclass
class MemoryItem:
    unique_id: str
    title: str
    source: str
    category: str
    platform: str
    quality_score: float = 0.0
    pillar: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "MemoryItem":
        return MemoryItem(
            unique_id=d.get("unique_id", ""),
            title=d.get("title", ""),
            source=d.get("source", ""),
            category=d.get("category", ""),
            platform=d.get("platform", ""),
            quality_score=float(d.get("quality_score", 0) or 0),
            pillar=d.get("pillar", ""),
            created_at=float(d.get("created_at", 0) or time.time()),
        )


def _resolve_path(path: str) -> str:
    """支持 COZE_WORKSPACE_PATH / cwd 兜底"""
    candidates = [
        os.path.join(os.getenv("COZE_WORKSPACE_PATH", ""), path) if os.getenv("COZE_WORKSPACE_PATH") else "",
        path,
        os.path.join(os.getcwd(), path),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    # 都不存在 → 默认 cwd 路径（让首次写入能成功）
    return os.path.join(os.getcwd(), path)


def load_memory(path: str = DEFAULT_PATH, max_items: int = DEFAULT_MAX_ITEMS) -> Deque[MemoryItem]:
    """加载最近 N 条记忆。文件不存在 → 返回空。"""
    real = _resolve_path(path)
    if not os.path.isfile(real):
        return deque(maxlen=max_items)
    try:
        with _LOCK:
            with open(real, "r", encoding="utf-8") as f:
                data = json.load(f)
        items_raw = data.get("items", []) if isinstance(data, dict) else []
        items = [MemoryItem.from_dict(x) for x in items_raw if isinstance(x, dict)]
        return deque(items, maxlen=max_items)
    except (OSError, ValueError) as e:
        logger.warning(f"editorial_memory 加载失败: {e}")
        return deque(maxlen=max_items)


def save_memory(items: Deque[MemoryItem], path: str = DEFAULT_PATH) -> bool:
    """原子写：先写 .tmp 再 rename。"""
    real = _resolve_path(path)
    Path(real).parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": time.time(),
        "total": len(items),
        "items": [it.to_dict() for it in items],
    }
    tmp = real + ".tmp"
    try:
        with _LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, real)
        return True
    except OSError as e:
        logger.warning(f"editorial_memory 写入失败: {e}")
        return False


def append_to_memory(item: MemoryItem, path: str = DEFAULT_PATH, max_items: int = DEFAULT_MAX_ITEMS) -> Deque[MemoryItem]:
    """追加一条，FIFO 截断，原子写回。"""
    items = load_memory(path, max_items)
    items.append(item)
    save_memory(items, path)
    return items


def render_memory_summary(items: Deque[MemoryItem], max_titles: int = 20) -> str:
    """渲染成可注入 prompt 的 markdown。"""
    if not items:
        return ""
    titles = [it.title for it in list(items)[-max_titles:] if it.title]
    if not titles:
        return ""
    lines = ["## 最近已做过的角度（避免重复，2026-07-26 新增）", ""]
    for t in titles:
        lines.append(f"- {t[:80]}")
    lines.append("")
    lines.append("如果当前素材角度与上面重合度 > 80%，请换一个角度。")
    return "\n".join(lines)