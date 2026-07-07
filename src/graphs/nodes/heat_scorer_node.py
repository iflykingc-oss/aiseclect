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

    # 调 LLM（失败则降级）
    try:
        model = build_chat_model(llm_cfg)
        from langchain_core.messages import SystemMessage, HumanMessage

        messages = [SystemMessage(content=cfg.get("sp", "")), HumanMessage(content=user_prompt)]
        resp = invoke_with_retry(model, messages)
        text = extract_text(resp.content)
        score_results = extract_json_array(text)
    except Exception as e:
        logger.error(f"热度打分失败，降级为统一 50 分: {e}")
        return HeatScorerOutput(
            scored_materials=[
                ScoredMaterial(
                    url=m.url,
                    title=m.title,
                    snippet=m.snippet,
                    content=m.content,
                    source=m.source,
                    publish_time=m.publish_time,
                    category=m.category,
                    extra_data=m.extra_data,
                    heat_score=50.0,
                    score_reason=f"LLM 调用失败: {e}",
                )
                for m in state.deduplicated_materials
            ],
            high_score_count=len(state.deduplicated_materials),
        )

    # 关联 url -> score
    score_map = {}
    for item in score_results:
        if isinstance(item, dict) and item.get("url"):
            try:
                score_map[item["url"]] = float(item.get("heat_score", 0) or 0)
            except (TypeError, ValueError):
                pass

    def _rule_score(m: StandardMaterial) -> float:
        text = " ".join([m.title or "", m.snippet or "", m.content or "", m.source or "", m.category or ""]).lower()
        score = 0.0
        if "watchlist" in (m.source or ""):
            score = max(score, 58.0)
        if any(k in text for k in ("xray", "xtls", "v2ray", "vpn", "proxy", "翻墙", "科学上网", "sing-box", "clash", "mihomo")):
            score = max(score, 55.0)
        if any(k in text for k in ("退出中国", "俄罗斯", "伊朗", "漏洞", "cve", "泄露", "封锁", "下架", "breaking change")):
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

    scored: List[ScoredMaterial] = []
    high = 0
    for m in state.deduplicated_materials:
        s = max(score_map.get(m.url, 0.0), _rule_score(m))
        if s >= 60:
            high += 1
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
                score_reason=(
                    next((x.get("score_reason", "") for x in score_results if x.get("url") == m.url), "")
                    if isinstance(score_results, list)
                    else ""
                ),
            )
        )

    logger.info(f"打分完成: {len(scored)} 条, 高分 {high} 条")
    return HeatScorerOutput(scored_materials=scored, high_score_count=high, total_after_score=len(scored))
