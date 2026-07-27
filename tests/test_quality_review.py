"""
quality_review 单元测试（2026-07-26 新增）

覆盖：
- ReviewIssue / QualityReview dataclass 序列化
- _load_review_cfg 加载 + 回退
- _parse_reviews 解析 + 容错
- review_drafts 集成（无 cfg / 失败 → 返回空 dict）
- TweetDraft review 字段写入

运行：pytest tests/test_quality_review.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEW_CFG = REPO_ROOT / "config" / "quality_review_llm_cfg.json"

from collect_pipeline.models import TweetDraft
from collect_pipeline.quality_review import (
    QualityReview,
    ReviewIssue,
    _load_review_cfg,
    _parse_reviews,
    review_drafts,
)


def _make_draft(idx: int, url: str = "", platform: str = "X+小红书") -> TweetDraft:
    return TweetDraft(
        unique_id=f"t{idx}",
        url=url or f"https://x.com/{idx}",
        title=f"Test Article {idx}",
        category="AI 产品",
        heat_score=80.0,
        tweet_content="测试推文内容",
        other_title="测试小红书标题",
        other_content="测试小红书正文" * 5,
        other_tags=["AI", "工具"],
        platform=platform,
    )


# ============ 文件 + Dataclass 完整性 ============

def test_review_cfg_exists():
    assert REVIEW_CFG.is_file(), "config/quality_review_llm_cfg.json 必须存在"


def test_review_cfg_has_required_sections():
    data = json.loads(REVIEW_CFG.read_text(encoding="utf-8"))
    assert "sp" in data
    assert "up" in data
    sp = data["sp"]
    # 7 维评分
    for dim in ["fact_consistency", "hook_quality", "actionability", "structure_quality",
                "expression_quality", "platform_fit", "risk_handling"]:
        assert dim in sp, f"缺少评分维度: {dim}"
    # 3 个 action
    for action in ["publish", "revise", "block"]:
        assert action in sp


def test_review_issue_serialization():
    issue = ReviewIssue(category="hook", severity="high", message="首行不够具体",
                        suggestion="加具体数字", auto_fixable=True)
    d = json.loads(json.dumps(issue, default=lambda x: x.__dict__))
    assert d["category"] == "hook"
    assert d["auto_fixable"] is True


def test_quality_review_to_from_dict():
    r = QualityReview(
        url="u", overall_score=75.0, allow_publish=True, recommended_action="publish",
        summary="ok", dimension_scores={"hook_quality": 18, "fact_consistency": 18},
        issues=[ReviewIssue(category="hook", severity="low", message="minor")],
        auto_fixable_count=0,
    )
    d = r.to_dict()
    assert d["url"] == "u"
    assert d["overall_score"] == 75.0
    assert d["issues"][0]["category"] == "hook"
    restored = QualityReview.from_dict(d)
    assert restored.url == "u"
    assert restored.issues[0].category == "hook"


def test_quality_review_from_dict_handles_empty():
    r = QualityReview.from_dict({})
    assert r.url == ""
    assert r.overall_score == 0.0


# ============ _load_review_cfg ============

def test_load_review_cfg_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    assert _load_review_cfg(str(tmp_path)) is None


def test_load_review_cfg_returns_none_on_parse_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    (Path(str(tmp_path)) / "config").mkdir()
    (Path(str(tmp_path)) / "config" / "quality_review_llm_cfg.json").write_text("bad", encoding="utf-8")
    assert _load_review_cfg(str(tmp_path)) is None


# ============ _parse_reviews ============

def test_parse_reviews_basic():
    drafts = [_make_draft(1), _make_draft(2)]
    raw = [
        {"url": "https://x.com/1", "overall_score": 80, "allow_publish": True,
         "recommended_action": "publish", "summary": "好", "dimension_scores": {}, "issues": [], "auto_fixable_count": 0},
        {"url": "https://x.com/2", "overall_score": 50, "allow_publish": False,
         "recommended_action": "block", "summary": "差", "dimension_scores": {}, "issues": [], "auto_fixable_count": 0},
    ]
    out = _parse_reviews(raw, drafts)
    assert "https://x.com/1" in out
    assert "https://x.com/2" in out
    assert out["https://x.com/2"].recommended_action == "block"


def test_parse_reviews_drops_unknown_urls():
    """URL 不在 drafts → 丢弃（防 LLM 编造）"""
    drafts = [_make_draft(1)]
    raw = [
        {"url": "https://x.com/1", "overall_score": 80, "recommended_action": "publish"},
        {"url": "https://evil.com/fake", "overall_score": 80, "recommended_action": "publish"},
    ]
    out = _parse_reviews(raw, drafts)
    assert "https://x.com/1" in out
    assert "https://evil.com/fake" not in out


def test_parse_reviews_handles_invalid_items():
    drafts = [_make_draft(1)]
    raw = ["not a dict", {"url": "https://x.com/1", "overall_score": 80}, 42]
    out = _parse_reviews(raw, drafts)
    assert len(out) == 1


# ============ review_drafts 集成 ============

def test_review_drafts_returns_empty_for_empty_input():
    assert review_drafts([]) == {}


def test_review_drafts_returns_empty_when_cfg_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    drafts = [_make_draft(1)]
    out = review_drafts(drafts)
    assert out == {}


def test_review_drafts_returns_empty_on_llm_error(tmp_path, monkeypatch):
    """LLM 调用失败时返回空 dict，不阻塞主流程"""
    monkeypatch.chdir(REPO_ROOT)
    drafts = [_make_draft(1)]
    with patch("collect_pipeline.quality_review.build_chat_model",
               side_effect=RuntimeError("LLM down")):
        out = review_drafts(drafts)
    assert out == {}


# ============ TweetDraft 字段 ============

def test_tweet_draft_has_review_fields():
    d = _make_draft(1)
    assert hasattr(d, "review_overall_score")
    assert hasattr(d, "review_recommended_action")
    assert hasattr(d, "review_summary")
    assert hasattr(d, "review_issues")
    assert d.review_overall_score == 0.0
    assert d.review_recommended_action == ""
    assert d.review_summary == ""
    assert d.review_issues == []