"""
正文补齐节点 - 对 content 空或过短的素材抓取原页面正文
- 位置：event_cluster 之后、content_cleaner 之前（只对代表条抓，省请求）
- 目标：解决聚合源只给标题/URL 导致 LLM 编造事实的问题
- 抓取失败时不再一刀切丢弃：信息足够的 GitHub / watchlist / 多源热点会保留

性能：并发抓取（ThreadPoolExecutor，max_workers=10），单节点整体超时 30s
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import List

import requests
from requests.exceptions import SSLError, ConnectionError

from graphs.state import (
    ContentEnricherInput,
    ContentEnricherOutput,
    ScoredMaterial,
)
from graphs.nodes.content_cleaner_node import _strip_html, _strip_boilerplate, _normalize_whitespace

logger = logging.getLogger(__name__)

MIN_CONTENT_FOR_LLM = 200
MIN_EVIDENCE_CHARS = 60

# NewsNow 聚合源：摘要普遍只有 title+URL，没有正文。短摘要也保留进 LLM，
# 让生成阶段自己提炼事实；不再因抓不到正文就整条 DROP。
NEWSNOW_SOURCE_PREFIXES = (
    "newsnow-",
)
FETCH_TIMEOUT = 6  # 单 URL 超时（秒）
NODE_TOTAL_TIMEOUT = 30  # 节点整体超时（秒）
MAX_WORKERS = 10  # 并发抓取数
MAX_FETCH_CHARS = 8000
UA = "Mozilla/5.0 (compatible; aiseclect/1.0; +https://github.com/iflykingc-oss/aiseclect)"


def _fetch_article(url: str) -> str:
    """抓单个 URL 的正文。失败或过短返回空字符串。SSL 错误不重试（避免长时间挂起）。"""
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": UA}, allow_redirects=True)
    except SSLError as e:
        logger.debug(f"正文 SSL 错误（跳过）{url}: {type(e).__name__}")
        return ""
    except ConnectionError as e:
        logger.debug(f"正文 连接错误（跳过）{url}: {type(e).__name__}")
        return ""
    except requests.RequestException as e:
        logger.debug(f"正文抓取失败 {url}: {type(e).__name__}: {str(e)[:100]}")
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
    # NewsNow 聚合源：标题+短摘要在主流热榜已是高信号证据，保留进 LLM 自提
    if any(source.startswith(p) for p in NEWSNOW_SOURCE_PREFIXES):
        return True
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

    # 1) 不需要抓的先留着
    enriched: List[ScoredMaterial] = []
    to_fetch: List[ScoredMaterial] = []
    for mat in materials:
        if _needs_enrich(mat):
            to_fetch.append(mat)
        else:
            enriched.append(mat)

    # 2) 并发抓需要补齐的（线程池，节点整体超时 NODE_TOTAL_TIMEOUT）
    fetch_results: dict[str, str] = {}
    if to_fetch:
        per_url_timeout = max(2, NODE_TOTAL_TIMEOUT // max(1, len(to_fetch) // MAX_WORKERS + 1))

        def _do_fetch(mat: ScoredMaterial) -> tuple[str, str]:
            return mat.url, _fetch_article(mat.url)

        logger.info(f"正文补齐: {len(to_fetch)} 条开始并发抓取 (workers={MAX_WORKERS}, per-url={per_url_timeout}s)")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = [ex.submit(_do_fetch, m) for m in to_fetch]
            try:
                for fut in futures:
                    try:
                        url, body = fut.result(timeout=per_url_timeout)
                        fetch_results[url] = body
                    except FuturesTimeout:
                        logger.debug("单条正文抓取超时")
                    except Exception as e:
                        logger.debug(f"抓取异常: {type(e).__name__}: {e}")
            finally:
                # 未完成的 future 取消（with 退出时自动）
                pass

    # 3) 根据抓取结果决定保留 / 丢弃 / fallback
    enriched_count = 0
    kept_without_fetch = 0
    dropped_count = 0
    dropped_titles: List[str] = []
    for mat in to_fetch:
        fetched = fetch_results.get(mat.url, "")
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
        f"正文补齐: 输入 {len(materials)} 条 / 抓取 {len(fetch_results)}/{len(to_fetch)} / "
        f"补齐 {enriched_count} / 抓取失败但保留 {kept_without_fetch} / 丢弃 {dropped_count}（信息不足）"
    )
    for t in dropped_titles[:5]:
        logger.info(f"  丢弃: {t}")

    return ContentEnricherOutput(
        scored_materials=enriched,
        enriched_count=enriched_count + kept_without_fetch,
        dropped_count=dropped_count,
    )