"""JSON 落盘工具

- persist_materials: 写素材 JSON（带时间戳文件名）
- persist_json: 通用 JSON 落盘（自定义文件名）
- write_quality_report: 写质量报告（含 hook 类型 / 评分理由等）
- write_reject_report: 写拒绝报告（哪些素材被门禁拒掉）

所有写文件操作都先写 .tmp 再 rename，原子替换。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable, List, Optional, Union

from .models import ScoredMaterial, TweetDraft

logger = logging.getLogger(__name__)


def _default_dir() -> Path:
    """默认输出目录：COLLECT_PIPELINE_OUTPUT_DIR > ./output > ./"""
    env_dir = os.getenv("COLLECT_PIPELINE_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    if Path("output").exists():
        return Path("output")
    return Path(".")


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _atomic_write(path: Path, payload: dict) -> None:
    """原子写 JSON：先 .tmp 再 rename。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def persist_materials(
    materials: Iterable[Union[ScoredMaterial, TweetDraft, dict]],
    output_dir: Optional[Path] = None,
    prefix: str = "materials",
    filename: Optional[str] = None,
) -> Path:
    """把素材列表落盘为 JSON。

    Args:
        materials: 素材列表（支持 ScoredMaterial / TweetDraft / dict）
        output_dir: 输出目录，默认 ./output
        prefix: 文件名前缀（默认 materials）
        filename: 自定义文件名（None 则用 prefix_YYYYMMDD_HHMMSS.json）

    Returns:
        写入的文件路径
    """
    out_dir = Path(output_dir) if output_dir else _default_dir()
    fname = filename or f"{prefix}_{_timestamp()}.json"
    path = out_dir / fname

    # 统一序列化为 dict
    items: List[dict] = []
    for m in materials:
        if isinstance(m, dict):
            items.append(m)
        elif hasattr(m, "model_dump"):
            items.append(m.model_dump(exclude_none=False))
        else:
            items.append(dict(m))

    _atomic_write(path, {"count": len(items), "items": items})
    logger.info(f"落盘 {len(items)} 条 → {path}")
    return path


def persist_json(
    payload: dict,
    output_dir: Optional[Path] = None,
    prefix: str = "data",
    filename: Optional[str] = None,
) -> Path:
    """通用 JSON 落盘。"""
    out_dir = Path(output_dir) if output_dir else _default_dir()
    fname = filename or f"{prefix}_{_timestamp()}.json"
    path = out_dir / fname
    _atomic_write(path, payload)
    logger.info(f"落盘 → {path}")
    return path


def write_quality_report(
    drafts: Iterable[TweetDraft],
    output_dir: Optional[Path] = None,
    filename: Optional[str] = None,
) -> Path:
    """写质量报告：每个草稿的 hook 类型 / 平台理由 / 质量分 / 发现原因。"""
    out_dir = Path(output_dir) if output_dir else _default_dir()
    fname = filename or f"quality_report_{_timestamp()}.json"
    path = out_dir / fname

    items: List[dict] = []
    for d in drafts:
        if isinstance(d, dict):
            items.append(d)
        elif hasattr(d, "model_dump"):
            items.append(d.model_dump(exclude_none=False))
        else:
            items.append(dict(d))

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(items),
        "drafts": items,
    }
    _atomic_write(path, payload)
    logger.info(f"质量报告 {len(items)} 条 → {path}")
    return path


def write_reject_report(
    rejects: Iterable[dict],
    output_dir: Optional[Path] = None,
    filename: Optional[str] = None,
) -> Path:
    """写拒绝报告：未生成 / 质量门禁失败 / 修复后仍失败的素材 + 原因。"""
    out_dir = Path(output_dir) if output_dir else _default_dir()
    fname = filename or f"reject_report_{_timestamp()}.json"
    path = out_dir / fname

    items = list(rejects)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(items),
        "rejects": items,
    }
    _atomic_write(path, payload)
    if items:
        logger.info(f"拒绝报告 {len(items)} 条 → {path}")
    return path