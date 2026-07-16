"""标点修复测试脚本

验证 humanizer 和 rhythm_humanizer 是否正确修复错误标点组合（，。 或 。，）
"""
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from collect_pipeline.humanizer import humanize_text
from collect_pipeline.rhythm_humanizer import vary_sentence_rhythm


def test_punctuation_fixes():
    """测试标点修复"""
    test_cases = [
        ("这是测试，。内容", "这是测试。内容"),
        ("OpenAI 现在专门养了一只 AI 去打自家 AI，。代号 GPT-Red", "OpenAI 现在专门养了一只 AI 去打自家 AI。代号 GPT-Red"),
        ("句子结尾。，下一句", "句子结尾。下一句"),
        ("多个错误，。，。修复", "多个错误。修复"),
        ("正常标点，没有问题。", "正常标点，没有问题。"),
    ]

    print("=" * 60)
    print("标点修复测试")
    print("=" * 60)

    for i, (input_text, expected) in enumerate(test_cases, 1):
        # 测试 humanize_text
        result_humanize, _ = humanize_text(input_text, level="soft")

        # 测试 vary_sentence_rhythm
        result_rhythm = vary_sentence_rhythm(input_text)

        print(f"\n测试 {i}:")
        print(f"  输入: {input_text}")
        print(f"  预期: {expected}")
        print(f"  humanize_text: {result_humanize}")
        print(f"  vary_sentence_rhythm: {result_rhythm}")

        humanize_pass = "✅" if "，。" not in result_humanize and "。，" not in result_humanize else "❌"
        rhythm_pass = "✅" if "，。" not in result_rhythm and "。，" not in result_rhythm else "❌"

        print(f"  结果: humanize={humanize_pass} rhythm={rhythm_pass}")


if __name__ == "__main__":
    test_punctuation_fixes()
