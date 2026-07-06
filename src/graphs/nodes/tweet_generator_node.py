"""
推文生成节点 - LLM 生成 X 内容 + 小红书内容
- min_heat_score 过滤低分素材
- 按 BATCH_SIZE 分批调 LLM，避免单次输出超 token 上限被截断
- 单条兜底：仍未匹配的，每条独立调一次
- 严格 DROP：未通过 LLM 或未通过质量门禁的素材不写入飞书
- 平台分流：所有素材生成 X；适合普通用户理解的素材额外生成小红书

质量门禁（post-generation）：
- X：2-8 行 / 无 hashtag / 无禁用营销词和 AI 套话 / 不能只剩情绪
- 小红书：仅当 platform=X+小红书 时检查标题、正文、标签、配图提示词
- 仅X：小红书字段强制置空
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
PLATFORM_GENERAL = "X+小红书"
DEFAULT_STRATEGY: Dict[str, Any] = {
    "x": {"pass_score": 75, "banned_weak_hooks": ["值得关注", "重要更新", "最新消息", "又有新消息", "一文看懂", "简单说"]},
    "xiaohongshu": {"pass_score": 80},
    "angles": {},
}

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

# 明显不适合小红书的硬技术素材：即使 LLM 写了小红书内容，也强制仅X。
HARD_ONLY_X_PATTERNS = [
    re.compile(r"\b(API|SDK|endpoint|migration|deprecation|deprecated|benchmark|CUDA|kernel|compiler)\b", re.I),
    re.compile(r"arxiv|预印本|基准|评测榜|训练技巧|微调|推理框架|端点|迁移|退役|废弃", re.I),
    re.compile(r"\b(Snowflake|Databricks|Salesforce|Kubernetes|K8s)\b", re.I),
]

SOFT_ONLY_X_PATTERNS = [
    re.compile(r"paper|论文|repo|repository|github|release|开源库|命令行|cli", re.I),
]

XHS_FRIENDLY_PATTERNS = [
    re.compile(r"工具|产品|硬件|眼镜|手机|耳机|视频|图片|图像|音乐|生成|效率|办公|创作者|打工人|隐私|安全|泄露|后门|翻车|涨价|浏览器|搜索|助手|学生|学习|教育|普通人|职场|截图|PDF", re.I),
    re.compile(r"怎么用|如何用|避坑|谁受影响|影响谁|适合谁|价格|权限|风险|教程|案例|工作流|提效", re.I),
    re.compile(r"\b(ChatGPT|Gemini|Sora|Suno|Notion|豆包|元宝|可灵|剪映)\b", re.I),
]

IMPACT_PATTERNS = [
    re.compile(r"影响|风险|机会|适合|利好|门槛|成本|避坑|注意|权限|隐私|安全|涨价|下架|封锁|泄露|翻车|普通人|打工人|创作者|开发者|企业|学生"),
    re.compile(r"上线|开放|发布|更新|限制|停止|迁移|退出|转投|支持|不再支持"),
]

# 按 source 字段自动给 prompt 提示写作视角。不是输出字段。
SOURCE_PERSONA_MAP = {
    "ai-models": ("技术产品编辑", "普通 AI 用户视角"),
    "ai-news": ("技术产品编辑", "普通 AI 用户视角"),
    "paper": ("技术解释型编辑", "仅在能转成使用价值时写小红书内容"),
    "official_ai": ("技术产品编辑", "普通 AI 用户视角"),
    "radar-hot": ("行业观察编辑", "普通 AI 用户视角"),
    "aihot-hot": ("行业观察编辑", "普通 AI 用户视角"),
    "radar-daily": ("行业观察编辑", "普通 AI 用户视角"),
    "aihot": ("产品编辑", "工具/效率视角"),
    "qbitai": ("产品编辑", "工具/效率视角"),
    "sspai": ("产品编辑", "工具/效率视角"),
    "github-trending": ("开发者工具编辑", "仅在普通人能用时写小红书内容"),
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
        "extra_data": m.extra_data,
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


def _load_strategy(workspace: str) -> Dict[str, Any]:
    candidates = [
        os.path.join(workspace, "config/content_strategy.json"),
        os.path.join(os.getcwd(), "config/content_strategy.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                logger.warning(f"content_strategy 读取失败，使用默认策略: {e}")
                break
    return DEFAULT_STRATEGY


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
    """规范化 platform 字段。未知值按是否有小红书内容保守推断。"""
    data = data or {}
    s = str(raw or "").strip().replace(" ", "")
    lower = s.lower()
    only_x_alias = {
        "仅x", "只发x", "只x", "xonly", "x-only", "onlyx", "twitteronly", "仅twitter", "只发twitter",
    }
    general_alias = {
        "x+小红书", "x+小红书内容", "x+其他平台", "x+通用", "x+通用内容", "x+通用平台", "x+other", "x+general",
    }
    if lower in only_x_alias:
        return PLATFORM_ONLY_X
    if lower in general_alias:
        return PLATFORM_GENERAL
    if not s:
        return PLATFORM_GENERAL if _has_other_content(data) else PLATFORM_ONLY_X
    logger.debug(f"未知 platform 值: {raw}，保守归为仅X")
    return PLATFORM_ONLY_X


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
    is_xhs_friendly = any(p.search(text) for p in XHS_FRIENDLY_PATTERNS)
    if any(p.search(text) for p in HARD_ONLY_X_PATTERNS):
        return True
    if any(p.search(text) for p in SOFT_ONLY_X_PATTERNS) and not is_xhs_friendly:
        return True
    # GitHub / watchlist 中没有明确普通用户场景的，默认仅X。
    if any(k in (mat.source or "") for k in ("github", "watchlist")) and not is_xhs_friendly:
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
    return False


def _infer_angle(mat: ScoredMaterial, strategy: Dict[str, Any]) -> str:
    text = _material_text(mat).lower()
    angles = strategy.get("angles") or {}
    for angle, keywords in angles.items():
        if any(str(k).lower() in text for k in keywords):
            return angle
    if any(k in text for k in ("xray", "v2ray", "xtls", "vpn", "proxy", "退出", "迁移", "维护者")):
        return "ecosystem_shift"
    if any(k in text for k in ("漏洞", "隐私", "安全", "泄露", "权限", "封锁")):
        return "risk_alert"
    if "github" in (mat.source or "").lower():
        return "developer_update"
    return "breaking_news"


def _discovery_reason(mat: ScoredMaterial) -> str:
    extra = mat.extra_data or {}
    parts: List[str] = []
    if extra.get("matched_terms"):
        parts.append("命中词: " + ", ".join(str(x) for x in extra.get("matched_terms", [])[:8]))
    if extra.get("query"):
        parts.append("query: " + str(extra.get("query")))
    if extra.get("source_count"):
        parts.append(f"{extra.get('source_count')} 个信源")
    if extra.get("source_names"):
        parts.append("来源: " + ", ".join(str(x) for x in extra.get("source_names", [])[:4]))
    if extra.get("stars"):
        parts.append(f"GitHub stars: {extra.get('stars')}")
    if extra.get("repo_path"):
        parts.append("repo: " + str(extra.get("repo_path")))
    if extra.get("points") is not None or extra.get("comments") is not None:
        parts.append(f"HN points/comments: {extra.get('points', 0)}/{extra.get('comments', 0)}")
    if extra.get("source_signal_score"):
        parts.append(f"source_signal: {extra.get('source_signal_score')}")
    return " | ".join(parts)


def _infer_hook_type(tweet: str, angle: str) -> str:
    first_line = next((line.strip() for line in (tweet or "").split("\n") if line.strip()), "")
    if any(k in first_line for k in ("不是", "不只是", "表面", "真正")):
        return "反常识"
    if any(k in first_line for k in ("风险", "隐私", "安全", "小心", "避坑", "封锁", "泄露")):
        return "风险提醒"
    if any(k in first_line for k in ("机会", "利好", "窗口", "省", "成本")):
        return "机会窗口"
    if any(k in first_line for k in ("圈", "地震", "小", "但")) or angle == "ecosystem_shift":
        return "圈层爆点" if angle != "ecosystem_shift" else "生态迁移"
    if any(k in first_line for k in ("谁", "开发者", "创作者", "打工人", "普通用户", "企业")):
        return "谁受影响"
    if angle == "cost_change":
        return "成本变化"
    return "谁受影响"


def _score_x_quality(mat: ScoredMaterial, data: dict, strategy: Dict[str, Any]) -> Tuple[float, str]:
    tweet = (data.get("tweet_content") or "").strip()
    first_line = next((line.strip() for line in tweet.split("\n") if line.strip()), "")
    score = 0.0
    notes: List[str] = []
    weak_hooks = tuple((strategy.get("x") or {}).get("banned_weak_hooks") or [])

    if first_line and not any(w in first_line for w in weak_hooks) and 10 <= len(first_line) <= 70:
        score += 22
    else:
        notes.append("首行 hook 弱")
    if _contains_core_signal(mat, tweet):
        score += 20
    else:
        notes.append("核心实体弱")
    if any(p.search(tweet) for p in IMPACT_PATTERNS):
        score += 20
    else:
        notes.append("影响/风险/机会弱")
    if 45 <= len(tweet) <= 280 and 2 <= _count_lines(tweet) <= 8:
        score += 12
    else:
        notes.append("长度/行数不佳")
    if not any(p.search(tweet) for p in BANNED_PATTERNS):
        score += 16
    else:
        notes.append("命中套话/营销词")
    if _violates_safety_boundary(mat, data):
        notes.append("网络工具边界风险")
        score -= 30
    if any(k in tweet for k in ("我看", "我的判断", "风险", "机会", "别", "但", "真正", "影响")):
        score += 10
    else:
        notes.append("判断感不足")
    return min(score, 100.0), "；".join(notes) or "ok"


def _score_xhs_quality(data: dict, strategy: Dict[str, Any]) -> Tuple[float, str]:
    title = (data.get("other_title") or "").strip()
    content = (data.get("other_content") or "").strip()
    image_prompt = (data.get("image_prompt") or "").strip()
    tags = _normalize_tags(data.get("other_tags"))
    score = 0.0
    notes: List[str] = []

    if 8 <= len(title) <= 24 and not any(w in title for w in ("震惊", "绝了", "不看后悔", "宝子", "家人们")):
        score += 25
    else:
        notes.append("标题点击/长度不足")
    if any(t in title + content for t in tags[:4]) or any(k in title + content for k in ("AI", "工具", "效率", "隐私", "避坑", "办公", "创作")):
        score += 20
    else:
        notes.append("搜索关键词弱")
    if any(k in content for k in ("普通人", "打工人", "创作者", "创业者", "学生", "开发者", "适合")):
        score += 20
    else:
        notes.append("场景/人群弱")
    if sum(1 for s in ("怎么", "可以", "建议", "注意", "风险", "避免", "判断", "避坑", "用来") if s in content) >= 2:
        score += 20
    else:
        notes.append("实用/避坑弱")
    if 120 <= len(content) <= 450 and 3 <= len(tags) <= 8:
        score += 10
    else:
        notes.append("正文或标签长度不佳")
    if 30 <= len(image_prompt) <= 220 and any(k in image_prompt for k in ("封面", "信息图", "3:4", "4:5", "小红书")):
        score += 5
    else:
        notes.append("封面提示词弱")
    return min(score, 100.0), "；".join(notes) or "ok"


def _strategy_weak_hooks(strategy: Dict[str, Any]) -> Tuple[str, ...]:
    return tuple((strategy.get("x") or {}).get("banned_weak_hooks") or DEFAULT_STRATEGY["x"]["banned_weak_hooks"])


def _violates_safety_boundary(mat: ScoredMaterial, data: dict) -> bool:
    text = "\n".join([
        _material_text(mat),
        data.get("tweet_content") or "",
        data.get("other_title") or "",
        data.get("other_content") or "",
    ]).lower()
    if not any(k in text for k in ("xray", "v2ray", "xtls", "vpn", "proxy", "代理", "翻墙", "科学上网", "节点")):
        return False
    unsafe_terms = ("节点", "机场", "订阅", "购买", "配置教程", "怎么配置", "绕过", "免翻", "免费节点", "客户端配置")
    return any(k in text for k in unsafe_terms)


def _quality_check_x(mat: ScoredMaterial, data: dict, strategy: Dict[str, Any]) -> Tuple[bool, str]:
    """X 内容质量门禁。返回 (pass, reason)。"""
    tweet = (data.get("tweet_content") or "").strip()
    if not tweet:
        return False, "tweet_content 空"
    lines = _count_lines(tweet)
    if lines < 2 or lines > 8:
        return False, f"X 内容行数 {lines} 不在 2-8"
    if len(tweet) < 45:
        return False, f"X 内容过短 ({len(tweet)} 字)"
    if len(tweet) > 280:
        return False, f"X 内容过长 ({len(tweet)} 字)"
    first_line = next((line.strip() for line in tweet.split("\n") if line.strip()), "")
    if len(first_line) < 10 or len(first_line) > 70:
        return False, f"X 首行长度 {len(first_line)} 不在 10-70"
    weak_hooks = _strategy_weak_hooks(strategy)
    if any(w in first_line for w in weak_hooks):
        return False, f"X 首行 hook 过弱: {first_line[:30]}"
    for pat in BANNED_PATTERNS:
        if pat.search(tweet):
            return False, f"X 内容命中禁用词: {pat.pattern}"
    if not _contains_core_signal(mat, tweet):
        return False, "X 内容缺少素材核心实体/事件"
    if not any(p.search(tweet) for p in IMPACT_PATTERNS):
        return False, "X 内容缺少影响/风险/机会/适合谁"
    if _violates_safety_boundary(mat, data):
        return False, "网络工具内容触及配置/节点/绕过边界"
    return True, "ok"


def _quality_check_other(data: dict) -> Tuple[bool, str]:
    """小红书内容质量门禁。"""
    title = (data.get("other_title") or "").strip()
    content = (data.get("other_content") or "").strip()
    image_prompt = (data.get("image_prompt") or "").strip()
    tags = _normalize_tags(data.get("other_tags"))
    if not title or not content:
        return False, "小红书标题/正文空"
    if len(title) < 8 or len(title) > 24:
        return False, f"小红书标题 {len(title)} 字不在 8-24"
    title_banned = ("震惊", "绝了", "不看后悔", "全网疯传", "宝子", "家人们", "冲啊")
    if any(w in title for w in title_banned):
        return False, f"小红书标题标题党/硬广: {title[:20]}"
    if len(content) < 120 or len(content) > 450:
        return False, f"小红书正文 {len(content)} 字不在 120-450"
    if not (3 <= len(tags) <= 8):
        return False, f"小红书标签数 {len(tags)} 不在 3-8"
    if len(image_prompt) < 30 or len(image_prompt) > 220:
        return False, f"配图提示词 {len(image_prompt)} 字不在 30-220"
    signals = ["适合", "影响", "可以", "建议", "注意", "风险", "用来", "帮助", "避免", "普通人", "打工人", "创作者", "创业者", "怎么", "判断", "避坑"]
    if sum(1 for s in signals if s in content) < 2:
        return False, "小红书正文缺少影响/使用/避坑信息"
    return True, "ok"


def _reject_event(mat: ScoredMaterial, stage: str, reason: str, data: dict | None = None) -> dict:
    return {
        "url": mat.url,
        "title": mat.title,
        "source": mat.source,
        "category": mat.category,
        "heat_score": mat.heat_score,
        "score_reason": mat.score_reason,
        "discovery_reason": _discovery_reason(mat),
        "stage": stage,
        "reason": reason,
        "platform": (data or {}).get("platform"),
        "content_angle": (data or {}).get("content_angle"),
        "hook_type": (data or {}).get("hook_type"),
        "tweet_preview": ((data or {}).get("tweet_content") or "")[:160],
        "xhs_title": (data or {}).get("other_title") or (data or {}).get("xiaohongshu_title"),
    }


def _build_draft(mat: ScoredMaterial, data: dict, strategy: Dict[str, Any]) -> Tuple[TweetDraft | None, str]:
    """从 LLM 生成的 dict 构造 TweetDraft。返回 (draft, reject_reason)。"""
    data = _normalize_generated_payload(data)
    if _force_only_x(mat):
        data["platform"] = PLATFORM_ONLY_X

    angle = str(data.get("content_angle") or _infer_angle(mat, strategy))
    x_quality_score, x_quality_notes = _score_x_quality(mat, data, strategy)
    x_pass_score = float((strategy.get("x") or {}).get("pass_score", 75))
    ok_x, reason_x = _quality_check_x(mat, data, strategy)
    if not ok_x or x_quality_score < x_pass_score:
        reason = reason_x if not ok_x else f"X质量分 {x_quality_score:.0f} < {x_pass_score:.0f}: {x_quality_notes}"
        logger.debug(f"X 内容门禁拒: {mat.title[:50]} | {reason}")
        return None, reason

    platform = data["platform"]
    tweet = (data.get("tweet_content") or "").strip()
    other_title = (data.get("other_title") or "").strip()
    other_content = (data.get("other_content") or "").strip()
    other_tags = _normalize_tags(data.get("other_tags"))
    image_prompt = (data.get("image_prompt") or "").strip()
    xhs_quality_score = 0.0
    xhs_quality_notes = "仅X"

    if platform == PLATFORM_GENERAL:
        xhs_quality_score, xhs_quality_notes = _score_xhs_quality({
            "other_title": other_title,
            "other_content": other_content,
            "other_tags": other_tags,
            "image_prompt": image_prompt,
        }, strategy)
        xhs_pass_score = float((strategy.get("xiaohongshu") or {}).get("pass_score", 80))
        ok_other, reason_other = _quality_check_other({
            "other_title": other_title,
            "other_content": other_content,
            "other_tags": other_tags,
            "image_prompt": image_prompt,
        })
        if not ok_other or xhs_quality_score < xhs_pass_score:
            reason = reason_other if not ok_other else f"小红书质量分 {xhs_quality_score:.0f} < {xhs_pass_score:.0f}: {xhs_quality_notes}"
            logger.debug(f"小红书内容门禁拒: {mat.title[:50]} | {reason}")
            return None, reason
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
        content_angle=angle,
        hook_type=str(data.get("hook_type") or _infer_hook_type(tweet, angle)),
        platform_reason=str(data.get("platform_reason") or ("适合小红书" if platform == PLATFORM_GENERAL else "硬技术/圈层内容，仅X更合适")),
        x_quality_score=x_quality_score,
        xhs_quality_score=xhs_quality_score,
        quality_notes=" | ".join([x_quality_notes, xhs_quality_notes]).strip(" |"),
        source=mat.source,
        score_reason=mat.score_reason,
        discovery_reason=_discovery_reason(mat),
        status="待审核",
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    ), "ok"


def tweet_generator_node(state: TweetGeneratorInput) -> TweetGeneratorOutput:
    candidates = [m for m in state.cleaned_materials if m.heat_score >= state.min_heat_score]
    candidates = sorted(
        candidates,
        key=lambda m: (m.heat_score, m.cluster_size, len((m.content or "") + (m.snippet or ""))),
        reverse=True,
    )
    max_tweets = int(os.getenv("AISECLECT_MAX_TWEETS", "12"))
    if max_tweets > 0:
        candidates = candidates[:max_tweets]
    if not candidates:
        logger.warning(f"无素材通过 min_heat_score={state.min_heat_score} 过滤")
        return TweetGeneratorOutput(tweet_drafts=[], total_tweets=0, other_platform_count=0)

    workspace = os.getenv("COZE_WORKSPACE_PATH", os.getcwd())
    try:
        cfg = load_llm_cfg("config/tweet_generator_llm_cfg.json", workspace_path=workspace)
    except FileNotFoundError:
        cfg = {"sp": "你是 AI 内容编辑。", "up": "素材: {{materials_json}}", "config": {}}

    llm_cfg = LLMConfig.from_env(default_model="gpt-4o-mini").merged(cfg.get("config", {}))
    strategy = _load_strategy(workspace)

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
            "platform 只能是 仅X 或 X+小红书。如果 platform=仅X，other_title/other_content/image_prompt 为空字符串，other_tags 为空数组。\n\n"
            + json.dumps([_material_payload(mat, persona_assignments)], ensure_ascii=False, indent=2)
        )
        single_parsed = _call_llm_once(llm_cfg, single_cfg, [mat], persona_assignments)
        for x in single_parsed:
            url = x.get("url", "")
            if url:
                by_url[url] = x

    drafts: List[TweetDraft] = []
    reject_events: List[dict] = []
    dropped_no_llm = 0
    dropped_quality = 0
    for mat in candidates:
        data = by_url.get(mat.url)
        if not data:
            dropped_no_llm += 1
            reason = "LLM 未生成素材"
            reject_events.append(_reject_event(mat, "llm_missing", reason))
            logger.warning(f"{reason}, drop: {mat.url} | {mat.title[:60]}")
            continue
        draft, reject_reason = _build_draft(mat, data, strategy)
        if draft is None:
            repair_cfg = {**cfg}
            repair_cfg["up"] = (
                "【质量修复】上一版没有通过质量门禁。请只根据素材重写这一条，重点修复 X 首行 hook、具体事实、影响/风险/机会；"
                "如果适合小红书，再写小红书；不适合就 platform=仅X。只返回 JSON 数组。\n\n"
                + json.dumps([_material_payload(mat, persona_assignments)], ensure_ascii=False, indent=2)
            )
            repaired = _call_llm_once(llm_cfg, repair_cfg, [mat], persona_assignments)
            if repaired:
                draft, repair_reason = _build_draft(mat, repaired[0], strategy)
                if draft is None:
                    reject_reason = f"首次失败: {reject_reason}; 修复后失败: {repair_reason}"
            else:
                reject_reason = f"首次失败: {reject_reason}; 修复调用无结果"
        if draft is None:
            dropped_quality += 1
            reject_events.append(_reject_event(mat, "quality_rejected", reject_reason, data))
            continue
        drafts.append(draft)

    general_count = sum(1 for d in drafts if d.platform == PLATFORM_GENERAL and d.other_content)
    only_x_count = sum(1 for d in drafts if d.platform == PLATFORM_ONLY_X)
    logger.info(
        f"内容生成: 命中 {len(drafts)} 条 / 丢弃 {dropped_no_llm} (无LLM) + {dropped_quality} (质量门禁) "
        f"/ 小红书内容 {general_count} / 仅X {only_x_count}"
    )
    return TweetGeneratorOutput(
        tweet_drafts=drafts,
        total_tweets=len(drafts),
        other_platform_count=general_count,
        reject_events=reject_events,
    )
