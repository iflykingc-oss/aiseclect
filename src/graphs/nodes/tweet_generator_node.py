"""
推文生成节点 - LLM 生成 X 推文 + 小红书内容
- min_heat_score 过滤低分素材
- 按 BATCH_SIZE 分批调 LLM，避免单次输出超 token 上限被截断
- 单条兜底：仍未匹配的，每条独立调一次
- 严格 DROP：未通过 LLM 或未通过质量门禁的素材不写入飞书
- 平台分流：LLM 先决策 platform（X+小红书 / 仅X），仅X 时跳过小红书门禁

质量门禁（post-generation）：
- X 推文：3-7 行 / 必含「我」字 / 无禁用营销词 / 无 hashtag
- viewpoint：长度 25-200 字 / 不能与推文完全重复
- 小红书：仅当 platform=X+小红书 时检查（标题 ≤25 字 / 正文 100-400 字 / 3-5 标签）
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from typing import List, Optional, Tuple

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

# 禁用词（命中即拒，与 prompt 同步）
BANNED_MARKETING_WORDS = [
    "重磅", "史诗", "颠覆", "天花板", "神器", "全民必备", "彻底革新",
    "震撼", "惊艳", "赋能", "闭环", "打法", "矩阵", "心智", "调性",
    "底层逻辑", "维度", "拐点", "划时代", "现象级", "里程碑", "强强联合",
]
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
    re.compile(r"值得注意的是|不容错过|值得深思|值得收藏"),
]

# 按 source 字段自动选人设
# key: source 关键词 → value: (X 人设, 小红书人设)
SOURCE_PERSONA_MAP = {
    # 技术/论文/官方 → 技术派 + 测评派
    "ai-models": ("A 技术派", "② 测评派"),
    "ai-news": ("C 工程师派", "② 测评派"),
    "paper": ("A 技术派", "② 测评派"),
    "radar-official_ai": ("A 技术派", "② 测评派"),
    "official_ai": ("A 技术派", "② 测评派"),
    # 热度/排行 → 行业评论派 + 清单派
    "radar-hot": ("B 行业评论派", "③ 清单派"),
    "aihot-hot": ("B 行业评论派", "③ 清单派"),
    "radar-daily-brief": ("B 行业评论派", "③ 清单派"),
    "radar-daily": ("B 行业评论派", "③ 清单派"),
    "hot-topics": ("B 行业评论派", "③ 清单派"),
    # 产品/通用 → PM 派 + 种草派
    "aihot": ("D 产品经理派", "① 种草派"),
    "aihot-products": ("D 产品经理派", "① 种草派"),
    "aihot-ai-products": ("D 产品经理派", "① 种草派"),
    "aihot-ai-models": ("A 技术派", "② 测评派"),  # 细分模型
    "qbitai": ("D 产品经理派", "① 种草派"),
    "sspai": ("D 产品经理派", "① 种草派"),
    # 工具/代码 → 工程师派 + 清单派
    "github-trending": ("C 工程师派", "③ 清单派"),
    "radar-github": ("C 工程师派", "③ 清单派"),
    "radar-tool": ("C 工程师派", "② 测评派"),
    "tool": ("C 工程师派", "② 测评派"),
}


def _pick_persona(source: str) -> Tuple[str, str]:
    """按 source 关键词匹配人设；未命中给个默认。"""
    if not source:
        return ("B 行业评论派", "① 种草派")
    for key, (x_persona, xhs_persona) in SOURCE_PERSONA_MAP.items():
        if key in source:
            return (x_persona, xhs_persona)
    return ("B 行业评论派", "① 种草派")


def _make_unique_id() -> str:
    return f"tweet_{int(time.time())}_{random.randint(1000, 9999)}"


def _chunked(items: list, size: int) -> list:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _build_messages(llm_cfg: LLMConfig, cfg: dict, materials: List[ScoredMaterial], persona_assignments: dict):
    """构造 LLM 调用消息，把人设分配注入 user_prompt。"""
    from langchain_core.messages import SystemMessage, HumanMessage

    materials_data = [
        {
            "url": m.url,
            "title": m.title,
            "snippet": m.snippet,
            "content": m.content or m.snippet,
            "source": m.source,
            "category": m.category,
            "heat_score": m.heat_score,
            "_persona": persona_assignments.get(m.url, {}),
        }
        for m in materials
    ]
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


# ========== 质量门禁 ==========

def _count_lines(text: str) -> int:
    """按 \\n 切分，过滤空行，得到有效行数。"""
    if not text:
        return 0
    return len([line for line in text.split("\n") if line.strip()])


def _quality_check_x(data: dict) -> Tuple[bool, str]:
    """X 推文质量门禁。返回 (pass, reason)。"""
    tweet = (data.get("tweet_content") or "").strip()
    if not tweet:
        return False, "tweet_content 空"
    lines = _count_lines(tweet)
    # 上限放宽到 10 行：因为「内心 OS」融进推文后可能多一行
    if lines < 2 or lines > 10:
        return False, f"X 推文行数 {lines} 不在 2-10"
    if "我" not in tweet and "我们" not in tweet:
        return False, "X 推文缺「我/我们」（人称）"
    for pat in BANNED_PATTERNS:
        if pat.search(tweet):
            return False, f"X 推文命中禁用词: {pat.pattern}"
    return True, "ok"


def _quality_check_viewpoint(data: dict, tweet: str) -> Tuple[bool, str]:
    """viewpoint 质量门禁。"""
    vp = (data.get("viewpoint") or "").strip()
    if not vp:
        return False, "viewpoint 空"
    if len(vp) < 25 or len(vp) > 200:
        return False, f"viewpoint 长度 {len(vp)} 不在 25-200"
    # 不应与推文完全相同
    if vp == tweet:
        return False, "viewpoint 与推文完全相同"
    # 不应与推文高度重复（用 SequenceMatcher 计算相似度）
    import difflib
    ratio = difflib.SequenceMatcher(None, vp, tweet).ratio()
    if ratio > 0.55:
        return False, f"viewpoint 与推文相似度 {ratio:.0%} 过高"
    return True, "ok"


def _quality_check_xhs(data: dict) -> Tuple[bool, str]:
    """小红书质量门禁。"""
    title = (data.get("xiaohongshu_title") or "").strip()
    content = (data.get("xiaohongshu_content") or "").strip()
    if not title or not content:
        return False, "小红书标题/正文空"
    if len(title) > 30:
        return False, f"小红书标题 {len(title)} 字超 30"
    if len(content) < 80 or len(content) > 500:
        return False, f"小红书正文 {len(content)} 字不在 80-500"
    tags = data.get("xiaohongshu_tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
    if not (2 <= len(tags) <= 8):
        return False, f"小红书标签数 {len(tags)} 不在 2-8"
    return True, "ok"


def _normalize_platform(raw: str) -> str:
    """规范化 platform 字段。未识别值默认「X+小红书」。"""
    if not raw:
        return "X+小红书"
    s = str(raw).strip()
    # 兼容各种可能的写法
    if s in ("仅X", "仅x", "X only", "x-only", "X-only", "只发X", "只X", "onlyX"):
        return "仅X"
    return "X+小红书"


def _build_draft(mat: ScoredMaterial, data: dict) -> Optional[TweetDraft]:
    """从 LLM 生成的 dict 构造 TweetDraft。质量门禁全过才返回。

    viewpoint 字段已不再要求 LLM 输出（内心 OS 融进 tweet_content），
    但保留字段兼容旧数据；若 LLM 意外输出了也存下，不做质量门禁。
    """
    platform = _normalize_platform(data.get("platform"))

    # 质量门禁：X 推文对所有 platform 都要过
    ok_x, reason_x = _quality_check_x(data)
    if not ok_x:
        logger.debug(f"X 推文门禁拒: {mat.title[:50]} | {reason_x}")
        return None
    tweet = (data.get("tweet_content") or "").strip()

    # 小红书门禁：仅当 platform=X+小红书 时才检查
    if platform == "X+小红书":
        ok_xhs, reason_xhs = _quality_check_xhs(data)
        if not ok_xhs:
            logger.debug(f"小红书门禁拒: {mat.title[:50]} | {reason_xhs}")
            return None
        xhs_title = (data.get("xiaohongshu_title") or "").strip()
        xhs = (data.get("xiaohongshu_content") or "").strip()
        tags = data.get("xiaohongshu_tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
    else:
        # 仅X：三个小红书字段置空
        xhs_title = ""
        xhs = ""
        tags = []

    return TweetDraft(
        unique_id=data.get("unique_id") or _make_unique_id(),
        url=mat.url,
        title=data.get("title") or mat.title,
        category=data.get("category") or mat.category,
        heat_score=float(data.get("heat_score") or mat.heat_score),
        tweet_content=tweet,
        viewpoint=(data.get("viewpoint") or "").strip(),  # 已废弃字段，兼容旧数据
        xiaohongshu_title=xhs_title,
        xiaohongshu_content=xhs,
        xiaohongshu_tags=tags,
        platform=platform,
        status="待发布",
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def tweet_generator_node(state: TweetGeneratorInput) -> TweetGeneratorOutput:
    candidates = [m for m in state.cleaned_materials if m.heat_score >= state.min_heat_score]
    if not candidates:
        logger.warning(f"无素材通过 min_heat_score={state.min_heat_score} 过滤")
        return TweetGeneratorOutput(tweet_drafts=[], total_tweets=0, xiaohongshu_count=0)

    workspace = os.getenv("COZE_WORKSPACE_PATH", os.getcwd())
    try:
        cfg = load_llm_cfg("config/tweet_generator_llm_cfg.json", workspace_path=workspace)
    except FileNotFoundError:
        cfg = {"sp": "你是双平台内容创作专家。", "up": "素材: {{materials_json}}", "config": {}}

    llm_cfg = LLMConfig.from_env(default_model="gpt-4o-mini").merged(cfg.get("config", {}))

    # 按 source 字段预分配人设
    persona_assignments = {m.url: {"x": _pick_persona(m.source)[0], "xhs": _pick_persona(m.source)[1]} for m in candidates}

    # 按 BATCH_SIZE 分批调 LLM
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
            "【单条专项生成】只生成下面这一条素材，所有字段必须完整输出"
            "（unique_id/url/title/category/heat_score/tweet_content/viewpoint/xiaohongshu_title/xiaohongshu_content/xiaohongshu_tags）：\n\n"
            + json.dumps([{
                "url": mat.url,
                "title": mat.title,
                "snippet": mat.snippet,
                "content": mat.content or mat.snippet,
                "source": mat.source,
                "category": mat.category,
                "heat_score": mat.heat_score,
                "_persona": persona_assignments.get(mat.url, {}),
            }], ensure_ascii=False, indent=2)
        )
        single_parsed = _call_llm_once(llm_cfg, single_cfg, [mat], persona_assignments)
        for x in single_parsed:
            url = x.get("url", "")
            if url:
                by_url[url] = x

    # 质量门禁：未匹配的 / 不达标的全部 DROP
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

    xhs_count = sum(1 for d in drafts if d.xiaohongshu_content and d.platform == "X+小红书")
    only_x_count = sum(1 for d in drafts if d.platform == "仅X")
    logger.info(
        f"推文生成: 命中 {len(drafts)} 条 / 丢弃 {dropped_no_llm} (无LLM) + {dropped_quality} (质量门禁) "
        f"/ 小红书 {xhs_count} / 仅X {only_x_count}"
    )
    return TweetGeneratorOutput(
        tweet_drafts=drafts,
        total_tweets=len(drafts),
        xiaohongshu_count=xhs_count,
    )