"""
飞书机器人通知节点
推送AI资讯采集完成通知到飞书群
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
    desc: 推送AI资讯采集完成通知到飞书群，包含素材统计和表格链接
    integrations: Feishu Message
    """
    ctx = runtime.context
    
    notification_sent = False
    message = "通知发送失败"
    
    # 获取飞书机器人凭证
    client_identity = Client()
    webhook_url = ""
    
    try:
        feishu_msg_credential = client_identity.get_integration_credential("integration-feishu-message")
        webhook_key = json.loads(feishu_msg_credential).get("webhook_url", "")
        webhook_url = webhook_key
    except Exception as e:
        logger.warning(f"飞书机器人凭证获取失败: {str(e)}")
        return FeishuNotifierOutput(
            notification_sent=False,
            message=f"飞书机器人凭证获取失败: {str(e)}"
        )
    
    if not webhook_url:
        logger.warning("飞书机器人webhook_url为空")
        return FeishuNotifierOutput(
            notification_sent=False,
            message="飞书机器人webhook_url为空"
        )
    
    try:
        # 构建飞书富文本消息
        title = "AI资讯采集通知"
        
        # 构建内容段落
        content_paragraphs = [
            [
                {"tag": "text", "text": "📢 AI资讯采集完成\n\n"}
            ],
            [
                {"tag": "text", "text": f"✅ 新增素材: {state.new_material_count}条\n"}
            ],
            [
                {"tag": "text", "text": f"✅ 生成推文: {state.tweet_count}条\n"}
            ],
            [
                {"tag": "text", "text": "📝 记录已写入飞书表格，请及时审核\n\n"}
            ]
        ]
        
        # 如果有真实表格链接，添加可点击链接
        if state.feishu_table_url and state.feishu_table_url.startswith("https://"):
            content_paragraphs.append([
                {"tag": "text", "text": "🔗 "},
                {"tag": "a", "text": "点击查看飞书表格", "href": state.feishu_table_url}
            ])
            message = f"通知发送成功，表格链接: {state.feishu_table_url}"
        elif state.feishu_app_token and state.feishu_table_id:
            # 如果有app_token和table_id，构建占位链接并提示用户
            placeholder_url = f"https://feishu.cn/base/{state.feishu_app_token}?table={state.feishu_table_id}"
            content_paragraphs.append([
                {"tag": "text", "text": "⚠️ 飞书表格集成未授权，链接可能无法打开\n"}
            ])
            content_paragraphs.append([
                {"tag": "text", "text": "🔗 "},
                {"tag": "a", "text": "尝试查看表格（需授权）", "href": placeholder_url}
            ])
            message = f"通知发送成功（占位链接，飞书集成未授权）"
        else:
            # 完全没有链接信息时，显示提示
            content_paragraphs.append([
                {"tag": "text", "text": "💡 提示: 请在飞书表格中查看详情并完成审核"}
            ])
            message = "通知发送成功（无表格链接信息）"
        
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": content_paragraphs
                    }
                }
            }
        }
        
        # 发送飞书消息
        @observe
        def send_feishu_message():
            try:
                resp = requests.post(
                    webhook_url,
                    json=payload,
                    timeout=30
                )
                return resp.json()
            except requests.exceptions.RequestException as e:
                logger.error(f"发送飞书消息网络异常: {str(e)}")
                return {"StatusCode": -1, "msg": str(e)}
        
        send_result = send_feishu_message()
        
        # 飞书机器人返回格式：{"StatusCode": 0, "msg": "success"}
        if send_result.get("StatusCode") == 0 or send_result.get("code") == 0:
            notification_sent = True
            logger.info(f"飞书通知发送成功: {message}")
        else:
            error_msg = send_result.get("msg", send_result.get("StatusMessage", "未知错误"))
            message = f"飞书通知发送失败: {error_msg}"
            logger.error(message)
    
    except Exception as e:
        message = f"飞书通知发送异常: {str(e)}"
        logger.error(message)
    
    return FeishuNotifierOutput(
        notification_sent=notification_sent,
        message=message
    )