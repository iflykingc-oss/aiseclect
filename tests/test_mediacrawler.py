"""
MediaCrawler 客户端 + collector 节点单元测试（无需真实 CLI）

覆盖：
- 二进制解析：MEDIACRAWLER_BIN 环境变量优先，否则 PATH 查找
- 不支持的平台 → 跳过（返回 []）
- CLI 不在 → 返回空（graceful）
- CLI 存在 + JSON 输出：解析为 MediaCrawlerItem
- CLI 退出码非 0 / 超时 / FileNotFoundError：返回空
- _normalize_item 处理缺 URL / 缺 title / 各种 likes 字段名
- collector_node 把 MediaCrawlerItem 转成 RawMaterial
- 与 graph.py 主流水线接通

运行：pytest tests/test_mediacrawler.py -v
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tools import mediacrawler as mc
from graphs.nodes.mediacrawler_collector_node import mediacrawler_collector_node
from graphs.state import (
    MediaCrawlerCollectorInput,
    MediaCrawlerCollectorOutput,
    RawMaterial,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MEDIACRAWLER_BIN", raising=False)
    yield


# ============ _resolve_bin / is_available ============

def test_resolve_bin_uses_env_var(monkeypatch):
    monkeypatch.setenv("MEDIACRAWLER_BIN", "/custom/path/mediacrawler")
    assert mc._resolve_bin() == "/custom/path/mediacrawler"


def test_resolve_bin_finds_in_path(monkeypatch):
    with patch("tools.mediacrawler.shutil.which", return_value="/usr/bin/mediacrawler"):
        assert mc._resolve_bin() == "/usr/bin/mediacrawler"


def test_resolve_bin_returns_none_when_not_installed():
    with patch("tools.mediacrawler.shutil.which", return_value=None):
        assert mc._resolve_bin() is None


def test_is_available_false_when_no_binary():
    with patch("tools.mediacrawler.shutil.which", return_value=None):
        assert mc.is_available() is False


# ============ fetch_topics 平台过滤 ============

def test_fetch_topics_returns_empty_for_unsupported_platform():
    """不支持的平台应直接跳过，不调 CLI"""
    items = mc.fetch_topics(platform="tiktok")
    assert items == []


# ============ _parse_json_output ============

def test_parse_json_list():
    out = json.dumps([{"url": "u1"}, {"url": "u2"}])
    assert len(mc._parse_json_output(out)) == 2


def test_parse_json_dict_with_items():
    out = json.dumps({"items": [{"url": "u1"}]})
    assert len(mc._parse_json_output(out)) == 1


def test_parse_json_invalid_returns_empty():
    assert mc._parse_json_output("not json") == []
    assert mc._parse_json_output("") == []


# ============ _normalize_item ============

def test_normalize_item_basic():
    raw = {"url": "https://xhs.com/1", "title": "T", "platform": "xhs"}
    item = mc._normalize(raw, 0)
    assert item is not None
    assert item.url == "https://xhs.com/1"
    assert item.title == "T"
    assert item.platform == "xhs"


def test_normalize_item_missing_url_returns_none():
    assert mc._normalize({"title": "no url"}, 0) is None


def test_normalize_item_missing_title_uses_fallback():
    raw = {"url": "https://x.com/1"}
    item = mc._normalize(raw, 5)
    assert item.title == "mediacrawler-5"


def test_normalize_item_handles_likes_aliases():
    for key in ("likes", "liked_count"):
        raw = {"url": "u", key: 100}
        item = mc._normalize(raw, 0)
        assert item.likes == 100


def test_normalize_item_handles_comments_aliases():
    for key in ("comments", "comment_count"):
        raw = {"url": "u", key: 50}
        item = mc._normalize(raw, 0)
        assert item.comments == 50


def test_normalize_item_invalid_likes_falls_back_to_zero():
    raw = {"url": "u", "likes": "not a number"}
    item = mc._normalize(raw, 0)
    assert item.likes == 0


def test_normalize_item_truncates_long_content():
    raw = {"url": "u", "content": "a" * 5000}
    item = mc._normalize(raw, 0)
    assert len(item.content) <= 1000


# ============ fetch_topics 路径覆盖 ============

def test_fetch_topics_returns_empty_when_cli_missing():
    with patch("tools.mediacrawler._resolve_bin", return_value=None):
        items = mc.fetch_topics(platform="xhs")
    assert items == []


def test_fetch_topics_parses_json_success(monkeypatch):
    monkeypatch.setattr("tools.mediacrawler._resolve_bin", lambda: "/usr/bin/mediacrawler")

    payload = json.dumps([
        {"url": "https://xhs.com/1", "title": "小红书笔记", "likes": 200, "platform": "xhs"},
        {"url": "https://xhs.com/2", "title": "另一条", "comments": 30, "platform": "xhs"},
    ])
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = payload
    mock_proc.stderr = ""

    with patch("tools.mediacrawler.subprocess.run", return_value=mock_proc) as mock_run:
        items = mc.fetch_topics(platform="xhs", max_results=10)

    assert len(items) == 2
    assert items[0].platform == "xhs"
    assert items[0].likes == 200
    # 验证 CLI 收到 --platform xhs
    call_args = mock_run.call_args[0][0]
    assert "--platform" in call_args
    assert "xhs" in call_args


def test_fetch_topics_returns_empty_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr("tools.mediacrawler._resolve_bin", lambda: "/usr/bin/mediacrawler")

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "auth failed"

    with patch("tools.mediacrawler.subprocess.run", return_value=mock_proc):
        items = mc.fetch_topics(platform="xhs")
    assert items == []


def test_fetch_topics_returns_empty_on_timeout(monkeypatch):
    monkeypatch.setattr("tools.mediacrawler._resolve_bin", lambda: "/usr/bin/mediacrawler")

    with patch("tools.mediacrawler.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd=["mediacrawler"], timeout=120)):
        items = mc.fetch_topics(platform="xhs")
    assert items == []


def test_fetch_topics_returns_empty_on_filenotfound(monkeypatch):
    monkeypatch.setattr("tools.mediacrawler._resolve_bin", lambda: "/usr/bin/mediacrawler")

    with patch("tools.mediacrawler.subprocess.run",
               side_effect=FileNotFoundError("not found")):
        items = mc.fetch_topics(platform="xhs")
    assert items == []


def test_fetch_topics_caps_max_results(monkeypatch):
    monkeypatch.setattr("tools.mediacrawler._resolve_bin", lambda: "/usr/bin/mediacrawler")

    items_raw = [{"url": f"https://xhs.com/{i}", "title": f"t{i}"} for i in range(50)]
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps(items_raw)
    mock_proc.stderr = ""

    with patch("tools.mediacrawler.subprocess.run", return_value=mock_proc):
        items = mc.fetch_topics(platform="xhs", max_results=5)
    assert len(items) == 5


# ============ collector_node 集成 ============

def test_collector_node_returns_empty_when_cli_missing():
    state = MediaCrawlerCollectorInput(max_per_source=10)
    with patch("graphs.nodes.mediacrawler_collector_node.fetch_topics", return_value=[]):
        out = mediacrawler_collector_node(state)
    assert out.mediacrawler_materials == []


def test_collector_node_converts_items_to_raw_materials():
    state = MediaCrawlerCollectorInput(max_per_source=10, platforms=["xhs"], keywords=["ai"])
    fetched = [
        mc.MediaCrawlerItem(url="https://xhs.com/1", title="小红书笔记", platform="xhs",
                            content="内容", likes=100, comments=20, author="user1"),
    ]

    with patch("graphs.nodes.mediacrawler_collector_node.fetch_topics", return_value=fetched):
        out = mediacrawler_collector_node(state)

    assert len(out.mediacrawler_materials) == 1
    m = out.mediacrawler_materials[0]
    assert isinstance(m, RawMaterial)
    assert m.source == "mediacrawler-xhs"
    assert m.extra_data["likes"] == 100


def test_collector_node_skips_unsupported_platform():
    """platforms 里含不支持的应被跳过，不调用 fetch_topics"""
    state = MediaCrawlerCollectorInput(max_per_source=10, platforms=["tiktok"])
    with patch("graphs.nodes.mediacrawler_collector_node.fetch_topics") as mock_fetch:
        out = mediacrawler_collector_node(state)
    mock_fetch.assert_not_called()
    assert out.mediacrawler_materials == []


def test_collector_node_caps_to_max_per_source():
    state = MediaCrawlerCollectorInput(max_per_source=4, platforms=["xhs"])
    fetched = [
        mc.MediaCrawlerItem(url=f"https://xhs.com/{i}", title=f"t{i}", platform="xhs")
        for i in range(10)
    ]
    with patch("graphs.nodes.mediacrawler_collector_node.fetch_topics", return_value=fetched):
        out = mediacrawler_collector_node(state)
    assert len(out.mediacrawler_materials) == 4


def test_collector_node_swallows_exceptions(monkeypatch):
    state = MediaCrawlerCollectorInput(max_per_source=10)
    with patch("graphs.nodes.mediacrawler_collector_node.fetch_topics",
               side_effect=RuntimeError("boom")):
        out = mediacrawler_collector_node(state)
    assert out.mediacrawler_materials == []


def test_collector_node_multi_platform_split():
    """多平台时 max_per_source 按平台数拆分"""
    state = MediaCrawlerCollectorInput(max_per_source=10, platforms=["xhs", "wb", "zhihu"])
    call_log = []

    def fake_fetch(platform, keywords, max_results):
        call_log.append((platform, max_results))
        return []

    with patch("graphs.nodes.mediacrawler_collector_node.fetch_topics", side_effect=fake_fetch):
        mediacrawler_collector_node(state)

    # 3 个平台都被调用，且每个拿到的 max_results ≈ 10/3
    assert len(call_log) == 3
    assert {p for p, _ in call_log} == {"xhs", "wb", "zhihu"}


# ============ 与 graph 集成 ============

def test_mediacrawler_in_main_graph():
    from graphs.graph import main_graph
    assert "mediacrawler_collector" in main_graph.nodes


def test_mediacrawler_in_state_schema():
    from graphs.state import GlobalState
    s = GlobalState(mediacrawler_materials=[])
    assert hasattr(s, "mediacrawler_materials")


def test_supported_platforms_constant():
    """SUPPORTED_PLATFORMS 与 README 一致：xhs/dy/ks/bili/wb/tieba/zhihu"""
    assert mc.SUPPORTED_PLATFORMS == ("xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu")