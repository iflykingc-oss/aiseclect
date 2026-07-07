"""
综合采集（AIHOT hot-topics + Tavily watchlist）
- AIHOT hot-topics: 多源热度排序，回答“现在 AI 圈最热是什么”
- Tavily watchlist: 定向补长尾小热点（Xray / VPN / proxy / 开源网络工具）
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import List

from graphs.state import TavilyCollectorInput, TavilyCollectorOutput, RawMaterial
from tools.aihot_client import AIHotClient
from tools.tavily import TavilyClient

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST = {
    "topics": [
        {
            "name": "proxy_ecosystem",
            "enabled": True,
            "max_results": 12,
            "queries": [
                "Xray Project X VPN proxy GitHub release",
                "Xray-core XTLS Reality update",
                "V2Ray Xray proxy release",
                "sing-box proxy GitHub release",
                "Clash Meta mihomo proxy update",
                "Hysteria Trojan Shadowsocks proxy release",
                "VPN proxy censorship GFW developer tools",
                "翻墙 科学上网 代理 工具 开源 更新",
                "Xray 作者 退出 中国 俄罗斯 伊朗",
            ],
            "keywords": [
                "xray", "project x", "xtls", "v2ray", "vless", "vmess", "reality",
                "sing-box", "clash", "mihomo", "hysteria", "trojan", "shadowsocks",
                "vpn", "proxy", "代理", "翻墙", "科学上网", "gfw", "审查", "封锁",
                "开源作者", "退出中国", "俄罗斯", "伊朗", "release", "breaking change", "漏洞", "cve",
            ],
        }
    ]
}


def _load_watchlist() -> dict:
    candidates = [
        os.path.join(os.getenv("COZE_WORKSPACE_PATH", os.getcwd()), "config/watchlist.json"),
        os.path.join(os.getcwd(), "config/watchlist.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                logger.warning(f"watchlist 读取失败，使用默认配置: {e}")
                break
    return DEFAULT_WATCHLIST


def _enabled_topics() -> List[dict]:
    cfg = _load_watchlist()
    return [t for t in (cfg.get("topics") or []) if t.get("enabled", True)]


def _matched_terms(text: str, terms: List[str]) -> List[str]:
    text_l = (text or "").lower()
    hits: List[str] = []
    for term in terms:
        if str(term).lower() in text_l:
            hits.append(str(term))
    return hits[:8]


def _collect_aihot_hot(state: TavilyCollectorInput, seen: set[str]) -> List[RawMaterial]:
    client = AIHotClient()
    take = max(50, state.max_per_source * 4)
    hot = client.hot_topics(take=take)
    materials: List[RawMaterial] = []
    for item in hot[: max(state.max_per_source * 2, state.max_per_source)]:
        url = item.get("permalink") or item.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        title = item.get("title", "")
        source_count = item.get("sourceCount", 0)
        source_names = item.get("sourceNames", []) or []
        snippet = f"🔥 {source_count} 个独立信源在报道"
        if source_names:
            snippet += f"\n来源: {', '.join(source_names[:5])}"
        materials.append(
            RawMaterial(
                url=url,
                title=title,
                snippet=snippet,
                content="",
                source="aihot-hot",
                publish_time=item.get("latestAt"),
                extra_data={
                    "source_count": source_count,
                    "source_names": source_names,
                    "original_url": item.get("url"),
                    "source_name": item.get("source"),
                },
            )
        )
    return materials


def _collect_tavily_watchlist(state: TavilyCollectorInput, seen: set[str]) -> List[RawMaterial]:
    client = TavilyClient(timeout=8)
    if not client.api_key:
        return []

    materials: List[RawMaterial] = []
    per_query = 2 if state.max_per_source <= 10 else 3
    for topic in _enabled_topics():
        topic_name = str(topic.get("name") or "watchlist")
        queries = [str(q) for q in (topic.get("queries") or []) if q]
        terms = [str(k) for k in (topic.get("keywords") or []) if k]
        max_watchlist = int(topic.get("max_results") or max(12, state.max_per_source))
        topic_count = 0
        for query in queries:
            if topic_count >= max_watchlist:
                break
            resp = client.search(
                query=query,
                max_results=per_query,
                topic="news",
                days=14,
                include_raw_content=True,
            )
            for item in resp.items:
                if topic_count >= max_watchlist:
                    break
                if not item.url or item.url in seen:
                    continue
                text = " ".join([item.title, item.snippet, item.raw_content])
                hits = _matched_terms(text, terms)
                # Tavily 也可能返回泛结果；没有命中 watchlist 词时不加入长尾热点池。
                if not hits:
                    continue
                seen.add(item.url)
                topic_count += 1
                snippet = item.snippet or item.content or ""
                snippet = re.sub(r"\s+", " ", snippet).strip()[:700]
                snippet = f"watchlist: {', '.join(hits)}\ntopic: {topic_name}\nquery: {query}\n{snippet}".strip()
                materials.append(
                    RawMaterial(
                        url=item.url,
                        title=item.title,
                        snippet=snippet,
                        content=(item.raw_content or item.content or snippet)[:3000],
                        source="tavily-watchlist",
                        publish_time=item.published_date,
                        extra_data={
                            "topic": topic_name,
                            "query": query,
                            "matched_terms": hits,
                            "tavily_score": item.score,
                            "site_name": item.site_name,
                        },
                    )
                )
        logger.info(f"Tavily watchlist topic={topic_name}: {topic_count} 条")
    return materials


def tavily_collector_node(state: TavilyCollectorInput) -> TavilyCollectorOutput:
    logger.info("综合采集开始（AIHOT hot-topics + Tavily watchlist）")
    seen: set[str] = set()
    materials: List[RawMaterial] = []

    try:
        materials.extend(_collect_aihot_hot(state, seen))
    except Exception as e:
        logger.warning(f"AIHOT hot-topics 采集失败: {e}")

    watchlist = _collect_tavily_watchlist(state, seen)
    materials.extend(watchlist)

    logger.info(f"综合采集: {len(materials)} 条（Tavily watchlist {len(watchlist)} 条）")
    return TavilyCollectorOutput(tavily_materials=materials)
