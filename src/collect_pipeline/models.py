"""collect_pipeline 数据模型

通用数据形状：
- RawMaterial: 采集器原始输出（任意 source）
- StandardMaterial: 合并/标准化后（带 category）
- ScoredMaterial: 打分后（带 heat_score + score_reason）
- TweetDraft: 终态（带生成内容 + 平台标记）

字段命名遵循 pydantic BaseModel，可被 langgraph 直接用作 State。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class RawMaterial(BaseModel):
    """原始素材数据（采集节点直接产出）"""
    url: str = Field(..., description="素材链接 URL")
    title: str = Field(default="", description="素材标题")
    snippet: str = Field(default="", description="摘要/简介")
    content: str = Field(default="", description="精读正文")
    source: str = Field(default="", description="采集来源（任意命名，如 aihot/rss/github/manual）")
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
    """内容草稿（双平台：X + 小红书，或单平台）"""
    unique_id: str
    url: str
    title: str = ""
    category: str = "未分类"
    heat_score: float = 0.0
    tweet_content: str = ""              # X 平台内容
    other_title: str = ""                # 小红书标题（兼容字段名）
    other_content: str = ""              # 小红书正文
    other_tags: List[str] = Field(default_factory=list)
    image_prompt: str = ""               # 配图提示词
    platform: str = "X+小红书"
    content_angle: str = ""
    hook_type: str = ""
    platform_reason: str = ""
    x_quality_score: float = 0.0
    xhs_quality_score: float = 0.0
    quality_notes: str = ""
    source: str = ""
    score_reason: str = ""
    discovery_reason: str = ""
    status: str = "待审核"
    generated_at: str = ""
    # === 以下字段为历史遗留 / 未来预留，新代码不要写入 ===
    viewpoint: str = ""                              # LEGACY: 早期流程使用
    xiaohongshu_title: str = ""                      # LEGACY: 已被 other_title 替代
    xiaohongshu_content: str = ""                    # LEGACY: 已被 other_content 替代
    xiaohongshu_tags: List[str] = Field(default_factory=list)  # LEGACY: 已被 other_tags 替代
    is_thread: bool = False                          # RESERVED: 长推拆分（暂未启用）
    thread_parts: List[str] = Field(default_factory=list)  # RESERVED: 长推拆分（暂未启用）