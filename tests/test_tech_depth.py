"""
tech_depth 路由字段测试

覆盖：
- ScoredMaterial / StandardMaterial.tech_depth 字段存在 + 默认值
- material_merge_node._tech_depth_from_category 映射（高 tech / 低 tech / 未知）
- route_to_generator 行为（mixed 模式下 >50% XHS-friendly → xiaohongshu_generator）
- 全链路字段透传：StandardMaterial.tech_depth → ScoredMaterial.tech_depth

修复 Bug: graph.route_to_generator 读取 tech_depth 但 merge / scorer / cleaner 都未写入，
导致 mixed 模式永远 fallback 到 tweet_generator。

运行：pytest tests/test_tech_depth.py -v
"""
from __future__ import annotations

from collect_pipeline.models import ScoredMaterial, StandardMaterial
from graphs.nodes.material_merge_node import (
    _CATEGORY_TECH_DEPTH,
    _tech_depth_from_category,
    material_merge_node,
)
from graphs.nodes.material_merge_node import MaterialMergeInput


# ============ 1. 字段默认值 ============

def test_standard_material_tech_depth_default():
    m = StandardMaterial(url="https://x.com/1", title="t", source="s", category="c")
    assert m.tech_depth == 70.0


def test_scored_material_tech_depth_default():
    m = ScoredMaterial(url="https://x.com/1", title="t", source="s", category="c")
    assert m.tech_depth == 70.0


# ============ 2. 分类映射 ============

def test_tech_depth_high_tech_categories():
    """硬核分类 → tech_depth > 60"""
    assert _tech_depth_from_category("论文研究") > 60
    assert _tech_depth_from_category("模型发布") > 60
    assert _tech_depth_from_category("开源治理") > 60
    assert _tech_depth_from_category("网络工具") > 60


def test_tech_depth_xhs_friendly_categories():
    """XHS 友好分类 → tech_depth <= 60"""
    assert _tech_depth_from_category("AI 产品") <= 60
    assert _tech_depth_from_category("效率工具") <= 60
    assert _tech_depth_from_category("视频热搜") <= 60
    assert _tech_depth_from_category("大众热搜") <= 60
    assert _tech_depth_from_category("争议事件") <= 60


def test_tech_depth_unknown_category_falls_back_to_neutral():
    """未知分类 → 默认 70（tech-leaning 中性）"""
    assert _tech_depth_from_category("不存在的分类") == 70.0
    assert _tech_depth_from_category("") == 70.0


def test_tech_depth_mapping_covers_all_categories_used_by_merger():
    """merge 节点会用到的所有分类都在映射表里（除非落到默认）"""
    used_categories = {
        "安全隐私", "开源治理", "网络工具", "开源项目", "论文研究",
        "模型发布", "AI 产品", "行业热点", "效率工具", "行业动态",
        "争议事件", "多模态生成", "综合资讯",
        # newsnow 子分类
        "大众热搜", "大众讨论", "视频热搜", "社会新闻", "科技产品",
        "数码社区", "产品发布", "开发者社区", "科技资讯", "财经资讯",
        "社区热议",
    }
    mapped = set(_CATEGORY_TECH_DEPTH.keys())
    missing = used_categories - mapped
    assert not missing, f"以下分类在映射表里缺失: {missing}"


# ============ 3. merge 节点透传 ============

def test_merge_node_populates_tech_depth():
    """merge 节点应把 tech_depth 写入每条 StandardMaterial"""
    state = MaterialMergeInput(
        newsnow_materials=[
            # 来源新闻聚合，category 应被自动设为"大众热搜" → tech_depth 应低
            {
                "url": "https://example.com/news1",
                "title": "某热门新闻",
                "source": "newsnow-weibo",
                "snippet": "",
                "extra_data": {"newsnow_source": "weibo"},
            },
            # AI 模型类
            {
                "url": "https://example.com/ai1",
                "title": "GPT-5 发布",
                "source": "ainews-ai-models",
                "snippet": "",
                "extra_data": {},
            },
            # GitHub 仓库类
            {
                "url": "https://github.com/x/y",
                "title": "新工具发布",
                "source": "github-watchlist",
                "snippet": "",
                "extra_data": {},
            },
        ],
        aihot_materials=[], ainews_materials=[], rss_materials=[],
        tavily_materials=[], github_materials=[], agent_reach_materials=[],
        feedgrab_materials=[],
    )
    out = material_merge_node(state)
    assert len(out.merged_materials) == 3
    by_url = {m.url: m for m in out.merged_materials}
    # 微博热搜 → 大众热搜 → tech_depth 应 ≤ 60
    assert by_url["https://example.com/news1"].tech_depth <= 60
    # AI 模型 → 模型发布 → tech_depth 应 > 60
    assert by_url["https://example.com/ai1"].tech_depth > 60
    # GitHub → 开源项目 → tech_depth 应 > 60
    assert by_url["https://github.com/x/y"].tech_depth > 60


# ============ 4. route_to_generator 行为 ============

def test_route_to_generator_picks_xhs_when_majority_xhs_friendly():
    """mixed 模式 + >50% XHS-friendly 素材 → xiaohongshu_generator"""
    from graphs import graph as graph_module
    from graphs.state import GlobalState

    state = GlobalState(target_platform="mixed")
    state.cleaned_materials = [
        ScoredMaterial(url=f"https://x.com/{i}", title="t", source="s",
                       category="AI 产品", tech_depth=55.0)
        for i in range(6)
    ] + [
        ScoredMaterial(url="https://x.com/tech", title="t", source="s",
                       category="模型发布", tech_depth=85.0)
    ]
    # 6/7 = 85.7% 是 XHS-friendly
    decision = graph_module.route_to_generator(state)
    assert decision == "xiaohongshu_generator"


def test_route_to_generator_picks_tweet_when_majority_tech(monkeypatch):
    """mixed 模式 + >50% 硬核素材 → tweet_generator"""
    from graphs import graph as graph_module
    from graphs.state import GlobalState

    state = GlobalState(target_platform="mixed")
    state.cleaned_materials = [
        ScoredMaterial(url=f"https://x.com/{i}", title="t", source="s",
                       category="模型发布", tech_depth=85.0)
        for i in range(6)
    ] + [
        ScoredMaterial(url="https://x.com/xhs", title="t", source="s",
                       category="效率工具", tech_depth=45.0)
    ]
    decision = graph_module.route_to_generator(state)
    assert decision == "tweet_generator"


def test_route_to_generator_respects_explicit_target(monkeypatch):
    """手动模式不受 tech_depth 影响"""
    from graphs import graph as graph_module
    from graphs.state import GlobalState

    # 即便全是 XHS-friendly，--target-platform=x 强制走 tweet
    state = GlobalState(target_platform="x")
    state.cleaned_materials = [
        ScoredMaterial(url=f"https://x.com/{i}", title="t", source="s",
                       category="AI 产品", tech_depth=30.0)
        for i in range(5)
    ]
    decision = graph_module.route_to_generator(state)
    assert decision == "tweet_generator"


def test_route_to_generator_handles_empty_cleaned_materials():
    """无素材时稳定 fallback 到 tweet_generator（与原行为一致）"""
    from graphs import graph as graph_module
    from graphs.state import GlobalState

    state = GlobalState(target_platform="mixed")
    state.cleaned_materials = []
    decision = graph_module.route_to_generator(state)
    assert decision == "tweet_generator"