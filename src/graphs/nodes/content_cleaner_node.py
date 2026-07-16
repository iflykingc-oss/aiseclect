"""
网页精读清洗节点
- 去 HTML 标签、广告、导航栏、页脚、版权声明
- 去 boilerplate 文本（订阅提示、分享按钮文案、Cookie 提示）
- 截断到合理长度（避免 LLM 输入过大）
- 多行空白合并成单换行
"""
from __future__ import annotations

import logging
import re
from html import unescape
from typing import List

from graphs.state import ContentCleanerInput, ContentCleanerOutput, ScoredMaterial

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 1200  # 清洗后单条素材上限（从 800 提升到 1200，保留更多上下文）
SNIPPET_MAX = 300
TITLE_MAX = 200

# boilerplate 关键词（出现则整行删）
BOILERPLATE_PATTERNS = [
    r"订阅.*?(?:邮件|newsletter|推送)",
    r"关注我们.*?(?:微信|微博|公众号)",
    r"扫码.*?二维码",
    r"Copyright\s*[©©]?\s*\d{4}.*?(?:All Rights Reserved|版权所有)",
    r"©\s*\d{4}.*?(?:All Rights Reserved|版权所有)",
    r"本站.*?(?:不承担|不承担责任)",
    r"如需.*?(?:转载|授权|联系)",
    r"点击.*?(?:关注|订阅|分享)",
    r"分享到.*?(?:微信|微博|QQ)",
    r"登录.*?(?:注册|账号)",
    r"Cookie\s*(?:政策|使用|提示)",
    r"广告\s*合作.*?联系",
    r"免责声明",
    r"Previous\s*Post|Next\s*Post|相关阅读|猜你喜欢|延伸阅读",
    r"Tags?\s*:.*$",
    r"Posted\s+(?:on|by|in).*?\d{4}.*?$",
    r"作者[:：].*?(?:来源|责编|编辑).*?$",
    r"责编[:：].*?$",
    r"编辑[:：].*?$",
]

_BOILERPLATE_RE = re.compile("|".join(BOILERPLATE_PATTERNS), re.IGNORECASE | re.MULTILINE)

# HTML 标签清理
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_NAV_RE = re.compile(r"<nav\b[^>]*>.*?</nav>", re.IGNORECASE | re.DOTALL)
_FOOTER_RE = re.compile(r"<footer\b[^>]*>.*?</footer>", re.IGNORECASE | re.DOTALL)
_HEADER_RE = re.compile(r"<header\b[^>]*>.*?</header>", re.IGNORECASE | re.DOTALL)
_ASCII_RE = re.compile(r"&#(\d+);|&#x([0-9a-fA-F]+);")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def _decode_html_entities(text: str) -> str:
    """还原 &amp; &lt; &gt; &#xx; 等 HTML 实体。"""
    text = unescape(text)
    # 处理遗漏的 &#xx; 形式
    def _replace(m: re.Match) -> str:
        try:
            return chr(int(m.group(1) or int(m.group(2), 16)))
        except (ValueError, OverflowError):
            return ""
    return _ASCII_RE.sub(_replace, text)


def _strip_html(text: str) -> str:
    """去 HTML 标签，保留文本内容。"""
    if not text:
        return ""
    # 先去整块（script/style/nav/footer/header）
    text = _SCRIPT_RE.sub(" ", text)
    text = _STYLE_RE.sub(" ", text)
    text = _NAV_RE.sub(" ", text)
    text = _FOOTER_RE.sub(" ", text)
    text = _HEADER_RE.sub(" ", text)
    # 再去剩余标签
    text = _HTML_TAG_RE.sub(" ", text)
    # 还原实体
    text = _decode_html_entities(text)
    return text


def _strip_boilerplate(text: str) -> str:
    """去掉 boilerplate 行/段。"""
    if not text:
        return ""
    lines = text.split("\n")
    keep = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _BOILERPLATE_RE.search(line):
            continue
        # 短行（< 5 个有效字符）大多是导航/按钮文案
        if len(re.sub(r"\s", "", line)) < 5:
            continue
        keep.append(line)
    return "\n".join(keep)


def _normalize_whitespace(text: str) -> str:
    """合并多余空白。"""
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def clean_text(text: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """一站式清洗：去 HTML → 去 boilerplate → 合并空白 → 智能截断。"""
    if not text:
        return ""
    text = _strip_html(text)
    text = _strip_boilerplate(text)
    text = _normalize_whitespace(text)
    if len(text) > max_chars:
        # 智能截断：优先在段落边界（双换行），其次在句子边界
        truncated = text[:max_chars]
        # 尝试在段落边界截断
        para_split = truncated.rsplit("\n\n", 1)
        if len(para_split) == 2 and len(para_split[0]) > max_chars * 0.7:
            text = para_split[0]
        else:
            # 尝试在句子边界截断
            sentence_split = truncated.rsplit("\n", 1)
            if len(sentence_split) == 2 and len(sentence_split[0]) > max_chars * 0.7:
                text = sentence_split[0]
            else:
                text = truncated
    return text


def _pick_content(mat: ScoredMaterial) -> tuple[str, bool]:
    """从素材挑正文：优先 content，其次 snippet。返回 (内容, 是否被截断)。"""
    raw = mat.content if mat.content else (mat.snippet or "")
    original_len = len(raw)
    cleaned = clean_text(raw, MAX_CONTENT_CHARS)
    truncated = len(cleaned) < original_len and original_len > MAX_CONTENT_CHARS
    return cleaned, truncated


def _clean_snippet(snippet: str) -> str:
    if not snippet:
        return ""
    return clean_text(snippet, SNIPPET_MAX)


def _clean_title(title: str) -> str:
    if not title:
        return ""
    title = _strip_html(title)
    title = _normalize_whitespace(title)
    return title[:TITLE_MAX]


def content_cleaner_node(state: ContentCleanerInput) -> ContentCleanerOutput:
    cleaned: List[ScoredMaterial] = []
    total_before = sum(len(m.content or "") for m in state.scored_materials)
    total_after = 0
    truncated_count = 0

    for mat in state.scored_materials:
        c, was_truncated = _pick_content(mat)
        total_after += len(c)
        if was_truncated:
            truncated_count += 1

        # 添加截断标记到 extra_data
        extra = dict(mat.extra_data or {})
        if was_truncated:
            extra["truncated"] = True

        cleaned.append(
            ScoredMaterial(
                url=mat.url,
                title=_clean_title(mat.title),
                snippet=_clean_snippet(mat.snippet),
                content=c,
                source=mat.source,
                publish_time=mat.publish_time,
                category=mat.category,
                heat_score=mat.heat_score,
                score_reason=mat.score_reason,
                related_urls=mat.related_urls,
                cluster_size=mat.cluster_size,
                extra_data=extra,
            )
        )

    reduction = (1 - total_after / total_before) * 100 if total_before else 0
    logger.info(
        f"清洗: {len(cleaned)} 条, 字符 {total_before} → {total_after} (↓{reduction:.0f}%), 截断 {truncated_count} 条"
    )
    return ContentCleanerOutput(cleaned_materials=cleaned)