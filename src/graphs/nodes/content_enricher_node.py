"""
正文补齐节点 - 对 content 空或过短的素材抓取原页面正文
- 位置：event_cluster 之后、content_cleaner 之前（只对代表条抓，省请求）
- 目标：解决聚合源只给标题/URL 导致 LLM 编造事实的问题
- 抓取失败时不再一刀切丢弃：信息足够的 GitHub / watchlist / 多源热点会保留
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

MIN_CONTENT_FOR_LLM = 200
MIN_EVIDENCE_CHARS = 120
FETCH_TIMEOUT = 8
MAX_FETCH_CHARS = 8000
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


def _evidence_text(m: ScoredMaterial) -> str:
    return _normalize_whitespace(" ".join([m.title or "", m.snippet or "", m.content or ""]))


def _has_enough_evidence(m: ScoredMaterial) -> bool:
    """聚合源 / GitHub / watchlist 素材即使抓不到正文，也可凭足够摘要进入生成。"""
    evidence = _evidence_text(m)
    if len(evidence) >= MIN_CONTENT_FOR_LLM:
        return True
    source = (m.source or "").lower()
    category = (m.category or "").lower()
    if len(evidence) < MIN_EVIDENCE_CHARS:
        return False
    if any(k in source for k in ("watchlist", "github", "aihot-hot", "radar-daily")):
        return True
    if any(k in category for k in ("网络工具", "开源治理", "安全隐私")):
        return True
    if m.cluster_size > 1:
        return True
    return False


def content_enricher_node(state: ContentEnricherInput) -> ContentEnricherOutput:
    materials = state.scored_materials
    if not materials:
        return ContentEnricherOutput(scored_materials=[], enriched_count=0, dropped_count=0)

    enriched: List[ScoredMaterial] = []
    enriched_count = 0
    kept_without_fetch = 0
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
            continue

        if _has_enough_evidence(mat):
            fallback = _evidence_text(mat)[:2000]
            enriched.append(mat.model_copy(update={"content": fallback}))
            kept_without_fetch += 1
        else:
            dropped_count += 1
            dropped_titles.append(mat.title[:40])

    logger.info(
        f"正文补齐: 输入 {len(materials)} 条 / 补齐 {enriched_count} / "
        f"抓取失败但保留 {kept_without_fetch} / 丢弃 {dropped_count}（信息不足）"
    )
    for t in dropped_titles[:5]:
        logger.info(f"  丢弃: {t}")

    return ContentEnricherOutput(
        scored_materials=enriched,
        enriched_count=enriched_count + kept_without_fetch,
        dropped_count=dropped_count,
    )
