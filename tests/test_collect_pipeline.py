"""
collect_pipeline 单元测试

覆盖：
- CrossRunDedup：filter_new / add / save / load / 原子写 / FIFO 截断
- Pipeline.run()：采集 → 合并 → 去重 → 打分 + 统计
- persistence：persist_materials / write_quality_report / write_reject_report
- build_collect_graph()：langgraph DAG 端到端

运行：pytest tests/test_collect_pipeline.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from collect_pipeline import (
    Collector,
    CrossRunDedup,
    Pipeline,
    RawMaterial,
    ScoredMaterial,
    StandardMaterial,
    persist_materials,
    write_quality_report,
    write_reject_report,
    persist_json,
)
from collect_pipeline.models import TweetDraft


# ========== CrossRunDedup ==========


class TestCrossRunDedup:
    def test_filter_new_basic(self, tmp_path: Path):
        d = CrossRunDedup(path=tmp_path / "dedup.json")
        new = d.filter_new(["https://a", "https://b", "https://a"])
        assert new == ["https://a", "https://b"]
        d.add(new)
        assert d.size == 2

    def test_filter_new_skips_empty_and_dup(self, tmp_path: Path):
        d = CrossRunDedup(path=tmp_path / "dedup.json")
        d.add(["https://known"])
        new = d.filter_new(["", "https://known", "https://fresh", None])
        # 空字符串和 None 都被过滤；已知的也跳过
        assert "https://fresh" in new
        assert "" not in new
        assert "https://known" not in new

    def test_save_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "dedup.json"
        d = CrossRunDedup(path=path)
        d.add(["https://x", "https://y"])
        d.save()
        # 重新加载
        d2 = CrossRunDedup(path=path)
        assert "https://x" in d2.known()
        assert "https://y" in d2.known()
        assert d2.last_run != ""

    def test_save_is_atomic(self, tmp_path: Path):
        """save 不会留 .tmp 残留。"""
        path = tmp_path / "dedup.json"
        d = CrossRunDedup(path=path)
        d.add(["https://x"])
        d.save()
        # 不应有 .tmp 文件残留
        siblings = list(tmp_path.iterdir())
        assert all(not s.name.endswith(".tmp") for s in siblings)
        assert (tmp_path / "dedup.json").exists()

    def test_clear_resets_state(self, tmp_path: Path):
        d = CrossRunDedup(path=tmp_path / "dedup.json")
        d.add(["https://x"])
        d.clear()
        assert d.size == 0
        assert d.filter_new(["https://x"]) == ["https://x"]

    def test_max_history_truncates_fifo(self, tmp_path: Path):
        d = CrossRunDedup(path=tmp_path / "dedup.json", max_history=3)
        d.add([f"https://u{i}" for i in range(10)])
        d.save()
        # 加载后 size 应为 3（最近 3 条）
        d2 = CrossRunDedup(path=tmp_path / "dedup.json", max_history=3)
        assert d2.size == 3
        # u7 / u8 / u9 应该保留
        assert "https://u9" in d2.known()
        assert "https://u0" not in d2.known()


# ========== Pipeline ==========


def make_collector(name: str, urls: List[str]) -> Collector:
    def fn() -> List[RawMaterial]:
        return [RawMaterial(url=u, title=f"[{name}] {u}", source=name) for u in urls]
    return Collector(name=name, fn=fn)


def mock_scorer(materials: List[StandardMaterial]) -> List[ScoredMaterial]:
    """Mock：所有 URL 含 'good' 打 80，其他打 30。"""
    return [
        ScoredMaterial(
            url=m.url,
            title=m.title,
            snippet=m.snippet,
            content=m.content,
            source=m.source,
            publish_time=m.publish_time,
            category=m.category,
            extra_data=m.extra_data,
            heat_score=80.0 if "good" in m.url else 30.0,
            score_reason="mock",
        )
        for m in materials
    ]


class TestPipeline:
    def test_run_basic_flow(self, tmp_path: Path):
        dedup = CrossRunDedup(path=tmp_path / "d.json")
        pipeline = Pipeline(
            collectors=[
                make_collector("a", ["https://good1", "https://good2"]),
                make_collector("b", ["https://bad1"]),
            ],
            dedup=dedup,
            scorer=mock_scorer,
            min_score=50.0,
        )
        result = pipeline.run()
        assert result.total_collected == 3
        assert result.total_after_dedup == 3
        assert result.duplicates_count == 0
        assert len(result.scored) == 3
        # 高分（≥50）应该有 2 条
        assert result.total_after_score == 2

    def test_run_filters_duplicates(self, tmp_path: Path):
        dedup = CrossRunDedup(path=tmp_path / "d.json")
        pipeline = Pipeline(
            collectors=[
                make_collector("a", ["https://x"]),
                make_collector("b", ["https://x", "https://y"]),  # x 重复
            ],
            dedup=dedup,
            scorer=mock_scorer,
        )
        result = pipeline.run()
        assert result.total_collected == 3
        assert result.duplicates_count == 1
        assert result.total_after_dedup == 2
        assert result.per_source_counts == {"a": 1, "b": 2}

    def test_run_max_per_source_truncates(self, tmp_path: Path):
        dedup = CrossRunDedup(path=tmp_path / "d.json")
        pipeline = Pipeline(
            collectors=[make_collector("a", [f"https://u{i}" for i in range(20)])],
            dedup=dedup,
            scorer=mock_scorer,
            max_per_source=5,
        )
        result = pipeline.run()
        assert result.total_collected == 5

    def test_run_collector_failure_doesnt_break(self, tmp_path: Path):
        def broken() -> List[RawMaterial]:
            raise RuntimeError("simulated failure")
        dedup = CrossRunDedup(path=tmp_path / "d.json")
        pipeline = Pipeline(
            collectors=[
                Collector("broken", broken),
                make_collector("ok", ["https://x"]),
            ],
            dedup=dedup,
            scorer=mock_scorer,
        )
        result = pipeline.run()
        assert result.total_collected == 1  # broken 失败但 ok 仍采集到

    def test_run_without_scorer_gives_zero(self, tmp_path: Path):
        dedup = CrossRunDedup(path=tmp_path / "d.json")
        pipeline = Pipeline(
            collectors=[make_collector("a", ["https://x"])],
            dedup=dedup,
            scorer=None,
        )
        result = pipeline.run()
        assert result.scored[0].heat_score == 0.0
        assert result.scored[0].score_reason == "无 scorer 配置"


# ========== Persistence ==========


class TestPersistence:
    def test_persist_materials_creates_file(self, tmp_path: Path):
        items = [RawMaterial(url="https://x", title="t"), RawMaterial(url="https://y")]
        path = persist_materials(items, output_dir=tmp_path, filename="out.json")
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["count"] == 2
        assert payload["items"][0]["url"] == "https://x"

    def test_persist_materials_accepts_dicts(self, tmp_path: Path):
        items = [{"url": "https://a"}, {"url": "https://b"}]
        path = persist_materials(items, output_dir=tmp_path, filename="d.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["count"] == 2

    def test_persist_materials_atomic(self, tmp_path: Path):
        path = persist_materials([RawMaterial(url="https://x")], output_dir=tmp_path, filename="x.json")
        # 无 .tmp 残留
        assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())
        assert path.exists()

    def test_write_quality_report(self, tmp_path: Path):
        drafts = [
            TweetDraft(unique_id="1", url="https://x", x_quality_score=80, xhs_quality_score=70),
            TweetDraft(unique_id="2", url="https://y", x_quality_score=60, xhs_quality_score=0),
        ]
        path = write_quality_report(drafts, output_dir=tmp_path, filename="q.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["count"] == 2
        assert payload["drafts"][0]["x_quality_score"] == 80

    def test_write_reject_report_empty(self, tmp_path: Path):
        path = write_reject_report([], output_dir=tmp_path, filename="r.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["count"] == 0
        assert payload["rejects"] == []

    def test_persist_json_custom(self, tmp_path: Path):
        path = persist_json({"hello": "world"}, output_dir=tmp_path, filename="h.json")
        assert path.read_text(encoding="utf-8") == json.dumps({"hello": "world"}, ensure_ascii=False, indent=2)


# ========== build_collect_graph（DAG） ==========


class TestDAG:
    def test_build_collect_graph_end_to_end(self, tmp_path: Path):
        """完整 DAG：2 collector → merge → dedup → score → sink"""
        pytest.importorskip("langgraph")  # 没装就跳过

        from collect_pipeline.dag import build_collect_graph

        dedup = CrossRunDedup(path=tmp_path / "d.json")
        sink_msgs: list[str] = []

        def sink(state) -> dict:
            items = getattr(state, "scored_materials", None) or (state.get("scored_materials", []) if isinstance(state, dict) else [])
            sink_msgs.append(f"OK {len(items or [])}")
            return {"sink_message": f"OK {len(items or [])}"}

        graph = build_collect_graph(
            collectors={
                "a": make_collector("a", ["https://good1"]).fn,
                "b": make_collector("b", ["https://bad1"]).fn,
            },
            scorer=mock_scorer,
            sink=sink,
            dedup=dedup,
        )
        out = graph.invoke({"max_per_source": 50, "min_heat_score": 0.0})

        # dedup / merge / score 都跑了，sink 收到了 scored materials
        assert "OK" in sink_msgs[0]
        assert out["total_collected"] == 2
        assert len(out["scored_materials"]) == 2

    def test_dedup_persists_across_runs(self, tmp_path: Path):
        pytest.importorskip("langgraph")

        from collect_pipeline.dag import build_collect_graph

        dedup = CrossRunDedup(path=tmp_path / "d.json")

        def noop_sink(state) -> dict:
            return {"sink_message": "ok"}

        graph = build_collect_graph(
            collectors={"a": make_collector("a", ["https://x"]).fn},
            scorer=mock_scorer,
            sink=noop_sink,
            dedup=dedup,
        )
        out1 = graph.invoke({"max_per_source": 50, "min_heat_score": 0.0})
        out2 = graph.invoke({"max_per_source": 50, "min_heat_score": 0.0})
        # 第二次 dedup 后应该 0 条
        assert out1["total_collected"] == 1
        assert out2["total_collected"] == 1
        assert out2["total_after_dedup"] == 0


# ========== Agent-Reach 解析（防止 summary/metadata 静默丢失回归）==========


class TestAgentReachParse:
    """针对 code-reviewer HIGH 发现的回归测试。"""

    def _parse(self, raw: str):
        from tools.agent_reach_collector import _parse_output
        return _parse_output(raw)

    def test_summary_goes_into_snippet(self):
        items = self._parse('[{"title": "T", "url": "https://x", "summary": "important"}]')
        assert len(items) == 1
        assert items[0].snippet == "important"

    def test_metadata_goes_into_extra_data(self):
        items = self._parse('[{"title": "T", "url": "https://x", "metadata": {"lang": "zh"}}]')
        assert len(items) == 1
        assert items[0].extra_data == {"lang": "zh"}

    def test_summary_and_metadata_combined(self):
        items = self._parse('[{"title": "T", "url": "https://x", "summary": "s", "metadata": {"k": "v"}}]')
        assert items[0].snippet == "s"
        assert items[0].extra_data == {"k": "v"}

    def test_missing_fields_default(self):
        items = self._parse('[{"url": "https://x"}]')
        assert items[0].snippet == ""
        assert items[0].extra_data == {}


# ========== FeedGrab 解析 ==========


class TestFeedgrabParse:
    """feedgrab 输出是 Obsidian 风格的 Markdown（含 YAML front matter）。"""

    def _parse(self, raw: str):
        from tools.feedgrab_collector import _parse_front_matter
        return _parse_front_matter(raw)

    def test_parses_standard_front_matter(self):
        md = """---
url: https://mp.weixin.qq.com/s/abc123
title: 测试公众号文章
platform: mpweixin
author: 张三
date: 2026-07-10
---

正文第一段。

正文第二段。
"""
        items = self._parse(md)
        assert len(items) == 1
        assert items[0].url == "https://mp.weixin.qq.com/s/abc123"
        assert items[0].title == "测试公众号文章"
        assert items[0].extra_data["author"] == "张三"
        assert items[0].publish_time == "2026-07-10"
        assert "正文第一段" in items[0].content

    def test_falls_back_to_h1_when_no_title(self):
        md = """---
url: https://example.com/x
---

# 我的标题

正文
"""
        items = self._parse(md)
        assert items[0].title == "我的标题"

    def test_skips_blocks_without_url(self):
        md = """---
title: 没 URL 的 front matter
---

正文
"""
        items = self._parse(md)
        assert items == []

    def test_empty_returns_empty(self):
        assert self._parse("") == []

    def test_no_front_matter_returns_generic(self):
        """没 front matter 时兜底：整个 raw 当 body，title 用首行。"""
        items = self._parse("这是第一行标题\n这是第二行正文")
        assert len(items) == 1
        assert items[0].url == ""  # 没 url，被后续逻辑过滤
        assert "这是第一行" in items[0].title

    def test_graceful_when_cli_missing(self):
        """CLI 不在时返回空列表，不抛错。"""
        from tools.feedgrab_collector import feedgrab_collector
        # 没装 feedgrab CLI，应返回空
        items = feedgrab_collector("mpweixin-id", "test", max_results=5)
        assert items == []


# ========== Models 兼容性 ==========


class TestModels:
    def test_tweet_draft_compat_fields(self):
        """老字段（viewpoint / xiaohongshu_*）仍可用，避免破坏旧调用。"""
        t = TweetDraft(
            unique_id="1",
            url="https://x",
            tweet_content="hi",
            viewpoint="legacy",
            xiaohongshu_title="标题",
            xiaohongshu_content="正文",
            xiaohongshu_tags=["AI"],
            is_thread=True,
            thread_parts=["part1", "part2"],
        )
        assert t.viewpoint == "legacy"
        assert t.xiaohongshu_title == "标题"
        assert t.is_thread is True
        assert len(t.thread_parts) == 2