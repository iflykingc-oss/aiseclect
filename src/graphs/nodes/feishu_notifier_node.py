"""
飞书机器人通知节点
向飞书群发送新增素材提醒（仅预览信息）
"""
import json
import logging
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import FeishuNotifierInput, FeishuNotifierOutput
from cozeloop.decorator import observe
from coze_workload_identity import Client
import requests

logger = logging.getLogger(__name__)


def feishu_notifier_node(
    state: FeishuNotifierInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> FeishuNotifierOutput:
    """
    title: 飞书机器人通知
    desc: 向飞书群发送新增素材提醒，仅显示预览信息不展示完整推文
    integrations: Feishu Message
    """
    ctx = runtime.context
    
    # 获取飞书机器人webhook URL
    client_identity = Client()
    credential = client_identity.get_integration_credential("integration-feishu-message")
    
    webhook_url = ""
    try:
        webhook_data = json.loads(credential)
        webhook_url = webhook_data.get("webhook_url", "")
    except Exception as e:
        logger.error(f"飞书凭证解析失败: {str(e)}")
        return FeishuNotifierOutput(
            notification_sent=False,
            message="飞书凭证解析失败"
        )
    
    if not webhook_url:
        return FeishuNotifierOutput(
            notification_sent=False,
            message="飞书webhook URL未配置"
        )
    
    # 构建通知消息（仅预览信息）
    message_content = f"📢 **AI资讯采集完成**\n\n"
    message_content += f"✅ 新增素材: {state.new_material_count}条\n"
    message_content += f"✅ 生成推文: {state.tweet_count}条\n"
    message_content += f"📝 记录已写入飞书表格，请及时审核\n"
    message_content += f"\n💡 提示: 请在飞书表格中查看详情并完成审核"
    
    # 发送富文本消息
    try:
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": "AI资讯采集通知",
                        "content": [
                            [
                                {"tag": "text", "text": message_content}
                            ]
                        ]
                    }
                }
            }
        }
        
        @observe
        def send_feishu_notification():
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=10
            )
            return resp.json()
        
        send_result = send_feishu_notification()
        
        # 飞书webhook成功返回StatusCode为0
        if send_result.get("StatusCode") == 0 or send_result.get("code") == 0:
            return FeishuNotifierOutput(
                notification_sent=True,
                message="通知发送成功"
            )
        else:
            logger.warning(f"飞书通知发送失败: {send_result}")
            return FeishuNotifierOutput(
                notification_sent=False,
                message=f"通知发送失败: {send_result.get('msg', '未知错误')}"
            )
    
    except Exception as e:
        logger.error(f"飞书通知发送异常: {str(e)}")
        return FeishuNotifierOutput(
            notification_sent=False,
            message=f"通知发送异常: {str(e)}"
        )