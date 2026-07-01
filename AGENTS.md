## 项目概述
- **名称**: AI资讯采集生成X中文短推文工作流
- **功能**: 实现从多渠道素材采集到人工审核管理的半自动化闭环工作流，每4小时自动采集AI行业资讯，经AI处理生成280字符内的推文草稿，写入飞书多维表格待人工审核。

### 节点清单
| 节点名 | 文件位置 | 类型 | 功能描述 | 分支逻辑 | 配置文件 |
|-------|---------|------|---------|---------|---------|
| aihot_collector | `nodes/aihot_collector_node.py` | task | AIHOT雷达采集AI热点资讯 | - | - |
| ainews_collector | `nodes/ainews_collector_node.py` | task | AI-News雷达采集技术突破新闻 | - | - |
| rss_collector | `nodes/rss_collector_node.py` | task | RSS订阅源采集社区动态 | - | - |
| tavily_collector | `nodes/tavily_collector_node.py` | task | Tavily搜索引擎采集综合资讯 | - | - |
| github_collector | `nodes/github_collector_node.py` | task | GitHub Trending采集热门AI项目 | - | - |
| material_merge | `nodes/material_merge_node.py` | task | 合并5路素材并标准化处理 | - | - |
| dedup_filter | `nodes/dedup_filter_node.py` | task | 比对飞书表格历史链接去重 | - | - |
| heat_scorer | `nodes/heat_scorer_node.py` | agent | AI热度打分（评估传播价值） | - | `config/heat_scorer_llm_cfg.json` |
| content_cleaner | `nodes/content_cleaner_node.py` | task | 网页精读清洗提取核心内容 | - | - |
| tweet_generator | `nodes/tweet_generator_node.py` | agent | 生成280字符内推文草稿 | - | `config/tweet_generator_llm_cfg.json` |
| feishu_writer | `nodes/feishu_writer_node.py` | task | 写入飞书多维表格（含唯一ID、链接、标题、分类、热度评分、处理状态） | - | - |
| feishu_notifier | `nodes/feishu_notifier_node.py` | task | 飞书群机器人推送新增素材提醒 | - | - |

**类型说明**: task(task节点) / agent(大模型节点) / condition(条件分支) / looparray(列表循环) / loopcond(条件循环)

## 子图清单
无子图（主图为DAG，无循环逻辑）

## 技能使用
- **采集节点** (aihot_collector/ainews_collector/rss_collector/tavily_collector/github_collector): 使用 **Web Search** 技能进行多渠道素材采集
- **去重节点** (dedup_filter): 使用 **Feishu Base** 技能查询历史链接进行去重
- **飞书写入节点** (feishu_writer): 使用 **Feishu Base** 技能批量写入推文草稿
- **飞书通知节点** (feishu_notifier): 使用 **Feishu Message** 技能发送机器人通知
- **热度打分节点** (heat_scorer): 使用 **LLM** 技能进行AI热度评分
- **推文生成节点** (tweet_generator): 使用 **LLM** 技能生成推文草稿（包含独立观点）

## 工作流流程
1. **并行采集**：5路采集节点同时执行（AIHOT、AI-News、RSS、Tavily、GitHub Trending）
2. **素材合并**：material_merge节点等待所有采集节点完成后合并素材
3. **去重过滤**：dedup_filter节点比对飞书表格历史链接，过滤重复素材
4. **AI打分**：heat_scorer节点使用LLM评估素材热度（0-100分）
5. **内容清洗**：content_cleaner节点深度清洗素材内容
6. **推文生成**：tweet_generator节点生成280字符内的推文草稿（包含独立观点）
7. **飞书写入**：feishu_writer节点将推文草稿写入飞书多维表格
8. **飞书通知**：feishu_notifier节点向飞书群推送新增素材提醒

## 配置要求
1. **飞书多维表格配置**：
   - 需要提供真实的 `feishu_app_token` 和 `feishu_table_id`
   - 表格字段：唯一ID、链接、标题、分类、热度评分、推文内容、独立观点、处理状态
2. **飞书集成授权**：
   - 需要在平台上配置飞书多维表格集成（integration-feishu-base）
   - 需要配置飞书机器人集成（integration-feishu-message）
3. **定时触发器**：
   - 需要在平台上配置每4小时自动运行的定时触发器

## 防御性处理
- 如果飞书凭证缺失，去重节点将仅进行内存去重（同批次URL去重）
- 如果飞书凭证缺失，写入节点将跳过写入操作但不中断流程
- 如果LLM解析失败，评分/推文节点将使用默认值生成结果