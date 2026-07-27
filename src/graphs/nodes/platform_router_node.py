"""
平台决策独立节点（2026-07-26 新增）

把 platform 判定从 tweet_generator 拆出，让 LLM 在生成推文之前先做平台审稿。
输入：cleaned_materials
输出：platform_decisions: dict[url → PlatformDecision]

决策维度（6 维评分，0-100 综合分）：
- 普通用户可读性 0-25
- 行动价值 0-20
- 收藏价值 0-15
- 时效紧迫 0-15
- 风险价格信号 0-15
- 执行难度 0-10（反向分）

综合分 ≥ 60 → X+小红书；< 60 → 仅X
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from jinja2 import Template
from langchain_core.messages import SystemMessage, HumanMessage

from graphs.state import (
    PlatformRouterInput,
    PlatformRouterOutput,
    ScoredMaterial,
)
from tools.llm import LLMConfig, build_chat_model, extract_json_array, extract_text, invoke_with_retry

logger = logging.getLogger(__name__)


@dataclass
class PlatformDecision:
    url: str
    platform: str = "X+小红书"  # "仅X" | "X+小红书"
    platform_reason: str = ""
    score: int = 60
    dimension_scores: Dict[str, int] = field(default_factory=dict)
    recommended_pillar: str = ""


def _load_router_cfg(workspace: str) -> Optional[Dict[str, Any]]:
    candidates = [
        os.path.join(workspace, "config/platform_router_llm_cfg.json"),
        os.path.join(os.getcwd(), "config/platform_router_llm_cfg.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                logger.warning(f"platform_router_llm_cfg 读取失败: {e}")
                return None
    return None


def _build_router_messages(llm_cfg: LLMConfig, cfg: Dict[str, Any], materials: List[ScoredMaterial]) -> List:
    """构造 LLM 调用消息"""
    materials_data = [
        {
            "url": m.url,
            "title": m.title,
            "snippet": m.snippet,
            "content": (m.content or m.snippet)[:600],
            "source": m.source,
            "category": m.category,
            "heat_score": m.heat_score,
        }
        for m in materials
    ]
    materials_json = json.dumps(materials_data, ensure_ascii=False, indent=2)
    user_prompt = Template(cfg.get("up", "")).render(materials_json=materials_json)
    return [
        SystemMessage(content=cfg.get("sp", "")),
        HumanMessage(content=user_prompt),
    ]


def _parse_decisions(raw: List[dict], materials: List[ScoredMaterial]) -> Dict[str, PlatformDecision]:
    """把 LLM 返回的 list[dict] 转成 url → PlatformDecision dict。无 url 匹配的丢弃。"""
    by_url: Dict[str, ScoredMaterial] = {m.url: m for m in materials}
    out: Dict[str, PlatformDecision] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url or url not in by_url:
            continue
        platform = str(item.get("platform", "")).strip()
        if platform not in ("仅X", "X+小红书"):
            platform = "X+小红书" if int(item.get("score", 0)) >= 60 else "仅X"
        out[url] = PlatformDecision(
            url=url,
            platform=platform,
            platform_reason=str(item.get("platform_reason", ""))[:200],
            score=int(item.get("score", 60)),
            dimension_scores=dict(item.get("dimension_scores") or {}),
            recommended_pillar=str(item.get("recommended_pillar", "")),
        )
    return out


def _fallback_decision(mat: ScoredMaterial) -> PlatformDecision:
    """LLM 失败时的兜底：按 tech_depth 简单判定"""
    platform = "X+小红书" if mat.tech_depth <= 60 else "仅X"
    return PlatformDecision(
        url=mat.url,
        platform=platform,
        platform_reason=f"fallback (tech_depth={mat.tech_depth})",
        score=60 if platform == "X+小红书" else 40,
        dimension_scores={},
        recommended_pillar="",
    )


def platform_router_node(state: PlatformRouterInput) -> PlatformRouterOutput:
    """平台决策独立节点。LLM 失败时按 tech_depth 兜底。"""
    workspace = os.getenv("COZE_WORKSPACE_PATH", os.getcwd())
    cfg = _load_router_cfg(workspace)
    if not cfg:
        logger.warning("platform_router_llm_cfg 缺失，按 tech_depth 兜底")
        decisions = {m.url: _fallback_decision(m) for m in state.cleaned_materials}
        return PlatformRouterOutput(platform_decisions=decisions)

    materials = list(state.cleaned_materials or [])
    if not materials:
        return PlatformRouterOutput(platform_decisions={})

    llm_cfg = LLMConfig.from_env(default_model="gpt-4o-mini").merged(cfg.get("config", {}))
    try:
        model = build_chat_model(llm_cfg)
        messages = _build_router_messages(llm_cfg, cfg, materials)
        resp = invoke_with_retry(model, messages)
        text = extract_text(resp.content)
        logger.info(f"platform_router LLM 响应前 600 字符: {text[:600]}")
        raw = extract_json_array(text)
        decisions = _parse_decisions(raw, materials)
    except Exception as e:
        logger.error(f"platform_router LLM 调用失败，按 tech_depth 兜底: {e}")
        decisions = {m.url: _fallback_decision(m) for m in materials}

    # 兜底：未返回的素材也补 fallback
    for m in materials:
        if m.url not in decisions:
            decisions[m.url] = _fallback_decision(m)

    logger.info(
        f"platform_router 决策: 总 {len(materials)} 条 / "
        f"X+小红书 {sum(1 for d in decisions.values() if d.platform == 'X+小红书')} / "
        f"仅X {sum(1 for d in decisions.values() if d.platform == '仅X')}"
    )
    return PlatformRouterOutput(platform_decisions=decisions)