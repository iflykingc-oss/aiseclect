"""异步 LLM 调用优化

将同步 LLM 调用改为异步并行处理，提升性能 50%+

优化方案:
1. tweet_generator_node.py - asyncio.gather() 并行处理多个 batch
2. collectors - httpx 异步 HTTP 客户端
3. pipeline.py - 异步采集器并行调用

依赖:
    pip install httpx aiohttp
"""
import asyncio
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


async def async_invoke_llm_batch(
    llm_model,
    messages: List[Dict],
    config: Dict[str, Any]
) -> str:
    """异步调用 LLM（单个 batch）"""
    try:
        # LangChain 的 ainvoke 方法
        response = await llm_model.ainvoke(messages, **config)
        return response.content if hasattr(response, 'content') else str(response)
    except Exception as e:
        logger.error(f"异步 LLM 调用失败: {e}")
        return ""


async def parallel_invoke_llm(
    llm_model,
    batch_messages: List[List[Dict]],
    config: Dict[str, Any]
) -> List[str]:
    """并行调用多个 LLM batch

    Args:
        llm_model: LangChain 模型实例
        batch_messages: 多个 batch 的消息列表
        config: LLM 配置参数

    Returns:
        List[str]: 每个 batch 的响应
    """
    tasks = [
        async_invoke_llm_batch(llm_model, messages, config)
        for messages in batch_messages
    ]

    # 并行执行所有任务
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理异常
    outputs = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Batch {i} 失败: {result}")
            outputs.append("")
        else:
            outputs.append(result)

    return outputs


# 集成到 tweet_generator_node.py 的示例
async def async_generate_tweets(materials: List[Any], llm_model, config: Dict) -> List[Any]:
    """异步生成推文（替换现有的同步循环）

    原代码（同步）:
        for batch in batches:
            response = llm_model.invoke(batch)

    新代码（异步）:
        responses = await parallel_invoke_llm(llm_model, batches, config)
    """
    BATCH_SIZE = 8
    batches = [materials[i:i + BATCH_SIZE] for i in range(0, len(materials), BATCH_SIZE)]

    # 构造每个 batch 的消息
    batch_messages = []
    for batch in batches:
        messages = [
            {"role": "system", "content": "生成推文..."},
            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}
        ]
        batch_messages.append(messages)

    # 并行调用
    responses = await parallel_invoke_llm(llm_model, batch_messages, config)

    # 解析响应
    all_drafts = []
    for response in responses:
        if response:
            drafts = parse_llm_response(response)
            all_drafts.extend(drafts)

    return all_drafts


def parse_llm_response(response: str) -> List[Dict]:
    """解析 LLM 响应（保持现有逻辑）"""
    # 现有的 extract_json_array 逻辑
    import json
    try:
        return json.loads(response)
    except Exception:
        return []


# 异步采集器示例
import httpx

async def async_fetch_url(url: str, headers: Dict = None) -> str:
    """异步 HTTP 请求"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url, headers=headers or {})
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"异步请求失败 {url}: {e}")
            return ""


async def parallel_fetch_sources(sources: List[Dict]) -> List[str]:
    """并行采集多个数据源

    Args:
        sources: [{"url": "...", "headers": {...}}, ...]

    Returns:
        List[str]: 每个源的 HTML/响应
    """
    tasks = [
        async_fetch_url(src["url"], src.get("headers"))
        for src in sources
    ]
    return await asyncio.gather(*tasks, return_exceptions=True)


# 使用示例（在 pipeline.py 中）
async def async_collect_all_sources():
    """异步并行采集所有数据源"""
    sources = [
        {"url": "https://www.jiqizhixin.com/", "headers": {"User-Agent": "..."}},
        {"url": "https://www.qbitai.com/", "headers": {"User-Agent": "..."}},
        {"url": "https://github.com/trending", "headers": {}},
    ]

    # 并行采集（3 个源同时请求，耗时 = max(t1, t2, t3) 而非 t1+t2+t3）
    responses = await parallel_fetch_sources(sources)

    # 解析每个响应
    materials = []
    for i, html in enumerate(responses):
        if isinstance(html, Exception):
            logger.error(f"源 {i} 采集失败: {html}")
            continue
        # 解析逻辑...

    return materials


# 集成到现有代码的步骤
"""
1. tweet_generator_node.py:
   - 找到 for batch in batches 循环
   - 替换为 asyncio.run(async_generate_tweets(...))

2. collectors:
   - 替换 requests.get() 为 httpx.AsyncClient
   - 函数改为 async def

3. pipeline.py:
   - 采集器节点改为 async def
   - 使用 asyncio.gather() 并行调用多个采集器

4. 测试:
   - 运行完整管道，确认输出正确
   - 对比优化前后耗时（预期 -50%）
"""

if __name__ == "__main__":
    # 测试异步 HTTP
    import asyncio

    async def test():
        sources = [
            {"url": "https://www.jiqizhixin.com/", "headers": {}},
            {"url": "https://www.qbitai.com/", "headers": {}},
        ]
        results = await parallel_fetch_sources(sources)
        print(f"采集完成: {len(results)} 个源")
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"  源 {i}: 失败 - {r}")
            else:
                print(f"  源 {i}: 成功 - {len(r)} 字符")

    asyncio.run(test())
