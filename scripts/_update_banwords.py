"""一次性脚本：在 tweet_generator_llm_cfg.json 的 sp 末尾追加扩展 ban 词段（2026-07-26）。"""
import json
from pathlib import Path

CFG = Path("config/tweet_generator_llm_cfg.json")

OLD_BAN_LINE = "- 不编造素材外细节。\n"

NEW_BAN_BLOCK = (
    "- 不编造素材外细节。\n\n"
    "## 强 ban 词表（直接写进 prompt 而非仅靠 humanizer，2026-07-26 新增）\n"
    "以下词在 X 和 XHS 都禁用，命中一次扣 15 分，命中 3 次直接 reject：\n"
    "- 模板化开场：今天给大家分享 / 大家好 / 各位 / 朋友们 / 小伙伴们 / 宝子们 / 家人们 / 集美们\n"
    "- AI 套话：本质上 / 说白了 / 这意味着 / 综上所述 / 根据以上 / 总而言之 / 不妨 / 未然 / 一定程度 / 不可或缺 / 与此同时 / 赋能 / 重塑 / 引领 / 开启新篇章 / 值得关注的是 / 未来有望 / 一文看懂 / 全网首发\n"
    "- 标题党：重磅 / 炸裂 / 杀疯了 / 震惊 / 必看 / 不看后悔 / 速来 / AI 速递 / 今日快报 / 颠覆\n"
    "- 元评论：我看 / 我觉得 / 我认为 / 个人认为 / 笔者认为 / 不得不说 / 先记一笔 / 划重点 / 敲黑板\n"
    "- 模糊量化：很多人 / 大部分人 / 不少人 / 几乎所有人 / 众所周知 / 大家普遍认为（必须用具体数字或场景）\n"
    "- 空泛判断：非常重要 / 值得关注 / 值得收藏 / 值得一看 / 很有意义 / 非常实用 / 强烈推荐（必须接具体可执行项）\n"
    "- 翻译腔：utilize / leverage / robust / seamless / delve into / multifaceted / navigate / empower / unlock the potential\n\n"
    "如不小心生成命中词，用自然中文替换：\n"
    "- 「本质上」 → 直接陈述事实，让读者自己理解\n"
    "- 「值得关注」 → 「这 3 类人要看」「如果你…」\n"
    "- 「赋能」 → 「帮你」\n"
    "- 「综上所述」 → 直接给结论\n"
)


def main() -> None:
    data = json.loads(CFG.read_text(encoding="utf-8"))
    sp = data["sp"]
    if OLD_BAN_LINE not in sp:
        raise SystemExit("anchor line not found")
    if "强 ban 词表" in sp:
        print("already updated, skip")
        return
    sp = sp.replace(OLD_BAN_LINE, NEW_BAN_BLOCK, 1)
    data["sp"] = sp
    CFG.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"updated sp len {len(sp)}")


if __name__ == "__main__":
    main()