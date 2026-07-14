"""临时验证脚本：统计 quality_report_*.json 的 AI 关键词命中率。

用法：
    .venv/Scripts/python.exe scripts/quality_ai_ratio.py output/quality_report_*.json

逻辑：
- 把每条草稿的 title + tweet_content + category 拼成文本
- 用 AI 关键词软命中（opus/claude/gpt/deepseek/trae/cursor/devin/manus/bolt/windsurf/...
  以及中文 豆包/通义/Kimi/元宝/智谱/月之暗面/...）
- 统计「命中数 / 总数」即 AI 占比
- 同时打印 platform、category、hook 分布
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

# 与 heat_scorer_node._AI_FALLBACK_KEYWORDS 对齐（仅 AI 公司/产品名；不包含"ai"等太宽的）
AI_KEYWORDS = (
    "gpt", "chatgpt", "claude", "sonnet", "opus", "haiku",
    "gemini", "grok", "mistral", "llama", "deepseek", "qwen", "kimi",
    "command", "cohere", "perplexity", "huggingface", "deepmind",
    "openai", "anthropic", "sensenova", "stepfun",
    "豆包", "元宝", "通义", "文心", "千问", "智谱", "月之暗面", "百川",
    "moonshot", "zhipu", "wenxin", "doubao", "yuanbao", "tongyi",
    "midjourney", "sora", "runway", "pika", "hailuo", "kling", "jimeng",
    "可灵", "即梦", "海螺", "suno", "udio", "dall-e", "dalle", "stable diffusion",
    "comfyui", "comfy ui",
    "cursor", "windsurf", "trae", "devin", "manus", "bolt", "lovable", "v0",
    "replit", "cline", "continue", "copilot", "codex", "claude code",
    "langchain", "langgraph", "llamaindex", "pydantic", "autogen",
    "prompt", "咒语", "智能体", "agent", "mcp", "rag", "向量", "向量化",
    "大模型", "agi", "世界模型", "llm", "人工智能",
    # AI 手机 / 手机 AI 功能
    "apple intelligence", "apple ai", "galaxy ai", "xiaomi ai", "小爱同学",
    "小爱", "oppo ai", "coloros ai", "vivo ai", "originos ai",
    "harmonyos ai", "鸿蒙 ai", "华为智慧助手", "pixel ai", "gemini nano",
    "copilot+", "copilot plus", "ai pc", "ai 笔记本", "ai 平板",
    "ai 眼镜", "ai 耳机", "ai 音箱", "ai 摄像头", "ai 翻译耳机",
    "ai 陪伴", "ai 学习机", "ai 拍照", "ai 修图", "ai 通话",
    "ai 摘要", "ai 翻译", "ai 助手", "ai 语音", "ai 字幕",
    "ai 实时翻译", "ai 抠图",
    # AI 软件 / 传统软件集成的 AI 功能
    "notion ai", "microsoft copilot", "office copilot", "microsoft 365 copilot",
    "adobe firefly", "firefly", "photoshop ai", "illustrator ai", "adobe sensei",
    "canva ai", "figma ai", "slack ai", "zoom ai", "zoom ai companion",
    "otter", "grammarly", "jasper", "quillbot", "motion ai", "reclaim ai", "mem ai",
    # 国产办公 AI / 企业 SaaS AI
    "钉钉 ai", "飞书 ai", "飞书智能伙伴", "企微 ai", "企业微信 ai",
    "wps ai", "腾讯文档 ai", "百度如流", "通义晓蜜", "chatppt",
    "salesforce einstein", "einstein ai", "servicenow ai", "hubspot ai",
    "zendesk ai", "atlassian rovo", "rovo ai", "duet ai",
    "google workspace ai", "gemini for workspace",
)


def _hit_ai(text: str) -> bool:
    t = (text or "").lower()
    return any(kw.lower() in t for kw in AI_KEYWORDS)


def _stats_for_report(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") or data.get("drafts") or data.get("tweets") or []
    if isinstance(items, dict):
        items = list(items.values())
    n = len(items)
    ai_n = 0
    cat_counts: Dict[str, int] = {}
    platform_counts: Dict[str, int] = {}
    hook_counts: Dict[str, int] = {}
    for it in items:
        title = it.get("title") or ""
        cat = it.get("category") or it.get("angle") or ""
        tweet = it.get("tweet_content") or it.get("x_content") or ""
        platform = it.get("platform") or ""
        hook = it.get("hook_type") or ""
        text = f"{title} {tweet} {cat}"
        if _hit_ai(text):
            ai_n += 1
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        platform_counts[platform] = platform_counts.get(platform, 0) + 1
        hook_counts[hook] = hook_counts.get(hook, 0) + 1
    return {
        "file": path.name,
        "total": n,
        "ai_n": ai_n,
        "ai_ratio": round(ai_n / n, 3) if n else 0,
        "cats": cat_counts,
        "platforms": platform_counts,
        "hooks": hook_counts,
    }


def main(argv: List[str]) -> int:
    if not argv:
        argv = ["output/quality_report_*.json"]
    paths: List[Path] = []
    for a in argv:
        p = Path(a)
        if p.is_file():
            paths.append(p)
        elif "*" in a:
            paths.extend(sorted(Path(".").glob(a)))
    if not paths:
        print("no report files matched")
        return 1
    total = 0
    ai_total = 0
    print(f"{'file':<42} {'n':>4} {'AI':>4} {'%':>6}")
    print("-" * 60)
    for p in sorted(paths):
        s = _stats_for_report(p)
        total += s["total"]
        ai_total += s["ai_n"]
        flag = "OK" if s["ai_ratio"] >= 0.7 else ("WARN" if s["ai_ratio"] >= 0.5 else "LOW")
        print(f"{s['file']:<42} {s['total']:>4} {s['ai_n']:>4} {s['ai_ratio']*100:>5.1f}%  {flag}")
    print("-" * 60)
    if total:
        print(f"{'TOTAL':<42} {total:>4} {ai_total:>4} {ai_total/total*100:>5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
