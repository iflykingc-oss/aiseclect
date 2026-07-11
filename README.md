# aiseclect

AI 资讯采集 → 双平台内容生成（X + 小红书） → 飞书 Bitable 写入（人工审核）。

不依赖扣子（Coze）平台，纯 Python + langgraph + langchain-openai。

## 工作流

```
飞书初始化 → 5 路并行采集 → 合并 → 去重 → 打分 → 清洗 → 推文生成 → 飞书写入
```

5 路采集：
- **aihot**：行业热点站点（36kr、techcrunch、aihot.net...）
- **ainews**：技术新闻/论文/官方博客（arxiv、openai、anthropic、deepmind...）
- **rss**：社区动态（medium、reddit、hackernews...）
- **tavily**：Tavily 综合搜索（不限站点）
- **github**：GitHub Trending（按 stars + AI 关键词）

**不自动发布到 X / 小红书**。生成的草稿全部进飞书表格，由人工审核后手动复制粘贴发布。

## 快速开始（本地）

```bash
# 1) 安装
uv sync

# 2) 配置
cp .env.example .env
# 编辑 .env：填 TAVILY_API_KEY / OPENAI_API_KEY；飞书凭证已经在 example 里

# 3) 跑一次（跳过飞书，只看流程通不通）
bash scripts/local_run.sh --no-feishu

# 4) 全量：采集 → 飞书写入 → 本地落盘
bash scripts/local_run.sh
```

## 定时调度（推荐 GitHub Actions）

`.github/workflows/aiseclect.yml` 已配好：
- 每 4 小时自动跑（UTC 0/4/8/12/16/20 = 北京时间 8/12/16/20/0/4）
- Actions 页面点 **Run workflow** 手动触发，可调阈值
- 跑完把 `output/` 当 artifact 上传，保留 7 天

### 部署步骤

1. 推到 GitHub
   ```bash
   git init && git add -A && git commit -m "init"
   gh repo create iflykingc-oss/aiseclect --public --source=. --push
   ```

2. 在 GitHub 仓库 **Settings → Secrets and variables → Actions** 加这些 secrets：

   | Secret | 值 |
   |--------|----|
   | `FEISHU_APP_ID` | 你的飞书自建应用 App ID（`cli_xxx`） |
   | `FEISHU_APP_SECRET` | 你的飞书 App Secret |
   | `FEISHU_PAGE_ID` | Wiki 链接里 `/wiki/` 后的 node token |
   | `FEISHU_TABLE_ID` | `?table=` 后的 table id |
   | `FEISHU_DOMAIN` | `my.feishu.cn` |
   | `TAVILY_API_KEY` | tvly-xxx |
   | `OPENAI_API_KEY` | 你的 LLM key |
   | `OPENAI_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3`（可选） |
   | `OPENAI_MODEL` | `doubao-seed-1-6-250615`（可选） |
   | `GITHUB_TOKEN` | gh 自动注入 `${{ secrets.GITHUB_TOKEN }}`，不用手填 |

3. 第一次手动跑：Actions → aiseclect → Run workflow

4. 看效果：飞书表格应自动出现新记录

### 触发频率调整

编辑 `.github/workflows/aiseclect.yml` 里的 cron：

```yaml
on:
  schedule:
    - cron: '0 */4 * * *'   # 每 4 小时
    # - cron: '0 0,12 * * *'  # 每天 0/12 点（UTC）
    # - cron: '0 8 * * *'    # 每天 8 点 UTC = 16 点北京时间
```

> ⚠️ GitHub Actions 定时器**不可靠**（队列延迟 5-30 分钟常见），不适合秒级调度。4 小时这种粒度够用。

## 飞书 Bitable 字段

第一次跑自动建字段；**单选字段无需手动加选项** —— 飞书 Bitable 在写入时自动接受任意字符串作为新选项值。

| 字段名 | 类型 | 自动/手填 | 选项 |
|--------|------|----------|------|
| 唯一ID | 文本 | 自动 | — |
| 链接 | URL | 自动 | — |
| 标题 | 文本 | 自动 | — |
| 分类 | 单选 | 自动 | 行业热点 / 技术突破 / 社区动态 / 开源项目 / 综合资讯 |
| 热度评分 | 数字 | 自动 | 0-100 |
| 推文内容 | 文本 | 自动 | X 推文正文（内心 OS 已融入首句或结尾金句） |
| 小红书标题 | 文本 | 自动 | 带 emoji |
| 小红书内容 | 文本 | 自动 | 200-300 字（仅X 时留空） |
| 小红书标签 | 文本 | 自动 | 逗号分隔（仅X 时留空） |
| **发布平台** | 单选 | 自动 | **X+小红书 / 仅X**（代码门禁 + LLM 决策） |
| **处理状态** | 单选 | 自动「待审核」；**发布后手改** | 待审核 / 已发布 / 需修改 / 驳回 |
| **审核备注** | 文本 | **手填** | 修改建议 / 发布后的 X 链接 / 备注 |
| 起号定位 | 文本 | 自动 | tutorial / spell / risk_alert 等内容支柱 |
| 笔记结构 | 文本 | 自动 | hook + step_list + copy_action 等结构提示 |
| 标题模板 | 文本 | 自动 | 教程型 / 咒语型 / 避坑型等 |
| 搜索分 / 收藏分 / 新手分 / 系列分 | 数字 | 自动 | 小红书起号复盘辅助分 |
| 起号备注 | 文本 | 自动 | 起号维度评分理由 |
| 创建时间 | 日期 | 自动 | 写入时间 |

### 审核 → 发布流程（一个字段搞定）

1. **工作流写入** → 「处理状态 = 待审核」，「发布平台」由 LLM 决策（技术类新闻自动标为「仅X」）
2. **审核推文**：打开飞书表格看推文草稿
3. **改「处理状态」**：
   - **已发布** → 你把这条发到 X 或小红书了
   - **需修改** → 想发但要改，「审核备注」写清楚哪里改
   - **驳回** → 这条不发
   - 保持「待审核」= 还没决定
4. 「审核备注」可选：想留发布链接 / 修改意见 / 数据反馈随便写
5. 想发的：按「发布平台」字段决定复制到哪些平台（仅X 时不用管小红书那三列），发完把「处理状态」改成「已发布」

## 内容质量系统

本项目现在把“生成更多内容”改为“生成更值得审核和发布的内容”。核心配置和输出：

- `config/watchlist.json`：配置长尾热点 watchlist，例如 Xray / VPN / proxy / Claude Code / Cursor / MCP 等；Tavily 与 GitHub 采集都会读取它。
- `config/content_strategy.json`：配置 X hook 类型、小红书标题/标签策略、平台分流规则、质量评分阈值。
- `collect_pipeline.humanizer`：本地中文去 AI 味后处理，减少“本质上 / 这意味着 / 未来已来”等模板句，并把 `ai_tone` 记录到质量备注。
- `content_strategy.image_prompt_rubric`：小红书封面提示词规范，约束主体、构图、配色、字体、氛围和禁用元素；当前仍输出文本提示词，不自动渲染图片。
- `content_strategy.xiaohongshu.growth_taxonomy`：小红书起号策略配置，给草稿打上内容支柱、笔记结构、标题模板、搜索/收藏/新手/系列化分，方便飞书审核和复盘。
- `output/quality_report_*.json`：每次本地落盘时生成，记录每条成功草稿的内容角度、hook 类型、平台理由、X质量分、小红书质量分、发现原因。
- `output/reject_report_*.json`：记录未生成、质量门禁失败、修复后仍失败的素材和原因，用于继续调 prompt / 策略。

飞书会写入辅助审核字段：内容角度、Hook类型、平台判断理由、X质量分、小红书质量分、质量备注、素材来源、发现原因、评分理由。

> 本系统不追踪发布后的展示、点赞、评论等数据；质量优化只服务于生成和人工审核。
>
> `x-research-skill` 与真实图像渲染已评估但暂不接入主流程：前者需要先确认 CLI/auth/output schema，后者需要确定稳定的图片 backend，避免影响 4 小时定时任务。

## 本地调试

```bash
# 先做语法检查
python -m compileall src

# 看流程通不通（不写飞书）
bash scripts/local_run.sh --no-feishu

# 清空去重，重抓所有 URL
bash scripts/local_run.sh --no-feishu --clear-dedup

# 收紧阈值（只留高分）
bash scripts/local_run.sh --no-feishu --min-heat-score 70

# 限制每个源最多 3 条
bash scripts/local_run.sh --no-feishu --max-per-source 3

# 独立表格模式（不用 Wiki 链接）
bash scripts/local_run.sh --no-wiki --feishu-app-token bascnxxx --feishu-table-id tblxxx

# 长驻循环（每 4 小时一次，开个窗口挂着）
.venv/bin/python scripts/run_loop.py --interval-hours 4
```

## 环境变量

| 环境变量 | 必填 | 默认 | 说明 |
|----------|------|------|------|
| `FEISHU_APP_ID` | ✓ | — | 飞书自建应用 App ID |
| `FEISHU_APP_SECRET` | ✓ | — | 飞书 App Secret |
| `FEISHU_PAGE_ID` | ✓ (Wiki) | — | Wiki 链接里 `/wiki/` 后的 node token |
| `FEISHU_TABLE_ID` | ✓ | — | `?table=` 后的 table id |
| `FEISHU_APP_TOKEN` | ✓ (独立) | — | 独立 Bitable app token（用 `--no-wiki`） |
| `FEISHU_DOMAIN` | ✗ | `my.feishu.cn` | 飞书域名 |
| `TAVILY_API_KEY` | ✓ | — | tavily.com 注册 |
| `OPENAI_API_KEY` | ✓ | — | LLM key（默认接火山方舟 ark- 开头） |
| `OPENAI_BASE_URL` | ✗ | `https://ark.cn-beijing.volces.com/api/plan/v3` | 任意 OpenAI 兼容端点 |
| `OPENAI_MODEL` | ✗ | `ark-code-latest` | 模型名 |
| `GITHUB_TOKEN` | ✗ | — | 提高 GitHub 限流阈值（5000/h） |
| `AISECLECT_OUTPUT_DIR` | ✗ | `output` | 本地输出目录 |
| `LOG_LEVEL` | ✗ | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `FEISHU_ALERT_WEBHOOK` | ✗ | — | 飞书自定义机器人 Webhook（不设则告警走 stdout fallback） |

## 告警配置（可选）

在飞书群添加「自定义机器人」→ 复制 Webhook URL → 写到 `.env`：

```bash
FEISHU_ALERT_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
```

不设也行，告警会 fallback 到 stdout 打印。设了之后会推送飞书卡片，4 类告警：

- 🟢 流程跑完汇总（采集/去重/推文/飞书写入数 + 飞书链接）
- 🚨 LLM 连续失败 3 次
- ⚠️ 飞书写入 0 条但本地有草稿
- ❌ 采集 / 去重 全 0 条
- 💥 主图崩溃

## 故障排查

| 现象 | 原因 / 解决 |
|------|------------|
| `OPENAI_API_KEY 未配置` | GitHub Secrets 没设 |
| `TAVILY_API_KEY 未配置` | 全部采集返回空 |
| `从 Wiki 节点获取 app_token 失败` | App 权限没勾 Bitable，去飞书开放平台改 |
| 飞书 HTTP 401 | App Secret 错，或 App 未发布 |
| 飞书 `field not found` | 第一次跑自动建字段，飞书有缓存，等几秒再跑 |
| 飞书 `单选字段写入失败` | 不会发生 —— Bitable 自动接受新选项 |
| 全部 0 推文 | 看 GitHub Actions 日志；Tavily key 错会导致空 |
| 调度不按时跑 | GitHub Actions cron 队列正常 5-30 分钟延迟 |

## 项目结构

```
aiseclect/
├── .github/workflows/aiseclect.yml   # GitHub Actions 调度
├── .env.example
├── pyproject.toml                    # 依赖：langgraph + langchain-openai + requests
├── README.md
├── config/                           # LLM 提示词
├── scripts/
│   ├── local_run.sh                  # 本地入口
│   ├── run_loop.py                   # 长驻循环
│   └── windows_task_scheduler.ps1    # Windows 任务计划（备用）
├── src/
│   ├── main.py                       # CLI 入口
│   ├── graphs/
│   │   ├── state.py                  # 11 个 Pydantic 模型
│   │   ├── graph.py                  # DAG 编排
│   │   └── nodes/                    # 11 个节点
│   └── tools/                        # Tavily / GitHub / 飞书 / LLM / 去重 / 落盘
└── output/                           # 本地推文草稿（gitignore）
```
