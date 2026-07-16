"""人工审核队列模块 - P2 人机协同反馈闭环

基于行业最佳实践（McKinsey 2026: 76% 企业使用 HITL），
实现：
1. 每日审核队列（低置信度内容）
2. 发布后反馈追踪
3. 周度重训练逻辑

目标：4周内准确率 +20-30%
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReviewItem:
    """待审核项"""
    article_id: str
    url: str
    title: str
    score: float
    reason: str
    status: str  # "pending" | "approved" | "rejected"
    created_at: str
    reviewed_at: Optional[str] = None
    reviewer: Optional[str] = None
    feedback: Optional[str] = None


class ReviewQueue:
    """审核队列管理器"""

    def __init__(self, workspace: str = "."):
        self.workspace = Path(workspace)
        self.queue_file = self.workspace / "output" / "review_queue.json"
        self.feedback_file = self.workspace / "output" / "feedback_log.json"
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)

    def add(self, article_id: str, url: str, title: str, score: float, reason: str) -> None:
        """添加到审核队列

        Args:
            article_id: 文章唯一 ID
            url: 文章 URL
            title: 文章标题
            score: 热度评分
            reason: 进入审核队列的原因
        """
        queue = self._load_queue()

        # 避免重复
        if any(item["article_id"] == article_id for item in queue):
            logger.debug(f"文章已在队列: {article_id}")
            return

        item = ReviewItem(
            article_id=article_id,
            url=url,
            title=title,
            score=score,
            reason=reason,
            status="pending",
            created_at=datetime.now().isoformat()
        )

        queue.append(item.__dict__)
        self._save_queue(queue)
        logger.info(f"添加到审核队列: {title[:50]} (score={score:.1f})")

    def get_pending(self, limit: int = 20) -> List[dict]:
        """获取待审核列表

        Args:
            limit: 返回数量上限

        Returns:
            待审核项列表
        """
        queue = self._load_queue()
        pending = [item for item in queue if item.get("status") == "pending"]
        return pending[:limit]

    def approve(self, article_id: str, reviewer: str = "human", feedback: str = "") -> bool:
        """批准文章

        Args:
            article_id: 文章 ID
            reviewer: 审核人
            feedback: 反馈意见

        Returns:
            是否成功
        """
        return self._update_status(article_id, "approved", reviewer, feedback)

    def reject(self, article_id: str, reviewer: str = "human", feedback: str = "") -> bool:
        """拒绝文章

        Args:
            article_id: 文章 ID
            reviewer: 审核人
            feedback: 拒绝原因

        Returns:
            是否成功
        """
        return self._update_status(article_id, "rejected", reviewer, feedback)

    def record_feedback(self, post_id: str, feedback: str, user_id: str = "system") -> None:
        """记录发布后反馈

        Args:
            post_id: 发布后的帖子 ID
            feedback: 反馈内容（"approved" | "rejected" | 具体意见）
            user_id: 反馈用户
        """
        feedback_log = self._load_feedback()

        feedback_entry = {
            "post_id": post_id,
            "feedback": feedback,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat()
        }

        feedback_log.append(feedback_entry)
        self._save_feedback(feedback_log)
        logger.info(f"记录反馈: {post_id} -> {feedback}")

    def get_feedback_stats(self, days: int = 7) -> dict:
        """获取反馈统计

        Args:
            days: 统计最近 N 天

        Returns:
            统计数据
        """
        feedback_log = self._load_feedback()

        # 简单统计（生产环境需要更复杂的时间过滤）
        approved = sum(1 for f in feedback_log if f.get("feedback") == "approved")
        rejected = sum(1 for f in feedback_log if f.get("feedback") == "rejected")
        total = len(feedback_log)

        return {
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "approval_rate": approved / total if total > 0 else 0.0
        }

    def should_retrain(self) -> bool:
        """判断是否需要重训练

        Returns:
            是否触发重训练条件
        """
        feedback_log = self._load_feedback()

        # 简单策略：累计 50 条反馈触发重训练
        return len(feedback_log) >= 50

    def get_training_data(self) -> dict:
        """获取训练数据（批准/拒绝案例）

        Returns:
            {"approved": [...], "rejected": [...]}
        """
        feedback_log = self._load_feedback()

        approved = [f for f in feedback_log if f.get("feedback") == "approved"]
        rejected = [f for f in feedback_log if f.get("feedback") == "rejected"]

        return {
            "approved": approved,
            "rejected": rejected
        }

    def _update_status(
        self,
        article_id: str,
        status: str,
        reviewer: str,
        feedback: str
    ) -> bool:
        """更新审核状态"""
        queue = self._load_queue()

        for item in queue:
            if item.get("article_id") == article_id:
                item["status"] = status
                item["reviewed_at"] = datetime.now().isoformat()
                item["reviewer"] = reviewer
                item["feedback"] = feedback
                self._save_queue(queue)
                logger.info(f"审核完成: {article_id} -> {status}")
                return True

        logger.warning(f"未找到文章: {article_id}")
        return False

    def _load_queue(self) -> List[dict]:
        """加载队列"""
        if not self.queue_file.exists():
            return []

        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            logger.error(f"加载队列失败: {e}")
            return []

    def _save_queue(self, queue: List[dict]) -> None:
        """保存队列"""
        try:
            with open(self.queue_file, "w", encoding="utf-8") as f:
                json.dump(queue, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"保存队列失败: {e}")

    def _load_feedback(self) -> List[dict]:
        """加载反馈日志"""
        if not self.feedback_file.exists():
            return []

        try:
            with open(self.feedback_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            logger.error(f"加载反馈日志失败: {e}")
            return []

    def _save_feedback(self, feedback_log: List[dict]) -> None:
        """保存反馈日志"""
        try:
            with open(self.feedback_file, "w", encoding="utf-8") as f:
                json.dump(feedback_log, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"保存反馈日志失败: {e}")


# 周度重训练函数
def weekly_retrain(workspace: str = ".") -> None:
    """周度重训练逻辑

    Args:
        workspace: 工作目录
    """
    queue = ReviewQueue(workspace)

    if not queue.should_retrain():
        logger.info("反馈数据不足，跳过重训练")
        return

    training_data = queue.get_training_data()
    approved = training_data["approved"]
    rejected = training_data["rejected"]

    logger.info(f"开始重训练: {len(approved)} 条批准 + {len(rejected)} 条拒绝")

    # TODO: 实际重训练逻辑
    # 1. 提取特征（标题关键词、来源、评分分布）
    # 2. 更新评分权重
    # 3. 保存新模型参数

    logger.info("重训练完成（占位符）")
