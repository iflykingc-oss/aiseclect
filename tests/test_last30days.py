"""
last30days 客户端 + collector 节点单元测试（无需真实 CLI）

覆盖：
- 二进制解析：LAST30DAYS_BIN 环境变量优先，否则 PATH 查找
- CLI 不在 → 返回空（graceful）
- CLI 存在 + JSON 输出：解析为 Last30daysItem
- CLI 存在 + 非 JSON 输出：返回空（不抛异常）
- CLI 退出码非 0：返回空
- CLI 超时：返回空
- 子进程异常（FileNotFoundError）：返回空
- _normalize_item 处理各种 engagement 字段名（engagement/score/points/upvotes）
- collector_node 把 Last30daysItem 转成 RawMaterial

运行：pytest tests/test_last30days.py -v
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tools import last30days as l30
from graphs.nodes.last30days_collector_node import last30days_collector_node
from graphs.state import Last30daysCollectorInput, RawMaterial


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("LAST30DAYS_BIN", raising=False)
    yield


# ============ _resolve_bin / is_available ============

def test_resolve_bin_uses_env_var(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_BIN", "/custom/path/last30days")
    assert l30._resolve_bin() == "/custom/path/last30days"


def test_resolve_bin_finds_in_path(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    with patch("tools.last30days.shutil.which", return_value="/usr/bin/last30days"):
        assert l30._resolve_bin() == "/usr/bin/last30days"


def test_resolve_bin_returns_none_when_not_installed():
    with patch("tools.last30days.shutil.which", return_value=None):
        assert l30._resolve_bin() is None


def test_is_available_false_when_no_binary():
    with patch("tools.last30days.shutil.which", return_value=None):
        assert l30.is_available() is False


# ============ _parse_json_output ============

def test_parse_json_list():
    out = json.dumps([{"url": "u1"}, {"url": "u2"}])
    assert len(l30._parse_json_output(out)) == 2


def test_parse_json_dict_with_items():
    out = json.dumps({"items": [{"url": "u1"}]})
    assert len(l30._parse_json_output(out)) == 1


def test_parse_json_dict_with_results_key():
    out = json.dumps({"results": [{"url": "u1"}, {"url": "u2"}]})
    assert len(l30._parse_json_output(out)) == 2


def test_parse_json_invalid_returns_empty():
    assert l30._parse_json_output("not json at all") == []
    assert l30._parse_json_output("") == []


def test_parse_json_skips_non_dict_items():
    out = json.dumps([{"url": "u1"}, "not a dict", 42])
    result = l30._parse_json_output(out)
    assert len(result) == 1
    assert result[0]["url"] == "u1"


# ============ _normalize_item ============

def test_normalize_item_basic():
    raw = {"url": "https://x.com/post/1", "title": "Test", "engagement": 100.0}
    item = l30._normalize_item(raw, 0)
    assert item is not None
    assert item.url == "https://x.com/post/1"
    assert item.title == "Test"
    assert item.engagement == 100.0


def test_normalize_item_handles_engagement_aliases():
    """engagement 字段在不同 CLI 版本里命名不同"""
    for key in ("engagement", "score", "points", "likes", "upvotes"):
        raw = {"url": "https://x.com/1", "title": "t", key: 42}
        item = l30._normalize_item(raw, 0)
        assert item is not None
        assert item.engagement == 42.0, f"failed for key {key}"


def test_normalize_item_missing_url_returns_none():
    raw = {"title": "no url"}
    assert l30._normalize_item(raw, 0) is None


def test_normalize_item_missing_title_uses_fallback():
    raw = {"url": "https://x.com/1"}
    item = l30._normalize_item(raw, 7)
    assert item.title == "last30days-7"


def test_normalize_item_normalizes_platform():
    raw = {"url": "https://x.com/1", "platform": "Reddit"}
    item = l30._normalize_item(raw, 0)
    assert item.platform == "reddit"


def test_normalize_item_invalid_engagement_falls_back_to_zero():
    raw = {"url": "https://x.com/1", "engagement": "not a number"}
    item = l30._normalize_item(raw, 0)
    assert item.engagement == 0.0


# ============ fetch_topics 路径覆盖 ============

def test_fetch_topics_returns_empty_when_cli_missing():
    with patch("tools.last30days._resolve_bin", return_value=None):
        items = l30.fetch_topics(queries=["ai"])
    assert items == []


def test_fetch_topics_parses_json_success(monkeypatch):
    monkeypatch.setattr("tools.last30days._resolve_bin", lambda: "/usr/bin/last30days")

    payload = json.dumps([
        {"url": "https://x.com/1", "title": "AI 热点", "engagement": 500, "platform": "x"},
        {"url": "https://reddit.com/r/1", "title": "Reddit 话题", "engagement": 300, "platform": "reddit"},
    ])
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = payload
    mock_proc.stderr = ""

    with patch("tools.last30days.subprocess.run", return_value=mock_proc) as mock_run:
        items = l30.fetch_topics(queries=["ai"], max_results=10)

    assert len(items) == 2
    assert items[0].url == "https://x.com/1"
    assert items[0].engagement == 500
    # 验证 CLI 收到 --json 参数
    call_args = mock_run.call_args[0][0]
    assert "--json" in call_args
    assert "--limit" in call_args


def test_fetch_topics_returns_empty_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr("tools.last30days._resolve_bin", lambda: "/usr/bin/last30days")

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "auth failed"

    with patch("tools.last30days.subprocess.run", return_value=mock_proc):
        items = l30.fetch_topics()
    assert items == []


def test_fetch_topics_returns_empty_on_timeout(monkeypatch):
    monkeypatch.setattr("tools.last30days._resolve_bin", lambda: "/usr/bin/last30days")

    with patch("tools.last30days.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd=["last30days"], timeout=60)):
        items = l30.fetch_topics()
    assert items == []


def test_fetch_topics_returns_empty_on_filenotfound(monkeypatch):
    monkeypatch.setattr("tools.last30days._resolve_bin", lambda: "/usr/bin/last30days")

    with patch("tools.last30days.subprocess.run",
               side_effect=FileNotFoundError("not found")):
        items = l30.fetch_topics()
    assert items == []


def test_fetch_topics_returns_empty_on_invalid_json(monkeypatch):
    monkeypatch.setattr("tools.last30days._resolve_bin", lambda: "/usr/bin/last30days")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "this is not JSON"
    mock_proc.stderr = ""

    with patch("tools.last30days.subprocess.run", return_value=mock_proc):
        items = l30.fetch_topics()
    assert items == []


def test_fetch_topics_caps_max_results(monkeypatch):
    monkeypatch.setattr("tools.last30days._resolve_bin", lambda: "/usr/bin/last30days")

    items_raw = [{"url": f"https://x.com/{i}", "title": f"t{i}"} for i in range(50)]
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps(items_raw)
    mock_proc.stderr = ""

    with patch("tools.last30days.subprocess.run", return_value=mock_proc):
        items = l30.fetch_topics(max_results=5)
    assert len(items) == 5


# ============ collector_node 集成 ============

def test_collector_node_returns_empty_when_cli_missing():
    state = Last30daysCollectorInput(max_per_source=5)
    with patch("graphs.nodes.last30days_collector_node.fetch_topics", return_value=[]):
        out = last30days_collector_node(state)
    assert out.last30days_materials == []


def test_collector_node_converts_items_to_raw_materials():
    state = Last30daysCollectorInput(max_per_source=5, queries=["ai"])
    fetched = [
        l30.Last30daysItem(url="https://x.com/1", title="AI 热点", platform="x",
                           engagement=100, snippet="snippet 1"),
        l30.Last30daysItem(url="https://reddit.com/2", title="Reddit 话题", platform="reddit",
                           engagement=50, snippet=""),
    ]

    with patch("graphs.nodes.last30days_collector_node.fetch_topics", return_value=fetched):
        out = last30days_collector_node(state)

    assert len(out.last30days_materials) == 2
    m0 = out.last30days_materials[0]
    assert isinstance(m0, RawMaterial)
    assert m0.url == "https://x.com/1"
    assert m0.title == "AI 热点"
    assert m0.source == "last30days-x"
    assert m0.extra_data["engagement"] == 100


def test_collector_node_handles_platformless_items():
    """platform 为空时 source 应回退到 'last30days'"""
    state = Last30daysCollectorInput(max_per_source=5)
    fetched = [
        l30.Last30daysItem(url="https://x.com/1", title="t", platform=""),
    ]
    with patch("graphs.nodes.last30days_collector_node.fetch_topics", return_value=fetched):
        out = last30days_collector_node(state)
    assert out.last30days_materials[0].source == "last30days"


def test_collector_node_caps_to_max_per_source():
    state = Last30daysCollectorInput(max_per_source=2)
    fetched = [
        l30.Last30daysItem(url=f"https://x.com/{i}", title=f"t{i}", platform="x")
        for i in range(5)
    ]
    with patch("graphs.nodes.last30days_collector_node.fetch_topics", return_value=fetched):
        out = last30days_collector_node(state)
    assert len(out.last30days_materials) == 2


def test_collector_node_swallows_exceptions(monkeypatch):
    """CLI 调用抛异常时，节点不应崩溃，应返回空"""
    state = Last30daysCollectorInput(max_per_source=5)
    with patch("graphs.nodes.last30days_collector_node.fetch_topics", side_effect=RuntimeError("boom")):
        out = last30days_collector_node(state)
    assert out.last30days_materials == []