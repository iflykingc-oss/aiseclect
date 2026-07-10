from __future__ import annotations

from collect_pipeline.humanizer import detect_ai_tone, humanize_draft, humanize_text


def test_detect_ai_tone_flags_cliche_heavy_text():
    text = "本质上，这意味着真正的核心已经出现。未来已来，这次更新值得关注，也值得深思。"
    report = detect_ai_tone(text)
    assert report.ai_score > 70
    assert "本质上" in report.ai_cliche_hits
    assert "这意味着" in report.ai_cliche_hits


def test_detect_ai_tone_allows_plain_creator_voice():
    text = "Claude Code 这次更新了 hooks。我会先看权限配置，再跑本地测试。"
    report = detect_ai_tone(text)
    assert report.ai_score < 30
    assert report.ai_cliche_hits == []


def test_humanize_text_removes_common_cliches_without_touching_url():
    original = "本质上，这意味着大家可以看看这个工具：https://example.com/demo"
    fixed, report = humanize_text(original)
    assert "本质上" not in fixed
    assert "这意味着" not in fixed
    assert "https://example.com/demo" in fixed
    assert report.ai_score < detect_ai_tone(original).ai_score


def test_humanize_text_converts_escaped_newlines():
    fixed, _ = humanize_text("第一行\\n第二行")
    assert fixed == "第一行\n第二行"


def test_humanize_text_preserves_newlines_after_punctuation():
    fixed, _ = humanize_text("第一行。\n第二行。")
    assert fixed == "第一行。\n第二行。"


def test_humanize_draft_processes_main_fields():
    data = {
        "tweet_content": "本质上，这意味着 AI 工具的真正的核心变了。\n今天先看价格和权限。",
        "other_title": "值得关注的AI更新",
        "other_content": "未来已来，这次更新不容错过。普通人可以先看隐私和价格。",
        "image_prompt": "保持不变",
    }
    fixed, report = humanize_draft(data)
    assert "本质上" not in fixed["tweet_content"]
    assert "未来已来" not in fixed["other_content"]
    assert fixed["image_prompt"] == "保持不变"
    assert report.ai_score < detect_ai_tone("\n".join([data["tweet_content"], data["other_title"], data["other_content"]])).ai_score
