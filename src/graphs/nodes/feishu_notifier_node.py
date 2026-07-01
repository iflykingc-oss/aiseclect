"""
飞书机器人通知节点
向飞书群发送新增素材提醒（包含多维表格链接）
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
    desc: 向飞书群发送新增素材提醒，包含多维表格可点击链接
    integrations: Feishu Message
    """
    ctx = runtime.context
    
    # 获取飞书机器人webhook URL
    client_identity = Client()
    credential = ""
    
    try:
        credential = client_identity.get_integration_credential("integration-feishu-message")
    except Exception as e:
        logger.warning(f"飞书消息凭证获取失败: {str(e)}")
        return FeishuNotifierOutput(
            notification_sent=False,
            message="飞书消息集成未配置"
        )
    
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
        logger.warning("飞书webhook URL为空")
        return FeishuNotifierOutput(
            notification_sent=False,
            message="飞书webhook URL未配置"
        )
    
    # 构建飞书多维表格链接
    # 使用飞书通用链接格式（会自动跳转到用户的企业飞书）
    table_url = f"https://feishu.cn/base/{state.feishu_app_token}?table={state.feishu_table_id}"
    
    # 构建通知消息（使用富文本格式，包含可点击链接）
    title_text = "AI资讯采集通知"
    
    content_lines = [
        [{"tag": "text", "text": "📢 "}],
        [{"tag": "text", "text": "AI资讯采集完成\n\n"}],
        
        [{"tag": "text", "text": "✅ "}],
        [{"tag": "text", "text": f"新增素材: {state.new_material_count}条\n"}],
        
        [{"tag": "text", "text": "✅ "}],
        [{"tag": "text", "text": f"生成推文: {state.tweet_count}条\n"}],
        
        [{"tag": "text", "text": "📝 "}],
        [{"tag": "text", "text": "记录已写入飞书表格，请及时审核\n\n"}],
        
        [{"tag": "text", "text": "🔗 "}],
        [{"tag": "a", "text": "点击查看飞书表格", "href": table_url}],
    ]
    
    # 发送富文本消息（使用飞书卡片格式）
    try:
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title_text,
                        "content": content_lines
                    }
                }
            }
        }
        
        @observe
        def send_feishu_notification():
            try:
                resp = requests.post(
                    webhook_url,
                    json=payload,
                    timeout=10
                )
                return resp.json()
            except requests.exceptions.RequestException as e:
                logger.error(f"飞书通知发送网络异常: {str(e)}")
                return {"StatusCode": -1, "msg": str(e)}
        
        send_result = send_feishu_notification()
        
        # 飞书webhook成功返回StatusCode为0
        if send_result.get("StatusCode") == 0 or send_result.get("code") == 0:
            logger.info("飞书通知发送成功")
            return FeishuNotifierOutput(
                notification_sent=True,
                message=f"通知发送成功，表格链接: {table_url}"
            )
        else:
            error_msg = send_result.get("msg", "未知错误")
            logger.warning(f"飞书通知发送失败: {error_msg}")
            return FeishuNotifierOutput(
                notification_sent=False,
                message=f"通知发送失败: {error_msg}"
            )
    
    except Exception as e:
        logger.error(f"飞书通知发送异常: {str(e)}")
        return FeishuNotifierOutput(
            notification_sent=False,
            message=f"通知发送异常: {str(e)}"
        )