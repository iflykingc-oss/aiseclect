"""
GitHub 采集
- Watchlist Search API: 补 Xray / VPN / proxy / 开源网络工具等小热点
- Trending HTML: 保留原 daily trending 兜底，按 AI + 网络工具关键词筛选
"""
from __future__ import annotations

import json
import logging
import os
import re
from html import unescape
from typing import List

import requests

from graphs.state import GitHubCollectorInput, GitHubCollectorOutput, RawMaterial
from tools.github_trending import GitHubTrendingClient

logger = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending"
GH_API = "https://api.github.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

AI_KEYWORDS = (
    "ai", "llm", "gpt", "agent", "rag", "langchain", "claude", "openai", "anthropic",
    "transformer", "diffusion", "copilot", "stable", "llama", "mistral", "qwen",
    "deepseek", "embedding", "vector", "prompt", "mcp", "ollama", "huggingface",
)

WATCHLIST_KEYWORDS = (
    "xray", "xtls", "v2ray", "vless", "vmess", "reality", "sing-box", "clash",
    "mihomo", "hysteria", "trojan", "shadowsocks", "vpn", "proxy", "gfw", "censorship",
)

DEFAULT_WATCHLIST_QUERY = "xray OR v2ray OR sing-box OR clash OR mihomo OR hysteria OR trojan OR shadowsocks OR vpn OR proxy OR xtls"


def _parse_stars(text: str) -> int:
    """解析 '1,234' / '12.3k' 格式的 star 数。"""
    text = text.strip().replace(",", "")
    if not text:
        return 0
    m = re.match(r"([\d.]+)\s*([kKmM]?)", text)
    if not m:
        return 0
    n = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        n *= 1000
    elif unit == "m":
        n *= 1_000_000
    return int(n)


def _load_sources() -> dict:
    candidates = [
        os.path.join(os.getenv("COZE_WORKSPACE_PATH", os.getcwd()), "config/sources.json"),
        os.path.join(os.getcwd(), "config/sources.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                logger.warning(f"sources 读取失败，跳过固定仓库采集: {e}")
                break
    return {"sources": []}


def _enabled_sources(source_type: str) -> List[dict]:
    cfg = _load_sources()
    return [s for s in (cfg.get("sources") or []) if s.get("enabled", True) and s.get("type") == source_type]


def _github_headers() -> dict:
    headers = {
        "User-Agent": "aiseclect-bot",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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
                logger.warning(f"watchlist 读取失败，使用默认 GitHub query: {e}")
                break
    return {"topics": [{"name": "proxy_ecosystem", "enabled": True, "github_query": DEFAULT_WATCHLIST_QUERY, "keywords": list(WATCHLIST_KEYWORDS)}]}


def _enabled_topics() -> List[dict]:
    cfg = _load_watchlist()
    return [t for t in (cfg.get("topics") or []) if t.get("enabled", True)]


def _has_target_keyword(text: str) -> bool:
    text_l = (text or "").lower()
    terms = list(AI_KEYWORDS) + list(WATCHLIST_KEYWORDS)
    for topic in _enabled_topics():
        terms.extend(str(k).lower() for k in (topic.get("keywords") or []))
    return any(k in text_l for k in terms)


def _split_github_query(query: str) -> List[str]:
    """GitHub Search 最多允许 5 个 OR；长 watchlist 拆成多次查询。"""
    parts = [p.strip() for p in re.split(r"\s+OR\s+", query, flags=re.IGNORECASE) if p.strip()]
    if len(parts) <= 6:
        return [query]
    return [" OR ".join(parts[i:i + 6]) for i in range(0, len(parts), 6)]


def _collect_watchlist(state: GitHubCollectorInput, seen: set[str]) -> List[RawMaterial]:
    client = GitHubTrendingClient(timeout=20)
    materials: List[RawMaterial] = []
    for topic in _enabled_topics():
        topic_name = str(topic.get("name") or "watchlist")
        query = str(topic.get("github_query") or "")
        if not query:
            continue
        keywords = [str(k).lower() for k in (topic.get("keywords") or [])]
        max_results = int(topic.get("max_results") or max(10, state.max_per_source))
        repos = []
        for query_part in _split_github_query(query):
            repos.extend(
                client.trending(
                    keywords=query_part,
                    days=30,
                    max_results=max_results,
                )
            )
        topic_count = 0
        for repo in repos:
            if not repo.url or repo.url in seen:
                continue
            seen.add(repo.url)
            topic_count += 1
            meta_text = " ".join([repo.name, repo.full_name, repo.description, " ".join(repo.topics)]).lower()
            hits = [k for k in keywords if k in meta_text]
            snippet = f"⭐ {repo.stars:,}"
            if repo.language:
                snippet += f" | {repo.language}"
            if repo.pushed_at:
                snippet += f" | pushed {repo.pushed_at[:10]}"
            if repo.description:
                snippet += f"\n{repo.description}"
            if hits:
                snippet += f"\nwatchlist: {', '.join(hits[:8])}"
            materials.append(
                RawMaterial(
                    url=repo.url,
                    title=repo.full_name or repo.name,
                    snippet=snippet,
                    content=repo.description,
                    source="github-watchlist",
                    publish_time=repo.pushed_at,
                    extra_data={
                        "topic": topic_name,
                        "stars": repo.stars,
                        "language": repo.language,
                        "topics": repo.topics,
                        "repo_path": repo.full_name,
                        "matched_terms": hits,
                    },
                )
            )
        logger.info(f"GitHub watchlist topic={topic_name}: {topic_count} 条")
    return materials


def _get_json(url: str) -> dict:
    try:
        resp = requests.get(url, headers=_github_headers(), timeout=20)
        if resp.status_code != 200:
            logger.debug(f"GitHub API HTTP {resp.status_code}: {url}")
            return {}
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"GitHub API 请求失败 {url}: {e}")
        return {}


def _collect_repo_watch(seen: set[str]) -> List[RawMaterial]:
    materials: List[RawMaterial] = []
    for source_cfg in _enabled_sources("github_repo"):
        source_name = str(source_cfg.get("name") or "github_repo_watch")
        repos = [str(r).strip() for r in (source_cfg.get("repos") or []) if str(r).strip()]
        max_results = int(source_cfg.get("max_results") or len(repos) or 20)
        source_weight = float(source_cfg.get("source_weight") or 1.0)
        count = 0
        for repo_path in repos:
            if count >= max_results:
                break
            repo = _get_json(f"{GH_API}/repos/{repo_path}")
            if not repo:
                continue
            html_url = repo.get("html_url") or f"https://github.com/{repo_path}"
            if html_url in seen:
                continue
            release = _get_json(f"{GH_API}/repos/{repo_path}/releases/latest")
            release_title = release.get("name") or release.get("tag_name") or ""
            release_body = (release.get("body") or "").strip()
            pushed_at = repo.get("pushed_at") or repo.get("updated_at")
            stars = int(repo.get("stargazers_count") or 0)
            topics = list(repo.get("topics") or [])
            description = repo.get("description") or ""
            snippet = f"⭐ {stars:,} | pushed {str(pushed_at or '')[:10]}"
            if release_title:
                snippet += f"\nlatest release: {release_title}"
            if description:
                snippet += f"\n{description}"
            if release_body:
                snippet += f"\n{release_body[:600]}"
            seen.add(html_url)
            count += 1
            source_signal_score = min(100.0, 45.0 + min(stars / 1000, 25.0) + (15.0 if release_title else 0.0))
            materials.append(
                RawMaterial(
                    url=release.get("html_url") or html_url,
                    title=f"{repo_path}: {release_title}" if release_title else repo_path,
                    snippet=snippet,
                    content=(release_body or description)[:3000],
                    source="github-repo-watch",
                    publish_time=release.get("published_at") or pushed_at,
                    extra_data={
                        "source_name": source_name,
                        "source_type": "github_repo",
                        "source_weight": source_weight,
                        "source_signal_score": source_signal_score,
                        "repo_path": repo_path,
                        "stars": stars,
                        "forks": int(repo.get("forks_count") or 0),
                        "topics": topics,
                        "language": repo.get("language") or "",
                        "pushed_at": pushed_at,
                        "latest_release": release_title,
                    },
                )
            )
        logger.info(f"GitHub repo watch source={source_name}: {count} 条")
    return materials


def _collect_trending_html(state: GitHubCollectorInput, seen: set[str]) -> List[RawMaterial]:
    materials: List[RawMaterial] = []
    try:
        resp = requests.get(
            TRENDING_URL,
            params={"since": "daily"},
            headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=20,
        )
        if resp.status_code != 200:
            logger.error(f"GitHub Trending HTTP {resp.status_code}")
            return materials
        html = resp.text
    except requests.RequestException as e:
        logger.error(f"GitHub Trending 网络异常: {e}")
        return materials

    repo_blocks = re.findall(r'<article class="Box-row">(.+?)</article>', html, re.DOTALL)
    if not repo_blocks:
        repo_blocks = re.findall(r'<li class="Box-row">(.+?)</li>', html, re.DOTALL)

    for block in repo_blocks:
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="/([^"]+)"', block)
        if not m:
            continue
        repo_path = m.group(1).strip()
        if not repo_path or "/" not in repo_path:
            continue
        url = f"https://github.com/{repo_path}"
        if url in seen:
            continue

        title_match = re.search(r'href="/' + re.escape(repo_path) + r'"[^>]*>(.+?)</a>', block, re.DOTALL)
        if title_match:
            full_title = re.sub(r"<[^>]+>", "", title_match.group(1))
            full_title = unescape(full_title).strip()
            full_title = re.sub(r"\s+", " ", full_title)
        else:
            full_title = repo_path

        desc_match = re.search(r'<p class="col-9[^"]*">(.+?)</p>', block, re.DOTALL)
        description = ""
        if desc_match:
            description = re.sub(r"<[^>]+>", "", desc_match.group(1))
            description = unescape(description).strip()
            description = re.sub(r"\s+", " ", description)

        lang_match = re.search(r'itemprop="programmingLanguage">([^<]+)</span>', block)
        language = lang_match.group(1).strip() if lang_match else ""

        stars_match = re.search(r'href="/' + re.escape(repo_path) + r'/stargazers"[^>]*>(.+?)</a>', block, re.DOTALL)
        stars = _parse_stars(stars_match.group(1)) if stars_match else 0

        forks_match = re.search(r'href="/' + re.escape(repo_path) + r'/forks"[^>]*>(.+?)</a>', block, re.DOTALL)
        forks = _parse_stars(forks_match.group(1)) if forks_match else 0

        if not _has_target_keyword(" ".join([full_title, description, repo_path])):
            continue

        seen.add(url)
        snippet = f"⭐ {stars:,} | 🍴 {forks:,}"
        if language:
            snippet += f" | {language}"
        if description:
            snippet += f"\n{description}"

        materials.append(
            RawMaterial(
                url=url,
                title=full_title,
                snippet=snippet,
                content=description,
                source="github-trending",
                publish_time=None,
                extra_data={
                    "stars": stars,
                    "forks": forks,
                    "language": language,
                    "repo_path": repo_path,
                },
            )
        )
        if len(materials) >= state.max_per_source:
            break
    return materials


def github_collector_node(state: GitHubCollectorInput) -> GitHubCollectorOutput:
    logger.info("GitHub 采集开始（watchlist search + trending HTML）")
    seen: set[str] = set()
    materials: List[RawMaterial] = []

    repo_watch = _collect_repo_watch(seen)
    materials.extend(repo_watch)

    watchlist = _collect_watchlist(state, seen)
    materials.extend(watchlist)

    trending = _collect_trending_html(state, seen)
    materials.extend(trending)

    logger.info(f"GitHub: {len(materials)} 条（repo_watch {len(repo_watch)} / watchlist {len(watchlist)} / trending {len(trending)}）")
    return GitHubCollectorOutput(github_materials=materials)
