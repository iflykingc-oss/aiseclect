"""
推文生成节点 - LLM 生成 X 内容 + 其他平台通用内容
- min_heat_score 过滤低分素材
- 按 BATCH_SIZE 分批调 LLM，避免单次输出超 token 上限被截断
- 单条兜底：仍未匹配的，每条独立调一次
- 严格 DROP：未通过 LLM 或未通过质量门禁的素材不写入飞书
- 平台分流：所有素材生成 X；适合普通用户理解的素材额外生成通用内容

质量门禁（post-generation）：
- X：2-8 行 / 无 hashtag / 无禁用营销词和 AI 套话 / 不能只剩情绪
- 通用内容：仅当 platform=X+通用内容 时检查标题、正文、标签、配图提示词
- 仅X：其他平台字段强制置空
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from typing import List, Tuple

from jinja2 import Template

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

BATCH_SIZE = 5  # 单次 LLM 调用素材上限
PLATFORM_ONLY_X = "仅X"
PLATFORM_GENERAL = "X+通用内容"

# 禁用词（命中即拒，与 prompt 同步）
BANNED_PATTERNS = [
    re.compile(r"#\w+"),  # hashtag
    re.compile(r"重磅|史诗|颠覆|天花板|神器|全民必备|彻底革新|震撼|惊艳"),
    re.compile(r"赋能|闭环|打法|矩阵|心智|调性|底层逻辑|维度|拐点"),
    # AI 高频句式
    re.compile(r"本质上|说白了|说穿了|归根结底|换句话说|值得注意的是"),
    re.compile(r"这意味着|真正的[核心终局护城河关键]|才是真正的|才是核心"),
    re.compile(r"不得不|不能不"),
    re.compile(r"对\w+来说[，,]这"),
    re.compile(r"未来已来|时代变了|我们拭目以待|未来可期"),
    re.compile(r"预示着|揭开了[.的]*序幕|分水岭|转折点"),
    re.compile(r"我个人认为|我的判断是|让我深感|让我震撼"),
    re.compile(r"不容错过|值得深思|值得收藏"),
]

# 明显不适合泛平台的硬技术素材：即使 LLM 写了通用内容，也强制仅X。
HARD_ONLY_X_PATTERNS = [
    re.compile(r"\b(API|SDK|endpoint|migration|deprecation|deprecated|benchmark|CUDA|kernel|compiler)\b", re.I),
    re.compile(r"arxiv|预印本|基准|评测榜|训练技巧|微调|推理框架|端点|迁移|退役|废弃", re.I),
    re.compile(r"\b(Snowflake|Databricks|Salesforce|Kubernetes|K8s)\b", re.I),
]

# 论文/研究/开发者内容如果能转成普通人收益或风险，则允许生成通用内容。
SOFT_ONLY_X_PATTERNS = [
    re.compile(r"paper|论文", re.I),
]

GENERAL_FRIENDLY_PATTERNS = [
    re.compile(r"工具|产品|硬件|眼镜|手机|耳机|视频|图片|图像|音乐|生成|效率|办公|创作者|打工人|隐私|安全|泄露|后门|翻车|涨价|浏览器|搜索|助手|Agent|学生|学习|教育|普通人|职场|截图|PDF", re.I),
    re.compile(r"\b(ChatGPT|Claude|Gemini|Sora|Suno|Cursor|Notion|LLM|Agent|豆包|元宝|可灵|剪映)\b", re.I),
]

# 按 source 字段自动给 prompt 提示写作视角。不是输出字段。
SOURCE_PERSONA_MAP = {
    "ai-models": ("技术产品编辑", "普通 AI 用户视角"),
    "ai-news": ("技术产品编辑", "普通 AI 用户视角"),
    "paper": ("技术解释型编辑", "仅在能转成使用价值时写通用内容"),
    "official_ai": ("技术产品编辑", "普通 AI 用户视角"),
    "radar-hot": ("行业观察编辑", "普通 AI 用户视角"),
    "aihot-hot": ("行业观察编辑", "普通 AI 用户视角"),
    "radar-daily": ("行业观察编辑", "普通 AI 用户视角"),
    "aihot": ("产品编辑", "工具/效率视角"),
    "qbitai": ("产品编辑", "工具/效率视角"),
    "sspai": ("产品编辑", "工具/效率视角"),
    "github-trending": ("开发者工具编辑", "仅在普通人能用时写通用内容"),
    "tool": ("开发者工具编辑", "工具/效率视角"),
}


def _pick_persona(source: str) -> Tuple[str, str]:
    """按 source 关键词匹配写作视角；未命中给默认。"""
    if not source:
        return ("AI 内容编辑", "普通 AI 用户视角")
    for key, (x_persona, other_persona) in SOURCE_PERSONA_MAP.items():
        if key in source:
            return (x_persona, other_persona)
    return ("AI 内容编辑", "普通 AI 用户视角")


def _make_unique_id() -> str:
    return f"tweet_{int(time.time())}_{random.randint(1000, 9999)}"


def _chunked(items: list, size: int) -> list:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _material_payload(m: ScoredMaterial, persona_assignments: dict) -> dict:
    """给 LLM 的素材 JSON；只传已有字段，不新增内部输出字段。"""
    payload = {
        "url": m.url,
        "title": m.title,
        "snippet": m.snippet,
        "content": m.content or m.snippet,
        "source": m.source,
        "category": m.category,
        "heat_score": m.heat_score,
        "_persona": persona_assignments.get(m.url, {}),
    }
    optional = {
        "publish_time": m.publish_time,
        "score_reason": m.score_reason,
        "cluster_size": m.cluster_size,
        "related_urls": m.related_urls,
    }
    payload.update({k: v for k, v in optional.items() if v})
    return payload


def _build_messages(llm_cfg: LLMConfig, cfg: dict, materials: List[ScoredMaterial], persona_assignments: dict):
    """构造 LLM 调用消息，把写作视角分配注入 user_prompt。"""
    from langchain_core.messages import SystemMessage, HumanMessage

    materials_data = [_material_payload(m, persona_assignments) for m in materials]
    materials_json = json.dumps(materials_data, ensure_ascii=False, indent=2)
    user_prompt = Template(cfg.get("up", "")).render(materials_json=materials_json)
    return [
        SystemMessage(content=cfg.get("sp", "")),
        HumanMessage(content=user_prompt),
    ]


def _call_llm_once(llm_cfg: LLMConfig, cfg: dict, materials: List[ScoredMaterial], persona_assignments: dict) -> List[dict]:
    """单次 LLM 调用。"""
    if not materials:
        return []
    try:
        model = build_chat_model(llm_cfg)
        messages = _build_messages(llm_cfg, cfg, materials, persona_assignments)
        resp = invoke_with_retry(model, messages)
        text = extract_text(resp.content)
        logger.info(f"推文 LLM 响应前 800 字符: {text[:800]}")
        raw_list = extract_json_array(text)
        return [x for x in raw_list if isinstance(x, dict)]
    except Exception as e:
        logger.error(f"推文 LLM 调用失败: {e}")
        return []


# ========== 归一化与质量门禁 ==========

def _count_lines(text: str) -> int:
    """按 \n 切分，过滤空行，得到有效行数。"""
    if not text:
        return 0
    return len([line for line in text.split("\n") if line.strip()])


def _normalize_tags(raw) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip().lstrip("#") for t in raw if str(t).strip()]
    if isinstance(raw, str):
        return [t.strip().lstrip("#") for t in raw.replace("，", ",").split(",") if t.strip()]
    return []


def _has_other_content(data: dict) -> bool:
    return bool((data.get("other_title") or "").strip() or (data.get("other_content") or "").strip())


def _normalize_platform(raw, data: dict | None = None) -> str:
    """规范化 platform 字段。未知值按是否有通用内容保守推断。"""
    data = data or {}
    s = str(raw or "").strip().replace(" ", "")
    lower = s.lower()
    only_x_alias = {
        "仅x", "只发x", "只x", "xonly", "x-only", "onlyx", "twitteronly", "仅twitter", "只发twitter",
    }
    general_alias = {
        "x+小红书", "x+其他平台", "x+通用", "x+通用内容", "x+通用平台", "x+other", "x+general",
    }
    if lower in only_x_alias:
        return PLATFORM_ONLY_X
    if lower in general_alias:
        return PLATFORM_GENERAL
    if not s:
        return PLATFORM_GENERAL if _has_other_content(data) else PLATFORM_ONLY_X
    logger.debug(f"未知 platform 值: {raw}，按字段内容推断")
    return PLATFORM_GENERAL if _has_other_content(data) else PLATFORM_ONLY_X


def _normalize_generated_payload(data: dict) -> dict:
    """兼容旧字段/别名，归一为新字段。"""
    normalized = dict(data)
    alias_map = {
        "other_title": ["other_title", "xiaohongshu_title", "xhs_title", "redbook_title", "小红书标题", "通用标题"],
        "other_content": ["other_content", "xiaohongshu_content", "xhs_content", "redbook_content", "小红书内容", "通用内容"],
        "other_tags": ["other_tags", "xiaohongshu_tags", "xhs_tags", "redbook_tags", "小红书标签", "通用标签"],
        "image_prompt": ["image_prompt", "other_image_prompt", "cover_image_prompt", "配图提示词", "通用配图提示词"],
    }
    for target, aliases in alias_map.items():
        if normalized.get(target):
            continue
        for key in aliases:
            if data.get(key):
                normalized[target] = data.get(key)
                break
    normalized["other_tags"] = _normalize_tags(normalized.get("other_tags"))
    normalized["platform"] = _normalize_platform(normalized.get("platform"), normalized)
    return normalized


def _material_text(mat: ScoredMaterial) -> str:
    return " ".join([
        mat.title or "",
        mat.snippet or "",
        mat.content or "",
        mat.source or "",
        mat.category or "",
    ])


def _force_only_x(mat: ScoredMaterial) -> bool:
    text = _material_text(mat)
    is_general_friendly = any(p.search(text) for p in GENERAL_FRIENDLY_PATTERNS)
    if any(p.search(text) for p in HARD_ONLY_X_PATTERNS) and not is_general_friendly:
        return True
    if any(p.search(text) for p in SOFT_ONLY_X_PATTERNS) and not is_general_friendly:
        return True
    # GitHub Trending 中没有明确普通用户场景的，默认仅X。
    if "github" in (mat.source or "") and not is_general_friendly:
        return True
    return False


def _contains_core_signal(mat: ScoredMaterial, tweet: str) -> bool:
    title = mat.title or ""
    title_tokens = [t for t in re.split(r"[\s｜|:：,，。/\\\-—_()（）\[\]【】]+", title) if len(t) >= 2]
    source_tokens = [t for t in re.split(r"[\s\-/_.]+", mat.source or "") if len(t) >= 3]
    tokens = title_tokens[:8] + source_tokens[:3]
    if any(t in tweet for t in tokens):
        return True
    # 中文标题常是一整句，LLM 会改写而非原样复述；用较短片段做宽松实体匹配。
    compact_title = re.sub(r"\s+", "", title)
    if len(compact_title) >= 6:
        fragments = {compact_title[i:i + 3] for i in range(0, len(compact_title) - 2)}
        if any(f in tweet for f in fragments):
            return True
    return len(tweet) >= 90


def _quality_check_x(mat: ScoredMaterial, data: dict) -> Tuple[bool, str]:
    """X 内容质量门禁。返回 (pass, reason)。"""
    tweet = (data.get("tweet_content") or "").strip()
    if not tweet:
        return False, "tweet_content 空"
    lines = _count_lines(tweet)
    if lines < 2 or lines > 8:
        return False, f"X 内容行数 {lines} 不在 2-8"
    if len(tweet) < 45:
        return False, f"X 内容过短 ({len(tweet)} 字)"
    for pat in BANNED_PATTERNS:
        if pat.search(tweet):
            return False, f"X 内容命中禁用词: {pat.pattern}"
    if not _contains_core_signal(mat, tweet):
        return False, "X 内容缺少素材核心实体/事件"
    return True, "ok"


def _quality_check_other(data: dict) -> Tuple[bool, str]:
    """其他平台通用内容质量门禁。"""
    title = (data.get("other_title") or "").strip()
    content = (data.get("other_content") or "").strip()
    image_prompt = (data.get("image_prompt") or "").strip()
    tags = _normalize_tags(data.get("other_tags"))
    if not title or not content:
        return False, "通用标题/正文空"
    if len(title) < 6 or len(title) > 30:
        return False, f"通用标题 {len(title)} 字不在 6-30"
    if len(content) < 100 or len(content) > 550:
        return False, f"通用正文 {len(content)} 字不在 100-550"
    if not (2 <= len(tags) <= 8):
        return False, f"通用标签数 {len(tags)} 不在 2-8"
    if len(image_prompt) < 30 or len(image_prompt) > 220:
        return False, f"配图提示词 {len(image_prompt)} 字不在 30-220"
    signals = ["适合", "影响", "可以", "建议", "注意", "风险", "用来", "帮助", "避免", "普通人", "打工人", "创作者", "创业者"]
    if sum(1 for s in signals if s in content) < 1:
        return False, "通用正文缺少影响/使用/避坑信息"
    return True, "ok"


def _build_draft(mat: ScoredMaterial, data: dict) -> TweetDraft | None:
    """从 LLM 生成的 dict 构造 TweetDraft。质量门禁全过才返回。"""
    data = _normalize_generated_payload(data)
    if _force_only_x(mat):
        data["platform"] = PLATFORM_ONLY_X

    ok_x, reason_x = _quality_check_x(mat, data)
    if not ok_x:
        logger.debug(f"X 内容门禁拒: {mat.title[:50]} | {reason_x}")
        return None

    platform = data["platform"]
    tweet = (data.get("tweet_content") or "").strip()
    other_title = (data.get("other_title") or "").strip()
    other_content = (data.get("other_content") or "").strip()
    other_tags = _normalize_tags(data.get("other_tags"))
    image_prompt = (data.get("image_prompt") or "").strip()

    if platform == PLATFORM_GENERAL:
        ok_other, reason_other = _quality_check_other({
            "other_title": other_title,
            "other_content": other_content,
            "other_tags": other_tags,
            "image_prompt": image_prompt,
        })
        if not ok_other:
            logger.debug(f"通用内容门禁拒: {mat.title[:50]} | {reason_other}")
            return None
    else:
        other_title = ""
        other_content = ""
        other_tags = []
        image_prompt = ""

    return TweetDraft(
        unique_id=data.get("unique_id") or _make_unique_id(),
        url=mat.url,
        title=data.get("title") or mat.title,
        category=data.get("category") or mat.category,
        heat_score=float(data.get("heat_score") or mat.heat_score),
        tweet_content=tweet,
        other_title=other_title,
        other_content=other_content,
        other_tags=other_tags,
        image_prompt=image_prompt,
        platform=platform,
        status="待审核",
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def tweet_generator_node(state: TweetGeneratorInput) -> TweetGeneratorOutput:
    candidates = [m for m in state.cleaned_materials if m.heat_score >= state.min_heat_score]
    if not candidates:
        logger.warning(f"无素材通过 min_heat_score={state.min_heat_score} 过滤")
        return TweetGeneratorOutput(tweet_drafts=[], total_tweets=0, other_platform_count=0)

    workspace = os.getenv("COZE_WORKSPACE_PATH", os.getcwd())
    try:
        cfg = load_llm_cfg("config/tweet_generator_llm_cfg.json", workspace_path=workspace)
    except FileNotFoundError:
        cfg = {"sp": "你是 AI 内容编辑。", "up": "素材: {{materials_json}}", "config": {}}

    llm_cfg = LLMConfig.from_env(default_model="gpt-4o-mini").merged(cfg.get("config", {}))

    persona_assignments = {
        m.url: {"x": _pick_persona(m.source)[0], "other": _pick_persona(m.source)[1]}
        for m in candidates
    }

    by_url: dict = {}
    batches = _chunked(candidates, BATCH_SIZE)
    for i, batch in enumerate(batches, 1):
        logger.info(f"批次 {i}/{len(batches)}: 生成 {len(batch)} 条 (累计匹配 {len(by_url)})")
        parsed = _call_llm_once(llm_cfg, cfg, batch, persona_assignments)
        for x in parsed:
            url = x.get("url", "")
            if url and url not in by_url:
                by_url[url] = x

    # 单条兜底
    unmatched = [m for m in candidates if m.url not in by_url]
    for mat in unmatched:
        single_cfg = {**cfg}
        single_cfg["up"] = (
            "【单条专项生成】只生成下面这一条素材，所有字段必须完整输出："
            "unique_id/url/title/category/heat_score/platform/tweet_content/other_title/other_content/other_tags/image_prompt。"
            "如果 platform=仅X，other_title/other_content/image_prompt 为空字符串，other_tags 为空数组。\n\n"
            + json.dumps([_material_payload(mat, persona_assignments)], ensure_ascii=False, indent=2)
        )
        single_parsed = _call_llm_once(llm_cfg, single_cfg, [mat], persona_assignments)
        for x in single_parsed:
            url = x.get("url", "")
            if url:
                by_url[url] = x

    drafts: List[TweetDraft] = []
    dropped_no_llm = 0
    dropped_quality = 0
    for mat in candidates:
        data = by_url.get(mat.url)
        if not data:
            dropped_no_llm += 1
            logger.warning(f"LLM 未生成素材, drop: {mat.url} | {mat.title[:60]}")
            continue
        draft = _build_draft(mat, data)
        if draft is None:
            dropped_quality += 1
            continue
        drafts.append(draft)

    general_count = sum(1 for d in drafts if d.platform == PLATFORM_GENERAL and d.other_content)
    only_x_count = sum(1 for d in drafts if d.platform == PLATFORM_ONLY_X)
    logger.info(
        f"内容生成: 命中 {len(drafts)} 条 / 丢弃 {dropped_no_llm} (无LLM) + {dropped_quality} (质量门禁) "
        f"/ 通用内容 {general_count} / 仅X {only_x_count}"
    )
    return TweetGeneratorOutput(
        tweet_drafts=drafts,
        total_tweets=len(drafts),
        other_platform_count=general_count,
    )
