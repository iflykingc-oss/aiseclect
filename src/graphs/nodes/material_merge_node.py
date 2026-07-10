"""
素材合并节点 - 8 路 → 标准化列表
- aihot / ainews / rss / tavily / github / newsnow / agent_reach（可选第 7 路）/ feedgrab（可选第 8 路）
"""
from __future__ import annotations

import logging
from typing import List

from graphs.state import (
    MaterialMergeInput,
    MaterialMergeOutput,
    RawMaterial,
    StandardMaterial,
)

logger = logging.getLogger(__name__)


def _category_from_raw(raw: RawMaterial) -> str:
    """轻量分类：优先用源数据分类，其次按 source/title 规则兜底。"""
    extra_category_zh = (raw.extra_data or {}).get("category_zh")
    if extra_category_zh:
        return str(extra_category_zh)

    extra_category = str((raw.extra_data or {}).get("category") or "").lower()
    source = (raw.source or "").lower()
    text = f"{source} {raw.title or ''} {raw.snippet or ''}".lower()

    # NewsNow 已经在 extra_data 里带了 category，直接用（中文大众向分类最准）
    if "newsnow" in source and (raw.extra_data or {}).get("newsnow_source"):
        ns = (raw.extra_data or {}).get("newsnow_source")
        # 中文分类映射
        newsnow_category_map = {
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
        if ns in newsnow_category_map:
            return newsnow_category_map[ns]

    network_terms = (
        "xray", "project x", "xtls", "v2ray", "vless", "vmess", "reality",
        "sing-box", "clash", "mihomo", "hysteria", "trojan", "shadowsocks",
        "vpn", "proxy", "代理", "翻墙", "科学上网", "gfw", "审查", "封锁",
    )
    governance_terms = ("维护者", "作者退出", "退出中国", "转投", "俄罗斯", "伊朗", "合规", "治理")

    if any(k in text for k in ("安全", "隐私", "泄露", "后门", "漏洞", "权限", "cve")):
        return "安全隐私"
    if any(k in text for k in governance_terms):
        return "开源治理"
    if any(k in text for k in network_terms):
        return "网络工具"
    if "github" in source:
        return "开源项目"
    if "paper" in source or "paper" in extra_category or "arxiv" in text or "论文" in text:
        return "论文研究"
    if "ai-models" in source or extra_category == "ai-models" or "模型" in text:
        return "模型发布"
    if "ai-products" in source or extra_category == "ai-products" or "产品" in text:
        return "AI 产品"
    if "aihot-hot" in source or "radar-daily" in source or "hot" in source:
        return "行业热点"
    if "sspai" in source or "tool" in source or "工具" in text or "效率" in text:
        return "效率工具"
    if "qbitai" in source or "industry" in extra_category:
        return "行业动态"
    if any(k in text for k in ("翻车", "争议", "涨价", "下架", "事故")):
        return "争议事件"
    if any(k in text for k in ("视频", "图片", "图像", "音乐", "多模态", "生成")):
        return "多模态生成"
    return "综合资讯"


def material_merge_node(state: MaterialMergeInput) -> MaterialMergeOutput:
    all_materials: List[RawMaterial] = []
    all_materials.extend(state.aihot_materials)
    all_materials.extend(state.ainews_materials)
    all_materials.extend(state.rss_materials)
    all_materials.extend(state.tavily_materials)
    all_materials.extend(state.github_materials)
    all_materials.extend(state.newsnow_materials)
    # 第 7 路：agent_reach（CLI 不在时为空列表）
    if getattr(state, "agent_reach_materials", None):
        all_materials.extend(state.agent_reach_materials)
    # 第 8 路：feedgrab（CLI 不在时为空列表）
    if getattr(state, "feedgrab_materials", None):
        all_materials.extend(state.feedgrab_materials)

    merged: List[StandardMaterial] = []
    for raw in all_materials:
        if not raw.url:
            continue
        merged.append(
            StandardMaterial(
                url=raw.url,
                title=raw.title or "",
                snippet=raw.snippet or "",
                content=raw.content or "",
                source=raw.source or "",
                publish_time=raw.publish_time,
                category=_category_from_raw(raw),
                extra_data=raw.extra_data or {},
            )
        )

    logger.info(f"合并: {len(merged)} 条")
    return MaterialMergeOutput(merged_materials=merged, total_collected=len(merged))
