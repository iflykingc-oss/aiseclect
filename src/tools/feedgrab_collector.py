"""
FeedGrab 采集器（aiseclect 第 8 路，可选）

封装 https://github.com/iBigQiang/feedgrab （543 ⭐）的 CLI：
- 多平台：mpweixin-id / x-so / xhs-so / ytb-dlv / hn top / reddit-so / medium-user / weibo-user / feishu-wiki
- 输出：Obsidian 兼容的 Markdown（含 YAML front matter）
- 自动从 Markdown front matter 解析 url / title / source / publish_time

graceful degradation：CLI 不在就返回空列表，不阻塞主流程。

安装（需网络可达 github.com）：
    pip install "feedgrab @ git+https://github.com/iBigQiang/feedgrab.git"
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from graphs.state import RawMaterial

logger = logging.getLogger(__name__)

FEEDGRAB_TIMEOUT = 90  # feedgrab 抓单条可能较慢（含 cookie 登录态/JS 渲染）


def _feedgrab_available() -> bool:
    """检查 feedgrab CLI 是否在 PATH 里。"""
    try:
        result = subprocess.run(
            ["feedgrab", "--help"],
            capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _call_feedgrab(platform: str, *args: str) -> Optional[str]:
    """调用 feedgrab CLI，返回 stdout。失败/超时/不存在返回 None。

    显式 encoding='utf-8' + errors='replace' 避免 Windows GBK 默认编码炸掉。
    """
    try:
        result = subprocess.run(
            ["feedgrab", platform, *args],
            capture_output=True, text=True, timeout=FEEDGRAB_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            logger.warning(f"feedgrab {platform} 退出码 {result.returncode}: {result.stderr[:200]}")
            return None
        return result.stdout
    except FileNotFoundError:
        logger.info("feedgrab CLI 不在 PATH，跳过（请 `pip install \"feedgrab @ git+https://github.com/iBigQiang/feedgrab.git\"`）")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"feedgrab {platform} 超时（>{FEEDGRAB_TIMEOUT}s）")
        return None


# ---------- Markdown front matter 解析（Obsidian 兼容）----------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_URL_RE = re.compile(r"https?://[^\s\)\]>]+")


def _parse_front_matter(raw: str) -> List[RawMaterial]:
    """feedgrab 输出是 Obsidian 风格的 Markdown 文件，front matter 是 YAML。

    这里只做轻量解析（不引入 pyyaml 依赖）：
    - url: 提取
    - title: 提取（fallback 用首行 # 标题）
    - source / author / publish_time: 提取
    - body: front matter 之后的正文（snippet）
    """
    if not raw:
        return []

    # 支持单条或多条 Markdown 拼接（用 "---" 切分）
    blocks: List[str] = []
    if raw.startswith("---"):
        # 第一个 front matter 到下一个 --- 或 EOF
        m = _FRONTMATTER_RE.match(raw)
        if m:
            blocks.append(raw)
    if not blocks:
        # 兜底：整个 raw 当成单条 body，从正文里捞 URL（feedgrab <url> 模式无 front matter）
        fallback_url = ""
        for line in raw.splitlines():
            m = _URL_RE.search(line)
            if m:
                fallback_url = m.group(0)
                break
        return [
            RawMaterial(
                url=fallback_url,
                title=raw.split("\n", 1)[0].lstrip("# ").strip()[:200],
                snippet=raw[:500],
                content=raw[:2000],
                source="feedgrab",
            )
        ]

    items: List[RawMaterial] = []
    for block in blocks:
        m = _FRONTMATTER_RE.match(block)
        if not m:
            continue
        fm_text, body = m.group(1), m.group(2)

        # 极简 YAML 解析：key: value 行（不处理嵌套/数组/多行值）
        meta: dict[str, str] = {}
        for line in fm_text.splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")

        url = meta.get("url", "") or meta.get("source", "")
        title = meta.get("title", "")
        if not title:
            # fallback: 首行 # 标题
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        if not url:
            continue  # 没 URL 的不算素材

        items.append(
            RawMaterial(
                url=url,
                title=title[:200],
                snippet=body[:500].strip(),
                content=body[:2000].strip(),
                source="feedgrab",
                publish_time=meta.get("date") or meta.get("publish_time") or None,
                extra_data={"feedgrab_platform": meta.get("platform", ""), "author": meta.get("author", "")},
            )
        )
    return items


def feedgrab_collector(
    platform: str = "mpweixin-id",
    *args: str,
    max_results: int = 10,
) -> List[RawMaterial]:
    """主入口。

    两种调用方式：
    1) 单 URL：`feedgrab_collector("https://example.com/x")` → 自动判定为 URL
    2) 子命令：`feedgrab_collector("mpweixin-id", "公众号名", max_results=5)`
       或 `feedgrab_collector("x-so", "关键词", max_results=5)`

    Args:
        platform: feedgrab 子命令 或 单个 URL（http/https 开头）
        args: 传给 feedgrab 的额外参数
        max_results: 最大返回条数

    Returns:
        RawMaterial 列表；CLI 不可用时返回 []
    """
    if not _feedgrab_available():
        return []

    # 检测：第一个参数是 URL 就直接调 `feedgrab <url>`，不要 platform 前缀
    if platform.startswith(("http://", "https://")):
        cmd = ["feedgrab", platform]
    else:
        cmd = ["feedgrab", platform, *args]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FEEDGRAB_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            logger.warning(f"feedgrab 退出码 {result.returncode}: {result.stderr[:200]}")
            return []
        raw = result.stdout
    except FileNotFoundError:
        return []
    except subprocess.TimeoutExpired:
        logger.warning(f"feedgrab 超时（>{FEEDGRAB_TIMEOUT}s）")
        return []

    items = _parse_front_matter(raw or "")
    items = items[:max_results]
    logger.info(f"feedgrab[{platform}] 抓到 {len(items)} 条")
    return items