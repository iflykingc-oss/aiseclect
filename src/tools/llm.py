"""
LLM 客户端（薄壳 → collect_pipeline.llm）

为保持旧 import 路径可用：
    from tools.llm import LLMConfig, build_chat_model, invoke_with_retry, ...
所有实现已迁移到 collect_pipeline.llm 子模块。
"""
from collect_pipeline.llm import (  # noqa: F401
    LLMConfig,
    load_llm_cfg,
    build_chat_model,
    invoke_with_retry,
    extract_text,
    extract_json_array,
)

__all__ = [
    "LLMConfig",
    "load_llm_cfg",
    "build_chat_model",
    "invoke_with_retry",
    "extract_text",
    "extract_json_array",
]