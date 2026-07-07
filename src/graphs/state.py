"""
工作流状态定义
- 5 路采集 → 合并 → 去重 → 打分 → 清洗 → 推文生成 → 飞书写入
- 飞书写入使用 Wiki 内嵌 Bitable（直接 tenant_access_token，不走 Coze）
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


# ========== 素材数据结构 ==========

class RawMaterial(BaseModel):
    """原始素材数据（采集节点直接产出）"""
    url: str = Field(..., description="素材链接 URL")
    title: str = Field(default="", description="素材标题")
    snippet: str = Field(default="", description="摘要/简介")
    content: str = Field(default="", description="精读正文（采集时由 Tavily raw_content 填充）")
    source: str = Field(default="", description="采集来源（aihot/ainews/rss/tavily/github）")
    publish_time: Optional[str] = Field(default=None, description="发布时间")
    extra_data: dict = Field(default_factory=dict, description="额外数据")


class StandardMaterial(BaseModel):
    """标准化素材（合并节点产出）"""
    url: str
    title: str = ""
    snippet: str = ""
    content: str = ""
    source: str = ""
    publish_time: Optional[str] = None
    category: str = "未分类"
    extra_data: dict = Field(default_factory=dict, description="采集阶段保留的来源元数据")


class ScoredMaterial(BaseModel):
    """打分 + 清洗后的素材"""
    url: str
    title: str = ""
    snippet: str = ""
    content: str = ""
    source: str = ""
    publish_time: Optional[str] = None
    category: str = "未分类"
    heat_score: float = 0.0
    score_reason: str = ""
    extra_data: dict = Field(default_factory=dict, description="采集阶段保留的来源元数据")
    related_urls: List[str] = Field(default_factory=list, description="同事件其他来源 URL（聚类后填充）")
    cluster_size: int = 1


class TweetDraft(BaseModel):
    """内容草稿（X + 小红书）"""
    unique_id: str
    url: str
    title: str = ""
    category: str = "未分类"
    heat_score: float = 0.0
    tweet_content: str = ""              # X 平台内容
    other_title: str = ""                # 小红书标题（历史字段名保留为 other_* 兼容）
    other_content: str = ""              # 小红书正文（可由人工复用到其他平台）
    other_tags: List[str] = Field(default_factory=list)
    image_prompt: str = ""               # 配图提示词（仅小红书内容需要）
    platform: str = "X+小红书"            # 发布平台：X+小红书 / 仅X
    content_angle: str = ""               # 内容角度：risk_alert / tool_use_case / ecosystem_shift 等
    hook_type: str = ""                   # X 首行 hook 类型
    platform_reason: str = ""             # 平台分流理由
    x_quality_score: float = 0.0           # X 内容质量分（0-100）
    xhs_quality_score: float = 0.0         # 小红书内容质量分（0-100，仅X 时为 0）
    quality_notes: str = ""               # 质量门禁/重写说明
    source: str = ""                      # 素材来源，便于飞书审核和复盘
    score_reason: str = ""                # 热度评分理由
    discovery_reason: str = ""            # 发现原因：watchlist 命中词 / 来源数 / query 等
    # 历史兼容字段：新流程不再写入飞书
    viewpoint: str = ""
    xiaohongshu_title: str = ""
    xiaohongshu_content: str = ""
    xiaohongshu_tags: List[str] = Field(default_factory=list)
    # Thread 长推预留字段（暂未启用；当 tweet_content 超长/信息密度高时可拆分）
    is_thread: bool = False
    thread_parts: List[str] = Field(default_factory=list)
    status: str = "待审核"
    generated_at: str = ""


# ========== 全局状态 ==========

class GlobalState(BaseModel):
    # 飞书配置
    feishu_app_token: str = Field(default="", description="飞书 Bitable app_token")
    feishu_table_id: str = Field(default="", description="飞书 table_id")
    feishu_page_id: str = Field(default="", description="飞书 Wiki 节点 token（Wiki 内嵌表格）")
    is_wiki_embed: bool = Field(default=False, description="是否为 Wiki 内嵌表格")
    feishu_domain: str = Field(default="my.feishu.cn", description="飞书企业域名")
    feishu_init_success: bool = Field(default=False, description="飞书初始化是否成功")
    feishu_init_message: str = Field(default="", description="飞书初始化消息")
    feishu_fields_created: List[str] = Field(default_factory=list)
    feishu_record_ids: List[str] = Field(default_factory=list)
    feishu_table_url: str = Field(default="", description="飞书记录/表格链接（写入后填充）")

    # 5 路采集
    aihot_materials: List[RawMaterial] = Field(default_factory=list)
    ainews_materials: List[RawMaterial] = Field(default_factory=list)
    rss_materials: List[RawMaterial] = Field(default_factory=list)
    tavily_materials: List[RawMaterial] = Field(default_factory=list)
    github_materials: List[RawMaterial] = Field(default_factory=list)
    newsnow_materials: List[RawMaterial] = Field(default_factory=list)

    # 中间结果
    merged_materials: List[StandardMaterial] = Field(default_factory=list)
    deduplicated_materials: List[StandardMaterial] = Field(default_factory=list)
    scored_materials: List[ScoredMaterial] = Field(default_factory=list)
    cleaned_materials: List[ScoredMaterial] = Field(default_factory=list)

    # 最终结果
    tweet_drafts: List[TweetDraft] = Field(default_factory=list)

    # 统计
    total_collected: int = 0
    total_after_dedup: int = 0
    total_after_score: int = 0
    total_tweets: int = 0
    duplicates_count: int = 0

    # 行为控制（从 GraphInput 透传）
    max_per_source: int = 10
    min_heat_score: float = 30.0
    max_tweets: int = 18
    clear_dedup: bool = False
    write_to_feishu: bool = True

    # 落盘
    output_path: str = ""
    reject_events: List[dict] = Field(default_factory=list, description="内容生成阶段被拒绝/丢弃的素材诊断")
    run_message: str = ""


# ========== 图输入输出 ==========

class GraphInput(BaseModel):
    """工作流输入（全部可选）"""
    # 飞书配置（空字符串表示未配置；不写入飞书时整段可忽略）
    feishu_app_token: str = Field(default="", description="飞书 Bitable app_token（独立表格时使用）")
    feishu_table_id: str = Field(default="", description="飞书 table_id")
    feishu_page_id: str = Field(default="", description="飞书 Wiki 节点 token（Wiki 内嵌表格）")
    is_wiki_embed: bool = Field(default=True, description="是否为 Wiki 内嵌表格")
    feishu_domain: str = Field(default="my.feishu.cn", description="飞书企业域名")

    # 行为配置
    max_per_source: int = Field(default=10, description="每个采集源最大条数")
    min_heat_score: float = Field(default=30.0, description="进入推文生成的最低热度分")
    max_tweets: int = Field(default=18, description="每轮最多生成草稿数")
    clear_dedup: bool = Field(default=False, description="是否清空历史去重状态")
    write_to_feishu: bool = Field(default=True, description="是否把推文写入飞书表格")
    write_to_local: bool = Field(default=True, description="是否本地落盘 output/tweets_*.json")


class GraphOutput(BaseModel):
    """工作流输出"""
    total_collected: int = 0
    total_after_dedup: int = 0
    total_tweets: int = 0
    tweet_drafts: List[TweetDraft] = Field(default_factory=list)
    reject_events: List[dict] = Field(default_factory=list)
    output_path: str = ""
    feishu_table_url: str = ""
    feishu_record_ids: List[str] = Field(default_factory=list)
    message: str = ""


# ========== 各节点输入输出 ==========

class AIHotCollectorInput(BaseModel):
    max_per_source: int = 10


class AIHotCollectorOutput(BaseModel):
    aihot_materials: List[RawMaterial] = Field(default_factory=list)


class AINewsCollectorInput(BaseModel):
    max_per_source: int = 10


class AINewsCollectorOutput(BaseModel):
    ainews_materials: List[RawMaterial] = Field(default_factory=list)


class RSSCollectorInput(BaseModel):
    max_per_source: int = 10


class RSSCollectorOutput(BaseModel):
    rss_materials: List[RawMaterial] = Field(default_factory=list)


class TavilyCollectorInput(BaseModel):
    max_per_source: int = 10


class TavilyCollectorOutput(BaseModel):
    tavily_materials: List[RawMaterial] = Field(default_factory=list)


class GitHubCollectorInput(BaseModel):
    max_per_source: int = 10


class GitHubCollectorOutput(BaseModel):
    github_materials: List[RawMaterial] = Field(default_factory=list)


class NewsNowCollectorInput(BaseModel):
    max_per_source: int = 10


class NewsNowCollectorOutput(BaseModel):
    newsnow_materials: List[RawMaterial] = Field(default_factory=list)


class MaterialMergeInput(BaseModel):
    aihot_materials: List[RawMaterial] = Field(default_factory=list)
    ainews_materials: List[RawMaterial] = Field(default_factory=list)
    rss_materials: List[RawMaterial] = Field(default_factory=list)
    tavily_materials: List[RawMaterial] = Field(default_factory=list)
    github_materials: List[RawMaterial] = Field(default_factory=list)
    newsnow_materials: List[RawMaterial] = Field(default_factory=list)


class MaterialMergeOutput(BaseModel):
    merged_materials: List[StandardMaterial] = Field(default_factory=list)
    total_collected: int = 0


class DedupFilterInput(BaseModel):
    merged_materials: List[StandardMaterial] = Field(default_factory=list)
    clear_dedup: bool = False


class DedupFilterOutput(BaseModel):
    deduplicated_materials: List[StandardMaterial] = Field(default_factory=list)
    duplicates_count: int = 0
    new_count: int = 0
    total_after_dedup: int = 0


class HeatScorerInput(BaseModel):
    deduplicated_materials: List[StandardMaterial] = Field(default_factory=list)


class HeatScorerOutput(BaseModel):
    scored_materials: List[ScoredMaterial] = Field(default_factory=list)
    high_score_count: int = 0
    total_after_score: int = 0


class EventClusterInput(BaseModel):
    scored_materials: List[ScoredMaterial] = Field(default_factory=list)


class EventClusterOutput(BaseModel):
    # 覆盖式：用聚类后的代表条替换原 scored_materials
    scored_materials: List[ScoredMaterial] = Field(default_factory=list)
    total_after_score: int = 0
    clustered_count: int = 0  # 合并掉的重复事件数


class ContentEnricherInput(BaseModel):
    scored_materials: List[ScoredMaterial] = Field(default_factory=list)


class ContentEnricherOutput(BaseModel):
    # 覆盖式：抓不到正文的直接丢弃，不硬编
    scored_materials: List[ScoredMaterial] = Field(default_factory=list)
    enriched_count: int = 0   # 成功抓到正文并补齐的数量
    dropped_count: int = 0    # 抓不到正文被丢弃的数量


class ContentCleanerInput(BaseModel):
    scored_materials: List[ScoredMaterial] = Field(default_factory=list)


class ContentCleanerOutput(BaseModel):
    cleaned_materials: List[ScoredMaterial] = Field(default_factory=list)


class TweetGeneratorInput(BaseModel):
    cleaned_materials: List[ScoredMaterial] = Field(default_factory=list)
    min_heat_score: float = 0.0
    max_tweets: int = 18


class TweetGeneratorOutput(BaseModel):
    tweet_drafts: List[TweetDraft] = Field(default_factory=list)
    total_tweets: int = 0
    other_platform_count: int = 0
    reject_events: List[dict] = Field(default_factory=list)


# ========== 飞书节点 ==========

class FeishuTableInitInput(BaseModel):
    feishu_app_token: Optional[str] = None
    feishu_table_id: Optional[str] = None
    feishu_page_id: Optional[str] = None
    is_wiki_embed: bool = True
    feishu_domain: str = "my.feishu.cn"
    write_to_feishu: bool = True


class FeishuTableInitOutput(BaseModel):
    """输出字段名与 GlobalState 严格对齐（修复 Bug #1）"""
    feishu_app_token: str = ""
    feishu_table_id: str = ""
    feishu_page_id: str = ""
    is_wiki_embed: bool = False
    feishu_domain: str = "my.feishu.cn"
    feishu_fields_created: List[str] = Field(default_factory=list)
    feishu_init_success: bool = False
    feishu_init_message: str = ""


class FeishuWriterInput(BaseModel):
    tweet_drafts: List[TweetDraft] = Field(default_factory=list)
    feishu_app_token: str = ""
    feishu_table_id: str = ""
    feishu_page_id: str = ""
    is_wiki_embed: bool = False
    feishu_domain: str = "my.feishu.cn"
    feishu_init_success: bool = False
    write_to_feishu: bool = True
    clear_dedup: bool = False  # True 时跳过飞书表已有 URL 检查
    total_tweets: int = 0


class FeishuWriterOutput(BaseModel):
    feishu_record_ids: List[str] = Field(default_factory=list)
    added_count: int = 0
    feishu_table_url: str = ""
    feishu_init_message: str = ""
    total_tweets: int = 0
