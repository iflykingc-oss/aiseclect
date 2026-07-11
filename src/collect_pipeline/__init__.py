"""collect_pipeline — 通用采集-去重-打分-落盘流水线

从 aiseclect 抽出，独立可复用。
- models: 通用数据模型（RawMaterial / StandardMaterial / ScoredMaterial / TweetDraft）
- dedup: 跨 run URL 去重（线程安全，文件持久化）
- persistence: JSON 落盘 + quality_report / reject_report 写出
- pipeline: 顺序执行器（不依赖 langgraph）
- dag: langgraph DAG 编排（可选，需要安装 langgraph）

设计原则：
- **不强依赖 langgraph** —— DataInsight/BuddyJob/AIkefu 不会想引入整个 DAG 框架
- **可选 langgraph** —— 想要 DAG 风格编排（断点 / 可视化 / 并行）时单独 import dag 子模块
"""
from .models import RawMaterial, StandardMaterial, ScoredMaterial, TweetDraft
from .growth_taxonomy import (
    assign_note_structure,
    assign_pillar,
    assign_title_pattern_key,
    load_growth_taxonomy,
    pillar_weight_overrides,
    score_xhs_dimensions,
    summarize_growth_scores,
)
from .dedup import CrossRunDedup
from .persistence import (
    persist_materials,
    write_quality_report,
    write_reject_report,
    persist_json,
)
from .pipeline import Pipeline, Collector
from .llm import (
    LLMConfig,
    invoke_with_retry,
    extract_text,
    extract_json_array,
)

__all__ = [
    # 数据模型
    "RawMaterial",
    "StandardMaterial",
    "ScoredMaterial",
    "TweetDraft",
    # 小红书起号策略
    "assign_note_structure",
    "assign_pillar",
    "assign_title_pattern_key",
    "load_growth_taxonomy",
    "pillar_weight_overrides",
    "score_xhs_dimensions",
    "summarize_growth_scores",
    # 去重
    "CrossRunDedup",
    # 落盘
    "persist_materials",
    "write_quality_report",
    "write_reject_report",
    "persist_json",
    # 流水线
    "Pipeline",
    "Collector",
    # LLM utils（按需 import collect_pipeline.llm 拿更完整 API）
    "LLMConfig",
    "invoke_with_retry",
    "extract_text",
    "extract_json_array",
]