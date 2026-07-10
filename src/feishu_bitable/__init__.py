"""feishu_bitable — 飞书 Bitable 客户端 + 自定义机器人告警

从 aiseclect 抽出，独立可复用。
- client: 字段管理 + 记录读写（自动建字段、单选免配、Wiki 节点反查）
- notifier: 自定义机器人 Webhook 推送（卡片/富文本），失败 fallback stdout

环境变量：
    FEISHU_APP_ID / FEISHU_APP_SECRET  Bitable 写入用
    FEISHU_ALERT_WEBHOOK              自定义机器人 Webhook（可选）
"""
from .client import FeishuClient, FeishuField
from .notifier import FeishuNotifier, get_notifier

__all__ = ["FeishuClient", "FeishuField", "FeishuNotifier", "get_notifier"]