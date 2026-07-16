# 数据源扩展计划（P1）

## 目标
- 从当前 3-5 个源扩展到 15+ 个高信号源
- 添加多样性评分机制
- 预期效果：内容多样性 +3-5 倍

## 新增数据源列表（10 个优先级源）

### 科技媒体（4 个）
1. **机器之心**
   - URL: https://www.jiqizhixin.com/
   - 类型: RSS/API
   - 关键词: AI 模型、研究、应用
   - 预期产出: 5-10 条/日

2. **量子位**
   - URL: https://www.qbitai.com/
   - 类型: RSS/Scraping
   - 关键词: AI 产业、公司动态
   - 预期产出: 8-12 条/日

3. **AI科技评论**
   - URL: https://www.leiphone.com/category/ai
   - 类型: RSS
   - 关键词: AI 技术深度解读
   - 预期产出: 3-5 条/日

4. **极客公园**
   - URL: https://www.geekpark.net/
   - 类型: RSS
   - 关键词: 科技产品、创业
   - 预期产出: 5-8 条/日

### 社交平台（3 个）
5. **知乎 AI 话题**
   - URL: https://www.zhihu.com/topic/19551275/hot
   - 类型: API/Scraping
   - 关键词: AI 讨论、问答
   - 预期产出: 10-15 条/日

6. **Bilibili AI UP主**
   - URL: 按 UP 主列表抓取
   - 类型: API
   - 关键词: AI 教程、测评
   - 预期产出: 5-10 条/日

7. **抖音 AI 话题**
   - URL: 话题页
   - 类型: 第三方 API
   - 关键词: AI 应用、热点
   - 预期产出: 3-5 条/日

### 国际源（3 个）
8. **Hacker News**
   - URL: https://news.ycombinator.com/
   - 类型: API (已有)
   - 增强: AI 过滤器
   - 预期产出: 8-12 条/日

9. **Reddit r/MachineLearning**
   - URL: https://www.reddit.com/r/MachineLearning/
   - 类型: API
   - 关键词: 学术、开源
   - 预期产出: 5-8 条/日

10. **Hugging Face Papers**
    - URL: https://huggingface.co/papers
    - 类型: API
    - 关键词: 最新论文
    - 预期产出: 3-5 条/日

## 实施步骤

### Phase 1: 创建采集器骨架（1-2 天）
- [ ] 创建 `jiqizhixin_collector_node.py`
- [ ] 创建 `qbitai_collector_node.py`
- [ ] 创建 `zhihu_ai_collector_node.py`
- [ ] 创建 `bilibili_ai_collector_node.py`
- [ ] 创建 `reddit_ml_collector_node.py`

### Phase 2: 实现采集逻辑（2-3 天）
- [ ] 机器之心: RSS feed 解析
- [ ] 量子位: HTML scraping + 关键词过滤
- [ ] 知乎: API 调用（需认证）
- [ ] Bilibili: API 调用
- [ ] Reddit: PRAW 库集成

### Phase 3: 添加多样性评分（1 天）
- [ ] 在 `heat_scorer_node.py` 添加 `diversity_score()` 函数
- [ ] 追踪每批次的源分布
- [ ] 目标：unique_source_ratio > 0.6

### Phase 4: 集成测试（1 天）
- [ ] 运行完整采集管道
- [ ] 验证新源数据质量
- [ ] 调整过滤阈值

## 多样性评分函数

```python
def diversity_score(articles):
    """计算素材源多样性分数
    
    Args:
        articles: ScoredMaterial 列表
        
    Returns:
        float: 多样性分数 (0-1)，目标 > 0.6
    """
    if not articles:
        return 0.0
        
    sources = [a.source for a in articles]
    unique_ratio = len(set(sources)) / len(sources)
    
    # 检测单源占比过高（>40%）
    from collections import Counter
    source_counts = Counter(sources)
    max_single_ratio = max(source_counts.values()) / len(sources)
    
    # 惩罚单源过度集中
    if max_single_ratio > 0.4:
        unique_ratio *= 0.7
        
    return unique_ratio
```

## 预期效果

| 指标 | 当前 | 目标 |
|------|------|------|
| 数据源数量 | 3-5 | 15+ |
| 每日素材量 | 50-80 | 150-200 |
| 源多样性 | ~0.4 | >0.6 |
| AI 相关性 | 40% | >80% |
| 内容重复率 | ~20% | <10% |

## 技术债务提醒
- 需要处理 API rate limit
- 知乎/B站需要 cookie 认证
- Reddit 需要申请 API key
- 抖音需要第三方服务（官方 API 不开放）

## 降级方案
如时间不足，优先实施：
1. 机器之心（RSS，最简单）
2. 量子位（Scraping）
3. Reddit（API 文档完善）
4. Hacker News AI 过滤增强（已有基础）

其余源可后续迭代添加。
