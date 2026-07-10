from __future__ import annotations

import json
from pathlib import Path

from graphs.nodes.tweet_generator_node import _quality_check_other, _score_image_prompt_quality, _validate_image_prompt


GOOD_PROMPT = "主体：年轻打工人对着电脑比较 AI 工具；构图：左侧旧流程右侧新流程对比；配色：蓝紫渐变加暖黄色重点；字体：大标题加三条短标签；氛围：清爽信息图卡片，4:5 小红书封面。"


def test_content_strategy_has_image_prompt_rubric():
    path = Path(__file__).resolve().parents[1] / "config" / "content_strategy.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    rubric = data["image_prompt_rubric"]
    assert "主体" in rubric["required_segments"]
    assert "水印" in rubric["forbidden"]
    assert rubric["min_length"] == 60


def test_validate_image_prompt_accepts_structured_prompt():
    ok, reason = _validate_image_prompt(GOOD_PROMPT)
    assert ok
    assert reason == "ok"


def test_validate_image_prompt_rejects_forbidden_terms():
    prompt = "主体：真实名人脸拿着手机；构图：居中；配色：蓝色；字体：大字；氛围：商业海报，右下角加水印和二维码，4:5。"
    ok, reason = _validate_image_prompt(prompt)
    assert not ok
    assert "命中禁用元素" in reason


def test_short_but_usable_prompt_warns_not_rejects():
    prompt = "小红书封面，3:4，蓝紫色信息图卡片，突出 AI 工具避坑。"
    ok, reason = _validate_image_prompt(prompt)
    assert ok
    assert "偏短" in reason


def test_quality_check_other_rejects_bad_image_prompt():
    data = {
        "other_title": "这类AI工具别急付费",
        "other_content": "普通人和打工人都可以先看这次更新。它会影响隐私、价格和使用方式。建议先检查权限，再判断是否付费。这个流程适合新手避坑，也适合创作者选择工具。对不想折腾的人来说，先看免费额度、数据权限和是否能导出结果，再决定要不要长期使用。最后再看同类工具有没有免费平替，避免因为营销话术冲动订阅。",
        "other_tags": ["AI工具", "效率工具", "隐私安全"],
        "image_prompt": "真实名人脸拿着手机，带水印和二维码，4:5 小红书封面。",
    }
    ok, reason = _quality_check_other(data)
    assert not ok
    assert "配图提示词" in reason


def test_score_image_prompt_quality_rewards_complete_prompt():
    score, notes = _score_image_prompt_quality(GOOD_PROMPT, {})
    assert score >= 9
    assert notes == "ok"
