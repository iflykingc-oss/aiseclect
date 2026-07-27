"""
platform_router_node 单元测试（2026-07-26 新增）

覆盖：
- config/platform_router_llm_cfg.json 合法 + 关键 section 存在
- _load_router_cfg 回退（缺失 / 解析失败）
- _parse_decisions 解析 LLM 返回 + 容错
- _fallback_decision 按 tech_depth 兜底
- platform_router_node 集成（无 LLM 时走 fallback）
- tweet_generator._apply_router_decision 覆盖 LLM 输出

运行：pytest tests/test_platform_router.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTER_CFG = REPO_ROOT / "config" / "platform_router_llm_cfg.json"

from collect_pipeline.models import ScoredMaterial
from graphs.nodes.platform_router_node import (
    PlatformDecision,
    _fallback_decision,
    _load_router_cfg,
    _parse_decisions,
    platform_router_node,
)
from graphs.nodes.tweet_generator_node import _apply_router_decision
from graphs.state import PlatformRouterInput


# ============ 配置文件完整性 ============

def test_router_cfg_exists():
    assert ROUTER_CFG.is_file(), "config/platform_router_llm_cfg.json 必须存在"


def test_router_cfg_is_valid_json():
    data = json.loads(ROUTER_CFG.read_text(encoding="utf-8"))
    assert "sp" in data
    assert "up" in data


def test_router_cfg_sp_has_decision_rules():
    sp = json.loads(ROUTER_CFG.read_text(encoding="utf-8"))["sp"]
    # 应包含「仅 X」/「X+小红书」决策规则
    assert "仅X" in sp
    assert "X+小红书" in sp
    # 6 维评分维度
    for dim in ["普通用户可读性", "行动价值", "收藏价值", "时效紧迫", "风险价格信号", "执行难度"]:
        assert dim in sp, f"缺少评分维度: {dim}"


# ============ _load_router_cfg 加载 + 回退 ============

def test_load_router_cfg_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    cfg = _load_router_cfg(str(tmp_path))
    assert cfg is None


def test_load_router_cfg_falls_back_on_parse_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    (Path(str(tmp_path)) / "config").mkdir()
    (Path(str(tmp_path)) / "config" / "platform_router_llm_cfg.json").write_text("not valid json", encoding="utf-8")
    assert _load_router_cfg(str(tmp_path)) is None


# ============ _parse_decisions ============

def test_parse_decisions_basic():
    materials = [ScoredMaterial(url=f"https://x.com/{i}", title=f"t{i}") for i in range(3)]
    raw = [
        {"url": "https://x.com/0", "platform": "仅X", "platform_reason": "无大众价值", "score": 30, "recommended_pillar": "资讯评论型"},
        {"url": "https://x.com/1", "platform": "X+小红书", "platform_reason": "AI 工具发布", "score": 80, "recommended_pillar": "教程型"},
        {"url": "https://x.com/2", "platform": "invalid", "score": 50},  # 未知 platform → fallback
    ]
    out = _parse_decisions(raw, materials)
    assert "https://x.com/0" in out
    assert out["https://x.com/0"].platform == "仅X"
    assert out["https://x.com/0"].score == 30
    assert "https://x.com/1" in out
    assert out["https://x.com/1"].platform == "X+小红书"
    # 无效 platform → 按 score 推断（50 < 60 → 仅X）
    assert out["https://x.com/2"].platform == "仅X"


def test_parse_decisions_drops_unknown_urls():
    """URL 不在 materials 列表里 → 丢弃（防 LLM 编造 URL）"""
    materials = [ScoredMaterial(url="https://x.com/0")]
    raw = [
        {"url": "https://x.com/0", "platform": "X+小红书", "score": 80},
        {"url": "https://evil.com/fake", "platform": "X+小红书", "score": 80},
    ]
    out = _parse_decisions(raw, materials)
    assert "https://x.com/0" in out
    assert "https://evil.com/fake" not in out


def test_parse_decisions_handles_non_dict_items():
    materials = [ScoredMaterial(url="https://x.com/0")]
    raw = [
        "not a dict",
        {"url": "https://x.com/0", "platform": "X+小红书", "score": 80},
        42,
    ]
    out = _parse_decisions(raw, materials)
    assert len(out) == 1


def test_parse_decisions_empty_input():
    materials = [ScoredMaterial(url="https://x.com/0")]
    out = _parse_decisions([], materials)
    assert out == {}


# ============ _fallback_decision ============

def test_fallback_xhs_friendly():
    """tech_depth <= 60 → X+小红书"""
    m = ScoredMaterial(url="u", title="t", tech_depth=50.0)
    d = _fallback_decision(m)
    assert d.platform == "X+小红书"
    assert "fallback" in d.platform_reason


def test_fallback_tech_heavy():
    """tech_depth > 60 → 仅X"""
    m = ScoredMaterial(url="u", title="t", tech_depth=80.0)
    d = _fallback_decision(m)
    assert d.platform == "仅X"


def test_fallback_uses_default_tech_depth():
    """tech_depth 缺省（70.0）→ 仅X"""
    m = ScoredMaterial(url="u", title="t")
    d = _fallback_decision(m)
    assert d.platform == "仅X"


# ============ platform_router_node 集成 ============

def test_router_node_falls_back_when_cfg_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    state = PlatformRouterInput(
        cleaned_materials=[
            ScoredMaterial(url="https://x.com/1", title="t", tech_depth=50.0),
            ScoredMaterial(url="https://x.com/2", title="t", tech_depth=80.0),
        ]
    )
    out = platform_router_node(state)
    assert len(out.platform_decisions) == 2
    assert out.platform_decisions["https://x.com/1"].platform == "X+小红书"
    assert out.platform_decisions["https://x.com/2"].platform == "仅X"


def test_router_node_handles_empty_input():
    out = platform_router_node(PlatformRouterInput(cleaned_materials=[]))
    assert out.platform_decisions == {}


# ============ _apply_router_decision (tweet_generator 集成) ============

def test_apply_router_decision_overrides_llm_output():
    """router 决策应覆盖 LLM 输出的 platform 字段"""
    data = {"platform": "X+小红书", "tweet_content": "test"}
    router = {"https://x.com/1": {"platform": "仅X", "platform_reason": "无大众价值", "score": 30}}
    out = _apply_router_decision(data, "https://x.com/1", router)
    assert out["platform"] == "仅X"
    # 理由应包含 router 部分
    assert "router" in out["platform_reason"]


def test_apply_router_decision_no_op_when_routes_match():
    """router 决策与 LLM 一致 → 不动"""
    data = {"platform": "X+小红书", "platform_reason": "原 LLM 理由"}
    router = {"https://x.com/1": {"platform": "X+小红书", "platform_reason": "一致"}}
    out = _apply_router_decision(data, "https://x.com/1", router)
    assert out["platform"] == "X+小红书"
    # 理由不应被追加
    assert "router" not in out.get("platform_reason", "")


def test_apply_router_decision_no_op_when_no_router():
    """无 router 决策 → 不动"""
    data = {"platform": "X+小红书"}
    out = _apply_router_decision(data, "https://x.com/1", None)
    assert out == data


def test_apply_router_decision_no_op_when_url_not_in_router():
    data = {"platform": "X+小红书"}
    router = {"https://other.com": {"platform": "仅X"}}
    out = _apply_router_decision(data, "https://x.com/1", router)
    assert out == data


def test_apply_router_decision_ignores_invalid_router_platform():
    """router 返回无效 platform 值 → 不覆盖"""
    data = {"platform": "X+小红书"}
    router = {"https://x.com/1": {"platform": "B站"}}
    out = _apply_router_decision(data, "https://x.com/1", router)
    assert out["platform"] == "X+小红书"


# ============ 集成：graph.py 接入 ============

def test_platform_router_in_main_graph():
    from graphs.graph import main_graph
    assert "platform_router" in main_graph.nodes