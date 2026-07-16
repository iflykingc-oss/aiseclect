"""
NewsNow 采集节点（中文/英文大众向热点聚合）
- 数据源: newsnow.busiyi.world 公开 API GET /api/s?id=<source_id>
- 覆盖中文大众向信源（微博/知乎/B站/百度/抖音/贴吧/澎湃/IT之家/少数派 等）
  + Product Hunt / Hacker News 等英文信源
- source id 通过环境变量 NEWSNOW_SOURCE_IDS 覆盖，默认用内置列表
- 仅读 GET，无 key，无严格速率限制（间隔 1s 即可）
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from graphs.state import NewsNowCollectorInput, NewsNowCollectorOutput, RawMaterial

logger = logging.getLogger(__name__)

NEWSNOW_BASE = "https://newsnow.busiyi.world"
DEFAULT_TIMEOUT = 8
INTER_REQUEST_SLEEP = 0.4  # 礼貌性间隔

# 已知可用的 source id（实战探测过 busiyi 部署，不依赖 GitHub 仓库的 source 列表）
# 按"普通用户能看懂 → 圈内开发者"的优先级排序，前面权重更高
DEFAULT_SOURCE_IDS: List[str] = [
    "weibo",       # 微博热搜（大众流量）
    "zhihu",       # 知乎热榜（大众讨论）
    "bilibili",    # B 站热搜（视频/年轻人）
    "baidu",       # 百度热搜
    "douyin",      # 抖音热搜
    "tieba",       # 贴吧热议
    "thepaper",    # 澎湃新闻（社会新闻）
    "ithome",      # IT 之家（科技产品新闻，普通用户能懂）
    "sspai",       # 少数派（效率工具）
    "producthunt", # 产品发布
    "hackernews",  # HN（保留一个开发者向）
    "v2ex",        # V2EX
    "solidot",     # 奇客资讯（开源/科技）
    "cls",         # 财联社（财经）
    "wallstreetcn", # 华尔街见闻
    "gelonghui",   # 格隆汇
    "jin10",       # 金十数据
    "fastbull",    # 快讯
    "coolapk",     # 酷安
]

# 中文分类映射（按 source id 给一个 category_zh）
CATEGORY_ZH_MAP: Dict[str, str] = {
    "weibo": "大众热搜",
    "zhihu": "大众讨论",
    "bilibili": "视频热搜",
    "baidu": "大众热搜",
    "douyin": "视频热搜",
    "tieba": "社区热议",
    "thepaper": "社会新闻",
    "ithome": "科技产品",
    "sspai": "效率工具",
    "producthunt": "产品发布",
    "hackernews": "开发者社区",
    "v2ex": "开发者社区",
    "solidot": "科技资讯",
    "cls": "财经资讯",
    "wallstreetcn": "财经资讯",
    "gelonghui": "财经资讯",
    "jin10": "财经资讯",
    "fastbull": "财经资讯",
    "coolapk": "数码社区",
}

# 大众热搜源（需要 AI 关键词过滤）
GENERAL_HOT_SOURCES = {"weibo", "zhihu", "bilibili", "baidu", "douyin", "tieba", "coolapk"}

# 技术/AI 源（保留全部内容）
TECH_SOURCES = {"hackernews", "v2ex", "ithome", "sspai", "producthunt", "solidot"}

# AI 关键词（从 heat_scorer_node 同步）
AI_KEYWORDS = (
    "ai", "a.i.", "llm", "gpt", "chatgpt", "claude", "gemini", "grok", "deepseek",
    "豆包", "通义", "文心", "千问", "kimi", "智谱", "月之暗面",
    "midjourney", "sora", "runway", "pika", "可灵", "即梦", "海螺",
    "cursor", "windsurf", "copilot", "v0", "bolt",
    "prompt", "咒语", "智能体", "agent", "大模型", "人工智能",
    "apple intelligence", "galaxy ai", "小爱同学", "copilot+",
    "notion ai", "office copilot", "adobe firefly", "wps ai", "飞书 ai",
    "ai 助手", "ai 写作", "ai 拍照", "ai 翻译", "ai 搜索",
)


def _parse_source_ids() -> List[str]:
    """从环境变量读覆盖列表，否则用默认。"""
    raw = os.getenv("NEWSNOW_SOURCE_IDS", "").strip()
    if not raw:
        return list(DEFAULT_SOURCE_IDS)
    parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
    return parts or list(DEFAULT_SOURCE_IDS)


def _fetch_source(source_id: str, timeout: int = DEFAULT_TIMEOUT) -> List[dict]:
    """GET /api/s?id=<id>，返回 items 数组（出错返回 []）。"""
    url = f"{NEWSNOW_BASE}/api/s?id={source_id}"
    # NewsNow 部署在 Cloudflare 后面，bot UA 直接 403。用真实浏览器 UA + Referer 绕过。
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://newsnow.busiyi.world/",
            "Origin": "https://newsnow.busiyi.world",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning(f"NewsNow {source_id} 拉取失败: {e}")
        return []
    if not isinstance(data, dict) or data.get("error"):
        logger.warning(f"NewsNow {source_id} 返回错误: {data.get('message') if isinstance(data, dict) else data}")
        return []
    items = data.get("items") or []
    if not isinstance(items, list):
        return []
    return items


def _normalize_url(url: str) -> str:
    """去 utm_* / ref 参数，避免来源不同导致去重失败。"""
    try:
        u = urlparse(url)
    except Exception:
        return url
    from urllib.parse import parse_qsl, urlunparse
    qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
          if not k.lower().startswith("utm_") and k.lower() not in ("ref", "ref_source")]
    return urlunparse((u.scheme, u.netloc, u.path, u.params, "&".join(f"{k}={v}" for k, v in qs), ""))


def _should_keep_item(source_id: str, title: str, snippet: str) -> bool:
    """判断是否保留该条目（技术源全保留，大众源需匹配 AI 关键词）。"""
    if source_id in TECH_SOURCES:
        return True
    if source_id not in GENERAL_HOT_SOURCES:
        return True  # 其他源默认保留

    # 大众热搜源：需要匹配 AI 关键词
    text = (title + " " + snippet).lower()
    return any(kw in text for kw in AI_KEYWORDS)


def newsnow_collector_node(state: NewsNowCollectorInput) -> NewsNowCollectorOutput:
    source_ids = _parse_source_ids()
    per_id = max(2, state.max_per_source // 3)  # 19 source × 3 条 ≈ 57 条（之前 4 条导致下游过载）
    materials: List[RawMaterial] = []
    seen_urls: set = set()
    filtered_count = 0

    logger.info(f"NewsNow 采集开始（{len(source_ids)} 个 source，每源最多 {per_id} 条）")

    for sid in source_ids:
        items = _fetch_source(sid)
        kept = 0
        for it in items:
            if kept >= per_id:
                break
            url = it.get("url")
            title = (it.get("title") or "").strip()
            if not url or not title:
                continue
            url = _normalize_url(url)
            if url in seen_urls:
                continue

            # snippet 取自 extra.description 或 extra.summary，没有就空
            extra = it.get("extra") or {}
            snippet = (extra.get("description") or extra.get("summary") or extra.get("info") or "").strip()

            # AI 关键词过滤
            if not _should_keep_item(sid, title, snippet):
                filtered_count += 1
                continue

            seen_urls.add(url)
            pub_ts = extra.get("pubDate") or extra.get("timestamp") or it.get("timestamp")
            publish_time = None
            if isinstance(pub_ts, (int, float)):
                # NewsNow 用 ms 居多
                if pub_ts > 10_000_000_000:
                    publish_time = str(int(pub_ts // 1000))
                else:
                    publish_time = str(int(pub_ts))
            materials.append(
                RawMaterial(
                    url=url,
                    title=title,
                    snippet=snippet[:500],
                    content=snippet[:500],
                    source=f"newsnow-{sid}",
                    publish_time=publish_time,
                    extra_data={
                        "newsnow_id": it.get("id"),
                        "newsnow_source": sid,
                        "category": CATEGORY_ZH_MAP.get(sid, "综合资讯"),
                        "score": it.get("score"),
                    },
                )
            )
            kept += 1
        if items:
            logger.info(f"  NewsNow {sid}: 拉取 {len(items)} 条，保留 {kept} 条")
        time.sleep(INTER_REQUEST_SLEEP)

    logger.info(f"NewsNow 采集: {len(materials)} 条（过滤 {filtered_count} 条非 AI，来自 {len(source_ids)} 个 source）")
    return NewsNowCollectorOutput(newsnow_materials=materials)