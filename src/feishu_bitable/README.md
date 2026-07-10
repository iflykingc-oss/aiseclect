# feishu_bitable

从 **aiseclect** 抽出的飞书 Bitable 客户端 + 自定义机器人告警，独立可复用。

## 安装

```bash
# 方式 1：作为 aiseclect 子包（pyproject.toml 已配 src/）
uv sync

# 方式 2：其他项目引用
# 把 src/feishu_bitable 复制过去，或 pip install -e .
```

## 环境变量

| 变量 | 必填 | 用途 |
|------|------|------|
| `FEISHU_APP_ID` | ✓（写入用） | 飞书自建应用 App ID（`cli_xxx`） |
| `FEISHU_APP_SECRET` | ✓（写入用） | 飞书 App Secret |
| `FEISHU_ALERT_WEBHOOK` | ✗ | 自定义机器人 Webhook（告警用，不设走 stdout） |

## 用法

### 写入 Bitable

```python
from feishu_bitable import FeishuClient

client = FeishuClient()

# Wiki 内嵌表格：自动从 FEISHU_PAGE_ID 反查 app_token
app_token = client.get_wiki_app_token(wiki_token="wikcnxxx")
table_id = "tblxxx"

# 自动建字段（缺什么建什么）
client.ensure_fields(app_token, table_id, [
    {"name": "标题", "type": 1},      # 1=文本
    {"name": "链接", "type": 15},     # 15=URL
    {"name": "热度", "type": 2},      # 2=数字
    {"name": "分类", "type": 3},      # 3=单选（飞书免配自动接受）
])

# 批量写入
records = client.batch_create_records(app_token, table_id, [
    {"fields": {"标题": "...", "链接": {"text": "...", "link": "https://..."}, "热度": 99}}
])
```

### 告警推送

```python
from feishu_bitable import get_notifier

notifier = get_notifier(app_name="my-app")

# 业务卡片
notifier.run_summary(
    total_collected=100, total_after_dedup=80,
    total_tweets=72, feishu_written=72, feishu_url="https://..."
)

# 通用告警
notifier.interactive(title="❌ 采集 0 条", lines=["阶段: tavily"], color="red")
```

## 字段类型速查

| type | 含义 |
|------|------|
| 1 | 文本 |
| 2 | 数字 |
| 3 | 单选（**Bitable 自动接受新值，无需手动加选项**） |
| 5 | 日期（毫秒时间戳） |
| 7 | 复选 |
| 15 | URL（写入格式：`{"text": "...", "link": "https://..."}`） |
| 17 | 附件 |

## 已接入项目

- ✅ **aiseclect**（src/tools/ 旧版可删，新版 import 此包）
- 计划：**DataInsight**、**BuddyJob**、**AIkefu**、**Sales-AI-Coach**

## 测试

```bash
# 语法
python -m compileall src/feishu_bitable

# 真实环境（需配置 FEISHU_APP_ID/SECRET）
python -m feishu_bitable.examples.demo --no-wiki --app-token bascnxxx --table-id tblxxx
```