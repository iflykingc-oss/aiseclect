from __future__ import annotations

import json
from pathlib import Path

from collect_pipeline.growth_taxonomy import (
    assign_note_structure,
    assign_pillar,
    assign_title_pattern_key,
    load_growth_taxonomy,
    score_xhs_dimensions,
)
from collect_pipeline.models import ScoredMaterial, TweetDraft
from graphs.nodes.feishu_writer_node import _build_records
from graphs.nodes.tweet_generator_node import _build_draft
from tools.tweet_writer import write_tweets

ROOT = Path(__file__).resolve().parents[1]


def _strategy() -> dict:
    return json.loads((ROOT / "config" / "content_strategy.json").read_text(encoding="utf-8"))


def _mat(**kwargs) -> ScoredMaterial:
    base = {
        "url": "https://example.com/prompt",
        "title": "Claude Code Prompt 模板，新手可以直接抄",
        "snippet": "5 步把需求拆成可执行任务",
        "content": "适合新手、打工人和创作者复用的 prompt 模板教程。",
        "source": "aihot",
        "category": "AI工具",
        "heat_score": 88.0,
        "score_reason": "mock",
    }
    base.update(kwargs)
    return ScoredMaterial(**base)


def _good_generated_payload() -> dict:
    return {
        "unique_id": "tweet_test_001",
        "url": "https://example.com/prompt",
        "title": "Claude Code Prompt 模板，新手可以直接抄",
        "category": "AI工具",
        "heat_score": 88,
        "platform": "X+小红书",
        "content_angle": "tool_use_case",
        "hook_type": "实用技巧",
        "platform_reason": "Prompt 模板适合普通用户和创作者直接复用",
        "tweet_content": "这个 Prompt 模板适合新手直接抄。\nClaude Code 用户可以用它把需求拆成步骤，减少来回改稿。\n我的判断：先用在非核心任务，今天就能复制试一次。",
        "other_title": "这一句Prompt新手直接抄",
        "other_content": "适合谁：刚开始用 Claude Code 的新手、打工人和创作者。这个模板能帮你把需求拆成目标、约束、步骤和验收标准，避免一句话丢给 AI 后反复返工。建议先用在非核心任务里，复制后替换成自己的项目名和输出格式。收藏价值在于：以后写需求、写文案、拆任务都能复用，也能继续改成自己的工作流模板。",
        "other_tags": ["AI工具", "prompt模板", "新手入门", "效率提升"],
        "image_prompt": "主体：年轻打工人对着电脑复制 Prompt 模板；构图：左侧旧需求右侧新模板对比；配色：蓝紫渐变加暖黄色重点；字体：大标题加三条短标签；氛围：清爽信息图卡片，4:5 小红书封面。",
    }


def test_growth_taxonomy_config_exists():
    data = _strategy()
    taxonomy = data["xiaohongshu"]["growth_taxonomy"]
    assert len(taxonomy["pillars"]) >= 8
    assert "assignment_rules" in taxonomy
    assert "title_patterns_by_pillar" in taxonomy


def test_assign_pillar_for_common_xhs_patterns():
    taxonomy = load_growth_taxonomy(str(ROOT))
    assert assign_pillar(_mat(title="一句 prompt 直接抄", content="提示词模板"), taxonomy) == "spell"
    assert assign_pillar(_mat(title="新手 5 步上手 AI 写作", content="教程 步骤"), taxonomy) == "tutorial"
    assert assign_pillar(_mat(title="这类 AI 工具别急着付费", content="隐私 风险 避坑"), taxonomy) == "risk_alert"
    assert assign_pillar(_mat(title="Claude 涨价后 3 个免费平替", snippet="", content="订阅 价格"), taxonomy) == "cost_change"
    assert assign_pillar(_mat(title="普通 AI 新闻", snippet="", content="行业更新"), taxonomy) == "news_commentary"


def test_title_pattern_key_matches_existing_patterns():
    strategy = _strategy()
    taxonomy = strategy["xiaohongshu"]["growth_taxonomy"]
    patterns = strategy["xiaohongshu"]["title_patterns"]
    for pillar in [p["key"] for p in taxonomy["pillars"]]:
        label = assign_title_pattern_key(pillar, taxonomy)
        assert any(p.startswith(label + "：") for p in patterns)
        assert assign_note_structure(pillar, taxonomy)


def test_score_xhs_dimensions_returns_growth_scores():
    taxonomy = load_growth_taxonomy(str(ROOT))
    mat = _mat()
    data = _good_generated_payload() | {"xhs_pillar": "spell"}
    scores = score_xhs_dimensions(data, mat, taxonomy)
    assert set(scores) == {"xhs_search_score", "xhs_save_score", "xhs_beginner_score", "xhs_series_score"}
    assert scores["xhs_search_score"][0] > 50
    assert scores["xhs_save_score"][0] > 50
    assert scores["xhs_beginner_score"][0] > 50


def test_build_draft_adds_xhs_growth_metadata():
    draft, reason = _build_draft(_mat(), _good_generated_payload(), _strategy())
    assert reason == "ok"
    assert draft is not None
    assert draft.xhs_pillar == "spell"
    assert draft.xhs_note_structure
    assert draft.xhs_title_pattern_key == "咒语型"
    assert draft.xhs_search_score > 0
    assert draft.xhs_save_score > 0
    assert draft.xhs_beginner_score > 0
    assert "growth=spell/咒语型" in draft.quality_notes


def test_tweet_draft_growth_defaults_are_safe():
    draft = TweetDraft(unique_id="u1", url="https://example.com")
    assert draft.xhs_pillar == ""
    assert draft.xhs_search_score == 0.0
    assert draft.xhs_growth_notes == ""


def test_feishu_record_contains_growth_fields():
    draft = TweetDraft(
        unique_id="u1",
        url="https://example.com",
        xhs_pillar="spell",
        xhs_note_structure="hook + prompt_block + tweak_notes",
        xhs_title_pattern_key="咒语型",
        xhs_search_score=80,
        xhs_save_score=70,
        xhs_beginner_score=90,
        xhs_series_score=60,
        xhs_growth_notes="search=80",
    )
    fields = _build_records([draft])[0]["fields"]
    assert fields["起号定位"] == "spell"
    assert fields["笔记结构"] == "hook + prompt_block + tweak_notes"
    assert fields["标题模板"] == "咒语型"
    assert fields["搜索分"] == 80
    assert fields["起号备注"] == "search=80"


def test_quality_and_growth_reports_include_growth_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("AISECLECT_OUTPUT_DIR", str(tmp_path))
    draft = TweetDraft(
        unique_id="u1",
        url="https://example.com",
        title="测试",
        xhs_pillar="spell",
        xhs_note_structure="hook + prompt_block + tweak_notes",
        xhs_title_pattern_key="咒语型",
        xhs_search_score=80,
        xhs_save_score=70,
        xhs_beginner_score=90,
        xhs_series_score=60,
        xhs_growth_notes="search=80",
    )
    write_tweets([draft])
    quality = json.loads(next(tmp_path.glob("quality_report_*.json")).read_text(encoding="utf-8"))
    growth = json.loads(next(tmp_path.glob("xhs_growth_report_*.json")).read_text(encoding="utf-8"))
    assert quality["items"][0]["xhs_pillar"] == "spell"
    assert growth["pillar_distribution"] == {"spell": 1}
    assert growth["items"][0]["xhs_title_pattern_key"] == "咒语型"
