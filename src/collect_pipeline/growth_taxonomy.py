"""小红书起号策略分类与评分。

把起号资料中的内容支柱、标题结构、搜索/收藏/新手友好/系列化判断
沉淀成确定性规则。不调用 LLM、不访问小红书、不做发布/评论自动化。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_GROWTH_TAXONOMY: dict[str, Any] = {
    "pillars": [
        {"key": "tutorial", "name": "教程型", "title_pattern": "教程型", "note_structure": "hook + step_list + copy_action", "weight_hint": 1.1},
        {"key": "spell", "name": "咒语型", "title_pattern": "咒语型", "note_structure": "hook + prompt_block + tweak_notes", "weight_hint": 1.1},
        {"key": "alternative", "name": "平替型", "title_pattern": "平替型", "note_structure": "hook + comparison_table + verdict", "weight_hint": 1.05},
        {"key": "review", "name": "测评型", "title_pattern": "测评型", "note_structure": "hook + 7day_log + pros_cons", "weight_hint": 1.0},
        {"key": "workflow", "name": "流程型", "title_pattern": "流程型", "note_structure": "hook + 5step_flow + tool_stack", "weight_hint": 1.05},
        {"key": "risk_alert", "name": "避坑型", "title_pattern": "避坑型", "note_structure": "hook + red_flags + safe_alt", "weight_hint": 1.15},
        {"key": "cost_change", "name": "价格型", "title_pattern": "结果型", "note_structure": "hook + price_table + decision_rule", "weight_hint": 1.05},
        {"key": "news_commentary", "name": "资讯评论型", "title_pattern": "结果型", "note_structure": "hook + what_happened + who_cares", "weight_hint": 0.95},
        {"key": "lifestyle", "name": "生活方式种草", "title_pattern": "人群型", "note_structure": "hook + scene + price + fit", "weight_hint": 0.95},
    ],
    "assignment_rules": {
        "spell_keywords": ["prompt", "咒语", "提示词", "提问", "模板", "一句话", "直接抄", "写法"],
        "tutorial_keywords": ["教程", "3 步", "5 步", "三步", "五步", "步骤", "新手", "入门", "怎么用", "如何用", "上手"],
        "alternative_keywords": ["平替", "替代", "免费", "涨价", "降价", "订阅", "对比", "vs", "哪个好"],
        "risk_alert_keywords": ["避坑", "风险", "隐私", "安全", "权限", "泄露", "下架", "翻车", "小心", "封禁", "别"],
        "cost_change_keywords": ["价格", "订阅", "涨价", "降价", "额度", "免费", "付费", "成本", "订阅攻略"],
        "workflow_keywords": ["工作流", "自动化", "一键", "流程", "Make", "Zapier", "n8n"],
        "lifestyle_keywords": ["智能眼镜", "耳机", "音箱", "陪伴", "留学", "翻译", "旅行", "头像", "写真"],
        "review_keywords": ["测评", "实测", "用了", "对比", "横评", "榜单"],
    },
    "title_patterns_by_pillar": {},
    "scoring_dimensions": {},
}

_PILLAR_RULE_MAP = {
    "spell": "spell_keywords",
    "tutorial": "tutorial_keywords",
    "alternative": "alternative_keywords",
    "risk_alert": "risk_alert_keywords",
    "cost_change": "cost_change_keywords",
    "workflow": "workflow_keywords",
    "lifestyle": "lifestyle_keywords",
    "review": "review_keywords",
}

_SEARCH_TERMS = ("AI", "工具", "教程", "技巧", "prompt", "提示词", "避坑", "平替", "测评", "效率", "免费", "新手")
_SAVE_TERMS = ("收藏", "清单", "步骤", "模板", "直接抄", "复制", "对比", "避坑", "流程", "表格", "建议")
_BEGINNER_TERMS = ("普通人", "新手", "小白", "入门", "打工人", "学生", "创作者", "怎么", "如何", "先看", "建议")
_SERIES_TERMS = ("系列", "第", "每天", "每周", "清单", "模板", "工作流", "教程", "测评", "复盘")
_JARGON_TERMS = ("benchmark", "endpoint", "CUDA", "kernel", "微调", "蒸馏", "推理框架", "编译器")


def _merge_taxonomy(raw: dict[str, Any] | None) -> dict[str, Any]:
    taxonomy = dict(DEFAULT_GROWTH_TAXONOMY)
    if raw:
        taxonomy.update({k: v for k, v in raw.items() if v is not None})
        rules = dict(DEFAULT_GROWTH_TAXONOMY.get("assignment_rules", {}))
        rules.update(raw.get("assignment_rules") or {})
        taxonomy["assignment_rules"] = rules
    return taxonomy


def load_growth_taxonomy(workspace: str | None = None) -> dict[str, Any]:
    """从 content_strategy.json 读取 xiaohongshu.growth_taxonomy。"""
    candidates = []
    if workspace:
        candidates.append(Path(workspace) / "config" / "content_strategy.json")
    candidates.append(Path(os.getcwd()) / "config" / "content_strategy.json")
    for path in candidates:
        if path.is_file():
            try:
                strategy = json.loads(path.read_text(encoding="utf-8"))
                raw = ((strategy.get("xiaohongshu") or {}).get("growth_taxonomy") or {})
                return _merge_taxonomy(raw)
            except (OSError, ValueError):
                break
    return _merge_taxonomy({})


def _text_for_material(mat: Any) -> str:
    parts = [
        getattr(mat, "title", ""),
        getattr(mat, "snippet", ""),
        getattr(mat, "content", ""),
        getattr(mat, "source", ""),
        getattr(mat, "category", ""),
        getattr(mat, "score_reason", ""),
    ]
    return " ".join(str(p or "") for p in parts).lower()


def _hit_count(text: str, keywords: list[str] | tuple[str, ...]) -> int:
    return sum(1 for k in keywords if str(k).lower() in text)


def _pillar_map(taxonomy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(p.get("key")): p for p in taxonomy.get("pillars", []) if p.get("key")}


def assign_pillar(mat: Any, taxonomy: dict[str, Any] | None = None) -> str:
    """按关键词确定内容支柱。"""
    taxonomy = _merge_taxonomy(taxonomy or {})
    rules = taxonomy.get("assignment_rules") or {}
    text = _text_for_material(mat)

    risk_hits = _hit_count(text, rules.get("risk_alert_keywords", []))
    if risk_hits and any(k in text for k in ("避坑", "别", "小心", "风险", "隐私", "权限", "泄露")):
        return "risk_alert"
    if _hit_count(text, rules.get("spell_keywords", [])):
        return "spell"
    if _hit_count(text, rules.get("tutorial_keywords", [])):
        return "tutorial"
    if _hit_count(text, rules.get("cost_change_keywords", [])):
        return "cost_change"

    pillar_meta = _pillar_map(taxonomy)
    best_key = "news_commentary"
    best_score = 0.0
    for pillar, rule_key in _PILLAR_RULE_MAP.items():
        hits = _hit_count(text, rules.get(rule_key, []))
        if not hits:
            continue
        weight_hint = float((pillar_meta.get(pillar) or {}).get("weight_hint") or 1.0)
        score = hits * weight_hint
        if score > best_score:
            best_key = pillar
            best_score = score
    return best_key


def assign_note_structure(pillar: str, taxonomy: dict[str, Any] | None = None) -> str:
    taxonomy = _merge_taxonomy(taxonomy or {})
    meta = _pillar_map(taxonomy).get(pillar) or _pillar_map(taxonomy).get("news_commentary") or {}
    return str(meta.get("note_structure") or "hook + what_happened + who_cares")


def assign_title_pattern_key(pillar: str, taxonomy: dict[str, Any] | None = None) -> str:
    taxonomy = _merge_taxonomy(taxonomy or {})
    meta = _pillar_map(taxonomy).get(pillar) or _pillar_map(taxonomy).get("news_commentary") or {}
    return str(meta.get("title_pattern") or "结果型")


def pillar_weight_overrides(pillar: str, taxonomy: dict[str, Any] | None = None) -> dict[str, float]:
    taxonomy = _merge_taxonomy(taxonomy or {})
    overrides: dict[str, float] = {}
    for dim, cfg in (taxonomy.get("scoring_dimensions") or {}).items():
        boost = (cfg or {}).get("pillar_boost") or {}
        value = boost.get(pillar, 0)
        if value:
            overrides[str(dim)] = float(value)
    return overrides


def _text_for_draft(data: dict[str, Any], mat: Any) -> str:
    tags = data.get("other_tags") or []
    if isinstance(tags, list):
        tag_text = " ".join(str(t) for t in tags)
    else:
        tag_text = str(tags)
    parts = [
        data.get("other_title", ""),
        data.get("other_content", ""),
        data.get("tweet_content", ""),
        data.get("image_prompt", ""),
        tag_text,
        _text_for_material(mat),
    ]
    return " ".join(str(p or "") for p in parts).lower()


def _score_from_hits(hits: int, *, max_hits: int = 4, base: float = 25.0) -> float:
    return min(100.0, base + (min(hits, max_hits) / max_hits) * (100 - base))


def score_xhs_dimensions(data: dict[str, Any], mat: Any, taxonomy: dict[str, Any] | None = None) -> dict[str, tuple[float, str]]:
    """返回搜索/收藏/新手/系列化四个起号维度评分。"""
    taxonomy = _merge_taxonomy(taxonomy or {})
    pillar = str(data.get("xhs_pillar") or assign_pillar(mat, taxonomy))
    text = _text_for_draft(data, mat)

    search_hits = _hit_count(text, _SEARCH_TERMS)
    save_hits = _hit_count(text, _SAVE_TERMS)
    beginner_hits = _hit_count(text, _BEGINNER_TERMS)
    jargon_hits = _hit_count(text, _JARGON_TERMS)
    series_hits = _hit_count(text, _SERIES_TERMS)

    search_score = _score_from_hits(search_hits, max_hits=5, base=20)
    save_score = _score_from_hits(save_hits, max_hits=4, base=20)
    beginner_score = max(0.0, _score_from_hits(beginner_hits, max_hits=4, base=25) - jargon_hits * 12)
    series_score = _score_from_hits(series_hits, max_hits=3, base=15)
    if pillar in {"tutorial", "spell", "workflow", "review", "risk_alert"}:
        series_score = min(100.0, series_score + 10)

    return {
        "xhs_search_score": (round(search_score, 2), f"搜索词命中 {search_hits}"),
        "xhs_save_score": (round(save_score, 2), f"收藏信号命中 {save_hits}"),
        "xhs_beginner_score": (round(beginner_score, 2), f"新手信号 {beginner_hits}; 黑话 {jargon_hits}"),
        "xhs_series_score": (round(series_score, 2), f"系列化信号命中 {series_hits}"),
    }


def summarize_growth_scores(scores: dict[str, tuple[float, str]]) -> str:
    parts = []
    for key, (score, note) in scores.items():
        label = key.replace("xhs_", "").replace("_score", "")
        parts.append(f"{label}={score:.0f}({note})")
    return "; ".join(parts)
