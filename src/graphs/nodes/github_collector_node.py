"""
GitHub Trending 采集 - 抓 github.com/trending HTML 页面
- 国内可达（已验证 200 OK）
- 走 HTML 解析，不依赖 GitHub Search API
"""
from __future__ import annotations

import logging
import re
from html import unescape
from typing import List, Optional

import requests

from graphs.state import GitHubCollectorInput, GitHubCollectorOutput, RawMaterial

logger = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


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


def github_collector_node(state: GitHubCollectorInput) -> GitHubCollectorOutput:
    logger.info("GitHub Trending HTML 采集开始")
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
            return GitHubCollectorOutput(github_materials=materials)
        html = resp.text
    except requests.RequestException as e:
        logger.error(f"GitHub Trending 网络异常: {e}")
        return GitHubCollectorOutput(github_materials=materials)

    # GitHub Trending 页面结构：每个 repo 一个 <article class="Box-row">
    repo_blocks = re.findall(
        r'<article class="Box-row">(.+?)</article>', html, re.DOTALL
    )
    if not repo_blocks:
        # 兜底：尝试另一种结构
        repo_blocks = re.findall(
            r'<li class="Box-row">(.+?)</li>', html, re.DOTALL
        )

    for block in repo_blocks:
        # 仓库标题 + 链接：<h2 class="h3 lh-condensed"><a href="/owner/repo">...</a></h2>
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="/([^"]+)"', block)
        if not m:
            continue
        repo_path = m.group(1).strip()
        if not repo_path or "/" not in repo_path:
            continue
        url = f"https://github.com/{repo_path}"

        # 标题提取（去除嵌套 span）
        title_match = re.search(r'href="/' + re.escape(repo_path) + r'"[^>]*>(.+?)</a>', block, re.DOTALL)
        if title_match:
            full_title = re.sub(r"<[^>]+>", "", title_match.group(1))
            full_title = unescape(full_title).strip()
            full_title = re.sub(r"\s+", " ", full_title)
        else:
            full_title = repo_path

        # 描述：<p class="col-9 ...">...</p>
        desc_match = re.search(r'<p class="col-9[^"]*">(.+?)</p>', block, re.DOTALL)
        description = ""
        if desc_match:
            description = re.sub(r"<[^>]+>", "", desc_match.group(1))
            description = unescape(description).strip()
            description = re.sub(r"\s+", " ", description)

        # 主语言
        lang_match = re.search(r'itemprop="programmingLanguage">([^<]+)</span>', block)
        language = lang_match.group(1).strip() if lang_match else ""

        # Stars（总星）
        stars_match = re.search(
            r'href="/' + re.escape(repo_path) + r'/stargazers"[^>]*>(.+?)</a>', block, re.DOTALL
        )
        stars = _parse_stars(stars_match.group(1)) if stars_match else 0

        # Forks
        forks_match = re.search(
            r'href="/' + re.escape(repo_path) + r'/forks"[^>]*>(.+?)</a>', block, re.DOTALL
        )
        forks = _parse_stars(forks_match.group(1)) if forks_match else 0

        # 标题中筛选 AI 关键词
        full_title_lower = full_title.lower()
        ai_keywords = ("ai", "llm", "gpt", "agent", "rag", "langchain", "claude",
                       "openai", "anthropic", "transformer", "diffusion", "copilot",
                       "stable", "llama", "mistral", "qwen", "deepseek", "embedding",
                       "vector", "prompt", "rag", "mcp", "ollama", "huggingface")
        if not any(k in full_title_lower for k in ai_keywords):
            continue  # 过滤非 AI 仓库

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

    logger.info(f"GitHub Trending: {len(materials)} 条（AI 关键词过滤）")
    return GitHubCollectorOutput(github_materials=materials)