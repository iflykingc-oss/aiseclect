"""手动测试标点修复 - 用户报告的实际案例"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from collect_pipeline.humanizer import humanize_text
from collect_pipeline.rhythm_humanizer import vary_sentence_rhythm

# 用户报告的实际问题案例
test_case = "OpenAI 现在专门养了一只 AI 去打自家 AI，。代号 GPT-Red"

print("=" * 60)
print("标点修复验证 - 用户实际案例")
print("=" * 60)
print(f"\n原文: {test_case}")
print(f"包含 ，。: {'❌ 是' if '，。' in test_case else '✅ 否'}")

# 测试 humanize_text
result1, _ = humanize_text(test_case)
print(f"\n经过 humanize_text: {result1}")
print(f"包含 ，。: {'❌ 仍存在' if '，。' in result1 else '✅ 已修复'}")

# 测试 vary_sentence_rhythm
result2 = vary_sentence_rhythm(test_case)
print(f"\n经过 vary_sentence_rhythm: {result2}")
print(f"包含 ，。: {'❌ 仍存在' if '，。' in result2 else '✅ 已修复'}")

# 综合测试
print("\n" + "=" * 60)
if '，。' not in result1 and '，。' not in result2:
    print("✅ 修复成功！两个函数都正确处理了错误标点")
else:
    print("❌ 修复失败！请检查代码")
print("=" * 60)
