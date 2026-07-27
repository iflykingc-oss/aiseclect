"""
editorial_memory 单元测试（2026-07-26 新增）

覆盖：
- MemoryItem to_dict / from_dict 序列化
- load_memory: 文件不存在 → 空 deque
- load_memory: 解析失败 → 空 deque（不回滚）
- save_memory: 原子写（.tmp + rename）
- append_to_memory: FIFO 截断到 maxlen
- render_memory_summary: 注入 prompt 格式

运行：pytest tests/test_editorial_memory.py -v
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from collect_pipeline.editorial_memory import (
    DEFAULT_MAX_ITEMS,
    MemoryItem,
    append_to_memory,
    load_memory,
    render_memory_summary,
    save_memory,
)
from collections import deque


@pytest.fixture
def tmp_memory_path(tmp_path: Path) -> str:
    """每个测试用独立的 memory 文件，不污染实际 output/editorial_memory.json"""
    return str(tmp_path / "memory.json")


def _make_item(idx: int, title: str = "") -> MemoryItem:
    return MemoryItem(
        unique_id=f"t{idx}",
        title=title or f"Test Article {idx}",
        source="aihot",
        category="AI 产品",
        platform="X+小红书",
        quality_score=80.0 + idx,
        pillar="教程型",
        created_at=time.time() + idx,
    )


# ============ MemoryItem 序列化 ============

def test_memory_item_to_from_dict():
    item = MemoryItem(
        unique_id="t1", title="Test", source="s", category="c",
        platform="X+小红书", quality_score=85.0, pillar="教程型",
    )
    d = item.to_dict()
    assert d["unique_id"] == "t1"
    assert d["quality_score"] == 85.0
    restored = MemoryItem.from_dict(d)
    assert restored.unique_id == "t1"
    assert restored.quality_score == 85.0


def test_memory_item_from_dict_handles_missing_fields():
    restored = MemoryItem.from_dict({})
    assert restored.unique_id == ""
    assert restored.quality_score == 0.0


# ============ load_memory ============

def test_load_memory_returns_empty_when_no_file(tmp_path):
    path = str(tmp_path / "no_such.json")
    items = load_memory(path)
    assert isinstance(items, deque)
    assert len(items) == 0


def test_load_memory_returns_empty_on_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not valid json", encoding="utf-8")
    items = load_memory(str(path))
    assert len(items) == 0


def test_load_memory_reads_existing(tmp_memory_path):
    save_memory(deque([_make_item(1), _make_item(2)], maxlen=DEFAULT_MAX_ITEMS), tmp_memory_path)
    items = load_memory(tmp_memory_path)
    assert len(items) == 2
    assert items[0].unique_id == "t1"


# ============ save_memory 原子写 ============

def test_save_memory_creates_file(tmp_memory_path):
    ok = save_memory(deque([_make_item(1)], maxlen=10), tmp_memory_path)
    assert ok is True
    assert Path(tmp_memory_path).is_file()


def test_save_memory_writes_valid_json(tmp_memory_path):
    save_memory(deque([_make_item(1, "中文测试")], maxlen=10), tmp_memory_path)
    data = json.loads(Path(tmp_memory_path).read_text(encoding="utf-8"))
    assert data["total"] == 1
    assert data["items"][0]["title"] == "中文测试"
    assert "updated_at" in data


def test_save_memory_uses_atomic_rename(tmp_memory_path):
    """写入应使用 .tmp 然后 rename，没有遗留 .tmp 文件"""
    save_memory(deque([_make_item(1)], maxlen=10), tmp_memory_path)
    assert not Path(tmp_memory_path + ".tmp").exists()


# ============ append_to_memory + FIFO 截断 ============

def test_append_to_memory_increases_count(tmp_memory_path):
    append_to_memory(_make_item(1), tmp_memory_path, max_items=10)
    append_to_memory(_make_item(2), tmp_memory_path, max_items=10)
    items = load_memory(tmp_memory_path, max_items=10)
    assert len(items) == 2
    assert items[0].unique_id == "t1"
    assert items[1].unique_id == "t2"


def test_append_to_memory_respects_maxlen_fifo(tmp_memory_path):
    """超过 maxlen → FIFO 截断，保留最新 N 条"""
    max_items = 3
    for i in range(5):
        append_to_memory(_make_item(i, title=f"Article {i}"), tmp_memory_path, max_items=max_items)
    items = load_memory(tmp_memory_path, max_items=max_items)
    assert len(items) == 3
    # 应保留最新 3 条（Article 2, 3, 4）
    titles = [it.title for it in items]
    assert titles == ["Article 2", "Article 3", "Article 4"]


# ============ render_memory_summary ============

def test_render_memory_summary_empty():
    block = render_memory_summary(deque(maxlen=10))
    assert block == ""


def test_render_memory_summary_includes_recent_titles():
    items = deque([_make_item(1, "标题1"), _make_item(2, "标题2"), _make_item(3, "标题3")], maxlen=10)
    block = render_memory_summary(items)
    assert "最近已做过的角度" in block
    assert "标题1" in block
    assert "标题2" in block
    assert "标题3" in block
    # 应有「避免重复」提示
    assert "避免重复" in block


def test_render_memory_summary_respects_max_titles():
    items = deque([_make_item(i, f"T{i}") for i in range(50)], maxlen=100)
    block = render_memory_summary(items, max_titles=5)
    # 只列最近 5 条
    lines = [line for line in block.split("\n") if line.startswith("- ")]
    assert len(lines) == 5
    assert "T49" in lines[-1]  # 最新一条


def test_render_memory_summary_truncates_long_titles():
    items = deque([_make_item(1, "x" * 200)], maxlen=10)
    block = render_memory_summary(items)
    # 截断到 80 字符
    assert "x" * 80 in block
    assert "x" * 81 not in block