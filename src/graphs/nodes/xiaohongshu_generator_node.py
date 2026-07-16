"""
小红书专用生成节点 - 基于 xiaohongshu-ai-workbench 风格优化

与 tweet_generator_node.py 的区别：
1. 强制 platform = X+小红书（所有输出都生成小红书内容）
2. 注入小红书爆款内容模板（教程型、咒语型、平替型、测评型等）
3. 增强标题公式（前3字吸睛 + 12-20字信息密度）
4. 正文结构优化（痛点→解决方案→注意事项→CTA）
5. emoji 自动装饰关键词
6. 配图提示词增强（5要素模板）

使用场景：
- 专门为小红书渠道生产内容时使用此节点
- 普通混合内容继续使用 tweet_generator_node.py
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Tuple

from jinja2 import Template

from collect_pipeline.growth_taxonomy import (
    assign_note_structure,
    assign_pillar,
    assign_title_pattern_key,
    score_xhs_dimensions,
    summarize_growth_scores,
)
from collect_pipeline.humanizer import humanize_draft
from graphs.nodes.length_validator import post_generation_check
from graphs.state import (
    ScoredMaterial,
    TweetDraft,
    TweetGeneratorInput,
    TweetGeneratorOutput,
)
from tools.llm import (
    LLMConfig,
    build_chat_model,
    extract_json_array,
    extract_text,
    invoke_with_retry,
    load_llm_cfg,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 8
PLATFORM_XHS = "X+小红书"  # 固定输出小红书内容

# Emoji 映射表（自动装饰关键词）
EMOJI_DICT = {
    # 工具/产品
    "AI": "🤖", "ChatGPT": "💬", "Claude": "🧠", "Gemini": "✨",
    "工具": "🔧", "产品": "📦", "软件": "💻", "App": "📱",
    "浏览器": "🌐", "搜索": "🔍", "助手": "🤝",
    # 价格/成本
    "涨价": "💸", "免费": "🆓", "付费": "💰", "订阅": "📋",
    "省钱": "💵", "额度": "📊", "成本": "💲",
    # 效果/价值
    "效率": "⚡", "提速": "🚀", "优化": "📈", "提升": "📊",
    "节省": "⏱️", "自动": "🤖", "智能": "🧠",
    # 风险/注意
    "风险": "⚠️", "注意": "❗", "避坑": "🚨", "安全": "🔒",
    "隐私": "🔐", "泄露": "⛔", "封禁": "🚫",
    # 适用场景/人群
    "办公": "💼", "学习": "📚", "创作": "✍️", "设计": "🎨",
    "写作": "📝", "翻译": "🌍", "编程": "👨‍💻", "视频": "🎬",
    "图片": "🖼️", "音乐": "🎵",
}


def _add_emojis(text: str) -> str:
    """在关键词前自动添加 emoji（每段最多2个，避免过度装饰）"""
    for keyword, emoji in EMOJI_DICT.items():
        # 只在关键词首次出现且前面没有emoji时添加
        if keyword in text and emoji not in text:
            text = text.replace(keyword, f"{emoji} {keyword}", 1)
            break  # 每次只添加1个，避免emoji过多
    return text


def _make_unique_id() -> str:
    return f"xhs_{int(time.time())}_{random.randint(1000, 9999)}"


def _material_payload(m: ScoredMaterial) -> dict:
    return {
        "url": m.url,
        "title": m.title,
        "snippet": m.snippet,
        "content": m.content or m.snippet,
        "source": m.source,
        "category": m.category,
        "heat_score": m.heat_score,
        "extra_data": m.extra_data,
    }


def _build_messages(llm_cfg: LLMConfig, cfg: dict, materials: List[ScoredMaterial]):
    from langchain_core.messages import SystemMessage, HumanMessage

    materials_data = [_material_payload(m) for m in materials]
    materials_json = json.dumps(materials_data, ensure_ascii=False, indent=2)
    user_prompt = Template(cfg.get("up", "")).render(materials_json=materials_json)
    return [
        SystemMessage(content=cfg.get("sp", "")),
        HumanMessage(content=user_prompt),
    ]


def _call_llm_once(llm_cfg: LLMConfig, cfg: dict, materials: List[ScoredMaterial]) -> List[dict]:
    if not materials:
        return []
    try:
        model = build_chat_model(llm_cfg)
        messages = _build_messages(llm_cfg, cfg, materials)
        resp = invoke_with_retry(model, messages)
        text = extract_text(resp.content)
        logger.info(f"小红书 LLM 响应前 800 字符: {text[:800]}")
        raw_list = extract_json_array(text)
        return [x for x in raw_list if isinstance(x, dict)]
    except Exception as e:
        logger.error(f"小红书 LLM 调用失败: {e}")
        return []


def _normalize_tags(raw) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip().lstrip("#") for t in raw if str(t).strip()]
    if isinstance(raw, str):
        return [t.strip().lstrip("#") for t in raw.replace("，", ",").split(",") if t.strip()]
    return []


def _quality_check_xhs(data: dict) -> Tuple[bool, str]:
    """小红书内容质量门禁"""
    title = (data.get("other_title") or "").strip()
    content = (data.get("other_content") or "").strip()
    image_prompt = (data.get("image_prompt") or "").strip()
    tags = _normalize_tags(data.get("other_tags"))

    if not title or not content:
        return False, "标题或正文为空"
    if len(title) < 8 or len(title) > 30:
        return False, f"标题 {len(title)} 字不在 8-30"

    title_banned = ("震惊", "绝了", "不看后悔", "全网疯传", "宝子", "家人们", "冲啊")
    if any(w in title for w in title_banned):
        return False, f"标题标题党: {title[:20]}"

    if len(content) < 120 or len(content) > 450:
        return False, f"正文 {len(content)} 字不在 120-450"

    if not (3 <= len(tags) <= 8):
        return False, f"标签数 {len(tags)} 不在 3-8"

    if len(image_prompt) < 30 or len(image_prompt) > 220:
        return False, f"配图提示词 {len(image_prompt)} 字不在 30-220"

    # 必须包含实用信息
    signals = [
        "适合", "影响", "可以", "建议", "注意", "风险", "用来", "帮助", "避免",
        "普通人", "打工人", "创作者", "怎么", "判断", "避坑", "教程", "步骤",
    ]
    if sum(1 for s in signals if s in content) < 2:
        return False, "正文缺少实用信息（适合谁/怎么用/如何避坑）"

    return True, "ok"


def _build_draft(mat: ScoredMaterial, data: dict, strategy: Dict[str, Any]) -> Tuple[TweetDraft | None, str]:
    """从 LLM 生成的 dict 构造小红书专用 TweetDraft"""

    # 字数预检与自动截断
    length_fixes = []
    try:
        data, length_fixes = post_generation_check(data, strict=True)
        if length_fixes:
            logger.info(f"字数自动修复: {' | '.join(length_fixes)}")
    except Exception as e:
        logger.warning(f"字数检查失败: {e}")

    # 人性化处理
    tone_note = ""
    try:
        data, tone_report = humanize_draft(data, platform="xiaohongshu", enable_rhythm=True)
        tone_note = f"ai_tone={tone_report.ai_score:.0f}"
        if tone_report.ai_cliche_hits:
            tone_note += "; hits=" + ",".join(tone_report.ai_cliche_hits[:3])
        if length_fixes:
            tone_note += f"; length_fixed={len(length_fixes)}"
    except Exception as e:
        logger.warning(f"humanizer 处理失败: {e}")

    # 强制 platform = X+小红书
    data["platform"] = PLATFORM_XHS

    # Emoji 装饰
    if data.get("other_content"):
        data["other_content"] = _add_emojis(data["other_content"])

    # 归一化字段
    data["other_tags"] = _normalize_tags(data.get("other_tags"))

    # 质量门禁
    ok, reason = _quality_check_xhs(data)
    if not ok:
        logger.debug(f"小红书内容门禁拒: {mat.title[:50]} | {reason}")
        return None, reason

    # 增长分类
    growth_taxonomy = ((strategy.get("xiaohongshu") or {}).get("growth_taxonomy") or {})
    xhs_pillar = assign_pillar(mat, growth_taxonomy)
    xhs_note_structure = assign_note_structure(xhs_pillar, growth_taxonomy)
    xhs_title_pattern_key = assign_title_pattern_key(xhs_pillar, growth_taxonomy)

    # 评分
    growth_data = {
        "tweet_content": data.get("tweet_content") or "",
        "other_title": data.get("other_title") or "",
        "other_content": data.get("other_content") or "",
        "other_tags": data.get("other_tags") or [],
        "image_prompt": data.get("image_prompt") or "",
        "xhs_pillar": xhs_pillar,
    }
    growth_scores = score_xhs_dimensions(growth_data, mat, growth_taxonomy)
    xhs_search_score = growth_scores["xhs_search_score"][0]
    xhs_save_score = growth_scores["xhs_save_score"][0]
    xhs_beginner_score = growth_scores["xhs_beginner_score"][0]
    xhs_series_score = growth_scores["xhs_series_score"][0]
    xhs_growth_notes = summarize_growth_scores(growth_scores)

    return TweetDraft(
        unique_id=data.get("unique_id") or _make_unique_id(),
        url=mat.url,
        title=data.get("title") or mat.title,
        category=data.get("category") or mat.category,
        heat_score=float(data.get("heat_score") or mat.heat_score),
        tweet_content=data.get("tweet_content") or "",
        other_title=data.get("other_title") or "",
        other_content=data.get("other_content") or "",
        other_tags=data.get("other_tags") or [],
        image_prompt=data.get("image_prompt") or "",
        platform=PLATFORM_XHS,
        content_angle=str(data.get("content_angle") or "tool_use_case"),
        hook_type=str(data.get("hook_type") or "实用技巧"),
        platform_reason="小红书专用渠道",
        x_quality_score=0.0,  # 不评分 X 内容
        xhs_quality_score=100.0,  # 通过门禁即满分
        quality_notes=f"{tone_note} | growth={xhs_pillar}/{xhs_title_pattern_key}: {xhs_growth_notes}",
        xhs_pillar=xhs_pillar,
        xhs_note_structure=xhs_note_structure,
        xhs_title_pattern_key=xhs_title_pattern_key,
        xhs_search_score=xhs_search_score,
        xhs_save_score=xhs_save_score,
        xhs_beginner_score=xhs_beginner_score,
        xhs_series_score=xhs_series_score,
        xhs_growth_notes=xhs_growth_notes,
        source=mat.source,
        score_reason=mat.score_reason,
        discovery_reason="",
        status="待审核",
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    ), "ok"


def xiaohongshu_generator_node(state: TweetGeneratorInput) -> TweetGeneratorOutput:
    """小红书专用生成节点入口"""
    candidates = [m for m in state.cleaned_materials if m.heat_score >= state.min_heat_score]
    candidates = sorted(
        candidates,
        key=lambda m: (m.heat_score, m.cluster_size, len((m.content or "") + (m.snippet or ""))),
        reverse=True,
    )

    max_tweets = int(getattr(state, "max_tweets", 18) or 18)
    if max_tweets > 0:
        candidates = candidates[:max_tweets]

    if not candidates:
        logger.warning(f"无素材通过 min_heat_score={state.min_heat_score} 过滤")
        return TweetGeneratorOutput(tweet_drafts=[], total_tweets=0, other_platform_count=0)

    workspace = os.getenv("COZE_WORKSPACE_PATH", os.getcwd())
    try:
        cfg = load_llm_cfg("config/xiaohongshu_generator_llm_cfg.json", workspace_path=workspace)
    except FileNotFoundError:
        # 兜底：使用通用配置
        cfg = load_llm_cfg("config/tweet_generator_llm_cfg.json", workspace_path=workspace)

    llm_cfg = LLMConfig.from_env(default_model="gpt-4o-mini").merged(cfg.get("config", {}))

    # 加载策略
    strategy_path = os.path.join(workspace, "config/content_strategy.json")
    if os.path.isfile(strategy_path):
        with open(strategy_path, "r", encoding="utf-8") as f:
            strategy = json.load(f)
    else:
        strategy = {}

    # 批量生成
    by_url: dict = {}
    batches = [candidates[i:i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]

    for i, batch in enumerate(batches, 1):
        logger.info(f"小红书批次 {i}/{len(batches)}: 生成 {len(batch)} 条")
        parsed = _call_llm_once(llm_cfg, cfg, batch)
        for x in parsed:
            url = x.get("url", "")
            if url and url not in by_url:
                by_url[url] = x

    # 单条兜底
    unmatched = [m for m in candidates if m.url not in by_url]
    for mat in unmatched:
        single_cfg = {**cfg}
        single_cfg["up"] = (
            "【单条专项生成】只生成下面这一条素材的小红书内容，所有字段必须完整：\n\n"
            + json.dumps([_material_payload(mat)], ensure_ascii=False, indent=2)
        )
        single_parsed = _call_llm_once(llm_cfg, single_cfg, [mat])
        if single_parsed:
            by_url[mat.url] = single_parsed[0]

    # 构造草稿
    drafts: List[TweetDraft] = []
    reject_events: List[dict] = []

    for mat in candidates:
        data = by_url.get(mat.url)
        if not data:
            reject_events.append({
                "url": mat.url,
                "title": mat.title,
                "reason": "LLM 未生成",
            })
            continue

        draft, reason = _build_draft(mat, data, strategy)
        if draft is None:
            reject_events.append({
                "url": mat.url,
                "title": mat.title,
                "reason": reason,
            })
            continue

        drafts.append(draft)

    logger.info(f"小红书生成完成: {len(drafts)} 条 / 丢弃 {len(reject_events)} 条")

    return TweetGeneratorOutput(
        tweet_drafts=drafts,
        total_tweets=len(drafts),
        other_platform_count=len(drafts),  # 全部是小红书内容
        reject_events=reject_events,
    )
