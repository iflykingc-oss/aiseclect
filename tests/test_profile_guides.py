"""
Profile-aware Newsroom Style Guide 测试（2026-07-26 新增）

覆盖：
- config/profile_guides.json 合法 + 5 个 profile + 选择规则完整
- _load_profile_guides 回退默认（缺失/解析失败）
- _select_profile_for_material 按 category / source / fallback 正确选择
- _render_profile_guides_block 包含关键字段
- 集成：_build_messages 把 profile block 注入到 sp

运行：pytest tests/test_profile_guides.py -v
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_GUIDES_PATH = REPO_ROOT / "config" / "profile_guides.json"

from collect_pipeline.models import ScoredMaterial
from graphs.nodes.tweet_generator_node import (
    DEFAULT_PROFILE_GUIDES,
    _load_profile_guides,
    _material_payload,
    _render_profile_guides_block,
    _select_profile_for_material,
)


@pytest.fixture(scope="module")
def profile_guides() -> dict:
    return json.loads(PROFILE_GUIDES_PATH.read_text(encoding="utf-8"))


# ============ 配置文件完整性 ============

def test_profile_guides_file_exists():
    assert PROFILE_GUIDES_PATH.is_file(), "config/profile_guides.json 必须存在"


def test_profile_guides_has_5_profiles(profile_guides):
    """5 个 profile 覆盖了所有主流内容方向"""
    profiles = profile_guides.get("profiles") or {}
    expected = {"ai_tools_consumer", "dev_framework", "industry_business", "risk_privacy", "open_source_release"}
    assert set(profiles.keys()) == expected, f"实际: {set(profiles.keys())}"


def test_each_profile_has_required_fields(profile_guides):
    """每个 profile 必须含 audience / editorial_tone / must_have_actionable_item / platform_priority / sample"""
    profiles = profile_guides.get("profiles") or {}
    for key, p in profiles.items():
        assert "audience" in p, f"{key} 缺 audience"
        assert "editorial_tone" in p and p["editorial_tone"], f"{key} 缺 editorial_tone"
        assert "must_have_actionable_item" in p, f"{key} 缺 must_have_actionable_item"
        assert "platform_priority" in p, f"{key} 缺 platform_priority"
        assert "sample_title" in p, f"{key} 缺 sample_title"
        assert "sample_tweet" in p, f"{key} 缺 sample_tweet"


def test_selection_rules_cover_categories_and_sources(profile_guides):
    rules = profile_guides.get("_selection_rules") or {}
    assert "by_category" in rules
    assert "by_source" in rules
    assert "fallback" in rules
    # fallback 必须是有效 profile key
    profiles = profile_guides.get("profiles") or {}
    assert rules["fallback"] in profiles


def test_selection_rule_targets_are_valid_profiles(profile_guides):
    """所有 category/source 规则的目标 key 必须在 profiles 字典里"""
    profiles = set((profile_guides.get("profiles") or {}).keys())
    rules = profile_guides.get("_selection_rules") or {}
    for k, v in (rules.get("by_category") or {}).items():
        assert v in profiles, f"category {k} → {v} 不存在"
    for k, v in (rules.get("by_source") or {}).items():
        assert v in profiles, f"source {k} → {v} 不存在"


# ============ _load_profile_guides 加载 + 回退 ============

def test_load_profile_guides_returns_real_config(tmp_path):
    """workspace 有 profile_guides.json 时能正常加载"""
    ws = str(tmp_path)
    (Path(ws) / "config").mkdir()
    (Path(ws) / "config" / "profile_guides.json").write_text(
        json.dumps({"profiles": {"ai_tools_consumer": {"label": "test"}}, "_selection_rules": {"fallback": "ai_tools_consumer"}}),
        encoding="utf-8",
    )
    loaded = _load_profile_guides(ws)
    assert loaded["profiles"]["ai_tools_consumer"]["label"] == "test"


def test_load_profile_guides_falls_back_when_missing(tmp_path, monkeypatch):
    """workspace 无 profile_guides.json 且 cwd 也无时回退到默认"""
    # 把 cwd 改到 tmp_path 隔离；并把环境变量也清掉
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    loaded = _load_profile_guides(str(tmp_path))
    assert loaded == DEFAULT_PROFILE_GUIDES


def test_load_profile_guides_falls_back_on_parse_error(tmp_path, monkeypatch):
    """profile_guides.json 解析失败时回退到默认"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    (Path(str(tmp_path)) / "config").mkdir()
    (Path(str(tmp_path)) / "config" / "profile_guides.json").write_text("not valid json", encoding="utf-8")
    loaded = _load_profile_guides(str(tmp_path))
    assert loaded == DEFAULT_PROFILE_GUIDES


# ============ _select_profile_for_material ============

def test_select_profile_by_category(profile_guides):
    m = ScoredMaterial(url="u", title="t", source="newsnow", category="AI 产品")
    assert _select_profile_for_material(m, profile_guides) == "ai_tools_consumer"


def test_select_profile_by_source_with_prefix(profile_guides):
    """newsnow-weibo / newsnow-weibo 等带前缀的 source 也能匹配"""
    m = ScoredMaterial(url="u", title="t", source="newsnow-weibo", category="")
    assert _select_profile_for_material(m, profile_guides) == "industry_business"


def test_select_profile_github_open_source(profile_guides):
    m = ScoredMaterial(url="u", title="t", source="github-watchlist", category="")
    assert _select_profile_for_material(m, profile_guides) == "open_source_release"


def test_select_profile_falls_back_when_unknown(profile_guides):
    m = ScoredMaterial(url="u", title="t", source="unknown-source", category="未知分类")
    assert _select_profile_for_material(m, profile_guides) == "ai_tools_consumer"


def test_select_profile_category_takes_precedence_over_source(profile_guides):
    """category 匹配优先于 source"""
    m = ScoredMaterial(url="u", title="t", source="github", category="安全隐私")
    # 安全隐私 → risk_privacy（不是 open_source_release）
    assert _select_profile_for_material(m, profile_guides) == "risk_privacy"


# ============ _render_profile_guides_block ============

def test_render_profile_guides_block_includes_all_profiles(profile_guides):
    block = _render_profile_guides_block(profile_guides)
    for key in profile_guides["profiles"].keys():
        assert f"Profile `{key}`" in block


def test_render_profile_guides_block_includes_samples(profile_guides):
    block = _render_profile_guides_block(profile_guides)
    for key, p in profile_guides["profiles"].items():
        assert p.get("sample_title", "")[:20] in block or p.get("sample_title") in block


def test_render_profile_guides_block_handles_empty_profiles():
    block = _render_profile_guides_block({"profiles": {}})
    assert block == ""


# ============ 集成：_material_payload 注入 _profile ============

def test_material_payload_includes_profile_key(profile_guides):
    """_material_payload 输出应含 _profile 字段"""
    m = ScoredMaterial(url="https://x.com/1", title="t", source="github", category="开源项目")
    payload = _material_payload(m, persona_assignments={}, profile_guides=profile_guides)
    assert "_profile" in payload
    assert payload["_profile"] == "open_source_release"


def test_material_payload_preserves_existing_fields(profile_guides):
    """_profile 是新字段，不应影响其他字段"""
    m = ScoredMaterial(url="https://x.com/1", title="t", source="newsnow", category="AI 产品")
    payload = _material_payload(m, persona_assignments={"https://x.com/1": {"x": "p1"}}, profile_guides=profile_guides)
    assert payload["url"] == "https://x.com/1"
    assert payload["title"] == "t"
    assert payload["category"] == "AI 产品"
    assert payload["_persona"]["x"] == "p1"
    assert payload["_profile"] == "ai_tools_consumer"