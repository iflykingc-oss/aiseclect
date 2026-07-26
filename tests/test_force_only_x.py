"""
_force_only_x 平台分流测试（2026-07-26 普通人口径重构后）

覆盖：
- 普通用户能懂的视角（ChatGPT 涨价 / 新模型发布）→ 不强制仅X
- 完全无大众价值的纯技术词（API/SDK 旧视角）→ **不再强制仅X**
- 企业 SaaS 内部更新 → 强制仅X
- 纯圈内人事/融资 → 强制仅X
- 网络工具边界（VPN/代理）+ 无普通用户场景 → 强制仅X
- 网络工具边界 + 有普通用户场景 → 不强制（可写小红书）

运行：pytest tests/test_force_only_x.py -v
"""
from __future__ import annotations

from graphs.nodes.tweet_generator_node import (
    HARD_ONLY_X_PATTERNS,
    PROXY_BOUNDARY_PATTERNS,
    SOFT_ONLY_X_PATTERNS,
    XHS_FRIENDLY_PATTERNS,
    _force_only_x,
)
from collect_pipeline.models import ScoredMaterial


def _mat(title: str, snippet: str = "", source: str = "aihot") -> ScoredMaterial:
    return ScoredMaterial(url="https://x.com/1", title=title, snippet=snippet, source=source)


# ============ 新模型/AI 工具新闻 → 不强制仅X ============

def test_new_model_release_not_forced_only_x():
    """Kimi 3 / GPT-5 / DeepSeek 发布属于「消费者新闻」，应能进小红书"""
    mat = _mat("Kimi 3 正式发布", "国产大模型升级")
    assert _force_only_x(mat) is False


def test_chatgpt_price_change_not_forced_only_x():
    """ChatGPT 涨价是普通用户关心的内容"""
    mat = _mat("ChatGPT Pro 涨价到 $25", "普通用户订阅价格变化")
    assert _force_only_x(mat) is False


def test_ai_product_launch_not_forced_only_x():
    """AI 产品新功能对普通用户有意义"""
    mat = _mat("豆包新功能：免费翻译 100 种语言", "普通人也能用")
    assert _force_only_x(mat) is False


# ============ 旧硬技术词不再自动触发仅X ============

def test_api_keyword_alone_no_longer_forces_only_x():
    """'API' 不再是硬技术关键词 —— 它可以是普通用户内容"""
    mat = _mat("ChatGPT API 涨价", "普通人调用 API 也会受影响")
    assert _force_only_x(mat) is False


def test_arxiv_alone_no_longer_forces_only_x():
    """arxiv 论文不再是自动仅X —— 普通用户对热门论文也关心"""
    mat = _mat("新论文：普通人如何用 AI 提高工作效率", "arXiv 热门研究")
    assert _force_only_x(mat) is False


# ============ 企业 SaaS → 强制仅X ============

def test_enterprise_saas_internal_update_forces_only_x():
    """Snowflake / Salesforce 等内部更新对普通用户无感"""
    mat = _mat("Snowflake 发布新功能", "企业级数据平台")
    assert _force_only_x(mat) is True


def test_kubernetes_internal_update_forces_only_x():
    mat = _mat("Kubernetes 新版本发布", "集群管理")
    assert _force_only_x(mat) is True


def test_enterprise_saas_with_xhs_friendly_escapes_only_x():
    """企业 SaaS + 有大众场景词 → 不强制仅X（罕见但合法）"""
    mat = _mat(
        "Snowflake 新增 AI 工具",
        "普通人也能用的数据可视化",
    )
    assert _force_only_x(mat) is False


# ============ 纯圈内人事/融资/收购 → 强制仅X ============

def test_acquisition_news_forces_only_x():
    mat = _mat("某 AI 公司被收购", "行业整合")
    assert _force_only_x(mat) is True


def test_ipo_news_forces_only_x():
    mat = _mat("OpenAI 启动 IPO 流程", "估值 XXX 亿美元")
    assert _force_only_x(mat) is True


def test_ceo_departure_forces_only_x():
    mat = _mat("某公司 CTO 离职", "管理层变动")
    assert _force_only_x(mat) is True


def test_industry_news_with_xhs_friendly_escapes_only_x():
    """纯圈内人事 + 有大众场景词 → 不强制仅X"""
    mat = _mat(
        "OpenAI CEO 谈普通人如何用 AI",
        "对学生和打工人有什么影响",
    )
    assert _force_only_x(mat) is False


# ============ 网络工具边界 → 强制仅X（除非有普通用户场景） ============

def test_proxy_boundary_alone_forces_only_x():
    mat = _mat("Xray 新版本发布", "代理协议升级", source="github")
    assert _force_only_x(mat) is True


def test_proxy_boundary_with_user_scenario_escapes_only_x():
    """VPN + 普通用户安全/隐私场景 → 可写小红书（生态观察）"""
    mat = _mat(
        "Xray 项目作者退出",
        "普通人需要担心隐私安全",
        source="github",
    )
    assert _force_only_x(mat) is False


def test_proxy_boundary_only_for_github_or_watchlist_sources():
    """proxy 边界仅对 github/watchlist 强制 —— 其他源不强制"""
    mat = _mat("VPN 行业观察", "生态变化")
    assert _force_only_x(mat) is False


# ============ 复合场景 ============

def test_typical_consumer_news_passes():
    """典型消费者新闻：AI 工具实测 + 普通用户场景 → 不强制"""
    mat = _mat(
        "我把 5 个 AI 工具都用了一周",
        "打工人 / 学生党 / 创作者 怎么选",
    )
    assert _force_only_x(mat) is False


def test_typical_pure_tech_news_no_longer_forced():
    """纯技术新闻（以前会强制仅X）现在不强制"""
    mat = _mat(
        "LangChain 发布新 SDK",
        "API 接口升级",
    )
    assert _force_only_x(mat) is False


def test_truly_unrelatable_content_forces_only_x():
    """完全无法转译成普通人内容 → 强制仅X"""
    mat = _mat(
        "Snowflake 发布新功能",
        "数据仓库内部优化",
    )
    assert _force_only_x(mat) is True


# ============ 模式定义完整性 ============

def test_pattern_lists_cover_key_cases():
    """回归测试：模式定义覆盖关键场景"""
    # HARD_ONLY_X_PATTERNS 应至少包含 SaaS 类
    saas_text = "Snowflake new feature"
    assert any(p.search(saas_text) for p in HARD_ONLY_X_PATTERNS)

    # SOFT_ONLY_X_PATTERNS 应至少包含融资/收购
    finance_text = "公司宣布融资 1 亿美元"
    assert any(p.search(finance_text) for p in SOFT_ONLY_X_PATTERNS)

    # PROXY_BOUNDARY_PATTERNS 应至少包含 VPN/proxy
    proxy_text = "vpn 工具发布"
    assert any(p.search(proxy_text) for p in PROXY_BOUNDARY_PATTERNS)

    # XHS_FRIENDLY_PATTERNS 应至少包含 AI 工具相关
    friendly_text = "普通人用的 AI 工具"
    assert any(p.search(friendly_text) for p in XHS_FRIENDLY_PATTERNS)