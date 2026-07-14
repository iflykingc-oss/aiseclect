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

from collect_pipeline.growth_taxonomy import (
    assign_note_structure,
    assign_pillar,
    assign_title_pattern_key,
    score_xhs_dimensions,
    summarize_growth_scores,
)
from collect_pipeline.humanizer import humanize_draft
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
DEFAULT_IMAGE_PROMPT_RUBRIC: Dict[str, Any] = {
    "aspect": ["3:4", "4:5"],
    "required_segments": ["主体", "构图", "配色", "字体", "氛围"],
    "forbidden": ["真实公司 logo", "真实名人脸", "水印", "复杂小字", "二维码", "logo"],
    "style_options": ["扁平插画", "信息图卡片", "对比图", "生活场景摄影", "产品截图风格", "暗色高亮文字海报"],
    "min_length": 60,
    "max_length": 220,
}

# 禁用词分两层：
# - HARD_BANNED：命中即拒（hashtag、纯营销词、个人情绪化表达）
# - SOFT_BANNED：命中只扣分（AI 套话、营销词、行业黑话）—— 避免误杀有判断力的圈层内容
HARD_BANNED_PATTERNS = [
    re.compile(r"#\w+"),  # hashtag
    re.compile(r"重磅|史诗|颠覆|天花板|神器|全民必备|彻底革新|震撼|惊艳"),
    # 「我的判断」「我个人认为」从 HARD 移到 SOFT —— system prompt 鼓励使用第一人称判断口吻
    # 留下「让我深感|让我震撼」（情绪化表达）
    re.compile(r"让我深感|让我震撼"),
]

SOFT_BANNED_PATTERNS = [
    re.compile(r"赋能|闭环|打法|矩阵|心智|调性|底层逻辑|维度|拐点"),
    # AI 高频句式
    re.compile(r"本质上|说白了|说穿了|归根结底|换句话说|值得注意的是"),
    re.compile(r"这意味着|真正的[核心终局护城河关键]|才是真正的|才是核心"),
    re.compile(r"不得不|不能不"),
    re.compile(r"对\w+来说[，,]这"),
    # 第一人称判断口吻（之前在 HARD，误杀合格内容。改软扣分）
    re.compile(r"我个人认为|我的判断是"),
    re.compile(r"未来已来|时代变了|我们拭目以待|未来可期"),
    re.compile(r"预示着|揭开了[.的]*序幕|分水岭|转折点"),
    re.compile(r"不容错过|值得深思|值得收藏"),
]

# 兼容别名：旧代码和外部模块可能引用 BANNED_PATTERNS
BANNED_PATTERNS = HARD_BANNED_PATTERNS + SOFT_BANNED_PATTERNS

# 不适合小红书的硬技术素材：命中且不命中 XHS_FRIENDLY_PATTERNS 时强制仅X。
HARD_ONLY_X_PATTERNS = [
    re.compile(r"\b(API|SDK|endpoint|migration|deprecation|deprecated|benchmark|CUDA|kernel|compiler)\b", re.I),
    re.compile(r"arxiv|预印本|基准|评测榜|训练技巧|微调|推理框架|端点|迁移|退役|废弃", re.I),
    re.compile(r"\b(Snowflake|Databricks|Salesforce|Kubernetes|K8s)\b", re.I),
]

# 软性仅X：只对真正只服务学术/研究读者的素材生效。
# 注意：github / release / repo / CLI 这类词在面向开发者的工具更新里太常见，
# 不能一刀切，否则所有 GitHub 来源都被踢出小红书。
SOFT_ONLY_X_PATTERNS = [
    re.compile(r"paper|论文|开源库", re.I),
]

# 网络工具 / VPN / proxy 边界：命中这些词的素材小红书只能写公开项目动态、生态与安全，
# 不能写配置、节点、教程。
PROXY_BOUNDARY_PATTERNS = [
    re.compile(r"xray|v2ray|xtls|vless|vmess|reality|sing-box|clash|mihomo|hysteria|trojan|shadowsocks|vpn|proxy|翻墙|科学上网|GFW|审查|封锁", re.I),
]

XHS_FRIENDLY_PATTERNS = [
    re.compile(r"工具|产品|硬件|眼镜|手机|耳机|视频|图片|图像|音乐|生成|效率|办公|创作者|打工人|隐私|安全|泄露|后门|翻车|涨价|浏览器|搜索|助手|学生|学习|教育|普通人|职场|截图|PDF", re.I),
    re.compile(r"怎么用|如何用|避坑|谁受影响|影响谁|适合谁|价格|权限|风险|教程|案例|工作流|提效", re.I),
    re.compile(r"\b(ChatGPT|Claude|Gemini|Sora|Suno|Cursor|Notion|LLM|Agent|豆包|元宝|可灵|剪映)\b", re.I),
]

IMPACT_PATTERNS = [
    re.compile(r"影响|风险|机会|适合|利好|门槛|成本|避坑|注意|权限|隐私|安全|涨价|下架|封锁|泄露|翻车|普通人|打工人|创作者|开发者|企业|学生"),
    re.compile(r"上线|开放|发布|更新|限制|停止|迁移|退出|转投|支持|不再支持"),
    # 新增：实用向可执行动词 / 普通用户场景（覆盖 prompt 鼓励的「今天就能试」「普通人马上用」风格）
    re.compile(r"试试|上手|白嫖|免费|立刻|马上|今天就能|可用|对照|比较|入手|替代|学习|实测|体验|尝鲜|一键|3 步|5 步|三步|五步|配置|部署|设置"),
    re.compile(r"小白|新手|家长|学生党|宝妈|刚入门|设计|运营|销售|程序员|产品经理|管理层"),
    re.compile(r"涨|降|省|赚|补|扣|减|送|加|加量|升级|解锁|开启|关闭|取消"),
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


# ========== 受众桶分类 + 配额挑选 ==========
# 让大众向（NewsNow/AI 产品/工具）保底进 top 12，技术向（GitHub/watchlist/论文）封顶。
_GENERAL_CATEGORY_TAGS = (
    "大众", "视频", "科技产品", "效率工具", "产品发布", "社会新闻", "数码社区",
    "智能硬件", "AI硬件", "AI陪伴", "AI教育", "AI视频", "AI图片", "AI创作",
    "生活方式", "消费电子", "普通用户",
)
_TECH_CATEGORY_TAGS = ("开源项目", "技术突破", "开发者社区")
_GENERAL_SOURCE_TAGS = (
    "newsnow-weibo", "newsnow-zhihu", "newsnow-bilibili", "newsnow-baidu",
    "newsnow-douyin", "newsnow-tieba", "newsnow-thepaper", "newsnow-producthunt",
    "newsnow-ithome", "newsnow-sspai", "newsnow-coolapk",
)
_TECH_SOURCE_TAGS = ("github", "watchlist", "ainews", "ai-models", "official_ai", "paper")


def _categorize_audience(mat: ScoredMaterial) -> str:
    """把素材分到 general / tech / neutral 三桶。"""
    cat = mat.category or ""
    src = (mat.source or "").lower()
    if any(tag in cat for tag in _GENERAL_CATEGORY_TAGS):
        return "general"
    if any(tag in src for tag in _GENERAL_SOURCE_TAGS):
        return "general"
    if any(tag in cat for tag in _TECH_CATEGORY_TAGS):
        return "tech"
    if any(tag in src for tag in _TECH_SOURCE_TAGS):
        return "tech"
    return "neutral"


def _balanced_pick(candidates: list, max_tweets: int) -> list:
    """按受众桶配额挑 top N：tech 保底、general 封顶、不足从中性和其他桶补。

    改：账号定位是「AI 时代资讯 + 教程」，tech 桶（AI 模型/工具/开发者生态）
    应占大头，general 桶（NewsNow 大众热点）只能少量出现（如 hackernews 上的
    AI 争议事件、Product Hunt 上的 AI 工具发布）。
    """
    if max_tweets <= 0:
        return []
    if len(candidates) <= max_tweets:
        return list(candidates)

    tech_quota = max(1, round(max_tweets * 0.45))     # 18 → 8 tech 保底
    general_quota = max(1, round(max_tweets * 0.35))  # 18 → 6 general 封顶

    general = [m for m in candidates if _categorize_audience(m) == "general"]
    tech = [m for m in candidates if _categorize_audience(m) == "tech"]
    neutral = [m for m in candidates if _categorize_audience(m) == "neutral"]

    selected: list = []
    used_urls: set = set()

    def take(bucket: list, n: int) -> None:
        for m in bucket:
            if n <= 0 or m.url in used_urls:
                continue
            selected.append(m)
            used_urls.add(m.url)
            n -= 1

    # 1) tech 保底（不够就用 neutral 补；AI 时代资讯账号 tech 必须占大头）
    take(tech, tech_quota)
    if sum(1 for x in selected if _categorize_audience(x) == "tech") < tech_quota:
        take(neutral, tech_quota - sum(1 for x in selected if _categorize_audience(x) == "tech"))

    # 2) general 封顶（少量保留大众源里的 AI 工具发布/争议事件）
    take(general, general_quota)

    # 3) neutral 补到 max_tweets（如果还不够，按 general → tech 顺序借）
    remaining = max_tweets - len(selected)
    if remaining > 0:
        take(neutral, remaining)
        if len(selected) < max_tweets:
            take(tech, max_tweets - len(selected))
            if len(selected) < max_tweets:
                take(general, max_tweets - len(selected))

    # 4) 还没满就按原排序补
    if len(selected) < max_tweets:
        for m in candidates:
            if m.url in used_urls:
                continue
            selected.append(m)
            used_urls.add(m.url)
            if len(selected) >= max_tweets:
                break

    logger.info(
        f"配额挑选: general={sum(1 for x in selected if _categorize_audience(x) == 'general')} "
        f"tech={sum(1 for x in selected if _categorize_audience(x) == 'tech')} "
        f"neutral={sum(1 for x in selected if _categorize_audience(x) == 'neutral')} "
        f"/ 总 {len(selected)}"
    )
    return selected[:max_tweets] if max_tweets > 0 else selected


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


# 允许的 content_angle 白名单（来自 config/content_strategy.json 的 angles）。
# LLM 偶尔会输出白名单外/自由发挥值，统一归一为默认 angle 避免污染飞书 / quality_report。
_KNOWN_ANGLES = {
    "breaking_news", "risk_alert", "tool_use_case", "cost_change",
    "privacy_security", "developer_update", "ecosystem_shift", "controversy",
}
_DEFAULT_ANGLE = "tool_use_case"


def _normalize_angle(raw: str) -> str:
    s = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    return s if s in _KNOWN_ANGLES else _DEFAULT_ANGLE


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
    logger.debug(f"未知 platform 值: {raw}，按小红书内容推断")
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
    is_xhs_friendly = any(p.search(text) for p in XHS_FRIENDLY_PATTERNS)
    is_proxy_boundary = any(p.search(text) for p in PROXY_BOUNDARY_PATTERNS)

    # 硬技术关键词 + 完全无普通用户场景 → 强制仅X（恢复 is_xhs_friendly 豁免）
    if any(p.search(text) for p in HARD_ONLY_X_PATTERNS) and not is_xhs_friendly:
        return True
    # 论文/学术 → 没有明确使用价值转译，仅X
    if any(p.search(text) for p in SOFT_ONLY_X_PATTERNS) and not is_xhs_friendly:
        return True
    # GitHub / watchlist 来源 + 命中网络工具边界 + 完全无普通用户场景 → 仅X
    # 关键是：只要素材里有任何「普通用户场景词」就放行小红书
    if (
        any(k in (mat.source or "") for k in ("github", "watchlist"))
        and is_proxy_boundary
        and not is_xhs_friendly
    ):
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
    if not any(p.search(tweet) for p in SOFT_BANNED_PATTERNS):
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


def _image_prompt_rubric(strategy: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """读取封面提示词 rubric，缺省时使用保守默认值。"""
    rubric = dict(DEFAULT_IMAGE_PROMPT_RUBRIC)
    if strategy:
        configured = strategy.get("image_prompt_rubric") or {}
        rubric.update({k: v for k, v in configured.items() if v is not None})
    return rubric


def _validate_image_prompt(prompt: str, strategy: Dict[str, Any] | None = None) -> Tuple[bool, str]:
    """校验小红书封面提示词。

    只对明确不可用/有风险的内容硬拒；缺少结构细节交给评分扣分，避免短期误杀。
    """
    prompt = (prompt or "").strip()
    rubric = _image_prompt_rubric(strategy)
    max_length = int(rubric.get("max_length", 220) or 220)
    min_length = int(rubric.get("min_length", 60) or 60)
    if len(prompt) < 30:
        return False, f"提示词过短 ({len(prompt)} 字)"
    if len(prompt) > max_length:
        return False, f"提示词过长 ({len(prompt)} 字)"
    forbidden = tuple(str(x) for x in (rubric.get("forbidden") or []))
    for term in forbidden:
        if term and term.lower() in prompt.lower():
            return False, f"命中禁用元素: {term}"
    if len(prompt) < min_length:
        return True, f"提示词偏短，建议补充构图/配色/字体/氛围 ({len(prompt)} 字)"
    required = [str(x) for x in (rubric.get("required_segments") or [])]
    missing = [x for x in required if x and x not in prompt]
    if missing:
        return True, "建议补充: " + ",".join(missing[:3])
    return True, "ok"


def _score_image_prompt_quality(prompt: str, strategy: Dict[str, Any]) -> Tuple[float, str]:
    """封面提示词质量分，最高 10 分。"""
    prompt = (prompt or "").strip()
    ok, reason = _validate_image_prompt(prompt, strategy)
    if not ok:
        return 0.0, reason

    rubric = _image_prompt_rubric(strategy)
    score = 0.0
    notes: List[str] = []
    if 30 <= len(prompt) <= int(rubric.get("max_length", 220) or 220):
        score += 2
    if len(prompt) >= int(rubric.get("min_length", 60) or 60):
        score += 2
    else:
        notes.append("提示词偏短")
    if any(str(x) in prompt for x in (rubric.get("aspect") or [])):
        score += 2
    else:
        notes.append("缺少比例")
    segment_hits = sum(1 for x in (rubric.get("required_segments") or []) if str(x) in prompt)
    score += min(segment_hits, 3)
    if segment_hits < 3:
        notes.append("画面要素不足")
    style_terms = tuple(str(x) for x in (rubric.get("style_options") or [])) + ("封面", "信息图", "卡片", "插画", "海报")
    if any(term in prompt for term in style_terms):
        score += 1
    else:
        notes.append("缺少视觉风格")
    if reason != "ok":
        notes.append(reason)
    return min(score, 10.0), "；".join(notes) or "ok"


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
    consumer_terms = (
        "智能眼镜", "耳机", "音箱", "家居", "陪伴", "教育", "学习", "翻译", "视频", "图片",
        "拍照", "健康", "孩子", "家长", "出差", "生活", "普通人", "新手",
    )
    practical_terms = (
        "怎么", "可以", "建议", "注意", "风险", "避免", "判断", "避坑", "用来", "场景",
        "适用", "价格", "权限", "隐私", "上手", "省时", "省钱", "效果", "体验", "选择", "不适合",
    )
    if any(t in title + content for t in tags[:4]) or any(k in title + content for k in ("AI", "工具", "效率", "隐私", "避坑", "办公", "创作") + consumer_terms):
        score += 20
    else:
        notes.append("搜索关键词弱")
    if any(k in content for k in ("普通人", "打工人", "创作者", "创业者", "学生", "开发者", "适合") + consumer_terms):
        score += 20
    else:
        notes.append("场景/人群弱")
    if sum(1 for s in practical_terms if s in content) >= 2:
        score += 20
    else:
        notes.append("实用/避坑弱")
    if 120 <= len(content) <= 450 and 3 <= len(tags) <= 8:
        score += 5
    else:
        notes.append("正文或标签长度不佳")
    image_score, image_notes = _score_image_prompt_quality(image_prompt, strategy)
    score += image_score
    if image_score < 7:
        notes.append("封面提示词弱" if image_notes == "ok" else f"封面提示词弱: {image_notes}")
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
    """X 内容质量门禁。返回 (pass, reason)。

    阈值（适配 LLM 偶发偏长，同时不放过过弱内容）：
    - 总长 30-380 字（初版 45-280；扩到 320 还是被卡，再扩到 380）
    - 首行 6-130 字（避免 LLM 一句话超长描述）
    - 行数 2-10 行（LLM 偶尔把内容分多段）
    """
    tweet = (data.get("tweet_content") or "").strip()
    if not tweet:
        return False, "tweet_content 空"
    lines = _count_lines(tweet)
    if lines < 2 or lines > 10:
        return False, f"X 内容行数 {lines} 不在 2-10"
    if len(tweet) < 30:
        return False, f"X 内容过短 ({len(tweet)} 字)"
    if len(tweet) > 380:
        return False, f"X 内容过长 ({len(tweet)} 字)"
    first_line = next((line.strip() for line in tweet.split("\n") if line.strip()), "")
    if len(first_line) < 6 or len(first_line) > 130:
        return False, f"X 首行长度 {len(first_line)} 不在 6-130"
    weak_hooks = _strategy_weak_hooks(strategy)
    if any(w in first_line for w in weak_hooks):
        return False, f"X 首行 hook 过弱: {first_line[:30]}"
    for pat in HARD_BANNED_PATTERNS:
        if pat.search(tweet):
            return False, f"X 内容命中硬禁用词: {pat.pattern}"
    # SOFT_BANNED_PATTERNS（AI 套话、营销黑话）不直接拒，由 _score_x_quality 扣分
    if not _contains_core_signal(mat, tweet):
        return False, "X 内容缺少素材核心实体/事件"
    if not any(p.search(tweet) for p in IMPACT_PATTERNS):
        return False, "X 内容缺少影响/风险/机会/适合谁"
    if _violates_safety_boundary(mat, data):
        return False, "网络工具内容触及配置/节点/绕过边界"
    return True, "ok"


def _quality_check_other(data: dict, strategy: Dict[str, Any] | None = None) -> Tuple[bool, str]:
    """小红书内容质量门禁。"""
    title = (data.get("other_title") or "").strip()
    content = (data.get("other_content") or "").strip()
    image_prompt = (data.get("image_prompt") or "").strip()
    tags = _normalize_tags(data.get("other_tags"))
    if not title or not content:
        return False, "小红书标题/正文空"
    if len(title) < 8 or len(title) > 30:
        return False, f"小红书标题 {len(title)} 字不在 8-30"
    title_banned = ("震惊", "绝了", "不看后悔", "全网疯传", "宝子", "家人们", "冲啊")
    if any(w in title for w in title_banned):
        return False, f"小红书标题标题党/硬广: {title[:20]}"
    if len(content) < 120 or len(content) > 450:
        return False, f"小红书正文 {len(content)} 字不在 120-450"
    if not (3 <= len(tags) <= 8):
        return False, f"小红书标签数 {len(tags)} 不在 3-8"
    if len(image_prompt) < 30 or len(image_prompt) > 220:
        return False, f"配图提示词 {len(image_prompt)} 字不在 30-220"
    ok_image, reason_image = _validate_image_prompt(image_prompt, strategy)
    if not ok_image:
        return False, f"配图提示词未通过 rubric: {reason_image}"
    signals = [
        "适合", "影响", "可以", "建议", "注意", "风险", "用来", "帮助", "避免", "普通人",
        "打工人", "创作者", "创业者", "怎么", "判断", "避坑", "智能眼镜", "耳机", "音箱",
        "家居", "陪伴", "教育", "学习", "翻译", "视频", "图片", "拍照", "健康", "孩子", "家长",
        "出差", "生活", "新手", "场景", "适用", "价格", "权限", "隐私", "上手", "省时",
        "省钱", "效果", "体验", "选择", "不适合",
    ]
    if sum(1 for s in signals if s in content) < 2:
        return False, "小红书正文缺少影响/使用/避坑信息"
    return True, "ok"


# reject_kind 命名空间（出现在 reject_report_*.json 的 stage_stats 字典里）
REJECT_KIND_NO_LLM = "no_llm_output"
REJECT_KIND_X_FAIL = "x_quality_fail"
REJECT_KIND_X_SCORE_LOW = "x_quality_score_low"
REJECT_KIND_XHS_FAIL = "xhs_quality_fail"
REJECT_KIND_XHS_SCORE_LOW = "xhs_quality_score_low"
REJECT_KIND_OFF_TOPIC = "off_topic"
REJECT_KIND_UNKNOWN = "unknown"


def _classify_reject(reason: str) -> str:
    """根据 reject_reason 字符串映射到 reject_kind 命名空间。"""
    if not reason:
        return REJECT_KIND_UNKNOWN
    r = reason.strip()
    # XHS 失败路径：_build_draft 用 'xhs_failed: ' 前缀打头
    if r.startswith("xhs_failed:"):
        inner = r[len("xhs_failed:"):].strip()
        if "小红书质量分" in inner:
            return REJECT_KIND_XHS_SCORE_LOW
        return REJECT_KIND_XHS_FAIL
    # X 评分低于阈值
    if r.startswith("X质量分"):
        return REJECT_KIND_X_SCORE_LOW
    # X 硬约束失败
    return REJECT_KIND_X_FAIL


def _reject_event(mat: ScoredMaterial, reject_kind: str, reason: str, data: dict | None = None) -> dict:
    return {
        "url": mat.url,
        "title": mat.title,
        "source": mat.source,
        "category": mat.category,
        "heat_score": mat.heat_score,
        "score_reason": mat.score_reason,
        "discovery_reason": _discovery_reason(mat),
        "reject_kind": reject_kind,
        "reason": reason,
        "platform": (data or {}).get("platform"),
        "content_angle": (data or {}).get("content_angle"),
        "hook_type": (data or {}).get("hook_type"),
        "tweet_preview": ((data or {}).get("tweet_content") or "")[:160],
        "xhs_title": (data or {}).get("other_title") or (data or {}).get("xiaohongshu_title"),
    }


def _downgrade_to_only_x(data: dict) -> dict:
    """保留可用 X 内容，清空小红书字段。"""
    downgraded = dict(data)
    downgraded["platform"] = PLATFORM_ONLY_X
    downgraded["other_title"] = ""
    downgraded["other_content"] = ""
    downgraded["other_tags"] = []
    downgraded["image_prompt"] = ""
    return downgraded


def _build_draft(mat: ScoredMaterial, data: dict, strategy: Dict[str, Any]) -> Tuple[TweetDraft | None, str]:
    """从 LLM 生成的 dict 构造 TweetDraft。返回 (draft, reject_reason)。"""
    data = _normalize_generated_payload(data)
    tone_note = ""
    try:
        data, tone_report = humanize_draft(data)
        tone_note = f"ai_tone={tone_report.ai_score:.0f}"
        if tone_report.ai_cliche_hits:
            tone_note += "; hits=" + ",".join(tone_report.ai_cliche_hits[:3])
    except Exception as e:
        logger.warning(f"humanizer 处理失败，保留原文: {e}")
    if _force_only_x(mat):
        data["platform"] = PLATFORM_ONLY_X

    angle = _normalize_angle(data.get("content_angle") or _infer_angle(mat, strategy))
    growth_taxonomy = ((strategy.get("xiaohongshu") or {}).get("growth_taxonomy") or {})
    xhs_pillar = assign_pillar(mat, growth_taxonomy)
    xhs_note_structure = assign_note_structure(xhs_pillar, growth_taxonomy)
    xhs_title_pattern_key = assign_title_pattern_key(xhs_pillar, growth_taxonomy)
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
    xhs_search_score = 0.0
    xhs_save_score = 0.0
    xhs_beginner_score = 0.0
    xhs_series_score = 0.0
    xhs_growth_notes = "仅X"

    if platform == PLATFORM_GENERAL:
        xhs_quality_score, xhs_quality_notes = _score_xhs_quality({
            "other_title": other_title,
            "other_content": other_content,
            "other_tags": other_tags,
            "image_prompt": image_prompt,
        }, strategy)
        xhs_pass_score = float((strategy.get("xiaohongshu") or {}).get("pass_score", 80))
        growth_data = {
            "tweet_content": tweet,
            "other_title": other_title,
            "other_content": other_content,
            "other_tags": other_tags,
            "image_prompt": image_prompt,
            "xhs_pillar": xhs_pillar,
        }
        growth_scores = score_xhs_dimensions(growth_data, mat, growth_taxonomy)
        xhs_search_score = growth_scores["xhs_search_score"][0]
        xhs_save_score = growth_scores["xhs_save_score"][0]
        xhs_beginner_score = growth_scores["xhs_beginner_score"][0]
        xhs_series_score = growth_scores["xhs_series_score"][0]
        xhs_growth_notes = summarize_growth_scores(growth_scores)
        ok_other, reason_other = _quality_check_other({
            "other_title": other_title,
            "other_content": other_content,
            "other_tags": other_tags,
            "image_prompt": image_prompt,
        }, strategy)
        if not ok_other or xhs_quality_score < xhs_pass_score:
            reason = reason_other if not ok_other else f"小红书质量分 {xhs_quality_score:.0f} < {xhs_pass_score:.0f}: {xhs_quality_notes}"
            logger.debug(f"小红书内容门禁拒: {mat.title[:50]} | {reason}")
            return None, f"xhs_failed: {reason}"
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
        quality_notes=" | ".join([x_quality_notes, xhs_quality_notes, tone_note, f"growth={xhs_pillar}/{xhs_title_pattern_key}: {xhs_growth_notes}"]).strip(" |"),
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
    max_tweets = int(getattr(state, "max_tweets", 18) or 18)
    if max_tweets > 0:
        candidates = _balanced_pick(candidates, max_tweets)
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
            reject_events.append(_reject_event(mat, REJECT_KIND_NO_LLM, reason))
            logger.warning(f"{reason}, drop: {mat.url} | {mat.title[:60]}")
            continue
        draft, reject_reason = _build_draft(mat, data, strategy)
        if draft is None:
            repair_cfg = {**cfg}
            if reject_reason.startswith("xhs_failed:"):
                repair_cfg["up"] = (
                    "【小红书专项修复】上一版 X 内容可用，但小红书标题/正文/标签/配图提示词没有通过门禁。"
                    "请保留素材事实和 X 内容方向，重点重写小红书字段：标题 8-30 字，正文 120-450 字，"
                    "讲清普通用户/打工人/创作者/创业者谁受影响、怎么用或怎么避坑，标签 3-8 个，配图提示词 30-220 字。"
                    "只返回 JSON 数组。\n\n"
                    + json.dumps([_material_payload(mat, persona_assignments)], ensure_ascii=False, indent=2)
                )
            else:
                repair_cfg["up"] = (
                    "【质量修复】上一版没有通过质量门禁。请只根据素材重写这一条，重点修复 X 首行 hook、具体事实、影响/风险/机会；"
                    "如果适合小红书，再写小红书；不适合就 platform=仅X。只返回 JSON 数组。\n\n"
                    + json.dumps([_material_payload(mat, persona_assignments)], ensure_ascii=False, indent=2)
                )
            repaired = _call_llm_once(llm_cfg, repair_cfg, [mat], persona_assignments)
            if repaired:
                draft, repair_reason = _build_draft(mat, repaired[0], strategy)
                if draft is None and reject_reason.startswith("xhs_failed:"):
                    draft, downgrade_reason = _build_draft(mat, _downgrade_to_only_x(data), strategy)
                    if draft is None:
                        reject_reason = f"首次失败: {reject_reason}; 小红书修复后失败: {repair_reason}; 降级仅X失败: {downgrade_reason}"
                    else:
                        draft.quality_notes = f"{draft.quality_notes} | 小红书修复失败，已降级仅X: {repair_reason}"
                        draft.platform_reason = "小红书质量未达标，保留 X 草稿待审核"
                elif draft is None:
                    reject_reason = f"首次失败: {reject_reason}; 修复后失败: {repair_reason}"
            else:
                if reject_reason.startswith("xhs_failed:"):
                    draft, downgrade_reason = _build_draft(mat, _downgrade_to_only_x(data), strategy)
                    if draft is None:
                        reject_reason = f"首次失败: {reject_reason}; 小红书修复调用无结果; 降级仅X失败: {downgrade_reason}"
                    else:
                        draft.quality_notes = f"{draft.quality_notes} | 小红书修复无结果，已降级仅X"
                        draft.platform_reason = "小红书质量未达标，保留 X 草稿待审核"
                else:
                    reject_reason = f"首次失败: {reject_reason}; 修复调用无结果"
        if draft is None:
            dropped_quality += 1
            reject_events.append(_reject_event(mat, _classify_reject(reject_reason), reject_reason, data))
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
