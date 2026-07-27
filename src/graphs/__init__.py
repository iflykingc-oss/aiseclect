"""
graphs 子包入口
2026-07-27：在导入 langgraph 前 suppress `allowed_objects` 弃用警告（langgraph 1.0 内部问题，等 1.0.2+ 修复后可移除）。
"""
import warnings

# 必须先于 langgraph import —— 在 langgraph 内部触发警告前把 LangChain 系列 deprecation 全屏蔽
try:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
    warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
except ImportError:
    pass

# 兜底：任何带 allowed_objects 字的 Deprecation 警告都忽略
warnings.filterwarnings("ignore", message=r".*allowed_objects.*")
