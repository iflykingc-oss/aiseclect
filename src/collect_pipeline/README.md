# collect_pipeline

从 **aiseclect** 抽出的「采集-去重-打分-落盘」通用流水线。

## 设计原则

- **不强依赖 langgraph** —— DataInsight / BuddyJob / AIkefu 不会想引入整个 DAG 框架
- **可选 langgraph DAG** —— 想要并行 / 断点 / 可视化时单独 import dag 子模块
- **数据模型 pydantic** —— 可直接被 langgraph StateGraph 用作 state schema

## 安装

```bash
# 已在 aiseclect/src/ 下，无需额外安装

# 其他项目引用：把 src/collect_pipeline 复制过去，或：
# pip install -e .
```

可选：
```bash
pip install langgraph   # 启用 DAG 子模块
```

## 模块

| 模块 | 用途 |
|------|------|
| `models` | RawMaterial / StandardMaterial / ScoredMaterial / TweetDraft |
| `dedup` | CrossRunDedup（跨 run URL 去重，线程安全 + 文件持久化） |
| `persistence` | JSON 落盘 + quality_report / reject_report |
| `pipeline` | Pipeline（顺序执行器，不依赖 langgraph） |
| `dag` | build_collect_graph()（langgraph DAG 工厂） |
| `llm` | LLMConfig / invoke_with_retry / extract_text / extract_json_array |

## 用法

### 方式 1：Pipeline.run()（推荐起步）

```python
from collect_pipeline import Pipeline, Collector, CrossRunDedup
from pathlib import Path

dedup = CrossRunDedup(path="./dedup_state.json")

pipeline = Pipeline(
    collectors=[
        Collector("aihot", aihot_fetch),
        Collector("github", github_trending_fetch),
    ],
    dedup=dedup,
    scorer=my_scorer,
    min_score=60.0,
)

result = pipeline.run()
pipeline.save_dedup()  # 持久化去重状态

print(f"采 {result.total_collected} → 去重 {result.total_after_dedup} → 打分 {len(result.scored)}")
```

### 方式 2：langgraph DAG

```python
from collect_pipeline.dag import build_collect_graph

graph = build_collect_graph(
    collectors={"aihot": aihot_fn, "github": gh_fn},
    scorer=my_scorer,
    sink=lambda state: write_to_feishu(state),
    dedup=dedup,
)
out = graph.invoke({"max_per_source": 50, "min_heat_score": 18.0})
```

### 单独用去重

```python
from collect_pipeline import CrossRunDedup

dedup = CrossRunDedup()
new_urls = dedup.filter_new(["https://a", "https://b", "https://a"])
# → ["https://a", "https://b"]（去重 + 过滤已见）
dedup.add(new_urls)
dedup.save()
```

### 单独用落盘

```python
from collect_pipeline import persist_materials, write_quality_report, write_reject_report

persist_materials(scored_materials, prefix="materials")  # output/materials_YYYYMMDD_HHMMSS.json
write_quality_report(drafts)  # output/quality_report_*.json
write_reject_report(rejects)  # output/reject_report_*.json
```

## Demo

```bash
python -m collect_pipeline.examples.demo
```

输出：
- `output/demo_dedup.json` — 去重状态
- `output/demo_scored.json` — 打分后素材
- 控制台打印流水线耗时 / per_source 统计

## 字段类型

详见 `models.py`：

| 模型 | 关键字段 |
|------|---------|
| RawMaterial | url, title, snippet, content, source, publish_time, extra_data |
| StandardMaterial | + category |
| ScoredMaterial | + heat_score, score_reason, related_urls, cluster_size |
| TweetDraft | + tweet_content, other_*, platform, x_quality_score, xhs_quality_score |

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `COLLECT_PIPELINE_DEDUP_PATH` | `dedup_state.json` | 去重状态文件路径 |
| `COLLECT_PIPELINE_DEDUP_MAX` | `5000` | 最大历史 URL 数 |
| `COLLECT_PIPELINE_OUTPUT_DIR` | `./output` | 默认落盘目录 |

## 已接入项目

- ✅ **aiseclect**（后续将逐步迁移 src/graphs/nodes 与 src/tools 下相关模块到此包）

## 计划接入

- **DataInsight**（Next.js）— 仅用 dedup + persistence 两件套，不引入 langgraph
- **BuddyJob** — 用 Pipeline 跑东南亚求职信息聚合
- **AIkefu** — 知识库更新用 CrossRunDedup 防止重复入索引