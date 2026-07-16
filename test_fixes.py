#!/usr/bin/env python3
"""
快速验证修复后的关键功能
"""
import sys
sys.path.insert(0, 'src')

from graphs.nodes.dedup_filter_node import dedup_filter_node
from graphs.nodes.newsnow_collector_node import _should_keep_item
from graphs.nodes.content_cleaner_node import clean_text
from graphs.state import DedupFilterInput, StandardMaterial
from collect_pipeline.humanizer import humanize_text, AI_CLICHES

print("=" * 60)
print("aiseclect 修复验证测试")
print("=" * 60)

# Test 1: Dedup persistence
print("\n[Test 1] Dedup 持久化测试")
try:
    materials = [
        StandardMaterial(url='https://test1.com', title='Test 1', snippet='', content='', source='test'),
        StandardMaterial(url='https://test2.com', title='Test 2', snippet='', content='', source='test'),
    ]
    result = dedup_filter_node(DedupFilterInput(merged_materials=materials, clear_dedup=True))
    assert result.new_count == 2
    assert result.duplicates_count == 0
    print(f"  PASS: {result.new_count} new, {result.duplicates_count} duplicates")
except Exception as e:
    print(f"  FAIL: {e}")

# Test 2: NewsNow AI keyword filtering
print("\n[Test 2] NewsNow AI 关键词过滤")
try:
    # Should keep: AI content from general source
    assert _should_keep_item("weibo", "ChatGPT 新功能发布", "") == True
    # Should filter: non-AI from general source
    assert _should_keep_item("weibo", "台风巴威又睁眼了", "") == False
    # Should keep: all content from tech source
    assert _should_keep_item("hackernews", "Some random tech news", "") == True
    print("  PASS: AI keyword filtering works correctly")
except Exception as e:
    print(f"  FAIL: {e}")

# Test 3: Content truncation
print("\n[Test 3] 智能截断测试")
try:
    long_text = "段落1内容。" * 100 + "\n\n段落2内容。" * 100 + "\n\n段落3内容。" * 100
    cleaned = clean_text(long_text, max_chars=1200)
    assert len(cleaned) <= 1200
    # Should prefer paragraph boundary
    assert cleaned.count("\n\n") >= 1 or cleaned.count("\n") >= 1
    print(f"  PASS: {len(long_text)} chars -> {len(cleaned)} chars (smart truncation)")
except Exception as e:
    print(f"  FAIL: {e}")

# Test 4: Humanizer coverage
print("\n[Test 4] Humanizer 覆盖测试")
try:
    original_count = 36  # Original AI_CLICHES count
    new_count = len(AI_CLICHES)
    assert new_count > original_count

    # Test new patterns
    text_with_cliches = "震惊！绝了宝子们，这个AI工具太神了！"
    cleaned, report = humanize_text(text_with_cliches)
    assert "震惊" not in cleaned
    assert "绝了" not in cleaned or "不错" in cleaned
    print(f"  PASS: AI_CLICHES expanded from {original_count} to {new_count}")
    print(f"  Sample: '{text_with_cliches}' -> '{cleaned}'")
except Exception as e:
    print(f"  FAIL: {e}")

# Test 5: Batch size increase
print("\n[Test 5] Batch size 检查")
try:
    from graphs.nodes.tweet_generator_node import BATCH_SIZE
    assert BATCH_SIZE == 8
    print(f"  PASS: BATCH_SIZE = {BATCH_SIZE} (increased from 5)")
except Exception as e:
    print(f"  FAIL: {e}")

print("\n" + "=" * 60)
print("测试完成！")
print("=" * 60)
