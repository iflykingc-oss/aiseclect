"""
LLM-as-judge 质量复盘模块（2026-07-26）

在 tweet_generator 主 LLM 调完后，对每个 draft 跑第二轮 LLM 复盘：
- 7 维评分（fact_consistency / hook_quality / actionability / structure / expression / platform_fit / risk）
- recommended_action: publish | revise | block
- issues[]：具体问题 + 严重程度 + auto_fixable
- 集成到 TweetDraft.x_quality_review 字段

不自动修复（auto-revision 留作下个 PR）。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Template
from langchain_core.messages import SystemMessage, HumanMessage

from collect_pipeline.models import TweetDraft
from tools.llm import LLMConfig, build_chat_model, extract_json_array, extract_text, invoke_with_retry

logger = logging.getLogger(__name__)


@dataclass
class ReviewIssue:
    category: str = ""
    severity: str = "low"  # low | medium | high | blocker
    message: str = ""
    suggestion: str = ""
    auto_fixable: bool = False


@dataclass
class QualityReview:
    url: str = ""
    overall_score: float = 0.0
    allow_publish: bool = True
    recommended_action: str = "publish"  # publish | revise | block
    summary: str = ""
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    issues: List[ReviewIssue] = field(default_factory=list)
    auto_fixable_count: int = 0

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "overall_score": self.overall_score,
            "allow_publish": self.allow_publish,
            "recommended_action": self.recommended_action,
            "summary": self.summary,
            "dimension_scores": self.dimension_scores,
            "issues": [asdict(i) for i in self.issues],
            "auto_fixable_count": self.auto_fixable_count,
        }

    @staticmethod
    def from_dict(d: dict) -> "QualityReview":
        issues = [ReviewIssue(**x) for x in d.get("issues", []) if isinstance(x, dict)]
        return QualityReview(
            url=d.get("url", ""),
            overall_score=float(d.get("overall_score", 0) or 0),
            allow_publish=bool(d.get("allow_publish", True)),
            recommended_action=str(d.get("recommended_action", "publish")),
            summary=str(d.get("summary", "")),
            dimension_scores=dict(d.get("dimension_scores") or {}),
            issues=issues,
            auto_fixable_count=int(d.get("auto_fixable_count", 0)),
        )


def _load_review_cfg(workspace: str) -> Optional[Dict[str, Any]]:
    candidates = [
        os.path.join(workspace, "config/quality_review_llm_cfg.json"),
        os.path.join(os.getcwd(), "config/quality_review_llm_cfg.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                logger.warning(f"quality_review_llm_cfg 读取失败: {e}")
                return None
    return None


def _build_review_messages(llm_cfg: LLMConfig, cfg: Dict[str, Any], drafts: List[TweetDraft]) -> List:
    drafts_data = []
    for d in drafts:
        drafts_data.append({
            "url": d.url,
            "title": d.title,
            "tweet_content": d.tweet_content[:500],
            "other_title": d.other_title,
            "other_content": d.other_content[:500] if d.other_content else "",
            "other_tags": d.other_tags,
            "platform": d.platform,
            "quality_notes": d.quality_notes,
        })
    drafts_json = json.dumps(drafts_data, ensure_ascii=False, indent=2)
    user_prompt = Template(cfg.get("up", "")).render(drafts_json=drafts_json)
    return [
        SystemMessage(content=cfg.get("sp", "")),
        HumanMessage(content=user_prompt),
    ]


def _parse_reviews(raw: List[dict], drafts: List[TweetDraft]) -> Dict[str, QualityReview]:
    by_url = {d.url: d for d in drafts}
    out: Dict[str, QualityReview] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url or url not in by_url:
            continue
        out[url] = QualityReview.from_dict(item)
    return out


def review_drafts(drafts: List[TweetDraft], workspace: str = "") -> Dict[str, QualityReview]:
    """对草稿跑 LLM 复盘。失败返回空 dict。

    通过环境变量 AISECLECT_ENABLE_REVIEW=false 可禁用（节省 token）。默认 on。
    """
    if not drafts:
        return {}
    if os.getenv("AISECLECT_ENABLE_REVIEW", "true").lower() in ("false", "0", "no", "off"):
        logger.info("AISECLECT_ENABLE_REVIEW=false，跳过质量复盘")
        return {}
    if not workspace:
        workspace = os.getenv("COZE_WORKSPACE_PATH", os.getcwd())
    cfg = _load_review_cfg(workspace)
    if not cfg:
        logger.debug("quality_review_llm_cfg 缺失，跳过复盘")
        return {}

    llm_cfg = LLMConfig.from_env(default_model="gpt-4o-mini").merged(cfg.get("config", {}))
    try:
        model = build_chat_model(llm_cfg)
        messages = _build_review_messages(llm_cfg, cfg, drafts)
        resp = invoke_with_retry(model, messages)
        text = extract_text(resp.content)
        logger.info(f"quality_review LLM 响应前 600 字符: {text[:600]}")
        raw = extract_json_array(text)
        reviews = _parse_reviews(raw, drafts)
        logger.info(
            f"quality_review 完成: 复盘 {len(reviews)}/{len(drafts)} 条 / "
            f"publish {sum(1 for r in reviews.values() if r.recommended_action == 'publish')} / "
            f"revise {sum(1 for r in reviews.values() if r.recommended_action == 'revise')} / "
            f"block {sum(1 for r in reviews.values() if r.recommended_action == 'block')}"
        )
        return reviews
    except Exception as e:
        logger.warning(f"quality_review LLM 调用失败: {e}")
        return {}


def _build_revision_messages(
    llm_cfg: LLMConfig, draft: TweetDraft, review: QualityReview, workspace: str
) -> List:
    """构造 auto-revision 用的 LLM 消息。"""
    cfg = _load_review_cfg(workspace) or {}
    issues_text = "\n".join(
        f"- [{i.severity}] {i.category}: {i.message} (建议: {i.suggestion})"
        for i in review.issues
    ) or "- （无具体 issue）"
    user_prompt = (
        f"请根据以下 7 维质量复盘结果，**只修复 auto_fixable 类别的问题**（title/hook/tone/structure/length）。"
        f"事实 / 风险类问题不要改。\n\n"
        f"## 当前草稿\n"
        f"URL: {draft.url}\n"
        f"Title: {draft.title}\n"
        f"Platform: {draft.platform}\n"
        f"X 内容: {draft.tweet_content}\n"
        f"XHS 标题: {draft.other_title}\n"
        f"XHS 正文: {draft.other_content}\n"
        f"XHS 标签: {', '.join(draft.other_tags)}\n"
        f"配图: {draft.image_prompt}\n\n"
        f"## 复盘问题（overall_score={review.overall_score:.0f}，{review.recommended_action}）\n"
        f"{issues_text}\n\n"
        f"请只修复可自动修复项，其他字段保持原样。返回 JSON 对象（不是数组）：\n"
        f"{{\n"
        f'  "url": "{draft.url}",\n'
        f'  "tweet_content": "修复后 X 内容",\n'
        f'  "other_title": "修复后 XHS 标题（仅 X+小红书时填）",\n'
        f'  "other_content": "修复后 XHS 正文（仅 X+小红书时填）",\n'
        f'  "other_tags": ["tag1", "tag2"],\n'
        f'  "image_prompt": "修复后配图提示词（仅 X+小红书时填）"\n'
        f"}}\n"
    )
    return [
        SystemMessage(content=cfg.get("sp", "你是内容修复编辑。") + "\n\n## 自动修复规则\n只修 auto_fixable 类（title/hook/tone/structure/length），其他保持。"),
        HumanMessage(content=user_prompt),
    ]


def auto_revise_draft(
    draft: TweetDraft, review: QualityReview, workspace: str = "", max_rounds: int = 1
) -> Tuple[TweetDraft, QualityReview, int]:
    """auto-revision 闭环：调 LLM 修复 → 写回 draft。

    Args:
        draft: 原草稿
        review: 复盘结果
        max_rounds: 最多修复几轮（默认 1，避免成本爆炸）

    Returns:
        (revised_draft, latest_review, rounds_used)
    """
    if not workspace:
        workspace = os.getenv("COZE_WORKSPACE_PATH", os.getcwd())
    if not review or review.recommended_action != "revise":
        return draft, review, 0
    if not any(i.auto_fixable for i in review.issues):
        logger.debug(f"auto_revise: {draft.url[:50]} 无 auto_fixable issue，跳过")
        return draft, review, 0

    cfg = _load_review_cfg(workspace)
    if not cfg:
        return draft, review, 0

    llm_cfg = LLMConfig.from_env(default_model="gpt-4o-mini").merged(cfg.get("config", {}))
    try:
        model = build_chat_model(llm_cfg)
        messages = _build_revision_messages(llm_cfg, draft, review, workspace)
        resp = invoke_with_retry(model, messages)
        text = extract_text(resp.content)
        # 解析为单个对象
        try:
            data = json.loads(text)
        except ValueError:
            # 容错：剥 markdown fence
            text = text.strip().strip("```").strip("json").strip()
            data = json.loads(text) if text.startswith("{") else {}

        if not data or data.get("url") != draft.url:
            logger.warning(f"auto_revise: LLM 返回无效 {draft.url[:50]}")
            return draft, review, 0

        # 写回字段
        if data.get("tweet_content"):
            draft.tweet_content = str(data["tweet_content"])[:1000]
        if draft.platform == "X+小红书":
            if data.get("other_title"):
                draft.other_title = str(data["other_title"])[:100]
            if data.get("other_content"):
                draft.other_content = str(data["other_content"])[:1000]
            if data.get("other_tags") and isinstance(data["other_tags"], list):
                draft.other_tags = [str(t)[:30] for t in data["other_tags"][:8]]
            if data.get("image_prompt"):
                draft.image_prompt = str(data["image_prompt"])[:300]

        logger.info(
            f"auto_revise: {draft.url[:50]} 已修复 1 轮 (overall_score {review.overall_score:.0f})"
        )
        return draft, review, 1
    except Exception as e:
        logger.warning(f"auto_revise 失败: {e}")
        return draft, review, 0