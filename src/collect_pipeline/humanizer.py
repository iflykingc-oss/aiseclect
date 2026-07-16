"""本地中文去 AI 味工具。

只做轻量、可预测的文本清理：替换套话、规整标点、保留事实与 URL。
不调用 LLM，避免给定时采集链路增加成本和不确定性。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class ToneReport:
    """文本 AI 腔检测摘要。"""

    ai_cliche_hits: list[str] = field(default_factory=list)
    em_dash_count: int = 0
    avg_sentence_len: float = 0.0
    hedge_word_density: float = 0.0
    bullet_density: float = 0.0
    ai_score: float = 0.0


_URL_RE = re.compile(r"https?://\S+")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*")

AI_CLICHES = (
    "本质上", "说白了", "说穿了", "归根结底", "换句话说", "值得注意的是",
    "这意味着", "真正的核心", "才是真正的", "才是核心", "未来已来", "时代变了",
    "我们拭目以待", "未来可期", "不容错过", "值得深思", "值得收藏", "值得关注",
    "重要更新", "最新消息", "一文看懂", "简单说", "快速了解", "赋能", "闭环",
    "打法", "矩阵", "心智", "调性", "底层逻辑", "维度", "拐点",
    "震惊", "绝了", "宝子们", "家人们", "姐妹们", "冲冲冲", "必看", "神器",
    "颠覆", "重磅", "史诗级", "天花板", "yyds", "绝绝子", "太绝了",
)

HEDGE_WORDS = (
    "可能", "也许", "或许", "一定程度上", "某种程度上", "有望", "预计", "持续",
    "全面", "显著", "大幅", "明显", "进一步", "不断", "逐步",
)

_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), replacement)
    for pattern, replacement in (
        (r"本质上[，,]?", ""),
        (r"说白了[，,]?", ""),
        (r"说穿了[，,]?", ""),
        (r"归根结底[，,]?", ""),
        (r"换句话说[，,]?", ""),
        (r"值得注意的是[，,]?", "注意看："),
        (r"这意味着", "接下来可能影响"),
        (r"真正的核心", "关键点"),
        (r"才是真正的", "更像是"),
        (r"才是核心", "更关键"),
        (r"未来已来", "已经能上手"),
        (r"时代变了", "玩法在变"),
        (r"我们拭目以待", "可以继续观察"),
        (r"未来可期", "后续看落地"),
        (r"不容错过", "可以先看"),
        (r"值得深思", "值得多看一眼"),
        (r"值得收藏", "可以收藏备用"),
        (r"值得关注", "可以看"),
        (r"重要更新", "这次更新"),
        (r"最新消息", "新消息"),
        (r"一文看懂", "先看这几点"),
        (r"简单说", "直接看"),
        (r"快速了解", "先抓重点"),
        (r"赋能", "帮"),
        (r"闭环", "流程"),
        (r"打法", "做法"),
        (r"矩阵", "组合"),
        (r"心智", "认知"),
        (r"调性", "风格"),
        (r"底层逻辑", "关键原因"),
        (r"维度", "角度"),
        (r"拐点", "变化点"),
        (r"震惊[！!]?", ""),
        (r"绝了[！!]?", "不错"),
        (r"宝子们?", "朋友"),
        (r"家人们?", "大家"),
        (r"姐妹们?", "朋友"),
        (r"冲冲冲[！!]*", "可以试试"),
        (r"必看[！!]?", "推荐看"),
        (r"神器[！!]?", "好工具"),
        (r"颠覆性?", "变化大"),
        (r"重磅[！!]?", "重要"),
        (r"史诗级", "很重要"),
        (r"天花板", "很强"),
        (r"yyds", "很好"),
        (r"绝绝子", "很不错"),
        (r"太绝了", "很好"),
    )
)


def _protect_urls(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def repl(match: re.Match[str]) -> str:
        key = f"__URL_{len(protected)}__"
        protected[key] = match.group(0)
        return key

    return _URL_RE.sub(repl, text), protected


def _restore_urls(text: str, protected: dict[str, str]) -> str:
    for key, url in protected.items():
        text = text.replace(key, url)
    return text


def _count_terms(text: str, terms: Iterable[str]) -> int:
    return sum(text.count(term) for term in terms)


def _avg_sentence_len(text: str) -> float:
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    if not parts:
        return 0.0
    return sum(len(p) for p in parts) / len(parts)


def _bullet_density(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    bullet_like = sum(1 for line in lines if _BULLET_RE.search(line) or len(line) <= 8)
    return bullet_like / len(lines)


def detect_ai_tone(text: str) -> ToneReport:
    """返回文本的 AI 腔风险摘要，分数越高越模板化。"""
    text = str(text or "")
    if not text.strip():
        return ToneReport()

    hits = [term for term in AI_CLICHES if term in text]
    cliche_count = _count_terms(text, AI_CLICHES)
    em_dash_count = text.count("——") + text.count("—")
    avg_len = _avg_sentence_len(text)
    hedge_count = _count_terms(text, HEDGE_WORDS)
    density_base = max(len(text) / 100, 1.0)
    hedge_density = hedge_count / density_base
    bullet = _bullet_density(text)

    score = 0.0
    score += min(cliche_count * 16, 76)
    score += min(em_dash_count * 4, 12)
    score += min(hedge_density * 8, 16)
    score += min(bullet * 12, 12)
    if avg_len > 42:
        score += min((avg_len - 42) * 0.6, 12)

    return ToneReport(
        ai_cliche_hits=hits,
        em_dash_count=em_dash_count,
        avg_sentence_len=round(avg_len, 2),
        hedge_word_density=round(hedge_density, 2),
        bullet_density=round(bullet, 2),
        ai_score=round(min(score, 100.0), 2),
    )


def _normalize_punctuation(text: str) -> str:
    text = re.sub(r"[!！]{2,}", "！", text)
    text = re.sub(r"[?？]{2,}", "？", text)
    text = re.sub(r"[。]{2,}", "。", text)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[ \t]+([，。！？；：])", r"\1", text)
    text = re.sub(r"([，。！？；：])[ \t]+", r"\1", text)
    return text


def humanize_text(text: str, *, level: str = "soft") -> tuple[str, ToneReport]:
    """轻量去 AI 味。

    level="off" 时只检测不改写。返回的 ToneReport 基于改写后的文本。
    """
    original = str(text or "")
    if level == "off" or not original.strip():
        return original, detect_ai_tone(original)

    protected_text, urls = _protect_urls(original)
    updated = protected_text.replace("\\n", "\n")
    for pattern, replacement in _REPLACEMENTS:
        updated = pattern.sub(replacement, updated)
    updated = _normalize_punctuation(updated)
    updated = re.sub(r"[ \t]+", " ", updated)
    updated = re.sub(r" *\n *", "\n", updated).strip()
    updated = _restore_urls(updated, urls)

    return updated, detect_ai_tone(updated)


def humanize_draft(
    data: dict,
    *,
    fields: tuple[str, ...] = ("tweet_content", "other_title", "other_content"),
    level: str = "soft",
    platform: str = "general",
    enable_rhythm: bool = True,
) -> tuple[dict, ToneReport]:
    """对 LLM 草稿字段做统一去 AI 味处理。

    Args:
        data: 包含文本字段的字典
        fields: 需要处理的字段名
        level: 人性化级别 ("off" | "soft")
        platform: 目标平台 ("xiaohongshu" | "weibo" | "general")
        enable_rhythm: 是否启用节奏变化（多层人性化第一层）
    """
    updated = dict(data or {})

    # 导入节奏人性化模块
    if enable_rhythm:
        try:
            from collect_pipeline.rhythm_humanizer import humanize_rhythm
        except ImportError:
            enable_rhythm = False

    for field in fields:
        value = updated.get(field)
        if isinstance(value, str) and value.strip():
            # 第一层：套话替换 + 标点规整
            updated[field], _ = humanize_text(value, level=level)

            # 第二层：节奏变化 + 平台语气（仅对正文生效）
            if enable_rhythm and field in ("tweet_content", "other_content"):
                target_platform = platform if field == "other_content" else "general"
                updated[field], _ = humanize_rhythm(updated[field], target_platform)

    combined = "\n".join(str(updated.get(field) or "") for field in fields)
    return updated, detect_ai_tone(combined)
