"""验证人性化效果测试脚本

对比开启/关闭 rhythm_humanizer 的效果差异：
1. 生成两组相同内容的草稿
2. 一组开启人性化，一组关闭
3. 对比 AI 检测率、ai_tone 评分
4. 输出测试报告

用法:
    python scripts/test_humanizer_effect.py
"""
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from collect_pipeline.humanizer import humanize_draft

# 测试样本（真实生成的草稿）
TEST_SAMPLES = [
    {
        "platform": "general",
        "content": "GPT-4.5 发布，性能提升显著。OpenAI 宣布新模型在推理和代码生成方面有重大突破，同时降低了 API 价格。这对开发者来说是个好消息，意味着更强大的 AI 能力触手可及。",
        "xhs_title": "GPT-4.5 来了！性能暴涨",
        "xhs_content": "姐妹们！GPT-4.5 正式发布啦！\n\n这次升级真的很猛：\n✨ 推理能力提升 30%\n✨ 代码生成准确率更高\n✨ API 价格降低 20%\n\n对咱们开发者来说，这就是实实在在的福利啊！更强的能力，更低的成本，真香！"
    },
    {
        "platform": "general",
        "content": "Anthropic 推出 Claude 3.5 Sonnet，在编程任务上超越 GPT-4。新模型在代码审查、重构和调试方面表现出色，同时保持了对话的自然性。",
        "xhs_title": "Claude 3.5 编程能力爆表",
        "xhs_content": "震惊！Claude 3.5 Sonnet 编程能力碾压 GPT-4！\n\n实测效果：\n🔥 代码审查超准确\n🔥 重构建议很靠谱\n🔥 Debug 一针见血\n\n关键是对话还特别自然，不像机器人。这波 Anthropic 是真的强！"
    }
]


def test_with_humanizer(sample: dict) -> dict:
    """测试开启人性化"""
    result, tone_report = humanize_draft(
        sample.copy(),
        platform="xiaohongshu" if "xhs" in sample else "general",
        enable_rhythm=True
    )
    return {
        "content": result.get("content", ""),
        "xhs_content": result.get("xhs_content", ""),
        "ai_tone": tone_report.ai_score,
        "ai_cliche_hits": tone_report.ai_cliche_hits,
        "humanizer": "enabled"
    }


def test_without_humanizer(sample: dict) -> dict:
    """测试关闭人性化"""
    result, tone_report = humanize_draft(
        sample.copy(),
        platform="xiaohongshu" if "xhs" in sample else "general",
        enable_rhythm=False
    )
    return {
        "content": result.get("content", ""),
        "xhs_content": result.get("xhs_content", ""),
        "ai_tone": tone_report.ai_score,
        "ai_cliche_hits": tone_report.ai_cliche_hits,
        "humanizer": "disabled"
    }


def main():
    """主函数"""
    print("人性化效果验证测试\n")

    results = []

    for i, sample in enumerate(TEST_SAMPLES, 1):
        print(f"测试样本 #{i}")
        print("-" * 60)

        # 开启人性化
        with_humanizer = test_with_humanizer(sample)

        # 关闭人性化
        without_humanizer = test_without_humanizer(sample)

        # 对比
        ai_tone_diff = without_humanizer["ai_tone"] - with_humanizer["ai_tone"]

        print(f"关闭人性化 - AI tone: {without_humanizer['ai_tone']:.0f}, "
              f"套话: {len(without_humanizer['ai_cliche_hits'])}")
        print(f"开启人性化 - AI tone: {with_humanizer['ai_tone']:.0f}, "
              f"套话: {len(with_humanizer['ai_cliche_hits'])}")
        print(f"改善: AI tone -{ai_tone_diff:.0f} "
              f"({-ai_tone_diff / max(without_humanizer['ai_tone'], 1) * 100:.0f}%)\n")

        results.append({
            "sample_id": i,
            "original": sample,
            "with_humanizer": with_humanizer,
            "without_humanizer": without_humanizer,
            "improvement": {
                "ai_tone_reduction": ai_tone_diff,
                "ai_tone_reduction_pct": -ai_tone_diff / max(without_humanizer['ai_tone'], 1) * 100
            }
        })

    # 保存测试报告
    report_file = Path("output/humanizer_test_report.json")
    report_file.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "test_time": datetime.now().isoformat(),
        "test_samples": len(TEST_SAMPLES),
        "results": results,
        "summary": {
            "avg_ai_tone_reduction": sum(r["improvement"]["ai_tone_reduction"] for r in results) / len(results),
            "avg_reduction_pct": sum(r["improvement"]["ai_tone_reduction_pct"] for r in results) / len(results)
        }
    }

    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print("测试总结")
    print(f"平均 AI tone 降低: {report['summary']['avg_ai_tone_reduction']:.0f} "
          f"({report['summary']['avg_reduction_pct']:.0f}%)")
    print(f"\n测试报告已保存: {report_file}")
    print("\n提示: 运行 7 天真实数据测试以获得更准确的效果评估")


if __name__ == "__main__":
    main()
