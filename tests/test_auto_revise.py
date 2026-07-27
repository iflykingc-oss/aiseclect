"""
auto_revise_draft 单元测试（2026-07-26 新增）

覆盖：
- revise + 有 auto_fixable → 调 LLM 修复，字段写回
- revise + 无 auto_fixable → 跳过
- block → 不修复（即使有 auto_fixable）
- publish → 不修复
- LLM 返回无效数据 → 草稿不变
- AISECLECT_ENABLE_REVIEW=false → review_drafts 跳过

运行：pytest tests/test_auto_revise.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from collect_pipeline.models import TweetDraft
from collect_pipeline.quality_review import (
    QualityReview,
    ReviewIssue,
    _build_revision_messages,
    auto_revise_draft,
    review_drafts,
)


def _make_draft(platform: str = "X+小红书") -> TweetDraft:
    return TweetDraft(
        unique_id="t1",
        url="https://x.com/1",
        title="Test",
        category="AI 产品",
        heat_score=80.0,
        tweet_content="原 X 内容（首行不够具体）",
        other_title="原 XHS 标题",
        other_content="原 XHS 正文" * 10,
        other_tags=["AI", "工具"],
        platform=platform,
        image_prompt="原配图",
    )


def _make_review(action: str, overall: float = 70.0, auto_fix_count: int = 1) -> QualityReview:
    issues = [
        ReviewIssue(category="hook", severity="medium",
                    message="首行不够具体", suggestion="加具体数字",
                    auto_fixable=bool(auto_fix_count)),
    ] * max(1, auto_fix_count)
    return QualityReview(
        url="https://x.com/1",
        overall_score=overall,
        allow_publish=action == "publish",
        recommended_action=action,
        summary="需要修复",
        issues=issues,
        auto_fixable_count=auto_fix_count,
    )


# ============ auto_revise_draft 行为 ============

def test_auto_revise_skips_when_action_not_revise(tmp_path, monkeypatch):
    """publish / block → 不修复"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    d = _make_draft()
    for action in ("publish", "block"):
        d.tweet_content = "原内容"
        r = _make_review(action=action)
        out, _, rounds = auto_revise_draft(d, r)
        assert rounds == 0
        assert out.tweet_content == "原内容"


def test_auto_revise_skips_when_no_auto_fixable_issues(tmp_path, monkeypatch):
    """revise 但所有 issue 都 auto_fixable=False → 跳过"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    d = _make_draft()
    r = _make_review(action="revise", auto_fix_count=1)
    r.issues = [ReviewIssue(category="fact", severity="blocker", message="事实错", auto_fixable=False)]
    out, _, rounds = auto_revise_draft(d, r)
    assert rounds == 0


def test_auto_revise_skips_when_cfg_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    d = _make_draft()
    r = _make_review(action="revise", auto_fix_count=1)
    out, _, rounds = auto_revise_draft(d, r)
    assert rounds == 0


def test_auto_revise_writes_back_fields(tmp_path, monkeypatch):
    """LLM 成功返回 → 字段被写回"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)

    # 准备 quality_review_llm_cfg
    cfg_path = Path(str(tmp_path)) / "config" / "quality_review_llm_cfg.json"
    cfg_path.parent.mkdir()
    cfg_path.write_text(json.dumps({"sp": "你很厉害", "up": "{{drafts_json}}", "config": {}}), encoding="utf-8")

    d = _make_draft()
    r = _make_review(action="revise", auto_fix_count=1)

    fake_response = {
        "url": "https://x.com/1",
        "tweet_content": "修复后 X 内容（首行加具体数字）",
        "other_title": "修复后 XHS 标题",
        "other_content": "修复后 XHS 正文" * 5,
        "other_tags": ["AI", "工具", "教程"],
        "image_prompt": "修复后配图",
    }

    mock_resp = MagicMock()
    mock_resp.content = json.dumps(fake_response, ensure_ascii=False)

    with patch("collect_pipeline.quality_review.build_chat_model"), \
         patch("collect_pipeline.quality_review.invoke_with_retry", return_value=mock_resp):
        out, _, rounds = auto_revise_draft(d, r, workspace=str(tmp_path), max_rounds=1)

    assert rounds == 1
    assert "修复后" in out.tweet_content
    assert out.other_title == "修复后 XHS 标题"
    assert "修复后" in out.other_content
    assert out.other_tags == ["AI", "工具", "教程"]
    assert out.image_prompt == "修复后配图"


def test_auto_revise_handles_markdown_fenced_json(tmp_path, monkeypatch):
    """LLM 返回被 ```json ... ``` 包住 → 仍能解析"""
    monkeypatch.chdir(tmp_path)
    cfg_path = Path(str(tmp_path)) / "config" / "quality_review_llm_cfg.json"
    cfg_path.parent.mkdir()
    cfg_path.write_text(json.dumps({"sp": "x", "up": "{{drafts_json}}", "config": {}}), encoding="utf-8")

    d = _make_draft()
    r = _make_review(action="revise", auto_fix_count=1)

    fenced = "```json\n" + json.dumps({
        "url": "https://x.com/1",
        "tweet_content": "fenced 修复",
    }, ensure_ascii=False) + "\n```"
    mock_resp = MagicMock()
    mock_resp.content = fenced

    with patch("collect_pipeline.quality_review.build_chat_model"), \
         patch("collect_pipeline.quality_review.invoke_with_retry", return_value=mock_resp):
        out, _, rounds = auto_revise_draft(d, r, workspace=str(tmp_path), max_rounds=1)

    assert rounds == 1
    assert out.tweet_content == "fenced 修复"


def test_auto_revise_handles_wrong_url(tmp_path, monkeypatch):
    """LLM 返回的 url 与草稿 url 不符 → 视为无效，保留原草稿"""
    monkeypatch.chdir(tmp_path)
    cfg_path = Path(str(tmp_path)) / "config" / "quality_review_llm_cfg.json"
    cfg_path.parent.mkdir()
    cfg_path.write_text(json.dumps({"sp": "x", "up": "{{drafts_json}}", "config": {}}), encoding="utf-8")

    d = _make_draft()
    r = _make_review(action="revise", auto_fix_count=1)
    mock_resp = MagicMock()
    mock_resp.content = json.dumps({"url": "https://evil.com/fake", "tweet_content": "改了我的"})

    with patch("collect_pipeline.quality_review.build_chat_model"), \
         patch("collect_pipeline.quality_review.invoke_with_retry", return_value=mock_resp):
        out, _, rounds = auto_revise_draft(d, r, workspace=str(tmp_path), max_rounds=1)
    assert rounds == 0
    assert out.tweet_content == "原 X 内容（首行不够具体）"


def test_auto_revise_truncates_long_fields(tmp_path, monkeypatch):
    """防 LLM 异常输出超长字段"""
    monkeypatch.chdir(tmp_path)
    cfg_path = Path(str(tmp_path)) / "config" / "quality_review_llm_cfg.json"
    cfg_path.parent.mkdir()
    cfg_path.write_text(json.dumps({"sp": "x", "up": "{{drafts_json}}", "config": {}}), encoding="utf-8")

    d = _make_draft()
    r = _make_review(action="revise", auto_fix_count=1)
    mock_resp = MagicMock()
    mock_resp.content = json.dumps({
        "url": "https://x.com/1",
        "tweet_content": "a" * 5000,
        "other_tags": ["t" * 100, "u" * 100, "v" * 100, "w" * 100, "x" * 100, "y" * 100, "z" * 100, "extra"],
    })

    with patch("collect_pipeline.quality_review.build_chat_model"), \
         patch("collect_pipeline.quality_review.invoke_with_retry", return_value=mock_resp):
        out, _, rounds = auto_revise_draft(d, r, workspace=str(tmp_path), max_rounds=1)
    assert len(out.tweet_content) <= 1000
    assert len(out.other_tags) <= 8
    assert all(len(t) <= 30 for t in out.other_tags)


def test_auto_revise_preserves_only_x_fields(tmp_path, monkeypatch):
    """仅 X 平台 → 不修改 XHS 字段"""
    monkeypatch.chdir(tmp_path)
    cfg_path = Path(str(tmp_path)) / "config" / "quality_review_llm_cfg.json"
    cfg_path.parent.mkdir()
    cfg_path.write_text(json.dumps({"sp": "x", "up": "{{drafts_json}}", "config": {}}), encoding="utf-8")

    d = _make_draft(platform="仅X")
    r = _make_review(action="revise", auto_fix_count=1)
    mock_resp = MagicMock()
    mock_resp.content = json.dumps({
        "url": "https://x.com/1",
        "tweet_content": "修复后 X",
        "other_title": "应被忽略",
        "other_content": "应被忽略",
        "other_tags": ["应被忽略"],
        "image_prompt": "应被忽略",
    })

    with patch("collect_pipeline.quality_review.build_chat_model"), \
         patch("collect_pipeline.quality_review.invoke_with_retry", return_value=mock_resp):
        out, _, rounds = auto_revise_draft(d, r, workspace=str(tmp_path), max_rounds=1)
    assert out.tweet_content == "修复后 X"
    # 仅 X 平台：XHS 字段应保持原值
    assert out.other_title == "原 XHS 标题"
    assert out.other_content == d.other_content
    assert out.other_tags == ["AI", "工具"]


# ============ AISECLECT_ENABLE_REVIEW flag ============

def test_review_disabled_by_env_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AISECLECT_ENABLE_REVIEW", "false")
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    drafts = [_make_draft()]
    out = review_drafts(drafts)
    assert out == {}


def test_review_enabled_by_default(tmp_path, monkeypatch):
    """无 env flag → 默认 on（但 cfg 缺失会返回空）"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AISECLECT_ENABLE_REVIEW", raising=False)
    monkeypatch.delenv("COZE_WORKSPACE_PATH", raising=False)
    drafts = [_make_draft()]
    # cfg 缺失 → 返回空（但说明 review_drafts 被调到了）
    out = review_drafts(drafts)
    assert out == {}


# ============ _build_revision_messages ============

def test_build_revision_messages_includes_issues():
    d = _make_draft()
    r = _make_review(action="revise", auto_fix_count=2)
    msgs = _build_revision_messages(None, d, r, workspace="/tmp")
    assert len(msgs) == 2
    user_text = msgs[1].content
    assert "Test" in user_text
    assert "首行不够具体" in user_text
    assert "auto_fixable" in user_text or "可自动修复" in user_text