"""
正文补齐节点 - 对 content 空或过短的素材抓取原页面正文
- 位置：event_cluster 之后、content_cleaner 之前（只对代表条抓，省请求）
- 目标：解决 LearnPrompt Radar 只给标题/URL 导致 LLM 在推文里编造事实的问题
- 抓取失败/正文过短的：**从素材池丢弃**，宁可少发不硬编
- 依赖：仅 requests + 现有 content_cleaner._strip_html，无新增第三方库

MIN_CONTENT_FOR_LLM 是判断「素材信息足够 LLM 生成事实性推文」的下限。
过短（如只有导航栏残余）说明抓取失败或页面被反爬，此时选择丢弃比编造安全。
"""
from __future__ import annotations

import logging
from typing import List

import requests

from graphs.state import (
    ContentEnricherInput,
    ContentEnricherOutput,
    ScoredMaterial,
)
from graphs.nodes.content_cleaner_node import _strip_html, _strip_boilerplate, _normalize_whitespace

logger = logging.getLogger(__name__)

MIN_CONTENT_FOR_LLM = 200   # 少于这么多字符视为「素材不足」，直接丢弃
FETCH_TIMEOUT = 8
MAX_FETCH_CHARS = 8000      # 抓下来的原始 HTML 最多截取这么多字符处理，避免超大页
UA = "Mozilla/5.0 (compatible; aiseclect/1.0; +https://github.com/iflykingc-oss/aiseclect)"


def _fetch_article(url: str) -> str:
    """抓单个 URL 的正文。失败或过短返回空字符串。"""
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": UA}, allow_redirects=True)
    except requests.RequestException as e:
        logger.debug(f"正文抓取失败 {url}: {e}")
        return ""
    if resp.status_code != 200:
        logger.debug(f"正文 HTTP {resp.status_code}: {url}")
        return ""

    # 编码回退：response.encoding 有时会给成 ISO-8859-1
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"

    html = resp.text[:MAX_FETCH_CHARS]
    text = _strip_html(html)
    text = _strip_boilerplate(text)
    text = _normalize_whitespace(text)
    return text


def _needs_enrich(m: ScoredMaterial) -> bool:
    """判断是否需要抓正文：content 空或过短。"""
    return len((m.content or "").strip()) < MIN_CONTENT_FOR_LLM


def content_enricher_node(state: ContentEnricherInput) -> ContentEnricherOutput:
    materials = state.scored_materials
    if not materials:
        return ContentEnricherOutput(scored_materials=[], enriched_count=0, dropped_count=0)

    enriched: List[ScoredMaterial] = []
    enriched_count = 0
    dropped_count = 0
    dropped_titles: List[str] = []

    for mat in materials:
        if not _needs_enrich(mat):
            enriched.append(mat)
            continue

        fetched = _fetch_article(mat.url)
        if len(fetched) >= MIN_CONTENT_FOR_LLM:
            enriched.append(mat.model_copy(update={"content": fetched[:2000]}))
            enriched_count += 1
        else:
            # 抓不到正文 → 丢弃（宁可少发不硬编）
            dropped_count += 1
            dropped_titles.append(mat.title[:40])

    logger.info(
        f"正文补齐: 输入 {len(materials)} 条 / 补齐 {enriched_count} / 丢弃 {dropped_count}（正文不足）"
    )
    for t in dropped_titles[:5]:
        logger.info(f"  丢弃: {t}")

    return ContentEnricherOutput(
        scored_materials=enriched,
        enriched_count=enriched_count,
        dropped_count=dropped_count,
    )
