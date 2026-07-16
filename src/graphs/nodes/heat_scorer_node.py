"""
AI 热度打分节点 - LLM（OpenAI 兼容）评分；支持降级

设计要点：
- 单 LLM 调用返回 5 个字段：heat_score, score_reason, ai_topic_relevant, ai_relevance, ai_subtopic
- AI 主题闸门：仅检查 ai_relevance 字段（core/adjacent/peripheral 通过，none 拒绝）
- ai_topic_relevant 字段仅作辅助信息记录，不影响闸门判定
- 缺数据默认值改为 False / "none"，避免 LLM 漏标时被默认放行
- fallback 路径（LLM 完全失败）保留 AI 关键词软提示，覆盖主流 AI 公司/产品名
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from jinja2 import Template

from graphs.state import HeatScorerInput, HeatScorerOutput, ScoredMaterial, StandardMaterial
from tools.llm import (
    LLMConfig,
    build_chat_model,
    extract_json_array,
    extract_text,
    invoke_with_retry,
    load_llm_cfg,
)

logger = logging.getLogger(__name__)


# 受众桶打分偏置：tech 提分（AI/技术向），general 不再加分（避免 NewsNow 大众源挤占名额）
AUDIENCE_BIAS = {
    "general": 0.0,
    "tech": 6.0,
    "neutral": 0.0,
}
CATEGORY_TO_BUCKET = {
    "大众热搜": "general", "大众讨论": "general", "视频热搜": "general",
    "社区热议": "general", "社会新闻": "general", "科技产品": "general",
    "效率工具": "general", "产品发布": "general", "数码社区": "general",
    "开源项目": "tech", "技术突破": "tech", "开发者社区": "tech",
    "科技资讯": "tech",
}
GENERAL_SOURCE_TAGS = (
    "newsnow-weibo", "newsnow-zhihu", "newsnow-bilibili", "newsnow-baidu",
    "newsnow-douyin", "newsnow-tieba", "newsnow-thepaper", "newsnow-producthunt",
    "newsnow-ithome", "newsnow-sspai", "newsnow-coolapk",
)
TECH_SOURCE_TAGS = ("github", "watchlist", "ainews", "ai-models", "official_ai")

# fallback 路径（LLM 完全失败）使用的 AI 关键词软提示。
# 仅在 _fallback_score 内使用，不参与正常 LLM 路径。覆盖主流模型/工具/产品名。
_AI_FALLBACK_KEYWORDS = (
    # 国外模型
    "ai", "a.i.", "llm", "gpt", "chatgpt", "claude", "sonnet", "opus", "haiku",
    "gemini", "grok", "mistral", "llama", "deepseek", "qwen", "kimi",
    "command", "cohere", "perplexity", "huggingface", "deepmind",
    "openai", "anthropic", "sensenova", "stepfun", "yi", "minimax",
    # 国内模型/产品
    "豆包", "元宝", "通义", "文心", "千问", "kimi", "智谱", "月之暗面", "百川",
    "moonshot", "zhipu", "wenxin", "doubao", "yuanbao", "tongyi",
    # AI 创作
    "midjourney", "sora", "runway", "pika", "hailuo", "kling", "jimeng",
    "可灵", "即梦", "海螺", "suno", "udio", "dall-e", "dalle", "stable diffusion",
    "comfyui", "comfy ui", "sora 2",
    # AI 开发工具
    "cursor", "windsurf", "trae", "devin", "manus", "bolt", "lovable", "v0",
    "replit", "cline", "continue", "copilot", "codex", "claude code",
    "langchain", "langgraph", "llamaindex", "pydantic", "autogen",
    # AI 概念词
    "prompt", "咒语", "智能体", "agent", "mcp", "rag", "向量", "向量化",
    "大模型", "agi", "世界模型", "llm", "智能", "人工智能",
    "machine learning", "deep learning", "transformer", "diffusion",
    "embedding", "finetune", "微调", "推理", "training",
    # AI 手机 / 手机 AI 功能（重要：边缘 AI 的核心场景）
    "apple intelligence", "apple ai", "galaxy ai", "xiaomi ai",
    "小爱同学", "小爱", "oppo ai", "coloros ai", "vivo ai", "originos ai",
    "harmonyos ai", "鸿蒙 ai", "华为智慧助手", "yoyo",
    "pixel ai", "gemini nano", "copilot+", "copilot plus",
    "ai pc", "ai 笔记本", "ai 平板", "ai 眼镜", "ai 耳机", "ai 音箱",
    "ai 摄像头", "ai 翻译耳机", "ai 陪伴", "ai 玩具", "ai 学习机",
    "ai 路由器", "ai 录音", "ai 字幕", "ai 实时翻译",
    # 数码产品里的 AI 功能（即使是子功能也算 AI 主线）
    "ai 拍照", "ai 修图", "ai 通话", "ai 摘要", "ai 翻译",
    "ai 助手", "ai 语音", "ai 搜索", "ai 抠图", "ai 去水印",
    # AI 软件 / 传统软件集成的 AI 功能
    "notion ai", "microsoft copilot", "office copilot", "microsoft 365 copilot",
    "adobe firefly", "firefly", "photoshop ai", "illustrator ai",
    "adobe sensei", "canva ai", "figma ai", "slack ai", "zoom ai",
    "zoom ai companion", "otter.ai", "otter ai", "grammarly", "jasper",
    "jasper ai", "quillbot", "motion ai", "reclaim ai", "mem ai",
    # 国产办公 AI / 企业 SaaS AI
    "钉钉 ai", "飞书 ai", "飞书智能伙伴", "企微 ai", "企业微信 ai",
    "wps ai", "腾讯文档 ai", "百度如流", "通义晓蜜", "chatppt",
    "salesforce einstein", "einstein ai", "servicenow ai", "hubspot ai",
    "zendesk ai", "atlassian rovo", "rovo ai", "duet ai",
    "google workspace ai", "gemini for workspace",
    # 通用 AI 软件产品词
    "ai 助手", "ai 写作", "ai 总结", "ai 翻译软件", "ai 录音",
)


def _audience_bias(category: Optional[str], source: Optional[str]) -> float:
    """按 category 命中桶；未命中按 source 前缀判断；返回偏置分（可正可负）。"""
    bucket = CATEGORY_TO_BUCKET.get(category or "")
    if not bucket:
        src = (source or "").lower()
        if any(tag in src for tag in GENERAL_SOURCE_TAGS):
            bucket = "general"
        elif any(tag in src for tag in TECH_SOURCE_TAGS):
            bucket = "tech"
        else:
            bucket = "neutral"
    return AUDIENCE_BIAS.get(bucket, 0.0)


def heat_scorer_node(state: HeatScorerInput) -> HeatScorerOutput:
    if not state.deduplicated_materials:
        return HeatScorerOutput(scored_materials=[], high_score_count=0)

    # 读取 heat_scorer LLM cfg（带 env 覆盖）
    workspace = os.getenv("COZE_WORKSPACE_PATH", os.getcwd())
    try:
        cfg = load_llm_cfg("config/heat_scorer_llm_cfg.json", workspace_path=workspace)
    except FileNotFoundError:
        logger.warning("heat_scorer cfg 找不到，使用默认配置")
        cfg = {"sp": "你是 AI 资讯热度评估专家。", "up": "素材: {{materials_json}}", "config": {}}

    llm_cfg = LLMConfig.from_env(default_model="gpt-4o-mini").merged(cfg.get("config", {}))

    materials_data = [
        {
            "url": m.url,
            "title": m.title,
            "snippet": m.snippet,
            "content": (m.content or "")[:800],
            "source": m.source,
            "publish_time": m.publish_time,
            "category": m.category,
            "extra_data": m.extra_data,
        }
        for m in state.deduplicated_materials
    ]
    materials_json = json.dumps(materials_data, ensure_ascii=False, indent=2)

    user_prompt = Template(cfg.get("up", "")).render(materials_json=materials_json)

    def _rule_score(m: StandardMaterial) -> float:
        text = " ".join([m.title or "", m.snippet or "", m.content or "", m.source or "", m.category or ""]).lower()
        score = 0.0
        if "watchlist" in (m.source or ""):
            score = max(score, 58.0)
        # 实用向 watchlist：prompt/教程/生图咒语/AI 办公/平替
        if "practical" in (m.source or "") or "tips" in (m.source or ""):
            score = max(score, 62.0)
        # 纯 AI 来源（aihot / ainews / github / radar-daily-brief 等）兜底分：
        # 即使 LLM 没给 heat_score，这些来源本身就 AI 强相关，给个中位分保底
        ai_source_bases = (
            "aihot", "ainews", "aihot-hot", "radar-daily", "radar-",
            "ai-models", "ai-products", "official_ai",
        )
        if any((m.source or "").startswith(s) for s in ai_source_bases) or "github" in (m.source or ""):
            score = max(score, 55.0)
        if any(k in text for k in ("漏洞", "cve", "泄露", "下架", "breaking change")):
            score += 8.0
        # NewsNow 中文大众向 source 加 base 分（保持热榜素材有底分；偏置由 AUDIENCE_BIAS 拉回）
        newsnow_consumer_ids = {
            "weibo", "zhihu", "bilibili", "baidu", "douyin", "tieba",
            "thepaper", "ithome", "sspai", "producthunt", "coolapk",
        }
        extra = m.extra_data or {}
        ns_id = str(extra.get("newsnow_source") or "")
        if ns_id in newsnow_consumer_ids:
            score = max(score, 80.0)
        # hackernews / solidot / v2ex / 财经类大众度低一档
        newsnow_secondary_ids = {
            "hackernews", "v2ex", "solidot", "cls", "wallstreetcn",
            "gelonghui", "jin10", "fastbull",
        }
        if ns_id in newsnow_secondary_ids:
            score = max(score, 65.0)
        try:
            source_signal = float(extra.get("source_signal_score") or 0)
            source_weight = float(extra.get("source_weight") or 1.0)
        except (TypeError, ValueError):
            source_signal = 0.0
            source_weight = 1.0
        if source_signal:
            score = max(score, min(source_signal * source_weight, 85.0))
        if "个独立信源" in text or "source_count" in text:
            score += 5.0
        return min(score, 85.0) if score else 0.0

    def _final_score(llm_score: float, m: StandardMaterial, topic_relevant: bool = False) -> float:
        # AI 主题闸门（仅检查 ai_relevance，ai_topic_relevant 作辅助信息）
        if not topic_relevant:
            return 0.0
        base = max(llm_score, _rule_score(m))
        # 修复：无条件应用受众偏置，允许 tech 源从 0 分提升到 6 分
        return max(0.0, min(100.0, base + _audience_bias(m.category, m.source)))

    def _fallback_score(m: StandardMaterial) -> float:
        """LLM 完全失败时按规则分保底。AI 关键词软提示覆盖主流 AI 公司/产品名。"""
        text_all = " ".join([
            m.title or "", m.snippet or "", m.content or "",
            m.source or "", m.category or "",
        ]).lower()
        ai_baseline = any(
            k in text_all for k in _AI_FALLBACK_KEYWORDS
        ) or "watchlist" in (m.source or "") or "practical" in (m.source or "") \
            or "ainews" in (m.source or "") or "aihot" in (m.source or "")
        if not ai_baseline:
            return 0.0
        base = max(50.0, _rule_score(m))
        return max(0.0, min(100.0, base + _audience_bias(m.category, m.source)))

    # ===== 双路并行 LLM 调用（顺序执行避免并发风险）=====
    # 路 1：heat_scorer（评分 + ai_topic_relevant）
    score_results: list = []
    heat_call_ok = False
    heat_call_err = None
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        model = build_chat_model(llm_cfg)
        messages = [SystemMessage(content=cfg.get("sp", "")), HumanMessage(content=user_prompt)]
        resp = invoke_with_retry(model, messages)
        text = extract_text(resp.content)
        score_results = extract_json_array(text)
        heat_call_ok = True
    except Exception as e:
        heat_call_err = e
        logger.error(f"热度打分 LLM 调用失败，按规则分降级: {e}")

    # ===== 路径 1：LLM 完全失败 → fallback_score =====
    if not heat_call_ok:
        scored = [
            ScoredMaterial(
                url=m.url,
                title=m.title,
                snippet=m.snippet,
                content=m.content,
                source=m.source,
                publish_time=m.publish_time,
                category=m.category,
                extra_data={**(m.extra_data or {}), "ai_relevance": "none", "ai_subtopic": "other"},
                heat_score=_fallback_score(m),
                score_reason=f"LLM 调用失败，使用规则分: {heat_call_err}",
            )
            for m in state.deduplicated_materials
        ]
        return HeatScorerOutput(
            scored_materials=scored,
            high_score_count=sum(1 for m in scored if m.heat_score >= 60),
            total_after_score=len(scored),
        )

    # ===== 路径 2：LLM 成功 → 解析 5 字段（合并 ai_classify）=====
    score_map = {}
    heat_topic_map = {}     # url -> bool (ai_topic_relevant)
    heat_reason_map = {}    # url -> str (score_reason)
    classify_relevance_map = {}   # url -> str (core|adjacent|peripheral|none)
    classify_subtopic_map = {}     # url -> str
    for item in score_results:
        if isinstance(item, dict) and item.get("url"):
            try:
                score_map[item["url"]] = float(item.get("heat_score", 0) or 0)
            except (TypeError, ValueError):
                pass
            heat_topic_map[item["url"]] = bool(item.get("ai_topic_relevant", False))
            heat_reason_map[item["url"]] = str(item.get("score_reason", ""))
            classify_relevance_map[item["url"]] = str(item.get("ai_relevance", "none")).strip().lower()
            classify_subtopic_map[item["url"]] = str(item.get("ai_subtopic", "other")).strip().lower()

    # 检测 LLM 遗漏的 URL（输入有但响应缺失）
    input_urls = {m.url for m in state.deduplicated_materials}
    missing_urls = input_urls - set(score_map.keys())
    if missing_urls:
        logger.warning(f"LLM 遗漏 {len(missing_urls)} 个 URL（将默认拒绝）: {list(missing_urls)[:5]}")

    scored: List[ScoredMaterial] = []
    high = 0
    non_ai_count = 0
    for m in state.deduplicated_materials:
        # 严闸门：ai_relevance != "none" 即通过（ai_topic_relevant 仅作辅助信息）。
        # 缺数据默认值：ai_relevance 漏 url → "none"（严闸门拒）。
        classify_ai = classify_relevance_map.get(m.url, "none")
        heat_ai = heat_topic_map.get(m.url, False)
        is_ai = classify_ai != "none"

        s = _final_score(score_map.get(m.url, 0.0), m, topic_relevant=is_ai)
        if s >= 60:
            high += 1
        if not is_ai:
            non_ai_count += 1

        reason_text = heat_reason_map.get(m.url, "") or ""
        if not is_ai:
            reason_text = (reason_text or "ai_relevance 判定非 AI 主题").strip()
        else:
            heat_info = f"heat_ai={'T' if heat_ai else 'F'}"
            reason_text = f"{reason_text} | relevance={classify_ai}/{classify_subtopic_map.get(m.url, 'other')} | {heat_info}".strip(" |")

        extra = dict(m.extra_data or {})
        extra["ai_relevance"] = classify_ai
        extra["ai_subtopic"] = classify_subtopic_map.get(m.url, "other")

        scored.append(
            ScoredMaterial(
                url=m.url,
                title=m.title,
                snippet=m.snippet,
                content=m.content,
                source=m.source,
                publish_time=m.publish_time,
                category=m.category,
                extra_data=extra,
                heat_score=s,
                score_reason=reason_text,
            )
        )

    # ===== 质量闸门统计（不改变输出，仅记录） =====
    from graphs.nodes.quality_gate import batch_quality_gate

    gate_materials = [(m.heat_score, m.url, m.title, m.source) for m in scored if m.heat_score > 0]
    if gate_materials:
        gate_result = batch_quality_gate(gate_materials)
        logger.info(
            f"质量闸门预览: 自动通过 {gate_result['stats']['approve']} | "
            f"待审核 {gate_result['stats']['review']} | "
            f"拒绝 {gate_result['stats']['reject']}"
        )

    logger.info(
        f"打分完成: {len(scored)} 条, 高分 {high} 条, 非AI主题DROP {non_ai_count} 条"
    )
    return HeatScorerOutput(
        scored_materials=scored,
        high_score_count=high,
        total_after_score=len(scored),
    )
