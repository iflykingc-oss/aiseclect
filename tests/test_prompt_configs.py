"""
Prompt 配置完整性测试（2026-07-26 新增）

覆盖：
- config/tweet_generator_llm_cfg.json 是合法 JSON + 关键 section 存在
- config/xiaohongshu_style_prompt.txt 含「视角锚定」段
- 强 ban 词表已写入 system prompt
- content_strategy.json 平台规则更新（不再把硬技术当仅X 触发）

运行：pytest tests/test_prompt_configs.py -v
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TWEET_CFG = REPO_ROOT / "config" / "tweet_generator_llm_cfg.json"
XHS_STYLE = REPO_ROOT / "config" / "xiaohongshu_style_prompt.txt"
STRATEGY_CFG = REPO_ROOT / "config" / "content_strategy.json"


@pytest.fixture(scope="module")
def tweet_cfg() -> dict:
    return json.loads(TWEET_CFG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def xhs_style() -> str:
    return XHS_STYLE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def strategy_cfg() -> dict:
    return json.loads(STRATEGY_CFG.read_text(encoding="utf-8"))


# ============ tweet_generator 配置完整性 ============

def test_tweet_cfg_is_valid_json(tweet_cfg):
    """JSON 可解析 + 顶层结构完整"""
    assert "sp" in tweet_cfg
    assert "up" in tweet_cfg
    assert "few_shot_examples" in tweet_cfg


def test_tweet_cfg_has_platform_decision(tweet_cfg):
    """系统 prompt 应包含「平台决策」段"""
    assert "平台决策" in tweet_cfg["sp"]


def test_tweet_cfg_has_extended_ban_list(tweet_cfg):
    """强 ban 词表已写入 system prompt（2026-07-26 新增）"""
    sp = tweet_cfg["sp"]
    assert "强 ban 词表" in sp
    # 关键 ban 词必须存在
    for ban_phrase in [
        "本质上",
        "赋能",
        "重塑",
        "值得关注的是",
        "未来有望",
        "AI 套话",
        "翻译腔",
        "元评论",
    ]:
        assert ban_phrase in sp, f"missing ban phrase: {ban_phrase}"


def test_tweet_cfg_platform_decision_uses_consumer_audience(tweet_cfg):
    """平台决策应明确「两个平台都是写给普通用户」"""
    sp = tweet_cfg["sp"]
    assert "普通用户" in sp
    # 不应再把"API / SDK / 论文"自动归为仅X
    # 老文案的「API / SDK / endpoint 迁移、退役、参数变化」应已被替换
    assert "API / SDK / endpoint 迁移、退役、参数变化" not in sp


# ============ xiaohongshu_style_prompt 完整性 ============

def test_xhs_style_has_perspective_anchor(xhs_style):
    """小红书 prompt 应含「视角锚定」段（2026-07-26 新增）"""
    assert "视角锚定" in xhs_style


def test_xhs_style_anchor_includes_who_scene_emotion(xhs_style):
    """视角锚定段必须包含「谁会搜」/「场景」/「情绪」三要素"""
    # 找到视角锚定段
    m = re.search(r"## 视角锚定[\s\S]+?(?=\n## )", xhs_style)
    assert m is not None
    anchor_block = m.group(0)
    assert "谁会搜" in anchor_block
    assert "场景" in anchor_block
    assert "情绪" in anchor_block


def test_xhs_style_requires_first_person(xhs_style):
    """视角锚定应允许第一人称微描写"""
    assert "第一人称" in xhs_style or "我" in xhs_style
    assert "微描写" in xhs_style or "具体场景" in xhs_style


def test_xhs_style_has_mom_test(xhs_style):
    """应含「我妈测试」启发式"""
    assert "我妈" in xhs_style or "妈妈" in xhs_style


# ============ content_strategy 完整性 ============

def test_strategy_has_only_x_default(strategy_cfg):
    rules = strategy_cfg.get("platform_rules") or {}
    only_x = rules.get("only_x_default") or []
    # 不应再把"纯 API/纯 SDK/纯 arxiv"当仅X 触发
    for forbidden in ["纯 API", "纯 SDK", "纯 endpoint 变更", "arxiv / 纯论文", "CUDA / kernel"]:
        assert forbidden not in only_x, f"老口径仍存在: {forbidden}"


def test_strategy_xhs_requires_includes_consumer_view(strategy_cfg):
    rules = strategy_cfg.get("platform_rules") or {}
    xhs_req = rules.get("xhs_requires") or []
    # 应明确普通用户视角
    assert any("普通用户" in r for r in xhs_req)


def test_strategy_hard_tech_filter_uses_mom_test(strategy_cfg):
    rules = strategy_cfg.get("platform_rules") or {}
    hard_filter = rules.get("hard_tech_filter", "")
    # 新口径：妈妈测试
    assert "妈妈" in hard_filter or "我妈妈" in hard_filter or "普通用户" in hard_filter


# ============ 整体一致性 ============

def test_no_legacy_hard_tech_list_in_strategy_only_x(strategy_cfg):
    """确保老的硬技术 only_x 列表已被新口径替换"""
    rules = strategy_cfg.get("platform_rules") or {}
    only_x = rules.get("only_x_default") or []
    # 新口径应基于「无大众价值」「圈内」「SaaS 内部」「网络工具」判断
    assert len(only_x) <= 6, "only_x_default 仍过多，可能没替换老口径"
    # 不应超过 6 条（原版有 8 条纯技术）


def test_xhs_style_includes_image_prompt_section(xhs_style):
    """回归：原有配图提示词段不应被覆盖"""
    assert "配图提示词" in xhs_style
    assert "主体" in xhs_style
    assert "构图" in xhs_style


def test_xhs_style_includes_banned_words(xhs_style):
    """回归：原有 ban 词段不应丢失"""
    # 老 ban 词还在
    assert "震惊" in xhs_style
    assert "宝子们" in xhs_style