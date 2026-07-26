"""一次性脚本：更新 tweet_generator_llm_cfg.json 的平台决策段（普通人口径）。

把旧的「## 仅X」「## 硬技术前言过滤」「## X+小红书」段重写为新口径。
运行一次即可，不参与主流程。
"""
import json
from pathlib import Path

CFG = Path("config/tweet_generator_llm_cfg.json")

OLD_BLOCK = (
    "# 平台决策\n"
    "platform 只能是：\n"
    "- 「仅X」\n"
    "- 「X+小红书」\n\n"
    "## 仅X\n"
    "这些只发 X，不写小红书：\n"
    "- API / SDK / endpoint 迁移、退役、参数变化\n"
    "- 论文、arxiv、benchmark、榜单、训练技巧\n"
    "- 底层框架、编译器、CUDA、kernel、纯开发者实现细节\n"
    "- 纯 GitHub repo/release 且无任何普通用户场景（能讲清谁受影响/怎么用/避坑时不适用）\n"
    "- 企业 SaaS 内部更新，普通用户无感\n"
    "- 融资、人事、收购等圈内消息，除非对普通用户有明确影响\n\n"
    "## 硬技术前言过滤（必读）\n"
    "模型/框架/benchmark/论文/纯 API/纯 SDK/纯 release 类硬技术前言，必须能同时回答两个问题才算合格：\n"
    "1. 这事和普通用户有什么关系？\n"
    "2. 大众关心吗？为什么关心？\n"
    "两个都答不出 → platform=「仅X」。能答出 → platform=「X+小红书」时必须显式把答案翻译给普通人。\n\n"
    "## X+小红书\n"
    "只有这些才写小红书：\n"
    "- AI 工具、AI 产品、AI 硬件、普通用户能用的新功能\n"
    "- 视频、图片、音乐、办公、学习、创作相关能力\n"
    "- 涨价、隐私、安全、权限、封锁、下架、翻车、风险提醒\n"
    "- prompt 技巧、生图咒语、AI 工作流、AI 写作/翻译/PPT/总结模板\n"
    "- AI 评测、平替（ChatGPT vs Claude vs Gemini 之类）、涨价/免费额度变化\n"
    "- 编程/Agent/MCP 工具如果能讲清「普通人能怎么用 / 怎么避坑」，可以写小红书\n"
    "- GitHub release / 开源库更新如果涉及隐私泄露、安全漏洞、breaking change 影响下游用户、生态位变化、价格/权限变化，可以写小红书\n"
)

NEW_BLOCK = (
    "# 平台决策（2026-07-26 重构，按普通人口径）\n"
    "platform 只能是：\n"
    "- 「仅X」\n"
    "- 「X+小红书」\n\n"
    "**核心原则：两个平台都是写给普通用户的**。区别是格式和深度，不是受众。\n"
    "硬技术 / API / 论文 / 框架 本身不直接决定 platform —— 关键是能不能讲清「对你有什么用」。\n\n"
    "## 仅X\n"
    "这些只发 X，不写小红书（小红书专题不值得做或不能做）：\n"
    "- 完全无法转译成普通人能懂的视角（讲不出「对你有什么用」「为什么关心」「有什么影响」）\n"
    "- 纯圈内人事/融资/收购消息（除非对普通用户有明确影响）\n"
    "- 企业 SaaS 内部技术更新（Snowflake / Salesforce / K8s 等，普通用户无感）\n"
    "- 网络工具边界（Xray/VPN/翻墙/代理）且无任何普通用户场景词 —— 小红书安全边界\n"
    "- 时效性短到不值得做专题（一句话就讲完的事）\n\n"
    "## X+小红书\n"
    "这些可以写小红书专题（普通用户高频搜索 + 收藏价值）：\n"
    "- AI 工具、AI 产品、AI 硬件、普通用户能用的新功能\n"
    "- 视频、图片、音乐、办公、学习、创作相关能力\n"
    "- 涨价、隐私、安全、权限、封锁、下架、翻车、风险提醒\n"
    "- prompt 技巧、生图咒语、AI 工作流、AI 写作/翻译/PPT/总结模板\n"
    "- AI 评测、平替（ChatGPT vs Claude vs Gemini 之类）、涨价/免费额度变化\n"
    "- 新模型发布（Kimi 3 / GPT-5 / DeepSeek / Qwen 等）—— 消费者新闻，按普通用户视角写\n"
    "- 编程/Agent/MCP 工具如果能讲清「普通人能怎么用 / 怎么避坑」，可以写小红书\n"
    "- GitHub release / 开源库更新如果涉及隐私泄露、安全漏洞、breaking change 影响下游用户、生态位变化、价格/权限变化，可以写小红书\n\n"
    "## 硬技术判断统一标准\n"
    "每个素材（无论来源是否技术）都要问自己：\n"
    "1. 我妈妈能看懂这条推文吗？\n"
    "2. 她能立刻知道「对我有什么用 / 我要不要关心 / 我现在能做什么」吗？\n"
    "两个都答不出 → 驳回或仅X。能答出 → 写 X+小红书时必须显式把答案翻译给普通人。\n"
)


def main() -> None:
    data = json.loads(CFG.read_text(encoding="utf-8"))
    sp = data["sp"]
    if OLD_BLOCK not in sp:
        raise SystemExit("OLD_BLOCK not found — 请检查 config/tweet_generator_llm_cfg.json")
    new_sp = sp.replace(OLD_BLOCK, NEW_BLOCK, 1)
    data["sp"] = new_sp
    CFG.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Updated {CFG} (sp len {len(sp)} -> {len(new_sp)})")


if __name__ == "__main__":
    main()