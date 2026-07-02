"""
飞书写入节点
- 把 tweet_drafts 写入目标 Bitable
- Wiki 内嵌模式用 app_token（已在 init 节点解析）
- 修复 Bug #2：不再把 page_id 当作 app_token 兜底
"""
from __future__ import annotations

import logging
import time
from typing import List

from graphs.state import FeishuWriterInput, FeishuWriterOutput, TweetDraft
from tools.feishu_client import FeishuClient

logger = logging.getLogger(__name__)


def _build_records(drafts: List[TweetDraft]) -> List[dict]:
    now_ms = int(time.time() * 1000)
    records: List[dict] = []
    for d in drafts:
        records.append(
            {
                "fields": {
                    "唯一ID": d.unique_id,
                    "链接": {"text": d.url, "link": d.url},
                    "标题": d.title,
                    "分类": d.category,
                    "热度评分": d.heat_score,
                    "推文内容": d.tweet_content,
                    "独立观点": d.viewpoint,
                    "小红书标题": d.xiaohongshu_title,
                    "小红书内容": d.xiaohongshu_content,
                    "小红书标签": ", ".join(d.xiaohongshu_tags) if d.xiaohongshu_tags else "",
                    "发布平台": d.platform or "X+小红书",
                    "处理状态": d.status or "待审核",
                    # 人工审核结果/备注创建时留空，等运营手动填
                    "创建时间": now_ms,
                }
            }
        )
    return records


def feishu_writer_node(state: FeishuWriterInput) -> FeishuWriterOutput:
    if not state.write_to_feishu:
        return FeishuWriterOutput(feishu_init_message="飞书写入已禁用")
    if not state.tweet_drafts:
        return FeishuWriterOutput(feishu_init_message="无推文草稿，跳过飞书写入")
    if not state.feishu_app_token or not state.feishu_table_id:
        return FeishuWriterOutput(
            feishu_init_message="飞书 app_token / table_id 缺失，跳过飞书写入"
        )

    try:
        client = FeishuClient()
    except Exception as e:
        return FeishuWriterOutput(feishu_init_message=f"飞书客户端初始化失败: {e}")

    records = _build_records(state.tweet_drafts)
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

    return FeishuWriterOutput(
        feishu_record_ids=record_ids,
        added_count=len(record_ids),
        feishu_table_url=table_url,
        feishu_init_message=f"已写入 {len(record_ids)} 条记录",
        total_tweets=len(state.tweet_drafts) or state.total_tweets,
    )
