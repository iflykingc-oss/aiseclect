"""
飞书写入节点
- 把 tweet_drafts 写入目标 Bitable
- Wiki 内嵌模式用 app_token（已在 init 节点解析）
- 修复 Bug #2：不再把 page_id 当作 app_token 兜底
- 2026-07-27：支持 freshness_hours —— 飞书表里 N 小时前已存在的 URL 视为可重新入
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple
from urllib.parse import parse_qsl, urlparse, urlunparse

from graphs.state import FeishuWriterInput, FeishuWriterOutput, TweetDraft
from tools.dedup_state import DedupState
from feishu_bitable import FeishuClient

logger = logging.getLogger(__name__)


def _normalize_status(status: str) -> str:
    if status == "待发布":
        return "待审核"
    if status in ("待审核", "已发布", "需修改", "驳回"):
        return status
    return "待审核"


def _normalize_platform(platform: str) -> str:
    s = (platform or "").strip().replace(" ", "")
    if s in ("仅X", "仅x", "只发X", "只X", "Xonly", "X-only"):
        return "仅X"
    if s in ("X+小红书", "X+其他平台", "X+通用", "X+通用内容", "X+小红书内容"):
        return "X+小红书"
    logger.warning(f"未知发布平台，按仅X处理: {platform}")
    return "仅X"


def _normalize_url(url: str) -> str:
    try:
        u = urlparse(url)
    except Exception:
        return url
    qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
          if not k.lower().startswith("utm_") and k.lower() not in ("ref", "ref_source")]
    return urlunparse((u.scheme, u.netloc, u.path.rstrip("/"), u.params, "&".join(f"{k}={v}" for k, v in qs), ""))


def _extract_url_value(value) -> str:
    if isinstance(value, dict):
        return str(value.get("link") or value.get("text") or "")
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return str(first.get("link") or first.get("text") or "")
    return str(value or "")


def _existing_links(
    client: FeishuClient,
    app_token: str,
    table_id: str,
    freshness_hours: Optional[int] = None,
) -> Tuple[set[str], int, int]:
    """返回 (近期内已存在 URL 集合, 总记录数, 被 freshness 过滤的旧记录数)。

    freshness_hours = None → 全部视为已存在（最严）
    freshness_hours = 168 → 只把 7 天内的视为已存在
    """
    fresh: set[str] = set()
    total = 0
    too_old = 0
    cutoff_ms: Optional[int] = None
    if freshness_hours and freshness_hours > 0:
        cutoff_ms = int((time.time() - freshness_hours * 3600) * 1000)
    for rec in client.list_records(app_token, table_id):
        total += 1
        fields = rec.get("fields") or {}
        url = _extract_url_value(fields.get("链接"))
        if not url:
            continue
        norm = _normalize_url(url.strip())
        if cutoff_ms is not None:
            created_ms = fields.get("创建时间")
            if isinstance(created_ms, (int, float)) and created_ms < cutoff_ms:
                too_old += 1
                continue  # 旧记录视为可重新入
        fresh.add(norm)
    return fresh, total, too_old


def _build_records(drafts: List[TweetDraft], existing_links: set[str] | None = None) -> List[dict]:
    now_ms = int(time.time() * 1000)
    records: List[dict] = []
    seen_links = set(existing_links or set())
    for d in drafts:
        dedup_key = _normalize_url(d.url)
        if dedup_key in seen_links:
            logger.info(f"飞书已存在，跳过: {d.url}")
            continue
        seen_links.add(dedup_key)
        platform = _normalize_platform(d.platform)
        other_title = d.other_title if platform == "X+小红书" else ""
        other_content = d.other_content if platform == "X+小红书" else ""
        other_tags = d.other_tags if platform == "X+小红书" else []
        image_prompt = d.image_prompt if platform == "X+小红书" else ""
        records.append(
            {
                "fields": {
                    "唯一ID": d.unique_id,
                    "链接": {"text": d.url, "link": d.url},
                    "标题": d.title,
                    "分类": d.category,
                    "热度评分": d.heat_score,
                    "推文内容": d.tweet_content,
                    "小红书标题": other_title,
                    "小红书内容": other_content,
                    "小红书标签": ", ".join(other_tags) if other_tags else "",
                    "配图提示词": image_prompt,
                    "素材来源": d.source,
                    "发布平台": platform,
                    "处理状态": _normalize_status(d.status),
                    "起号定位": d.xhs_pillar,
                    "笔记结构": d.xhs_note_structure,
                    "标题模板": d.xhs_title_pattern_key,
                    "搜索分": d.xhs_search_score,
                    "收藏分": d.xhs_save_score,
                    "新手分": d.xhs_beginner_score,
                    "系列分": d.xhs_series_score,
                    "起号备注": d.xhs_growth_notes,
                    # 审核备注创建时留空，等运营手动填
                    "创建时间": now_ms,
                }
            }
        )
    return records


def feishu_writer_node(state: FeishuWriterInput) -> FeishuWriterOutput:
    if not state.write_to_feishu:
        state_obj = DedupState()
        state_obj.add(_normalize_url(d.url) for d in state.tweet_drafts)
        state_obj.save()
        return FeishuWriterOutput(feishu_init_message="飞书写入已禁用，已保存本地去重")
    if not state.tweet_drafts:
        return FeishuWriterOutput(feishu_init_message="无推文草稿，跳过飞书写入")
    if not state.feishu_init_success:
        return FeishuWriterOutput(feishu_init_message="飞书初始化失败，跳过飞书写入")
    if not state.feishu_app_token or not state.feishu_table_id:
        return FeishuWriterOutput(
            feishu_init_message="飞书 app_token / table_id 缺失，跳过飞书写入"
        )

    try:
        client = FeishuClient()
    except Exception as e:
        return FeishuWriterOutput(feishu_init_message=f"飞书客户端初始化失败: {e}")

    try:
        if state.clear_dedup:
            # clear_dedup=True：跳过飞书表 dedup 检查，让新推文强制入库（dedup_state 已被清空）
            logger.info("clear_dedup=True，跳过飞书表已有链接检查，所有推文强制入库")
            existing = set()
            existing_total = 0
            existing_too_old = 0
        else:
            existing, existing_total, existing_too_old = _existing_links(
                client,
                state.feishu_app_token,
                state.feishu_table_id,
                freshness_hours=getattr(state, "freshness_hours", None),
            )
            logger.info(
                f"飞书表已加载: 总 {existing_total} 条 / "
                f"视为已存在 {len(existing)} 条 / 视为可重入 {existing_too_old} 条"
                + (f" (freshness={getattr(state, 'freshness_hours', None)}h)" if getattr(state, "freshness_hours", None) else "")
            )
    except Exception as e:
        logger.warning(f"读取飞书已有链接失败，将继续尝试写入: {e}")
        existing = set()
        existing_total = 0
        existing_too_old = 0

    records = _build_records(state.tweet_drafts, existing_links=existing)
    skipped_existing = len(state.tweet_drafts) - len(records)
    logger.info(
        f"飞书写入准备: 草稿 {len(state.tweet_drafts)} 条 / 已有或重复跳过 {skipped_existing} 条 / 尝试写入 {len(records)} 条"
    )
    try:
        created = client.batch_create_records(
            state.feishu_app_token, state.feishu_table_id, records, with_shared_url=True
        )
    except Exception as e:
        logger.error(f"飞书写入异常: {e}")
        return FeishuWriterOutput(feishu_init_message=f"飞书写入异常: {e}")

    record_ids: List[str] = []
    table_url = ""
    for rec in created:
        rid = rec.get("record_id", "")
        if rid:
            record_ids.append(rid)
        if not table_url and rec.get("shared_url"):
            table_url = rec.get("shared_url", "")

    if not table_url and state.is_wiki_embed and state.feishu_page_id and state.feishu_table_id:
        table_url = (
            f"https://{state.feishu_domain}/wiki/{state.feishu_page_id}"
            f"?table={state.feishu_table_id}"
        )

    # 只有确认写入成功，或全部因飞书已有链接而跳过时，才持久化去重。
    if not records or len(created) >= len(records):
        state_obj = DedupState()
        state_obj.add(_normalize_url(d.url) for d in state.tweet_drafts)
        state_obj.save()

    return FeishuWriterOutput(
        feishu_record_ids=record_ids,
        added_count=len(record_ids),
        feishu_table_url=table_url,
        feishu_init_message=(
            f"草稿 {len(state.tweet_drafts)} 条，跳过已有 {skipped_existing} 条，"
            f"尝试写入 {len(records)} 条，已写入 {len(record_ids)} 条记录"
        ),
        total_tweets=len(state.tweet_drafts) or state.total_tweets,
    )
