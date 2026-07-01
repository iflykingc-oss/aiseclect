"""
AI资讯采集生成X中文短推文工作流的状态定义
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ========== 素材数据结构 ==========

class RawMaterial(BaseModel):
    """原始素材数据"""
    url: str = Field(..., description="素材链接URL")
    title: str = Field(default="", description="素材标题")
    snippet: str = Field(default="", description="素材摘要/描述")
    source: str = Field(default="", description="采集来源（aihot/ainews/rss/tavily/github）")
    publish_time: Optional[str] = Field(default=None, description="发布时间")
    extra_data: Dict[str, Any] = Field(default={}, description="额外数据")


class StandardMaterial(BaseModel):
    """标准化素材数据"""
    url: str = Field(..., description="素材链接URL")
    title: str = Field(default="", description="素材标题")
    snippet: str = Field(default="", description="素材摘要")
    source: str = Field(default="", description="采集来源")
    publish_time: Optional[str] = Field(default=None, description="发布时间")
    content: Optional[str] = Field(default=None, description="清洗后的详细内容")
    category: str = Field(default="未分类", description="资讯分类")


class ScoredMaterial(BaseModel):
    """打分后的素材数据"""
    url: str = Field(..., description="素材链接URL")
    title: str = Field(default="", description="素材标题")
    snippet: str = Field(default="", description="素材摘要")
    source: str = Field(default="", description="采集来源")
    publish_time: Optional[str] = Field(default=None, description="发布时间")
    content: Optional[str] = Field(default=None, description="清洗后的详细内容")
    category: str = Field(default="未分类", description="资讯分类")
    heat_score: float = Field(default=0.0, description="AI热度评分（0-100）")
    score_reason: str = Field(default="", description="评分理由")


class TweetDraft(BaseModel):
    """推文草稿（双平台支持）"""
    unique_id: str = Field(..., description="唯一ID")
    url: str = Field(..., description="素材链接URL")
    title: str = Field(default="", description="素材标题")
    category: str = Field(default="未分类", description="资讯分类")
    heat_score: float = Field(default=0.0, description="热度评分")
    tweet_content: str = Field(..., description="X平台推文内容（280字符内）")
    viewpoint: str = Field(default="", description="独立观点")
    xiaohongshu_title: str = Field(default="", description="小红书标题（简短吸引人的标题）")
    xiaohongshu_content: str = Field(default="", description="小红书短内容（200-300字，包含emoji、话题标签）")
    xiaohongshu_tags: List[str] = Field(default=[], description="小红书话题标签（#标签列表）")
    status: str = Field(default="待审核", description="处理状态（待审核/待发布/已发布）")


# ========== 全局状态 ==========

class GlobalState(BaseModel):
    """全局状态定义"""
    # 飞书表格信息
    feishu_app_token: str = Field(default="", description="飞书多维表格App Token（独立表格）")
    feishu_table_id: str = Field(default="", description="飞书数据表ID")
    feishu_page_id: str = Field(default="", description="飞书Wiki页面ID（Wiki内嵌表格）")
    is_wiki_embed: bool = Field(default=False, description="是否为Wiki内嵌表格")
    feishu_domain: str = Field(default="my.feishu.cn", description="飞书企业域名")
    feishu_table_url: str = Field(default="", description="飞书表格共享链接（用于通知）")
    fields_created: List[str] = Field(default=[], description="飞书表格新创建的字段列表")
    init_success: bool = Field(default=False, description="飞书表格初始化是否成功")
    init_message: str = Field(default="", description="飞书表格初始化消息")
    
    # 采集结果
    aihot_materials: List[RawMaterial] = Field(default=[], description="AIHOT雷达采集素材")
    ainews_materials: List[RawMaterial] = Field(default=[], description="AI-News雷达采集素材")
    rss_materials: List[RawMaterial] = Field(default=[], description="RSS采集素材")
    tavily_materials: List[RawMaterial] = Field(default=[], description="Tavily搜索采集素材")
    github_materials: List[RawMaterial] = Field(default=[], description="GitHub Trending采集素材")
    
    # 处理中间结果
    merged_materials: List[StandardMaterial] = Field(default=[], description="合并后的标准化素材")
    deduplicated_materials: List[StandardMaterial] = Field(default=[], description="去重后的素材")
    scored_materials: List[ScoredMaterial] = Field(default=[], description="打分后的素材")
    cleaned_materials: List[ScoredMaterial] = Field(default=[], description="清洗后的素材")
    
    # 最终结果
    tweet_drafts: List[TweetDraft] = Field(default=[], description="生成的推文草稿")
    added_record_ids: List[str] = Field(default=[], description="飞书表格新增记录ID")
    
    # 统计信息
    total_collected: int = Field(default=0, description="采集素材总数")
    total_after_dedup: int = Field(default=0, description="去重后素材数量")
    total_after_score: int = Field(default=0, description="打分筛选后素材数量")
    total_tweets: int = Field(default=0, description="生成推文数量")
    
    # 状态标记
    notification_sent: bool = Field(default=False, description="飞书通知发送状态")


# ========== 图输入输出 ==========

class GraphInput(BaseModel):
    """工作流输入"""
    feishu_app_token: Optional[str] = Field(default=None, description="飞书多维表格App Token（独立表格）")
    feishu_table_id: str = Field(..., description="飞书数据表ID")
    feishu_page_id: Optional[str] = Field(default=None, description="飞书Wiki页面ID（Wiki内嵌表格）")
    is_wiki_embed: bool = Field(default=False, description="是否为Wiki内嵌表格")
    feishu_domain: str = Field(default="my.feishu.cn", description="飞书企业域名")


class GraphOutput(BaseModel):
    """工作流输出"""
    total_tweets: int = Field(default=0, description="生成推文数量")
    total_after_dedup: int = Field(default=0, description="去重后素材数量")
    added_record_ids: List[str] = Field(default=[], description="飞书表格新增记录ID")
    notification_sent: bool = Field(default=False, description="飞书通知发送状态")
    message: str = Field(default="", description="执行结果消息")


# ========== 各节点输入输出定义 ==========

# 1. 采集节点（5个并行）

class AIHotCollectorInput(BaseModel):
    """AIHOT雷达采集节点输入"""
    pass


class AIHotCollectorOutput(BaseModel):
    """AIHOT雷达采集节点输出"""
    aihot_materials: List[RawMaterial] = Field(default=[], description="采集的素材列表")


class AINewsCollectorInput(BaseModel):
    """AI-News雷达采集节点输入"""
    pass


class AINewsCollectorOutput(BaseModel):
    """AI-News雷达采集节点输出"""
    ainews_materials: List[RawMaterial] = Field(default=[], description="采集的素材列表")


class RSSCollectorInput(BaseModel):
    """RSS采集节点输入"""
    pass


class RSSCollectorOutput(BaseModel):
    """RSS采集节点输出"""
    rss_materials: List[RawMaterial] = Field(default=[], description="采集的素材列表")


class TavilyCollectorInput(BaseModel):
    """Tavily搜索采集节点输入"""
    pass


class TavilyCollectorOutput(BaseModel):
    """Tavily搜索采集节点输出"""
    tavily_materials: List[RawMaterial] = Field(default=[], description="采集的素材列表")


class GitHubCollectorInput(BaseModel):
    """GitHub Trending采集节点输入"""
    pass


class GitHubCollectorOutput(BaseModel):
    """GitHub Trending采集节点输出"""
    github_materials: List[RawMaterial] = Field(default=[], description="采集的素材列表")


# 2. 素材合并节点

class MaterialMergeInput(BaseModel):
    """素材合并节点输入"""
    aihot_materials: List[RawMaterial] = Field(default=[], description="AIHOT雷达素材")
    ainews_materials: List[RawMaterial] = Field(default=[], description="AI-News雷达素材")
    rss_materials: List[RawMaterial] = Field(default=[], description="RSS素材")
    tavily_materials: List[RawMaterial] = Field(default=[], description="Tavily素材")
    github_materials: List[RawMaterial] = Field(default=[], description="GitHub素材")


class MaterialMergeOutput(BaseModel):
    """素材合并节点输出"""
    merged_materials: List[StandardMaterial] = Field(default=[], description="合并后的标准化素材")
    total_count: int = Field(default=0, description="素材总数")


# 3. 去重过滤节点

class DedupFilterInput(BaseModel):
    """去重过滤节点输入"""
    merged_materials: List[StandardMaterial] = Field(default=[], description="合并后的素材")
    feishu_app_token: str = Field(default="", description="飞书多维表格App Token（可选）")
    feishu_table_id: str = Field(default="", description="飞书数据表ID（可选）")


class DedupFilterOutput(BaseModel):
    """去重过滤节点输出"""
    deduplicated_materials: List[StandardMaterial] = Field(default=[], description="去重后的素材")
    duplicates_count: int = Field(default=0, description="重复素材数量")
    new_count: int = Field(default=0, description="新素材数量")


# 4. AI热度打分节点

class HeatScorerInput(BaseModel):
    """AI热度打分节点输入"""
    materials: List[StandardMaterial] = Field(default=[], description="待打分的素材列表")


class HeatScorerOutput(BaseModel):
    """AI热度打分节点输出"""
    scored_materials: List[ScoredMaterial] = Field(default=[], description="打分后的素材列表")
    high_score_count: int = Field(default=0, description="高分素材数量")


# 5. 网页精读清洗节点

class ContentCleanerInput(BaseModel):
    """网页精读清洗节点输入"""
    materials: List[ScoredMaterial] = Field(default=[], description="待清洗的素材列表")


class ContentCleanerOutput(BaseModel):
    """网页精读清洗节点输出"""
    cleaned_materials: List[ScoredMaterial] = Field(default=[], description="清洗后的素材列表")


# 6. 推文生成节点

class TweetGeneratorInput(BaseModel):
    """推文生成节点输入"""
    materials: List[ScoredMaterial] = Field(default=[], description="待生成推文的素材列表")


class TweetGeneratorOutput(BaseModel):
    """推文生成节点输出（双平台支持）"""
    tweet_drafts: List[TweetDraft] = Field(default=[], description="生成的推文草稿")
    total_count: int = Field(default=0, description="推文总数")
    xiaohongshu_count: int = Field(default=0, description="小红书内容数量")


# 7. 飞书表格初始化节点

class FeishuTableInitInput(BaseModel):
    """飞书表格初始化节点输入"""
    feishu_app_token: str = Field(default="", description="飞书表格app_token（可选，未提供则自动创建）")
    feishu_table_id: str = Field(default="", description="飞书数据表ID（可选，未提供则自动创建）")
    feishu_page_id: str = Field(default="", description="飞书Wiki页面ID（Wiki内嵌表格）")
    is_wiki_embed: bool = Field(default=False, description="是否为Wiki内嵌表格")
    feishu_domain: str = Field(default="my.feishu.cn", description="飞书企业域名")


class FeishuTableInitOutput(BaseModel):
    """飞书表格初始化节点输出"""
    app_token: str = Field(default="", description="飞书表格app_token（初始化后的实际值）")
    table_id: str = Field(default="", description="飞书数据表ID（初始化后的实际值）")
    page_id: str = Field(default="", description="飞书Wiki页面ID")
    is_wiki_embed: bool = Field(default=False, description="是否为Wiki内嵌表格")
    feishu_domain: str = Field(default="my.feishu.cn", description="飞书企业域名")
    fields_created: List[str] = Field(default=[], description="新创建的字段名称列表")
    init_success: bool = Field(default=False, description="初始化是否成功")
    message: str = Field(default="", description="初始化结果消息")


# 8. 飞书表格写入节点

class FeishuWriterInput(BaseModel):
    """飞书表格写入节点输入"""
    tweet_drafts: List[TweetDraft] = Field(default=[], description="待写入的推文草稿")
    feishu_app_token: str = Field(default="", description="飞书多维表格App Token（可选）")
    feishu_table_id: str = Field(default="", description="飞书数据表ID（可选）")
    feishu_page_id: str = Field(default="", description="飞书Wiki页面ID（可选）")
    is_wiki_embed: bool = Field(default=False, description="是否为Wiki内嵌表格")
    feishu_domain: str = Field(default="my.feishu.cn", description="飞书企业域名")


class FeishuWriterOutput(BaseModel):
    """飞书表格写入节点输出"""
    added_record_ids: List[str] = Field(default=[], description="新增记录ID列表")
    added_count: int = Field(default=0, description="新增记录数量")
    feishu_table_url: str = Field(default="", description="飞书表格共享链接（如果有）")
    feishu_table_url: str = Field(default="", description="飞书表格共享链接")


# 8. 飞书通知节点

class FeishuNotifierInput(BaseModel):
    """飞书通知节点输入"""
    new_material_count: int = Field(default=0, description="新增素材数量")
    tweet_count: int = Field(default=0, description="生成推文数量")
    added_record_ids: List[str] = Field(default=[], description="新增记录ID")
    feishu_table_url: str = Field(default="", description="飞书表格共享链接（从写入节点获取）")
    feishu_app_token: str = Field(default="", description="飞书表格app_token（独立表格）")
    feishu_table_id: str = Field(default="", description="飞书表格table_id")
    feishu_page_id: str = Field(default="", description="飞书Wiki页面ID（Wiki内嵌表格）")
    is_wiki_embed: bool = Field(default=False, description="是否为Wiki内嵌表格")
    feishu_domain: str = Field(default="my.feishu.cn", description="飞书企业域名")


class FeishuNotifierOutput(BaseModel):
    """飞书通知节点输出"""
    notification_sent: bool = Field(default=False, description="通知发送状态")
    message: str = Field(default="", description="通知内容预览")