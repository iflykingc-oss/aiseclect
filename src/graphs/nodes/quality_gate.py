"""质量闸门模块 - 三级过滤机制

根据竞品分析和行业最佳实践（McKinsey 2026: 76% 企业使用 HITL），
实现三级质量过滤：
- score < 0.6: 自动拒绝
- 0.6 <= score < 0.8: 进入人工审核队列
- score >= 0.8: 自动通过

目标：将噪音率从 60% 降低到 <20%
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class QualityGateResult:
    """质量闸门判定结果"""
    action: str  # "REJECT" | "REVIEW_QUEUE" | "AUTO_APPROVE"
    reason: str
    confidence_score: float


def quality_gate(score: float, url: str, title: str, source: str = "") -> QualityGateResult:
    """三级质量闸门

    Args:
        score: 热度评分 (0-100)
        url: 素材 URL
        title: 素材标题
        source: 素材来源

    Returns:
        QualityGateResult: 包含判定动作、原因和置信度
    """
    if score < 60.0:
        return QualityGateResult(
            action="REJECT",
            reason=f"热度分 {score:.1f} < 60.0 (低置信度)",
            confidence_score=score
        )
    elif 60.0 <= score < 80.0:
        return QualityGateResult(
            action="REVIEW_QUEUE",
            reason=f"热度分 {score:.1f} 在 60-80 区间 (边缘案例，需人工审核)",
            confidence_score=score
        )
    else:
        return QualityGateResult(
            action="AUTO_APPROVE",
            reason=f"热度分 {score:.1f} >= 80.0 (高置信度)",
            confidence_score=score
        )


def batch_quality_gate(materials: List[tuple]) -> dict:
    """批量质量闸门判定

    Args:
        materials: [(score, url, title, source), ...] 列表

    Returns:
        dict: {
            "auto_approve": [...],
            "review_queue": [...],
            "rejected": [...],
            "stats": {"total": N, "approve": N, "review": N, "reject": N}
        }
    """
    auto_approve = []
    review_queue = []
    rejected = []

    for item in materials:
        score, url, title = item[0], item[1], item[2]
        source = item[3] if len(item) > 3 else ""

        result = quality_gate(score, url, title, source)

        material_data = {
            "url": url,
            "title": title,
            "source": source,
            "score": score,
            "gate_action": result.action,
            "gate_reason": result.reason
        }

        if result.action == "AUTO_APPROVE":
            auto_approve.append(material_data)
        elif result.action == "REVIEW_QUEUE":
            review_queue.append(material_data)
        else:
            rejected.append(material_data)

    stats = {
        "total": len(materials),
        "approve": len(auto_approve),
        "review": len(review_queue),
        "reject": len(rejected)
    }

    logger.info(
        f"质量闸门: 总计 {stats['total']} 条 | "
        f"自动通过 {stats['approve']} 条 | "
        f"待审核 {stats['review']} 条 | "
        f"拒绝 {stats['reject']} 条"
    )

    return {
        "auto_approve": auto_approve,
        "review_queue": review_queue,
        "rejected": rejected,
        "stats": stats
    }
