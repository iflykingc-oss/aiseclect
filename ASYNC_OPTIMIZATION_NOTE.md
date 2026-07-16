# 异步化优化说明

## 当前实现

异步化代码框架已完成（`async_llm_optimizer.py`），但**暂不集成到主管道**，原因：

1. **风险较高**: 改动核心调用链，需充分测试
2. **ROI 中等**: 性能提升 50%，但需 2-3 天重构
3. **优先级调整**: 先完成数据源优化和 Few-shot 注入（ROI 更高）

## 优化方案

### 阶段 1: 异步采集器（推荐先做）
```python
# 影响范围小，风险低
async def parallel_collect():
    tasks = [
        jiqizhixin_collector(),
        qbitai_collector(),
        zhihu_collector()
    ]
    return await asyncio.gather(*tasks)
```

### 阶段 2: 异步 LLM 调用（风险较高）
```python
# 需要改动 tweet_generator_node.py 核心逻辑
responses = await parallel_invoke_llm(batches)
```

## 实施建议

**立即**: 完成 Task #13 (Few-shot 注入，1-2 天，质量 +10-15%)  
**短期**: 优化数据源采集器（修复 RSS/反爬问题）  
**中期**: 实施异步采集器（风险低，性能 +30%）  
**长期**: 实施异步 LLM 调用（需充分测试）

## 依赖安装

```bash
pip install httpx aiohttp
```

## 性能对比（预期）

| 场景 | 同步耗时 | 异步耗时 | 提升 |
|------|---------|---------|------|
| 3 个采集器 | 15s | 5s | 67% |
| 3 个 LLM batch | 30s | 10s | 67% |
| 完整管道 | 50s | 20s | 60% |
