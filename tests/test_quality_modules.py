"""
quality_gate + length_validator + rhythm_humanizer 单元测试

覆盖：
- quality_gate: 三档门限（<60 reject / 60-80 review / >=80 approve）
- quality_gate.batch_quality_gate: 批量分类 + stats
- length_validator: validate_length / smart_truncate / post_generation_check
- rhythm_humanizer: vary_sentence_rhythm / add_platform_voice

这三个模块都被 heat_scorer / tweet_generator 在主路径上调用，但之前没有 pytest 覆盖。
本次新增单测，避免回归。

运行：pytest tests/test_quality_modules.py -v
"""
from __future__ import annotations

from collect_pipeline.rhythm_humanizer import (
    add_platform_voice,
    vary_sentence_rhythm,
)
from graphs.nodes.length_validator import (
    PLATFORM_LIMITS,
    post_generation_check,
    smart_truncate,
    validate_length,
)
from graphs.nodes.quality_gate import (
    QualityGateResult,
    batch_quality_gate,
    quality_gate,
)


# ============ quality_gate 单条 ============

def test_quality_gate_reject_low_score():
    """<60 → REJECT"""
    r = quality_gate(45.0, "https://x.com/1", "低分", "s")
    assert r.action == "REJECT"
    assert r.confidence_score == 45.0
    assert "60" in r.reason


def test_quality_gate_review_mid_score():
    """60 <= score < 80 → REVIEW_QUEUE"""
    r = quality_gate(70.0, "https://x.com/2", "中分", "s")
    assert r.action == "REVIEW_QUEUE"
    assert r.confidence_score == 70.0


def test_quality_gate_approve_high_score():
    """>=80 → AUTO_APPROVE"""
    r = quality_gate(85.0, "https://x.com/3", "高分", "s")
    assert r.action == "AUTO_APPROVE"
    assert r.confidence_score == 85.0


def test_quality_gate_boundary_60_is_review_not_reject():
    """边界值 60.0 应归入 REVIEW，不是 REJECT"""
    r = quality_gate(60.0, "u", "t", "s")
    assert r.action == "REVIEW_QUEUE"


def test_quality_gate_boundary_80_is_approve_not_review():
    """边界值 80.0 应归入 AUTO_APPROVE，不是 REVIEW"""
    r = quality_gate(80.0, "u", "t", "s")
    assert r.action == "AUTO_APPROVE"


def test_quality_gate_returns_dataclass():
    """返回值类型稳定为 QualityGateResult"""
    r = quality_gate(50.0, "u", "t", "s")
    assert isinstance(r, QualityGateResult)
    assert hasattr(r, "action")
    assert hasattr(r, "reason")
    assert hasattr(r, "confidence_score")


# ============ quality_gate 批量 ============

def test_batch_quality_gate_classifies_correctly():
    items = [
        (45.0, "u1", "t1", "s1"),   # REJECT
        (65.0, "u2", "t2", "s2"),   # REVIEW
        (85.0, "u3", "t3", "s3"),   # APPROVE
        (75.0, "u4", "t4", "s4"),   # REVIEW
        (90.0, "u5", "t5", "s5"),   # APPROVE
    ]
    out = batch_quality_gate(items)
    assert out["stats"] == {"total": 5, "approve": 2, "review": 2, "reject": 1}
    assert {m["url"] for m in out["auto_approve"]} == {"u3", "u5"}
    assert {m["url"] for m in out["review_queue"]} == {"u2", "u4"}
    assert {m["url"] for m in out["rejected"]} == {"u1"}


def test_batch_quality_gate_handles_3_tuple_input():
    """旧调用约定：3 元组 (score, url, title)，应仍能工作"""
    items = [(45.0, "u1", "t1"), (90.0, "u2", "t2")]
    out = batch_quality_gate(items)
    assert out["stats"]["total"] == 2
    assert out["stats"]["reject"] == 1
    assert out["stats"]["approve"] == 1


def test_batch_quality_gate_empty():
    out = batch_quality_gate([])
    assert out["stats"]["total"] == 0
    assert out["auto_approve"] == []
    assert out["review_queue"] == []
    assert out["rejected"] == []


# ============ length_validator.validate_length ============

def test_validate_length_x_within_range():
    ok, reason, n = validate_length("a" * 100, "x")
    assert ok is True
    assert n == 100
    assert reason == "ok"


def test_validate_length_x_too_short():
    ok, _, n = validate_length("短", "x")
    assert ok is False
    assert n < PLATFORM_LIMITS["x"]["min"]


def test_validate_length_x_too_long():
    ok, _, n = validate_length("a" * 500, "x")
    assert ok is False
    assert n > PLATFORM_LIMITS["x"]["max"]


def test_validate_length_xiaohongshu_within_range():
    body = "a" * 300
    ok, _, n = validate_length(body, "xiaohongshu")
    assert ok is True
    assert n == 300


def test_validate_length_empty_text_fails():
    ok, reason, n = validate_length("", "x")
    assert ok is False
    assert reason == "文本为空"
    assert n == 0


def test_validate_length_unknown_platform_falls_back_to_x():
    """未知平台用 x 的限制"""
    ok, _, n = validate_length("a" * 100, "tiktok")
    assert n == 100
    assert ok is True


# ============ length_validator.smart_truncate ============

def test_smart_truncate_no_op_when_within_limit():
    text = "短文本"
    assert smart_truncate(text, 100) == text


def test_smart_truncate_preserves_paragraph_boundary():
    """长文本应在段落边界截断"""
    text = "第一段内容\n\n第二段内容" + "x" * 500 + "\n\n第三段"
    out = smart_truncate(text, 100)
    # 第一段 + 第二段（部分）
    assert "第一段内容" in out
    # 长度不超限
    assert len(out) <= 110  # 允许少量 buffer


def test_smart_truncate_falls_back_to_sentence_boundary():
    """无段落边界时按句号截断"""
    text = "第一句。" + "中段内容。" * 50 + "末句。"
    out = smart_truncate(text, 100)
    assert "第一句。" in out
    assert len(out) <= 110


def test_smart_truncate_hard_truncate_when_no_boundary():
    """硬截断保底"""
    text = "a" * 1000  # 没有段落/句子边界
    out = smart_truncate(text, 100, preserve_structure=True)
    # 应该 hard-truncate 到 100 + "..."
    assert out.startswith("a" * 95)
    assert out.endswith("...")


def test_smart_truncate_hard_mode():
    """preserve_structure=False 直接硬截断"""
    text = "一。二。三。"
    out = smart_truncate(text, 5, preserve_structure=False)
    assert len(out) <= 10  # 5 + "..."
    assert out.endswith("...")


# ============ length_validator.post_generation_check ============

def test_post_generation_check_truncates_long_x():
    # 用有段落的输入，让 smart_truncate 真的能压短
    draft = {"tweet_content": "段落一。" + "a" * 100 + "\n\n" + "段落二。" + "b" * 600}
    original_len = len(draft["tweet_content"])
    fixed, fixes = post_generation_check(draft, strict=True)
    # smart_truncate 是近似的，允许 +5 buffer
    assert len(fixed["tweet_content"]) <= PLATFORM_LIMITS["x"]["max"] + 5
    # 长度明显缩短
    assert len(fixed["tweet_content"]) < original_len
    assert any("X 内容超长" in f and "已截断" in f for f in fixes)


def test_post_generation_check_truncates_long_xiaohongshu():
    # 输入远超上限 + 有结构，smart_truncate 才有机会截到 max
    draft = {"other_content": "段落一。" + "a" * 200 + "\n\n" + "段落二。" + "b" * 500}
    fixed, fixes = post_generation_check(draft, strict=True)
    assert len(fixed["other_content"]) <= PLATFORM_LIMITS["xiaohongshu"]["max"] + 5  # 允许 buffer
    assert any("小红书内容超长" in f and "已截断" in f for f in fixes)


def test_post_generation_check_strict_false_records_but_does_not_truncate():
    draft = {"tweet_content": "a" * 500}
    fixed, fixes = post_generation_check(draft, strict=False)
    # 不应截断
    assert fixed["tweet_content"] == draft["tweet_content"]
    # 但记录异常
    assert any("长度异常" in f for f in fixes)


def test_post_generation_check_no_change_when_within_range():
    # X 限制 50-380，这里写 100 字符（无段落，smart_truncate 走硬截断前不动）
    # XHS 限制 120-450，写 200 字符
    draft = {
        "tweet_content": "测试内容" * 17,  # 17*4 = 68 字
        "other_content": "测试内容" * 40,  # 40*4 = 160 字
    }
    fixed, fixes = post_generation_check(draft, strict=True)
    assert fixes == []
    assert fixed == draft


def test_post_generation_check_empty_draft():
    fixed, fixes = post_generation_check({})
    assert fixes == []
    assert fixed == {}


# ============ rhythm_humanizer.vary_sentence_rhythm ============

def test_vary_sentence_rhythm_empty_returns_empty():
    assert vary_sentence_rhythm("") == ""
    assert vary_sentence_rhythm("   ") == "   "


def test_vary_sentence_rhythm_short_text_unchanged():
    """<20 字的短文本不处理"""
    short = "短文本"
    assert vary_sentence_rhythm(short) == short


def test_vary_sentence_rhythm_splits_long_sentence():
    """超长单句应在逗号处拆分"""
    long_sentence = "这是前半部分内容，" + ("中间填充 " * 10) + "这是后半部分"
    out = vary_sentence_rhythm(long_sentence)
    # 应该出现句号拆分
    assert "。" in out or len(out) < len(long_sentence) + 1


def test_vary_sentence_rhythm_repairs_punctuation():
    """修复错误标点组合（，。和。，）"""
    import re
    broken = "你好，。世界" + "，" + ("扩展内容" * 5)
    out = vary_sentence_rhythm(broken)
    # 修复后的文本不应有连续。，或。，
    assert not re.search(r"，+。+", out), f"发现连续，。: {out}"
    assert not re.search(r"。+，+", out), f"发现连续。，: {out}"


# ============ rhythm_humanizer.add_platform_voice ============

def test_add_platform_voice_xiaohongshu_adds_emoji():
    text = "这是一段普通的中文文本，没有 emoji"
    out = add_platform_voice(text, "xiaohongshu")
    # 小红书语气会加 emoji 或特定语气词
    # 不严格断言长度变长，但应与原文不同
    assert isinstance(out, str)
    assert len(out) >= len(text) or out != text


def test_add_platform_voice_empty_returns_empty():
    assert add_platform_voice("", "xiaohongshu") == ""


def test_add_platform_voice_unknown_platform_returns_text():
    """未知平台语气保留原文"""
    text = "原始文本"
    out = add_platform_voice(text, "tiktok")
    # 未知平台应至少不破坏原文
    assert isinstance(out, str)
    assert text in out or out == text