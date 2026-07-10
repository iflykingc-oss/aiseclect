"""
跨 run URL 去重状态（薄壳 → collect_pipeline.CrossRunDedup）

为保持旧 import 路径可用：
    from tools.dedup_state import DedupState
    DedupState 是 CrossRunDedup 的别名。
"""
from collect_pipeline.dedup import CrossRunDedup

# 旧名别名（保持向后兼容）
DedupState = CrossRunDedup

__all__ = ["DedupState", "CrossRunDedup"]