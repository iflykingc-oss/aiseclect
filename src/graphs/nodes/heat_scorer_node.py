"""
AI 热度打分节点 - LLM（OpenAI 兼容）评分；支持降级
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


# 受众桶打分偏置：让大众向（NewsNow/产品/热搜）更容易进 top 12，技术向（GitHub/watchlist/论文）封顶
AUDIENCE_BIAS = {
    "general": 10.0,
    "tech": -8.0,
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

    # 读取 LLM cfg（带 env 覆盖）
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
        if any(k in text for k in ("漏洞", "cve", "泄露", "下架", "breaking change")):
            score += 8.0
        # NewsNow 中文大众向 source 加 base 分（之前 GitHub watchlist 占 80%+，
        # NewsNow 中文热榜素材被打到 0 分进不了高分池，被 LLM 看到的机会很少）
        newsnow_consumer_ids = {
            "weibo", "zhihu", "bilibili", "baidu", "douyin", "tieba",
            "thepaper", "ithome", "sspai", "producthunt", "coolapk",
        }
        extra = m.extra_data or {}
        ns_id = str(extra.get("newsnow_source") or "")
        if ns_id in newsnow_consumer_ids:
            score = max(score, 80.0)  # 提分到 80，覆盖 LLM 给普通热点的低打分
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

    def _final_score(llm_score: float, m: StandardMaterial, topic_relevant: bool = True) -> float:
        # AI 主题闸门：LLM 判定非 AI 主题时直接 0 分，不进入推文生成
        if not topic_relevant:
            return 0.0
        base = max(llm_score, _rule_score(m))
        return max(0.0, min(100.0, base + _audience_bias(m.category, m.source))) if base else 0.0

    def _fallback_score(m: StandardMaterial, topic_relevant: bool = True) -> float:
        # LLM 调用失败时按规则分保底；如果 LLM 明确说非 AI 主题，再做一次关键词兜底
        if not topic_relevant:
            text_ai = " ".join([m.title or "", m.snippet or "", m.content or ""]).lower()
            ai_signal = any(
                k in text_ai for k in (
                    "ai", "llm", "gpt", "claude", "gemini", "豆包", "通义", "kim", "文心",
                    "可灵", "即梦", "midjourney", "suno", "sora", "dall-e",
                    "prompt", "咒语", "智能", "大模型", "agent", "mcp", "cursor",
                    "openai", "anthropic", "deepmind", "huggingface", "copilot",
                    "文心", "kimi", "千问", "通义", "元宝",
                )
            ) or "watchlist" in (m.source or "") or "practical" in (m.source or "")
            if not ai_signal:
                return 0.0
        # fallback 路径（LLM 完全失败）：也做一次 AI 关键词检测，避免规则加分把娱乐/体育送进飞书
        text_all = " ".join([m.title or "", m.snippet or "", m.content or "", m.source or "", m.category or ""]).lower()
        ai_baseline = any(
            k in text_all for k in (
                "ai", "llm", "gpt", "claude", "gemini", "豆包", "通义", "kimi", "文心",
                "可灵", "即梦", "midjourney", "suno", "sora", "dall-e",
                "prompt", "咒语", "智能", "大模型", "agent", "mcp", "cursor",
                "openai", "anthropic", "deepmind", "huggingface", "copilot",
                "千问", "元宝", "chatgpt",
            )
        ) or "watchlist" in (m.source or "") or "practical" in (m.source or "") or "ainews" in (m.source or "") or "aihot" in (m.source or "")
        if not ai_baseline:
            return 0.0
        base = max(50.0, _rule_score(m))
        return max(0.0, min(100.0, base + _audience_bias(m.category, m.source)))

    # 调 LLM（失败则降级）
    try:
        model = build_chat_model(llm_cfg)
        from langchain_core.messages import SystemMessage, HumanMessage

        messages = [SystemMessage(content=cfg.get("sp", "")), HumanMessage(content=user_prompt)]
        resp = invoke_with_retry(model, messages)
        text = extract_text(resp.content)
        score_results = extract_json_array(text)
    except Exception as e:
        logger.error(f"热度打分失败，按规则分降级: {e}")
        scored = [
            ScoredMaterial(
                url=m.url,
                title=m.title,
                snippet=m.snippet,
                content=m.content,
                source=m.source,
                publish_time=m.publish_time,
                category=m.category,
                extra_data=m.extra_data,
                heat_score=_fallback_score(m),
                score_reason=f"LLM 调用失败，使用规则分: {e}",
            )
            for m in state.deduplicated_materials
        ]
        return HeatScorerOutput(
            scored_materials=scored,
            high_score_count=sum(1 for m in scored if m.heat_score >= 60),
            total_after_score=len(scored),
        )

    # 关联 url -> score
    score_map = {}
    topic_relevant_map = {}
    reason_map = {}
    for item in score_results:
        if isinstance(item, dict) and item.get("url"):
            try:
                score_map[item["url"]] = float(item.get("heat_score", 0) or 0)
            except (TypeError, ValueError):
                pass
            topic_relevant_map[item["url"]] = bool(item.get("ai_topic_relevant", True))
            reason_map[item["url"]] = str(item.get("score_reason", ""))

    scored: List[ScoredMaterial] = []
    high = 0
    non_ai_count = 0
    for m in state.deduplicated_materials:
        is_ai_topic = topic_relevant_map.get(m.url, True)
        s = _final_score(score_map.get(m.url, 0.0), m, topic_relevant=is_ai_topic)
        if s >= 60:
            high += 1
        if not is_ai_topic:
            non_ai_count += 1
        reason_text = reason_map.get(m.url, "") or ""
        if not is_ai_topic:
            reason_text = (reason_text or "LLM 判定非 AI 主题").strip()
        scored.append(
            ScoredMaterial(
                url=m.url,
                title=m.title,
                snippet=m.snippet,
                content=m.content,
                source=m.source,
                publish_time=m.publish_time,
                category=m.category,
                extra_data=m.extra_data,
                heat_score=s,
                score_reason=reason_text,
            )
        )

    logger.info(
        f"打分完成: {len(scored)} 条, 高分 {high} 条, 非AI主题DROP {non_ai_count} 条"
    )
    return HeatScorerOutput(scored_materials=scored, high_score_count=high, total_after_score=len(scored))
