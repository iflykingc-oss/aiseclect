## 项目概述
- **名称**: AI资讯采集生成X中文短推文工作流
- **功能**: 实现从多渠道素材采集到人工审核管理的半自动化闭环工作流，每4小时自动采集AI行业资讯，经AI处理生成280字符内的推文草稿，写入飞书多维表格待人工审核。

### 节点清单
| 节点名 | 文件位置 | 类型 | 功能描述 | 分支逻辑 | 配置文件 |
|-------|---------|------|---------|---------|---------|
| feishu_table_init | `nodes/feishu_table_init_node.py` | task | 飞书表格初始化，自动创建所需字段（唯一ID、链接、标题、分类、热度评分、推文内容、独立观点、处理状态、创建时间） | → 并行启动5个采集节点 | - |
| aihot_collector | `nodes/aihot_collector_node.py` | task | AIHOT雷达采集AI热点资讯 | → material_merge | - |
| ainews_collector | `nodes/ainews_collector_node.py` | task | AI-News雷达采集技术突破新闻 | → material_merge | - |
| rss_collector | `nodes/rss_collector_node.py` | task | RSS订阅源采集社区动态 | → material_merge | - |
| tavily_collector | `nodes/tavily_collector_node.py` | task | Tavily搜索引擎采集综合资讯（动态关键词+时间戳） | → material_merge | - |
| github_collector | `nodes/github_collector_node.py` | task | GitHub Trending采集热门AI项目 | → material_merge | - |
| material_merge | `nodes/material_merge_node.py` | task | 合并5路素材并标准化处理 | → dedup_filter | - |
| dedup_filter | `nodes/dedup_filter_node.py` | task | 双层去重（飞书表格历史链接+内存去重） | → heat_scorer | - |
| heat_scorer | `nodes/heat_scorer_node.py` | agent | AI热度打分（0-100分，评估传播价值） | → content_cleaner | `config/heat_scorer_llm_cfg.json` |
| content_cleaner | `nodes/content_cleaner_node.py` | task | 网页精读清洗提取核心内容 | → tweet_generator | - |
| tweet_generator | `nodes/tweet_generator_node.py` | agent | 生成280字符内推文草稿（含独立观点） | → feishu_writer | `config/tweet_generator_llm_cfg.json` |
| feishu_writer | `nodes/feishu_writer_node.py` | task | 写入飞书多维表格（含唯一ID、链接、标题、分类、热度评分、处理状态） | → feishu_notifier | - |
| feishu_notifier | `nodes/feishu_notifier_node.py` | task | 飞书群机器人推送通知（带表格可点击链接） | → END | - |

**类型说明**: task(task节点) / agent(大模型节点) / condition(条件分支) / looparray(列表循环) / loopcond(条件循环)

## 子图清单
无子图（主图为DAG，无循环逻辑）

## 技能使用
- **飞书表格初始化节点** (feishu_table_init): 使用 **Feishu Base** 技能自动创建表格和字段
- **采集节点** (aihot_collector/ainews_collector/rss_collector/tavily_collector/github_collector): 使用 **Web Search** 技能进行多渠道素材采集
- **去重节点** (dedup_filter): 使用 **Feishu Base** 技能查询历史链接进行全局去重
- **飞书写入节点** (feishu_writer): 使用 **Feishu Base** 技能批量写入推文草稿并获取共享链接
- **飞书通知节点** (feishu_notifier): 使用 **Feishu Message** 技能发送机器人通知（带表格链接）
- **热度打分节点** (heat_scorer): 使用 **LLM** 技能进行AI热度评分
- **推文生成节点** (tweet_generator): 使用 **LLM** 技能生成推文草稿（包含独立观点）

## 工作流流程
1. **飞书表格初始化**：feishu_table_init节点自动创建表格和字段（如果不存在）
2. **并行采集**：5路采集节点同时执行（AIHOT、AI-News、RSS、Tavily、GitHub Trending）
3. **素材合并**：material_merge节点等待所有采集节点完成后合并素材
4. **去重过滤**：dedup_filter节点比对飞书表格历史链接，过滤重复素材
5. **AI打分**：heat_scorer节点使用LLM评估素材热度（0-100分）
6. **内容清洗**：content_cleaner节点深度清洗素材内容
7. **推文生成**：tweet_generator节点生成280字符内的推文草稿（包含独立观点）
8. **飞书写入**：feishu_writer节点将推文草稿写入飞书多维表格
9. **飞书通知**：feishu_notifier节点向飞书群推送带表格链接的通知消息

## 配置要求
1. **飞书多维表格配置**：
   - 可提供现有的 `feishu_app_token` 和 `feishu_table_id`（工作流会自动补充缺失字段）
   - 如果不提供，工作流会自动创建新的表格和数据表
   - 表格字段：唯一ID、链接、标题、分类、热度评分、推文内容、独立观点、处理状态、创建时间
2. **飞书集成授权**：
   - 需要在平台上配置飞书多维表格集成（integration-feishu-base）
   - 需要配置飞书机器人集成（integration-feishu-message）
3. **定时触发器**：
   - 需要在平台上配置每4小时自动运行的定时触发器

## 防御性处理
- 如果飞书凭证缺失，表格初始化节点会跳过创建操作并返回提示信息
- 如果飞书凭证缺失，去重节点将仅进行内存去重（同批次URL去重）
- 如果飞书凭证缺失，写入节点将跳过写入操作但不中断流程
- 如果飞书凭证缺失，通知节点会使用占位链接并提示用户需授权
- 如果LLM解析失败，评分/推文节点将使用默认值生成结果
- Tavily搜索使用动态关键词（包含当前年份）确保搜索最新资讯

## 并行采集字段映射
为了避免并行执行时的字段冲突，每个采集节点使用独立的输出字段名：
- aihot_collector → aihot_materials
- ainews_collector → ainews_materials
- rss_collector → rss_materials
- tavily_collector → tavily_materials
- github_collector → github_materials

这些字段在GlobalState中定义，并在material_merge节点中统一合并。