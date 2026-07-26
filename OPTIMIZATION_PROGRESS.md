# 优化进度追踪

**开始时间**: 2026-07-16
**当前状态**: 推进中（2026-07-26 更新）

---

## 已完成任务 ✅

### Task #6: 修复字数控制失效问题（P0）
- **状态**: ✅ 完成
- **结果**: 最新 quality_report 显示 0 条超长案例
- **验证**: `post_generation_check()` 已正确集成到 `_build_draft()`
- **效果**: 字数控制已生效

### Task #7: 接入机器之心数据源（P0）
- **状态**: ✅ 代码完成 + 测试覆盖
- **文件**: `jiqizhixin_collector_node.py` + `tests/test_legacy_collectors.py`
- **依赖**: 已补 `beautifulsoup4>=4.12`、`feedparser>=6` 到 pyproject
- **接入状态**: ⚠️ 签名仍为旧版（返回 `List[StandardMaterial]`），未接入 graph 主流水线
- **建议**: 重写签名为 `(state: JiqizhixinCollectorInput) -> JiqizhixinCollectorOutput` 后再接 graph

### Task #8: 接入量子位数据源（P0）
- **状态**: ✅ 代码完成 + 测试覆盖
- **文件**: `qbitai_collector_node.py` + `tests/test_legacy_collectors.py`
- **接入状态**: ⚠️ 同上，签名旧，未接 graph
- **建议**: 重写签名后再接

### Task #9: 接入知乎 AI 话题（P0）
- **状态**: ✅ 代码完成 + 测试覆盖
- **文件**: `zhihu_ai_collector_node.py` + `tests/test_legacy_collectors.py`
- **接入状态**: ⚠️ 同上，签名旧，未接 graph
- **注意**: 知乎反爬强，需要 Cookie 池 / 代理 IP 才能稳定

### Task #10: 审核队列 CLI（P0）
- **状态**: ✅ 已就绪
- **文件**: `scripts/review_cli.py`（215 行，Rich 交互）
- **覆盖**: load/save review_queue.json + 反馈日志
- **无需额外工作**

### Task #11: 验证 humanizer 效果（P1）
- **状态**: ✅ 已就绪
- **测试**: `tests/test_humanizer.py`（6 个 pytest）+ `scripts/test_humanizer_effect.py`（手动）
- **无需额外工作**

### Task #12: 异步化 LLM 调用（P1）
- **状态**: ⏸️ 显式延后
- **依据**: `ASYNC_OPTIMIZATION_NOTE.md` 明确推荐延后，优先数据源 + few-shot
- **重评估**: ROI 不高，`langchain_openai.ChatOpenAI` 已有并发能力（batch 调 `ainvoke`）

### Task #13: Few-shot 示例注入（P1）
- **状态**: ✅ 已就绪
- **配置**: `config/tweet_generator_llm_cfg.json` 的 `few_shot_examples` + Jinja 模板 `{{few_shot_examples[N].example_output}}`
- **无需额外工作**

### Task #14: Firecrawl 接入（2026-07-26 新增）
- **状态**: ✅ 完成
- **文件**: `src/tools/firecrawl.py` + `tests/test_firecrawl.py`（26 个测试）+ 集成到 `content_enricher_node.py`
- **行为**: 配置了 `FIRECRAWL_API_KEY` 时优先用 Firecrawl 拿 markdown，否则降级到 requests 兜底

### Task #15: last30days collector 接入（2026-07-26 新增）
- **状态**: ✅ 完成
- **文件**: `src/tools/last30days.py` + `src/graphs/nodes/last30days_collector_node.py` + `tests/test_last30days.py`（27 个测试）
- **接入**: graph.py 第 9 路采集，material_merge_node 已合并
- **行为**: CLI 不在时 graceful 返回空

### Task #16: route_to_generator tech_depth 字段补全（2026-07-26 新增）
- **状态**: ✅ 完成
- **修复 Bug**: graph.route_to_generator 读 `tech_depth` 但 merge / scorer / cleaner 全不写入，导致 mixed 模式永远 fallback 到 tweet_generator
- **改动**: 加 `tech_depth` 字段到 StandardMaterial / ScoredMaterial + 分类映射表 `_CATEGORY_TECH_DEPTH`（25 个分类）+ 透传全链路 + `tests/test_tech_depth.py`（11 个测试）

---

## 进行中任务 🔄

（无）

---

## 待开始任务 📋

### P0 优先级（立即执行）
- **Task #17**: 把 jiqizhixin / qbitai / zhihu 三个 collector 的签名从 `(state) -> List[StandardMaterial]` 改为新 LangGraph 节点约定 `(state: XxxCollectorInput) -> XxxCollectorOutput`，然后接入 graph.py 主流水线
  - 影响文件：state.py（加 3 对 IO 类）、graph.py（_select + add_node + add_edge）、material_merge_node.py（合并）、每个 collector 内部
  - 估计：1-2 天

---

## 技术债务

1. **旧版 collector 签名未迁移**（见 Task #17）
2. **机器之心 RSS 空结果** — 需 User-Agent 或网页抓取备选
3. **langgraph 1.0.1 弃用警告**：`allowed_objects` 默认值会在未来版本变化，建议显式传值
4. **1 个真实 TODO**：`review_queue.py:273` 周重训仍是 stub

---

**下一步**: 推进 Task #17（旧 collector 签名迁移），或视运营反馈决定是否启用 Task #12 async LLM
