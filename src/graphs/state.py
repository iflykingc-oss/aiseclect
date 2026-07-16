"""
工作流状态定义
- 5 路采集 → 合并 → 去重 → 打分 → 清洗 → 推文生成 → 飞书写入
- 飞书写入使用 Wiki 内嵌 Bitable（直接 tenant_access_token，不走 Coze）

素材数据模型（RawMaterial / StandardMaterial / ScoredMaterial / TweetDraft）
已迁移到 collect_pipeline.models，本文件 re-export 保持旧 import 路径可用。
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field

# 素材模型从 collect_pipeline.models 复用
from collect_pipeline.models import (  # noqa: F401
    RawMaterial,
    StandardMaterial,
    ScoredMaterial,
    TweetDraft,
)


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

    # 6 路采集
    aihot_materials: List[RawMaterial] = Field(default_factory=list)
    ainews_materials: List[RawMaterial] = Field(default_factory=list)
    rss_materials: List[RawMaterial] = Field(default_factory=list)
    tavily_materials: List[RawMaterial] = Field(default_factory=list)
    github_materials: List[RawMaterial] = Field(default_factory=list)
    newsnow_materials: List[RawMaterial] = Field(default_factory=list)
    agent_reach_materials: List[RawMaterial] = Field(default_factory=list, description="Agent-Reach CLI 采集（可选第 7 路）")
    feedgrab_materials: List[RawMaterial] = Field(default_factory=list, description="FeedGrab CLI 采集（可选第 8 路，mpweixin/xhs/ytb/reddit 等）")

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
    min_heat_score: float = 18.0
    max_tweets: int = 30
    clear_dedup: bool = False
    write_to_feishu: bool = True
    target_platform: str = "mixed"

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
    min_heat_score: float = Field(default=18.0, description="进入推文生成的最低热度分")
    max_tweets: int = Field(default=30, description="每轮最多生成草稿数")
    clear_dedup: bool = Field(default=False, description="是否清空历史去重状态")
    write_to_feishu: bool = Field(default=True, description="是否把推文写入飞书表格")
    write_to_local: bool = Field(default=True, description="是否本地落盘 output/tweets_*.json")

    # 平台选择
    target_platform: str = Field(default="mixed", description="内容生成目标平台: mixed(默认混合) | xiaohongshu(纯小红书) | x(纯X)")


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


class AgentReachCollectorInput(BaseModel):
    max_per_source: int = 10
    platform: str = "web"
    queries: List[str] = Field(default_factory=list, description="URL 或搜索关键词列表")


class AgentReachCollectorOutput(BaseModel):
    agent_reach_materials: List[RawMaterial] = Field(default_factory=list)


class FeedgrabCollectorInput(BaseModel):
    max_per_source: int = 10
    platform: str = "mpweixin-id"
    queries: List[str] = Field(default_factory=list, description="关键词 / 用户名 / URL 等传给 feedgrab")


class FeedgrabCollectorOutput(BaseModel):
    feedgrab_materials: List[RawMaterial] = Field(default_factory=list)


class MaterialMergeInput(BaseModel):
    aihot_materials: List[RawMaterial] = Field(default_factory=list)
    ainews_materials: List[RawMaterial] = Field(default_factory=list)
    rss_materials: List[RawMaterial] = Field(default_factory=list)
    tavily_materials: List[RawMaterial] = Field(default_factory=list)
    github_materials: List[RawMaterial] = Field(default_factory=list)
    newsnow_materials: List[RawMaterial] = Field(default_factory=list)
    agent_reach_materials: List[RawMaterial] = Field(default_factory=list)
    feedgrab_materials: List[RawMaterial] = Field(default_factory=list)


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
