"""
飞书自定义机器人 Webhook 推送（告警用）
- 文档: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
- 配置: 环境变量 FEISHU_ALERT_WEBHOOK
- 失败 fallback：网络异常时 stdout 打印，不阻塞主流程
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 8


class FeishuNotifier:
    def __init__(self, webhook: Optional[str] = None):
        self.webhook = webhook or os.getenv("FEISHU_ALERT_WEBHOOK", "")

    @property
    def enabled(self) -> bool:
        return bool(self.webhook)

    def _post(self, payload: dict) -> bool:
        if not self.enabled:
            logger.debug(f"FEISHU_ALERT_WEBHOOK 未配置，告警改 stdout: {payload.get('msg_type', '?')}")
            print(f"[ALERT/fallback] {json.dumps(payload, ensure_ascii=False)}")
            return False
        try:
            resp = requests.post(
                self.webhook,
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=WEBHOOK_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning(f"飞书 Webhook HTTP {resp.status_code}: {resp.text[:200]}")
                print(f"[ALERT/fallback] HTTP {resp.status_code}: {payload}")
                return False
            try:
                data = resp.json()
            except ValueError:
                return True
            if data.get("StatusCode", 0) not in (0, "0"):
                # 飞书自定义机器人 StatusCode=0 表示成功
                logger.warning(f"飞书 Webhook 业务错误: {data}")
                return False
            return True
        except requests.RequestException as e:
            logger.warning(f"飞书 Webhook 网络异常: {e}")
            print(f"[ALERT/fallback] network err: {payload}")
            return False

    # ---------- 消息类型 ----------

    def text(self, content: str, at_mobiles: Optional[List[str]] = None) -> bool:
        """纯文本消息。at_mobiles: @ 指定成员（手机号）。"""
        payload: dict = {"msg_type": "text", "content": {"text": content}}
        if at_mobiles:
            payload["content"]["at_mobiles"] = at_mobiles
        return self._post(payload)

    def post(self, title: str, lines: List[str]) -> bool:
        """富文本（post）消息：标题 + 文本行数组。"""
        content = [[{"tag": "text", "text": line}] for line in lines]
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": content,
                    }
                }
            },
        }
        return self._post(payload)

    def interactive(self, title: str, lines: List[str], color: str = "blue") -> bool:
        """交互式卡片消息（更醒目）。"""
        elements = [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": line},
            }
            for line in lines
        ]
        elements.append(
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "aiseclect · 自动告警"}],
            }
        )
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": color,  # blue / green / red / orange / ...
                },
                "elements": elements,
            },
        }
        return self._post(payload)

    # ---------- 业务快捷方法 ----------

    def llm_failure(self, error: str, attempts: int = 3) -> bool:
        """LLM 连续失败告警。"""
        return self.interactive(
            title="🚨 推文 LLM 失败",
            lines=[
                f"**错误**: {error[:500]}",
                f"**重试次数**: {attempts}",
                "**可能原因**: 火山方舟 API 限流 / key 失效 / 网络问题",
            ],
            color="red",
        )

    def feishu_write_zero(self, drafted: int) -> bool:
        """飞书写入 0 条但本地有草稿。"""
        return self.interactive(
            title="⚠️ 飞书写入 0 条",
            lines=[
                f"**本地推文**: {drafted} 条",
                "**飞书写入**: 0 条",
                "**可能原因**: App 权限没勾 Bitable / 字段缺失 / 单选项不存在",
            ],
            color="orange",
        )

    def zero_materials(self, stage: str = "采集") -> bool:
        """全链路 0 素材告警。"""
        return self.interactive(
            title=f"❌ {stage} 0 条",
            lines=[
                f"**阶段**: {stage}",
                "**可能原因**: 数据源 key 失效 / 限流 / SSL 阻断",
            ],
            color="red",
        )

    def run_summary(
        self,
        total_collected: int,
        total_after_dedup: int,
        total_tweets: int,
        feishu_written: int,
        feishu_url: str = "",
        dropped: int = 0,
    ) -> bool:
        """流程跑完汇总。"""
        ok = feishu_written > 0
        color = "green" if ok else "orange"
        title = f"{'✅' if ok else '⚠️'} aiseclect 跑完"
        lines = [
            f"**采集**: {total_collected} 条",
            f"**去重后**: {total_after_dedup} 条",
            f"**生成推文**: {total_tweets} 条（丢弃 {dropped}）",
            f"**飞书写入**: {feishu_written} 条",
        ]
        if feishu_url:
            lines.append(f"**[打开飞书]({feishu_url})**")
        return self.interactive(title, lines, color)


# 单例（懒加载）
_notifier: Optional[FeishuNotifier] = None


def get_notifier() -> FeishuNotifier:
    global _notifier
    if _notifier is None:
        _notifier = FeishuNotifier()
    return _notifier